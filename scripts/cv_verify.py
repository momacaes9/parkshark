#!/usr/bin/env python3
"""
ParkShark Duke - computer-vision verification of candidate curb parking (P2).

For the N candidate curb segments closest to Wilkinson:
  1. download NC OneMap orthoimagery (NC CGIA, public record; NOT Esri/Google) for the segment
  2. run an off-the-shelf aerial object detector: Ultralytics YOLOv8n-OBB pretrained on DOTA v1
     (classes 'small vehicle' / 'large vehicle', oriented boxes)
  3. a car counts as parked on the segment if its centre falls inside one of the segment's
     6.1 m curb spaces (expanded by 1 m); moving traffic sits in the travel lane and is ignored
  4. write data/curb_cv.json and an annotated JPG per segment in data/cv_tiles/

Usage (needs the venv):  .venv/bin/python scripts/cv_verify.py [--n 10] [--conf 0.25]
"""
import argparse
import io
import json
import math
import os
import time
import urllib.request

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEOJSON = os.path.join(ROOT, "data", "parking.geojson")
OUT_JSON = os.path.join(ROOT, "data", "curb_cv.json")
TILE_DIR = os.path.join(ROOT, "data", "cv_tiles")
RAW_DIR = os.path.join(TILE_DIR, "raw")
NC_ONEMAP = "https://services.nconemap.gov/secure/rest/services/Imagery/Orthoimagery_Latest/ImageServer/exportImage"
GSD = 0.15            # metres per pixel requested from NC OneMap (native is 6 in = 0.15 m)
PAD = 12.0            # metres around the segment
VEHICLE_CLASSES = {"small vehicle", "large vehicle"}


def merc(lon, lat):
    x = lon * 20037508.34 / 180
    y = math.log(math.tan((90 + lat) * math.pi / 360)) * 6378137.0
    return x, y


def point_in_ring(x, y, r):
    inside, j = False, len(r) - 1
    for i in range(len(r)):
        xi, yi = r[i]
        xj, yj = r[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def expand(rect, m):
    cx = sum(p[0] for p in rect) / len(rect)
    cy = sum(p[1] for p in rect) / len(rect)
    out = []
    for x, y in rect:
        d = math.hypot(x - cx, y - cy) or 1
        out.append((x + (x - cx) / d * m, y + (y - cy) / d * m))
    return out


def fetch_tile(seg_id, minx, miny, maxx, maxy):
    w = min(1600, int((maxx - minx) / GSD))
    h = min(1600, int((maxy - miny) / GSD))
    path = os.path.join(RAW_DIR, f"{seg_id}.jpg")
    if not os.path.exists(path):
        url = (f"{NC_ONEMAP}?bbox={minx},{miny},{maxx},{maxy}&bboxSR=3857&imageSR=3857"
               f"&size={w},{h}&format=jpg&f=image")
        req = urllib.request.Request(url, headers={"User-Agent": "ParkShark-Duke-hackathon/0.1"})
        try:
            data = urllib.request.urlopen(req, timeout=90).read()
        except Exception:      # macOS system-Python TLS issues -> curl
            import subprocess
            data = subprocess.run(["curl", "-sS", "--fail", "-m", "90", url], capture_output=True, check=True).stdout
        Image.open(io.BytesIO(data)).convert("RGB").save(path, quality=92)
    return Image.open(path).convert("RGB")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10, help="number of curb segments to verify (closest to Wilkinson)")
    ap.add_argument("--conf", type=float, default=0.25)
    args = ap.parse_args()
    os.makedirs(RAW_DIR, exist_ok=True)

    from ultralytics import YOLO
    model = YOLO("yolov8n-obb.pt")          # DOTA-v1 pretrained, downloads ~6 MB on first run

    fc = json.load(open(GEOJSON))
    curbs = [f for f in fc["features"] if f["properties"]["kind"] == "curb"]
    spaces = {}
    for f in fc["features"]:
        p = f["properties"]
        if p.get("curb"):
            spaces.setdefault(p["curb"], []).append([merc(*c) for c in f["geometry"]["coordinates"][0][:-1]])
    curbs.sort(key=lambda f: f["properties"]["dist_to_wilkinson_m"])
    todo = curbs[:args.n]

    results = json.load(open(OUT_JSON)).get("segments", {}) if os.path.exists(OUT_JSON) else {}
    t0 = time.time()
    for f in todo:
        p = f["properties"]
        sid = p["id"]
        line = [merc(*c) for c in f["geometry"]["coordinates"]]
        rects = spaces.get(sid, [])
        pts = line + [q for r in rects for q in r]
        minx, miny = min(q[0] for q in pts) - PAD, min(q[1] for q in pts) - PAD
        maxx, maxy = max(q[0] for q in pts) + PAD, max(q[1] for q in pts) + PAD
        img = fetch_tile(sid, minx, miny, maxx, maxy)
        W, H = img.size
        sx, sy = (maxx - minx) / W, (maxy - miny) / H
        to_px = lambda x, y: ((x - minx) / sx, (maxy - y) / sy)            # noqa: E731
        to_m = lambda u, v: (minx + u * sx, maxy - v * sy)                 # noqa: E731

        res = model.predict(img, conf=args.conf, imgsz=max(640, (max(W, H) + 31) // 32 * 32), verbose=False)[0]
        names = res.names
        zones = [expand(r, 1.0) for r in rects]
        cars, all_veh = [], 0
        draw = ImageDraw.Draw(img)
        for r in rects:
            draw.polygon([to_px(*q) for q in r], outline=(245, 158, 11))
        if res.obb is not None:
            for poly, cls, cf in zip(res.obb.xyxyxyxy.tolist(), res.obb.cls.tolist(), res.obb.conf.tolist()):
                if names[int(cls)] not in VEHICLE_CLASSES:
                    continue
                all_veh += 1
                cu = sum(q[0] for q in poly) / 4
                cv = sum(q[1] for q in poly) / 4
                mx, my = to_m(cu, cv)
                parked = any(point_in_ring(mx, my, z) for z in zones)
                draw.polygon([tuple(q) for q in poly], outline=(34, 197, 94) if parked else (148, 163, 184), width=3 if parked else 1)
                if parked:
                    cars.append(round(cf, 2))
        draw.line([to_px(*q) for q in line], fill=(34, 197, 94) if cars else (245, 158, 11), width=2)
        img.thumbnail((640, 640))
        out_img = os.path.join(TILE_DIR, f"{sid}.jpg")
        img.save(out_img, quality=80)
        results[sid] = {"observed": len(cars) > 0, "cars": len(cars), "confidences": cars,
                        "vehicles_in_tile": all_veh, "est_spaces": p["est_spaces"], "name": p.get("name"),
                        "image": f"data/cv_tiles/{sid}.jpg", "imagery": "NC OneMap Orthoimagery_Latest (6-inch)",
                        "imagery_date": None, "model": "YOLOv8n-OBB (DOTA v1)", "conf_threshold": args.conf}
        print(f"  {sid:<22} {p.get('name') or '-':<22} {p['length_m']:>6} m  cars parked: {len(cars):>2}  "
              f"(vehicles in tile {all_veh})")
    json.dump({"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "source": "NC OneMap Orthoimagery_Latest - NC Center for Geographic Information and Analysis",
               "model": "Ultralytics YOLOv8n-OBB pretrained on DOTA v1 (off-the-shelf, no fine-tuning)",
               "segments": results}, open(OUT_JSON, "w"), indent=1)
    obs = sum(1 for r in results.values() if r["observed"])
    print(f"\nVerified {len(todo)} segments in {time.time() - t0:.0f}s -> {obs} total observed. Wrote {OUT_JSON}")


if __name__ == "__main__":
    main()
