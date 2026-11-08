"""
fetch_data.py: download every public file the project needs.

WHY THIS IS ITS OWN SCRIPT
The model code runs in one place, but downloads may need to run somewhere
with a different internet connection. This script uses ONLY Python's
built in libraries (no pandas, no pip installs), so it runs on any
computer with Python 3.

Usage:
  python3 fetch_data.py raw                          # parking data files
  python3 fetch_data.py weather birmingham 2016-10-04 2016-12-19
  python3 fetch_data.py weather hague 2023-01-01 2023-12-31
  python3 fetch_data.py forecast                     # 7 day Duke forecast

Every download is saved to disk. If the file already exists we skip it,
so we never call a website twice for the same thing (this is "caching").
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"
CACHE = ROOT / "data" / "cache"

# Coordinates for each place (same values as config.toml).
PLACES = {
    "birmingham": (52.4862, -1.8904, "Europe/London"),
    "hague": (52.0705, 4.3007, "Europe/Amsterdam"),
    "duke": (36.0014, -78.9382, "America/New_York"),
}

RAW_FILES = {
    RAW / "birmingham" / "parking_birmingham.zip":
        "https://archive.ics.uci.edu/static/public/482/parking+birmingham.zip",
    RAW / "hague" / "hague_metadata.docx":
        "https://zenodo.org/records/17657881/files/DT02_UC1_%20Parking%20Data%20_EMERALDS_20251120_V0.1_Metadata.docx?download=1",
    RAW / "hague" / "hague_parking.csv":
        "https://zenodo.org/records/17657881/files/DT02_UC1_%20Parking%20Data%20_EMERALDS_20251120_V0.1.csv?download=1",
}

HEADERS = {"User-Agent": "park-shark-hackathon/0.1 (student project)"}


def download(url, dest):
    """Save url to dest, unless dest already exists."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        print(f"[skip] already have {dest.name}")
        return True
    print(f"[get ] {url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
            while True:
                chunk = r.read(1 << 20)  # 1 MB at a time so big files fit in memory
                if not chunk:
                    break
                f.write(chunk)
        tmp.rename(dest)
        print(f"[ok  ] {dest.name}: {dest.stat().st_size/1e6:.1f} MB")
        return True
    except Exception as e:  # report and keep going, never crash on one file
        print(f"[FAIL] {dest.name}: {e}")
        if tmp.exists():
            tmp.unlink()
        return False


def fetch_raw():
    for dest, url in RAW_FILES.items():
        download(url, dest)


def fetch_weather(place, start, end):
    """Hourly weather history from the Open-Meteo archive API.
    timezone= makes Open-Meteo return times in that place's LOCAL clock,
    so the weather hour lines up with the parking hour."""
    lat, lon, tz = PLACES[place]
    dest = CACHE / f"weather_{place}_{start}_{end}.json"
    url = ("https://archive-api.open-meteo.com/v1/archive"
           f"?latitude={lat}&longitude={lon}&start_date={start}&end_date={end}"
           "&hourly=temperature_2m,precipitation,snowfall,weather_code"
           f"&timezone={tz.replace('/', '%2F')}")
    download(url, dest)


def fetch_forecast():
    lat, lon, tz = PLACES["duke"]
    stamp = time.strftime("%Y-%m-%d")
    dest = CACHE / f"forecast_duke_{stamp}.json"
    url = ("https://api.open-meteo.com/v1/forecast"
           f"?latitude={lat}&longitude={lon}"
           "&hourly=temperature_2m,precipitation,snowfall,weather_code"
           f"&timezone={tz.replace('/', '%2F')}&forecast_days=8")
    download(url, dest)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "raw"
    if cmd == "raw":
        fetch_raw()
    elif cmd == "weather":
        fetch_weather(sys.argv[2], sys.argv[3], sys.argv[4])
    elif cmd == "forecast":
        fetch_forecast()
    else:
        print(__doc__)
