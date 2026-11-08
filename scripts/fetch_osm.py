#!/usr/bin/env python3
"""
ParkShark Duke - build the parking inventory from OpenStreetMap (ODbL).

Inputs
  data/osm_raw.json          Overpass response (fetched with scripts/overpass_query.txt)
  data/manual_lots.geojson   lots drawn by hand in the app ("Draw lot" tool)
  data/duke_lots.json        Duke Parking's published lot table (names/capacity/rates)
Output
  data/parking.geojson       kind = lot | space | street | curb | charger | candidate

Stall layout (estimated stalls, 2.74 m x 5.5 m, 6 m aisles):
  1. Along every mapped parking aisle (highway=service + service=parking_aisle):
     perpendicular stalls on both sides, 3 m from the aisle centre line.
  2. Remaining lot area: double-loaded grid oriented along the dominant aisle
     direction, or the polygon's minimum rotated rectangle when no aisle exists;
     the grid offset is searched to maximise fitted stalls.
  Every stall must lie fully inside the lot polygon, must not touch a building,
  must keep clear of roads/aisles, and must not overlap another stall.

Pure standard library.  Usage:
  python3 scripts/fetch_osm.py            # fetch from Overpass + build
  python3 scripts/fetch_osm.py --cached   # rebuild from data/osm_raw.json
"""
import json
import math
import os
import subprocess
import sys
import time

CENTER_LAT, CENTER_LON = 36.00346, -78.93805   # Wilkinson Building (OSM node 5824182200)
RADIUS_M = 1500
OVERPASS_URLS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_PATH = os.path.join(ROOT, "data", "osm_raw.json")
OUT_PATH = os.path.join(ROOT, "data", "parking.geojson")
MANUAL_PATH = os.path.join(ROOT, "data", "manual_lots.geojson")
DUKE_PATH = os.path.join(ROOT, "data", "duke_lots.json")
QUERY_PATH = os.path.join(ROOT, "scripts", "overpass_query.txt")

STALL_W, STALL_D, AISLE = 2.74, 5.5, 6.0
HALF_AISLE = AISLE / 2
MIN_LOT_AREA = 60.0
ROAD_CLEAR = HALF_AISLE + STALL_D / 2 - 0.3   # min distance stall-centre -> other aisle/road centre line

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ======================================================================= fetch
def fetch_overpass():
    query = open(QUERY_PATH).read()
    last = None
    for url in OVERPASS_URLS:
        for attempt in range(3):
            out = subprocess.run(["curl", "-sS", "--fail", "--max-time", "200", "-A", "ParkShark-Duke-hackathon/0.1",
                                  "--data-urlencode", "data=" + query, url], capture_output=True, text=True)
            if out.returncode == 0:
                return json.loads(out.stdout)
            last = out.stderr.strip()
            print(f"  {url} attempt {attempt + 1}: {last}", file=sys.stderr)
            time.sleep(15 * (attempt + 1))
    raise SystemExit("Overpass failed: " + str(last))


# ======================================================================= geometry
M_LAT = 111_320.0
M_LON = 111_320.0 * math.cos(math.radians(CENTER_LAT))


def to_xy(lon, lat):
    return ((lon - CENTER_LON) * M_LON, (lat - CENTER_LAT) * M_LAT)


def to_ll(x, y):
    return [round(CENTER_LON + x / M_LON, 7), round(CENTER_LAT + y / M_LAT, 7)]


def ring_area(r):
    return sum(r[i][0] * r[(i + 1) % len(r)][1] - r[(i + 1) % len(r)][0] * r[i][1] for i in range(len(r))) / 2.0


def centroid(r):
    a = ring_area(r)
    if abs(a) < 1e-9:
        return (sum(p[0] for p in r) / len(r), sum(p[1] for p in r) / len(r))
    cx = cy = 0.0
    for i in range(len(r)):
        (x1, y1), (x2, y2) = r[i], r[(i + 1) % len(r)]
        f = x1 * y2 - x2 * y1
        cx += (x1 + x2) * f
        cy += (y1 + y2) * f
    return (cx / (6 * a), cy / (6 * a))


def point_in_ring(x, y, r):
    inside, j = False, len(r) - 1
    for i in range(len(r)):
        xi, yi = r[i]
        xj, yj = r[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def point_in_poly(x, y, outers, inners):
    return any(point_in_ring(x, y, o) for o in outers) and not any(point_in_ring(x, y, h) for h in inners)


def seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def _orient(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def seg_intersect(a, b, c, d):
    d1, d2, d3, d4 = _orient(c, d, a), _orient(c, d, b), _orient(a, b, c), _orient(a, b, d)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def edges(r):
    return [(r[i], r[(i + 1) % len(r)]) for i in range(len(r))]


def bbox(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def bbox_hit(a, b, pad=0.0):
    return not (a[2] + pad < b[0] or b[2] + pad < a[0] or a[3] + pad < b[1] or b[3] + pad < a[1])


def rect_in_poly(rect, outers, inners):
    """Rect fully inside polygon: all corners inside and no polygon edge crosses a rect edge."""
    if not all(point_in_poly(x, y, outers, inners) for x, y in rect):
        return False
    re = edges(rect)
    for ring in outers + inners:
        for a, b in edges(ring):
            for c, d in re:
                if seg_intersect(a, b, c, d):
                    return False
    return True


def poly_overlap(rect, ring):
    """True if the convex rect and an arbitrary ring overlap (edges cross or containment)."""
    for a, b in edges(rect):
        for c, d in edges(ring):
            if seg_intersect(a, b, c, d):
                return True
    if point_in_ring(ring[0][0], ring[0][1], rect):
        return True
    cx = sum(p[0] for p in rect) / 4
    cy = sum(p[1] for p in rect) / 4
    return point_in_ring(cx, cy, ring)


def convex_overlap(p, q):
    """Separating axis test for two convex polygons (shrunk slightly so touching stalls are OK)."""
    for poly in (p, q):
        for (a, b) in edges(poly):
            nx, ny = a[1] - b[1], b[0] - a[0]
            pa = [nx * v[0] + ny * v[1] for v in p]
            qa = [nx * v[0] + ny * v[1] for v in q]
            L = math.hypot(nx, ny) or 1
            if max(pa) <= min(qa) + 0.05 * L or max(qa) <= min(pa) + 0.05 * L:
                return False
    return True


def convex_hull(pts):
    pts = sorted(set(pts))
    if len(pts) < 3:
        return pts
    lo, hi = [], []
    for p in pts:
        while len(lo) >= 2 and _orient(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    for p in reversed(pts):
        while len(hi) >= 2 and _orient(hi[-2], hi[-1], p) <= 0:
            hi.pop()
        hi.append(p)
    return lo[:-1] + hi[:-1]


def mrr_angle(ring):
    """Orientation of the minimum-area rotated rectangle (rotating calipers over hull edges)."""
    hull = convex_hull([tuple(p) for p in ring])
    best = (float("inf"), 0.0)
    for (a, b) in edges(hull):
        th = math.atan2(b[1] - a[1], b[0] - a[0])
        c, s = math.cos(-th), math.sin(-th)
        xs = [p[0] * c - p[1] * s for p in hull]
        ys = [p[0] * s + p[1] * c for p in hull]
        area = (max(xs) - min(xs)) * (max(ys) - min(ys))
        if area < best[0]:
            best = (area, th)
    return best[1]


def strip_closing(r):
    return r[:-1] if len(r) > 1 and r[0] == r[-1] else r


def join_ways(segments):
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


class Grid:
    """Tiny spatial hash on bounding boxes."""
    def __init__(self, cell=25.0):
        self.cell, self.d = cell, {}

    def _cells(self, bb):
        c = self.cell
        for i in range(int(math.floor(bb[0] / c)), int(math.floor(bb[2] / c)) + 1):
            for j in range(int(math.floor(bb[1] / c)), int(math.floor(bb[3] / c)) + 1):
                yield (i, j)

    def add(self, bb, item):
        for k in self._cells(bb):
            self.d.setdefault(k, []).append((bb, item))

    def query(self, bb, pad=0.0):
        seen, out = set(), []
        q = (bb[0] - pad, bb[1] - pad, bb[2] + pad, bb[3] + pad)
        for k in self._cells(q):
            for b, it in self.d.get(k, ()):
                if id(it) not in seen and bbox_hit(b, q):
                    seen.add(id(it))
                    out.append(it)
        return out


# ======================================================================= stall layout
class Context:
    def __init__(self, buildings, roads):
        self.bgrid = Grid(40)
        for ring in buildings:
            self.bgrid.add(bbox(ring), ring)
        self.rgrid = Grid(40)       # road / aisle segments: (a, b, way_id)
        for wid, line in roads:
            for i in range(len(line) - 1):
                seg = (line[i], line[i + 1], wid, i)
                self.rgrid.add(bbox([line[i], line[i + 1]]), seg)
        self.sgrid = Grid(15)       # placed stalls

    def clear_of_world(self, rect, own=None):
        bb = bbox(rect)
        for ring in self.bgrid.query(bb):
            if poly_overlap(rect, ring):
                return False
        cx = sum(p[0] for p in rect) / 4
        cy = sum(p[1] for p in rect) / 4
        for (a, b, wid, i) in self.rgrid.query(bb, pad=ROAD_CLEAR + 1):
            if own is not None and (wid, i) == own:
                continue
            if seg_dist(cx, cy, a[0], a[1], b[0], b[1]) < ROAD_CLEAR:
                return False
            for c, d in edges(rect):
                if seg_intersect(a, b, c, d):
                    return False
        for other in self.sgrid.query(bb):
            if convex_overlap(rect, other):
                return False
        return True

    def place(self, rect):
        self.sgrid.add(bbox(rect), rect)


def aisle_stalls(ctx, aisle_id, line, outers=None, inners=None, skip_lots=None):
    """Perpendicular stalls on both sides of an aisle centre line."""
    out = []
    for i in range(len(line) - 1):
        (ax, ay), (bx, by) = line[i], line[i + 1]
        L = math.hypot(bx - ax, by - ay)
        if L < STALL_W:
            continue
        ux, uy = (bx - ax) / L, (by - ay) / L
        nx, ny = -uy, ux
        k = int(L // STALL_W)
        off = (L - k * STALL_W) / 2
        for side in (1, -1):
            for j in range(k):
                px, py = ax + ux * (off + j * STALL_W), ay + uy * (off + j * STALL_W)
                h0, h1 = side * HALF_AISLE, side * (HALF_AISLE + STALL_D)
                rect = [(px + nx * h0, py + ny * h0), (px + ux * STALL_W + nx * h0, py + uy * STALL_W + ny * h0),
                        (px + ux * STALL_W + nx * h1, py + uy * STALL_W + ny * h1), (px + nx * h1, py + ny * h1)]
                if outers is not None and not rect_in_poly(rect, outers, inners):
                    continue
                if skip_lots is not None:
                    cx = sum(p[0] for p in rect) / 4
                    cy = sum(p[1] for p in rect) / 4
                    if any(bbox_hit(lb, (cx, cy, cx, cy)) and point_in_poly(cx, cy, lo, li) for lb, lo, li in skip_lots):
                        continue
                if not ctx.clear_of_world(rect, own=(aisle_id, i)):
                    continue
                ctx.place(rect)
                out.append({"rect": rect, "row": f"a{aisle_id}.{i}.{'L' if side > 0 else 'R'}",
                            "t": j, "src": "aisle"})
    return out


def grid_stalls(ctx, outers, inners, theta, row_prefix):
    """Double-loaded grid (stall | 6 m aisle | stall) rotated by theta; best of several offsets."""
    c, s = math.cos(-theta), math.sin(-theta)
    rot = lambda p: (p[0] * c - p[1] * s, p[0] * s + p[1] * c)          # noqa: E731
    ci, si = math.cos(theta), math.sin(theta)
    unrot = lambda p: (p[0] * ci - p[1] * si, p[0] * si + p[1] * ci)    # noqa: E731
    R_out = [[rot(p) for p in r] for r in outers]
    minx, miny, maxx, maxy = bbox([p for r in R_out for p in r])
    module = 2 * STALL_D + AISLE

    def layout(ox, oy, commit):
        res = []
        y = miny - module + oy
        row = 0
        while y < maxy:
            for y0 in (y, y + STALL_D + AISLE):
                x = minx - STALL_W + ox
                while x < maxx:
                    r = [unrot(p) for p in ((x, y0), (x + STALL_W, y0), (x + STALL_W, y0 + STALL_D), (x, y0 + STALL_D))]
                    if rect_in_poly(r, outers, inners) and ctx.clear_of_world(r):
                        if commit:
                            ctx.place(r)
                        res.append({"rect": r, "row": f"{row_prefix}g{row}", "t": x, "src": "grid"})
                    x += STALL_W
                row += 1
            y += module
        return res

    best, best_n = (0, 0), -1
    for oy in (0.0, module * 0.25, module * 0.5, module * 0.75):
        for ox in (0.0, STALL_W / 2):
            n = len(layout(ox, oy, False))
            if n > best_n:
                best, best_n = (ox, oy), n
    return layout(best[0], best[1], True) if best_n > 0 else []


# ======================================================================= helpers
def to_int(v):
    if v is None:
        return None
    try:
        return int(str(v).split(";")[0].strip())
    except ValueError:
        return None


def gxy(el):
    return [to_xy(p["lon"], p["lat"]) for p in el.get("geometry", []) if p]


def ring_ll(r):
    pts = [to_ll(*p) for p in r]
    return pts + [pts[0]]


def dist_to_rings(x, y, rings):
    return min(seg_dist(x, y, a[0], a[1], b[0], b[1]) for r in rings for a, b in edges(r))


TAG_KEYS = ["name", "operator", "access", "fee", "capacity", "capacity:disabled", "capacity:charging", "parking",
            "surface", "building:levels", "levels", "maxstay", "opening_hours", "ref", "supervised", "covered"]
ROAD_TYPES = {"primary", "secondary", "tertiary", "residential", "unclassified", "service", "living_street",
              "primary_link", "secondary_link", "tertiary_link"}


# ======================================================================= build
def build(raw):
    els = raw["elements"]
    lots, spaces, streets, chargers, aisles, roads, buildings, candidates = [], [], [], [], [], [], [], []
    road_named = {}
    nodes_block = []          # (x, y, kind) crossing / bus stop / signals for curb cut-outs

    for el in els:
        t = el.get("tags", {})
        am, typ = t.get("amenity"), el["type"]
        if am == "parking" and typ in ("way", "relation"):
            if typ == "way":
                pts = strip_closing(gxy(el))
                if len(pts) < 3:
                    continue
                outers, inners = [pts], []
            else:
                mem = [m for m in el.get("members", []) if m.get("type") == "way"]
                outers = join_ways([gxy(m) for m in mem if m.get("role") != "inner"])
                inners = join_ways([gxy(m) for m in mem if m.get("role") == "inner"])
                if not outers:
                    continue
            area = sum(abs(ring_area(r)) for r in outers) - sum(abs(ring_area(r)) for r in inners)
            if area >= MIN_LOT_AREA:
                lots.append({"id": f"{typ[0]}{el['id']}", "tags": t, "outers": outers, "inners": inners,
                             "area": area, "spaces": [], "chargers": 0, "source": "osm"})
        elif am == "parking_space":
            if typ == "node":
                spaces.append({"id": f"n{el['id']}", "tags": t, "pt": to_xy(el["lon"], el["lat"]), "ring": None})
            elif typ == "way":
                pts = strip_closing(gxy(el))
                if len(pts) >= 3:
                    spaces.append({"id": f"w{el['id']}", "tags": t, "pt": centroid(pts), "ring": pts})
        elif am == "charging_station":
            if typ == "node":
                pt = to_xy(el["lon"], el["lat"])
            else:
                g = gxy(el)
                if not g:
                    continue
                pt = centroid(strip_closing(g)) if len(g) > 2 else g[0]
            chargers.append({"id": f"{typ[0]}{el['id']}", "tags": t, "pt": pt})
        elif typ == "way" and "building" in t:
            pts = strip_closing(gxy(el))
            if len(pts) >= 3 and t.get("building") not in ("roof", "carport") and t.get("parking") != "multi-storey":
                buildings.append(pts)
        elif typ == "way" and t.get("highway") in ROAD_TYPES:
            g = gxy(el)
            if len(g) < 2:
                continue
            wid = f"w{el['id']}"
            if t.get("highway") == "service" and t.get("service") == "parking_aisle":
                aisles.append({"id": wid, "line": g, "tags": t})
            else:
                roads.append({"id": wid, "line": g, "tags": t})
                if t.get("name"):
                    road_named.setdefault(t["name"], []).append(g)
        elif typ == "node" and (t.get("highway") in ("crossing", "bus_stop", "traffic_signals", "stop")
                                or t.get("public_transport") == "platform"):
            nodes_block.append((to_xy(el["lon"], el["lat"]), t.get("highway") or "platform"))
        elif typ == "way" and (t.get("landuse") in ("garages", "parking") or
                               (t.get("area") == "yes" and t.get("surface") in ("asphalt", "paved", "concrete"))):
            pts = strip_closing(gxy(el))
            if len(pts) >= 3 and abs(ring_area(pts)) > 150:
                candidates.append({"id": f"w{el['id']}", "ring": pts, "tags": t})

        # street parking tags (parking:lane / parking:both|left|right) on any way
        if typ == "way" and "highway" in t:
            pk = {k: v for k, v in t.items() if k.startswith("parking:")}
            sides = set()
            for k, v in pk.items():
                parts = k.split(":")
                if v in ("no", "none", "no_parking", "no_stopping", "separate", "fire_lane"):
                    continue
                if parts[1] == "lane" and len(parts) == 3:
                    sides |= {"left", "right"} if parts[2] == "both" else ({parts[2]} & {"left", "right"})
                elif parts[1] in ("both", "left", "right") and len(parts) == 2:
                    sides |= {"left", "right"} if parts[1] == "both" else {parts[1]}
            if sides:
                g = gxy(el)
                length = sum(math.hypot(g[i + 1][0] - g[i][0], g[i + 1][1] - g[i][1]) for i in range(len(g) - 1))
                streets.append({"id": f"w{el['id']}", "tags": t, "line": g, "sides": sorted(sides), "length": length, "pk": pk})

    # ---- manual lots drawn in the app
    n_manual = 0
    if os.path.exists(MANUAL_PATH):
        for i, f in enumerate(json.load(open(MANUAL_PATH)).get("features", [])):
            g = f.get("geometry") or {}
            polys = [g["coordinates"]] if g.get("type") == "Polygon" else g.get("coordinates", []) if g.get("type") == "MultiPolygon" else []
            for poly in polys:
                outers = [strip_closing([to_xy(*c[:2]) for c in poly[0]])]
                inners = [strip_closing([to_xy(*c[:2]) for c in h]) for h in poly[1:]]
                area = abs(ring_area(outers[0])) - sum(abs(ring_area(h)) for h in inners)
                if area < MIN_LOT_AREA:
                    continue
                p = f.get("properties") or {}
                tags = {k: str(v) for k, v in p.items() if k in TAG_KEYS and v not in (None, "")}
                lots.append({"id": f"m{i + 1}", "tags": tags, "outers": outers, "inners": inners, "area": area,
                             "spaces": [], "chargers": 0, "source": "manual"})
                n_manual += 1

    for lot in lots:
        lot["bb"] = bbox([p for r in lot["outers"] for p in r])

    # ---- service ways inside a lot are its circulation aisles too
    for r in list(roads):
        if r["tags"].get("highway") != "service" or r["tags"].get("service") in ("alley", "drive-through"):
            continue
        mx, my = r["line"][len(r["line"]) // 2]
        if r["tags"].get("service") in (None, "driveway") and any(
                bbox_hit(l["bb"], (mx, my, mx, my)) and point_in_poly(mx, my, l["outers"], l["inners"]) for l in lots):
            aisles.append(r)
            roads.remove(r)

    ctx = Context(buildings, [(a["id"], a["line"]) for a in aisles] + [(r["id"], r["line"]) for r in roads])

    # ---- mapped spaces & chargers -> lots
    lot_of_space = {}
    for sp in spaces:
        for lot in lots:
            if bbox_hit(lot["bb"], (*sp["pt"], *sp["pt"])) and point_in_poly(*sp["pt"], lot["outers"], lot["inners"]):
                lot["spaces"].append(sp)
                lot_of_space[sp["id"]] = lot["id"]
                break
        if sp["ring"]:
            ctx.place(sp["ring"] if len(sp["ring"]) == 4 else convex_hull(sp["ring"]))
    for ch in chargers:
        for lot in lots:
            if point_in_poly(*ch["pt"], lot["outers"], lot["inners"]) or dist_to_rings(*ch["pt"], lot["outers"]) < 8:
                lot["chargers"] += max(1, to_int(ch["tags"].get("capacity")) or 1)
                ch["lot"] = lot["id"]
                break

    # ---- Duke names
    duke = match_duke(lots, road_named)

    # ---- aisles -> lots
    for a in aisles:
        a["lot"] = None
        pts = a["line"]
        inside = [l for l in lots if bbox_hit(l["bb"], bbox(pts), 2) and
                  sum(point_in_poly(x, y, l["outers"], l["inners"]) for x, y in pts) * 2 >= len(pts)]
        if inside:
            a["lot"] = inside[0]["id"]
    aisles_by_lot = {}
    for a in aisles:
        if a["lot"]:
            aisles_by_lot.setdefault(a["lot"], []).append(a)

    features = []
    stats = dict(lots=0, garages=0, mapped=0, estimated=0, est_aisle=0, est_grid=0, ada=0, ev=0, streets=len(streets),
                 street_spaces=0, chargers=len(chargers), capacity_total=0, manual=n_manual, aisle_lots=0,
                 duke_named=0, candidates=0, aisles=len(aisles))

    def emit_lot(lot, stalls, tier, extra):
        t = lot["tags"]
        cap_ada = to_int(t.get("capacity:disabled"))
        cap_ev = to_int(t.get("capacity:charging")) or (lot["chargers"] or None)
        for st in stalls:
            st["c"] = (sum(p[0] for p in st["rect"]) / 4, sum(p[1] for p in st["rect"]) / 4)
            st["d"] = math.hypot(*st["c"])
        rows = {}
        for st in stalls:
            rows.setdefault(st["row"], []).append(st)
        for rs in rows.values():
            rs.sort(key=lambda s: s["t"])
            for i, st in enumerate(rs):
                st["pos"], st["row_len"] = i, len(rs)
        by_d = sorted(stalls, key=lambda s: s["d"])
        n_ada = cap_ada if cap_ada is not None else (max(1, round(len(stalls) / 25)) if len(stalls) >= 10 else 0)
        n_ev = cap_ev or 0
        for i, st in enumerate(by_d):
            st["ada"] = i < n_ada
            st["ev"] = (not st["ada"]) and n_ada <= i < n_ada + n_ev
        feats = []
        for i, st in enumerate(stalls):
            feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring_ll(st["rect"])]},
                          "properties": {"kind": "space", "id": f"{lot['id']}-e{i}", "lot": lot["id"], "estimated": True,
                                         "ada": st["ada"], "ev": st["ev"], "row": st["row"], "pos": st["pos"],
                                         "row_len": st["row_len"], "layout": st["src"]}})
        return feats, sum(s["ada"] for s in stalls), sum(s["ev"] for s in stalls)

    for lot in lots:
        t = lot["tags"]
        d = duke.get(lot["id"])
        cap = to_int(t.get("capacity")) or (d["capacity"] if d and not d.get("zone") else None)
        ptype = t.get("parking", "surface")
        is_garage = ptype in ("multi-storey", "underground") or "building" in t
        levels = to_int(t.get("building:levels")) or to_int(t.get("levels")) or (4 if ptype == "multi-storey" else 1)
        stall_feats, est_n, ada_n, ev_n, theta = [], 0, 0, 0, None
        layout = None

        if lot["spaces"]:
            tier = "measured"
            for sp in lot["spaces"]:
                st = sp["tags"]
                ada = st.get("parking_space") == "disabled" or st.get("wheelchair") in ("yes", "designated") or \
                    (to_int(st.get("capacity:disabled")) or 0) > 0
                ev = st.get("parking_space") == "charging" or (to_int(st.get("capacity:charging")) or 0) > 0
                ada_n += ada
                ev_n += ev
                geom = ({"type": "Polygon", "coordinates": [ring_ll(sp["ring"])]} if sp["ring"]
                        else {"type": "Point", "coordinates": to_ll(*sp["pt"])})
                stall_feats.append({"type": "Feature", "geometry": geom, "properties": {
                    "kind": "space", "id": sp["id"], "lot": lot["id"], "estimated": False, "ada": bool(ada),
                    "ev": bool(ev), "row": None, "pos": None, "row_len": None, "layout": "mapped"}})
            stats["mapped"] += len(lot["spaces"])
        elif is_garage:
            tier = "counted" if cap else "estimated"
            stats["garages"] += 1
        else:
            tier = "counted" if cap else "estimated"
            stalls = []
            la = aisles_by_lot.get(lot["id"], [])
            for a in la:
                stalls += aisle_stalls(ctx, a["id"], a["line"], lot["outers"], lot["inners"])
            if la:
                # dominant aisle direction (length-weighted, mod 180deg)
                sx = sy = 0.0
                for a in la:
                    for (p, q) in zip(a["line"], a["line"][1:]):
                        L = math.hypot(q[0] - p[0], q[1] - p[1])
                        ang = 2 * math.atan2(q[1] - p[1], q[0] - p[0])
                        sx += L * math.cos(ang)
                        sy += L * math.sin(ang)
                theta = math.atan2(sy, sx) / 2
            else:
                theta = mrr_angle(max(lot["outers"], key=lambda r: abs(ring_area(r))))
            g = grid_stalls(ctx, lot["outers"], lot["inners"], theta, lot["id"])
            n_a = len(stalls)
            stalls += g
            layout = "aisle+grid" if n_a and g else ("aisle" if n_a else "grid")
            if cap and len(stalls) > cap:
                lc = centroid(max(lot["outers"], key=lambda r: abs(ring_area(r))))
                stalls.sort(key=lambda s: (s["src"] != "aisle",
                                           math.hypot(sum(p[0] for p in s["rect"]) / 4 - lc[0],
                                                      sum(p[1] for p in s["rect"]) / 4 - lc[1])))
                stalls = stalls[:cap]
            stall_feats, ada_n, ev_n = emit_lot(lot, stalls, tier, None)
            est_n = len(stalls)
            stats["estimated"] += est_n
            stats["est_aisle"] += sum(s["src"] == "aisle" for s in stalls)
            stats["est_grid"] += sum(s["src"] == "grid" for s in stalls)

        cap_ada = to_int(t.get("capacity:disabled"))
        if cap_ada is not None and tier != "measured":
            ada_n = max(ada_n, cap_ada)
        capacity = cap or len(lot["spaces"]) or est_n or (int(lot["area"] * levels / 30) if is_garage else 0)
        lc = centroid(max(lot["outers"], key=lambda r: abs(ring_area(r))))
        props = {"kind": "lot", "id": lot["id"], "source": lot["source"], "capacity": capacity,
                 "capacity_tagged": to_int(t.get("capacity")), "mapped_spaces": len(lot["spaces"]),
                 "estimated_spaces": est_n, "ada": ada_n, "ev": ev_n, "tier": tier, "garage": is_garage,
                 "levels": levels, "area_m2": round(lot["area"]), "center": to_ll(*lc),
                 "dist_to_wilkinson_m": round(math.hypot(*lc)), "layout": layout,
                 "orientation_deg": None if theta is None else round(math.degrees(theta) % 180, 1)}
        for k in TAG_KEYS:
            if k in t:
                props[k if k not in props else "osm_" + k] = t[k]
        if d:
            props.update({"duke_name": d["name"], "duke_match": d["match"], "duke_capacity": d["capacity"],
                          "duke_gated": d.get("gated"), "duke_rate_tier": d.get("rate_tier"),
                          "duke_annual": d.get("annual"), "duke_zone": bool(d.get("zone"))})
            stats["duke_named"] += 1
        stats["lots"] += 1
        stats["ada"] += ada_n
        stats["ev"] += ev_n
        stats["capacity_total"] += capacity
        geom = ({"type": "Polygon", "coordinates": [ring_ll(lot["outers"][0])] + [ring_ll(h) for h in lot["inners"]]}
                if len(lot["outers"]) == 1 else
                {"type": "MultiPolygon", "coordinates": [[ring_ll(o)] for o in lot["outers"]]})
        features.append({"type": "Feature", "geometry": geom, "properties": props})
        features.extend(stall_feats)

    # ---- parking aisles that are not inside any mapped lot -> unmapped lots
    skip = [(l["bb"], l["outers"], l["inners"]) for l in lots]
    for a in aisles:
        if a["lot"]:
            continue
        stalls = aisle_stalls(ctx, a["id"], a["line"], skip_lots=skip)
        if len(stalls) < 6:
            continue
        hull = convex_hull([p for s in stalls for p in s["rect"]] + [tuple(p) for p in a["line"]])
        lot = {"id": "a" + a["id"][1:], "tags": {}, "outers": [hull], "inners": [], "area": abs(ring_area(hull)),
               "spaces": [], "chargers": 0, "source": "parking_aisle"}
        feats, ada_n, ev_n = emit_lot(lot, stalls, "estimated", None)
        lc = centroid(hull)
        features.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring_ll(hull)]},
                         "properties": {"kind": "lot", "id": lot["id"], "source": "parking_aisle",
                                        "capacity": len(stalls), "capacity_tagged": None, "mapped_spaces": 0,
                                        "estimated_spaces": len(stalls), "ada": ada_n, "ev": ev_n,
                                        "tier": "estimated", "garage": False, "levels": 1,
                                        "area_m2": round(lot["area"]), "center": to_ll(*lc),
                                        "dist_to_wilkinson_m": round(math.hypot(*lc)), "layout": "aisle",
                                        "osm_aisle": a["id"]}})
        features.extend(feats)
        stats.update(lots=stats["lots"] + 1, aisle_lots=stats["aisle_lots"] + 1, ada=stats["ada"] + ada_n,
                     estimated=stats["estimated"] + len(stalls), est_aisle=stats["est_aisle"] + len(stalls),
                     capacity_total=stats["capacity_total"] + len(stalls))
        skip.append((bbox(hull), [hull], []))

    # ---- paved / landuse candidates not covered by a lot (outline only)
    for c in candidates:
        cx, cy = centroid(c["ring"])
        if any(point_in_poly(cx, cy, o, i) for _, o, i in skip):
            continue
        features.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring_ll(c["ring"])]},
                         "properties": {"kind": "candidate", "id": c["id"], "area_m2": round(abs(ring_area(c["ring"]))),
                                        "tags": {k: v for k, v in c["tags"].items() if k in ("landuse", "surface", "area:highway", "name")}}})
        stats["candidates"] += 1

    for sp in spaces:
        if sp["id"] in lot_of_space:
            continue
        st = sp["tags"]
        ada, ev = st.get("parking_space") == "disabled", st.get("parking_space") == "charging"
        stats["mapped"] += 1
        geom = ({"type": "Polygon", "coordinates": [ring_ll(sp["ring"])]} if sp["ring"]
                else {"type": "Point", "coordinates": to_ll(*sp["pt"])})
        features.append({"type": "Feature", "geometry": geom, "properties": {
            "kind": "space", "id": sp["id"], "lot": None, "estimated": False, "ada": ada, "ev": ev, "layout": "mapped"}})

    for s in streets:
        est = int(s["length"] // 6.1) * len(s["sides"])
        stats["street_spaces"] += est
        props = {"kind": "street", "id": s["id"], "name": s["tags"].get("name"), "sides": s["sides"],
                 "length_m": round(s["length"]), "est_spaces": est}
        props.update(s["pk"])
        features.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": [to_ll(*p) for p in s["line"]]},
                         "properties": props})
    for ch in chargers:
        t = ch["tags"]
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": to_ll(*ch["pt"])},
                         "properties": {"kind": "charger", "id": ch["id"], "lot": ch.get("lot"), "name": t.get("name"),
                                        "operator": t.get("operator"), "capacity": to_int(t.get("capacity")),
                                        "access": t.get("access"), "fee": t.get("fee")}})

    # ---- candidate curb parking (P2)
    try:
        import curbs
        curb_feats, curb_stats = curbs.build_curbs(roads, aisles, lots, buildings, nodes_block, ctx, to_ll, CENTER_LAT)
        features.extend(curb_feats)
        stats.update(curb_stats)
    except ImportError:
        pass

    fc = {"type": "FeatureCollection",
          "metadata": {"source": "OpenStreetMap contributors via Overpass API",
                       "license": "ODbL 1.0 - https://www.openstreetmap.org/copyright",
                       "osm_timestamp": raw.get("osm3s", {}).get("timestamp_osm_base"),
                       "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                       "center": [CENTER_LON, CENTER_LAT], "radius_m": RADIUS_M,
                       "stall": {"w": STALL_W, "d": STALL_D, "aisle": AISLE}, "summary": stats,
                       "duke_source": "https://parking.duke.edu/parking-permits/garages-lots-fees/"},
          "features": features}
    return fc, stats


def match_duke(lots, road_named):
    """Attach Duke's published lot names to OSM lots (exact OSM name first, then proximity to the anchor)."""
    if not os.path.exists(DUKE_PATH):
        return {}
    table = json.load(open(DUKE_PATH))["lots"]
    out = {}

    def lot_type(l):
        t = l["tags"]
        return "garage" if t.get("parking") in ("multi-storey", "underground") or "building" in t else "surface"

    def est_cap(l):
        return to_int(l["tags"].get("capacity")) or max(1, l["area"] / 28)

    for d in table:   # 0) explicit OSM id chosen by a human (see osm_id_note)
        if d.get("osm_id"):
            for l in lots:
                if l["id"] == d["osm_id"]:
                    out[l["id"]] = dict(d, match="manual")
    for d in table:   # 1) exact OSM name
        if d.get("osm_name"):
            for l in lots:
                if l["tags"].get("name") == d["osm_name"]:
                    out[l["id"]] = dict(d, match="osm_name")
    for d in table:   # 2) proximity
        if any(v["name"] == d["name"] for v in out.values()):
            continue
        if d.get("anchor"):
            ax, ay = to_xy(d["anchor"][1], d["anchor"][0])
            dist = lambda l: 0 if point_in_poly(ax, ay, l["outers"], l["inners"]) else dist_to_rings(ax, ay, l["outers"])  # noqa: E731
        elif d.get("anchor_road") and d["anchor_road"] in road_named:
            lines = road_named[d["anchor_road"]]
            if d.get("anchor_near"):
                nx, ny = to_xy(d["anchor_near"][1], d["anchor_near"][0])
                lines = [min(lines, key=lambda g: min(math.hypot(p[0] - nx, p[1] - ny) for p in g))]

            def dist(l, lines=lines):
                return min(seg_dist(p[0], p[1], a[0], a[1], b[0], b[1]) for r in l["outers"] for p in r
                           for g in lines for a, b in zip(g, g[1:]))
        else:
            continue
        maxd = d.get("max_dist", 200)
        cands = [l for l in lots if l["id"] not in out and l["source"] != "manual" and lot_type(l) == d["type"]
                 and not l["tags"].get("name")]          # never rename a lot OSM already names
        cands = [(dist(l), l) for l in cands if math.hypot(*centroid(l["outers"][0])) < 4000]
        cands = [(dd, l) for dd, l in cands if dd <= maxd]
        if d.get("zone"):
            for dd, l in cands:
                if l["area"] >= d.get("min_area", 0):
                    out[l["id"]] = dict(d, match="zone")
            continue
        if not cands:
            continue
        score = lambda c: c[0] + 60 * abs(math.log(est_cap(c[1]) / d["capacity"]))  # noqa: E731
        best = min(cands, key=score)
        out[best[1]["id"]] = dict(d, match="proximity")
    return out


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
    t0 = time.time()
    fc, s = build(raw)
    with open(OUT_PATH, "w") as f:
        json.dump(fc, f, separators=(",", ":"))
    print(f"\nWrote {OUT_PATH} ({os.path.getsize(OUT_PATH) / 1e6:.1f} MB, {time.time() - t0:.0f}s)")
    print("=" * 52)
    rows = [("Parking lots (all)", "lots"), ("  garages", "garages"), ("  unmapped lots found via parking aisles", "aisle_lots"),
            ("  manual lots (data/manual_lots.geojson)", "manual"), ("  with Duke names", "duke_named"),
            ("Mapped spaces (OSM)", "mapped"), ("Estimated stalls", "estimated"), ("  along mapped aisles", "est_aisle"),
            ("  grid fill", "est_grid"), ("ADA spaces", "ada"), ("EV spaces", "ev"), ("EV charging stations (OSM)", "chargers"),
            ("Street parking segments (OSM tags)", "streets"), ("Candidate curb segments", "curb_segments"),
            ("  candidate curb spaces", "curb_spaces"), ("Paved candidates (outline only)", "candidates"),
            ("Total lot capacity", "capacity_total")]
    for label, k in rows:
        if k in s:
            print(f" {label:<42}{s[k]:>8}")
    print("=" * 52)
    print("Data (c) OpenStreetMap contributors, ODbL.")
    try:
        import add_elevation
        add_elevation.main()
    except Exception as e:  # noqa: BLE001 - elevation is optional
        print("  (elevation skipped:", e, ")")


if __name__ == "__main__":
    main()
