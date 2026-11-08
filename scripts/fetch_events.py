#!/usr/bin/env python3
"""
Fetch Duke home-game schedules (football + men's basketball) from goduke.com text schedules
and write data/events.json. Only HOME games matter for campus parking.

Usage: python3 scripts/fetch_events.py
"""
import html
import json
import os
import re
import subprocess
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "events.json")
SPORTS = {
    "football": {"url": "https://goduke.com/sports/football/schedule/text", "venue": "Wallace Wade Stadium",
                 "venue_ll": [35.99541, -78.94203], "before_h": 4, "after_h": 4, "radius_m": 1200, "boost": 0.9},
    "mens-basketball": {"url": "https://goduke.com/sports/mens-basketball/schedule/text", "venue": "Cameron Indoor Stadium",
                        "venue_ll": [35.99734, -78.94234], "before_h": 3, "after_h": 3, "radius_m": 900, "boost": 0.75},
}
MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def get(url):
    return subprocess.run(["curl", "-sSL", "--fail", "-m", "60", "-A", "Mozilla/5.0 (ParkShark hackathon)", url],
                          capture_output=True, text=True, check=True).stdout


def parse_time(s):
    m = re.match(r"(\d{1,2})(?::(\d{2}))?(?:/[\d:]+)?\s*(a\.m\.|p\.m\.)", s or "")
    if not m:
        return None
    h = int(m.group(1)) % 12 + (12 if m.group(3) == "p.m." else 0)
    return f"{h:02d}:{m.group(2) or '00'}"


def main():
    events = []
    for sport, cfg in SPORTS.items():
        page = re.sub(r"<script.*?</script>|<style.*?</style>", "", get(cfg["url"]), flags=re.S)
        title = re.search(r"<title>(.*?)</title>", page, re.S)
        season = re.search(r"(20\d\d)(?:-(\d\d))?", title.group(1) if title else "")
        y0 = int(season.group(1)) if season else datetime.now().year
        for row in re.findall(r"<tr.*?</tr>", page, flags=re.S):
            cells = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<.*?>", "", c))).strip()
                     for c in re.findall(r"<td.*?</td>", row, flags=re.S)]
            if len(cells) < 5 or cells[2] != "Home":
                continue
            m = re.match(r"([A-Z][a-z]{2}) (\d{1,2})", cells[0])
            if not m:
                continue
            mon, day = MONTHS[m.group(1)], int(m.group(2))
            year = y0 + 1 if (sport == "mens-basketball" and mon < 7) else y0
            events.append({"sport": sport, "date": f"{year}-{mon:02d}-{day:02d}", "time": parse_time(cells[1]),
                           "time_text": cells[1], "opponent": cells[3], "venue": cfg["venue"],
                           "venue_ll": cfg["venue_ll"], "before_h": cfg["before_h"], "after_h": cfg["after_h"],
                           "radius_m": cfg["radius_m"], "boost": cfg["boost"], "result": cells[-1] if cells[-1] != "-" else None})
    events.sort(key=lambda e: (e["date"], e["time"] or ""))
    json.dump({"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "sources": [c["url"] for c in SPORTS.values()],
               "rules_source": ["https://parking.duke.edu/visitor-parking/athletic-event-parking/football-parking",
                                "https://parking.duke.edu/visitor-parking/athletic-event-parking/mens-basketball-parking/"],
               "events": events}, open(OUT, "w"), indent=1)
    print(f"Wrote {OUT}: {len(events)} home games "
          f"({sum(e['sport'] == 'football' for e in events)} football, {sum(e['sport'] == 'mens-basketball' for e in events)} MBB)")
    for e in events:
        print(f"  {e['date']} {e['time'] or 'TBA':>5}  {e['sport']:<16} vs {e['opponent']}")


if __name__ == "__main__":
    main()
