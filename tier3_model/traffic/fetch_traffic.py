"""
Park Shark: street traffic near the Duke lots (Tier 3 helper).

Run this on your own Mac, NOT in the cloud workspace (its network
allowlist blocks these sites). Standard library only, no installs.

What it does, step by step:
  1. NCDOT counts   : downloads official daily traffic counts (AADT) for
                      count stations near Duke West Campus.
  2. OSM tags       : downloads road size facts (road type, lanes, speed
                      limit) for the streets next to each lot.
  3. Google crossing: (optional, needs an API key) asks Google how long
                      it takes to drive each short street segment at every
                      hour of a typical week, with and without traffic.
  4. Labels         : (optional, no key) reads a CSV where you typed in the
                      Google Maps "Typical traffic" colour per hour.
  5. Estimate       : turns ratio or label into speed ratio, then
                      volume/capacity, cars per hour, and density.

Usage:
  python3 fetch_traffic.py ncdot
  python3 fetch_traffic.py osm
  GOOGLE_MAPS_API_KEY=... python3 fetch_traffic.py google --dry-run
  GOOGLE_MAPS_API_KEY=... python3 fetch_traffic.py google
  python3 fetch_traffic.py estimate

Every number marked ASSUMPTION below is a guess, not a measurement.
"""
import csv
import datetime as dt
import json
import math
import os
import sys
import urllib.parse
import urllib.request

HERE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE_DIR, "traffic_data")
SEGMENTS_FILE = os.path.join(HERE_DIR, "traffic_segments.json")
UA = "ParkShark-student-project/0.1"

# Box around Duke West Campus: west, south, east, north (lon/lat).
BBOX = (-78.952, 35.994, -78.925, 36.012)

NCDOT_STATIONS = ("https://services.arcgis.com/NuWFvHYDMVmmxMeM/ArcGIS/rest/"
                  "services/NCDOT_AADT_Traffic_Segmentation/FeatureServer/0/query")
OVERPASS = "https://overpass-api.de/api/interpreter"
ROUTES = "https://routes.googleapis.com/directions/v2:computeRoutes"

# ---------------------------------------------------------------- ASSUMPTIONS
# Google Maps colour label -> speed ratio (current speed / free flow speed).
# Google does not publish its thresholds. These midpoints are our guess.
LABEL_TO_SPEED_RATIO = {
    "green": 0.90,      # no delays
    "orange": 0.65,     # some slowing
    "red": 0.40,        # heavy
    "darkred": 0.15,    # stop and go
}
# Jam density: cars per mile per lane when traffic is bumper to bumper.
# Textbook values sit around 185 to 200. ASSUMPTION.
JAM_DENSITY = 185
# Speed limit (mph) to use when OSM has no maxspeed tag. NC default inside
# towns is 35 mph; campus roads are often posted lower. ASSUMPTION.
DEFAULT_SPEED_MPH = {"primary": 35, "secondary": 35, "tertiary": 30,
                     "residential": 25, "service": 15, "unclassified": 25}
# Why not the BPR curve? We tested it: with the standard FHWA values
# (0.15, 4) every ratio below about 0.87 maps to the same maxed out v/c,
# so orange, red and dark red all became one number. Greenshields' model
# (below) keeps them apart, because density rises steadily as speed drops.


def get_json(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def save(name, obj):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name)
    with open(path, "w") as f:
        json.dump(obj, f, indent=2)
    print("saved", path)


# ---------------------------------------------------------------- 1. NCDOT
def cmd_ncdot():
    params = {
        "where": "1=1",
        "geometry": ",".join(str(x) for x in BBOX),
        "geometryType": "esriGeometryEnvelope", "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects", "outFields": "*",
        "outSR": "4326", "f": "json",
    }
    data = get_json(NCDOT_STATIONS + "?" + urllib.parse.urlencode(params))
    rows = []
    for feat in data.get("features", []):
        a, g = feat["attributes"], feat.get("geometry", {})
        years = {k: a[k].strip() for k in a if k.startswith("AADT_") and str(a[k]).strip()}
        latest = max(years) if years else None
        rows.append({"station": a.get("LocationID"), "route": a.get("ROUTE"),
                     "location": a.get("LOCATION"), "lon": g.get("x"), "lat": g.get("y"),
                     "latest_year": latest and latest[5:],
                     "latest_aadt": latest and int(years[latest]), "all_years": years})
    save("ncdot_aadt_stations.json", rows)
    for r in rows:
        print(f"  {r['route']:<28} {r['location']:<26} AADT {r['latest_aadt']} ({r['latest_year']})")


# ---------------------------------------------------------------- 2. OSM
def cmd_osm():
    w, s, e, n = BBOX
    q = (f'[out:json][timeout:60];way["highway"]({s},{w},{n},{e});'
         "out tags center;")
    data = get_json(OVERPASS, data=urllib.parse.urlencode({"data": q}).encode())
    roads = []
    for el in data.get("elements", []):
        t = el.get("tags", {})
        if t.get("highway") in ("footway", "path", "steps", "cycleway", "pedestrian"):
            continue
        roads.append({"osm_id": el["id"], "name": t.get("name"), "highway": t.get("highway"),
                      "lanes": t.get("lanes"), "maxspeed": t.get("maxspeed"),
                      "oneway": t.get("oneway"), "access": t.get("access"),
                      "center": el.get("center")})
    save("osm_roads.json", roads)
    named = {}
    for r in roads:
        if r["name"]:
            named.setdefault(r["name"], []).append(r)
    for name in sorted(named):
        ways = named[name]
        print(f"  {name:<28} type={sorted({w['highway'] for w in ways})} "
              f"lanes={sorted({w['lanes'] or '?' for w in ways})} "
              f"maxspeed={sorted({w['maxspeed'] or '?' for w in ways})}")


# ---------------------------------------------------------------- 3. Google
def next_week_hours():
    """168 departure times (every hour) starting next Monday 00:00 Eastern.
    Future times make Google use its historical 'typical' traffic."""
    eastern = dt.timezone(dt.timedelta(hours=-5))  # EST; fine for November
    now = dt.datetime.now(eastern)
    monday = (now + dt.timedelta(days=7 - now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return [monday + dt.timedelta(hours=h) for h in range(168)]


def route_once(key, seg, when):
    body = {
        "origin": {"location": {"latLng": {"latitude": seg["start"][0], "longitude": seg["start"][1]}}},
        "destination": {"location": {"latLng": {"latitude": seg["end"][0], "longitude": seg["end"][1]}}},
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE_OPTIMAL",
        "trafficModel": "BEST_GUESS",
        "departureTime": when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    headers = {"Content-Type": "application/json", "X-Goog-Api-Key": key,
               "X-Goog-FieldMask": "routes.duration,routes.staticDuration,routes.distanceMeters"}
    r = get_json(ROUTES, data=json.dumps(body).encode(), headers=headers)["routes"][0]
    return {"seconds": float(r["duration"].rstrip("s")),
            "static_seconds": float(r["staticDuration"].rstrip("s")),
            "meters": r.get("distanceMeters")}


def cmd_google(dry_run):
    segs = [s for s in load_segments() if s.get("start") and s.get("end")]
    hours = next_week_hours()
    print(f"{len(segs)} segments x {len(hours)} hours = {len(segs) * len(hours)} requests")
    if dry_run:
        print("dry run: nothing sent. Check this fits your free monthly cap first.")
        return
    key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not key:
        sys.exit("Set GOOGLE_MAPS_API_KEY first.")
    rows = []
    for seg in segs:
        for when in hours:
            try:
                res = route_once(key, seg, when)
            except Exception as exc:  # keep going, log the failure
                print("  failed", seg["id"], when, exc)
                continue
            rows.append({"segment": seg["id"], "dow": when.weekday(), "hour": when.hour, **res})
        print("  done", seg["id"])
    save("google_crossing_times.json", rows)


# ---------------------------------------------------------------- 4 and 5
def load_segments():
    with open(SEGMENTS_FILE) as f:
        return json.load(f)["segments"]


def estimate_row(seg, r):
    """Greenshields model. r = current speed / free flow speed (0 to 1).
         speed   = free_flow * r
         density = jam_density * (1 - r)        cars per mile per lane
         flow    = density * speed * lanes      cars per hour, one direction
    Density only goes up as speed goes down, so it is the safest index.
    Flow peaks at r = 0.5 and falls again in a jam, so the same flow can
    mean light OR heavy traffic. Read it next to the speed ratio."""
    road = seg.get("road_class", "tertiary")
    lanes = seg.get("lanes_per_direction") or 1
    mph = seg.get("speed_limit_mph") or DEFAULT_SPEED_MPH.get(road, 25)
    r = min(max(r, 0.0), 1.0)
    speed = mph * r
    density = JAM_DENSITY * (1.0 - r)
    flow = density * speed * lanes
    return {"speed_ratio": round(r, 3), "congestion_index": round(1.0 - r, 3),
            "speed_mph_est": round(speed, 1), "density_per_mile_lane": round(density, 1),
            "cars_per_hour_est": round(flow)}


def cmd_estimate():
    segs = {s["id"]: s for s in load_segments()}
    out = []
    gpath = os.path.join(OUT, "google_crossing_times.json")
    if os.path.exists(gpath):
        for row in json.load(open(gpath)):
            r = row["static_seconds"] / row["seconds"] if row["seconds"] else 1.0
            out.append({**row, "source": "google_ratio", **estimate_row(segs[row["segment"]], r)})
    lpath = os.path.join(HERE_DIR, "traffic_labels.csv")
    if os.path.exists(lpath):
        for row in csv.DictReader(open(lpath)):
            label = row["label"].strip().lower().replace(" ", "")
            if label not in LABEL_TO_SPEED_RATIO:
                print("  unknown label skipped:", row)
                continue
            r = LABEL_TO_SPEED_RATIO[label]
            out.append({"segment": row["segment"], "dow": int(row["dow"]), "hour": int(row["hour"]),
                        "source": "label", **estimate_row(segs[row["segment"]], r)})
    if not out:
        sys.exit("No inputs. Run 'google' or fill traffic_labels.csv first.")

    # Calibration check against NCDOT counts, where a segment has a station.
    # AADT counts both directions over a whole year, so compare it to the
    # average day of the estimated week, both directions (x2, ASSUMPTION:
    # roughly even split).
    for sid, seg in segs.items():
        aadt = seg.get("ncdot_aadt")
        rows = [o for o in out if o["segment"] == sid and o["source"] == "google_ratio"]
        if aadt and len(rows) == 168:
            est_daily = 2 * sum(o["cars_per_hour_est"] for o in rows) / 7
            print(f"  {sid}: estimated {est_daily:,.0f} cars/day vs NCDOT {aadt:,} "
                  f"-> scale factor {aadt / max(est_daily, 1):.2f}")
    save("traffic_estimates.json", out)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "ncdot":
        cmd_ncdot()
    elif cmd == "osm":
        cmd_osm()
    elif cmd == "google":
        cmd_google("--dry-run" in sys.argv)
    elif cmd == "estimate":
        cmd_estimate()
    else:
        print(__doc__)
