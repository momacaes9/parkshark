#!/usr/bin/env python3
"""
ParkShark Duke - fetch real parking inventory from OpenStreetMap (ODbL).

Queries the Overpass API around Duke West/Science campus and writes
data/parking.geojson with:
  kind=lot      parking lots (polygons)            amenity=parking
  kind=space    individual stalls (polygons/points) amenity=parking_space
                + estimated stalls generated inside lots ("estimated": true)
  kind=street   on-street parking (lines)          parking:lane / parking:both|left|right
  kind=charger  EV charging stations (points)      amenity=charging_station

Pure standard library (no pip installs needed).
Usage:  python3 scripts/fetch_osm.py            # fetch + build
        python3 scripts/fetch_osm.py --cached   # rebuild from data/osm_raw.json
"""
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request

CENTER_LAT, CENTER_LON = 36.0035, -78.9405   # Wilkinson Building, 534 Research Dr
RADIUS_M = 1500
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_PATH = os.path.join(ROOT, "data", "osm_raw.json")
OUT_PATH = os.path.join(ROOT, "data", "parking.geojson")

# Stall geometry (metres) - typical US 9ft x 18ft stall, 24ft two-way aisle
STALL_W, STALL_D, AISLE = 2.74, 5.5, 7.3
EDGE_MARGIN = 0.3          # keep stalls slightly off the lot edge
MIN_LOT_AREA = 60.0        # m2; ignore tiny slivers

QUERY = f"""
[out:json][timeout:120];
(
  way["amenity"="parking"](around:{RADIUS_M},{CENTER_LAT},{CENTER_LON});
  relation["amenity"="parking"](around:{RADIUS_M},{CENTER_LAT},{CENTER_LON});
  node["amenity"="parking_space"](around:{RADIUS_M},{CENTER_LAT},{CENTER_LON});
  way["amenity"="parking_space"](around:{RADIUS_M},{CENTER_LAT},{CENTER_LON});
  way[~"^parking:(lane|both|left|right)"~"."](around:{RADIUS_M},{CENTER_LAT},{CENTER_LON});
  node["amenity"="charging_station"](around:{RADIUS_M},{CENTER_LAT},{CENTER_LON});
  way["amenity"="charging_station"](around:{RADIUS_M},{CENTER_LAT},{CENTER_LON});
);
out tags geom;
"""


# ---------------------------------------------------------------- fetching
def fetch_with_curl(url):
    """Fallback: macOS system Python ships an old LibreSSL that fails the TLS handshake."""
    import subprocess
    out = subprocess.run(
        ["curl", "-sS", "--fail", "--max-time", "180", "-A", "ParkShark-Duke-hackathon/0.1",
         "--data-urlencode", f"data={QUERY}", url],
        capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip())
    return json.loads(out.stdout)


def fetch_overpass():
    data = urllib.parse.urlencode({"data": QUERY}).encode()
    last_err = None
    for url in OVERPASS_URLS:
        try:
            return fetch_with_curl(url)
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"  curl {url} failed: {e}", file=sys.stderr)
        for attempt in range(2):
            try:
                req = urllib.request.Request(
                    url, data=data,
                    headers={"User-Agent": "ParkShark-Duke-hackathon/0.1",
                             "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=180) as r:
                    return json.loads(r.read().decode())
            except Exception as e:  # noqa: BLE001
                last_err = e
                print(f"  overpass {url} attempt {attempt + 1} failed: {e}", file=sys.stderr)
                time.sleep(5 * (attempt + 1))
    raise SystemExit(f"Overpass failed: {last_err}")


# ---------------------------------------------------------------- geometry
M_PER_DEG_LAT = 111_320.0
M_PER_DEG_LON = 111_320.0 * math.cos(math.radians(CENTER_LAT))


def to_xy(lon, lat):
    return ((lon - CENTER_LON) * M_PER_DEG_LON, (lat - CENTER_LAT) * M_PER_DEG_LAT)


def to_ll(x, y):
    return (round(CENTER_LON + x / M_PER_DEG_LON, 7), round(CENTER_LAT + y / M_PER_DEG_LAT, 7))


def ring_area(r):
    a = 0.0
    for i in range(len(r)):
        x1, y1 = r[i]
        x2, y2 = r[(i + 1) % len(r)]
        a += x1 * y2 - x2 * y1
    return a / 2.0


def centroid(r):
    a = ring_area(r)
    if abs(a) < 1e-9:
        xs, ys = zip(*r)
        return sum(xs) / len(xs), sum(ys) / len(ys)
    cx = cy = 0.0
    for i in range(len(r)):
        x1, y1 = r[i]
        x2, y2 = r[(i + 1) % len(r)]
        f = x1 * y2 - x2 * y1
        cx += (x1 + x2) * f
        cy += (y1 + y2) * f
    return cx / (6 * a), cy / (6 * a)


def point_in_ring(x, y, r):
    inside = False
    j = len(r) - 1
    for i in range(len(r)):
        xi, yi = r[i]
        xj, yj = r[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-15) + xi:
            inside = not inside
        j = i
    return inside


def point_in_poly(x, y, outers, inners):
    return any(point_in_ring(x, y, o) for o in outers) and not any(point_in_ring(x, y, h) for h in inners)


def seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0 if L == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / L))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def dist_to_rings(x, y, rings):
    best = float("inf")
    for r in rings:
        for i in range(len(r)):
            a, b = r[i], r[(i + 1) % len(r)]
            best = min(best, seg_dist(x, y, a[0], a[1], b[0], b[1]))
    return best


def strip_closing(r):
    return r[:-1] if len(r) > 1 and r[0] == r[-1] else r


def join_ways(segments):
    """Join open way segments (lists of xy) into closed rings (multipolygon members)."""
    segs = [list(s) for s in segments if len(s) >= 2]
    rings = []
    while segs:
        cur = segs.pop(0)
        changed = True
        while cur[0] != cur[-1] and changed:
            changed = False
            for i, s in enumerate(segs):
                if s[0] == cur[-1]:
                    cur += s[1:]
                elif s[-1] == cur[-1]:
                    cur += s[::-1][1:]
                elif s[-1] == cur[0]:
                    cur = s[:-1] + cur
                elif s[0] == cur[0]:
                    cur = s[::-1][:-1] + cur
                else:
                    continue
                segs.pop(i)
                changed = True
                break
        if len(cur) >= 4 and cur[0] == cur[-1]:
            rings.append(strip_closing(cur))
    return rings


def generate_stalls(outers, inners, area):
    """Estimated stall grid: double-loaded rows (stall|aisle|stall) aligned to longest edge."""
    main = max(outers, key=lambda r: abs(ring_area(r)))
    best_len, theta = 0, 0.0
    for i in range(len(main)):
        (x1, y1), (x2, y2) = main[i], main[(i + 1) % len(main)]
        L = math.hypot(x2 - x1, y2 - y1)
        if L > best_len:
            best_len, theta = L, math.atan2(y2 - y1, x2 - x1)
    c, s = math.cos(-theta), math.sin(-theta)
    rot = lambda p: (p[0] * c - p[1] * s, p[0] * s + p[1] * c)  # noqa: E731
    R_out = [[rot(p) for p in r] for r in outers]
    R_in = [[rot(p) for p in r] for r in inners]
    xs = [p[0] for r in R_out for p in r]
    ys = [p[1] for r in R_out for p in r]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)

    def ok(rect):
        for (x, y) in rect + [((rect[0][0] + rect[2][0]) / 2, (rect[0][1] + rect[2][1]) / 2)]:
            if not point_in_poly(x, y, R_out, R_in):
                return False
        cx = (rect[0][0] + rect[2][0]) / 2
        cy = (rect[0][1] + rect[2][1]) / 2
        return dist_to_rings(cx, cy, R_out + R_in) > min(STALL_W, STALL_D) / 2 + EDGE_MARGIN - 0.01

    stalls = []
    # rows: stall, aisle, stall, (back-to-back) stall, aisle, stall ...
    # module = stall + aisle + stall; modules are packed back to back
    module = 2 * STALL_D + AISLE
    y = miny + EDGE_MARGIN
    row = 0
    while y + STALL_D <= maxy:
        for (y0, facing) in ((y, "up"), (y + STALL_D + AISLE, "down")):
            if y0 + STALL_D > maxy:
                continue
            x = minx + EDGE_MARGIN
            while x + STALL_W <= maxx:
                rect = [(x, y0), (x + STALL_W, y0), (x + STALL_W, y0 + STALL_D), (x, y0 + STALL_D)]
                if ok(rect):
                    stalls.append({"rect": rect, "row": row, "aisle_side": facing})
                x += STALL_W
            row += 1
        y += module
    # rotate back
    ci, si = math.cos(theta), math.sin(theta)
    unrot = lambda p: (p[0] * ci - p[1] * si, p[0] * si + p[1] * ci)  # noqa: E731
    for st in stalls:
        st["rect"] = [unrot(p) for p in st["rect"]]
    return stalls, math.degrees(theta)


# ---------------------------------------------------------------- helpers
def to_int(v):
    if v is None:
        return None
    try:
        return int(str(v).split(";")[0].strip())
    except ValueError:
        return None


def geom_xy(el):
    return [to_xy(p["lon"], p["lat"]) for p in el.get("geometry", []) if p]


def ring_to_ll(r):
    pts = [list(to_ll(*p)) for p in r]
    return pts + [pts[0]]


TAG_KEYS = ["name", "operator", "access", "fee", "capacity", "capacity:disabled",
            "capacity:charging", "parking", "surface", "building:levels", "levels",
            "maxstay", "opening_hours", "ref", "supervised", "covered"]


# ---------------------------------------------------------------- build
def build(raw):
    els = raw["elements"]
    lots, spaces, streets, chargers = [], [], [], []

    for el in els:
        t = el.get("tags", {})
        am = t.get("amenity")
        if am == "parking":
            if el["type"] == "way":
                pts = strip_closing(geom_xy(el))
                if len(pts) < 3:
                    continue
                outers, inners = [pts], []
            elif el["type"] == "relation":
                o_segs = [geom_xy(m) for m in el.get("members", []) if m.get("type") == "way" and m.get("role") != "inner"]
                i_segs = [geom_xy(m) for m in el.get("members", []) if m.get("type") == "way" and m.get("role") == "inner"]
                outers, inners = join_ways(o_segs), join_ways(i_segs)
                if not outers:
                    continue
            else:
                continue
            area = sum(abs(ring_area(r)) for r in outers) - sum(abs(ring_area(r)) for r in inners)
            if area < MIN_LOT_AREA:
                continue
            lots.append({"id": f"{el['type'][0]}{el['id']}", "tags": t, "outers": outers,
                         "inners": inners, "area": area, "spaces": [], "chargers": 0})
        elif am == "parking_space":
            if el["type"] == "node":
                spaces.append({"id": f"n{el['id']}", "tags": t, "pt": to_xy(el["lon"], el["lat"]), "ring": None})
            else:
                pts = strip_closing(geom_xy(el))
                if len(pts) >= 3:
                    spaces.append({"id": f"w{el['id']}", "tags": t, "pt": centroid(pts), "ring": pts})
        elif am == "charging_station":
            if el["type"] == "node":
                pt = to_xy(el["lon"], el["lat"])
            else:
                g = geom_xy(el)
                if not g:
                    continue
                pt = centroid(strip_closing(g)) if len(g) > 2 else g[0]
            chargers.append({"id": f"{el['type'][0]}{el['id']}", "tags": t, "pt": pt})
        elif el["type"] == "way":
            pk = {k: v for k, v in t.items() if k.startswith("parking:")}
            sides = set()
            for k, v in pk.items():
                parts = k.split(":")
                if len(parts) < 2 or v in ("no", "none", "no_parking", "no_stopping", "separate", "fire_lane"):
                    continue
                if parts[1] == "lane" and len(parts) >= 3:
                    side = parts[2]
                    if len(parts) == 3:
                        sides |= {"left", "right"} if side == "both" else ({side} if side in ("left", "right") else set())
                elif parts[1] in ("both", "left", "right") and len(parts) == 2:
                    sides |= {"left", "right"} if parts[1] == "both" else {parts[1]}
            if not sides:
                continue
            g = geom_xy(el)
            if len(g) < 2:
                continue
            length = sum(math.hypot(g[i + 1][0] - g[i][0], g[i + 1][1] - g[i][1]) for i in range(len(g) - 1))
            streets.append({"id": f"w{el['id']}", "tags": t, "line": g, "sides": sorted(sides),
                            "length": length, "pk": pk})

    # assign mapped spaces + chargers to lots
    lot_of_space = {}
    for sp in spaces:
        for lot in lots:
            if point_in_poly(sp["pt"][0], sp["pt"][1], lot["outers"], lot["inners"]):
                lot["spaces"].append(sp)
                lot_of_space[sp["id"]] = lot["id"]
                break
    for ch in chargers:
        for lot in lots:
            if point_in_poly(ch["pt"][0], ch["pt"][1], lot["outers"], lot["inners"]) or \
                    dist_to_rings(ch["pt"][0], ch["pt"][1], lot["outers"]) < 8:
                lot["chargers"] += max(1, to_int(ch["tags"].get("capacity")) or 1)
                ch["lot"] = lot["id"]
                break

    features = []
    stats = dict(lots=len(lots), mapped=0, estimated=0, ada=0, ev=0, streets=len(streets),
                 street_spaces=0, chargers=len(chargers), garages=0, capacity_total=0)
    tgt = (0.0, 0.0)  # Wilkinson is the projection origin

    for lot in lots:
        t = lot["tags"]
        cap = to_int(t.get("capacity"))
        cap_ada = to_int(t.get("capacity:disabled"))
        cap_ev = to_int(t.get("capacity:charging")) or (lot["chargers"] or None)
        ptype = t.get("parking", "surface")
        is_garage = ptype in ("multi-storey", "underground") or "building" in t
        levels = to_int(t.get("building:levels")) or to_int(t.get("levels")) or (4 if ptype == "multi-storey" else 1)
        stall_feats = []
        est_n = 0
        ada_n = ev_n = 0

        if lot["spaces"]:
            tier = "measured"
            for sp in lot["spaces"]:
                st = sp["tags"]
                ada = st.get("parking_space") == "disabled" or st.get("disabled") == "yes" or \
                    st.get("wheelchair") in ("yes", "designated") or (to_int(st.get("capacity:disabled")) or 0) > 0
                ev = st.get("parking_space") == "charging" or (to_int(st.get("capacity:charging")) or 0) > 0
                ada_n += ada
                ev_n += ev
                geom = ({"type": "Polygon", "coordinates": [ring_to_ll(sp["ring"])]} if sp["ring"]
                        else {"type": "Point", "coordinates": list(to_ll(*sp["pt"]))})
                stall_feats.append({"type": "Feature", "geometry": geom, "properties": {
                    "kind": "space", "id": sp["id"], "lot": lot["id"], "estimated": False,
                    "ada": bool(ada), "ev": bool(ev), "row": None, "pos": None, "row_len": None}})
            stats["mapped"] += len(lot["spaces"])
        elif is_garage:
            tier = "counted" if cap else "estimated"
            stats["garages"] += 1
        else:
            tier = "counted" if cap else "estimated"
            stalls, _ = generate_stalls(lot["outers"], lot["inners"], lot["area"])
            # nearest-to-destination stalls first so ADA/EV land near the entrance side
            for st in stalls:
                cx = sum(p[0] for p in st["rect"]) / 4
                cy = sum(p[1] for p in st["rect"]) / 4
                st["c"] = (cx, cy)
                st["d"] = math.hypot(cx - tgt[0], cy - tgt[1])
            if cap:
                # keep the cap closest-to-centroid stalls -> a compact, realistic subset
                lc = centroid(max(lot["outers"], key=lambda r: abs(ring_area(r))))
                stalls.sort(key=lambda s: math.hypot(s["c"][0] - lc[0], s["c"][1] - lc[1]))
                stalls = stalls[:cap]
            # position within row (for "middle stall" scoring)
            rows = {}
            for st in stalls:
                rows.setdefault(st["row"], []).append(st)
            for r_stalls in rows.values():
                # sort along the row direction using projection on first edge
                (ax, ay), (bx, by) = r_stalls[0]["rect"][0], r_stalls[0]["rect"][1]
                ux, uy = bx - ax, by - ay
                r_stalls.sort(key=lambda s: s["c"][0] * ux + s["c"][1] * uy)
                for i, st in enumerate(r_stalls):
                    st["pos"], st["row_len"] = i, len(r_stalls)
            by_d = sorted(stalls, key=lambda s: s["d"])
            n_ada = cap_ada if cap_ada is not None else (max(1, round(len(stalls) / 25)) if len(stalls) >= 10 else 0)
            n_ev = cap_ev or 0
            for i, st in enumerate(by_d):
                st["ada"] = i < n_ada
                st["ev"] = (not st["ada"]) and n_ada <= i < n_ada + n_ev
            for i, st in enumerate(stalls):
                ada_n += st["ada"]
                ev_n += st["ev"]
                stall_feats.append({"type": "Feature",
                                    "geometry": {"type": "Polygon", "coordinates": [ring_to_ll(st["rect"])]},
                                    "properties": {"kind": "space", "id": f"{lot['id']}-e{i}", "lot": lot["id"],
                                                   "estimated": True, "ada": st["ada"], "ev": st["ev"],
                                                   "row": st["row"], "pos": st["pos"], "row_len": st["row_len"]}})
            est_n = len(stalls)
            stats["estimated"] += est_n

        if cap_ada is not None:
            ada_n = max(ada_n, cap_ada) if tier != "measured" else ada_n
        capacity = cap or (len(lot["spaces"]) if lot["spaces"] else None) or est_n or \
            (int(lot["area"] * levels / 30) if is_garage else None) or 0
        stats["ada"] += ada_n
        stats["ev"] += ev_n
        stats["capacity_total"] += capacity
        lc = centroid(max(lot["outers"], key=lambda r: abs(ring_area(r))))
        props = {"kind": "lot", "id": lot["id"], "capacity": capacity, "capacity_tagged": cap,
                 "mapped_spaces": len(lot["spaces"]), "estimated_spaces": est_n,
                 "ada": ada_n, "ev": ev_n, "tier": tier, "garage": is_garage, "levels": levels,
                 "area_m2": round(lot["area"]), "center": list(to_ll(*lc)),
                 "dist_to_wilkinson_m": round(math.hypot(lc[0], lc[1]))}
        for k in TAG_KEYS:
            if k in t:
                props[k] = t[k]
        coords = [ring_to_ll(r) for r in lot["outers"]] if len(lot["outers"]) == 1 else None
        geom = ({"type": "Polygon", "coordinates": coords + [ring_to_ll(h) for h in lot["inners"]]}
                if coords else {"type": "MultiPolygon",
                                "coordinates": [[ring_to_ll(o)] for o in lot["outers"]]})
        features.append({"type": "Feature", "geometry": geom, "properties": props})
        features.extend(stall_feats)

    for sp in spaces:  # mapped spaces outside any lot polygon (e.g. curbside)
        if sp["id"] in lot_of_space:
            continue
        st = sp["tags"]
        ada = st.get("parking_space") == "disabled"
        ev = st.get("parking_space") == "charging"
        stats["mapped"] += 1
        stats["ada"] += ada
        stats["ev"] += ev
        geom = ({"type": "Polygon", "coordinates": [ring_to_ll(sp["ring"])]} if sp["ring"]
                else {"type": "Point", "coordinates": list(to_ll(*sp["pt"]))})
        features.append({"type": "Feature", "geometry": geom, "properties": {
            "kind": "space", "id": sp["id"], "lot": None, "estimated": False, "ada": ada, "ev": ev}})

    for s in streets:
        est = int(s["length"] // 6.1) * len(s["sides"])
        stats["street_spaces"] += est
        props = {"kind": "street", "id": s["id"], "name": s["tags"].get("name"), "sides": s["sides"],
                 "length_m": round(s["length"]), "est_spaces": est}
        props.update(s["pk"])
        features.append({"type": "Feature",
                         "geometry": {"type": "LineString", "coordinates": [list(to_ll(*p)) for p in s["line"]]},
                         "properties": props})
    for ch in chargers:
        t = ch["tags"]
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": list(to_ll(*ch["pt"]))},
                         "properties": {"kind": "charger", "id": ch["id"], "lot": ch.get("lot"),
                                        "name": t.get("name"), "operator": t.get("operator"),
                                        "capacity": to_int(t.get("capacity")), "access": t.get("access"),
                                        "fee": t.get("fee")}})

    fc = {"type": "FeatureCollection",
          "metadata": {"source": "OpenStreetMap contributors via Overpass API",
                       "license": "ODbL 1.0 - https://www.openstreetmap.org/copyright",
                       "osm_timestamp": raw.get("osm3s", {}).get("timestamp_osm_base"),
                       "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                       "center": [CENTER_LON, CENTER_LAT], "radius_m": RADIUS_M,
                       "stall": {"w": STALL_W, "d": STALL_D, "aisle": AISLE}, "summary": stats},
          "features": features}
    return fc, stats


def main():
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    if "--cached" in sys.argv and os.path.exists(RAW_PATH):
        raw = json.load(open(RAW_PATH))
        print(f"Using cached {RAW_PATH}")
    else:
        print(f"Querying Overpass ({RADIUS_M} m around {CENTER_LAT},{CENTER_LON}) ...")
        raw = fetch_overpass()
        json.dump(raw, open(RAW_PATH, "w"))
    print(f"  {len(raw['elements'])} OSM elements")
    fc, s = build(raw)
    with open(OUT_PATH, "w") as f:
        json.dump(fc, f, separators=(",", ":"))
    print(f"\nWrote {OUT_PATH} ({os.path.getsize(OUT_PATH) / 1e6:.1f} MB)")
    print("=" * 46)
    print(f" Parking lots (polygons)        {s['lots']:>8}")
    print(f"   of which garages             {s['garages']:>8}")
    print(f" Mapped spaces (OSM)            {s['mapped']:>8}")
    print(f" Estimated stalls (grid)        {s['estimated']:>8}")
    print(f" ADA spaces                     {s['ada']:>8}")
    print(f" EV spaces                      {s['ev']:>8}")
    print(f" EV charging stations (OSM)     {s['chargers']:>8}")
    print(f" Street parking segments        {s['streets']:>8}")
    print(f"   est. curbside spaces         {s['street_spaces']:>8}")
    print(f" Total lot capacity             {s['capacity_total']:>8}")
    print("=" * 46)
    print("Data (c) OpenStreetMap contributors, ODbL.")


if __name__ == "__main__":
    main()
