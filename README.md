# 🦈 ParkShark Duke

**Find the parking spot that actually gets you to class fastest, including the curb spots Google and Apple don't show.**
Triangle AI Hackathon · Robotics track · [Privacy](privacy.html)

![ParkShark dashboard](docs/screenshot.png)

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
- **Reserve / Pay demo** with Google Pay in **TEST** mode (no real charge, no card data).

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
cp config.example.js config.js      # optional: Supabase / Google keys
npm start                           # or: python3 -m http.server 8000  → http://localhost:8000
```
Demo switches: `?date=2026-11-20&hour=18` (football game day), `?date=2026-11-17&hour=18` (basketball), `?weather=hot`, `?weather=rain`.

Rebuild the data:
```bash
npm run data                                    # OSM -> stalls, curbs, Duke names, elevation
python3 scripts/fetch_events.py                 # goduke.com home games
python3 -m venv .venv && .venv/bin/pip install ultralytics
.venv/bin/python scripts/cv_verify.py --n 60    # CV on the 60 curb segments nearest Wilkinson
```
