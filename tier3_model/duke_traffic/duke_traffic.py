"""
Park Shark: Duke only traffic model (separate from the Birmingham / Hague occupancy model).

Question it answers: at which hours and which of the four Duke lots will traffic be heaviest?

"Traffic" here has two parts, both in vehicles per hour:
  1. street_vph  = cars passing on the counted roads next to the lot, weighted by direction
                 = sum over roads of  weight x AADT x day_factor x hour_share x calendar factors x event boost
                   x (share toward campus x 1.0 + share away from campus x 0.5)
     The share toward campus comes from NCDOT's D factor (peak hour directional split).
  2. lot_moves   = cars driving into or out of the lot itself
                 = capacity x turnover x (arrival share + departure share) + class change churn
  total = street_vph + lot_moves
  index = total as a percent of the single busiest lot-hour on a normal in-session Tuesday (so 100 = a normal peak)

Every input lives in duke_traffic_inputs.json with a status tag (VERIFIED or ASSUMPTION).
Roads with "measured": true (Research Dr) use the team's own magnetometer counts from the
sensor feed instead of an NCDOT count (see measured_counts in the inputs file).
No Birmingham or Hague data is used anywhere in this file.

Usage:
  python3 duke_traffic.py                 # writes outputs/ (CSV + JSON for the dashboard)
  python3 duke_traffic.py 2026-11-17      # prints the ranked busiest hours for one day
"""
import csv
import json
import os
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

HERE = Path(__file__).resolve().parent
INPUTS = json.loads((HERE / "duke_traffic_inputs.json").read_text())
OUT = HERE / "outputs"

REF_LOTS = {l["name"] for l in INPUTS["lots"] if l.get("reference_lot", True)}

START = date(2026, 11, 7)
END = date(2027, 5, 16)


# ---------------------------------------------------------------- helpers
def pick_aadt(station, rule):
    """Return (year, value) for a station using the chosen year rule."""
    years = sorted(int(y) for y in station["aadt"])
    if rule == "latest_before_2020":
        years = [y for y in years if y < 2020] or years
    y = years[-1]
    return y, station["aadt"][str(y)]


def in_ranges(d, ranges):
    s = d.isoformat()
    for r in ranges:
        if r[0] <= s <= r[1]:
            return r[2]
    return None


def day_type(d):
    wd = d.weekday()  # Monday = 0
    return "saturday" if wd == 5 else "sunday" if wd == 6 else "weekday"


def day_context(d):
    """Calendar flags for one date, all from the inputs file."""
    cal = INPUTS["calendar"]
    s = d.isoformat()
    ctx = {
        "day_type": day_type(d),
        "no_class": in_ranges(d, cal["no_class_ranges"]),
        "exams": in_ranges(d, cal["exam_ranges"]),
        "holiday": s in cal["staff_holidays"],
        "events": [e for e in INPUTS["events"]["list"] if e["date"] == s],
        "untimed_events": [g["name"] for g in INPUTS["events"]["untimed"] if s in g["dates"]],
    }
    ctx["classes_on"] = ctx["day_type"] == "weekday" and not ctx["no_class"] and not ctx["exams"] and not ctx["holiday"]
    return ctx


def normalise(v):
    t = sum(v)
    return [x / t for x in v]


# ---------------------------------------------------------------- measured counts
def load_measured():
    """Hourly car counts from the team's own sensors, read from the sensor feed CSV.
    Returns {station: {"exact": {(date, hour): cars}, "typical": {(day_type, hour): average cars},
    "hours": number of usable hours}}. Empty if the file does not exist yet."""
    mc = INPUTS.get("measured_counts")
    if not mc:
        return {}
    path = Path(os.environ.get("PARK_SHARK_SENSOR_FEED", (HERE / mc["file"]).resolve()))
    if not path.exists():
        return {}
    to_station = {g: sid for sid, st in INPUTS["stations"].items() if st.get("measured")
                  for g in st.get("sensor_groups", [])}
    tz = ZoneInfo("America/New_York")
    cars, first, last = {}, {}, {}
    with open(path) as f:
        for r in csv.DictReader(line for line in f if not line.startswith("#")):
            if r.get("kind") != "vehicle_count" or r.get("group") not in to_station:
                continue
            try:
                t = datetime.fromisoformat(r["timestamp"].strip())
                v = float(r["value"])
            except (ValueError, KeyError):
                continue
            if t.tzinfo:
                t = t.astimezone(tz).replace(tzinfo=None)
            k = (to_station[r["group"]], t.date(), t.hour)
            cars[k] = cars.get(k, 0.0) + v
            m = t.minute + t.second / 60
            first[k] = min(first.get(k, 60), m)
            last[k] = max(last.get(k, 0), m)
    need = mc["min_minutes_per_hour"]["value"]
    out = {}
    for k, v in cars.items():
        if last[k] - first[k] < need:
            continue   # too little of this hour was recorded
        sid, d, h = k
        o = out.setdefault(sid, {"exact": {}, "sum": {}, "n": {}})
        o["exact"][(d, h)] = v
        dt = (day_type(d), h)
        o["sum"][dt] = o["sum"].get(dt, 0.0) + v
        o["n"][dt] = o["n"].get(dt, 0) + 1
    for o in out.values():
        o["typical"] = {k: o["sum"][k] / o["n"][k] for k in o["sum"]}
        o["hours"] = len(o["exact"])
    return out


MEASURED = None


def measured():
    global MEASURED
    if MEASURED is None:
        MEASURED = load_measured()
    return MEASURED


# ---------------------------------------------------------------- model
def toward_share(st_id, hour, ctx):
    """Share of this road's traffic heading toward campus in this hour (uses NCDOT D factor)."""
    if ctx["day_type"] != "weekday":
        return 0.5
    D = INPUTS["stations"][st_id]["d_factor"]["value"]
    p = INPUTS["direction"]["peak_pattern"].get(str(hour), 0)
    return 0.5 + (D - 0.5) * p


def street_vph(lot, hour, ctx, rule):
    """Returns (weighted street traffic, cars toward campus, cars away from campus) per hour."""
    sh = INPUTS["street_hour_share"]
    dr = INPUTS["direction"]
    w_in, w_out = dr["weight_toward_campus"]["value"], dr["weight_away_from_campus"]["value"]
    cal = INPUTS["calendar"]
    ev = INPUTS["events"]
    share = normalise(sh[ctx["day_type"]])[hour]
    dfac = INPUTS["day_factor"][ctx["day_type"]]
    total = t_in = t_out = 0.0
    w_all = w_used = 0.0
    for r in lot["roads"]:
        st_id = r["station"]
        st = INPUTS["stations"][st_id]
        w_all += r["weight"]
        if st.get("measured"):
            # Our own sensor: the exact count for this date and hour if we have it ...
            m = measured().get(st_id)
            exact = m["exact"].get((ctx["_date"], hour)) if m else None
            if exact is not None:
                v = exact * r["weight"]
                f_in = toward_share(st_id, hour, ctx)
                t_in += v * f_in
                t_out += v * (1 - f_in)
                total += v * (f_in * w_in + (1 - f_in) * w_out)
                w_used += r["weight"]
                continue   # a real count already includes holidays and events
            # ... else the average measured count for this kind of day and hour ...
            typ = m["typical"].get((ctx["day_type"], hour)) if m else None
            if typ is None:
                continue   # ... else skip this road; the other counted roads take its weight
            v = typ * r["weight"]
        else:
            _, aadt = pick_aadt(st, rule)
            v = aadt * dfac * share * r["weight"]
        w_used += r["weight"]
        if ctx["holiday"]:
            v *= cal["holiday_road_factor"]["value"]
        elif ctx["no_class"] and st_id in cal["campus_roads"] and ctx["day_type"] == "weekday":
            v *= cal["no_class_campus_road_factor"]["value"]
        for e in ctx["events"]:
            lo = e["start"] - ev["window_before_hours"]
            hi = e["start"] + ev["window_after_hours"]
            # the hour [hour, hour+1) overlaps the event window
            if st_id in e["roads"] and hour + 1 > lo and hour < hi:
                v *= e.get("boost", ev["boost"]["value"])
        f_in = toward_share(st_id, hour, ctx)
        t_in += v * f_in
        t_out += v * (1 - f_in)
        total += v * (f_in * w_in + (1 - f_in) * w_out)
    if 0 < w_used < w_all:   # weights split over the roads that have a count
        k = w_all / w_used
        total, t_in, t_out = total * k, t_in * k, t_out * k
    return total, t_in, t_out


def lot_moves(lot, hour, ctx):
    lm = INPUTS["lot_moves"]
    cal = INPUTS["calendar"]
    cap = lot["capacity"]
    if ctx["day_type"] == "weekday":
        m = cap * lm["turnover_weekday"] * (lm["arrive_weekday"][hour] + lm["depart_weekday"][hour])
        if lot.get("class_turnover") and ctx["classes_on"]:
            # Mon/Wed/Fri vs Tue/Thu class grids (Duke registrar)
            grid = "tth" if ctx_weekday(ctx) in (1, 3) else "mwf"
            if hour in lm["class_change_hours"][grid]:
                m += cap * lm["class_change_extra_per_change"]
        if ctx["holiday"]:
            m *= cal["holiday_lot_factor"]["value"]
        elif ctx["no_class"]:
            m *= cal["no_class_lot_factor"]["value"]
    else:
        hrs = lm["weekend_hours"]
        m = (cap * lm["turnover_weekday"] * lm["weekend_fraction"] * 2 / len(hrs)) if hour in hrs else 0.0
    return m


def ctx_weekday(ctx):
    return ctx["_weekday"]


def day_rows(d, rule):
    ctx = day_context(d)
    ctx["_weekday"] = d.weekday()
    ctx["_date"] = d
    rows = []
    for lot in INPUTS["lots"]:
        for h in range(24):
            s, s_in, s_out = street_vph(lot, h, ctx, rule)
            m = lot_moves(lot, h, ctx)
            rows.append({"date": d.isoformat(), "lot": lot["name"], "hour": h,
                         "street_vph": s, "street_in": s_in, "street_out": s_out,
                         "lot_moves": m, "total": s + m})
    return rows, ctx


def reference_peak(rule):
    """Busiest single lot-hour on a normal in-session Tuesday (Nov 10, 2026)."""
    rows, ctx = day_rows(date(2026, 11, 10), rule)
    assert ctx["classes_on"] and not ctx["events"], "reference day must be a plain class day"
    return max(r["total"] for r in rows if r["lot"] in REF_LOTS)


# ---------------------------------------------------------------- outputs
def run(rule=None):
    rule = rule or INPUTS["aadt_year_rule"]["value"]
    OUT.mkdir(exist_ok=True)
    ref = reference_peak(rule)
    all_rows, days = [], []
    d = START
    while d <= END:
        rows, ctx = day_rows(d, rule)
        for r in rows:
            r["index"] = 100 * r["total"] / ref
        all_rows += rows
        days.append({"date": d.isoformat(), "day_type": ctx["day_type"], "no_class": ctx["no_class"],
                     "exams": ctx["exams"], "holiday": ctx["holiday"],
                     "events": [e["name"] for e in ctx["events"]], "untimed_events": ctx["untimed_events"]})
        d += timedelta(days=1)

    with open(OUT / "duke_traffic_by_hour.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "lot", "hour", "street_vph", "street_in", "street_out", "lot_moves", "total", "index"])
        w.writeheader()
        for r in all_rows:
            w.writerow({k: (int(round(v)) if isinstance(v, float) else v) for k, v in r.items()})

    # Top 25 busiest lot-hours over the whole range
    top = sorted(all_rows, key=lambda r: -r["total"])[:25]
    with open(OUT / "duke_traffic_top25.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "lot", "hour", "street_vph", "street_in", "street_out", "lot_moves", "total", "index"])
        w.writeheader()
        for r in top:
            w.writerow({k: (int(round(v)) if isinstance(v, float) else v) for k, v in r.items()})

    (OUT / "duke_traffic_days.json").write_text(json.dumps(days, indent=1))
    write_reference_tables(days, rule)
    print(f"AADT rule: {rule}. Reference peak (normal Tuesday, original four lots): {ref:.0f} vehicles/hour.")
    m = measured()
    print("Measured road counts: " + (", ".join(f"{k} ({v['hours']} hours)" for k, v in m.items()) if m
                                      else "none yet (no sensor feed file), Research Dr skipped"))
    print(f"Wrote {len(all_rows)} rows for {len(days)} days to {OUT}/")
    return ref


def _csv(name, fields, rows):
    with open(OUT / name, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def write_reference_tables(days, rule):
    """Small tables the dashboard shows next to the model output."""
    # Calendar flags per day
    rows = []
    for d in days:
        cal = [x for x in [d["no_class"], d["exams"], "Staff holiday" if d["holiday"] else None] if x]
        rows.append({"date": d["date"], "day_type": d["day_type"],
                     "calendar": "; ".join(cal) or "Classes in session" if d["day_type"] == "weekday" else "; ".join(cal) or "Weekend",
                     "events": "; ".join(d["events"] + d["untimed_events"])})
    _csv("dash_days.csv", ["date", "day_type", "calendar", "events"], rows)

    # Count stations actually used, with the year the rule picked
    used = {}
    for lot in INPUTS["lots"]:
        for r in lot["roads"]:
            used.setdefault(r["station"], []).append(lot["name"])
    rows = []
    for sid, st in INPUTS["stations"].items():
        if st.get("measured"):
            m = measured().get(sid)
            rows.append({"station_id": st["station_id"], "road": st["road"], "location": st["location"],
                         "year_used": "measured", "aadt_used": f'{m["hours"]} hours counted' if m else "no counts yet",
                         "d_factor": st["d_factor"]["value"], "d_status": st["d_factor"]["status"],
                         "lots": "; ".join(used.get(sid, [])) or "Not used (context only)",
                         "lat": st["lat"], "lon": st["lon"],
                         "status": "MEASURED (team sensor)" if m else "WAITING FOR SENSOR DATA", "source": st["source"]})
            continue
        y, v = pick_aadt(st, rule)
        rows.append({"station_id": st["station_id"], "road": st["road"], "location": st["location"],
                     "year_used": y, "aadt_used": v, "d_factor": st["d_factor"]["value"], "d_status": st["d_factor"]["status"],
                     "lots": "; ".join(used.get(sid, [])) or "Not used (context only)",
                     "lat": st["lat"], "lon": st["lon"], "status": st["status"],
                     "source": st.get("source_2023_2025", st["source"]) if y >= 2023 else st["source"]})
    _csv("dash_stations.csv", ["station_id", "road", "location", "year_used", "aadt_used", "d_factor", "d_status", "lots", "lat", "lon", "status", "source"], rows)

    # Lots
    rows = [{"lot": l["name"], "capacity": l["capacity"], "anchor": l["anchor"], "lat": l["lat"], "lon": l["lon"],
             "roads": "; ".join(f'{INPUTS["stations"][r["station"]]["road"]} ({r["weight"]:g})' for r in l["roads"]),
             "note": l["note"]} for l in INPUTS["lots"]]
    _csv("dash_lots.csv", ["lot", "capacity", "anchor", "lat", "lon", "roads", "note"], rows)

    # Assumptions and verified inputs, one line each
    I = INPUTS
    lm, cal, ev = I["lot_moves"], I["calendar"], I["events"]
    pct = lambda v: f"{v * 100:.0f}%"
    a = [
        ("Daily traffic per road", "NCDOT annual average daily traffic, latest published year (2025 where available)", "VERIFIED", "Count stations near each lot. Science Dr and Towerview Rd stop at 2017.", I["stations"]["trent_s_elba"]["source_2023_2025"]),
        ("Direction split on Erwin Rd (D factor)", "0.675 west of Fulton St, 0.687 east of Fulton St (by Trent Lot)", "VERIFIED", "NCDOT 2025 traffic segments: share of peak hour traffic going the busier way.", I["direction"]["source"]),
        ("Direction split on other roads", "0.65 on Trent Dr, Duke University Rd, Science Dr, Towerview Rd", "ASSUMPTION", "No D factor published for these segments; set just under the Erwin Rd values.", ""),
        ("Which way the busy flow goes", "Toward campus 6 to 10am, away from campus 3 to 7pm, even otherwise and on weekends", "ASSUMPTION", I["direction"]["why"], ""),
        ("Direction weights", f'Toward campus x{I["direction"]["weight_toward_campus"]["value"]}, away x{I["direction"]["weight_away_from_campus"]["value"]}', "ASSUMPTION", I["direction"]["weight_away_from_campus"]["why"], ""),
        ("Peak hour check (K factor)", "NCDOT Erwin Rd K = 0.085 to 0.100; model weekday peak = about 0.087", "VERIFIED", I["direction"]["k_factor_check"], I["direction"]["source"]),
        ("Which roads feed each lot", "Equal split between the counted roads next to the lot", "ASSUMPTION", I["road_weights_status"]["why"], ""),
        ("Hour by hour street pattern", "Weekday peaks at 7 to 9 and 15 to 18; weekend peaks midday", "ASSUMPTION", I["street_hour_share"]["why"], ""),
        ("Weekday vs weekend traffic", f'Weekday x{I["day_factor"]["weekday"]}, Saturday x{I["day_factor"]["saturday"]}, Sunday x{I["day_factor"]["sunday"]}', "ASSUMPTION", I["day_factor"]["why"], ""),
        ("Lot capacity", "Science 52, Public Policy 85, Trent 80, IM 27, Bryan Research Garage 163, Research Drive Garage Reserved 239 spaces", "VERIFIED", "parking.duke.edu permit and garages pages", "https://parking.duke.edu/parking-permits/garages-lots-fees/"),
        ("Research Dr traffic", ("measured by the team magnetometer" if measured() else "no count yet (road skipped, Erwin Rd carries the weight)"), "VERIFIED" if measured() else "PARTIAL", I["measured_counts"]["how_used"], ""),
        ("Magnetometer counts both directions", str(I["measured_counts"]["counts_both_directions"]["value"]), "ASSUMPTION", I["measured_counts"]["counts_both_directions"]["why"], ""),
        ("Cars in and out of each lot", f'Each space turns over {lm["turnover_weekday"]} times on a weekday; weekends {pct(lm["weekend_fraction"])} of that', "ASSUMPTION", lm["why"], ""),
        ("Class change times", lm["class_change_note"], "VERIFIED", "Extra churn per class change is an assumption: " + pct(lm["class_change_extra_per_change"]) + " of the lot's spaces.", lm["class_change_hours_source"]),
        ("Academic calendar", "Thanksgiving, reading periods, exams, winter break, spring recess", "VERIFIED", "Duke 2026-2027 academic calendar", cal["source"]),
        ("Lot activity on no class days", f'x{cal["no_class_lot_factor"]["value"]}', "ASSUMPTION", cal["no_class_lot_factor"]["why"], ""),
        ("Campus road traffic on no class days", f'x{cal["no_class_campus_road_factor"]["value"]} on Science Dr and Towerview Rd', "ASSUMPTION", cal["no_class_campus_road_factor"]["why"], ""),
        ("Staff holidays", f'Lots x{cal["holiday_lot_factor"]["value"]}, roads x{cal["holiday_road_factor"]["value"]}', "ASSUMPTION", cal["staff_holidays_status"], ""),
        ("Game and commencement dates and start times", "Football, men's basketball, commencement", "VERIFIED", "goduke.com schedules and commencement.duke.edu", "https://goduke.com/sports/mens-basketball/schedule/2026-27"),
        ("Event traffic boost", f'Street traffic x{ev["boost"]["value"]} (x2.0 for football, UNC and commencement) from {ev["window_before_hours"]} hours before to {ev["window_after_hours"]} hours after the start', "ASSUMPTION", ev["window_status"], "https://parking.duke.edu/visitor-parking/athletic-event-parking/mens-basketball-parking/"),
        ("Women's basketball", "Home dates flagged, no boost", "PARTIAL", "Start times could not be read.", "https://goduke.com/sports/womens-basketball/schedule/2026-27"),
    ]
    _csv("dash_assumptions.csv", ["item", "value", "status", "why", "source"],
         [dict(zip(["item", "value", "status", "why", "source"], r)) for r in a])

    # Google typical traffic, read by eye on Nov 7, 2026
    g = [("Tuesday", t) for t in ["6:10", "7:00", "8:00", "8:55", "12:00", "15:50", "16:55", "17:55"]] + [("Saturday", t) for t in ["11:50", "17:55"]]
    _csv("dash_google.csv", ["day", "time", "erwin_rd", "duke_university_rd", "campus_roads"],
         [{"day": d, "time": t, "erwin_rd": "Green", "duke_university_rd": "Green", "campus_roads": "No colour shown"} for d, t in g])


def show_day(s, rule=None):
    rule = rule or INPUTS["aadt_year_rule"]["value"]
    ref = reference_peak(rule)
    rows, ctx = day_rows(date.fromisoformat(s), rule)
    flags = [x for x in [ctx["no_class"], ctx["exams"], "staff holiday" if ctx["holiday"] else None] if x]
    flags += [e["name"] for e in ctx["events"]] + ctx["untimed_events"]
    print(f"{s} ({ctx['day_type']}) {'; '.join(flags) or 'normal day'}")
    print(f"{'lot':24} {'hour':>5} {'street':>7} {'lot in/out':>10} {'total':>7} {'index':>6}")
    for r in sorted(rows, key=lambda r: -r["total"])[:10]:
        print(f"{r['lot']:24} {r['hour']:>4}h {r['street_vph']:7.0f} {r['lot_moves']:10.0f} {r['total']:7.0f} {100*r['total']/ref:6.0f}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        show_day(sys.argv[1])
    else:
        run()
