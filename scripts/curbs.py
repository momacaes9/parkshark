"""
Candidate curb parking for ParkShark Duke (P2).

OSM has no parking:lane tags around Duke, so we generate *candidates*:
  - campus roads (highway=residential|tertiary|unclassified|service) within CURB_RADIUS_M
  - offset 3 m to each side of the centre line
  - cut 10 m around intersections, crosswalks, driveways, bus stops, signals/stop signs
  - drop curb sections inside parking lots or buildings, or that run into another road
  - split the remaining runs into 6.1 m parallel spaces
Every segment is confidence "candidate - unverified" until the CV step (scripts/cv_verify.py)
sees parked cars on it, which upgrades it to "observed parking".
"""
import math

CURB_RADIUS_M = 1000
OFFSET = 3.0          # curb line distance from the road centre line
CUT = 10.0            # clearance around intersections / crossings / driveways / bus stops
SPACE_LEN = 6.1       # parallel parking space length
SPACE_W = 2.4
STEP = 0.5            # sampling step along the road
CURB_ROADS = {"residential", "tertiary", "unclassified", "service"}
SKIP_SERVICE = {"parking_aisle", "driveway", "drive-through", "alley", "emergency_access"}


def _pip(x, y, r):
    inside, j = False, len(r) - 1
    for i in range(len(r)):
        xi, yi = r[i]
        xj, yj = r[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _segd(px, py, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((px - a[0]) * dx + (py - a[1]) * dy) / L))
    return math.hypot(px - a[0] - t * dx, py - a[1] - t * dy)


def build_curbs(roads, aisles, lots, buildings, nodes_block, ctx, to_ll, center_lat):
    # ---- cut points: shared vertices between any two road/aisle ways (= intersections & driveways)
    count = {}
    for w in roads + aisles:
        for p in set(w["line"]):
            count[p] = count.get(p, 0) + 1
    cuts = [p for p, n in count.items() if n >= 2]
    for w in roads + aisles:              # dead ends that touch another way mid-segment are rare; ends of
        for p in (w["line"][0], w["line"][-1]):   # driveways/aisles always cut
            if w in aisles or w["tags"].get("service") in SKIP_SERVICE:
                cuts.append(p)
    cuts += [p for p, _ in nodes_block]
    cut_grid = {}
    for p in cuts:
        cut_grid.setdefault((int(p[0] // 20), int(p[1] // 20)), []).append(p)

    def near_cut(x, y):
        gx, gy = int(x // 20), int(y // 20)
        for i in (gx - 1, gx, gx + 1):
            for j in (gy - 1, gy, gy + 1):
                for p in cut_grid.get((i, j), ()):
                    if (p[0] - x) ** 2 + (p[1] - y) ** 2 < CUT * CUT:
                        return True
        return False

    def blocked(x, y, own_id):
        if near_cut(x, y):
            return True
        bb = (x, y, x, y)
        for ring in ctx.bgrid.query(bb, pad=0.5):
            if _pip(x, y, ring):
                return True
        for l in lots:
            b = l["bb"]
            if b[0] <= x <= b[2] and b[1] <= y <= b[3] and any(_pip(x, y, o) for o in l["outers"]):
                return True
        for (a, b, wid, _) in ctx.rgrid.query(bb, pad=OFFSET + 1):
            if wid != own_id and _segd(x, y, a, b) < OFFSET - 0.4:   # runs into another road
                return True
        return False

    feats, n_seg, n_spaces = [], 0, 0
    for w in roads:
        t = w["tags"]
        if t.get("highway") not in CURB_ROADS or t.get("service") in SKIP_SERVICE:
            continue
        if t.get("access") in ("private", "no") or t.get("tunnel") or t.get("bridge"):
            continue
        if t.get("junction") in ("roundabout", "circular"):
            continue
        if t.get("highway") == "service" and not t.get("name"):   # unnamed service = loading docks, paths
            continue
        line = w["line"]
        if min(math.hypot(*p) for p in line) > CURB_RADIUS_M:
            continue
        oneway = t.get("oneway") == "yes"
        for side, sname in ((1, "left"), (-1, "right")):
            run, runs = [], []
            for i in range(len(line) - 1):
                (ax, ay), (bx, by) = line[i], line[i + 1]
                L = math.hypot(bx - ax, by - ay)
                if L < 1:
                    continue
                ux, uy = (bx - ax) / L, (by - ay) / L
                nx, ny = -uy * side, ux * side
                s = 0.0
                while s <= L:
                    cx, cy = ax + ux * s, ay + uy * s
                    px, py = cx + nx * OFFSET, cy + ny * OFFSET
                    if blocked(px, py, w["id"]) or blocked(cx + nx * (OFFSET + SPACE_W / 2), cy + ny * (OFFSET + SPACE_W / 2), w["id"]):
                        if run:
                            runs.append(run)
                        run = []
                    else:
                        run.append((px, py, ux, uy, nx, ny))
                    s += STEP
                # break runs at sharp bends
                if run and i + 2 < len(line):
                    (cx2, cy2) = line[i + 2]
                    L2 = math.hypot(cx2 - bx, cy2 - by) or 1
                    if (ux * (cx2 - bx) + uy * (cy2 - by)) / L2 < math.cos(math.radians(25)):
                        runs.append(run)
                        run = []
            if run:
                runs.append(run)
            for k, r in enumerate(runs):
                length = (len(r) - 1) * STEP
                n = int(length // SPACE_LEN)
                if n < 1:
                    continue
                seg_id = f"c{w['id'][1:]}{sname[0]}{k}"
                spaces = []
                # spaces laid end to end, centred in the run
                start = (length - n * SPACE_LEN) / 2
                for j in range(n):
                    i0 = int(round((start + j * SPACE_LEN) / STEP))
                    i1 = min(len(r) - 1, int(round((start + (j + 1) * SPACE_LEN) / STEP)))
                    (x0, y0, _, _, nx, ny), (x1, y1, _, _, _, _) = r[i0], r[i1]
                    rect = [(x0, y0), (x1, y1), (x1 + nx * SPACE_W, y1 + ny * SPACE_W), (x0 + nx * SPACE_W, y0 + ny * SPACE_W)]
                    spaces.append(rect)
                coords = [to_ll(p[0], p[1]) for p in r[::4]] + [to_ll(r[-1][0], r[-1][1])]
                mid = r[len(r) // 2]
                feats.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords},
                              "properties": {"kind": "curb", "id": seg_id, "road": w["id"], "name": t.get("name"),
                                             "highway": t.get("highway"), "side": sname, "oneway": oneway,
                                             "length_m": round(length, 1), "est_spaces": n,
                                             "confidence": "candidate - unverified", "observed_cars": None,
                                             "dist_to_wilkinson_m": round(math.hypot(mid[0], mid[1])),
                                             "center": to_ll(mid[0], mid[1])}})
                for j, rect in enumerate(spaces):
                    pts = [to_ll(*p) for p in rect]
                    feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [pts + [pts[0]]]},
                                  "properties": {"kind": "space", "id": f"{seg_id}-s{j}", "lot": None, "curb": seg_id,
                                                 "estimated": True, "ada": False, "ev": False, "row": seg_id,
                                                 "pos": j, "row_len": n, "layout": "curb"}})
                n_seg += 1
                n_spaces += n
    return feats, {"curb_segments": n_seg, "curb_spaces": n_spaces}
