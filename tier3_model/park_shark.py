"""
park_shark.py: Tier 3 "model only" parking occupancy estimator.

WHAT THIS FILE DOES (read top to bottom, it follows the project steps)
  Step 2  load and clean the public parking datasets
  Step 3  turn them into one common hourly table
  Step 4  add features (time of day, weather, holidays)
  Step 5  campus tuning: similarity weights (A) and template blend (B)
  Step 6  four honest backtests plus baselines
  Step 7  uncertainty band (10th to 90th percentile) and its coverage
  Step 8  predict(), 7 day Duke forecast, charts, saved model
  Step 9  calibrate() hook for real Duke counts later

HOW TO RUN
  python3 park_shark.py run                 # Steps 2 to 8 end to end
  python3 park_shark.py calibrate my.csv    # Step 9 with real Duke counts
  python3 park_shark.py example-feed        # write a FAKE sensor feed for testing
  python3 park_shark.py live feed.csv "2026-11-10 09:00"   # every lot, without vs with live data
  python3 park_shark.py predict "OIT Telecom spots" "2026-11-10 09:00" feed.csv
  python3 park_shark.py feed-to-counts feed.csv   # hourly counts, then: calibrate sensor_data/hourly_counts.csv
  python3 park_shark.py export-app path/to/parkshark/data/tier3_forecast.json   # forecast for the team dashboard
  python3 park_shark.py nearby "2026-11-10 09:00" [feed.csv]   # lots WITHOUT sensors, low confidence

KEY IDEA: we predict an OCCUPANCY RATE (0 = empty, 1 = full), not a car
count. A rate means the same thing for a 27 space lot and a 2,000 space
lot, so one model can learn from big public car parks and still be used
on small Duke lots. We multiply by capacity at the very end to get cars.
"""
import json
import math
import os
import sys
try:
    import tomllib            # Python 3.11+
except ModuleNotFoundError:   # Python 3.10: same reader, installed as "tomli"
    import tomli as tomllib
import warnings
import zipfile
from pathlib import Path

import holidays
import joblib
import matplotlib
matplotlib.use("Agg")  # draw charts to files, no screen needed
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

warnings.filterwarnings("ignore", category=FutureWarning)

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("PARK_SHARK_DATA_DIR", ROOT / "data"))
OUT = Path(os.environ.get("PARK_SHARK_OUT_DIR", ROOT / "outputs"))
with open(ROOT / "config.toml", "rb") as _f:
    CFG = tomllib.load(_f)

SEED = CFG["training"]["random_seed"]
DUKE_CAPACITIES = {lot["name"]: lot["capacity"] for lot in CFG["duke"]["lots"]}
CAR_CAPS = [52, 85, 80, 27]  # used to translate rate errors into cars

# The columns the model is allowed to learn from. Every one of these is
# something we can also know for Duke at prediction time.
FEATURES = ["hour", "dow", "is_weekend", "hour_sin", "hour_cos",
            "temperature_2m", "precipitation", "snowfall", "weather_code",
            "is_holiday"]
# Placeholders: unknown (always 0) in public data, so the model must NOT
# see them. They are only used by the campus adjustment in Step 5B.
PLACEHOLDERS = ["class_in_session", "event_flag"]
assert not set(FEATURES) & set(PLACEHOLDERS), "placeholders leaked into FEATURES"

# Lot facts from config.toml. lot_type and area were added Nov 2026. The AI has no
# lot identity yet, so today they decide which live sensors a lot listens to. They are
# also written into every output so a future model trained on Duke data can learn from them.
LOT_INFO = {lot["name"]: {"capacity": lot["capacity"], "lot_type": lot.get("lot_type", "unknown"),
                          "area": lot.get("area", "unknown"), "sensor_group": lot.get("sensor_group")}
            for lot in CFG["duke"]["lots"]}
SENSOR_GROUPS = {g["name"]: g for g in CFG["duke"].get("sensor_groups", [])}
LIVE_CFG = CFG.get("live", {})

# Live sensor inputs, built from the hardware team's sensor feed (see load_sensor_feed).
# The public training data (Birmingham, The Hague) has no such columns, so the AI can
# only LEARN from them once training data that contains them exists (simulated
# neighbours, or real Duke history). model_features() adds them to the model
# automatically the moment such data is present. Until then live_adjust() applies them.
LIVE_FEATURES = ["live_nearby_rate", "live_vs_expected", "live_trend_30min",
                 "cars_passed_15min", "sensors_reporting"]


def model_features(df, min_share=0.2):
    """Columns the AI trains on: the fixed FEATURES, plus any live input that is
    filled in for at least 20% of the training rows."""
    feats = list(FEATURES)
    for f in LIVE_FEATURES:
        if f in df.columns and df[f].notna().mean() >= min_share:
            feats.append(f)
    assert not set(feats) & set(PLACEHOLDERS), "placeholders leaked into the model"
    return feats

TIME_BLOCKS = [("overnight", 0, 6), ("morning ramp", 7, 9), ("midday", 10, 14),
               ("afternoon", 15, 17), ("evening", 18, 23)]


def log(msg=""):
    print(msg, flush=True)


def section(title):
    log("\n" + "=" * 72 + f"\n{title}\n" + "=" * 72)


# =====================================================================
# STEP 2: LOAD, INSPECT, CLEAN
# =====================================================================
def inspect(df, name, time_col, lot_col):
    """Print the basic facts about a raw table before we change anything."""
    log(f"\n--- {name}: raw inspection ---")
    log(f"rows: {len(df):,}")
    log(f"columns: {list(df.columns)}")
    t = pd.to_datetime(df[time_col], errors="coerce")
    log(f"date range: {t.min()}  to  {t.max()}")
    log(f"number of lots: {df[lot_col].nunique()}")
    log("missing values per column:")
    log(df.isna().sum().to_string())
    log("5 sample rows:")
    log(df.sample(5, random_state=SEED).to_string())


def clean_steps(df, name):
    """Shared cleaning. Each step reports how many rows it removed.
    df needs: lot_id, ts_local (time zone aware), capacity, occupancy_rate."""
    log(f"\n--- {name}: cleaning ---")
    n0 = len(df)
    df = df.dropna(subset=["lot_id", "ts_local", "capacity", "occupancy_rate"])
    log(f"drop rows with a missing key value:      -{n0 - len(df):,}")
    n0 = len(df)
    df = df.drop_duplicates()
    log(f"drop exact duplicate rows:               -{n0 - len(df):,}")
    n0 = len(df)
    df = df.drop_duplicates(subset=["lot_id", "ts_local"], keep="last")
    log(f"drop repeat readings (same lot and time):-{n0 - len(df):,}")
    n0 = len(df)
    df = df[df["capacity"] > 0]
    log(f"drop rows with capacity <= 0:            -{n0 - len(df):,}")
    lo_bad, hi_bad = CFG["cleaning"]["invalid_below"], CFG["cleaning"]["invalid_above"]
    n0 = len(df)
    df = df[(df["occupancy_rate"] >= lo_bad) & (df["occupancy_rate"] <= hi_bad)]
    log(f"drop impossible rates (< {lo_bad:.0%} or > {hi_bad:.0%}):   -{n0 - len(df):,}")
    below = (df["occupancy_rate"] < 0).sum()
    above = (df["occupancy_rate"] > 1).sum()
    df = df.assign(occupancy_rate=df["occupancy_rate"].clip(0, 1))
    log(f"clip small overshoots to 0..1 (rows kept, values changed): {below:,} below 0, {above:,} above 1")
    return df


def flag_dead_lots(df, name):
    """A lot that reads 0 (or one constant value) most of the time is almost
    certainly a broken sensor, not an empty car park. Training on it would
    teach the model that lots are empty at noon. Controlled by config."""
    cfgc = CFG.get("cleaning", {})
    thr = cfgc.get("dead_lot_zero_share", 0.5)
    stats = df.groupby("lot_id")["occupancy_rate"].agg(
        zero_share=lambda s: (s <= 0.001).mean(), std="std", n="size")
    dead = stats[(stats["zero_share"] >= thr) | (stats["std"] < 0.005)]
    if len(dead):
        log(f"\nPossible broken sensors in {name} (>= {thr:.0%} zero readings or flat line):")
        log(dead.round(3).to_string())
        if cfgc.get("drop_dead_lots", True):
            n0 = len(df)
            df = df[~df["lot_id"].isin(dead.index)]
            log(f"DROPPED these lots (config cleaning.drop_dead_lots = true): -{n0 - len(df):,} rows")
        else:
            log("Kept them (config cleaning.drop_dead_lots = false).")
    else:
        log(f"\nNo broken sensor lots found in {name}.")
    return df


def load_birmingham():
    folder = DATA / "raw" / "birmingham"
    zpath = folder / "parking_birmingham.zip"
    if zpath.exists():
        with zipfile.ZipFile(zpath) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".csv")]
            log(f"zip contains: {z.namelist()}")
            with z.open(names[0]) as f:
                raw = pd.read_csv(f)
    elif list(folder.glob("*.csv")):
        raw = pd.read_csv(sorted(folder.glob("*.csv"))[0])
    else:
        log("Birmingham data not found.")
        return None

    section("STEP 2A: Birmingham (UCI Parking Birmingham)")
    inspect(raw, "Birmingham", "LastUpdated", "SystemCodeNumber")

    # Is "Occupancy" a car count or already a rate? Check, do not assume.
    occ, cap = raw["Occupancy"], raw["Capacity"]
    log("\nIs Occupancy a count or a rate?")
    log(f"  Occupancy min / median / max: {occ.min()} / {occ.median()} / {occ.max()}")
    log(f"  Capacity  min / median / max: {cap.min()} / {cap.median()} / {cap.max()}")
    log(f"  share of rows with Occupancy > 1: {(occ > 1).mean():.1%}")
    log(f"  share of rows with Occupancy > Capacity: {(occ > cap).mean():.2%}")
    log(f"  share of rows with Occupancy < 0: {(occ < 0).mean():.2%}")
    is_count = (occ > 1).mean() > 0.5
    log(f"  VERDICT: Occupancy is a {'CAR COUNT' if is_count else 'RATE'}"
        + (" -> rate = Occupancy / Capacity" if is_count else ""))

    df = pd.DataFrame({
        "lot_id": raw["SystemCodeNumber"].astype(str),
        "ts_naive": pd.to_datetime(raw["LastUpdated"], errors="coerce"),
        "capacity": raw["Capacity"].astype(float),
        "occupancy_rate": (raw["Occupancy"] / raw["Capacity"]) if is_count else raw["Occupancy"],
    })
    # The file has no time zone. Readings start near 8:00 every day on both
    # sides of the Oct 30 2016 clock change, which is what a LOCAL clock
    # schedule looks like, so we treat the stamps as UK local time.
    before = df[df.ts_naive < "2016-10-30"].ts_naive
    after = df[df.ts_naive >= "2016-10-31"].ts_naive
    if len(before) and len(after):
        first = lambda s: s.groupby(s.dt.date).min().dt.hour.median()
        log(f"\nTime zone check: typical first reading hour before clock change = "
            f"{first(before):.0f}, after = {first(after):.0f}. Same hour -> local time.")
    tz = CFG["places"]["birmingham"]["timezone"]
    # Readings are stamped like 7:59:42 or 8:26:10. They belong to the 8:00 and
    # 8:30 slots, so we round to the nearest half hour before anything else.
    df["ts_naive"] = df["ts_naive"].dt.round("30min")
    df["ts_local"] = df["ts_naive"].dt.tz_localize(tz, ambiguous="NaT", nonexistent="NaT")
    df = df.drop(columns="ts_naive")
    df = clean_steps(df, "Birmingham")
    df = flag_dead_lots(df, "Birmingham")
    df["source"] = "birmingham"
    log(f"\nBirmingham after cleaning: {len(df):,} rows, {df.lot_id.nunique()} lots")
    log("hours of day present: " + str(sorted(int(x) for x in df.ts_local.dt.hour.dropna().unique())))
    return df


def load_hague():
    """The Hague (EMERALDS). Column names come from the metadata file,
    which we read first (see read_hague_metadata)."""
    folder = DATA / "raw" / "hague"
    csv = folder / "hague_parking.csv"
    if not csv.exists():
        log("\nThe Hague data NOT loaded (file missing). THE MODEL IS BIRMINGHAM ONLY.")
        return None
    hcfg = CFG.get("hague_columns")
    if not hcfg:
        log("\nThe Hague file exists but config [hague_columns] is not set yet. "
            "Read the metadata first. THE MODEL IS BIRMINGHAM ONLY for this run.")
        return None
    section("STEP 2B: The Hague (EMERALDS, Zenodo)")
    usecols = [hcfg["time"], hcfg["lot"], hcfg["capacity"]] + \
              [c for c in [hcfg.get("occupied"), hcfg.get("vacant"), hcfg.get("rate"),
                           hcfg.get("error_flag")] if c]
    read_kw = dict(sep=hcfg.get("sep", ","), usecols=usecols)
    # Read in 1 million row chunks so a 235 MB file never sits in memory twice.
    chunks = []
    for ch in pd.read_csv(csv, chunksize=1_000_000, **read_kw):
        chunks.append(ch)
    raw = pd.concat(chunks, ignore_index=True)
    inspect(raw, "The Hague", hcfg["time"], hcfg["lot"])

    log("\nHague specific cleaning (decided with Jacob: drop broken rows, clip only small overshoots):")
    if hcfg.get("error_flag"):
        n0 = len(raw)
        raw = raw[~raw[hcfg["error_flag"]].astype(bool)]
        log(f"drop rows flagged has_error = true:      -{n0 - len(raw):,}")
    if hcfg.get("capacity_placeholder"):
        n0 = len(raw)
        raw = raw[raw[hcfg["capacity"]] != hcfg["capacity_placeholder"]]
        log(f"drop rows with placeholder capacity {hcfg['capacity_placeholder']}: -{n0 - len(raw):,}")
    cap = pd.to_numeric(raw[hcfg["capacity"]], errors="coerce")
    if hcfg.get("occupied"):
        rate = pd.to_numeric(raw[hcfg["occupied"]], errors="coerce") / cap
    elif hcfg.get("vacant"):
        rate = 1 - pd.to_numeric(raw[hcfg["vacant"]], errors="coerce") / cap
    else:
        rate = pd.to_numeric(raw[hcfg["rate"]], errors="coerce") / hcfg.get("rate_scale", 1.0)
    ts = pd.to_datetime(raw[hcfg["time"]], errors="coerce", utc=hcfg.get("time_is_utc", False), format="ISO8601")
    tz = CFG["places"]["hague"]["timezone"]
    if hcfg.get("time_is_utc", False):
        ts_local = ts.dt.tz_convert(tz)
    else:
        ts_local = ts.dt.tz_localize(tz, ambiguous="NaT", nonexistent="NaT")
    # The IDs are long codes; the first 8 characters are unique and easier to read.
    short = raw[hcfg["lot"]].astype(str).str[:8]
    assert short.nunique() == raw[hcfg["lot"]].nunique(), "short Hague IDs collide"
    df = pd.DataFrame({"lot_id": short, "ts_local": ts_local,
                       "capacity": cap, "occupancy_rate": rate})
    log(f"time zone: stamps are UTC, converted to {tz} (so 8am means 8am local)")
    df = clean_steps(df, "The Hague")
    df = flag_dead_lots(df, "The Hague")
    df["source"] = "hague"
    log(f"\nThe Hague after cleaning: {len(df):,} rows, {df.lot_id.nunique()} lots")
    return df


# =====================================================================
# STEP 3: ONE COMMON HOURLY TABLE
# =====================================================================
def to_hourly(df):
    """One row per lot per hour. If a lot reported twice in an hour
    (for example 8:00 and 8:30) we average the two readings."""
    d = df.copy()
    # Floor to the hour in UTC, then convert back: identical to a local floor
    # (offsets are whole hours) but never trips over the clock change.
    parts = []
    for src, g in d.groupby("source"):
        tz = g["ts_local"].dt.tz
        parts.append(g.assign(timestamp_local=g["ts_local"].dt.tz_convert("UTC").dt.floor("h").dt.tz_convert(tz)))
    d = pd.concat(parts).dropna(subset=["timestamp_local"])
    h = (d.groupby(["source", "lot_id", "timestamp_local"], as_index=False)
           .agg(capacity=("capacity", "median"), occupancy_rate=("occupancy_rate", "mean"),
                n_readings=("occupancy_rate", "size")))
    h["lot_key"] = h["source"] + ":" + h["lot_id"]
    return h


# =====================================================================
# STEP 4: FEATURES
# =====================================================================
def weather_cache_path(place, start, end):
    return DATA / "cache" / f"weather_{place}_{start}_{end}.json"


def load_weather_json(path):
    with open(path) as f:
        j = json.load(f)
    w = pd.DataFrame(j["hourly"])
    w["key"] = pd.to_datetime(w["time"])  # local wall clock time (we asked for timezone=)
    w = w.drop(columns="time").drop_duplicates("key")  # DST fall back repeats one hour
    return w


def load_weather(place, start, end):
    p = weather_cache_path(place, start, end)
    if not p.exists():  # any cached file whose date range covers what we need is fine
        for f in sorted((DATA / "cache").glob(f"weather_{place}_*.json")):
            a, b = f.stem.split("_")[-2:]
            if a <= start and b >= end:
                p = f
                break
    if not p.exists():
        raise FileNotFoundError(
            f"Weather cache missing: {p}\nRun:  python3 fetch_data.py weather {place} {start} {end}")
    return load_weather_json(p)


def add_time_features(h):
    ts = h["timestamp_local"]
    h["hour"] = ts.dt.hour
    h["dow"] = ts.dt.dayofweek            # 0 = Monday, 6 = Sunday
    h["is_weekend"] = (h["dow"] >= 5).astype(int)
    # sine and cosine of the hour put the 24 hour clock on a circle, so the
    # model knows 23:00 and 00:00 are neighbours (as numbers 23 and 0 they
    # look far apart).
    h["hour_sin"] = np.sin(2 * np.pi * h["hour"] / 24)
    h["hour_cos"] = np.cos(2 * np.pi * h["hour"] / 24)
    h["date"] = ts.dt.date
    return h


def holiday_dates(country, years):
    return set(holidays.country_holidays(country, years=list(years)).keys())


def add_features(h, place):
    """Time, weather, holiday and placeholder columns for one place."""
    h = add_time_features(h.copy())
    pcfg = CFG["places"][place] if place in CFG["places"] else CFG["duke"]
    start = h["timestamp_local"].min().strftime("%Y-%m-%d")
    end = h["timestamp_local"].max().strftime("%Y-%m-%d")
    w = load_weather(place, start, end)
    h["key"] = h["timestamp_local"].dt.tz_localize(None)
    h = h.merge(w, on="key", how="left").drop(columns="key")
    missing = h["temperature_2m"].isna().mean()
    log(f"[{place}] weather matched for {1 - missing:.1%} of hours")
    years = range(h["timestamp_local"].dt.year.min(), h["timestamp_local"].dt.year.max() + 1)
    hol = holiday_dates(pcfg["holiday_country"], years)
    h["is_holiday"] = h["date"].isin(hol).astype(int)
    log(f"[{place}] public holiday days in data: {sorted({d for d in h['date'].unique() if d in hol})}")
    # Placeholders. Unknown for public data -> 0, and NOT in FEATURES.
    h["class_in_session"] = 0
    h["event_flag"] = 0
    return h


def time_block(hour):
    for name, a, b in TIME_BLOCKS:
        if a <= hour <= b:
            return name
    return "?"


# =====================================================================
# STEP 5: CAMPUS TUNING
# =====================================================================
CA = CFG["campus_assumptions"]
TEMPLATE_WD = np.array(CA["template_weekday"], dtype=float)
assert len(TEMPLATE_WD) == 24, "template_weekday needs 24 hourly values"
TEMPLATE_WE = np.maximum(TEMPLATE_WD * CA["weekend_multiplier"], CA["weekend_floor"])


def template_value(hour, is_weekend):
    hour = np.asarray(hour, dtype=int)
    is_weekend = np.asarray(is_weekend, dtype=bool)
    return np.where(is_weekend, TEMPLATE_WE[hour], TEMPLATE_WD[hour])


def blend_weight(hour, cfg_blend=None, live=False):
    """Weight w on the template. High where there is no public data.
    live=True (live sensor data present for this lot's area) switches to the lower
    *_live weights in config.toml: real readings now do part of the template's job."""
    b = cfg_blend or CFG["blend"]
    hour = np.asarray(hour)
    model_hours = (hour >= b["model_hours_start"]) & (hour <= b["model_hours_end"])
    w = np.where(model_hours, b["weight_model_hours"], b["weight_assumption_hours"])
    if np.any(live):
        w_live = np.where(model_hours, b.get("weight_model_hours_live", b["weight_model_hours"]),
                          b.get("weight_assumption_hours_live", b["weight_assumption_hours"]))
        w = np.where(np.asarray(live, dtype=bool), w_live, w)
    return w


def similarity_scores(train):
    """STEP 5A. For each lot: average weekday and weekend curve by hour,
    then correlation with the campus template at the same hours.
    Uses ONLY the rows passed in (the training table), so held out lots
    never influence the weights."""
    rows = []
    for lot, g in train.groupby("lot_key"):
        wd = g[g.is_weekend == 0].groupby("hour")["occupancy_rate"].mean()
        we = g[g.is_weekend == 1].groupby("hour")["occupancy_rate"].mean()
        obs = np.r_[wd.values, we.values]
        tmp = np.r_[TEMPLATE_WD[wd.index.values], TEMPLATE_WE[we.index.values]]
        corr = np.corrcoef(obs, tmp)[0, 1] if len(obs) >= 3 and obs.std() > 0 else np.nan
        wd_corr = (np.corrcoef(wd.values, TEMPLATE_WD[wd.index.values])[0, 1]
                   if len(wd) >= 3 and wd.std() > 0 else np.nan)
        rows.append({"lot_key": lot, "source": g.source.iloc[0], "similarity": corr,
                     "weekday_shape_corr": wd_corr,
                     "weekday_mean": wd.mean(), "weekend_mean": we.mean() if len(we) else np.nan})
    s = pd.DataFrame(rows)
    floor = CFG["training"]["min_sample_weight"]
    s["sample_weight"] = s["similarity"].clip(lower=floor, upper=1.0).fillna(floor)
    return s.sort_values("similarity", ascending=False).reset_index(drop=True)


def campus_estimate(q10, q50, q90, hour, is_weekend, closed_holiday=0,
                    class_in_session=1, event_flag=0, blend_cfg=None, live=False):
    """STEP 5B. estimate = w * template + (1 - w) * model, then the campus
    multipliers. Returns median, low, high (rates) and the weight w."""
    w = blend_weight(hour, blend_cfg, live)
    t = template_value(hour, is_weekend)
    extra = CFG["uncertainty"]["assumption_band_extra"] * w
    med = w * t + (1 - w) * q50
    lo = w * t + (1 - w) * q10 - extra
    hi = w * t + (1 - w) * q90 + extra
    mult = np.where((np.asarray(closed_holiday) == 1) | (np.asarray(class_in_session) == 0),
                    CA["break_holiday_multiplier"], CA["class_in_session_multiplier"])
    mult = mult * np.where(np.asarray(event_flag) == 1, CA["event_multiplier"], 1.0)
    floor = np.where(np.asarray(is_weekend, dtype=bool), CA["weekend_floor"], CA["overnight_floor"])
    med = np.clip(med * mult, floor, 1)
    lo = np.clip(lo * mult, 0, med)
    hi = np.clip(hi * mult, med, 1)
    return med, lo, hi, w


# =====================================================================
# MODEL FITTING (used by every backtest and by the final model)
# =====================================================================
def _new_model(alpha):
    t = CFG["training"]
    return HistGradientBoostingRegressor(
        loss="quantile", quantile=alpha, max_iter=t["max_iter"],
        learning_rate=t["learning_rate"], max_leaf_nodes=t["max_leaf_nodes"],
        min_samples_leaf=t["min_samples_leaf"], random_state=SEED)


def _fit_quantiles(df, weights, feats=None):
    X = df[feats or FEATURES]
    return {a: _new_model(a).fit(X, df["occupancy_rate"], sample_weight=weights)
            for a in CFG["uncertainty"]["quantiles"]}


def _raw_predict(models, df, feats=None):
    X = df[feats or FEATURES]
    q = {a: m.predict(X) for a, m in models.items()}
    med = np.clip(q[0.5], 0, 1)
    lo = np.clip(np.minimum(q[0.1], med), 0, 1)   # quantile lines can cross,
    hi = np.clip(np.maximum(q[0.9], med), 0, 1)   # so force low <= median <= high
    return lo, med, hi


def _coverage(y, lo, hi):
    return float(np.mean((y >= lo) & (y <= hi)))


def fit_bundle(train, calibrate_band=True):
    """Fit the 10/50/90 quantile models on `train` ONLY.
    Everything (similarity weights, band scale) is computed from `train`,
    which is how we guarantee no leakage from the held out data."""
    sims = similarity_scores(train)   # weights use ALL training rows
    cap_rows = CFG["training"].get("max_rows_per_lot")
    if cap_rows:
        # Give every lot a similar number of rows (seeded sample), so 25
        # Hague lots with 3 years of data do not drown out Birmingham, and
        # training stays fast. Held out data is never part of `train`.
        train = (train.sample(frac=1.0, random_state=SEED)   # seeded shuffle
                      .groupby("lot_key").head(cap_rows))         # then keep up to cap_rows per lot
    wmap = dict(zip(sims.lot_key, sims.sample_weight))
    weights = train["lot_key"].map(wmap).values
    feats = model_features(train)   # FEATURES, plus live inputs if the training data has them

    band_scale = 1.0
    lots = np.array(sorted(train.lot_key.unique()))
    if calibrate_band and len(lots) >= 4:
        # Band width check WITHOUT touching held out data: hide a quarter of
        # the TRAINING lots, fit on the rest, and stretch or shrink the band
        # until it covers 80% of those hidden training lots.
        rng = np.random.default_rng(SEED)
        inner = set(rng.choice(lots, size=max(1, round(len(lots) * 0.25)), replace=False))
        m_in = train.lot_key.isin(inner).values
        inner_models = _fit_quantiles(train[~m_in], weights[~m_in], feats)
        lo, med, hi = _raw_predict(inner_models, train[m_in], feats)
        y = train.loc[m_in, "occupancy_rate"].values
        target = CFG["uncertainty"]["target_coverage"]
        grid = np.arange(0.5, 4.001, 0.05)
        covs = [_coverage(y, np.clip(med - s * (med - lo), 0, 1), np.clip(med + s * (hi - med), 0, 1))
                for s in grid]
        band_scale = float(grid[int(np.argmin(np.abs(np.array(covs) - target)))])

    models = _fit_quantiles(train, weights, feats)
    return {"models": models, "band_scale": band_scale, "similarity": sims,
            "train_lots": sorted(train.lot_key.unique()), "features": feats}


def predict_bundle(bundle, df):
    feats = bundle.get("features", FEATURES)
    missing = [f for f in feats if f not in df.columns]
    if missing:   # e.g. live inputs with no sensor data: blank (NaN), which the AI handles
        df = df.assign(**{f: np.nan for f in missing})
    lo, med, hi = _raw_predict(bundle["models"], df, feats)
    s = bundle["band_scale"]
    lo_adj = np.clip(med - s * (med - lo), 0, 1)
    hi_adj = np.clip(med + s * (hi - med), 0, 1)
    return {"pred": med, "lo_raw": lo, "hi_raw": hi, "lo": lo_adj, "hi": hi_adj}


# =====================================================================
# STEP 6: BACKTESTING HELPERS
# =====================================================================
def assert_no_leak(train, label, test_lots=None, test_dates=None):
    """Automatic leakage check. Stops the program if a held out lot or
    held out date is found anywhere in the training table."""
    if test_lots is not None:
        test_lots = sorted(set(test_lots))
        overlap = set(train.lot_key) & set(test_lots)
        shown = test_lots if len(test_lots) <= 12 else test_lots[:12] + [f"... ({len(test_lots)} total)"]
        log(f"[leak check] {label}: held out lots {shown}")
        assert not overlap, f"LEAKAGE in {label}: {overlap} found in training table"
    if test_dates is not None:
        overlap = set(train.date) & set(test_dates)
        assert not overlap, f"LEAKAGE in {label}: {len(overlap)} held out dates in training"
        log(f"[leak check] {label}: train dates {min(train.date)}..{max(train.date)}, "
            f"test dates {min(test_dates)}..{max(test_dates)}, no overlap")
    log(f"[leak check] {label}: PASSED")


def baseline_hour_dow(train, test):
    """Baseline 1: average rate for each (hour, day of week) from TRAINING
    rows only. Falls back to hour only, then the flat average, when a
    combination never appears in training."""
    hd = train.groupby(["hour", "dow"])["occupancy_rate"].mean()
    hh = train.groupby("hour")["occupancy_rate"].mean()
    flat = train["occupancy_rate"].mean()
    idx = pd.MultiIndex.from_arrays([test["hour"], test["dow"]])
    b = pd.Series(hd.reindex(idx).values, index=test.index)
    b = b.fillna(test["hour"].map(hh)).fillna(flat)
    return b.values, flat


def run_eval(bundle, train, test):
    p = predict_bundle(bundle, test)
    base, flat = baseline_hour_dow(train, test)
    r = test[["source", "lot_key", "timestamp_local", "date", "hour", "dow", "is_weekend",
              "is_holiday", "occupancy_rate"]].copy()
    for k, v in p.items():
        r[k] = v
    r["base_hd"] = base
    r["base_flat"] = flat
    r["time_block"] = r["hour"].map(time_block)
    return r


def metrics(r, pred="pred", lo="lo", hi="hi"):
    y = r["occupancy_rate"].values
    e = r[pred].values - y
    eb = r["base_hd"].values - y
    ef = r["base_flat"].values - y
    out = {"n_rows": len(r),
           "model_MAE": np.mean(np.abs(e)), "model_RMSE": math.sqrt(np.mean(e ** 2)),
           "baseline_MAE": np.mean(np.abs(eb)), "baseline_RMSE": math.sqrt(np.mean(eb ** 2)),
           "flat_MAE": np.mean(np.abs(ef)), "flat_RMSE": math.sqrt(np.mean(ef ** 2))}
    out["improvement_pct"] = 100 * (out["baseline_MAE"] - out["model_MAE"]) / out["baseline_MAE"]
    if lo in r:
        out["coverage_raw"] = _coverage(y, r["lo_raw"].values, r["hi_raw"].values) if "lo_raw" in r else np.nan
        out["coverage_adjusted"] = _coverage(y, r[lo].values, r[hi].values)
    for c in CAR_CAPS:
        out[f"MAE_cars_cap{c}"] = out["model_MAE"] * c
    return out


RESULTS, PER_LOT, BLOCKS, LOG = [], [], [], []


def record(test, held_out, r, train, test_split, extra_log=None, seed=SEED):
    m = metrics(r)
    RESULTS.append({"test": test, "held_out": held_out, **m})
    for lot, g in r.groupby("lot_key"):
        PER_LOT.append({"test": test, "lot_key": lot, **{k: v for k, v in metrics(g).items()
                        if k in ("n_rows", "model_MAE", "baseline_MAE", "flat_MAE",
                                 "improvement_pct", "coverage_adjusted")}})
    for blk, g in r.groupby("time_block"):
        mm = metrics(g)
        BLOCKS.append({"test": test, "time_block": blk, "n_rows": mm["n_rows"],
                       "model_MAE": mm["model_MAE"], "baseline_MAE": mm["baseline_MAE"],
                       "coverage_adjusted": mm["coverage_adjusted"]})
    LOG.append({"test": test, "random_seed": seed,
                "train_date_range": f"{min(train.date)}..{max(train.date)}",
                "test_date_range": f"{min(r.date)}..{max(r.date)}",
                "n_train_lots": train.lot_key.nunique(), "n_test_lots": r.lot_key.nunique(),
                "held_out_lots": ";".join(sorted(r.lot_key.unique())) if test_split == "lots" else "",
                "split_type": test_split, **(extra_log or {})})
    log(f"  -> {test}: model MAE {m['model_MAE']:.4f}  baseline MAE {m['baseline_MAE']:.4f}  "
        f"flat MAE {m['flat_MAE']:.4f}  improvement {m['improvement_pct']:+.1f}%  "
        f"band coverage {m['coverage_adjusted']:.1%} (raw {m['coverage_raw']:.1%})")
    return m


def choose_holdouts(sims, source):
    """Test 1 named holdouts for one source: 2 most campus like,
    2 least campus like, 2 random from the middle (fixed seed)."""
    s = sims[sims.source == source].sort_values("similarity", ascending=False).reset_index(drop=True)
    if len(s) < 8:
        log(f"{source}: only {len(s)} lots, too few for 6 named holdouts")
        return {}
    top = list(s.lot_key[:2])
    bottom = list(s.lot_key[-2:])
    n = len(s)
    mid_pool = list(s.lot_key[n // 3: n - n // 3])
    mid_pool = [m for m in mid_pool if m not in top + bottom]
    rng = np.random.default_rng(SEED)
    mid = [str(x) for x in rng.choice(mid_pool, size=2, replace=False)]
    return {"most campus like": top, "least campus like": bottom, f"random mid (seed {SEED})": mid}


# =====================================================================
# STEP 6: THE FOUR TESTS
# =====================================================================
def test1_lot_holdouts(H, sims):
    section("TEST 1: Leave lots out (named holdouts, then full leave one lot out)")
    log("Every lot with its campus similarity score (computed on each lot's own data,\n"
        "used here ONLY to pick holdouts; training weights are recomputed without them):")
    log(sims[["lot_key", "similarity", "weekday_shape_corr", "weekday_mean",
              "weekend_mean", "sample_weight"]].round(3).to_string())
    groups = {}
    for src in sorted(H.source.unique()):
        g = choose_holdouts(sims, src)
        for k, v in g.items():
            groups[f"{src}: {k}"] = v
    log(f"\nNamed holdout lots (random seed {SEED}):")
    for k, v in groups.items():
        log(f"  {k}: {v}")
    holdouts = sorted({lot for v in groups.values() for lot in v})
    train = H[~H.lot_key.isin(holdouts)]
    test = H[H.lot_key.isin(holdouts)]
    assert_no_leak(train, "Test 1 named holdouts", test_lots=holdouts)
    b = fit_bundle(train)
    r = run_eval(b, train, test)
    record("T1 named holdouts (all 6 per city)", ";".join(holdouts), r, train, "lots",
           {"holdout_groups": json.dumps(groups)})
    log("\nPer lot error for the named holdouts:")
    rows = []
    for grp, lots in groups.items():
        for lot in lots:
            m = metrics(r[r.lot_key == lot])
            rows.append({"group": grp, "lot": lot, "model_MAE": m["model_MAE"],
                         "baseline_MAE": m["baseline_MAE"], "improvement_pct": m["improvement_pct"],
                         "coverage": m["coverage_adjusted"],
                         "WORSE_THAN_BASELINE": m["model_MAE"] > m["baseline_MAE"]})
    named = pd.DataFrame(rows)
    log(named.round(4).to_string())
    named.to_csv(OUT / "test1_named_holdouts.csv", index=False)

    # Full leave one lot out: every lot takes a turn
    log("\nFull leave one lot out (every lot held out once):")
    allr = []
    for lot in sorted(H.lot_key.unique()):
        tr = H[H.lot_key != lot]
        te = H[H.lot_key == lot]
        assert lot not in set(tr.lot_key), f"LEAKAGE: {lot}"
        bb = fit_bundle(tr)
        rr = run_eval(bb, tr, te)
        allr.append(rr)
        LOG.append({"test": "T1 full LOLO", "random_seed": SEED, "held_out_lots": lot,
                    "train_date_range": f"{min(tr.date)}..{max(tr.date)}",
                    "test_date_range": f"{min(te.date)}..{max(te.date)}", "split_type": "lots",
                    "n_train_lots": tr.lot_key.nunique(), "n_test_lots": 1})
    log("[leak check] full LOLO: each held out lot asserted absent from its training table: PASSED")
    R = pd.concat(allr)
    m = metrics(R)
    RESULTS.append({"test": "T1 full leave one lot out", "held_out": "each lot in turn", **m})
    per = pd.DataFrame([{"lot_key": k, **metrics(g)} for k, g in R.groupby("lot_key")])
    per["WORSE_THAN_BASELINE"] = per.model_MAE > per.baseline_MAE
    per = per.merge(sims[["lot_key", "similarity"]], on="lot_key").sort_values("model_MAE")
    per.to_csv(OUT / "test1_full_lolo_per_lot.csv", index=False)
    for _, x in per.iterrows():
        PER_LOT.append({"test": "T1 full LOLO", "lot_key": x.lot_key, "n_rows": x.n_rows,
                        "model_MAE": x.model_MAE, "baseline_MAE": x.baseline_MAE,
                        "flat_MAE": x.flat_MAE, "improvement_pct": x.improvement_pct,
                        "coverage_adjusted": x.coverage_adjusted})
    for blk, g in R.groupby("time_block"):
        mm = metrics(g)
        BLOCKS.append({"test": "T1 full LOLO", "time_block": blk, "n_rows": mm["n_rows"],
                       "model_MAE": mm["model_MAE"], "baseline_MAE": mm["baseline_MAE"],
                       "coverage_adjusted": mm["coverage_adjusted"]})
    log(per[["lot_key", "similarity", "model_MAE", "baseline_MAE", "improvement_pct",
             "coverage_adjusted", "WORSE_THAN_BASELINE"]].round(4).to_string())
    log(f"\nFull LOLO average MAE {m['model_MAE']:.4f} (baseline {m['baseline_MAE']:.4f}); "
        f"best lot {per.iloc[0].lot_key} {per.iloc[0].model_MAE:.4f}; "
        f"worst lot {per.iloc[-1].lot_key} {per.iloc[-1].model_MAE:.4f}")
    worse = per[per.WORSE_THAN_BASELINE]
    log(f"Lots where the model is WORSE than the hour x weekday baseline: "
        f"{list(worse.lot_key) if len(worse) else 'none'}")
    return groups, holdouts, b, r


def test2_place_transfer(H):
    section("TEST 2: Leave one place out (train on one city, test on the other)")
    srcs = sorted(H.source.unique())
    if len(srcs) < 2:
        log("SKIPPED: only one city loaded, so cross city transfer cannot be tested.")
        RESULTS.append({"test": "T2 leave one place out", "held_out": "SKIPPED (one city only)"})
        return
    for a in srcs:
        for bsrc in srcs:
            if a == bsrc:
                continue
            tr, te = H[H.source == a], H[H.source == bsrc]
            assert_no_leak(tr, f"Test 2 {a}->{bsrc}", test_lots=te.lot_key.unique())
            bb = fit_bundle(tr)
            record(f"T2 train {a} -> test {bsrc}", f"all {bsrc} lots", run_eval(bb, tr, te), tr, "place")


def date_split(dates, frac=0.7, gap_days=1):
    dates = sorted(dates)
    k = int(len(dates) * frac)
    return dates[:k], dates[k + gap_days:]


def test3_time(H, holdouts):
    section("TEST 3: Time based backtest (train on the past, predict the future)")
    roll_rows = []
    for src in sorted(H.source.unique()):
        S = H[H.source == src]
        hold_src = [l for l in holdouts if l.startswith(src + ":")]
        tr_d, te_d = date_split(S.date.unique())
        log(f"\n{src}: train {tr_d[0]}..{tr_d[-1]} ({len(tr_d)} days), gap 1 day, "
            f"test {te_d[0]}..{te_d[-1]} ({len(te_d)} days)")
        for case in ["all lots", "holdout lots removed (new lot AND new time)"]:
            tr = S[S.date.isin(tr_d)]
            te = S[S.date.isin(te_d)]
            if case.startswith("holdout"):
                tr = tr[~tr.lot_key.isin(hold_src)]
                te = te[te.lot_key.isin(hold_src)]
                assert_no_leak(tr, f"Test 3 {src} {case}", test_lots=hold_src, test_dates=te_d)
            else:
                assert_no_leak(tr, f"Test 3 {src} {case}", test_dates=te_d)
            bb = fit_bundle(tr)
            record(f"T3 70/30 time split {src} ({case})", f"dates {te_d[0]}..{te_d[-1]}",
                   run_eval(bb, tr, te), tr, "time")

        # Rolling: train on weeks 1..N, predict week N+1
        start = pd.Timestamp(min(S.date))
        S = S.assign(week=((pd.to_datetime(S.date) - start).dt.days // 7) + 1)
        weeks = sorted(S.week.unique())
        max_steps = CFG.get("backtest", {}).get("max_rolling_steps", 12)
        first_n = max(3, weeks[-1] - max_steps)
        for case in ["all lots", "holdout lots removed"]:
            for N in range(first_n, weeks[-1]):
                tr = S[S.week <= N]
                te = S[S.week == N + 1]
                if case != "all lots":
                    tr = tr[~tr.lot_key.isin(hold_src)]
                    te = te[te.lot_key.isin(hold_src)]
                if len(te) == 0:
                    continue
                assert not set(tr.date) & set(te.date), "LEAKAGE in rolling test"
                if case != "all lots":
                    assert not set(tr.lot_key) & set(hold_src), "LEAKAGE in rolling test lots"
                bb = fit_bundle(tr)
                rr = run_eval(bb, tr, te)
                mm = metrics(rr)
                roll_rows.append({"source": src, "case": case, "train_weeks": f"1..{N}",
                                  "test_week": N + 1, "test_dates": f"{min(te.date)}..{max(te.date)}",
                                  "model_MAE": mm["model_MAE"], "baseline_MAE": mm["baseline_MAE"],
                                  "improvement_pct": mm["improvement_pct"],
                                  "coverage": mm["coverage_adjusted"]})
                LOG.append({"test": f"T3 rolling {src} {case}", "random_seed": SEED,
                            "train_date_range": f"{min(tr.date)}..{max(tr.date)}",
                            "test_date_range": f"{min(te.date)}..{max(te.date)}",
                            "split_type": "rolling time",
                            "held_out_lots": ";".join(hold_src) if case != "all lots" else "",
                            "n_train_lots": tr.lot_key.nunique(), "n_test_lots": te.lot_key.nunique()})
    log("[leak check] rolling tests: train and test dates (and lots where removed) asserted disjoint: PASSED")
    roll = pd.DataFrame(roll_rows)
    roll.to_csv(OUT / "test3_rolling.csv", index=False)
    log("\nRolling weekly backtest:")
    log(roll.round(4).to_string())
    for (src, case), g in roll.groupby(["source", "case"]):
        RESULTS.append({"test": f"T3 rolling weekly {src} ({case})", "held_out": f"{len(g)} weekly steps",
                        "model_MAE": g.model_MAE.mean(), "baseline_MAE": g.baseline_MAE.mean(),
                        "improvement_pct": 100 * (g.baseline_MAE.mean() - g.model_MAE.mean()) / g.baseline_MAE.mean(),
                        "coverage_adjusted": g.coverage.mean()})
    # chart
    fig, axes = plt.subplots(1, roll.source.nunique(), figsize=(7 * roll.source.nunique(), 4), squeeze=False)
    for ax, (src, g) in zip(axes[0], roll.groupby("source")):
        for case, gg in g.groupby("case"):
            ax.plot(gg.test_week, gg.model_MAE, marker="o", label=f"model, {case}")
            ax.plot(gg.test_week, gg.baseline_MAE, ls="--", marker="x", label=f"baseline, {case}")
        ax.set_title(f"Rolling weekly backtest: {src}")
        ax.set_xlabel("week predicted"); ax.set_ylabel("MAE (occupancy rate)")
        ax.legend(fontsize=7); ax.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(OUT / "test3_rolling.png", dpi=130); plt.close(fig)


def test4_campus_blend(r1, H, groups, holdouts):
    section("TEST 4: Campus profile backtest (model vs template vs blend)")
    log("Uses the Test 1 model, which never saw the holdout lots.")
    b = CFG["blend"]
    rows = []
    sub = r1[(r1.is_weekend == 0) & (r1.hour >= b["model_hours_start"]) & (r1.hour <= b["model_hours_end"])].copy()
    sub_sets = {"all named holdouts": sub}
    for grp, lots in groups.items():
        sub_sets[grp] = sub[sub.lot_key.isin(lots)]
    for name, s in sub_sets.items():
        if len(s) == 0:
            continue
        y = s.occupancy_rate.values
        tmpl = template_value(s.hour, s.is_weekend) * np.where(s.is_holiday == 1, CA["break_holiday_multiplier"], 1)
        med, lo, hi, w = campus_estimate(s.lo.values, s.pred.values, s.hi.values, s.hour.values,
                                         s.is_weekend.values, s.is_holiday.values, 1, 0)
        rows.append({"subset": name, "n_rows": len(s),
                     "model_MAE": np.mean(np.abs(s.pred - y)),
                     "template_MAE": np.mean(np.abs(tmpl - y)),
                     f"blend_MAE (w={b['weight_model_hours']})": np.mean(np.abs(med - y)),
                     "baseline_MAE": np.mean(np.abs(s.base_hd - y)),
                     "blend_band_coverage": _coverage(y, lo, hi)})
    t4 = pd.DataFrame(rows)
    log("\nWeekday 8:00 to 16:30 on holdout lots (MAE in occupancy rate):")
    log(t4.round(4).to_string())
    t4.to_csv(OUT / "test4_campus_blend.csv", index=False)

    # weight sweep (diagnostic only: picking a weight from this table would be
    # tuning on test data, so we only use it to SUGGEST a value)
    y = sub.occupancy_rate.values
    t = template_value(sub.hour, sub.is_weekend)
    sweep = pd.DataFrame([{"w": w, "MAE": np.mean(np.abs(w * t + (1 - w) * sub.pred.values - y))}
                          for w in np.round(np.arange(0, 1.01, 0.1), 1)])
    log("\nBlend weight sweep on the same hours (diagnostic):")
    log(sweep.round(4).to_string(index=False))
    sweep.to_csv(OUT / "test4_weight_sweep.csv", index=False)
    a = t4.iloc[0]
    blend_col = [c for c in t4.columns if c.startswith("blend_MAE")][0]
    better = min(a.model_MAE, a.template_MAE)
    if a[blend_col] < better:
        log(f"VERDICT: blend wins ({a[blend_col]:.4f} vs best single {better:.4f}, "
            f"{100 * (better - a[blend_col]) / better:.1f}% better).")
    else:
        bw = sweep.loc[sweep.MAE.idxmin(), "w"]
        log(f"VERDICT: blend does NOT beat the better single predictor "
            f"({a[blend_col]:.4f} vs {better:.4f}). Lowest error weight in the sweep is w = {bw}. "
            f"Caution: that weight was picked on these test rows, so treat it as a suggestion.")
    RESULTS.append({"test": "T4 campus blend, weekday 8:00 to 16:30", "held_out": "named holdout lots",
                    "n_rows": int(a.n_rows), "model_MAE": a[blend_col], "baseline_MAE": a.baseline_MAE,
                    "improvement_pct": 100 * (a.baseline_MAE - a[blend_col]) / a.baseline_MAE,
                    "coverage_adjusted": a.blend_band_coverage,
                    "note": f"model alone {a.model_MAE:.4f}, template alone {a.template_MAE:.4f}"})

    # off hours
    off = r1[(r1.hour < b["model_hours_start"]) | (r1.hour > b["model_hours_end"])]
    if len(off) == 0:
        log("\nHours before 8:00 and after 16:30: NO real data in the loaded holdout lots, "
            "so these hours CANNOT be backtested. They are assumption driven.")
        RESULTS.append({"test": "T4 off hours (before 8:00, after 16:30)", "held_out": "CANNOT BE BACKTESTED (no data)"})
    else:
        y = off.occupancy_rate.values
        tmpl = template_value(off.hour, off.is_weekend)
        med, lo, hi, w = campus_estimate(off.lo.values, off.pred.values, off.hi.values, off.hour.values,
                                         off.is_weekend.values, off.is_holiday.values, 1, 0)
        log(f"\nOff hours on holdout lots ({off.source.unique()}): model MAE {np.mean(np.abs(off.pred - y)):.4f}, "
            f"template MAE {np.mean(np.abs(tmpl - y)):.4f}, blend MAE {np.mean(np.abs(med - y)):.4f}, "
            f"blend band coverage {_coverage(y, lo, hi):.1%}")
        RESULTS.append({"test": "T4 off hours (before 8:00, after 16:30)", "held_out": "named holdout lots",
                        "n_rows": len(off), "model_MAE": np.mean(np.abs(med - y)),
                        "baseline_MAE": np.mean(np.abs(off.base_hd - y)),
                        "coverage_adjusted": _coverage(y, lo, hi),
                        "note": f"model alone {np.mean(np.abs(off.pred - y)):.4f}, template alone {np.mean(np.abs(tmpl - y)):.4f}"})
    return t4, sweep


# =====================================================================
# STEP 8: PREDICT, FORECAST, CHARTS
# =====================================================================
MODEL_PATH = OUT / "park_shark_model.joblib"
CALIB_PATH = OUT / "calibration_params.json"
DUKE_CLOSED = set(CFG.get("duke_calendar", {}).get("closed_holiday_names", []))


def duke_holiday_flags(dates):
    us = holidays.country_holidays("US", years=sorted({d.year for d in dates}))
    is_hol = np.array([int(d in us) for d in dates])
    closed = np.array([int(d in us and any(n in us.get(d) for n in DUKE_CLOSED)) for d in dates])
    return is_hol, closed


_BUNDLE = None


def load_bundle():
    global _BUNDLE
    if _BUNDLE is None:
        _BUNDLE = joblib.load(MODEL_PATH)
    return _BUNDLE


def _duke_rows(when, weather=None):
    """One Duke hour as a model input row (time features, holiday flags, weather)."""
    ts = pd.Timestamp(when)
    if ts.tzinfo is None:
        ts = ts.tz_localize(CFG["duke"]["timezone"])
    w = {"temperature_2m": 15.0, "precipitation": 0.0, "snowfall": 0.0, "weather_code": 1}
    w.update(weather or {})
    df = add_time_features(pd.DataFrame({"timestamp_local": [ts.floor("h")]}))
    is_hol, closed = duke_holiday_flags(list(df["date"]))
    df["is_holiday"] = is_hol
    for k, v in w.items():
        df[k] = v
    return ts, df, closed


def predict(lot, when, weather=None, class_in_session=1, event_flag=0, live_feed=None):
    """Estimate one Duke lot at one hour.
    lot: lot name from config (e.g. "Trent Lot")
    when: a datetime or string in Duke local time, e.g. "2026-11-10 09:00"
    weather: dict with temperature_2m (C), precipitation (mm), snowfall (cm),
             weather_code (WMO). Missing values default to a mild dry day.
    live_feed: optional sensor feed (CSV path or the table from load_sensor_feed).
             Used only if enough fresh readings exist in this lot's area.
    Returns median, low, high as rates and car counts, plus what drove it."""
    if lot not in LOT_INFO:
        raise ValueError(f"unknown lot {lot!r}; choose from {list(LOT_INFO)}")
    bundle = load_bundle()
    ts, df, closed = _duke_rows(when, weather)
    info = LOT_INFO[lot]
    live = None
    if live_feed is not None:
        live = live_features(live_feed, ts, info["area"], df=df, closed=closed,
                             class_in_session=class_in_session, event_flag=event_flag,
                             own_group=info.get("sensor_group"))
    has_live = bool(live and live["usable"])
    ai_uses_live = has_live and any(f in bundle.get("features", FEATURES) for f in LIVE_FEATURES)
    if ai_uses_live:
        df = df.assign(**{f: live.get(f, np.nan) for f in LIVE_FEATURES})
    p = predict_bundle(bundle, df)
    med, lo, hi, wt = campus_estimate(p["lo"], p["pred"], p["hi"], df.hour.values,
                                      df.is_weekend.values, closed, class_in_session, event_flag,
                                      live=has_live)
    live_method, shift = "none", 0.0
    if ai_uses_live:
        live_method = "AI (learned from training data that had live inputs)"
    elif has_live:
        med, lo, hi, shift = live_adjust(med, lo, hi, live)
        live_method = ("RULE (ASSUMPTION): shifted by the surprise at the sensored spots. "
                       "The AI has not been trained on live inputs yet.")
    med, lo, hi = apply_calibration(lot, med, lo, hi)
    cap = info["capacity"]
    # This lot's OWN sensors: if enough of its spots reported just now, those spots are a
    # measurement, not an estimate. Only the silent spots are filled in by the estimate above.
    own_n = live["own_reporting"] if live else 0
    measured_now = own_n >= max(1, math.ceil(LIVE_CFG.get("own_min_share", 0.5) * cap))
    if measured_now:
        n_meas = min(own_n, cap)
        occ = min(live["own_occupied"], n_meas)
        silent = cap - n_meas
        med = (occ + silent * med) / cap
        lo = (occ + silent * lo) / cap
        hi = (occ + silent * hi) / cap
        live_method = (f"MEASURED: {n_meas} of {cap} spots reported in the last "
                       f"{LIVE_CFG.get('max_age_minutes', 2)} min"
                       + (f"; {silent} silent spots estimated" if silent else ""))
    out = {"lot": lot, "time": str(ts), "capacity": cap,
           "lot_type": info["lot_type"], "area": info["area"],
           "median_rate": float(med[0]), "low_rate": float(lo[0]), "high_rate": float(hi[0]),
           "median_cars": round(float(med[0]) * cap), "low_cars": round(float(lo[0]) * cap),
           "high_cars": round(float(hi[0]) * cap), "template_weight": float(wt[0]),
           "driven_by": ("SENSORS (measured)" if measured_now else
                         "ASSUMPTION (campus template)" if wt[0] >= 0.5 else "MODEL (public data)"),
           "calibrated_with_duke_data": lot in load_calibration(),
           "live_data_used": has_live or measured_now, "measured_now": measured_now,
           "live_method": live_method, "live_shift_rate": round(shift, 4)}
    if live is not None:
        out["live_inputs"] = {k: (None if isinstance(v, float) and math.isnan(v) else
                                  round(v, 3) if isinstance(v, float) else v) for k, v in live.items()}
    return out


def load_calibration():
    if CALIB_PATH.exists():
        return json.loads(CALIB_PATH.read_text())
    return {}


def apply_calibration(lot, med, lo, hi):
    c = load_calibration().get(lot)
    if not c:
        return med, lo, hi
    f = lambda x: np.clip(c["scale"] * x + c["offset"], 0, 1)
    return f(med), f(lo), f(hi)


def predict_frame(frame, class_in_session=1, event_flag=0):
    """Vectorised predict for many Duke hours. frame needs timestamp_local
    (Duke local) plus the four weather columns."""
    bundle = load_bundle()
    df = add_time_features(frame.copy())
    is_hol, closed = duke_holiday_flags(list(df["date"]))
    df["is_holiday"] = is_hol
    p = predict_bundle(bundle, df)
    med, lo, hi, wt = campus_estimate(p["lo"], p["pred"], p["hi"], df.hour.values,
                                      df.is_weekend.values, closed, class_in_session, event_flag)
    out = []
    for lot, cap in DUKE_CAPACITIES.items():
        m2, l2, h2 = apply_calibration(lot, med, lo, hi)
        o = pd.DataFrame({"lot": lot, "lot_type": LOT_INFO[lot]["lot_type"], "area": LOT_INFO[lot]["area"],
                          "timestamp_local": df.timestamp_local.astype(str),
                          "hour": df.hour, "is_weekend": df.is_weekend, "us_holiday": is_hol,
                          "temperature_2m": df.temperature_2m, "precipitation": df.precipitation,
                          "median_rate": m2.round(3), "low_rate": l2.round(3), "high_rate": h2.round(3),
                          "median_cars": np.round(m2 * cap).astype(int), "low_cars": np.round(l2 * cap).astype(int),
                          "high_cars": np.round(h2 * cap).astype(int), "capacity": cap,
                          "template_weight": wt,
                          "driven_by": np.where(wt >= 0.5, "ASSUMPTION", "MODEL")})
        out.append(o)
    return pd.concat(out, ignore_index=True)


def duke_forecast_csv():
    files = sorted((DATA / "cache").glob("forecast_duke_*.json"))
    if not files:
        log("No Duke forecast file found. Run: python3 fetch_data.py forecast")
        return None
    w = load_weather_json(files[-1])
    tz = CFG["duke"]["timezone"]
    w["timestamp_local"] = w["key"].dt.tz_localize(tz, ambiguous="NaT", nonexistent="NaT")
    w = w.dropna(subset=["timestamp_local"])
    now = pd.Timestamp.now(tz=tz).floor("h")
    w = w[(w.timestamp_local >= now) & (w.timestamp_local < now + pd.Timedelta(days=7))]
    fc = predict_frame(w.drop(columns="key"))
    fc.to_csv(OUT / "duke_forecast_next_7_days.csv", index=False)
    log(f"Wrote 7 day forecast: {len(fc)} rows, from {fc.timestamp_local.min()} to {fc.timestamp_local.max()} "
        f"(weather file {files[-1].name})")
    return fc


def typical_weekday_charts():
    tz = CFG["duke"]["timezone"]
    day = pd.Timestamp("2026-11-11", tz=tz)  # a Wednesday; weather held at a mild dry 15 C
    frame = pd.DataFrame({"timestamp_local": [day + pd.Timedelta(hours=h) for h in range(24)],
                          "temperature_2m": 15.0, "precipitation": 0.0, "snowfall": 0.0, "weather_code": 1})
    # Use a normal in-session weekday: no holiday multiplier for this chart.
    bundle = load_bundle()
    df = add_time_features(frame)
    df["is_holiday"] = 0
    p = predict_bundle(bundle, df)
    med, lo, hi, wt = campus_estimate(p["lo"], p["pred"], p["hi"], df.hour.values, df.is_weekend.values, 0, 1, 0)
    hours = np.arange(24)
    nrows = math.ceil(len(DUKE_CAPACITIES) / 2)
    fig, axes = plt.subplots(nrows, 2, figsize=(12, 4 * nrows), squeeze=False)
    for ax in axes.ravel()[len(DUKE_CAPACITIES):]:
        ax.set_visible(False)
    for ax, (lot, cap) in zip(axes.ravel(), DUKE_CAPACITIES.items()):
        m2, l2, h2 = apply_calibration(lot, med, lo, hi)
        for h in hours[wt >= 0.5]:
            ax.axvspan(h - 0.5, h + 0.5, color="#bbbbbb", alpha=0.35, lw=0)
        ax.fill_between(hours, l2 * cap, h2 * cap, color="#2b7bba", alpha=0.25, label="10th to 90th band")
        ax.plot(hours, m2 * cap, color="#2b7bba", lw=2, label="median estimate")
        ax.plot(hours, np.clip(p["pred"], 0, 1) * cap, color="#e07b39", lw=1, ls="--", label="model alone")
        ax.axhline(cap, color="k", lw=0.8, ls=":")
        ax.set_title(f"{lot} (capacity {cap})")
        ax.set_xlim(-0.5, 23.5); ax.set_ylim(0, cap * 1.05)
        ax.set_xticks(range(0, 24, 2)); ax.set_xlabel("hour of day"); ax.set_ylabel("cars")
        ax.grid(alpha=0.3)
    axes[0, 0].legend(fontsize=8, loc="upper left")
    fig.suptitle("Typical in-session weekday (mild dry day). GRAY = assumption driven hours "
                 "(campus template, no public data). WHITE = model driven hours.", fontsize=10)
    fig.tight_layout(); fig.savefig(OUT / "duke_typical_weekday.png", dpi=140); plt.close(fig)
    log("Wrote chart outputs/duke_typical_weekday.png")


# =====================================================================
# LIVE SENSOR INPUTS (Nov 2026): the hardware team's 0/1 spot readings
# and vehicle counts, turned into the LIVE_FEATURES columns
# =====================================================================
FEED_KINDS = ("occupancy", "vehicle_count")


def load_sensor_feed(src):
    """Read the hardware team's sensor feed. One row per sensor message:
      timestamp  Duke local time, e.g. 2026-11-10 08:45:00
      sensor_id  unique id of the physical sensor
      group      a sensor group name from config.toml [[duke.sensor_groups]]
      kind       "occupancy"     -> value is 0 (empty) or 1 (occupied)
                 "vehicle_count" -> value is cars counted since that sensor's last message
      value      the number
    Extra columns (raw tesla readings, radar distance, battery) are allowed and kept.
    Lines starting with # are comments."""
    if isinstance(src, pd.DataFrame) and "ts" in src.columns:
        return src   # already loaded
    feed = pd.read_csv(src, comment="#") if not isinstance(src, pd.DataFrame) else src.copy()
    need = {"timestamp", "sensor_id", "group", "kind", "value"}
    if not need <= set(feed.columns):
        raise ValueError(f"sensor feed needs columns {sorted(need)}, found {list(feed.columns)}")
    tz = CFG["duke"]["timezone"]
    t = pd.to_datetime(feed["timestamp"])
    t = t.dt.tz_localize(tz, ambiguous="NaT", nonexistent="NaT") if t.dt.tz is None else t.dt.tz_convert(tz)
    feed["ts"] = t
    feed = feed.dropna(subset=["ts", "value"])
    unknown = sorted(set(feed["group"]) - set(SENSOR_GROUPS))
    if unknown:
        log(f"note: sensor groups not in config.toml were ignored: {unknown}")
    bad_kind = sorted(set(feed["kind"]) - set(FEED_KINDS))
    if bad_kind:
        log(f"note: unknown kinds ignored: {bad_kind} (use {FEED_KINDS})")
    feed = feed[feed["group"].isin(SENSOR_GROUPS) & feed["kind"].isin(FEED_KINDS)]
    bad = (feed["kind"] == "occupancy") & ~feed["value"].isin([0, 1])
    if bad.any():
        log(f"note: dropped {int(bad.sum())} occupancy rows whose value is not 0 or 1")
        feed = feed[~bad]
    return feed.sort_values("ts").reset_index(drop=True)


def _area_rate(occ, t_end, max_age):
    """Share of sensored spots occupied at t_end: the latest reading of each sensor
    inside (t_end - max_age, t_end], weighted by its group's lot type."""
    win = occ[(occ.ts <= t_end) & (occ.ts > t_end - max_age)]
    if win.empty:
        return np.nan, 0
    last = win.groupby("sensor_id").tail(1)
    gw = LIVE_CFG.get("group_weight", {})
    w = last["group"].map(lambda g: gw.get(SENSOR_GROUPS[g].get("lot_type"), 1.0)).astype(float)
    if w.sum() <= 0:
        return np.nan, int(len(last))
    return float((w * last["value"]).sum() / w.sum()), int(len(last))


def live_features(feed, when, area, df=None, closed=None, class_in_session=1, event_flag=0,
                  own_group=None):
    """Build the LIVE_FEATURES for one area at one moment. Returns None if no sensor
    group sits in that area. 'usable' is False when too few sensors reported.
    own_group: the lot's own sensor group. Its spots are reported separately
    (own_reporting, own_occupied), because for that lot they are a measurement,
    not just a hint."""
    groups = [g for g, c in SENSOR_GROUPS.items() if c.get("area") == area]
    if not groups:
        return None
    feed = load_sensor_feed(feed)
    ts = pd.Timestamp(when)
    if ts.tzinfo is None:
        ts = ts.tz_localize(CFG["duke"]["timezone"])
    f = feed[feed["group"].isin(groups) & (feed.ts <= ts)]
    occ = f[f.kind == "occupancy"]
    max_age = pd.Timedelta(minutes=LIVE_CFG.get("max_age_minutes", 20))
    rate, n = _area_rate(occ, ts, max_age)
    past, _ = _area_rate(occ, ts - pd.Timedelta(minutes=LIVE_CFG.get("trend_window_minutes", 30)), max_age)
    counts = f[f.kind == "vehicle_count"]
    recent = counts[counts.ts > ts - pd.Timedelta(minutes=LIVE_CFG.get("count_window_minutes", 15))]
    cars = float(recent["value"].sum()) if len(counts) else np.nan
    # Expected: what the model says for this hour WITHOUT live data. Every lot shares
    # one curve before calibration, so this is also the expectation for the sensored spots.
    if df is None:
        _, df, closed = _duke_rows(ts)
    p = predict_bundle(load_bundle(), df)
    exp, _, _, _ = campus_estimate(p["lo"], p["pred"], p["hi"], df.hour.values, df.is_weekend.values,
                                   closed, class_in_session, event_flag)
    expected = float(exp[0])
    min_n = LIVE_CFG.get("min_sensors_reporting", 3)
    usable = n >= min_n and not np.isnan(rate)
    own_n = own_occ = 0
    if own_group is not None:
        own = occ[(occ["group"] == own_group) & (occ.ts > ts - max_age)]
        last = own.groupby("sensor_id").tail(1)
        own_n, own_occ = int(len(last)), int(last["value"].sum())
    return {"usable": bool(usable), "area": area, "sensors_reporting": n,
            "own_group": own_group, "own_reporting": own_n, "own_occupied": own_occ,
            "sensors_total": int(occ["sensor_id"].nunique()),
            "live_nearby_rate": rate, "expected_rate_no_live": expected,
            "live_vs_expected": rate - expected if usable else np.nan,
            "live_trend_30min": rate - past if usable and not np.isnan(past) else np.nan,
            "cars_passed_15min": cars,
            "why_not_used": None if usable else
            f"only {n} sensors reported in the last {int(max_age.total_seconds() // 60)} min (need {min_n})"}


def live_adjust(med, lo, hi, live):
    """Interim RULE (ASSUMPTION, not learned) used until the AI is trained on live inputs.
    shift = transfer_same_area x trust x (live rate - expected rate), trust = n / (n + shrink_spots).
    The band narrows by up to max_band_shrink at full trust. All three numbers are in config.toml."""
    n = live["sensors_reporting"]
    trust = n / (n + LIVE_CFG.get("shrink_spots", 10))
    shift = LIVE_CFG.get("transfer_same_area", 0.5) * trust * live["live_vs_expected"]
    shrink = 1 - LIVE_CFG.get("max_band_shrink", 0.3) * trust
    new_med = np.clip(med + shift, 0, 1)
    new_lo = np.clip(new_med - (med - lo) * shrink, 0, new_med)
    new_hi = np.clip(new_med + (hi - med) * shrink, new_med, 1)
    return new_med, new_lo, new_hi, float(shift)


def live_compare(feed_src, when, save=True):
    """The leader's test: every lot, WITHOUT and WITH live sensor data, side by side.
    This shows how the estimate CHANGES. It cannot show whether it got more ACCURATE;
    that needs real counts at the target lots."""
    feed = load_sensor_feed(feed_src)
    rows = []
    for lot in LOT_INFO:
        a = predict(lot, when)
        b = predict(lot, when, live_feed=feed)
        li = b.get("live_inputs") or {}
        rows.append({"lot": lot, "area": b["area"], "capacity": b["capacity"],
                     "cars_no_live": a["median_cars"], "band_no_live": f'{a["low_cars"]} to {a["high_cars"]}',
                     "band_width_no_live": a["high_cars"] - a["low_cars"],
                     "cars_with_live": b["median_cars"], "band_with_live": f'{b["low_cars"]} to {b["high_cars"]}',
                     "band_width_with_live": b["high_cars"] - b["low_cars"],
                     "template_weight_no_live": a["template_weight"], "template_weight_with_live": b["template_weight"],
                     "live_used": b["live_data_used"], "measured_now": b["measured_now"],
                     "own_spots_reporting": li.get("own_reporting"),
                     "sensors_reporting": li.get("sensors_reporting"),
                     "live_rate": li.get("live_nearby_rate"), "expected_rate": li.get("expected_rate_no_live"),
                     "cars_passed_15min": li.get("cars_passed_15min"), "method": b["live_method"]})
    out = pd.DataFrame(rows)
    if save:
        OUT.mkdir(parents=True, exist_ok=True)
        out.to_csv(OUT / "live_comparison.csv", index=False)
    return out


def feed_to_counts(feed_src, out_path=None):
    """Turn the sensor feed into the hourly table calibrate() reads
    (lot, timestamp, occupancy_count). occupancy_count = average number of occupied
    spots in that hour (each sensor's average 0/1 reading, summed over the group).
    After about 5 days of sensor data:  calibrate sensor_data/hourly_counts.csv"""
    feed = load_sensor_feed(feed_src)
    group_to_lot = {i["sensor_group"]: lot for lot, i in LOT_INFO.items() if i.get("sensor_group")}
    occ = feed[(feed.kind == "occupancy") & feed["group"].isin(group_to_lot)].copy()
    occ["hour"] = occ.ts.dt.floor("h")
    per_sensor = occ.groupby(["group", "hour", "sensor_id"])["value"].mean()
    counts = per_sensor.groupby(["group", "hour"]).agg(["sum", "count"]).reset_index()
    counts["lot"] = counts["group"].map(group_to_lot)
    cap = counts["lot"].map(lambda l: LOT_INFO[l]["capacity"])
    # Hours where fewer than half the spots reported are dropped, not guessed.
    keep = counts["count"] >= np.ceil(cap * LIVE_CFG.get("own_min_share", 0.5))
    out = pd.DataFrame({"lot": counts["lot"], "timestamp": counts["hour"].dt.strftime("%Y-%m-%d %H:%M"),
                        "occupancy_count": (counts["sum"] / counts["count"] * cap).round(2),
                        "spots_reporting": counts["count"]})[keep.values]
    out_path = Path(out_path or ROOT / "sensor_data" / "hourly_counts.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False)
    log(f"Wrote {out_path}: {len(out)} lot-hours ({int((~keep).sum())} dropped for too few spots reporting)")
    return out


def export_app(out_path=None):
    """Write the forecast file the team dashboard reads: data/tier3_forecast.json in
    github.com/momacaes9/parkshark. For each of the four spot groups:
      hourly:  hour by hour for every hour in the latest weather forecast file
      typical: a normal in-session weekday, Saturday and Sunday (mild dry weather),
               used by the dashboard for any hour outside the forecast (e.g. ?date= demos)
    Values are occupancy rates (0 to 1): m = middle estimate, lo and hi = 10th to 90th band.
    Map placement (feature id or marker position) comes from map_* in config.toml."""
    tz = CFG["duke"]["timezone"]
    bundle = load_bundle()

    def by_lot(frame):
        fc = predict_frame(frame)
        return {lot: g for lot, g in fc.groupby("lot", sort=False)}

    typical_days = {"weekday": "2026-11-10", "saturday": "2026-11-14", "sunday": "2026-11-15"}
    typical = {}
    for kind, day in typical_days.items():
        start = pd.Timestamp(day, tz=tz)
        frame = pd.DataFrame({"timestamp_local": [start + pd.Timedelta(hours=h) for h in range(24)],
                              "temperature_2m": 15.0, "precipitation": 0.0, "snowfall": 0.0, "weather_code": 1})
        for lot, g in by_lot(frame).items():
            typical.setdefault(lot, {})[kind] = [[round(float(a), 3), round(float(b), 3), round(float(c), 3)]
                                                 for a, b, c in zip(g.median_rate, g.low_rate, g.high_rate)]
    hourly, weather_file = {}, None
    files = sorted((DATA / "cache").glob("forecast_duke_*.json"))
    if files:
        weather_file = files[-1].name
        w = load_weather_json(files[-1])
        w["timestamp_local"] = w["key"].dt.tz_localize(tz, ambiguous="NaT", nonexistent="NaT")
        w = w.dropna(subset=["timestamp_local"]).drop(columns="key")
        for lot, g in by_lot(w).items():
            hourly[lot] = [{"t": t[:16], "m": round(float(a), 3), "lo": round(float(b), 3), "hi": round(float(c), 3)}
                           for t, a, b, c in zip(g.timestamp_local, g.median_rate, g.low_rate, g.high_rate)]
    else:
        log("No weather forecast file: only the typical days are exported. Run: python3 fetch_data.py forecast")
    calib = load_calibration()
    groups = {}
    for c in CFG["duke"]["lots"]:
        gid = c.get("sensor_group") or c["name"]
        groups[gid] = {"name": c["name"], "spots": c["capacity"], "lot_type": c.get("lot_type"),
                       "area": c.get("area"), "feature_id": c.get("map_feature_id") or None,
                       "lat": c.get("map_lat"), "lon": c.get("map_lon"), "match": c.get("map_match", ""),
                       "calibrated_with_duke_data": c["name"] in calib,
                       "typical": typical.get(c["name"], {}), "hourly": hourly.get(c["name"], [])}
    doc = {"generated": pd.Timestamp.now(tz=tz).strftime("%Y-%m-%d %H:%M %Z"),
           "model": "Park Shark Tier 3: gradient boosted quantile regression (scikit-learn "
                    "HistGradientBoostingRegressor, 10th/50th/90th percentile) blended with a campus template",
           "features": bundle.get("features", FEATURES),
           "trained_on": bundle.get("trained_on", ["birmingham", "hague"]),
           "band": "10th to 90th percentile",
           "weather_file": weather_file, "typical_days": typical_days,
           "honesty_note": ("Learned from public car parks in Birmingham (UK) and The Hague (NL). No Duke readings "
                            "used yet. On the most campus-like public lots it beat the hour x weekday average by 44% "
                            "(MAE 0.129 vs 0.231). Live sensor readings replace these estimates when present."),
           "groups": groups}
    # Sensored groups: confidence is "high" when live, "medium" otherwise (set by the dashboard).
    for g in groups.values():
        g["confidence"] = "medium"
        g["sensors_for_80pct"] = sensors_for_80(g["spots"])
    if NEARBY:
        typ_frames = {}
        for kind, day in typical_days.items():
            start = pd.Timestamp(day, tz=tz)
            typ_frames[kind] = pd.DataFrame({"timestamp_local": [start + pd.Timedelta(hours=h) for h in range(24)],
                                             "temperature_2m": 15.0, "precipitation": 0.0, "snowfall": 0.0,
                                             "weather_code": 1})
        nb, unusual, settings = nearby_export(typ_frames, w if files else None)
        doc["nearby"] = nb
        doc["traffic_unusual"] = unusual
        doc["nearby_settings"] = settings
        doc["nearby_note"] = ("Lots WITHOUT sensors. Same AI curve as the sensored groups (the model has no lot "
                              "identity yet), a wider band, a small street traffic nudge (duke_traffic) and part of "
                              "the live sensor surprise from the nearest sensored groups. Expect these to be wrong "
                              "by several cars; that gap is the case for more sensors.")
    out_path = Path(out_path or OUT / "tier3_forecast.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, indent=1))
    log(f"Wrote {out_path} ({len(groups)} groups, {sum(len(g['hourly']) for g in groups.values())} hourly rows, "
        f"{len(doc.get('nearby', {}))} nearby lots)")
    return out_path


# =====================================================================
# NEARBY LOTS (Nov 8 2026): lots with NO sensors, estimated with LOW confidence
# =====================================================================
# The team lead asked for a few lots around the 4 sensored groups, shown with honest
# (lower) confidence, to show two things on one dashboard: the model can say something
# beyond the hardware, and it gets worse the further a lot is from real sensors.
NEARBY = CFG["duke"].get("nearby_lots", [])
NB_CFG = CFG.get("nearby", {})
# sensors_needed.py (Nov 7 sim): sensors for 80% of busy hours within +/- 10 points.
SENSORS_FOR_80 = [(5, 5), (8, 6), (10, 7), (27, 12), (52, 14.5), (80, 16), (85, 16), (163, 18), (239, 19)]


def sensors_for_80(n):
    """Sensors a lot of n spots needs for 80% of busy hours within +/- 10 points.
    Read off the simulation table on a log scale; beyond 239 spots it stays near 20,
    like a poll needing about the same sample for a small or a huge town."""
    if n <= SENSORS_FOR_80[0][0]:
        return int(n)
    xs = [math.log(a) for a, _ in SENSORS_FOR_80]
    ys = [b for _, b in SENSORS_FOR_80]
    if n >= SENSORS_FOR_80[-1][0]:
        return int(min(n, 20))
    return int(min(n, math.ceil(np.interp(math.log(n), xs, ys))))


def _dist_m(lat1, lon1, lat2, lon2):
    return math.hypot((lat1 - lat2) * 111_000, (lon1 - lon2) * 111_000 * math.cos(math.radians(lat1)))


def nearby_info(c):
    """Distance to the nearest sensored group, confidence level and live transfer for
    one [[duke.nearby_lots]] entry. Confidence is worked out here, never typed in."""
    placed = [l for l in CFG["duke"]["lots"] if l.get("map_lat") is not None]
    dists = sorted((_dist_m(c["map_lat"], c["map_lon"], l["map_lat"], l["map_lon"]), l) for l in placed)
    d_near, near = dists[0][0], dists[0][1]
    same = [(d, l) for d, l in dists if l.get("area") == c.get("area")]
    low = d_near <= NB_CFG.get("low_max_m", 150) and c.get("lot_type") in NB_CFG.get("similar_types", [])
    why = []
    why.append(f"nearest sensored group: {near['name']}, about {round(d_near, -1):.0f} m away")
    if d_near > NB_CFG.get("low_max_m", 150):
        why.append(f"more than {NB_CFG.get('low_max_m', 150)} m from any sensor")
    if c.get("lot_type") not in NB_CFG.get("similar_types", []):
        why.append(f"a {c.get('lot_type', 'unknown').replace('_', ' ')} behaves unlike the sensored spots")
    transfer = 0.0
    if same:
        transfer = LIVE_CFG.get("transfer_same_area", 0.5) * math.exp(-same[0][0] / NB_CFG.get("decay_m", 150))
        why.append(f"gets {transfer / max(LIVE_CFG.get('transfer_same_area', 0.5), 1e-9):.0%} of the normal "
                   f"live sensor nudge from the {c.get('area', '').replace('_', ' ')} groups")
    else:
        why.append("no sensored group in its area, so live sensor data is not passed on")
    conf = "low" if low else "very_low"
    mae = NB_CFG.get("mae_low" if low else "mae_very_low", 0.23)
    return {"confidence": conf, "nearest_group": near.get("sensor_group"), "nearest_group_name": near["name"],
            "distance_m": round(d_near), "live_transfer": round(transfer, 3), "why": why,
            "typical_error_cars": max(1, round(mae * c["capacity"])), "mae_rate": mae,
            "sensors_for_80pct": sensors_for_80(c["capacity"])}


def traffic_ratios(path=None):
    """Street traffic this hour divided by the same hour on a normal day of the same kind
    (median of all weekdays, or of all weekend days), per traffic lot and date.
    Comes from duke_traffic (NCDOT counts, Duke calendar, game windows). It is about 1.0
    on a normal day and moves on breaks, holidays and game windows.
    Returns {traffic_lot: {"YYYY-MM-DD": [24 ratios]}}; empty if the file is missing."""
    path = Path(path or ROOT / "duke_traffic" / "outputs" / "duke_traffic_by_hour.csv")
    if not path.exists():
        log(f"note: {path} not found, street traffic nudge switched off (ratio 1.0)")
        return {}
    t = pd.read_csv(path)
    want = {c.get("traffic_lot") for c in NEARBY if c.get("traffic_lot")}
    t = t[t["lot"].isin(want)].copy()
    t = t.assign(is_weekend=pd.to_datetime(t["date"]).dt.dayofweek >= 5)
    ref = t.groupby(["lot", "is_weekend", "hour"])["street_vph"].median().rename("ref")
    t = t.join(ref, on=["lot", "is_weekend", "hour"])
    lo, hi = NB_CFG.get("traffic_ratio_clip", [0.5, 1.5])
    t["ratio"] = (t["street_vph"] / t["ref"].where(t["ref"] > 0)).fillna(1.0).clip(lo, hi)
    out = {}
    for (lot, date), g in t.sort_values("hour").groupby(["lot", "date"]):
        r = g.set_index("hour")["ratio"].reindex(range(24)).fillna(1.0)
        out.setdefault(lot, {})[date] = [round(float(x), 2) for x in r]
    return out


def _campus_curve(frame, class_in_session=1, event_flag=0):
    """The shared AI curve (before any lot calibration) for many Duke hours."""
    df = add_time_features(frame.copy())
    is_hol, closed = duke_holiday_flags(list(df["date"]))
    df["is_holiday"] = is_hol
    p = predict_bundle(load_bundle(), df)
    med, lo, hi, wt = campus_estimate(p["lo"], p["pred"], p["hi"], df.hour.values,
                                      df.is_weekend.values, closed, class_in_session, event_flag)
    return df, med, lo, hi


def nearby_adjust(med, lo, hi, info, ratio=1.0):
    """Street traffic nudge, then a wider band. Live data is applied later (dashboard or
    nearby_now), because it changes minute to minute."""
    beta = NB_CFG.get("traffic_beta", 0.3)
    f = 1 + beta * (np.asarray(ratio, dtype=float) - 1)
    extra = NB_CFG.get("band_extra_low" if info["confidence"] == "low" else "band_extra_very_low", 0.1)
    m = np.clip(med * f, 0, 1)
    return m, np.clip(lo * f - extra, 0, m), np.clip(hi * f + extra, m, 1)


def nearby_export(typical_frames, forecast_frame=None):
    """The 'nearby' block of tier3_forecast.json."""
    ratios = traffic_ratios()
    out = {}
    for c in NEARBY:
        info = nearby_info(c)
        tr = ratios.get(c.get("traffic_lot"), {})
        typical = {}
        for kind, frame in typical_frames.items():
            _, med, lo, hi = _campus_curve(frame)
            m, l, h = nearby_adjust(med, lo, hi, info, 1.0)
            typical[kind] = [[round(float(a), 3), round(float(b), 3), round(float(x), 3)] for a, b, x in zip(m, l, h)]
        hourly = []
        if forecast_frame is not None and len(forecast_frame):
            df, med, lo, hi = _campus_curve(forecast_frame)
            keys = df.timestamp_local.dt.strftime("%Y-%m-%d").values
            r = np.array([tr.get(k, [1.0] * 24)[hh] for k, hh in zip(keys, df.hour.values)])
            m, l, h = nearby_adjust(med, lo, hi, info, r)
            hourly = [{"t": t.strftime("%Y-%m-%d %H:%M"), "m": round(float(a), 3), "lo": round(float(b), 3),
                       "hi": round(float(x), 3), "tr": round(float(rr), 2)}
                      for t, a, b, x, rr in zip(df.timestamp_local, m, l, h, r)]
        out[c["name"]] = {"name": c["name"], "spots": c["capacity"], "lot_type": c.get("lot_type"),
                          "area": c.get("area"), "feature_ids": c.get("map_feature_ids", []),
                          "lat": c.get("map_lat"), "lon": c.get("map_lon"), "note": c.get("note", ""),
                          "traffic_lot": c.get("traffic_lot"), **info,
                          "typical": typical, "hourly": hourly}
    # Only days where traffic is not normal are written (the dashboard assumes 1.0 otherwise).
    unusual = {lot: {d: v for d, v in days.items() if any(abs(x - 1) > 0.02 for x in v)}
               for lot, days in ratios.items()}
    settings = {"transfer_same_area": LIVE_CFG.get("transfer_same_area", 0.5),
                "shrink_spots": LIVE_CFG.get("shrink_spots", 10),
                "min_sensors_reporting": LIVE_CFG.get("min_sensors_reporting", 3),
                "group_weight": LIVE_CFG.get("group_weight", {}),
                "max_band_shrink": NB_CFG.get("max_band_shrink", 0.0),
                "traffic_beta": NB_CFG.get("traffic_beta", 0.3),
                "share_within_10_no_sensors": NB_CFG.get("share_within_10_no_sensors", [0.31, 0.51]),
                "confidence_levels": {
                    "high": "Measured: this lot's own sensors are reporting right now",
                    "medium": "Sensored group, AI estimate: sensors are installed here, so the estimate is "
                              "replaced by readings when they arrive and calibrated after about 5 days",
                    "low": "No sensors, but close to a sensored group and a similar kind of parking",
                    "very_low": "No sensors, and far from them or a different kind of parking (garage, huge lot)"}}
    return out, unusual, settings


def nearby_now(when, feed=None):
    """Every nearby lot at one moment, with live sensor data if a feed is given.
    Same rule as live_adjust(), scaled by each lot's live_transfer, band NOT narrowed."""
    ts = pd.Timestamp(when)
    if ts.tzinfo is None:
        ts = ts.tz_localize(CFG["duke"]["timezone"])
    frame = pd.DataFrame({"timestamp_local": [ts.floor("h")], "temperature_2m": 15.0, "precipitation": 0.0,
                          "snowfall": 0.0, "weather_code": 1})
    _, med, lo, hi = _campus_curve(frame)
    ratios = traffic_ratios()
    rows = []
    for c in NEARBY:
        info = nearby_info(c)
        r = ratios.get(c.get("traffic_lot"), {}).get(ts.strftime("%Y-%m-%d"), [1.0] * 24)[ts.hour]
        m, l, h = nearby_adjust(med, lo, hi, info, r)
        shift, n = 0.0, 0
        if feed is not None and info["live_transfer"] > 0:
            live = live_features(feed, ts, c.get("area"))
            if live and live["usable"]:
                n = live["sensors_reporting"]
                trust = n / (n + LIVE_CFG.get("shrink_spots", 10))
                shift = info["live_transfer"] * trust * live["live_vs_expected"]
                m2 = np.clip(m + shift, 0, 1)
                l, h = np.clip(m2 - (m - l), 0, m2), np.clip(m2 + (h - m), m2, 1)
                m = m2
        cap = c["capacity"]
        rows.append({"lot": c["name"], "confidence": info["confidence"], "distance_m": info["distance_m"],
                     "capacity": cap, "cars": round(float(m[0]) * cap),
                     "likely": f"{round(float(l[0]) * cap)} to {round(float(h[0]) * cap)}",
                     "typical_error_cars": info["typical_error_cars"], "traffic_ratio": r,
                     "live_shift_cars": round(shift * cap, 1), "sensors_reporting": n,
                     "sensors_for_80pct": info["sensors_for_80pct"]})
    return pd.DataFrame(rows)


def write_example_feed(path=None, day="2026-11-10"):
    """Write a FAKE sensor feed so the pipeline can be tested before the hardware works.
    Every value is invented: each spot gets a random arrival time (about 7:50) and
    departure time (about 16:30), so the morning fills earlier than the campus template."""
    path = Path(path or ROOT / "sensor_data" / "EXAMPLE_sensor_feed_FAKE.csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(SEED)
    tz = CFG["duke"]["timezone"]
    times = pd.date_range(f"{day} 06:00", f"{day} 19:00", freq="30s", tz=tz)   # one ping every 30 s
    hours = times.hour + times.minute / 60
    rows = []
    for g, c in SENSOR_GROUPS.items():
        for i in range(int(c.get("n_spots", 0))):
            arrive, leave = rng.normal(7.8, 0.6), rng.normal(16.5, 0.8)
            stays_empty = rng.random() < 0.1
            for t, h in zip(times, hours):
                occ = int((not stays_empty) and arrive <= h < leave)
                rows.append((t.strftime("%Y-%m-%d %H:%M:%S"), f"{g}_{i + 1:02d}", g, "occupancy", occ))
    counter = next((g for g, c in SENSOR_GROUPS.items() if c.get("lot_type") == "curb"), None)
    if counter:
        for t, h in zip(times, hours):
            rate = 40 + 200 * np.exp(-((h - 8) ** 2) / 0.8) + 150 * np.exp(-((h - 17) ** 2) / 0.8)
            rows.append((t.strftime("%Y-%m-%d %H:%M:%S"), f"{counter}_counter", counter, "vehicle_count",
                         int(rng.poisson(rate / 120))))
    with open(path, "w") as fh:
        fh.write("# FAKE EXAMPLE DATA for testing the pipeline. Every value is invented. Not a real reading.\n")
        pd.DataFrame(rows, columns=["timestamp", "sensor_id", "group", "kind", "value"]).to_csv(fh, index=False)
    log(f"Wrote FAKE example feed {path} ({len(rows):,} rows)")
    return path


# =====================================================================
# STEP 9: CALIBRATION HOOK FOR REAL DUKE COUNTS
# =====================================================================
def calibrate(csv_path, weather_json=None, save=True):
    """Check and correct the model with real Duke counts.
    csv columns: lot, timestamp, occupancy_count (timestamp in Duke local time).
    For each lot: (a) error before correction, (b) fit ONE scale and ONE
    offset, (c) error after correction on readings the fit did not see,
    (d) refuse when there are too few readings."""
    c = CFG["calibration"]
    obs = pd.read_csv(csv_path)
    need = {"lot", "timestamp", "occupancy_count"}
    if not need <= set(obs.columns):
        raise ValueError(f"CSV needs columns {need}, found {list(obs.columns)}")
    tz = CFG["duke"]["timezone"]
    obs["timestamp_local"] = pd.to_datetime(obs["timestamp"]).dt.tz_localize(tz, ambiguous="NaT", nonexistent="NaT")
    obs = obs.dropna(subset=["timestamp_local"])
    obs["timestamp_local"] = obs["timestamp_local"].dt.floor("h")
    if weather_json:
        w = load_weather_json(weather_json)
        obs["key"] = obs.timestamp_local.dt.tz_localize(None)
        obs = obs.merge(w, on="key", how="left").drop(columns="key")
    for k, v in {"temperature_2m": 15.0, "precipitation": 0.0, "snowfall": 0.0, "weather_code": 1}.items():
        if k not in obs or obs[k].isna().all():
            log(f"note: no weather for {k}, using a mild dry default ({v})")
            obs[k] = v
        obs[k] = obs[k].fillna(v)
    preds = predict_frame(obs[["timestamp_local", "temperature_2m", "precipitation", "snowfall", "weather_code"]]
                          .drop_duplicates("timestamp_local"))
    # predict_frame applies any EXISTING calibration; we want the raw campus estimate
    existing = load_calibration()
    results, params = [], dict(existing)
    for lot, g in obs.groupby("lot"):
        if lot not in DUKE_CAPACITIES:
            log(f"[{lot}] unknown lot name, skipped. Use one of {list(DUKE_CAPACITIES)}")
            continue
        cap = DUKE_CAPACITIES[lot]
        pr = preds[preds.lot == lot][["timestamp_local", "median_rate"]]
        g = g.assign(ts=g.timestamp_local.astype(str)).merge(pr, left_on="ts", right_on="timestamp_local",
                                                             suffixes=("", "_p"))
        if lot in existing:  # undo the old correction so we fit on the raw estimate
            g["median_rate"] = (g["median_rate"] - existing[lot]["offset"]) / existing[lot]["scale"]
        g = g.sort_values("timestamp_local")
        y = (g.occupancy_count / cap).clip(0, 1).values
        x = g.median_rate.values
        n, ndays = len(g), g.timestamp_local.dt.date.nunique()
        before = np.mean(np.abs(x - y)) * cap
        if n < c["min_points_per_lot"] or ndays < c["min_distinct_days"]:
            msg = (f"REFUSED: only {n} readings over {ndays} days (need {c['min_points_per_lot']} readings "
                   f"over {c['min_distinct_days']} days). A scale and offset fitted to so few points would "
                   f"chase noise. Keep collecting.")
            log(f"[{lot}] error before correction {before:.2f} cars MAE. {msg}")
            results.append({"lot": lot, "n": n, "days": ndays, "MAE_cars_before": before, "status": msg})
            continue
        # Fit on the earliest 70% of readings, check on the latest 30%.
        k = int(n * 0.7)
        scale, offset = _fit_scale_offset(x[:k], y[:k])
        after = np.mean(np.abs(np.clip(scale * x[k:] + offset, 0, 1) - y[k:])) * cap
        before_test = np.mean(np.abs(x[k:] - y[k:])) * cap
        # final parameters use every reading
        scale_all, offset_all = _fit_scale_offset(x, y)
        params[lot] = {"scale": scale_all, "offset": offset_all, "n_points": int(n), "days": int(ndays)}
        log(f"[{lot}] n={n} over {ndays} days. On the latest 30% (not used to fit): MAE before "
            f"{before_test:.2f} cars, after {after:.2f} cars. Saved scale {scale_all:.3f}, offset {offset_all:+.3f}.")
        results.append({"lot": lot, "n": n, "days": ndays, "MAE_cars_before": before_test,
                        "MAE_cars_after": after, "scale": scale_all, "offset": offset_all, "status": "fitted"})
    if save:
        CALIB_PATH.write_text(json.dumps(params, indent=2))
    return pd.DataFrame(results)


def _fit_scale_offset(x, y, ridge=None):
    """Least squares y = scale*x + offset, pulled toward scale=1, offset=0
    (a 'ridge' penalty) and kept inside sensible limits. This is what stops
    a handful of odd readings from bending the whole curve."""
    lam = ridge if ridge is not None else CFG["calibration"].get("ridge", 5.0)
    A = np.c_[x, np.ones_like(x)]
    prior = np.array([1.0, 0.0])
    coef = np.linalg.solve(A.T @ A + lam * np.eye(2), A.T @ y + lam * prior)
    scale = float(np.clip(coef[0], 0.5, 1.5))
    offset = float(np.clip(coef[1], -0.3, 0.3))
    return scale, offset


# =====================================================================
# MAIN
# =====================================================================
def main():
    OUT.mkdir(parents=True, exist_ok=True)
    section("STEP 1: setup")
    log(f"data folder: {DATA}\noutput folder: {OUT}\nrandom seed: {SEED}")
    log(f"model features: {FEATURES}\nplaceholders kept OUT of the model: {PLACEHOLDERS}")

    frames = {}
    b = load_birmingham()
    if b is not None:
        frames["birmingham"] = b
    hg = load_hague()
    if hg is not None:
        frames["hague"] = hg
    if not frames:
        sys.exit("No data loaded. Run fetch_data.py first.")
    if "hague" not in frames:
        log("\n*** THE HAGUE IS NOT LOADED. EVERY RESULT BELOW IS BIRMINGHAM ONLY. ***")

    section("STEP 3 + 4: common hourly table with features")
    parts = []
    for place, df in frames.items():
        h = to_hourly(df)
        log(f"[{place}] {len(df):,} readings -> {len(h):,} lot-hours")
        parts.append(add_features(h, place))
    H = pd.concat(parts, ignore_index=True)
    before = len(H)
    H = H.dropna(subset=FEATURES + ["occupancy_rate"])
    log(f"rows dropped for missing weather: {before - len(H):,}")
    H.to_csv(OUT / "hourly_table.csv", index=False)
    log(f"combined hourly table: {len(H):,} rows, {H.lot_key.nunique()} lots, columns {list(H.columns)}")
    log(H.groupby("source").agg(rows=("lot_key", "size"), lots=("lot_key", "nunique"),
                                first=("timestamp_local", "min"), last=("timestamp_local", "max")).to_string())
    log("hours of day with data, by source:")
    for src, g in H.groupby("source"):
        log(f"  {src}: {sorted(int(x) for x in g.hour.unique())}")

    section("STEP 5A: campus similarity of every lot (all data, for reporting and holdout choice)")
    sims_all = similarity_scores(H)
    sims_all.to_csv(OUT / "lot_similarity.csv", index=False)
    log(sims_all.round(3).to_string())

    groups, holdouts, b1, r1 = test1_lot_holdouts(H, sims_all)
    test2_place_transfer(H)
    test3_time(H, holdouts)
    test4_campus_blend(r1, H, groups, holdouts)

    section("RESULTS TABLE (all tests)")
    res = pd.DataFrame(RESULTS)
    cols = ["test", "held_out", "n_rows", "model_MAE", "baseline_MAE", "improvement_pct", "flat_MAE",
            "model_RMSE", "baseline_RMSE", "flat_RMSE", "coverage_raw", "coverage_adjusted",
            "MAE_cars_cap52", "MAE_cars_cap85", "MAE_cars_cap80", "MAE_cars_cap27", "note"]
    res = res.reindex(columns=cols)
    for c in CAR_CAPS:
        res[f"MAE_cars_cap{c}"] = res["model_MAE"] * c
    res.to_csv(OUT / "backtest_results.csv", index=False)
    with pd.option_context("display.width", 250, "display.max_colwidth", 60):
        log(res[["test", "model_MAE", "baseline_MAE", "improvement_pct", "flat_MAE", "coverage_adjusted",
                 "MAE_cars_cap52", "MAE_cars_cap27"]].round(4).to_string())
    pd.DataFrame(PER_LOT).to_csv(OUT / "backtest_per_lot.csv", index=False)
    blocks = pd.DataFrame(BLOCKS)
    blocks.to_csv(OUT / "errors_by_time_block.csv", index=False)
    log("\nErrors by time block:")
    log(blocks.round(4).to_string())
    pd.DataFrame(LOG).to_csv(OUT / "backtest_log.csv", index=False)
    pl = pd.DataFrame(PER_LOT)
    worse = pl[pl.model_MAE > pl.baseline_MAE]
    log(f"\nLOT-TEST PAIRS WHERE THE MODEL IS WORSE THAN BASELINE: {len(worse)}")
    if len(worse):
        log(worse.round(4).to_string())

    section("FINAL MODEL: fit on every lot, save")
    final = fit_bundle(H)
    final["config"] = CFG
    final["trained_on"] = sorted(H.source.unique())
    final["data_ranges"] = {s: [str(g.timestamp_local.min()), str(g.timestamp_local.max())] for s, g in H.groupby("source")}
    final["hours_with_data"] = {s: sorted(int(x) for x in g.hour.unique()) for s, g in H.groupby("source")}
    joblib.dump(final, MODEL_PATH)
    log(f"saved {MODEL_PATH} (band scale {final['band_scale']:.2f}, trained on {final['trained_on']})")

    section("STEP 8: Duke outputs")
    typical_weekday_charts()
    duke_forecast_csv()
    log("\nExample predict():")
    for lot in DUKE_CAPACITIES:
        log(json.dumps(predict(lot, "2026-11-10 10:00", {"temperature_2m": 14, "precipitation": 0})))
    log(json.dumps(predict("Trent Lot", "2026-11-10 03:00")))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        main()
    elif cmd == "calibrate":
        log(calibrate(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None).to_string())
    elif cmd == "predict":
        feed = sys.argv[4] if len(sys.argv) > 4 else None
        log(json.dumps(predict(sys.argv[2], sys.argv[3], live_feed=feed), indent=2))
    elif cmd == "live":
        # python3 park_shark.py live sensor_feed.csv "2026-11-10 09:00"
        with pd.option_context("display.width", 250, "display.max_columns", 30):
            log(live_compare(sys.argv[2], sys.argv[3]).drop(columns="method").to_string(index=False))
        log("\nThis shows how the estimate CHANGES with live data. It cannot show whether it got more "
            "ACCURATE: that needs real counts at these lots. Saved outputs/live_comparison.csv")
    elif cmd == "feed-to-counts":
        feed_to_counts(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    elif cmd == "export-app":
        # python3 park_shark.py export-app ../parkshark/data/tier3_forecast.json
        export_app(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == "nearby":
        # python3 park_shark.py nearby "2026-11-10 09:00" [sensor_feed.csv]
        feed = sys.argv[3] if len(sys.argv) > 3 else None
        with pd.option_context("display.width", 250, "display.max_columns", 30):
            log(nearby_now(sys.argv[2], feed).to_string(index=False))
        log("\nLots WITHOUT sensors: low confidence by design. typical_error_cars = typical miss in cars.")
    elif cmd == "example-feed":
        write_example_feed(sys.argv[2] if len(sys.argv) > 2 else None)
    else:
        print(__doc__)
