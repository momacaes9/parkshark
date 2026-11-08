"""Build Park_Shark_Duke_Traffic.xlsx from the Duke traffic model inputs and outputs."""
import csv
import json
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

HERE = Path(__file__).resolve().parent
I = json.loads((HERE / "duke_traffic_inputs.json").read_text())
OUT = Path("/mnt/user-data/outputs/Park_Shark_Duke_Traffic.xlsx")

F = "Arial"
f_base = Font(name=F, size=10)
f_bold = Font(name=F, size=10, bold=True)
f_title = Font(name=F, size=14, bold=True)
f_note = Font(name=F, size=9, italic=True, color="555555")
f_input = Font(name=F, size=10, color="0000FF")
f_link = Font(name=F, size=10, color="008000")
f_head = Font(name=F, size=10, bold=True, color="FFFFFF")
fill_head = PatternFill("solid", fgColor="1F3A5F")
fill_sub = PatternFill("solid", fgColor="DCE6F1")
fill_assume = PatternFill("solid", fgColor="FFF2CC")
fill_verified = PatternFill("solid", fgColor="E2EFDA")
thin = Side(style="thin", color="BFBFBF")
box = Border(bottom=thin)
wrap = Alignment(wrap_text=True, vertical="top")

wb = Workbook()


def header(ws, row, labels, col=1):
    for i, h in enumerate(labels):
        c = ws.cell(row=row, column=col + i, value=h)
        c.font, c.fill = f_head, fill_head
        c.alignment = Alignment(wrap_text=True, vertical="center")


def widths(ws, w):
    for i, x in enumerate(w, start=1):
        ws.column_dimensions[get_column_letter(i)].width = x


def all_font(ws):
    for row in ws.iter_rows():
        for c in row:
            if c.font is None or c.font.name != F:
                c.font = Font(name=F, size=c.font.size if c.font else 10, bold=c.font.bold if c.font else False,
                              italic=c.font.italic if c.font else False, color=c.font.color if c.font else None)


# ------------------------------------------------------------------ README
ws = wb.active
ws.title = "README"
rows = [
    ("Park Shark: Duke lot traffic and open spots", f_title),
    ("Built Nov 7, 2026 from duke_traffic.py (Duke data only; nothing from the Birmingham or Hague data) plus the earlier occupancy model's forecast for comparison.", f_note),
    ("", None),
    ("Tabs", f_bold),
    ("Assumptions: every input to the model. Blue numbers are inputs you can change; the Typical Weekday and Open Spots tabs update automatically.", f_base),
    ("Typical Weekday: a normal class Tuesday, built with live formulas: street traffic, cars in and out, total, cars parked and open spots for each lot and hour.", f_base),
    ("Open Spots Compare: open spots on Tue Nov 10 from this model (live) next to the earlier occupancy model's forecast and its range.", f_base),
    ("Occupancy Forecast: the earlier model's predicted cars for Nov 10 (values copied from outputs/duke_forecast_next_7_days.csv).", f_base),
    ("Hourly All Days: every lot, hour and day from Nov 7, 2026 to May 16, 2027, as values from duke_traffic.py (includes games, breaks and holidays). Use the filter arrows.", f_base),
    ("Top 25: the 25 busiest lot hours of the school year.", f_base),
    ("Calendar & Events: each day's class status plus home games and commencement with start times.", f_base),
    ("Road Counts: all 19 NCDOT count stations in the search box, every published year.", f_base),
    ("Google Typical Traffic: colours read by eye from Google Maps on Nov 7, 2026.", f_base),
    ("Sources & Status: each input tagged VERIFIED, PARTIAL or ASSUMPTION with its source link.", f_base),
    ("", None),
    ("Colour key", f_bold),
    ("Blue text = an input you can change. Black text = a formula. Green text = a link to another tab.", f_base),
    ("Yellow fill = an assumption (our guess). Green fill = read from a real source.", f_base),
    ("", None),
    ("Units", f_bold),
    ("Street traffic, cars in and out, and total are vehicles per hour; street totals are direction weighted. Index 100 = the busiest lot hour on a normal class Tuesday (Trent Lot, 08:00).", f_base),
    ("Open spots = lot spaces minus cars parked. Cars parked = spaces x turnover x (arrivals so far minus departures so far), kept between 0 and the lot size.", f_base),
    ("", None),
    ("Big caveats", f_bold),
    ("No real count has been taken at any of these lots. The hour by hour pattern is assumed because no hourly road counts could be found.", f_base),
    ("Street traffic is much larger than lot traffic, so Trent Lot (next to Erwin Rd and Trent Dr) ranks busiest on almost every normal day.", f_base),
    ("Direction: NCDOT's D factor (share of rush hour traffic going the busier way) splits each road into toward campus and away from campus. Verified on Erwin Rd (0.675 and 0.687); assumed 0.65 elsewhere. Which way is busier (toward campus in the morning, away in the evening) is an assumption.", f_base),
    ("Street traffic heading toward campus counts fully and traffic heading away counts half, because leaving cars cannot take a space.", f_base),
]
for i, (t, f) in enumerate(rows, start=1):
    c = ws.cell(row=i, column=1, value=t)
    c.font = f or f_base
ws.column_dimensions["A"].width = 150

# ------------------------------------------------------------------ Assumptions
A = wb.create_sheet("Assumptions")
A["A1"], A["A1"].font = "Model inputs", f_title
A["A2"], A["A2"].font = "Blue = change me. Yellow fill = assumption, green fill = verified. Source links are on the Sources & Status tab.", f_note

# Stations rows 5-10
A["A4"], A["A4"].font = "Road counts used (NCDOT annual average daily traffic, both directions)", f_bold
header(A, 4, ["Key", "Station ID", "Road", "Where", "Year used", "Cars per day", "Status", "Source", "D factor (peak direction share)", "D factor status"], col=1)
A["A3"], A["A3"].font = "Road counts used (NCDOT annual average daily traffic, both directions)", f_bold
st_keys = ["science", "towerview", "trent_s_elba", "erwin_w_anderson", "duke_univ_rd", "erwin_e_douglas"]
for i, k in enumerate(st_keys):
    s = I["stations"][k]
    y = max(int(x) for x in s["aadt"])
    r = 5 + i
    vals = [k, s["station_id"], s["road"], s["location"], y, s["aadt"][str(y)], "VERIFIED",
            s.get("source_2023_2025", s["source"]) if y >= 2023 else s["source"], s["d_factor"]["value"], s["d_factor"]["status"]]
    for j, v in enumerate(vals, start=1):
        c = A.cell(row=r, column=j, value=v)
        c.font = f_base
    A.cell(row=r, column=6).font = f_input
    A.cell(row=r, column=6).fill = fill_verified
    A.cell(row=r, column=6).number_format = "#,##0"
    A.cell(row=r, column=9).font = f_input
    A.cell(row=r, column=9).fill = fill_verified if s["d_factor"]["status"] == "VERIFIED" else fill_assume

# Day factors rows 13-15
A["A12"], A["A12"].font = "Weekday vs weekend factor (multiplies the daily count)", f_bold
df = I["day_factor"]
for r, (lab, k) in zip([13, 14, 15], [("Weekday", "weekday"), ("Saturday", "saturday"), ("Sunday", "sunday")]):
    A.cell(row=r, column=1, value=lab).font = f_base
    c = A.cell(row=r, column=2, value=df[k])
    c.font, c.fill = f_input, fill_assume
A["C16"], A["C16"].font = df["why"], f_note
dr = I["direction"]
A["D12"], A["D12"].font = "Direction weights", f_bold
for r, lab, v in [(13, "Weight toward campus", dr["weight_toward_campus"]["value"]), (14, "Weight away from campus", dr["weight_away_from_campus"]["value"])]:
    A.cell(row=r, column=4, value=lab).font = f_base
    c = A.cell(row=r, column=5, value=v)
    c.font, c.fill = f_input, fill_assume
A["D15"], A["D15"].font = "Share toward campus = 0.5 + (D factor - 0.5) x direction pattern (column J below).", f_note

# Lot settings rows 18-20
lm = I["lot_moves"]
A["A17"], A["A17"].font = "Lot behaviour", f_bold
for r, lab, v, note in [
    (18, "Turnover (cars per space per weekday)", lm["turnover_weekday"], "Above 1 means some spaces are used by more than one car a day."),
    (19, "Weekend activity (share of weekday)", lm["weekend_fraction"], "Used on the Hourly All Days tab only."),
    (20, "Extra churn per class change (share of spaces)", lm["class_change_extra_per_change"], "Science and Public Policy lots only, at Duke class start times."),
]:
    A.cell(row=r, column=1, value=lab).font = f_base
    c = A.cell(row=r, column=2, value=v)
    c.font, c.fill = f_input, fill_assume
    A.cell(row=r, column=3, value=note).font = f_note

# Lots rows 23-26
A["A22"], A["A22"].font = "Lots and the roads that feed them", f_bold
header(A, 22, ["Lot", "Spaces", "Road 1 key", "Road 1 weight", "Road 2 key", "Road 2 weight", "Class churn (1 = yes)", "Note"], col=1)
A["A21"], A["A21"].font = "Lots and the roads that feed them", f_bold
A["A22"].value = "Lot"
for i, lot in enumerate(I["lots"]):
    r = 23 + i
    rd = lot["roads"] + [{"station": "", "weight": 0}]
    vals = [lot["name"], lot["capacity"], rd[0]["station"], rd[0]["weight"], rd[1]["station"], rd[1]["weight"],
            1 if lot.get("class_turnover") else 0, lot["note"]]
    for j, v in enumerate(vals, start=1):
        c = A.cell(row=r, column=j, value=v)
        c.font = f_input if j in (2, 3, 4, 5, 6, 7) else f_base
    A.cell(row=r, column=2).fill = fill_verified
    for j in (4, 6):
        A.cell(row=r, column=j).fill = fill_assume

# Hourly table rows 30-53
A["A28"], A["A28"].font = "Hour by hour shares (0 = midnight to 1am). Street shares are rescaled to add to 100% in column E.", f_bold
header(A, 29, ["Hour", "Street share weekday", "Street share Saturday", "Street share Sunday",
               "Weekday share rescaled", "Lot arrivals share", "Lot departures share",
               "Tue/Thu class start (1/0)", "Mon/Wed/Fri class start (1/0)", "Direction pattern (+1 toward campus peak, -1 away)"], col=1)
sh = I["street_hour_share"]
for h in range(24):
    r = 30 + h
    A.cell(row=r, column=1, value=h).font = f_base
    for j, k in [(2, "weekday"), (3, "saturday"), (4, "sunday")]:
        c = A.cell(row=r, column=j, value=sh[k][h])
        c.font, c.fill, c.number_format = f_input, fill_assume, "0.0%"
    c = A.cell(row=r, column=5, value=f"=B{r}/SUM($B$30:$B$53)")
    c.font, c.number_format = f_base, "0.0%"
    for j, arr in [(6, lm["arrive_weekday"]), (7, lm["depart_weekday"])]:
        c = A.cell(row=r, column=j, value=arr[h])
        c.font, c.fill, c.number_format = f_input, fill_assume, "0%"
    for j, grid in [(8, "tth"), (9, "mwf")]:
        c = A.cell(row=r, column=j, value=1 if h in lm["class_change_hours"][grid] else 0)
        c.font, c.fill = f_input, fill_verified
    c = A.cell(row=r, column=10, value=dr["peak_pattern"].get(str(h), 0))
    c.font, c.fill = f_input, fill_assume
A["A54"], A["A54"].font = "Total", f_bold
for col in "BCDEFG":
    c = A[f"{col}54"]
    c.value = f"=SUM({col}30:{col}53)"
    c.font, c.number_format = f_bold, "0.0%"
A["A55"], A["A55"].font = "Arrivals and departures should each total 100%. Class start hours are VERIFIED from registrar.duke.edu; the churn size is an assumption.", f_note
widths(A, [34, 14, 16, 26, 16, 14, 18, 18, 18, 18])
A.freeze_panes = "A5"

# ------------------------------------------------------------------ Typical Weekday
T = wb.create_sheet("Typical Weekday")
T["A1"], T["A1"].font = "Typical class Tuesday (no games, classes in session)", f_title
T["A2"], T["A2"].font = "All formulas. Change inputs on the Assumptions tab and this updates. Vehicles per hour, except Cars parked and Open spots (cars).", f_note
sub = ["Street toward campus", "Street away from campus", "Street (direction weighted)", "Cars in and out", "Total", "Cars parked", "Open spots"]
NC = len(sub)
T.cell(row=5, column=1, value="Hour")
T["A5"].font, T["A5"].fill = f_head, fill_head
open_cols = {}
for i, lot in enumerate(I["lots"]):
    base = 2 + i * NC
    lr = 23 + i  # lot row on Assumptions
    c = T.cell(row=4, column=base, value=lot["name"])
    c.font, c.fill = f_bold, fill_sub
    T.merge_cells(start_row=4, start_column=base, end_row=4, end_column=base + NC - 1)
    header(T, 5, sub, col=base)
    L = [get_column_letter(base + k) for k in range(NC)]
    open_cols[lot["name"]] = L[6]
    for h in range(24):
        r = 6 + h
        ar = 30 + h
        T.cell(row=r, column=1, value=h).font = f_base
        look = lambda col, key_cell: f"IFERROR(INDEX(Assumptions!${col}$5:${col}$10,MATCH({key_cell},Assumptions!$A$5:$A$10,0)),0)"
        vol = lambda kc, wc: f"{look('F', kc)}*{wc}*Assumptions!$B$13*Assumptions!$E${ar}"
        tw = lambda kc: f"(0.5+({look('I', kc)}-0.5)*Assumptions!$J${ar})"
        k1, w1, k2, w2 = f"Assumptions!$C${lr}", f"Assumptions!$D${lr}", f"Assumptions!$E${lr}", f"Assumptions!$F${lr}"
        s_in = f"={vol(k1, w1)}*{tw(k1)}+{vol(k2, w2)}*{tw(k2)}"
        s_out = f"={vol(k1, w1)}*(1-{tw(k1)})+{vol(k2, w2)}*(1-{tw(k2)})"
        s_w = f"={L[0]}{r}*Assumptions!$E$13+{L[1]}{r}*Assumptions!$E$14"
        moves = (f"=Assumptions!$B${lr}*Assumptions!$B$18*(Assumptions!$F${ar}+Assumptions!$G${ar})"
                 f"+Assumptions!$G${lr}*Assumptions!$B${lr}*Assumptions!$B$20*Assumptions!$H${ar}")
        total = f"={L[2]}{r}+{L[3]}{r}"
        parked = (f"=MIN(Assumptions!$B${lr},MAX(0,Assumptions!$B${lr}*Assumptions!$B$18*"
                  f"(SUM(Assumptions!$F$30:$F${ar})-SUM(Assumptions!$G$30:$G${ar}))))")
        opn = f"=Assumptions!$B${lr}-{L[5]}{r}"
        for k, v in enumerate([s_in, s_out, s_w, moves, total, parked, opn]):
            c = T.cell(row=r, column=base + k, value=v)
            c.font, c.number_format = f_base, "#,##0"
    # summary rows
    T.cell(row=31, column=base, value=f"=MAX({L[4]}6:{L[4]}29)").number_format = "#,##0"
    T.cell(row=32, column=base, value=f"=INDEX($A$6:$A$29,MATCH(MAX({L[4]}6:{L[4]}29),{L[4]}6:{L[4]}29,0))")
    T.cell(row=33, column=base, value=f"=MIN({L[6]}6:{L[6]}29)").number_format = "#,##0"
    T.cell(row=34, column=base, value=f"=SUM({L[0]}6:{L[1]}29)").number_format = "#,##0"
    for rr in (31, 32, 33, 34):
        T.cell(row=rr, column=base).font = f_bold
T["A31"], T["A32"], T["A33"], T["A34"] = "Busiest hour total", "Busiest hour", "Fewest open spots", "Street cars per day"
for rr in (31, 32, 33, 34):
    T[f"A{rr}"].font = f_bold
T["A35"], T["A35"].font = "Street cars per day (both directions) should equal the daily count x weekday factor (a check that the hourly shares add to 100%).", f_note
widths(T, [20] + [11] * 28)
T.freeze_panes = "B6"

# ------------------------------------------------------------------ Occupancy Forecast (values)
fc = [r for r in csv.DictReader(open("/mnt/user-data/uploads/park_shark/outputs/duke_forecast_next_7_days.csv"))
      if r["timestamp_local"].startswith("2026-11-10")]
O = wb.create_sheet("Occupancy Forecast")
O["A1"], O["A1"].font = "Earlier occupancy model: predicted cars on Tue Nov 10, 2026", f_title
O["A2"], O["A2"].font = ("Values copied from park_shark/outputs/duke_forecast_next_7_days.csv (trained on Birmingham and The Hague, blended with a campus template). "
                         "'Driven by' says whether the hour comes from the trained model or the campus assumption.", f_note)
header(O, 4, ["Lot", "Hour", "Predicted cars (median)", "Low (10th pct)", "High (90th pct)", "Spaces", "Driven by"])
for i, r in enumerate(fc):
    rr = 5 + i
    vals = [r["lot"], int(r["hour"]), int(r["median_cars"]), int(r["low_cars"]), int(r["high_cars"]), int(r["capacity"]), r["driven_by"]]
    for j, v in enumerate(vals, start=1):
        O.cell(row=rr, column=j, value=v).font = f_base
O_last = 4 + len(fc)
O.auto_filter.ref = f"A4:G{O_last}"
O.freeze_panes = "A5"
widths(O, [24, 8, 22, 14, 14, 10, 14])

# ------------------------------------------------------------------ Open Spots Compare
C = wb.create_sheet("Open Spots Compare", 3)
C["A1"], C["A1"].font = "Open spots on a normal class day (Tue Nov 10, 2026)", f_title
C["A2"], C["A2"].font = ("This model = live link to Typical Weekday. Occupancy model = spaces minus its predicted cars; its range runs from its high car count (fewest open) to its low car count (most open). "
                         "Neither has been checked against a real count.", f_note)
C["A5"].value = "Hour"
C["A5"].font, C["A5"].fill = f_head, fill_head
csub = ["This model", "Occupancy model", "Range low", "Range high"]
for i, lot in enumerate(I["lots"]):
    base = 2 + i * 4
    c = C.cell(row=4, column=base, value=f'{lot["name"]} ({lot["capacity"]} spaces)')
    c.font, c.fill = f_bold, fill_sub
    C.merge_cells(start_row=4, start_column=base, end_row=4, end_column=base + 3)
    header(C, 5, csub, col=base)
    oc = open_cols[lot["name"]]
    for h in range(24):
        r = 6 + h
        C.cell(row=r, column=1, value=h).font = f_base
        crit = f'Occupancy Forecast'
        look = lambda col: (f"SUMIFS('Occupancy Forecast'!${col}$5:${col}${O_last},'Occupancy Forecast'!$A$5:$A${O_last},\"{lot['name']}\","
                            f"'Occupancy Forecast'!$B$5:$B${O_last},$A{r})")
        cap = f"Assumptions!$B${23 + i}"
        vals = [f"='Typical Weekday'!{oc}{6 + h}", f"={cap}-{look('C')}", f"={cap}-{look('E')}", f"={cap}-{look('D')}"]
        for k, v in enumerate(vals):
            c = C.cell(row=r, column=base + k, value=v)
            c.number_format = "0"
            c.font = f_link if k == 0 else f_base
widths(C, [8] + [13] * 16)
C.freeze_panes = "B6"

# ------------------------------------------------------------------ Hourly All Days (values)
days = {d["date"]: d for d in csv.DictReader(open(HERE / "outputs" / "dash_days.csv"))}
H = wb.create_sheet("Hourly All Days")
header(H, 1, ["Date", "Day", "Lot", "Hour", "Street toward campus", "Street away from campus", "Street (direction weighted)", "Cars in and out", "Total", "Index (100 = normal Tue peak)", "Class status", "Events"])
n = 1
for r in csv.DictReader(open(HERE / "outputs" / "duke_traffic_by_hour.csv")):
    n += 1
    d = date.fromisoformat(r["date"])
    dd = days[r["date"]]
    vals = [d, d.strftime("%a"), r["lot"], int(r["hour"]), int(r["street_in"]), int(r["street_out"]), int(r["street_vph"]), int(r["lot_moves"]), int(r["total"]), int(r["index"]),
            dd["calendar"], dd["events"]]
    for j, v in enumerate(vals, start=1):
        c = H.cell(row=n, column=j, value=v)
        c.font = f_base
    H.cell(row=n, column=1).number_format = "yyyy-mm-dd"
H.auto_filter.ref = f"A1:L{n}"
H.freeze_panes = "A2"
widths(H, [12, 6, 22, 6, 13, 13, 14, 14, 10, 14, 30, 50])

# ------------------------------------------------------------------ Top 25
P = wb.create_sheet("Top 25")
P["A1"], P["A1"].font = "The 25 busiest lot hours, Nov 7, 2026 to May 16, 2027", f_title
header(P, 3, ["Rank", "Date", "Day", "Lot", "Hour", "Street (direction weighted)", "Cars in and out", "Total", "Index", "What is happening"])
for i, r in enumerate(csv.DictReader(open(HERE / "outputs" / "duke_traffic_top25.csv")), start=1):
    d = date.fromisoformat(r["date"])
    dd = days[r["date"]]
    vals = [i, d, d.strftime("%a"), r["lot"], int(r["hour"]), int(r["street_vph"]), int(r["lot_moves"]), int(r["total"]), int(r["index"]),
            dd["events"] or dd["calendar"]]
    for j, v in enumerate(vals, start=1):
        P.cell(row=3 + i, column=j, value=v).font = f_base
    P.cell(row=3 + i, column=2).number_format = "yyyy-mm-dd"
widths(P, [6, 12, 6, 22, 6, 13, 14, 10, 8, 50])

# ------------------------------------------------------------------ Calendar & Events
K = wb.create_sheet("Calendar & Events")
K["A1"], K["A1"].font = "Duke calendar and events", f_title
header(K, 3, ["Date", "Day", "Class status", "Events"])
for i, d in enumerate(csv.DictReader(open(HERE / "outputs" / "dash_days.csv"))):
    dt = date.fromisoformat(d["date"])
    for j, v in enumerate([dt, dt.strftime("%a"), d["calendar"], d["events"]], start=1):
        K.cell(row=4 + i, column=j, value=v).font = f_base
    K.cell(row=4 + i, column=1).number_format = "yyyy-mm-dd"
K.auto_filter.ref = f"A3:D{3 + len(days)}"
header(K, 3, ["Event", "Date", "Start (24 h)", "Roads boosted", "Boost", "Source"], col=7)
for i, e in enumerate(I["events"]["list"]):
    hh, mm = int(e["start"]), int(round((e["start"] % 1) * 60))
    vals = [e["name"], date.fromisoformat(e["date"]), f"{hh:02d}:{mm:02d}", ", ".join(e["roads"]), e.get("boost", I["events"]["boost"]["value"]), e["source"]]
    for j, v in enumerate(vals, start=7):
        K.cell(row=4 + i, column=j, value=v).font = f_base
    K.cell(row=4 + i, column=8).number_format = "yyyy-mm-dd"
    K.cell(row=4 + i, column=11).fill = fill_assume
r0 = 4 + len(I["events"]["list"]) + 1
K.cell(row=r0, column=7, value="Women's basketball home dates (start times not read, so no boost): " + ", ".join(I["events"]["untimed"][0]["dates"])).font = f_note
K.cell(row=r0 + 1, column=7, value="Event window: from 2 hours before the start to 3 hours after (assumption, based on Duke's basketball checkpoint rule).").font = f_note
widths(K, [12, 6, 30, 50, 2, 2, 38, 12, 11, 40, 7, 60])
K.freeze_panes = "A4"

# ------------------------------------------------------------------ Road Counts (all 19)
R = wb.create_sheet("Road Counts")
R["A1"], R["A1"].font = "All NCDOT count stations in the search box (lon -78.952 to -78.925, lat 35.994 to 36.012)", f_title
R["A2"], R["A2"].font = "Annual average daily traffic, both directions, 2002 to 2022 from the older NCDOT layer. Blank = not published that year (not zero). 2023 and 2025 counts for the stations the model uses are on the Assumptions tab (NCDOT 2025 AADT and traffic segments layer).", f_note
src = list(csv.DictReader(open("/mnt/user-data/uploads/output/park-shark-traffic/aadt_stations_all_years.csv")))
years = [str(y) for y in range(2002, 2023)]
used_ids = {I["stations"][k]["station_id"]: k for k in st_keys[:5]}
header(R, 4, ["Station ID", "Road", "Location", "Used by model"] + years + ["Source"])
for i, s in enumerate(src):
    r = 5 + i
    R.cell(row=r, column=1, value=s["station_id"]).font = f_base
    R.cell(row=r, column=2, value=s["road"]).font = f_base
    R.cell(row=r, column=3, value=s["location"]).font = f_base
    R.cell(row=r, column=4, value="Yes" if s["station_id"] in used_ids else "").font = f_base
    for j, y in enumerate(years):
        v = s[f"AADT_{y}"].strip()
        c = R.cell(row=r, column=5 + j, value=int(v) if v else None)
        c.font, c.number_format = f_base, "#,##0"
    R.cell(row=r, column=5 + len(years), value=s["source_url"]).font = f_base
widths(R, [13, 22, 34, 11] + [8] * len(years) + [60])
R.freeze_panes = "E5"

# ------------------------------------------------------------------ Google
G = wb.create_sheet("Google Typical Traffic")
G["A1"], G["A1"].font = "Google Maps Typical traffic, read by eye on Nov 7, 2026", f_title
G["A2"], G["A2"].font = "Source: Google Maps Traffic layer set to Typical traffic, centred on Duke West Campus. Campus roads = Science Dr, Towerview Rd inside campus, Flowers Dr, Wannamaker Dr, campus Trent Dr.", f_note
header(G, 4, ["Day", "Time", "Erwin Rd", "Duke University Rd", "Campus roads"])
for i, r in enumerate(csv.DictReader(open(HERE / "outputs" / "dash_google.csv"))):
    for j, k in enumerate(["day", "time", "erwin_rd", "duke_university_rd", "campus_roads"], start=1):
        G.cell(row=5 + i, column=j, value=r[k]).font = f_base
widths(G, [12, 8, 12, 20, 20])

# ------------------------------------------------------------------ Sources & Status
S = wb.create_sheet("Sources & Status")
S["A1"], S["A1"].font = "Every input, with its status and source", f_title
header(S, 3, ["Input", "Value", "Status", "Why", "Source"])
for i, r in enumerate(csv.DictReader(open(HERE / "outputs" / "dash_assumptions.csv"))):
    for j, k in enumerate(["item", "value", "status", "why", "source"], start=1):
        c = S.cell(row=4 + i, column=j, value=r[k])
        c.font, c.alignment = f_base, wrap
    S.cell(row=4 + i, column=3).fill = fill_verified if r["status"].startswith("VERIFIED") else fill_assume
extra = 4 + 14
for j, v in enumerate(["Lot locations", "Building next to each lot", "VERIFIED", "OpenStreetMap building coordinates; the lot sits beside the building.", I["lot_anchor_status"]["source"]], start=1):
    c = S.cell(row=extra, column=j, value=v)
    c.font, c.alignment = f_base, wrap
S.cell(row=extra, column=3).fill = fill_verified
widths(S, [32, 45, 14, 60, 70])

for sh_ in wb.worksheets:
    sh_.sheet_view.showGridLines = sh_.title not in ("README",)
wb.save(OUT)
print("saved", OUT)
