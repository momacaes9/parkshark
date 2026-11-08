# 🦈 ParkShark Duke

### 🔗 Live: **https://parkshark-blond.vercel.app**

![ParkShark: Google Maps view with lot pins and the Garage I deck panel open](docs/screenshot.png)

## The problem
Drivers around Duke's Wilkinson Building circle full lots while emptier ones sit a few minutes' walk away.
Map apps show a "P" pin with no free-space count, no price, no permit rules and no curb parking.
The facts exist (Duke's own facility data, OpenStreetMap), but nobody puts them on one honest map.

## What it does
- **Google Maps with labeled lot pins:** name and free spaces, colored by availability, for 309 lots and decks plus on-street segments.
- **Click any lot or street** for its stall layout (open / taken / ADA / EV, entrances, deck roof level) and official Duke facts: capacity, permits, visitor rate, hours, sources.
- **Honest numbers:** every value is badged *measured*, *counted* or *estimated*; anything unsourced says "Not published" (77 lots with official Duke data, 13 lots + 5 streets with a published visitor rate).
- **Free-space forecast for any hour** from our Tier 3 model, with its confidence level; live sensor readings replace it when they report.
- **Search, filters (♿ EV 🛻 💲), my location, dark mode**, a 💬 Gemini parking concierge, and a Google Pay **TEST** demo (no real charge).

## How we built it
- **Data:** Duke Parking & Transportation / Campus Maps (Anne's atlas) first, then OpenStreetMap, merged by `scripts/build_main.py` with point-in-polygon joins only.
- **Occupancy:** Tier 3 model (scikit-learn quantile gradient boosting trained on public car parks, campus-blended) plus a privacy-first radar/LoRa sensor pilot.
- **App:** Google Maps JS (Advanced Markers, Places) with SVG stall schematics; Leaflet + OSM fallback without a key.
- **Backend:** Vercel serverless (`/api/config`, `/api/concierge` → Gemini 3.8 Flash with function calling); keys live only in server env vars.

## Team
- **Ishan**: founder, product
- **Anne**: Duke parking atlas (official data)
- **Jacob**: Tier 3 occupancy model
- **Monica**: map, app and deployment

Built during **Triangle AI Hackathon 2026**.

📄 **[Full details](docs/DETAILS.md)** (data provenance, Duke atlas, Tier 3 model, run instructions) · 🔒 **[Privacy](https://parkshark-blond.vercel.app/privacy.html)**
