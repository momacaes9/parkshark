# 🦈 ParkShark Duke

**Find the parking spot that actually gets you to class fastest.**
Triangle AI Hackathon · Robotics track

![ParkShark dashboard](docs/screenshot.png)

## Inspiration / the problem
Around Duke's West & Science campus, drivers circle lots looking for a free stall. A full lot near the building usually loses to an emptier lot five minutes' walk away, but nobody can see that from the car. Circling wastes time, burns fuel, and clogs Research Dr and Science Dr at peak hours.

## What it does
ParkShark Duke is a live dashboard of **real** parking around the Wilkinson Building (534 Research Dr):

- **Every lot and every stall on satellite imagery.** 179 real lot polygons from OpenStreetMap, 14,108 stalls drawn (19 individually mapped plus 14,089 estimated inside the real lot outlines). Stall colors: 🟩 open, 🟥 taken, 🟦 ADA, 🟪 EV. Estimated stalls have dashed outlines.
- **Confidence tiers per lot:** *measured* (stalls mapped one by one), *counted* (capacity tagged in OSM), *estimated* (capacity from our stall-packing model).
- **Time-to-park agent.** Ranks lots by `drive + search + walk` to the destination, with `search_min = min(15, 1 / (1 − occupancy))`. Drag the 🚗 or right-click the map to change the start point.
- **Spot score (0–100).** Click any stall to see its score and the reasons: large vehicle next door, middle of the row, walking distance to the entrance, sun exposure on hot days, and walking in the rain (live weather from Open-Meteo).
- **Live sensors or a prior.** Reads `occupancy_latest` from Supabase when it's configured. Otherwise it uses a time-of-day/weekday occupancy prior.

## How we built it
**Hardware (sensor network)**
- **mmWave radar** units on light poles watch stall rows and detect a parked car even when it isn't moving.
- **Magnetometers** (in-ground pucks) detect the change in the Earth's magnetic field caused by a car's steel. They're cheap and run for years on a battery.
- **Raspberry Pi** gateways fuse the sensor readings per lot, hash plates on the device, and push the results to Supabase.
- **LoRa** radios link the battery sensors to the Pi over long range at low power. Parking lots have no Wi-Fi.

**AI**
- **Occupancy estimator:** fuses radar, magnetometer and entry/exit counts with the time-of-day prior, and fills in stalls that have no sensor yet.
- **Agents:** the time-to-park agent ranks lots by total door-to-door time, and the spot-scoring agent explains its choice with human-readable reasons.

**Software**
- `scripts/fetch_osm.py`: Overpass API → `data/parking.geojson`. Pure-Python geometry: polygon assembly, point-in-polygon, and a stall-grid generator (2.74 m × 5.5 m stalls, 7.3 m aisles, aligned to the lot's longest edge, capped at the OSM `capacity` tag).
- `index.html` + `app.js`: Leaflet on a canvas renderer (14k polygons stay smooth), Esri World Imagery, an optional Google satellite basemap, and Supabase REST.
- `supabase.sql`: `occupancy` table + `occupancy_latest` view with public-read RLS.

## Real numbers (OpenStreetMap, 1.5 km around Wilkinson)
| | |
|---|---|
| Parking lots (polygons) | **179** (15 garages) |
| Individually mapped spaces | **19** |
| Estimated stalls generated inside real lots | **14,089** |
| ADA spaces | **575** (tagged, or ~4% of estimated stalls near the destination) |
| EV spaces / charging stations in OSM | **0 / 0** |
| On-street parking segments tagged | **0** |
| Total lot capacity | **26,924** |

So OSM has lot outlines but almost no stall-level, EV or curb data for Duke. That gap is why we estimate stalls and why the sensor network matters.

## Data sources & licensing
- **Parking inventory:** © OpenStreetMap contributors, **ODbL 1.0**, fetched via the Overpass API. `data/parking.geojson` is a derived database and is shared under ODbL.
- **No Google-derived data.** The optional Google layer is only a display basemap. No inventory, geometry or counts come from Google.
- Imagery: Esri World Imagery (display only). Weather: Open-Meteo (CC BY 4.0).

## Privacy
- Raw license plates never leave the Raspberry Pi. They're stored only as salted SHA-256 hashes (`vehicle_events.plate_hash`), used to match entry/exit events.
- `vehicle_events` has RLS enabled and no public policy. The public dashboard only sees anonymous occupancy.

## Challenges we ran into
- OSM has lot polygons but almost no individual stalls, so we wrote a stall-packing generator that respects lot shape, holes and tagged capacity.
- macOS's built-in Python can't complete a TLS handshake with overpass-api.de (old LibreSSL), so the script falls back to `curl`.
- Drawing 14,000 stall polygons smoothly: we used Leaflet's canvas renderer and hid stalls below zoom 15.

## What's next
- Deploy radar + magnetometer pilots in one Science Dr lot and calibrate the estimator against ground truth.
- Contribute verified stall, ADA and EV data back to OpenStreetMap.
- Permit-aware routing (Duke permit zones), turn-by-turn to the chosen stall, and alerts when your favorite lot fills up.

## Run it
```bash
cp config.example.js config.js      # optional: add Supabase / Google keys
npm start                           # or: python3 -m http.server 8000
# open http://localhost:8000
```
Demo switches: `?weather=hot`, `?weather=rain`, `?hour=10` (simulate a time of day).
Refresh the data: `npm run data` (or `python3 scripts/fetch_osm.py`).
