#!/usr/bin/env python3
"""Create explicitly LOW-confidence, synthetic bay-layout estimates inside OSM lots.

Requires: Python 3.12 with dependencies pinned in scripts/requirements-geometry.txt.
    python -m pip install -r scripts/requirements-geometry.txt
    python scripts/estimate_spaces.py

No network calls. Inputs: source-data/geometry/osm-parking-{lots,spaces}.geojson.
An optional --osm-xml directory reads already-downloaded OSM map XML to refresh
source-data/estimates/osm-building-exclusions.geojson. The checked-in building
cache otherwise makes the build reproducible without the XML downloads.

These rectangles are a geometric planning model, NOT surveyed/traced stalls,
legal parking designations, verified capacities, or official Duke facilities.
"""
import argparse
import collections
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import box, mapping, shape, Polygon, MultiPolygon
from shapely.ops import unary_union
from pyproj import Transformer

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'source-data' / 'estimates'
BBOX = (-78.963, 35.990, -78.914, 36.018)
METHOD = 'geometry-grid-v1'
WIDTH, DEPTH, AISLE = 2.60, 5.40, 6.20
SETBACK, BUILDING_BUFFER = 1.20, 1.00
MODULE = 2 * DEPTH + AISLE
TO_M = Transformer.from_crs('EPSG:4326', 'EPSG:32617', always_xy=True).transform
TO_LL = Transformer.from_crs('EPSG:32617', 'EPSG:4326', always_xy=True).transform


def transform(fn, geometry):
    return shapely.transform(geometry, fn, interleaved=False)


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':')) + '\n')


def features_file(name, features):
    return {'type': 'FeatureCollection', 'name': name,
            'attribution': 'Contains © OpenStreetMap contributors data, ODbL',
            'features': features}


def valid_polygon(g):
    if not g.is_valid:
        g = shapely.make_valid(g)
    if g.geom_type in ('Polygon', 'MultiPolygon'):
        return g
    parts = [a for a in getattr(g, 'geoms', []) if a.geom_type in ('Polygon', 'MultiPolygon')]
    return unary_union(parts) if parts else Polygon()


def refresh_buildings(directory):
    """Preserve exact closed OSM building ways; join relation member rings."""
    objects = {}
    files = sorted(directory.glob('osm-bbox-*.xml'))
    if not files:
        raise SystemExit('No osm-bbox-*.xml input extracts found')
    for path in files:
        for e in ET.parse(path).getroot():
            if e.tag in ('node', 'way', 'relation'):
                objects[e.tag, e.get('id')] = e
    nodes = {i: (float(e.get('lon')), float(e.get('lat')))
             for (kind, i), e in objects.items() if kind == 'node'}
    ways = {i: e for (kind, i), e in objects.items() if kind == 'way'}
    tags = lambda e: {x.get('k'): x.get('v') for x in e.findall('tag')}
    coords = lambda e: [nodes[x.get('ref')] for x in e.findall('nd') if x.get('ref') in nodes]

    def rings(parts):
        result, pending = [], list(parts)
        while pending:
            c = pending.pop(0)
            if not c:
                continue
            while c[0] != c[-1]:
                for j, d in enumerate(pending):
                    if c[-1] == d[0]:
                        c += d[1:]; pending.pop(j); break
                    if c[-1] == d[-1]:
                        c += d[-2::-1]; pending.pop(j); break
                    if c[0] == d[-1]:
                        c = d[:-1] + c; pending.pop(j); break
                    if c[0] == d[0]:
                        c = d[:0:-1] + c; pending.pop(j); break
                else:
                    return None
            if len(c) >= 4:
                result.append(c)
        return result

    fs, failed = [], []
    extent = box(*BBOX)
    for (kind, i), e in sorted(objects.items()):
        t = tags(e)
        if not ((t.get('building') and t['building'] != 'no') or
                (t.get('building:part') and t['building:part'] != 'no')):
            continue
        g = None
        if kind == 'way':
            c = coords(e)
            if len(c) >= 4 and c[0] == c[-1]:
                g = Polygon(c)
        elif kind == 'relation' and t.get('type') == 'multipolygon':
            parts = {'outer': [], 'inner': []}
            for m in e.findall('member'):
                if m.get('type') == 'way' and m.get('ref') in ways:
                    parts['inner' if m.get('role') == 'inner' else 'outer'].append(coords(ways[m.get('ref')]))
            outers, inners = rings(parts['outer']), rings(parts['inner'])
            if outers is not None and inners is not None:
                polys = []
                for o in outers:
                    po = Polygon(o)
                    holes = [h for h in inners if po.covers(Polygon(h).representative_point())]
                    polys.append(Polygon(o, holes))
                if polys:
                    g = MultiPolygon(polys) if len(polys) > 1 else polys[0]
        if g is None:
            if kind != 'node':
                failed.append(kind + '/' + i)
            continue
        if not g.intersects(extent):
            continue
        fs.append({'type': 'Feature', 'id': 'osm-' + kind + '-' + i,
                   'properties': {'osm_id': kind + '/' + i, 'tags': t,
                                  'source': 'OpenStreetMap',
                                  'source_url': 'https://www.openstreetmap.org/' + kind + '/' + i,
                                  'source_updated_at': e.get('timestamp')},
                   'geometry': mapping(g)})
    extraction = {'features': len(fs), 'unconvertible': failed}
    output = features_file('Source-mapped building exclusions; not estimated geometry', fs)
    output['sourceExtraction'] = extraction
    write(OUT / 'osm-building-exclusions.geojson', output)
    return extraction


def layout(available):
    """Try 16 deterministic orientations/phases; keep most locally accessible bays.

    Each accepted rectangle and its adjacent 6.2m-wide maneuvering aisle slice
    must fit entirely in the usable lot. This does not establish an entrance,
    turning paths, connected aisles, drainage/island clearance or legal design.
    """
    mrr = available.minimum_rotated_rectangle
    c = list(mrr.exterior.coords)
    edges = [(math.dist(c[i], c[i + 1]), c[i], c[i + 1]) for i in range(4)]
    _, a, b = max(edges, key=lambda e: (round(e[0], 6), e[1], e[2]))
    angle = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 180
    center = available.centroid.coords[0]
    best, best_meta = [], None
    for turn in (0, 90):
        theta = (angle + turn) % 180
        local = affinity.rotate(available, -theta, origin=center)
        shapely.prepare(local)
        xmin, ymin, xmax, ymax = local.bounds
        for phase_y in (0, 0.25, 0.5, 0.75):
            for phase_x in (0, 0.5):
                # Start before bounds so all phases explore both edge rows.
                xs = np.arange(xmin - WIDTH + phase_x * WIDTH, xmax, WIDTH)
                ys = np.arange(ymin - MODULE + phase_y * MODULE, ymax, MODULE)
                candidates = []
                for row_side in (0, 1):
                    xx, yy = np.meshgrid(xs, ys)
                    xx, yy = xx.ravel(), yy.ravel()
                    bottom = yy + (DEPTH + AISLE if row_side else 0)
                    bay_boxes = shapely.box(xx, bottom, xx + WIDTH, bottom + DEPTH)
                    maneuver_bottom = yy + (DEPTH if row_side else 0)
                    # Bottom-row bay + aisle; top-row aisle + bay.
                    maneuver_top = yy + (MODULE if row_side else DEPTH + AISLE)
                    local_access = shapely.box(xx, maneuver_bottom, xx + WIDTH, maneuver_top)
                    keep = shapely.covers(local, local_access) & shapely.covers(local, bay_boxes)
                    for k in np.flatnonzero(keep):
                        candidates.append((float(bottom[k]), float(xx[k]), row_side, bay_boxes[k]))
                if len(candidates) > len(best):
                    candidates.sort(key=lambda c: (c[0], c[1], c[2]))
                    best = [affinity.rotate(q[3], theta, origin=center) for q in candidates]
                    best_meta = {'orientation_degrees_utm': round(theta, 4),
                                 'phase_x_fraction': phase_x, 'phase_y_fraction': phase_y}
    return best, best_meta


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--osm-xml', type=Path,
                        help='Optional local raw OSM XML directory to refresh building exclusions')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.osm_xml:
        refresh_buildings(args.osm_xml)
    cache = OUT / 'osm-building-exclusions.geojson'
    if not cache.exists():
        raise SystemExit('Building exclusion cache missing: supply --osm-xml PATH first')
    building_fc = read(cache)
    building_geoms = [valid_polygon(transform(TO_M, shape(f['geometry']))).buffer(BUILDING_BUFFER)
                      for f in building_fc['features']]
    buildings = unary_union(building_geoms)
    shapely.prepare(buildings)
    exact = read(ROOT / 'source-data/geometry/osm-parking-spaces.geojson')['features']
    exact_geoms = [valid_polygon(transform(TO_M, shape(f['geometry']))).buffer(0.20)
                   for f in exact if f['geometry']['type'] in ('Polygon', 'MultiPolygon')]
    known_spaces = unary_union(exact_geoms)
    shapely.prepare(known_spaces)
    lots = read(ROOT / 'source-data/geometry/osm-parking-lots.geojson')['features']
    records, candidates, bays, coverage = [], [], [], []
    extent = box(*BBOX)
    for f in sorted(lots, key=lambda f: f['properties']['id']):
        p, g = f['properties'], shape(f['geometry'])
        t = p.get('tags', {})
        record = {'source_lot_id': p['id'], 'source_lot_name': t.get('name'),
                  'source_url': p['source_url'], 'source_parking_type': t.get('parking'),
                  'estimated_bays': 0, 'confidence': 'low', 'status': 'no_coverage'}
        records.append(record)
        if g.geom_type not in ('Polygon', 'MultiPolygon'):
            record['reason'] = 'source_geometry_not_polygon'; continue
        # Whole footprints only; never clip a source lot into a guessed boundary.
        if not extent.covers(g):
            record['reason'] = 'outside_or_crosses_campus_model_bbox'; continue
        if t.get('building') not in (None, 'no') or t.get('building:part') not in (None, 'no'):
            record['reason'] = 'building_or_garage_tag'; continue
        if t.get('parking') != 'surface':
            record['reason'] = ('excluded_non_surface_' + t['parking']) if t.get('parking') else 'surface_type_not_explicitly_mapped'
            continue
        if t.get('location') in ('underground', 'rooftop', 'roof') or t.get('tunnel') == 'yes':
            record['reason'] = 'underground_or_roof'; continue
        gm = valid_polygon(transform(TO_M, g))
        record['footprint_area_m2'] = round(gm.area, 2)
        if gm.area < 65:
            record['reason'] = 'footprint_too_small'; continue
        candidates.append((gm.area, p['id'], gm, f, record))
    used = Polygon()
    for _, lot_id, gm, f, record in sorted(candidates, key=lambda a: (a[0], a[1])):
        usable = gm.buffer(-SETBACK).difference(buildings).difference(known_spaces).difference(used)
        usable = valid_polygon(usable)
        record['usable_area_m2'] = round(usable.area, 2)
        if usable.is_empty or usable.area < WIDTH * (DEPTH + AISLE):
            record['reason'] = 'insufficient_area_after_setbacks_buildings_existing_spaces_or_overlaps'; continue
        components = [usable] if usable.geom_type == 'Polygon' else sorted(usable.geoms, key=lambda g: (g.bounds, g.area))
        result, models = [], []
        for component in components:
            if component.area < WIDTH * (DEPTH + AISLE):
                continue
            layout_bays, model = layout(component)
            result.extend(layout_bays)
            if model:
                models.append(model)
        if not result:
            record['reason'] = 'no_bay_with_adjacent_aisle_fits'; continue
        # Deterministic footprint-order assignment prevents overlap between lots.
        used = unary_union([used, gm])
        record.update({'estimated_bays': len(result), 'status': 'modeled',
                       'reason': None, 'model_components': models})
        base = lot_id.replace('/', '-')
        shared = {'sourceLotId': lot_id, 'sourceLotName': f['properties'].get('name'),
                  'sourceUrl': f['properties']['source_url'],
                  'geometryStatus': 'estimated_geometry_model', 'confidence': 'low',
                  'method': METHOD, 'kind': 'estimated-bay', 'estimated': True,
                  'label': 'Estimated bay — unverified geometry model',
                  'tracedFromImagery': False, 'officialFacilityId': None,
                  'accessible': None, 'ev': None, 'occupancy': None,
                  'permit': None, 'price': None, 'retrieved': '2026-11-07'}
        shapely.prepare(gm)
        for n, bay in enumerate(result, 1):
            # Validate metric containment and building exclusions before export.
            building_overlap = buildings.intersects(bay) and bay.intersection(buildings).area > 1e-7
            space_overlap = known_spaces.intersects(bay) and bay.intersection(known_spaces).area > 1e-7
            if not gm.covers(bay) or building_overlap or space_overlap:
                raise AssertionError('Unsafe model geometry in ' + lot_id)
            bay_id = 'estimate-' + METHOD + '-' + base + '-' + str(n).zfill(5)
            bays.append({'type': 'Feature', 'id': bay_id,
                         'properties': {'id': bay_id, **shared},
                         'geometry': mapping(transform(TO_LL, bay))})
        coverage.append({'type': 'Feature', 'id': 'estimate-lot-' + base,
                         'properties': {'id': 'estimate-lot-' + base,
                                        **shared, 'kind': 'estimated-lot',
                                        'estimatedBays': len(result)},
                         'geometry': f['geometry']})
    bays.sort(key=lambda f: f['id'])
    coverage.sort(key=lambda f: f['id'])
    write(OUT / 'estimated-bays.geojson', features_file('LOW-confidence modeled parking bays', bays))
    write(OUT / 'estimated-lots.geojson', features_file('OSM footprints with LOW-confidence bay models', coverage))
    counts = dict(collections.Counter(r.get('reason') or r['status'] for r in records))
    manifest = {
        'schemaVersion': 1, 'method': METHOD, 'created': '2026-11-07',
        'confidence': 'low', 'status': 'geometry_model_estimate', 'bbox': BBOX,
        'warning': 'SYNTHETIC ESTIMATES. No aerial tracing, survey, measured stall count, legal parking designation or completeness claim. Layout and count may differ substantially from reality.',
        'counts': {'source_lots': len(lots), 'eligible_surface_candidates': len(candidates),
                   'modeled_lots': len(coverage), 'estimated_bays': len(bays),
                   'building_exclusions': len(building_fc['features']),
                   'known_source_parking_space_polygons_excluded': len(exact)},
        'noCoverageReasons': {k: v for k, v in counts.items() if k != 'modeled'},
        'dependencies': {'python': '3.12', 'shapely': '2.2.0', 'pyproj': '3.8.0', 'numpy': '2.5.4', 'certifi': '2026.10.23'},
        'assumptions': {'projection': 'EPSG:32617 WGS84 / UTM zone 17N; metres',
                        'stall_width_m': WIDTH, 'stall_depth_m': DEPTH,
                        'aisle_width_m': AISLE, 'boundary_setback_m': SETBACK,
                        'mapped_building_clearance_m': BUILDING_BUFFER,
                        'existing_mapped_space_clearance_m': 0.20,
                        'layout': 'Minimum rotated rectangle orientation and perpendicular orientation; test eight grid phases each; double-loaded rows with adjacent local aisle containment.',
                        'selection': 'Only whole polygons within bbox explicitly tagged parking=surface; no garages, roofs, underground facilities, building-tagged lots, unknown surface types or on-street estimates.',
                        'not_modeled': ['Entrances and connected circulation', 'turning radii', 'unmapped landscape islands, trees, sidewalks and obstacles', 'ADA or EV status', 'occupancy', 'prices and permits', 'current construction or changes since OSM mapping'],
                        'official_rule_join': 'None. No Duke facility facts, rules, fees or permits assigned to model footprints.'},
        'sources': [{'name': 'OpenStreetMap lot footprints', 'path': '../geometry/osm-parking-lots.geojson'},
                    {'name': 'OpenStreetMap source parking-space polygons', 'path': '../geometry/osm-parking-spaces.geojson'},
                    {'name': 'OpenStreetMap building outlines', 'path': 'osm-building-exclusions.geojson'}],
        'license': 'Contains OpenStreetMap data © OpenStreetMap contributors, ODbL; https://www.openstreetmap.org/copyright',
        'validation': {'all_generated_bays_contained_in_source_lot': True,
                       'all_generated_bays_avoid_mapped_buildings_and_known_source_space_polygons': True,
                       'cross_lot_overlap_prevented_by_source_footprint_reservation': True},
        'buildingExtraction': building_fc.get('sourceExtraction', {'features': len(building_fc['features'])}),
        'lots': records,
    }
    write(OUT / 'manifest.json', manifest)
    print(json.dumps({'counts': manifest['counts'], 'reasons': counts}, indent=2))


if __name__ == '__main__':
    main()
