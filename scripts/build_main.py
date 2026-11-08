"""Build data/main.json + data/layouts.json for the main page from REAL sources only.

Inputs (all checked in or reproducible):
  dist/data/*            Anne's Duke atlas (run `python3 scripts/build_data.py` first): official Duke
                         facility points, permit/visitor rates, published capacities, on-street rules,
                         Lewis St curb trace, Durham parking signs.
  data/parking.geojson   OSM lot polygons + stalls (mapped by OSM contributors, or estimated by our grid).
  data/osm_raw.json      Raw OSM tags (building:levels, opening_hours, capacity, highways for entrances).
  data/tier3_forecast.json  Jacob's Tier 3 occupancy model (4 sensor-pilot groups + typical-day profiles).

Rules: Duke official data wins over OSM where both exist (a Duke point inside an OSM polygon). Anything we
cannot source is null and the UI shows "Not published". Nothing here invents prices, spaces or names.
"""
import json, math, pathlib, re

ROOT = pathlib.Path(__file__).resolve().parents[1]
ATLAS = ROOT / "dist" / "data"
DATA = ROOT / "data"
WILK = (-78.93805, 36.00346)      # Wilkinson Building (OSM node 5824182200)
RADIUS = 1500
STALL_W, STALL_L, AISLE = 2.74, 5.5, 6.0


def read(p): return json.loads(pathlib.Path(p).read_text())


def dist_m(a, b):
    kx = 111320 * math.cos(math.radians((a[1] + b[1]) / 2))
    return math.hypot((a[0] - b[0]) * kx, (a[1] - b[1]) * 110540)


def pip(pt, ring):
    x, y, inside = pt[0], pt[1], False
    for i in range(len(ring)):
        x1, y1 = ring[i]; x2, y2 = ring[i - 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


class Frame:
    """Local metric frame around a lot, rotated so the lot's minimum-area rectangle is axis aligned."""
    def __init__(self, ring):
        self.lon0 = sum(p[0] for p in ring) / len(ring); self.lat0 = sum(p[1] for p in ring) / len(ring)
        self.kx = 111320 * math.cos(math.radians(self.lat0)); self.ky = 110540
        pts = [self.xy0(p) for p in ring]
        best = None
        for i in range(len(pts) - 1):
            a = math.atan2(pts[i + 1][1] - pts[i][1], pts[i + 1][0] - pts[i][0])
            c, s = math.cos(-a), math.sin(-a)
            r = [(x * c - y * s, x * s + y * c) for x, y in pts]
            xs, ys = [p[0] for p in r], [p[1] for p in r]
            area = (max(xs) - min(xs)) * (max(ys) - min(ys))
            if best is None or area < best[0]: best = (area, a, max(xs) - min(xs), max(ys) - min(ys))
        a = best[1]
        if best[3] > best[2]: a += math.pi / 2          # long side horizontal
        self.c, self.s = math.cos(-a), math.sin(-a)

    def xy0(self, p): return ((p[0] - self.lon0) * self.kx, (p[1] - self.lat0) * self.ky)

    def xy(self, p):
        x, y = self.xy0(p)
        return (x * self.c - y * self.s, -(x * self.s + y * self.c))   # SVG y points down


def r1(v): return round(v, 1)


def seg_intersect(p1, p2, p3, p4):
    d = (p2[0] - p1[0]) * (p4[1] - p3[1]) - (p2[1] - p1[1]) * (p4[0] - p3[0])
    if abs(d) < 1e-15: return None
    t = ((p3[0] - p1[0]) * (p4[1] - p3[1]) - (p3[1] - p1[1]) * (p4[0] - p3[0])) / d
    u = ((p3[0] - p1[0]) * (p2[1] - p1[1]) - (p3[1] - p1[1]) * (p2[0] - p1[0])) / d
    if 0 <= t <= 1 and 0 <= u <= 1: return (p1[0] + t * (p2[0] - p1[0]), p1[1] + t * (p2[1] - p1[1]))
    return None


def first_int(s):
    m = re.match(r"\s*([\d,]+)", str(s or ""))
    return int(m.group(1).replace(",", "")) if m else None


# ------------------------------------------------------------------ inputs
fc = read(DATA / "parking.geojson")
raw = read(DATA / "osm_raw.json")["elements"]
tags_by_id = {("w" if e["type"] == "way" else e["type"][0]) + str(e["id"]): e.get("tags", {}) for e in raw}
roads = [e for e in raw if e["type"] == "way" and e.get("tags", {}).get("highway") and
         e["tags"].get("service") != "parking_aisle" and "geometry" in e]
facilities = read(ATLAS / "facilities.geojson")["features"]
rules = read(ATLAS / "rules.json")
t3 = read(DATA / "tier3_forecast.json")

lots = [f for f in fc["features"] if f["properties"]["kind"] == "lot"]
stalls_by_lot = {}
for f in fc["features"]:
    p = f["properties"]
    if p["kind"] == "space" and p.get("lot"): stalls_by_lot.setdefault(p["lot"], []).append(f)

# Duke facility points within the study radius
duke_pts = [f for f in facilities if dist_m(f["geometry"]["coordinates"], WILK) <= RADIUS]
duke_in_lot, used = {}, set()
for lot in lots:
    ring = lot["geometry"]["coordinates"][0]
    for f in duke_pts:
        if pip(f["geometry"]["coordinates"], ring):
            duke_in_lot.setdefault(lot["properties"]["id"], []).append(f["properties"]); used.add(f["properties"]["id"])


def duke_fields(ds):
    """Merge one or more Duke facility sections (official) into display fields."""
    names = list(dict.fromkeys(d["name"] for d in ds))
    caps = [c for d in ds for c in d["capacity"]]
    vis = [v for d in ds for v in d["visitorRates"]]
    ev = [d["ev"] for d in ds if d.get("ev")]
    return {
        "name": " / ".join(names),
        "dukeIds": [d["dukeId"] for d in ds],
        "permits": " / ".join(dict.fromkeys(d["permit"] for d in ds if d.get("permit"))) or None,
        "access": " / ".join(dict.fromkeys(d["access"] for d in ds if d.get("access"))) or None,
        "visitorRate": ({"hourlyUsd": vis[0].get("hourlyUsd"), "dailyMaxUsd": vis[0].get("dailyMaxUsd"),
                         "note": vis[0].get("caveat") or vis[0].get("note"), "source": vis[0].get("source")} if vis else None),
        "permitRates": [{"name": r["name"], "annualUsd": r.get("annualUsd"), "monthlyUsd": r.get("monthlyUsd"),
                         "eligibility": r.get("eligibility"), "clearance": r.get("clearance"), "source": r.get("source")}
                        for d in ds for r in d["rates"] if not r["name"].startswith("HEW")],
        "capacityPublished": caps,
        "hours": next((d["afterHours"] for d in ds if d.get("afterHours")), None),
        "description": next((d["description"] for d in ds if d.get("description")), None),
        "evStations": sum(e.get("stationsInSection") or 0 for e in ev) or None,
        "evNote": ev[0].get("name") if ev else None,
        "adaCount": next((d["accessibleCount"] for d in ds if d.get("accessibleCount") is not None), None),
        "accessible": True if any(d.get("accessible") for d in ds) else None,
        "warnings": [w for d in ds for w in d.get("warnings", [])],
        "sources": [{"label": "Duke Campus Maps · " + d["dukeId"], "url": d["sourceUrl"]} for d in ds],
    }


def capacity_of(published, osm_cap, mapped, est, roof=None, levels=None):
    nums = [first_int(c["value"]) for c in published if first_int(c["value"])]
    if nums:
        return {"value": max(nums), "confidence": "counted", "basis": "Published by Duke Parking",
                "scopes": [f'{c["value"]} · {c["scope"]}' for c in published], "source": published[0]["source"]}
    if osm_cap:
        return {"value": osm_cap, "confidence": "counted", "basis": "OSM capacity tag (community count)", "source": None}
    if mapped:
        return {"value": mapped, "confidence": "counted", "basis": "Stalls mapped individually in OSM", "source": None}
    if roof and levels:
        return {"value": roof * levels, "confidence": "estimated",
                "basis": f"Roof stalls drawn by our grid model ({roof}) × {levels} levels (building:levels)", "source": None}
    if est:
        return {"value": est, "confidence": "estimated", "basis": "Stalls drawn by our grid model inside the OSM outline", "source": None}
    return {"value": None, "confidence": None, "basis": "Not published", "source": None}


def entrances(ring, frame):
    pts = []
    for rd in roads:
        g = [(n["lon"], n["lat"]) for n in rd["geometry"]]
        for i in range(len(g) - 1):
            for j in range(len(ring) - 1):
                x = seg_intersect(g[i], g[i + 1], ring[j], ring[j + 1])
                if x and all(dist_m(x, q) > 10 for q in pts): pts.append(x)
    return [[r1(v) for v in frame.xy(p)] for p in pts[:4]]


def roof_grid(ring, frame):
    poly = [frame.xy(p) for p in ring]
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    out, y = [], min(ys) + 1.0
    pattern = [STALL_L, AISLE, STALL_L]               # double-loaded row: stalls | aisle | stalls
    while y + STALL_L <= max(ys) - 1.0:
        for k, h in enumerate(pattern):
            if k == 1: continue
            yy = y + sum(pattern[:k]); x = min(xs) + 1.0
            while x + STALL_W <= max(xs) - 1.0:
                corners = [(x, yy), (x + STALL_W, yy), (x + STALL_W, yy + h), (x, yy + h)]
                if all(pip(c, poly) for c in corners): out.append([r1(x + STALL_W / 2), r1(yy + h / 2), 0, STALL_W, STALL_L, 0])
                x += STALL_W
        y += sum(pattern) + 0.5
    return out


def stall_rec(f, frame):
    ring = [frame.xy(p) for p in f["geometry"]["coordinates"][0][:4]]
    cx, cy = sum(p[0] for p in ring) / 4, sum(p[1] for p in ring) / 4
    e1 = math.dist(ring[0], ring[1]); e2 = math.dist(ring[1], ring[2])
    a = math.degrees(math.atan2(ring[1][1] - ring[0][1], ring[1][0] - ring[0][0]))
    w, l = (e1, e2) if e1 <= e2 else (e2, e1)
    if e1 > e2: a += 90
    a = ((a + 90) % 180) - 90
    p = f["properties"]
    flags = (1 if p.get("ada") else 0) | (2 if p.get("ev") else 0) | (4 if p.get("estimated") else 0)
    return [r1(cx), r1(cy), round(a), r1(w), r1(l), flags]


# ------------------------------------------------------------------ lots
out_lots, layouts = [], {}
for lot in lots:
    p = lot["properties"]; lid = p["id"]
    ring = lot["geometry"]["coordinates"][0]
    t = tags_by_id.get(lid, {})
    ds = duke_in_lot.get(lid, [])
    dk = duke_fields(ds) if ds else None
    ptype = t.get("parking") or p.get("parking")
    multi = ptype == "multi-storey"
    underground = ptype == "underground"
    levels_tag = first_int(t.get("building:levels") or t.get("levels"))
    frame = Frame(ring)
    st = stalls_by_lot.get(lid, [])
    recs = [stall_rec(f, frame) for f in st]
    roof = None
    if multi and not recs:
        recs = roof_grid(ring, frame); roof = len(recs)
        for r in recs: r[5] |= 4
    mapped = sum(1 for f in st if not f["properties"].get("estimated"))
    osm_cap = first_int(t.get("capacity"))
    cap = capacity_of(dk["capacityPublished"] if dk else [], osm_cap, mapped, p.get("estimated_spaces") or 0, roof, levels_tag)
    name = dk["name"] if dk else (t.get("name") or None)
    name_src = "Duke Parking (official)" if dk else ("OpenStreetMap" if t.get("name") else None)
    deck = multi or underground or bool(dk and dk["description"] and re.search(r"garage|deck", dk["description"], re.I))
    outline = [[r1(v) for v in frame.xy(q)] for q in ring]
    layouts[lid] = {"outline": outline, "stalls": recs, "entrances": entrances(ring, frame)}
    ada_osm = sum(1 for f in st if f["properties"].get("ada")); ev_osm = sum(1 for f in st if f["properties"].get("ev"))
    osm_url = ("https://www.openstreetmap.org/way/" + lid[1:]) if lid.startswith("w") else None
    out_lots.append({
        "id": lid, "kind": "deck" if deck else "lot", "name": name, "nameSource": name_src,
        "lat": round(p["center"][1], 6), "lon": round(p["center"][0], 6),
        "dist": round(dist_m(p["center"], WILK)),
        "ring": [[round(q[0], 6), round(q[1], 6)] for q in ring],
        "capacity": cap,
        "permits": dk["permits"] if dk else None,
        "access": dk["access"] if dk else (("OSM access=" + t["access"]) if t.get("access") else None),
        "visitorRate": dk["visitorRate"] if dk else None,
        "permitRates": dk["permitRates"] if dk else [],
        "hours": (dk and dk["hours"]) or (("OSM opening_hours: " + t["opening_hours"]) if t.get("opening_hours") else None),
        "description": dk["description"] if dk else None,
        "ada": dk["adaCount"] if dk and dk["adaCount"] is not None else (ada_osm or None),
        "adaBasis": "Duke" if dk and dk["adaCount"] is not None else ("stalls tagged in OSM" if ada_osm else None),
        "accessible": (dk and dk["accessible"]) or (True if ada_osm else None),
        "ev": (dk and dk["evStations"]) or (ev_osm or None),
        "evBasis": ("Duke EV charging page" if dk and dk["evStations"] else ("stalls tagged in OSM" if ev_osm else None)),
        "levels": levels_tag, "roof": bool(multi), "roofStalls": roof, "underground": underground,
        "layoutStatus": ("Roof level drawn by our grid model (estimated); lower levels not mapped" if roof else
                         "Underground garage: interior not mapped" if underground and not st else
                         "Stalls mapped in OSM" if st and mapped == len(st) else
                         "Stalls estimated by our grid model inside the OSM outline" if st else "No stall layout"),
        "warnings": dk["warnings"] if dk else [],
        "sources": (dk["sources"] if dk else []) + ([{"label": "OpenStreetMap " + lid, "url": osm_url}] if osm_url else []),
        "profile": "department_lot",
    })

# Duke facility points that are not inside any OSM polygon: official info, no layout
for f in duke_pts:
    d = f["properties"]
    if d["id"] in used: continue
    dk = duke_fields([d]); lon, lat = f["geometry"]["coordinates"]
    deck = bool(dk["description"] and re.search(r"garage|deck", dk["description"], re.I))
    out_lots.append({
        "id": d["id"], "kind": "deck" if deck else "lot", "name": dk["name"], "nameSource": "Duke Parking (official)",
        "lat": round(lat, 6), "lon": round(lon, 6), "dist": round(dist_m((lon, lat), WILK)), "ring": None,
        "capacity": capacity_of(dk["capacityPublished"], None, 0, 0),
        "permits": dk["permits"], "access": dk["access"], "visitorRate": dk["visitorRate"], "permitRates": dk["permitRates"],
        "hours": dk["hours"], "description": dk["description"], "ada": dk["adaCount"], "adaBasis": "Duke" if dk["adaCount"] else None,
        "accessible": dk["accessible"], "ev": dk["evStations"], "evBasis": "Duke EV charging page" if dk["evStations"] else None,
        "levels": None, "roof": False, "underground": "underground" in (dk["description"] or "").lower(),
        "layoutStatus": "Official facility reference point; Duke does not publish a stall layout",
        "warnings": dk["warnings"], "sources": dk["sources"], "profile": "department_lot",
    })

# ------------------------------------------------------------------ on-street
signs = [s for s in read(ATLAS / "signs.geojson")["features"]
         if s["geometry"] and dist_m(s["geometry"]["coordinates"], WILK) <= RADIUS + 200]


def near_signs(lines, tol=20):
    found = {}
    for s in signs:
        c = s["geometry"]["coordinates"]
        if any(dist_m(c, q) < tol for ln in lines for q in ln):
            p = s["properties"]
            key = " · ".join(x for x in [p.get("LEGEND"), p.get("TIMELIMIT"), p.get("ENFORCEHOURS_label"), p.get("ENFORCEDAYS_label")] if x)
            if key: found[key] = found.get(key, 0) + 1
    return [{"text": k, "count": v} for k, v in sorted(found.items(), key=lambda kv: -kv[1])[:6]], sum(found.values())


streets = []
by_name = {}
for f in read(ATLAS / "roads.geojson")["features"]:
    by_name.setdefault(f["properties"]["name"], []).append(f)
curb = read(ATLAS / "curb-estimates.geojson")
lewis_bays = [b for b in curb["features"] if b["properties"].get("kind") == "estimated-curb-bay"]
for name, fs in by_name.items():
    lines = []
    for f in fs:
        g = f["geometry"]
        lines += [g["coordinates"]] if g["type"] == "LineString" else g["coordinates"]
    rule = fs[0]["properties"]["rule"]
    allpts = [q for ln in lines for q in ln]
    c = min(allpts, key=lambda q: dist_m(q, WILK))
    sg, nsg = near_signs(lines)
    spaces = rule.get("spaceCount") or rule.get("visitorSpaces")
    item = {"id": "street-" + re.sub(r"\W+", "-", name.lower()), "kind": "street", "name": name,
            "nameSource": "Duke Parking (official)", "lat": round(c[1], 6), "lon": round(c[0], 6), "dist": round(dist_m(c, WILK)),
            "lines": [[[round(x, 6), round(y, 6)] for x, y in ln] for ln in lines],
            "capacity": ({"value": spaces, "confidence": "counted", "basis": "Published by Duke", "source": rule["source"]} if spaces else
                         {"value": None, "confidence": None, "basis": "Not published", "source": None}),
            "visitorRate": {"hourlyUsd": rule.get("hourlyUsd"), "dailyMaxUsd": None,
                            "note": ("Pay with " + rule["payment"] + (" + transaction fees" if rule.get("transactionFeesAdditional") else "")) if rule.get("payment") else None,
                            "source": rule.get("rateSource") or rule["source"]},
            "hours": rule.get("open"), "permits": None, "access": "Signed visitor spaces only",
            "rules": [fs[0]["properties"]["rule_scope"]], "signs": sg, "signCount": nsg,
            "ada": None, "ev": None, "layoutStatus": "Road centerline only; Duke does not publish curb positions",
            "sources": [{"label": "Duke rule", "url": rule["source"]}] + [{"label": "OSM " + f["properties"]["id"], "url": f["properties"]["source_url"]} for f in fs[:3]],
            "warnings": [], "profile": "curb"}
    if name == "Lewis Street" and lewis_bays:
        fr = Frame([q for b in lewis_bays for q in b["geometry"]["coordinates"][0]] + [lewis_bays[0]["geometry"]["coordinates"][0][0]])
        layouts[item["id"]] = {"outline": None, "entrances": [],
                               "stalls": [stall_rec({"geometry": b["geometry"], "properties": {"estimated": True}}, fr) for b in lewis_bays]}
        item["layoutStatus"] = f"{len(lewis_bays)} bays traced from Durham 2025 orthophotos (partial north curb, medium confidence)"
        item["sources"].append({"label": "Durham 2025 orthophotos", "url": curb["streets"]["Lewis Street"]["source"]})
    streets.append(item)

# OSM streets tagged with on-street parking
for f in read(ATLAS / "street.geojson")["features"]:
    p = f["properties"]; tg = p["tags"]; g = f["geometry"]
    lines = [g["coordinates"]] if g["type"] == "LineString" else g["coordinates"]
    c = min((q for ln in lines for q in ln), key=lambda q: dist_m(q, WILK))
    if dist_m(c, WILK) > RADIUS: continue
    ptags = {k: v for k, v in tg.items() if k.startswith("parking")}
    cap = first_int(tg.get("parking:both:capacity") or tg.get("parking:left:capacity") or tg.get("parking:right:capacity"))
    fee = tg.get("parking:both:fee") or tg.get("parking:left:fee") or tg.get("parking:right:fee")
    sg, nsg = near_signs(lines)
    streets.append({"id": "street-" + p["id"].replace("/", "-"), "kind": "street",
                    "name": tg.get("name"), "nameSource": "OpenStreetMap" if tg.get("name") else None,
                    "lat": round(c[1], 6), "lon": round(c[0], 6), "dist": round(dist_m(c, WILK)),
                    "lines": [[[round(x, 6), round(y, 6)] for x, y in ln] for ln in lines],
                    "capacity": ({"value": cap, "confidence": "counted", "basis": "OSM capacity tag", "source": None} if cap else
                                 {"value": None, "confidence": None, "basis": "Not published", "source": None}),
                    "visitorRate": None, "feeTag": fee, "hours": None, "permits": None, "access": None,
                    "rules": [f"{k} = {v}" for k, v in sorted(ptags.items())][:8], "signs": sg, "signCount": nsg,
                    "ada": None, "ev": None, "layoutStatus": "OSM street-parking tags on the road centerline",
                    "sources": [{"label": "OpenStreetMap " + p["id"], "url": p["source_url"]}], "warnings": [], "profile": "curb"})

# Jacob's sensor-pilot groups (team site survey counts; placements as stated by the model export)
for gid, g in t3["groups"].items():
    if g.get("lat") is None: continue
    streets.append({"id": "pilot-" + gid, "kind": "pilot", "name": g["name"], "nameSource": "ParkShark sensor pilot",
                    "lat": g["lat"], "lon": g["lon"], "dist": round(dist_m((g["lon"], g["lat"]), WILK)), "lines": None,
                    "capacity": {"value": g["spots"], "confidence": "counted", "basis": "Team site survey (sensor pilot)", "source": None},
                    "visitorRate": None, "hours": None, "permits": None, "access": None, "rules": [], "signs": [], "signCount": 0,
                    "ada": None, "ev": None, "layoutStatus": "Spot positions not surveyed yet",
                    "sources": [], "warnings": [g["match"]], "profile": "pilot", "tier3": gid})

main = {
    "generated": t3.get("generated"), "center": {"lon": WILK[0], "lat": WILK[1], "name": "Wilkinson Building"},
    "radius_m": RADIUS,
    "sources": {
        "duke": "Duke Parking & Transportation + Duke Campus Maps via structured-map atlas (retrieved 2026-11-07)",
        "osm": "© OpenStreetMap contributors (ODbL-1.0)",
        "signs": "City of Durham parking signs (open data)",
        "tier3": t3["model"], "tier3Note": t3["honesty_note"], "tier3Band": t3["band"],
    },
    "lots": sorted(out_lots, key=lambda x: x["dist"]),
    "streets": sorted(streets, key=lambda x: x["dist"]),
    "tier3": {"typical": t3["typical_days"], "groups": {k: {"name": v["name"], "spots": v["spots"], "hourly": v["hourly"],
                                                           "typical": v["typical"], "match": v["match"], "lot_type": v["lot_type"]} for k, v in t3["groups"].items()}},
}
(DATA / "main.json").write_text(json.dumps(main, separators=(",", ":")))
(DATA / "layouts.json").write_text(json.dumps(layouts, separators=(",", ":")))
print(json.dumps({"lots": len(out_lots), "duke_joined": sum(1 for x in out_lots if x["nameSource"] == "Duke Parking (official)"),
                  "decks": sum(1 for x in out_lots if x["kind"] == "deck"), "streets": len(streets),
                  "with_visitor_rate": sum(1 for x in out_lots + streets if x.get("visitorRate")),
                  "layouts": len(layouts), "stalls": sum(len(v["stalls"]) for v in layouts.values()),
                  "main_kb": (DATA / "main.json").stat().st_size // 1024, "layouts_kb": (DATA / "layouts.json").stat().st_size // 1024}))
