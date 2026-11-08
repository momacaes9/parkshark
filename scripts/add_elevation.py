#!/usr/bin/env python3
"""
Annotate lots and curb segments in data/parking.geojson with ground elevation (m) so the
"senior mode" profile can penalise uphill walks to Wilkinson.
Elevation: Open-Meteo Elevation API (Copernicus DEM GLO-90, keyless). Cached in data/elevation_cache.json.
Usage: python3 scripts/add_elevation.py   (fetch_osm.py calls it automatically)
"""
import json
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEOJSON = os.path.join(ROOT, "data", "parking.geojson")
CACHE = os.path.join(ROOT, "data", "elevation_cache.json")
API = "https://api.open-meteo.com/v1/elevation"


def key(lon, lat):
    return f"{lat:.5f},{lon:.5f}"


def main():
    fc = json.load(open(GEOJSON))
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    dest = fc["metadata"]["center"]
    pts = [tuple(dest)] + [tuple(f["properties"]["center"]) for f in fc["features"]
                           if f["properties"]["kind"] in ("lot", "curb") and f["properties"].get("center")]
    todo = sorted({key(*p) for p in pts} - set(cache))
    for i in range(0, len(todo), 100):
        chunk = todo[i:i + 100]
        lats = ",".join(k.split(",")[0] for k in chunk)
        lons = ",".join(k.split(",")[1] for k in chunk)
        out = subprocess.run(["curl", "-sS", "--fail", "-m", "60", f"{API}?latitude={lats}&longitude={lons}"],
                             capture_output=True, text=True)
        if out.returncode != 0:
            print("  elevation API failed:", out.stderr.strip())
            break
        for k, e in zip(chunk, json.loads(out.stdout)["elevation"]):
            cache[k] = e
    json.dump(cache, open(CACHE, "w"))
    n = 0
    for f in fc["features"]:
        p = f["properties"]
        if p["kind"] in ("lot", "curb") and p.get("center") and key(*p["center"]) in cache:
            p["elev_m"] = cache[key(*p["center"])]
            n += 1
    fc["metadata"]["dest_elev_m"] = cache.get(key(*dest))
    fc["metadata"]["elevation_source"] = "Open-Meteo Elevation API (Copernicus DEM GLO-90)"
    json.dump(fc, open(GEOJSON, "w"), separators=(",", ":"))
    print(f"  elevation added to {n} features (destination {fc['metadata']['dest_elev_m']} m)")


if __name__ == "__main__":
    main()
