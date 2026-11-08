# Park Shark: Tier 3 street traffic sources and method

Companion to claude/tier3-data-source-registry.md. Same status key: VERIFIED, PARTIAL, UNVERIFIED.

Goal: an hourly "how busy is the street" number for the roads next to each lot, built from a traffic label or a crossing time plus road size and speed limit. Script: claude/fetch_traffic.py, segment list: claude/traffic_segments.json.

**Official counts (ground truth for volume)**
- NCDOT AADT stations (VERIFIED, queried Nov 7): https://services.arcgis.com/NuWFvHYDMVmmxMeM/ArcGIS/rest/services/NCDOT_AADT_Traffic_Segmentation/FeatureServer/0 . Point layer, fields LocationID, COUNTY, RTE_CLS, ROUTE, LOCATION, AADT_2002 to AADT_2022 (strings, blank years hold a space). AADT = vehicles per day, both directions, annual average. Counts near the lots:
  | Station | Road | Location | 2019 | 2021 |
  |---|---|---|---|---|
  | 0320000349 | SR 1320 Erwin Rd | East of Douglas St | 19,000 | 15,000 |
  | 0320000348 | SR 1320 Erwin Rd | West of Anderson St | 10,500 | 8,700 |
  | 0320000518 | SR 1320 Erwin Rd | East of Oregon St | 7,400 | 6,200 |
  | 0320000738 | Duke University Rd | West of Anderson St | 11,500 | 10,500 |
  2021 counts sit below 2019, likely a pandemic effect (not checked). Prefer 2019 or newer published years.
- NCDOT traffic volume maps page (VERIFIED): https://connect.ncdot.gov/resources/State-Mapping/pages/traffic-volume-maps.aspx . Historical AADT 2002 onward, Excel and File Geodatabase. Segment layer has K_FACTOR (design hour volume as a share of AADT) and D_FACTOR. No hourly volume download. Links to TCDS for historical counts (format not checked).
- Internal campus roads (Science Dr, Towerview Rd, Flowers Dr): no NCDOT station turned up, but only two name searches were run (Erwin, Duke University Rd). The full box search around campus failed from the cloud; `python3 fetch_traffic.py ncdot` runs it. These roads are probably Duke maintained (not confirmed), so Duke Parking and Transportation is the likeliest source of counts. Add this to the gate count email.
- DCHC MPO data page (VERIFIED, links not extracted): https://dchcmpo.org/resources/data . Lists AADT, turning movement counts 2011 to 2017, a Congestion Management Process map. Turning counts could cover Erwin Rd intersections, not checked.
- Warning: maps.durham.ca layers are Durham Region, Ontario, not Durham NC.

**Crossing time and speed (the "label" side)**
- Google Routes API, computeRoutes (VERIFIED docs): returns `duration` (with traffic) and `staticDuration` (no traffic). trafficModel BEST_GUESS, PESSIMISTIC, OPTIMISTIC works only with routingPreference TRAFFIC_AWARE_OPTIMAL and is marked pre GA. A future departureTime gives a typical, history based estimate. Past departure times were not documented. Pricing: Compute Routes Essentials 10,000 free per month, Pro 5,000 free per month, then $5 or $10 per 1,000. Which SKU traffic aware calls bill under was NOT confirmed. Our plan (4 segments x 168 hours = 672 calls) fits either cap.
- Google Maps "Typical traffic" layer (green, orange, red, dark red): no API. Can only be read by eye and typed into traffic_labels.csv. Google does not publish the speed thresholds behind the colours.
- TomTom Traffic Flow Segment Data (VERIFIED docs): currentSpeed, freeFlowSpeed, currentTravelTime, freeFlowTravelTime, confidence 0 to 1, roadClosure, frc (FRC0 motorway to FRC6 local road). Live only. 20,000 free requests per month. Historical (Traffic Stats) is Contact Sales.
- HERE Traffic API v7 flow (VERIFIED docs): speed, freeFlow (m/s), jamFactor 0 to 10 (10 = closed), confidence bands tell you whether the speed is live (above 0.7), historical (0.5 to 0.7) or just the speed limit (0.5 and below). Free tier not checked.
- Terms of use (UNVERIFIED, check before the demo): Google, TomTom and HERE all limit storing their data. Saving a week of results to build a model input may not be allowed. Read the Google Maps Platform terms on caching first.

**Road size and speed limit**
- OpenStreetMap via Overpass (PARTIAL): highway type, lanes, maxspeed, oneway, access tags. The cloud fetch was refused (robots), so run `python3 fetch_traffic.py osm` on the Mac. Missing maxspeed falls back to an assumed default (NC 35 mph in town, lower for campus roads).

**How a label becomes a number (method in fetch_traffic.py)**
1. Speed ratio r = current speed / free flow speed. From Google: r = staticDuration / duration, or currentSpeed / freeFlowSpeed (TomTom), or a label lookup (ASSUMPTION: green 0.90, orange 0.65, red 0.40, dark red 0.15).
2. Greenshields model: speed = speed limit x r, density = 185 x (1 minus r) cars per mile per lane (jam density ASSUMPTION), flow = density x speed x lanes.
3. Density is the main index because it only rises as speed falls. Flow peaks at r = 0.5 and falls again in a jam, so the same flow can mean light or heavy traffic.
4. The BPR curve (FHWA, 0.15 and 4) was tested and rejected: every r below about 0.87 hit the same capped value, so orange, red and dark red became one number.
5. Calibrate: for a segment with an NCDOT station, compare 2 x (sum of one direction hourly flow over the week) / 7 with AADT. The ratio is a scale factor for that road class. The 2x assumes an even directional split.

**How it should enter the model (proposal)**
- Not as a model feature: the training lots (Birmingham 2016, The Hague) have no matching traffic data, so the model could not learn its effect.
- Use it as (a) a check that the campus template's morning ramp lines up with the inbound street peak, and (b) a later campus multiplier once Duke gate counts let us measure the link.
- Honest limit: Erwin Rd carries hospital and through traffic, so a busy street does not prove a full lot. Treat the link as a hypothesis to test.

**Next steps**
1. On the Mac: `python3 fetch_traffic.py ncdot` then `python3 fetch_traffic.py osm`.
2. Fill start and end coordinates in traffic_segments.json (one block per segment, from Google Maps).
3. Read the Google Maps Platform terms on storing results. If OK, create an API key, run `google --dry-run`, then `google`.
4. If no key: read the Typical traffic layer by eye for a few weekday hours and type them into traffic_labels.csv.
5. `python3 fetch_traffic.py estimate`, then check the Erwin Rd scale factor against NCDOT.
6. Add campus road counts to the gate count request to Duke Parking and Transportation.
