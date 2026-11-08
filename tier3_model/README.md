# Park Shark: Tier 3 occupancy model (model only, no Duke data yet)

This model estimates how full a Duke employee lot is for any hour of any day, with a
10th to 90th percentile band. It learned from **public** car parks in Birmingham (UK) and
The Hague (Netherlands), then gets blended with a **campus template that we assumed**.
It has never seen a single Duke reading. Do not present it as accurate for Duke.

Target lots: Science (Physics) 52, Public Policy 85, Trent 80, IM 27.

## How to run

```
python3 park_shark.py run                      # Steps 2 to 8, writes everything to outputs/
python3 park_shark.py predict "Trent Lot" "2026-11-10 09:00"
python3 park_shark.py calibrate duke_counts.csv  # Step 9, once real counts exist
python3 fetch_data.py raw | weather <place> <start> <end> | forecast
```

Python 3.11+ with pandas, numpy, scikit-learn, matplotlib, joblib, holidays.
All campus guesses live in `config.toml`, marked as ASSUMPTIONS.

How the data was actually obtained: the cloud build machine was blocked from UCI, Zenodo and
Open-Meteo, so the files were downloaded through a normal web browser and copied into `data/`.
`fetch_data.py` does the same downloads on any machine with open internet.

## What the model does

1. Predicts an **occupancy rate** (0 to 1), not a car count, so one model works for a 27 space
   lot and a 2,000 space car park. Cars = rate x capacity at the very end.
2. Features: hour, day of week, weekend flag, sine and cosine of the hour, temperature,
   precipitation, snowfall, weather code, public holiday flag. No lot ID and no capacity,
   because Duke lots are new to the model and far smaller than the training car parks.
3. Model: scikit-learn `HistGradientBoostingRegressor` with quantile loss, three versions
   (10th, 50th, 90th percentile). The 50th percentile is the point estimate.
4. **Campus weighting (Step 5A):** each training lot gets a weight equal to the correlation
   of its average weekday and weekend hourly curve with the campus template
   (minimum 0.05). Office shaped car parks count more.
5. **Campus blend (Step 5B):** estimate = w x template + (1 - w) x model, with w = 0.3 from
   8:00 to 16:30 (model driven) and w = 0.95 at all other hours (assumption driven). Then
   multipliers: break or closed holiday 0.5, event day 1.0, class in session 1.0.
6. **Band (Step 7):** the raw 10/90 quantile band is stretched by a scale factor tuned on a
   hidden quarter of the *training* lots (never on test data) to hit 80% coverage. Where the
   template weight is high, an extra +/- 0.10 x w is added, because those hours are guesses.
7. `class_in_session` and `event_flag` exist as columns but are **not model features**. In
   the public data we do not know them (always 0), so the model could not learn anything
   real from them. They only act through the campus multipliers.

## Data cleaning decisions

* Birmingham `Occupancy` is a car count (99.9% of values above 1), converted to a rate.
  Timestamps are local UK time (first reading lands at the same clock hour on both sides of
  the Oct 30 2016 clock change) and are rounded to the nearest half hour.
* The Hague timestamps are UTC, converted to Europe/Amsterdam.
* The Hague: rows flagged `has_error`, rows with the placeholder capacity 9999, and any rate
  below -5% or above 105% are dropped as broken (decided with the team, about 4.9% of rows).
  Small overshoots (between -5% and 0%, or 100% and 105%) are clipped to 0..1.
* Training uses at most 6,000 randomly sampled rows per lot (fixed seed) so three years of
  Hague data do not drown out Birmingham.

## Backtest results (from an actual run; every number is in outputs/)

MAE = mean absolute error in occupancy rate (0.10 = off by 10% of the lot). Baseline = the
average rate for each hour and day of week, computed only from training data. Flat baseline,
RMSE and raw (unadjusted) coverage are in `outputs/backtest_results.csv`.

| Test | Held out | Model MAE (rate) | Baseline MAE | Improvement | Band coverage (target 80%) | Model MAE in cars, 52 / 85 / 80 / 27 space lot |
|---|---|---|---|---|---|---|
| T1 named holdouts (all 6 per city) | 12 named lots (see backtest_log.csv) | 0.235 | 0.203 | -15.5% | 81.8% | 12.2 / 20.0 / 18.8 / 6.3 |
| T1 full leave one lot out | each lot in turn | 0.232 | 0.219 | -5.8% | 85.4% | 12.0 / 19.7 / 18.5 / 6.3 |
| T2 train birmingham -> test hague | all hague lots | 0.305 | 0.306 | +0.3% | 27.4% | 15.9 / 25.9 / 24.4 / 8.2 |
| T2 train hague -> test birmingham | all birmingham lots | 0.219 | 0.217 | -1.2% | 97.7% | 11.4 / 18.6 / 17.5 / 5.9 |
| T3 70/30 time split birmingham (all lots) | dates 2016-11-27..2016-12-19 | 0.217 | 0.207 | -5.3% | 66.3% | 11.3 / 18.5 / 17.4 / 5.9 |
| T3 70/30 time split birmingham (holdout lots removed (new lot AND new time)) | dates 2016-11-27..2016-12-19 | 0.188 | 0.236 | +20.5% | 82.4% | 9.8 / 15.9 / 15.0 / 5.1 |
| T3 70/30 time split hague (all lots) | dates 2024-09-15..2025-10-01 | 0.229 | 0.220 | -4.0% | 87.4% | 11.9 / 19.5 / 18.3 / 6.2 |
| T3 70/30 time split hague (holdout lots removed (new lot AND new time)) | dates 2024-09-15..2025-10-01 | 0.236 | 0.207 | -14.2% | 62.6% | 12.3 / 20.1 / 18.9 / 6.4 |
| T3 rolling weekly birmingham (all lots) | 8 weekly steps | 0.215 | 0.197 | -8.8% | 65.4% | 11.2 / 18.2 / 17.2 / 5.8 |
| T3 rolling weekly birmingham (holdout lots removed) | 8 weekly steps | 0.173 | 0.208 | +17.0% | 86.5% | 9.0 / 14.7 / 13.8 / 4.7 |
| T3 rolling weekly hague (all lots) | 12 weekly steps | 0.243 | 0.246 | +1.2% | 88.0% | 12.7 / 20.7 / 19.5 / 6.6 |
| T3 rolling weekly hague (holdout lots removed) | 12 weekly steps | 0.260 | 0.236 | -10.0% | 61.5% | 13.5 / 22.1 / 20.8 / 7.0 |
| T4 campus blend, weekday 8:00 to 16:30 | named holdout lots | 0.304 | 0.209 | -46.0% | 63.1% | 15.8 / 25.9 / 24.4 / 8.2 |
| T4 off hours (before 8:00, after 16:30) | named holdout lots | 0.230 | 0.191 |  | 46.3% | 11.9 / 19.5 / 18.4 / 6.2 |

**What matters most for Duke: campus like lots (full leave one lot out, grouped by similarity)**

| Lots by campus similarity | Lots | Model MAE | Baseline MAE | Improvement | Lots where model is worse |
|---|---|---|---|---|---|
| low (<0.3) | 27 | 0.272 | 0.209 | -30.0% | 23 |
| mid (0.3 to 0.8) | 10 | 0.248 | 0.231 | -7.5% | 5 |
| high (>=0.8) | 18 | 0.129 | 0.231 | +43.9% | 2 |

The model beats the baseline by a wide margin on lots shaped like a campus employee lot, and
loses on lots that are not (event venues, shopping, weekend heavy car parks). That is the
trade the campus weighting makes on purpose. Removing the weighting brings the overall
number level with the baseline but makes the most campus like Birmingham lots far worse
(`outputs/diag_weighting_ablation.csv`).

**Test 4: campus template blend**

| Subset (weekday 8:00 to 16:30) | Model alone | Template alone | Blend (w=0.3) | Baseline |
|---|---|---|---|---|
| all named holdouts | 0.272 | 0.413 | 0.304 | 0.209 |
| birmingham: most campus like | 0.140 | 0.085 | 0.099 | 0.367 |
| birmingham: least campus like | 0.276 | 0.439 | 0.323 | 0.090 |
| birmingham: random mid (seed 42) | 0.103 | 0.165 | 0.104 | 0.258 |
| hague: most campus like | 0.164 | 0.335 | 0.203 | 0.172 |
| hague: least campus like | 0.389 | 0.556 | 0.435 | 0.239 |
| hague: random mid (seed 42) | 0.250 | 0.364 | 0.272 | 0.195 |

Across all holdouts the blend does **not** beat the model alone. On the most campus like
Birmingham lots the template alone wins and the lowest error weight is about w = 0.7. On the
most campus like Hague lots the model alone wins (those lots have a campus shape but only
fill to about 25%). Because those two disagree and both were measured on test rows, w stays
at 0.3 until real Duke counts can pick it (`outputs/diag_blend_sweep_campus_like.csv`).
Hours before 8:00 and after 16:30 cannot be backtested on Birmingham (no data); on the holdout
lots (almost all Hague rows, plus a few Birmingham 7:30 readings) the model alone (MAE 0.206)
beat the template (0.231) and the blend (0.230).

Lots where the model is worse than the baseline are listed in
`outputs/test1_full_lolo_per_lot.csv` (column WORSE_THAN_BASELINE) and
`outputs/backtest_per_lot.csv`. Errors by time block: `outputs/errors_by_time_block.csv`.
Seeds, date ranges and held out lot IDs: `outputs/backtest_log.csv`.


## Known limitations

* **Domain gap.** Birmingham and The Hague are public, mostly paid city car parks. Duke
  lots are gated employee permit lots. Their daily rhythm may be very different.
* **Assumption driven hours.** Before 8:00 and after 16:30 the estimate is 95% our campus
  template. Birmingham has no readings at those hours at all. The Hague does, but they are
  city car parks, not campus lots.
* **Campus multipliers are guesses.** The template curve, the 0.5 break multiplier, the
  weekend multiplier and the event multiplier were not measured anywhere.
* **No real Duke validation yet.** Every error number here comes from public lots. The
  first real check happens when sensor or manual counts arrive (`calibrate`).
* **One curve for all four Duke lots.** The model has no lot identity, so before calibration
  all four lots get the same rate; only the car counts differ by capacity.
* **Similarity measures shape, not level.** A lot that peaks at 25% full can still score 0.99.
* **Old data.** Birmingham is from 2016.
* **Open-Meteo** is free for non commercial use only. A paid plan is required if Park Shark
  becomes a commercial product.
* **Birmingham licence ambiguity.** The UCI page lists CC BY 4.0, but its description says UK
  Open Government Licence. Check the original at data.birmingham.gov.uk before commercial use.
* The Duke holiday list in `config.toml` (which US holidays thin the lots) is an assumption;
  check it against Duke HR's official holiday schedule.

## Attribution

* Birmingham: Stolfi, D. (2017). Parking Birmingham [Dataset]. UCI Machine Learning
  Repository. https://doi.org/10.24432/C51K5Z (licence listed as CC BY 4.0 and as UK OGL).
* The Hague: Argaleo B.V., EMERALDS project, DT02 UC1 Parking Data (RDW Open Data), V0.1.
  https://doi.org/10.5281/zenodo.17657881, CC BY 4.0.
* Weather: Open-Meteo.com, historical and forecast APIs, CC BY 4.0.

## Live sensor inputs and Research Drive lots (added Nov 7, 2026)

What changed:

1. **Two Research Drive lots** in `config.toml`: Bryan Research Garage (163) and Research Drive
   Garage Reserved (239), capacities from parking.duke.edu. Every lot now has a `lot_type` and
   an `area`. Four **sensor groups** (Germanic Languages, OIT Telecom, Steam Plant curb, Duke
   Clinic corner) are listed with placeholder spot counts (ASSUMPTION until the leader confirms).
2. **Live inputs** built from the hardware team's sensor feed: `live_nearby_rate`,
   `live_vs_expected`, `live_trend_30min`, `cars_passed_15min`, `sensors_reporting`.
   Feed format (one row per sensor message): `timestamp, sensor_id, group, kind, value`, where
   kind is `occupancy` (0 or 1) or `vehicle_count` (cars since the last message).
3. **Lower template weight with live data**: `weight_model_hours_live = 0.15` and
   `weight_assumption_hours_live = 0.6` replace 0.3 and 0.95 for lots whose area has live data.

How live data is used today: the public training data has no live columns, so the AI cannot
learn from them yet. Until it can, a transparent rule (`live_adjust`, numbers in `[live]` in
config.toml, all ASSUMPTION) shifts the estimate by part of the "surprise" at the sensored
spots and narrows the band a little. `model_features()` switches the AI over to learning from
the live columns automatically as soon as training data contains them.

Known effect: outside 8:00 to 16:30 the band gets WIDER with live data, because a lower
template weight means more weight on the model, which is less sure than the template pretends
to be. Set `weight_assumption_hours_live = 0.95` to switch that part off.

Commands:

```
python3 park_shark.py example-feed                               # FAKE feed for testing
python3 park_shark.py live sensor_data/EXAMPLE_sensor_feed_FAKE.csv "2026-11-10 09:00"
python3 park_shark.py predict "Bryan Research Garage" "2026-11-10 09:00" sensor_data/EXAMPLE_sensor_feed_FAKE.csv
```

`live` prints every lot without and with live data. It shows how the estimate changes, not
whether it got more accurate; that needs real counts at the target lots. Save the real feed as
`sensor_data/sensor_feed.csv` and the traffic model (duke_traffic/) picks up Research Dr counts too.

## Update, later Nov 7, 2026: the model now predicts only the four sensored spot groups

The team lead confirmed which spots get sensors, and those are now the model's ONLY targets:

| Lot (config name) | Spots | Type |
|---|---|---|
| Germanic Languages spots | 5 | department_lot |
| OIT Telecom spots | 8 | department_lot |
| Steam Plant curb (Research Dr) | 8 | curb |
| Duke Clinic corner (Research Dr) | 10 | clinic_curb |

The six earlier lots moved to `[[duke.reference_lots]]` in config.toml (kept, not predicted).
Sensors ping about every 30 seconds and on every change, so a sensor silent for 2 minutes
(`max_age_minutes = 2`) counts as down.

How a prediction works now:
* **Right now, sensors working** (at least half of a lot's spots reported in the last 2 min):
  the answer is the measurement. Only silent spots are estimated. `driven_by = SENSORS`.
* **Right now, a lot's own sensors down**: estimated from the model, nudged by what the other
  three groups show (the `live_adjust` rule).
* **Future hours** (the 7 day forecast): the model and template, as before.
* **After about 5 days of sensor data**: `python3 park_shark.py feed-to-counts sensor_data/sensor_feed.csv`
  then `python3 park_shark.py calibrate sensor_data/hourly_counts.csv`. This fits each lot's
  scale and offset to its own real readings, so forecasts start reflecting these exact spots.
