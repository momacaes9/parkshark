"""Build Lewis Street curb estimates from reproducible, manually selected aerial anchors."""

import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / 'source-data' / 'estimates'
ANCHORS = DIR / 'lewis-curb-anchors.json'
OUTPUT = DIR / 'lewis-curb.geojson'


def convex_rings_overlap(first, second):
    """Positive-area overlap of two convex closed rings, via separating axes."""
    x0, y0 = second[0]
    rings = [[(x - x0, y - y0) for x, y in ring] for ring in (first, second)]
    for ring in rings:
        for (ax, ay), (bx, by) in zip(ring, ring[1:]):
            nx, ny = by - ay, ax - bx
            if nx == 0 and ny == 0:
                continue
            a, b = ([nx * x + ny * y for x, y in r] for r in rings)
            if max(a) <= min(b) or max(b) <= min(a):
                return False
    return True


def to_lonlat(pixel, imagery):
    """Convert mosaic-relative Web Mercator pixels to WGS84 GeoJSON coordinates."""
    tile_size = imagery['tileSize']
    tx, ty = imagery['mosaicOriginTile']
    world_size = tile_size * 2 ** imagery['zoom']
    x, y = tx * tile_size + pixel[0], ty * tile_size + pixel[1]
    lon = x / world_size * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / world_size))))
    return [round(lon, 8), round(lat, 8)]


def divider_bay_rings(stripes, imagery):
    """Turn pairs of adjacent dividers into closed bay outlines."""
    for left, right in zip(stripes, stripes[1:]):
        corners = [left[0], left[1], right[1], right[0], left[0]]
        yield [to_lonlat(pixel, imagery) for pixel in corners]


def build_estimates(data):
    imagery = data['imagery']
    features = []
    for run in data['runs']:
        rings = divider_bay_rings(run['stripeEndpointsPixels'], imagery)
        for index, ring in enumerate(rings, start=1):
            fid = f"estimate-lewis-2025-{run['id']}-{index:02d}"
            xs, ys = zip(*ring)
            features.append({
                'type': 'Feature',
                'id': fid,
                'bbox': [min(xs), min(ys), max(xs), max(ys)],
                'geometry': {'type': 'Polygon', 'coordinates': [ring]},
                'properties': {
                    'kind': 'estimated-curb-bay',
                    'name': f"Lewis Street · {run['id']} {index:02d}",
                    'street': data['street'],
                    'curbSide': data['curbSide'],
                    'orientation': data['orientation'],
                    'runId': run['id'],
                    'estimated': True,
                    'confidence': data['confidence'],
                    'method': 'Adjacent painted divider endpoints manually estimated from aerial imagery',
                    'imageryYear': imagery['year'],
                    'sourceUrl': imagery['sourceUrl'],
                    'occupancy': None,
                    'accessibility': None,
                    'evCharging': None,
                    'legalDesignation': None,
                },
            })
    return {
        'type': 'FeatureCollection',
        'name': 'Lewis Street aerial curb-bay estimates',
        'coverage': 'Visible striped portions of the north curb only; partial, unverified layout.',
        'estimatedBayCount': len(features),
        'publishedVisitorCount': data['publishedVisitorParking']['count'],
        'modeledBayExclusion': {
            'type': 'Polygon',
            'coordinates': [[to_lonlat(pixel, imagery) for pixel in data['modeledBayExclusion']['ringPixels']]],
        },
        'features': features,
    }


if __name__ == '__main__':
    source = json.loads(ANCHORS.read_text(encoding='utf-8'))
    result = build_estimates(source)
    OUTPUT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f"Built {result['estimatedBayCount']} Lewis Street curb estimates in {OUTPUT}")
