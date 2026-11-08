# Park Shark: Duke lot traffic model (Duke data only)

Separate from the Birmingham / Hague occupancy model in the folder above. Nothing here uses foreign data.

Question: at which hours, and at which of the four lots (Science/Physics 52, Public Policy 85, Trent 80, IM 27), is traffic heaviest?

## Run it

    python3 duke_traffic.py              # writes outputs/
    python3 duke_traffic.py 2026-11-17   # top 10 busiest lot hours for one day

Python 3.9+, standard library only. Every input is in `duke_traffic_inputs.json`, tagged VERIFIED, PARTIAL or ASSUMPTION. Change an assumption there and rerun.

## How it works

For each lot, day and hour, in vehicles per hour:

* **Street traffic** = NCDOT daily count (AADT) x weekday/weekend factor x share of the day in that hour, split across the counted roads next to the lot, then adjusted for no class days, staff holidays and game or commencement windows.
* **Direction**: each road's hourly traffic is split toward and away from campus with NCDOT's D factor (share of peak hour traffic going the busier way): share toward campus = 0.5 + (D - 0.5) x pattern, where the pattern is +1 in the morning peak, -1 in the evening peak, 0 otherwise. Toward campus counts 1.0, away counts 0.5.
* **Lot in and out** = spaces x turnover x share of arrivals plus departures in that hour, plus churn at Duke class change times for the Science and Public Policy lots.
* **Total** = direction weighted street + lot. **Index** 100 = the busiest lot hour on a normal class Tuesday (Trent Lot, 08:00, about 712 weighted vehicles an hour).

## What is real and what is assumed

VERIFIED: NCDOT counts (2025 where published, from the NCDOT 2025 AADT and traffic segments layer; Science Dr and Towerview Rd stop at 2017), NCDOT D factors on Erwin Rd (0.675 west of Fulton St, 0.687 east of it), NCDOT K factor check (Erwin Rd 0.085 to 0.100 vs the model's weekday peak of about 0.087), lot capacities, building coordinates (OpenStreetMap), Duke 2026-27 academic calendar, Duke class start times, game dates and tip times, commencement, basketball checkpoint rule (2 h before to 45 min after).

ASSUMPTION: D factor 0.65 on Trent Dr, Duke University Rd, Science Dr and Towerview Rd; which way the busy flow goes (toward campus mornings, away evenings); the 1.0 / 0.5 direction weights; the hour by hour shape of street traffic, weekday/weekend factors, equal split between roads, lot turnover and arrival/departure timing, break and holiday factors, event boost size (x1.6, x2.0 for football, UNC and commencement).

Google Maps Typical traffic was checked by eye on Nov 7, 2026: Erwin Rd and Duke University Rd were green at every time checked (Tue 6:10 to 17:55, Sat noon and 17:55). Google shows no colour on Science Dr, campus Towerview Rd, Flowers Dr or Wannamaker Dr. So it cannot supply an hourly pattern.

## Main limits

* Street traffic dominates the total, so Trent Lot (next to Erwin Rd and Trent Dr) ranks first on almost every normal day. Use the "Cars in and out of the lot only" view or the "Each lot against its own peak" scale to compare timing inside each lot.
* No hourly count exists for any of these roads in the sources we could reach. NCDOT's TCDS portal blocked automated access; asking NCDOT or Duke Parking & Transportation directly is the next step.
* Flowers Dr (Trent Lot's entrance) and Wannamaker Dr have no count at all.
* A busy street does not prove a full lot. One day of gate counts at each lot would test the link and replace the biggest assumption.

## Files

* `duke_traffic.py`, `duke_traffic_inputs.json`: the model and all inputs
* `outputs/duke_traffic_by_hour.csv`: every lot x hour x day, Nov 7 2026 to May 16 2027 (street toward campus, away, weighted, lot in and out, total)
* `Park_Shark_Duke_Traffic.xlsx`: everything above in one workbook, with live formulas for a typical weekday and open spots
* `build_xlsx.py`: rebuilds the workbook
* `outputs/duke_traffic_top25.csv`: the 25 busiest lot hours of the year
* `outputs/dash_*.csv`: tables shown on the dashboard

## Research Drive and the team magnetometer (added Nov 7, 2026)

* Two new lots: Bryan Research Garage (163) and Research Drive Garage Reserved (239), each fed by
  Research Dr (weight 0.5) and Erwin Rd east of Douglas St (weight 0.5, ASSUMPTION).
* Research Dr has no NCDOT count. It is a **measured** station: the model reads `vehicle_count`
  rows from `../sensor_data/sensor_feed.csv` (same file as park_shark.py). For each hour it uses
  the exact count, else the average measured count for that kind of day, else it skips the road
  and Erwin Rd carries the full weight (the existing rule: weights split over counted roads).
* Until that file exists the new lots run on Erwin Rd alone, which is why they rank above Trent
  Lot (index about 176 at 08:00). Treat that as a placeholder, not a finding.
* Index 100 stays tied to the original four lots, so old results did not change (checked: all
  18,336 original rows identical).
* Assumption to confirm: the magnetometer counts both directions.
* `build_xlsx.py` was not rerun; the workbook still shows the original four lots.
