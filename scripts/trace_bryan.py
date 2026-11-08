#!/usr/bin/env python3
"""Rebuild a bounded, imagery-guided estimate of one Bryan Center row.

This is a visual row estimate, not a survey, authoritative stall inventory,
or live occupancy observation. No spaces are generated in the terminal
crosshatched area or the tree-obscured opposite row.
"""
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "source-data" / "estimates"
Z = 20
TILE_X, TILE_Y = 294353, 411752
PIXELS = 1024
ROTATED_PIXELS = 1450
SOURCE = "https://webgis2.durhamnc.gov/server/rest/services/Rasters/Ortho2025/MapServer"

# Manually inspected in a 45-degree counterclockwise rotated 4x4 tile mosaic.
# The unbroken northeast row was bounded before the terminal hatch area.
# Car alignment and intermittent visible divider paint support 30 bays;
# individual divisions hidden by vehicles remain estimates.
ROW = {
    "id": "bryan-northeast-row-2025",
    "estimated_count": 30,
    "rotated_u_start": 108.0,
    "rotated_u_end": 791.0,
    "rotated_v_back_start": 690.0,
    "rotated_v_back_end": 667.0,
    "rotated_v_front_start": 736.0,
    "rotated_v_front_end": 713.0,
    "rotation_degrees_ccw": 45,
    "basis": "Visible pavement row, intermittent divider paint, and aligned parked vehicles",
    "exclusions": ["Terminal crosshatched/access area", "Opposite tree-obscured row", "Drive aisle", "Circular turnaround"],
}


def unrotate(u, v):
    c = math.sqrt(0.5)
    du, dv = u - ROTATED_PIXELS / 2, v - ROTATED_PIXELS / 2
    return [PIXELS / 2 + c * du - c * dv,
            PIXELS / 2 + c * du + c * dv]


def lonlat(x, y):
    n = 2 ** Z
    wx, wy = TILE_X + x / 256, TILE_Y + y / 256
    return [wx / n * 360 - 180,
            math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * wy / n))))]


def at(t, front=False):
    suffix = "front" if front else "back"
    return [ROW["rotated_u_start"] + t * (ROW["rotated_u_end"] - ROW["rotated_u_start"]),
            ROW[f"rotated_v_{suffix}_start"] + t * (ROW[f"rotated_v_{suffix}_end"] - ROW[f"rotated_v_{suffix}_start"])]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    features, pixel_polygons = [], []
    for i in range(ROW["estimated_count"]):
        # Small rendering inset keeps adjacent estimated polygons distinct.
        a, b = (i + 0.035) / ROW["estimated_count"], (i + 0.965) / ROW["estimated_count"]
        corners = [at(a), at(b), at(b, True), at(a, True)]
        pixels = [unrotate(*p) for p in corners]
        coords = [lonlat(*p) for p in pixels]
        coords.append(coords[0])
        fid = f"estimate-bryan-img-2025-ne-{i + 1:02d}"
        features.append({
            "type": "Feature", "id": fid,
            "geometry": {"type": "Polygon", "coordinates": [coords]},
            "properties": {
                "id": fid, "name": f"Bryan Center estimated bay NE-{i + 1:02d}",
                "kind": "parking_space_estimate", "facility_id": "duke-2021-W6",
                "facility_name": "Bryan Ctr Pay Lot", "row_id": ROW["id"],
                "geometry_status": "estimated_imagery_guided",
                "geometryStatus": "Imagery-guided estimate; not surveyed",
                "confidence": "medium", "count_confidence": "low",
                "method": "Manual row anchors and interpolated divisions from 2025 orthophoto",
                "estimated": True, "surveyed": False,
                "imagery_year": 2025, "retrieved": "2026-11-07",
                "source": "City of Durham 2025 aerial orthophotos (derived estimate)",
                "source_url": SOURCE, "sourceUrl": SOURCE,
                "source_tile_z": Z, "position_accuracy_m": None,
                "individual_divider_visibility": "partial; vehicles obscure some boundaries",
                "accessible": None, "ev": None, "ada_status": "unknown", "ev_status": "unknown",
                "occupancy": None, "occupancy_status": "unknown",
                "warning": "Estimated geometry only. Verify painted boundaries and signs on site. Imagery does not establish current occupancy, accessible designation, or EV designation.",
            },
        })
        pixel_polygons.append(pixels)
    fc = {"type": "FeatureCollection", "features": features,
          "description": "30 imagery-guided estimates in one Bryan Center row; incomplete lot coverage; not an authoritative capacity"}
    (OUT / "bryan-imagery-spaces.geojson").write_text(json.dumps(fc, indent=2) + "\n")
    metadata = {
        "source_url": SOURCE, "source_year": 2025, "z": Z,
        "tile_origin_x": TILE_X, "tile_origin_y": TILE_Y,
        "tile_size": 256, "mosaic_size": [PIXELS, PIXELS],
        "rotated_mosaic_size": [ROTATED_PIXELS, ROTATED_PIXELS],
        "row": ROW,
        "row_original_pixel_corners": [unrotate(*p) for p in [at(0), at(1), at(1, True), at(0, True)]],
        "row_lonlat_corners": [lonlat(*unrotate(*p)) for p in [at(0), at(1), at(1, True), at(0, True)]],
        "estimated_bay_count": len(features), "total_lot_capacity": None,
        "quality": "Medium positional confidence from georeferenced imagery; low count confidence because vehicles obscure individual divider lines",
        "layout_status": "Partial row trace, not exact or surveyed",
    }
    (OUT / "bryan-row-anchors.json").write_text(json.dumps(metadata, indent=2) + "\n")
    try:
        from PIL import Image, ImageDraw
        im = Image.open(OUT / "bryan-actual-overview.png").convert("RGBA")
        overlay = Image.new("RGBA", im.size)
        draw = ImageDraw.Draw(overlay)
        for poly in pixel_polygons:
            draw.polygon([tuple(p) for p in poly], fill=(255, 170, 20, 70), outline=(255, 110, 0, 230))
        Image.alpha_composite(im, overlay).save(OUT / "bryan-estimate-overlay.png")
    except (ImportError, FileNotFoundError):
        pass
    print(f"Wrote {len(features)} imagery-guided bay estimates; ADA, EV and occupancy unknown.")


if __name__ == "__main__":
    main()
