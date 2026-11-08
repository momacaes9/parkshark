"""Validate the data boundaries that matter for this map, without network access."""
import json, pathlib
from collections import Counter
from trace_lewis import convex_rings_overlap
ROOT=pathlib.Path(__file__).resolve().parents[1]
def load(name): return json.loads((ROOT/'dist/data'/name).read_text())
counts={}
for name in ['facilities','lots','spaces','signs','street','roads']:
    d=load(name+'.geojson');fs=d['features'];counts[name]=len(fs)
    assert d['type']=='FeatureCollection'
    assert len({f['id'] for f in fs})==len(fs),f'duplicate IDs: {name}'
    for f in fs:
        assert f['geometry']['type'] in ['Point','LineString','Polygon','MultiPolygon']
        def coords(c):
            if isinstance(c[0],(float,int)):
                assert -79.1<c[0]<-78.7 and 35.8<c[1]<36.2,(name,f['id'],c)
            else:
                for n in c: coords(n)
        coords(f['geometry']['coordinates'])
fs={f['properties']['dukeId']:f['properties'] for f in load('facilities.geojson')['features']}
assert counts=={'facilities':128,'lots':499,'spaces':31,'signs':3885,'street':8,'roads':18},counts
assert all(f['occupancy'] is None for f in fs.values())
assert all(f['accessibleCount'] is None for f in fs.values())
assert not fs['2021-W6']['rates'],'garage rate must not attach to Bryan surface'
assert fs['2217-W9']['rates'],'PG4 permit rate missing'
assert not fs['2003-E18']['visitorRates'],'incorrect Epworth link not excluded'
assert fs['2003-E25']['visitorRates'][0]['visitorSpaces']==2
assert not fs['2019-M42']['visitorRates'],'Flowers on-street price attached to Trent lot'
assert all(r['permit']!='CAMPUS DRIVE (CPD)' for r in fs['2066-C53']['rates'])
assert any(r['name']=='Campus Drive' for r in fs['2066-C51']['rates'])
assert fs['2020-C40']['warnings'],'source conflict hidden'
assert load('occupancy.json')['observations']==[]
estimates=load('estimates.geojson')['features']
summary=load('estimate-summary.json')
assert len({f['id'] for f in estimates})==len(estimates)
imagery_counts=Counter(f['properties']['sourceLotId'] for f in estimates if f['properties']['imagery'])
assert imagery_counts=={'way/32336367':30,'way/1065904267':2,'way/594764289':8}
assert imagery_counts=={lot:trace['count'] for lot,trace in summary['imageryLots'].items()}
assert sum(imagery_counts.values())==summary['imageryBays']
assert len(estimates)==summary['modeledBays']+summary['imageryBays']
assert all(f['properties']['estimated'] and f['properties']['occupancy'] is None for f in estimates)
assert all(f['properties']['imagery'] or f['properties']['sourceLotId'] not in summary['replacedModelLots'] for f in estimates)
assert all(f['properties']['confidence']==('medium' if f['properties']['imagery'] else 'low') for f in estimates)
assert sum(summary['lotCounts'].values())==len(estimates)
curb=load('curb-estimates.geojson');cf=curb['features']
mask=curb['modeledBayExclusion']['coordinates'][0]
assert len({f['id'] for f in cf})==len(cf)==curb['streets']['Lewis Street']['estimatedBays']==22
assert Counter(f['properties']['runId'] for f in cf)=={'west-middle':5,'middle':5,'east':12}
for f in cf:
    p=f['properties'];ring=f['geometry']['coordinates'][0]
    assert p['kind']=='estimated-curb-bay' and p['estimated'] and p['street']=='Lewis Street'
    assert p['confidence']=='medium' and p['imageryYear']==2025 and p['sourceUrl'].endswith('/Ortho2025/MapServer')
    assert all(p[k] is None for k in ('occupancy','accessibility','evCharging','legalDesignation'))
    assert f['geometry']['type']=='Polygon' and len(ring)==5 and ring[0]==ring[-1]
    assert convex_rings_overlap(ring,mask)
assert not any(convex_rings_overlap(f['geometry']['coordinates'][0],mask) for f in estimates if not f['properties']['imagery'])
assert summary['surfaceBaysRemovedForCurb']==47
assert {f['properties']['rule']['spaceCount'] for f in load('roads.geojson')['features'] if f['properties']['name']=='Lewis Street'}=={44}
print('PASS: geometry, stable IDs, null unknowns, corrected source joins and empty occupancy.',counts)
print('PASS: isolated estimate layers, stable IDs, provenance and no modeled duplicates at aerial review lots.',len(estimates))
print('PASS: 22 curb estimates, separate 44-space published count, and 47 conflicting model shapes excluded.')
