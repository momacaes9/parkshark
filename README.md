# 🦈 ParkShark Duke

**Find the parking spot that actually gets you to class fastest, including the curb spots Google and Apple don't show.**
Triangle AI Hackathon · Robotics track · **Live: https://parkshark-blond.vercel.app** · [Privacy](privacy.html)

![ParkShark dashboard](docs/screenshot.png)

## Main page (real data only)

The main page (`index.html`) shows a full-screen map of parking within 1.5 km of the Wilkinson Building:
- Each lot, deck, street and sensor-pilot group appears as a pin showing how many spaces are free, colored by availability.
- Clicking a pin opens a panel with an SVG drawing of that place's real stall layout. The drawing shows stalls, ADA/EV spaces, entrances, and the roof level for decks.
- The panel also lists sourced facts: Duke name, capacity and its basis, permit zones, visitor rate, hours, ADA/EV, posted signs and source links.
- Badges say whether each number is **counted**, **estimated** or **measured**.
- Anything without a source shows **"Not published"**. We never guess prices, spaces or names.

| Layer | Source | Priority |
| --- | --- | --- |
| Facility names, rates, permits, published capacities, on-street rules, Lewis St traced bays | Anne's Duke atlas (`structured-map`: Duke Parking & Transportation, Duke Campus Maps, Durham orthophotos) | 1st: official |
| Lot outlines, mapped stalls, capacity tags, `building:levels` | OpenStreetMap (ODbL) | 2nd |
| Estimated stalls, deck roof grid | Our grid model inside the real OSM outline | labeled *estimated* |
| Free spaces | Live sensors (Supabase) when reporting, else Jacob's Tier 3 model (`tier3-ai-estimate`) with its confidence level | *measured* / *estimated* |
| Posted signs | City of Durham parking-sign inventory | as published |

A Duke facility is joined to an OSM polygon only when the Duke point lies inside that polygon. A capacity Duke publishes for a permit zone covering several facilities is shown as a zone note, not as one lot's capacity.

Rebuild the data with `python3 scripts/build_data.py && python3 scripts/build_main.py`. This writes `data/main.json` and `data/layouts.json`.

**Map:** Google Maps JS when `GOOGLE_MAPS_KEY` is set on the server. The browser gets it from `/api/config`; restrict the key to your site's referrers. Without the key the page falls back to OpenStreetMap tiles. Google is display only and nothing from Google goes into the inventory. "Use my location" stays in the browser.

The previous analyst dashboard is still available at `classic.html`.

## Inspiration
Around Duke's West and Science campus, drivers circle lots looking for a free stall. A full lot next to the building usually loses to an emptier lot five minutes' walk away, but nobody can see that from the car. Map apps show a "P" pin with no stalls, no prices, no game-day rules and no curb parking. Circling wastes time and fuel and clogs Research Dr and Science Dr at peak hours.

## What it does
ParkShark Duke is a live map of **real** parking around the Wilkinson Building (534 Research Dr):

- **Every lot and every stall on satellite imagery.** 274 lots, 12,473 stalls drawn. Stalls are laid out along the lot's real mapped parking aisles, so rows line up with the asphalt. Colors: 🟩 open, 🟥 taken, 🟦 ADA, 🟪 EV. Estimated stalls have dashed outlines.
- **Lots OSM was missing.** 89 lots were found only through their mapped parking aisles. A **Draw lot** tool lets anyone outline a missing lot and export it to `data/manual_lots.geojson`.
- **Duke names, prices and rules.** Duke's published lot table (capacity, gating, rate tier) is attached to 20 lots. Visitor rates, daily passes and garage clearances come from parking.duke.edu.
- **Curb parking candidates.** 415 curb segments (3,052 parallel spaces) are generated from road geometry. **Computer vision** on NC OneMap aerial imagery confirms which ones actually have parked cars.
- **Time-to-park agent + compare view.** Ranks every option by `drive + search + walk`, with `search_min = min(15, 1/(1 − occupancy))`, plus price. Sort by best value (time × your value of time + cost), fastest or cheapest.
- **Driver profiles.** Visitor / Duke affiliate / permit holder, stay length, vehicle size (garage clearance, tight stalls), ♿ wheelchair (ADA stalls), 👵 senior mode (slower walk + uphill penalty from a DEM), ⚡ EV.
- **Spot score (0–100)** with reasons: big vehicle next door, middle stall, walk, slope, sun on hot days, rain, price, ADA/EV fit.
- **Game days.** 25 Duke home games (football + men's basketball) from goduke.com. On game day a banner shows Duke's event rules (free general-public lots for football, $25 for basketball) and occupancy jumps for lots near Wallace Wade and Cameron.
- **💬 Parking Concierge (LLM agent).** Tell it "arriving 9:30 with a pickup truck for 3 hours, budget $10". Claude (`claude-opus-5-5`) calls our own functions as tools (time-to-park ranking with a forecast for your arrival time, Duke pricing, spot scoring, show-on-map) and answers with a lot **and** a specific stall, highlighted on the map.
- **Reserve / Pay demo** with Google Pay in **TEST** mode, labelled "Demo, no real charge" (no card data, nothing stored).

## How we built it
**Hardware (sensor network)**
- **24 GHz gate radar** detects a car stopping at the gate and triggers the camera. Plate OCR runs **on the Raspberry Pi** and the plate is hashed with a per-session salt immediately.
- **60 GHz BSD radar** follows the car into the lot. Where its track stops becomes the stall.
- **Magnetometers** in the entrance lanes give in/out counts for "counted" lots.
- **LoRa** (WM1302 gateway) carries only the hashed ID and timestamps. Images never leave the Pi.

**AI / CV**
- **Occupancy estimator:** schedule prior (hour, weekday), events and weather today; Kalman-style fusion with the counters when sensors are live.
- **Curb verification (CV):** `scripts/cv_verify.py` downloads **NC OneMap Orthoimagery_Latest** (6-inch, NC CGIA, not Esri or Google) for each candidate segment and runs an off-the-shelf **Ultralytics YOLOv8n-OBB model pretrained on DOTA** (aerial "small vehicle" class, oriented boxes). A car counts as parked if its centre falls inside the segment's curb spaces, so moving traffic in the lane is ignored. We processed the **60 segments closest to Wilkinson** (23 s on a laptop): **5 observed** (8 parked cars, 16 spaces). One of them is the 54 m Research Dr curb from our site survey, with 4 cars detected. The rest of the 415 segments stay "candidate - unverified". Run `--n 415` to do them all.
- **Agents:** the time-to-park/compare agent and the explainable spot-scoring agent.

**Software**
- `scripts/fetch_osm.py`: Overpass → `data/parking.geojson`. Pure-Python geometry. Stalls (2.74 × 5.5 m) go along mapped parking aisles (6 m aisle). Leftover lot area gets a grid oriented to the dominant aisle or the polygon's minimum rotated rectangle, with an offset search. Every stall must sit fully inside the polygon and clear of buildings, roads and other stalls.
- `scripts/curbs.py`: candidate curbs. Campus roads offset 3 m, with 10 m cut-outs at intersections, crosswalks, driveways, bus stops and signals, split into 6.1 m spaces.
- `scripts/fetch_events.py`, `scripts/add_elevation.py`, `data/pricing.json`, `data/duke_lots.json`.
- `index.html` + `app.js`: Leaflet (canvas renderer), Leaflet.draw, Esri imagery, Supabase REST, Open-Meteo, Google Pay TEST.
- `api/concierge.js`: Vercel serverless proxy to the Claude API. It holds `ANTHROPIC_API_KEY` server-side and sets the model, system prompt and 4 strict tools itself, with per-IP rate limiting, size checks and an output cap. The browser runs the tool loop against the live map state, so the key never reaches the browser.
- `supabase.sql`: `occupancy` + `occupancy_latest` (public read) and `vehicle_events` (hashed plates, no public access, 30-day purge).

## Real numbers (1.5 km around Wilkinson, OSM snapshot 2026-11-08)
| | |
|---|---|
| Parking lots | **274** (185 OSM polygons + 89 recovered from parking aisles; 16 garages) |
| Lots with a Duke name | **20** polygons / 19 Duke facilities (+ Blue Zone area) |
| Individually mapped spaces (OSM) | **18** |
| Estimated stalls | **12,455** (10,571 along mapped aisles + 1,884 grid fill) |
| ADA stalls | **506** |
| Total lot capacity | **31,805** |
| OSM street-parking tags | **0** → we generated **415 candidate curb segments / 3,052 spaces** |
| CV-verified curb segments | **60 checked → 5 observed** (8 cars, 16 spaces) |
| EV chargers mapped in OSM | **0**: the EV filter has nothing to show yet; a gap to fill |
| Duke home games loaded | **25** (6 football, 19 men's basketball) |

## Data sources & licensing
- **Inventory:** © OpenStreetMap contributors, **ODbL 1.0** (Overpass API). `data/parking.geojson` is a derived database shared under ODbL.
- **Duke lots, prices, event rules:** [parking.duke.edu garages/lots/fees](https://parking.duke.edu/parking-permits/garages-lots-fees/), [visitor parking](https://parking.duke.edu/visitor-parking), [Blue Spot daily](https://parking.duke.edu/parking-permits/occasional-parking-options/duke-blue-spot/), [football](https://parking.duke.edu/visitor-parking/athletic-event-parking/football-parking), [men's basketball](https://parking.duke.edu/visitor-parking/athletic-event-parking/mens-basketball-parking/). Duke doesn't publish coordinates, so names are matched by OSM name, by an explicit OSM id (PG1/PG2), or **by proximity** to the building Duke names (shown with "≈"). Verify these.
- **Schedules:** goduke.com football and men's basketball text schedules.
- **Curb CV imagery:** NC OneMap Orthoimagery (NC Center for Geographic Information and Analysis). Model: Ultralytics YOLOv8n-OBB (AGPL-3.0) pretrained on DOTA.
- **Elevation:** Open-Meteo Elevation API (Copernicus DEM GLO-90). **Weather:** Open-Meteo (CC BY 4.0). **Display imagery:** Esri World Imagery.
- **No Google-derived data.** The optional Google layer is only a basemap, and Google Pay is a TEST-mode demo.

## Privacy
See **[privacy.html](privacy.html)**. In short: plate IDs are hashed with a per-session salt on the Pi, images are deleted right after OCR, there are no names and no driver location tracking, and hashed events are purged after 30 days. The live plate demo runs only on team and volunteer cars.

## Challenges we ran into
- OSM has lot outlines but almost no stalls, no curb tags and no EV chargers here. We built stall packing along real aisles, found 89 unmapped lots through their aisles, and generated curb candidates.
- Grids aligned to a polygon's longest edge looked wrong at zoom 18. Aligning to the mapped aisles fixed it.
- "Observed" curb parking can be a drop-off (e.g. Duke Medicine Circle). We label it honestly and add a ticket-risk premium when ranking.
- macOS system Python can't TLS-handshake with overpass-api.de, so the scripts use `curl`.

## Accomplishments we're proud of
Real stalls on real aisles; real Duke prices and game-day rules; a CV pipeline on public imagery that found the exact curb our team measured by hand.

## What's next
- Run CV on all 415 curb segments and multiple imagery years; fine-tune the detector on 15 cm NC imagery.
- Pilot radar + magnetometer sensors in the Science (Physics) Lot and calibrate the estimator.
- Map accessible entrances and EV chargers, then contribute verified stalls, ADA, EV and curb data back to OSM.
- Real reservations with Duke Parking's Blue Spot.

## Run it
```bash
npm install
cp .env.example .env                # add ANTHROPIC_API_KEY for the concierge (gitignored, server-side only)
cp config.example.js config.js      # optional, local only: Supabase / Google Maps browser keys
npm start                           # node scripts/dev-server.mjs -> http://localhost:8000 (static + /api)
```
Deploy: `npx vercel --prod`. Set the key once with `npx vercel env add ANTHROPIC_API_KEY production`. `vercel.json` pins the plain static + `api/` functions setup (otherwise Vercel mistakes the browser `app.js` for a Node server).
Demo switches: `?date=2026-11-20&hour=18` (football game day), `?date=2026-11-17&hour=18` (basketball), `?weather=hot`, `?weather=rain`.

Rebuild the data:
```bash
npm run data                                    # OSM -> stalls, curbs, Duke names, elevation
python3 scripts/fetch_events.py                 # goduke.com home games
python3 -m venv .venv && .venv/bin/pip install ultralytics
.venv/bin/python scripts/cv_verify.py --n 60    # CV on the 60 curb segments nearest Wilkinson
```

---

## ParkShark — Duke Parking Atlas

A static parking explorer for Duke and nearby Durham, with source-backed rules and separately labeled layout estimates. [Live dashboard](https://parkshark-duke-atlas.jpama.chatgpt.site/). Serve the checked-in `dist/` directory; no API key, backend, or package installation is required.

```sh
python3 -m http.server 4173 --directory dist
```

Open `http://localhost:4173`. For another static host, publish `dist/` as-is. Netlify can use the included configuration. The dashboard and GitHub repository have separate access controls.

### Snapshot: November 7, 2026

- 128 Duke official facility reference points. Visitor/permit sections can be separate points in one physical garage; this is not a count of distinct lots.
- 498 community-mapped lot polygons and one additional OSM parking point.
- 31 existing parking-space polygons. Several contain groups of bays; this is not 31 verified individual stalls.
- 12,917 **low-confidence modeled bay shapes** across 169 mapped surface lots, plus 40 **imagery-guided estimates** across three partially traced surface lots. These are shape counts. Zoom in and use **Estimates** to toggle this layer.
- 22 **estimated on-street bays** on Lewis Street, separate from Duke’s published 44-space count. See [Lewis Street on-street estimates](#lewis-street-on-street-estimates).
- 3,885 Durham parking signs located by the authoritative sign-to-pole foreign key; six explicitly inactive records excluded, 29 unknown-status records retained.
- 18 road-centerline features for five named Duke streets with published visitor parking rules. The lines do not establish permitted curb extents.
- Eight additional OSM street-centerline segments with community parallel-parking tags.
- 48 published annual permit-rate entries and 21 visitor-table entries retained in the rule snapshot. Only verified exact associations are attached to map points. Some current-rate entries have no location in the campus map feed.

The map exposes permit eligibility, visitor prices, scoped capacity strings, after-hours access, five known EV facility locations (six mapped sections), and known accessible parking. Missing facts remain null. Capacity is not occupancy. Approximate campus area labels follow Duke's facility-code prefixes.

### Data provenance

The app's **Sources & coverage** panel links every authoritative source and explains conflicts. Each exported feature retains its original source identity. Duke rates are joined by explicit facility IDs, with documented corrections in `scripts/build_data.py`; there are no spatial or fuzzy joins between Duke points and OSM polygons.

`source-data/` preserves the downloaded inputs required to reproduce `dist/data/`:

```sh
python3 scripts/build_data.py
python3 scripts/validate_data.py
```

### Modeling

**Surface geometry model.** [`scripts/estimate_spaces.py`](scripts/estimate_spaces.py) selects whole OpenStreetMap (OSM) polygons explicitly tagged `parking=surface` within the study boundary. It projects them into UTM zone 17N to work in metres, subtracts a 1.2 m perimeter setback, buffered buildings and known space polygons, and fits 2.6 × 5.4 m rectangles. For each usable polygon it tests two orientations and eight grid offsets per orientation. It keeps the layout with the most rectangles for which both the bay and a 6.2 m adjacent aisle slice fit. The dimensions are modeling assumptions. The [model manifest](source-data/estimates/manifest.json) records the boundary, assumptions, omissions, and counts per source lot before publication filters.

**Why small lots can be undercounted.** A polygon drawn tightly around a row can omit the driveway or shared aisle used to reach it. The grid can then fit zero bays even when stripes are visible. Irregular boundaries, missing tags and setbacks can also reduce coverage. A zero or missing estimate means the model found or drew no bays. Where OSM supplies a `capacity` tag, the detail panel shows that unverified community count separately. For example, [Branson Hall Parking](https://www.openstreetmap.org/way/611698330) reports four spaces in OSM and has one modeled shape. Shadows in the reviewed imagery prevent a reliable trace there.

**Aerial review.** At reviewed lots the build uses a partial trace from [Durham's 2025 orthophotos](https://webgis2.durhamnc.gov/server/rest/services/Rasters/Ortho2025/MapServer) and omits synthetic shapes for the same OSM footprint. Each trace covers a specific visible area and leaves unreviewed areas blank. The [small-lot anchors](source-data/estimates/small-lot-anchors.json) record exact lot IDs, image tile origins and divider endpoints. The builder converts adjacent endpoints into WGS84 polygons. The tiles themselves are fetched on demand and are not bundled.

| Footprint | Bays drawn in the previous build | Bays drawn now | Evidence |
| --- | ---: | ---: | --- |
| [Surface lot 1065904267](https://www.openstreetmap.org/way/1065904267) | 0 | 2 | Readable painted dividers in the north row. |
| [Surface lot 594764289](https://www.openstreetmap.org/way/594764289) | 0 | 8 | Repeated markings in the west part of one row, with some divider ends interpolated around vehicles. The trace follows the imagery and extends slightly beyond the community footprint at the aisle edge. |
| [Bryan Center surface lot](https://www.openstreetmap.org/way/32336367) | 30 | 30 | Northeast row anchors and interpolated bay divisions. |

**Counts and confidence.** Published Duke counts keep their original scope, including shared facilities and designated visitor subsets. OSM capacity tags are community reports. Modeled counts are numbers of synthetic shapes with low confidence. Aerial counts are numbers of traced or interpolated shapes, with medium confidence in row placement and varying visibility of individual dividers. A field count or a current facility plan is needed for full capacity. The estimates do not establish access, accessible or EV designation, or occupancy. Garage interiors remain unmapped.

To regenerate the estimates using Python 3.12:

```sh
python3 -m pip install -r scripts/requirements-geometry.txt
python3 scripts/estimate_spaces.py
python3 scripts/trace_bryan.py
python3 scripts/trace_lewis.py
python3 scripts/build_data.py
python3 scripts/validate_data.py
```

The exported snapshot keeps `estimatedBayLayouts` and `estimatedCurbLayouts` separate from `spaceFootprints`. `estimateSummary.lotCounts` tallies displayed surface shapes by OSM ID and `estimateSummary.imageryLots` records the count and scope of each partial trace. Estimated IDs can support review and reconciliation. The occupancy adapter uses existing source space footprints.

### Lewis Street on-street estimates

**Open the layout:** [Lewis Street on the live map](https://parkshark-duke-atlas.jpama.chatgpt.site/?street=Lewis%20Street). From the main map, choose **On-street**, search **Lewis Street**, and select **View curb estimates on aerial**. Blue dashed outlines are estimated bays. The orange dashed road centerline is a reference line. The **Estimates** toggle controls the blue layer.

| What the map shows | Source and interpretation |
| --- | --- |
| 44 published visitor spaces; daily 8 a.m. to sunset while the Gardens grounds are open | [Duke Gardens](https://gardens.duke.edu/visit/parking/). This count describes the street-level visitor inventory. |
| Blue Spot, $2/hour plus transaction fees | [Duke Parking & Transportation](https://parking.duke.edu/visitor-parking/blue-spot-hourly-parking/). Applies to signed visitor parking. |
| 22 medium-confidence bay polygons: 5 west-middle, 5 middle, 12 east | Manually placed on readable painted dividers in [Durham's 2025 orthophotos](https://webgis2.durhamnc.gov/server/rest/services/Rasters/Ortho2025/MapServer). All are on the north side next to H Lot. Each polygon comes from a pair of adjacent stripe endpoints. |
| Unmapped gaps | Foliage, shadows, vehicles and equipment hide parts of the row. The opposite curb, intersections, entrances and H Lot's interior rows were excluded. |

**Interpretation:** 22 is the number of drawn estimates, not the number of spaces available, surveyed, or legally confirmed. The 44-space count was never used to place or fill bays. The geometry does not establish current signs, ADA or EV designation, or occupancy. These fields are `null` on every estimate. Check signs on arrival.

**Rebuild and review:** `source-data/estimates/lewis-curb-anchors.json` records the mosaic tile range, individual stripe endpoints, omissions, and source links. Run `python3 scripts/trace_lewis.py`, `python3 scripts/build_data.py`, then `python3 scripts/validate_data.py`. This is an offline rebuild with the Python standard library; no imagery downloads are required. The first command converts the recorded pixel anchors to WGS84 and writes `source-data/estimates/lewis-curb.geojson`. The build copies the geometry to `dist/data/curb-estimates.geojson`, which the app exports as `estimatedCurbLayouts` in snapshot schema 3.

**Overlap handling:** The anchors also describe a mask around the curb row, including obscured gaps. The build removes 47 surface-model shapes that overlap the mask. The mask is metadata for de-duplication; it is neither drawn as parking nor used to assign parking permission. The original model inputs are retained for reproducibility.

The public campus map returns null parking geometry and car-capacity fields; bike-rack capacity values are deliberately excluded. Duke's historical library GIS archives require authorized access and were not used. Current exact stall geometry, garage floor plans, and signed curb spans remain the largest coverage gaps. The map is not 90% complete at individual-stall level.

OSM geometry is © OpenStreetMap contributors under ODbL-1.0; its layers and attribution remain separate. Duke and Durham source facts retain their source URLs and applicable reuse terms. The basemap uses normal interactive OSM tiles with visible attribution and browser caching, without bulk/offline downloads. Aerial tiles are City of Durham/Durham County GIS 2025 orthophotography, native zoom 20. Internet is needed for basemaps and optional fonts; all parking records and the Leaflet library are bundled.

### Fill in the missing spaces

Choose **Map spaces**, switch to the aerial view, and zoom to 19+. Select a facility and click the four corners of one visible bay in order. Implausible/crossed polygons are rejected. Drafts use stable IDs, remain local to the browser, and can be exported as GeoJSON. They are explicitly marked `needs-review`; they are not silently added to the published source data. Accessible/EV classifications still require evidence. A covered garage needs floor plans or a field survey; aerial imagery cannot reveal its interior stalls.

### Occupancy integration

`dist/data/occupancy.json` is an intentionally empty adapter seam. The app reads it on page load. Replace this file (or change the fetch URL in `app.js`) with the team's feed using:

```json
{
  "schemaVersion": 1,
  "updatedAt": "2026-11-07T18:00:00Z",
  "observations": [
    {
      "featureId": "a-real-source-feature-id",
      "status": "occupied",
      "observedAt": "2026-11-07T18:00:00Z"
    }
  ]
}
```

Supported statuses are `occupied` and `vacant`. Records older than five minutes, future timestamps and IDs not present in the geometry layer are ignored. No feed is connected and no demonstration occupancy is generated. The current adapter colors source space footprints; review grouped geometries before treating them as single stalls. Production streaming/polling and capacity aggregation belong in the occupancy service.

### Publication

The live dashboard is hosted on Sites, where its audience is managed separately. This repository is portable: its `dist/`, scripts, and input data can run and deploy independently of the Sites checkout.
