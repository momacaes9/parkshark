"""Build a static, provenance-preserving snapshot. No spatial/fuzzy facility joins."""
import json, pathlib, re, shutil
from trace_lewis import convex_rings_overlap, divider_bay_rings
ROOT = pathlib.Path(__file__).resolve().parents[1]
RESEARCH = ROOT / 'source-data'
OUT = ROOT / 'dist' / 'data'
OUT.mkdir(exist_ok=True, parents=True)
def read(p): return json.loads(p.read_text())
def save(name, value): (OUT/name).write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':')))
rules = read(RESEARCH/'duke-rules.json')
raw = read(RESEARCH/'duke-parking-raw.json')['data']['search']['locations']['records']
def focus(link): return link.split('focus=')[-1]
features=[]
for r in raw:
    if r['category'] not in ['Permit','Visitor','Mix']: continue
    rid=r['id']
    rates=[]
    for a in rules['annualPermitRates']:
        ids=[focus(x) for x in a['mapLinks']]
        # Published fee-table hyperlink points to surface lot in error. Explicit correction by garage identity.
        if a['name']=='Parking Garage IV (PG4)': ids=['2217-W9','2014-W9']
        if a['name']=='Campus Drive': ids=['2066-C51']
        if rid in ids: rates.append(a)
    visitors=[]
    for v in rules['visitorRates']:
        # Official visitor table incorrectly links Fuqua spaces to Trent; keep separate and unjoined.
        if v['name'].startswith('Fuqua'): continue
        # Flowers Drive is on-street, not the gated Trent lot linked in the table.
        if v['name'].startswith('Flowers Drive'): continue
        if v['name'].startswith('Epworth Dorm'):
            if rid=='2003-E25': visitors.append({**v,'source':'https://parking.duke.edu/visitor-parking/blue-spot-hourly-parking/'})
            continue
        if rid in [focus(x) for x in v['mapLinks']]: visitors.append(v)
    ev=None
    ev_ids={'2229-SDG':(0,6),'2238-SDGNEST':(0,1),'2017-M24':(1,1),'2012-M13':(2,2),'2021-W6':(3,2),'2267-M46':(4,1)}
    if rid in ev_ids:
        idx,n=ev_ids[rid];ev=dict(rules['evCharging']['locations'][idx]);ev['stationsInSection']=n
    garden=rid in ['2062-D66','2062-D65']
    # General visitor page retains an expired Gardens closure. Current Gardens page takes precedence.
    if garden:
        visitors=[{'name':'Duke Gardens','hourlyUsd':2,'visitorSpaces':None,'source':'https://parking.duke.edu/visitor-parking/blue-spot-hourly-parking/','caveat':'Blue Spot transaction fees apply.'}]
    lon,lat=r['long'],r['lat']
    suffix=rid.split('-',1)[-1]
    area='Medical & Research' if suffix.startswith('M') else 'East Campus' if suffix.startswith('E') else 'West & Athletics' if suffix.startswith(('W','SDG')) else 'Central & Nearby'
    capacity=[{'value':a['spaces'],'scope':a['name'],'source':a['source']} for a in rates if a.get('spaces') and not a['name'].startswith('HEW')]
    # Keep published capacity strings scoped to original zones; never sum repeated zone capacities.
    capacity=list({(c['value'],c['scope']):c for c in capacity}.values())
    p={'id':'duke-'+rid,'dukeId':rid,'name':r['nameDisplay'].removeprefix('Parking ').strip(),'kind':'facility',
       'area':area,'access':r['category'],'visitor':bool(visitors) or r['attrLotVisitor']=='Y',
       'mapPermit':r['attrValidPermit'],
       'permit':' / '.join(dict.fromkeys(a['permit'] for a in rates if a.get('permit') and not a['name'].startswith('HEW'))) or r['attrValidPermit'],
       'afterHours':r['attrAfterHourAccess'],'description':r['description'],
       'gated':r['attrGated'],'rates':rates,'visitorRates':visitors,'capacity':capacity,'ev':ev,
       'accessible':True if garden else None,'accessibleCount':None,
       'gardens':rules['currentGardensRules'] if garden else None,
       'geometryStatus':'Official facility reference point; stall layout not provided',
       'warnings':['Campus map lists YELLOW; the current fee table includes Freeman Center in CHAPEL DRIVE (CHD). Confirm the applicable permit with Duke.'] if rid=='2020-C40' else [],
       'occupancy':None,'source':'Duke Campus Maps','sourceUrl':'https://maps.duke.edu/?focus='+rid,
       'retrieved':'2026-11-07'}
    features.append({'type':'Feature','id':p['id'],'geometry':{'type':'Point','coordinates':[lon,lat]},'properties':p})
save('facilities.geojson',{'type':'FeatureCollection','features':features})
signraw=read(RESEARCH/'durham/duke_parking_signs_active.geojson')
signs=[]
for f in signraw['features']:
    p=f['properties']
    keep=['id','name','category','street_name','TIMELIMIT','ENFORCEHOURS_label','ENFORCEDAYS_label','LEGEND','MUTCD_label','status','EDITDATE','source_feature_url','pole_objectid']
    prop={k:p.get(k) for k in keep};prop.update({'kind':'sign','occupancy':None})
    signs.append({'type':'Feature','id':f['id'],'geometry':f['geometry'],'properties':prop})
save('signs.geojson',{'type':'FeatureCollection','bbox':signraw['bbox'],'features':signs})
save('rules.json',rules)
save('occupancy.json',{'schemaVersion':1,'updatedAt':None,'observations':[]})
for name in ['lots','spaces','street']:
    shutil.copyfile(RESEARCH/f'geometry/osm-parking-{name}.geojson', OUT/f'{name}.geojson')
shutil.copyfile(RESEARCH/'geometry/osm-parking-provenance.json', OUT/'geometry-provenance.json')
roads=read(RESEARCH/'geometry/duke-road-centerlines.geojson')
rule_by_name={r['name'].replace(' ungated spaces',''):r for r in rules['onStreetRules']}
rule_by_name['Circuit Drive']={'name':'Circuit Drive','hourlyUsd':2,'visitorSpaces':8,'source':'https://parking.duke.edu/visitor-parking/'}
for f in roads['features']:
    p=f['properties'];p['kind']='road-rule';p['rule']=rule_by_name[p['name']]
    p['occupancy']=None
save('roads.geojson',roads)
estimate_dir=RESEARCH/'estimates'
curb=read(estimate_dir/'lewis-curb.geojson')
curb_mask=curb['modeledBayExclusion']['coordinates'][0]
model=read(estimate_dir/'estimated-bays.geojson')
aerial=read(estimate_dir/'bryan-imagery-spaces.geojson')['features']
small_lot_traces=read(estimate_dir/'small-lot-anchors.json')
manifest=read(estimate_dir/'manifest.json')
imagery_lots={'way/32336367':{
    'scope':'Northeast row at Bryan Center',
    'detail':'Row edges were traced from aerial imagery. Vehicles obscure some dividers, so the individual divisions are interpolated.',
    'count':len(aerial),
}}
for f in aerial:
    f['properties']['sourceLotId']='way/32336367'
for lot, trace in small_lot_traces['lots'].items():
    rings=list(divider_bay_rings(trace['stripeEndpointsPixels'],trace['imagery']))
    imagery_lots[lot]={'scope':trace['scope'],'detail':trace['detail'],'count':len(rings)}
    for index, ring in enumerate(rings, 1):
        aerial.append({'type':'Feature','id':f"estimate-small-lot-2025-{lot.replace('/', '-')}-{index:02d}",
            'geometry':{'type':'Polygon','coordinates':[ring]},
            'properties':{'sourceLotId':lot,'confidence':'medium'}})
# Use each reviewed trace for its exact OSM footprint. Leave unseen areas blank.
replaced_lots=set(imagery_lots)
surface_candidates=[f for f in model['features'] if f['properties']['sourceLotId'] not in replaced_lots]
modeled=[f for f in surface_candidates if not convex_rings_overlap(f['geometry']['coordinates'][0],curb_mask)]
curb_overlap_removed=len(surface_candidates)-len(modeled)
lot_counts={}
web_estimates=[]
for f in [*modeled,*aerial]:
    p=f['properties'];lot=p['sourceLotId'];image=lot in imagery_lots
    lot_counts[lot]=lot_counts.get(lot,0)+1
    rings=[[[round(x,8),round(y,8)] for x,y in ring] for ring in f['geometry']['coordinates']]
    xs,ys=zip(*rings[0]);bbox=[min(xs),min(ys),max(xs),max(ys)]
    web_estimates.append({'type':'Feature','id':f['id'],'bbox':bbox,'geometry':{'type':'Polygon','coordinates':rings},'properties':{
        'kind':'estimated-bay','estimated':True,'sourceLotId':lot,'imagery':image,
        'confidence':p['confidence'],'facilityId':p.get('facility_id'),'occupancy':None}})
save('estimates.geojson',{'type':'FeatureCollection','features':web_estimates})
save('estimate-summary.json',{'modeledBays':len(modeled),'modeledLots':len({f['properties']['sourceLotId'] for f in modeled}),
    'imageryBays':len(aerial),'imageryYear':small_lot_traces['imageryYear'],'imageryFacilityId':'duke-2021-W6',
    'imagerySource':small_lot_traces['sourceUrl'],'imageryLots':imagery_lots,
    'modelMethod':manifest['method'],'modelAssumptions':manifest['assumptions'],'lotCounts':lot_counts,
    'replacedModelLots':sorted(replaced_lots),'surfaceBaysRemovedForCurb':curb_overlap_removed,
    'coverage':'Surface models within the campus area and partial aerial traces at three surface lots. Curb estimates are in a separate layer.'})
curb_features=curb['features']
lewis_bays=sum(f['properties']['kind']=='estimated-curb-bay' for f in curb_features)
save('curb-estimates.geojson',{'type':'FeatureCollection','features':curb_features,'modeledBayExclusion':curb['modeledBayExclusion'],'streets':{
    'Lewis Street':{'estimatedBays':lewis_bays,'imageryYear':2025,'scope':'North curb alongside H Lot; readable portions of one striped row',
        'source':'https://webgis2.durhamnc.gov/server/rest/services/Rasters/Ortho2025/MapServer'}}})
print(json.dumps({'facilities':len(features),'signs':len(signs),'rateRecords':len(rules['annualPermitRates']),'visitorEntries':len(rules['visitorRates'])}))
