"""
Telemetry scraper (issue #4).

Downloads per-lap car telemetry from TracingInsights (via the jsDelivr CDN, the
same way writedb.py already fetches their lap data) and stores one zstd-compressed
Parquet file per driver per session:

    telemetry/<season>/<GrandPrixID>/<SessionID>/<DriverID>.parquet

Each file is indexed by one row in the TelemetryIndex table.

Called from writedb.py for every new Grand Prix, and from
updaters/update_telemetry.py to backfill or catch up. It can also be run on its
own to write Parquet files without a database:

    python -m scrapers.telemetry_scraper --year 2026 --event "Australian Grand Prix" --session Race --drivers VER
"""
import argparse
import datetime
import json
import random
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

SOURCE = "TracingInsights"
# Tried in order. jsDelivr is the CDN writedb.py already uses; it caches @main for
# hours and can refuse individual files, so GitHub itself is the fallback.
BASE_URLS = (
    "https://cdn.jsdelivr.net/gh/TracingInsights/{year}@main/{path}",
    "https://raw.githubusercontent.com/TracingInsights/{year}/main/{path}",
)
TELEMETRY_DIR = Path("telemetry")
FIRST_SEASON = 2018  # TracingInsights coverage starts here
MAX_WORKERS = 4
PAUSE_RANGE = (0.05, 0.15)  # seconds; random pause before every request
RETRIES = 3
ZSTD_LEVEL = 19

# Sessions.SessionName values that have telemetry. Sub-sessions such as
# "Qualifying 1" or "Grid", and cancelled sessions, are skipped.
TELEMETRY_SESSIONS = (
    "Practice 1", "Practice 2", "Practice 3", "Qualifying",
    "Sprint Shootout", "Sprint Qualifying", "Sprint", "Race",
)

# source column -> (output column, dtype)
CORE_COLUMNS = {
    "speed": ("speed", "float32"),
    "rpm": ("rpm", "float32"),
    "throttle": ("throttle", "float32"),  # 104 is FastF1's sensor-error value; kept as published
    "gear": ("gear", "int8"),
    "brake": ("brake", "bool"),
    "drs": ("drs", "uint8"),  # always 0 from 2026 (active aero); on/off only in earlier years
    "distance": ("distance", "float32"),
    "x": ("x", "float32"),
    "y": ("y", "float32"),
    "z": ("z", "float32"),
}
# Computed upstream rather than measured. Roughly doubles file size.
EXTRA_COLUMNS = {
    "rel_distance": ("rel_distance", "float32"),
    "acc_x": ("acc_x", "float32"),
    "acc_y": ("acc_y", "float32"),
    "acc_z": ("acc_z", "float32"),
    "DriverAhead": ("driver_ahead", "UInt8"),
    "DistanceToDriverAhead": ("dist_ahead", "float32"),
}
INCLUDE_EXTRA_COLUMNS = False


def normalize_driver_name(name):
    """Same normalisation as writedb.normalize_name (which cannot be imported)."""
    if not name:
        return ""
    if name.lower() == "gianmaria bruni":
        return "gimmi bruni"
    if name.lower() == "zhou guanyu":
        return "guanyu zhou"
    return unicodedata.normalize("NFKD", name.lower()).encode("ascii", "ignore").decode("ascii").replace("-", " ")


def _letters(text):
    return re.sub(r"[^a-z]", "", text)


def resolve_driver(entry, drivers, grandprix_drivers):
    """Map one TracingInsights drivers.json entry to a (DriverID, Name) row.

    1. If the entry has a usable first and last name (most entries from 2019),
       match it against the whole Drivers table.
    2. Otherwise, or if the name is spelled differently from ours, use the
       three-letter code (e.g. 'HUL'), which is the start of the surname: look
       for exactly one driver in this Grand Prix whose surname starts with it.
       2018 files and some later ones only carry the code, and the name field
       is sometimes the literal string 'None'."""
    first, last = entry.get("fn"), entry.get("ln")
    if all(value not in (None, "", "None") for value in (first, last)):
        match = drivers.get(normalize_driver_name(f"{first} {last}"))
        if match is not None:
            return match

    code = entry["driver"].lower()
    surname_matches, word_matches = [], []
    for driver_id, name in grandprix_drivers:
        words = normalize_driver_name(name).split()
        # 'de vries' -> 'dev', "o'ward" -> 'owa'
        surnames = {_letters(words[-1])[:3], _letters("".join(words[1:]))[:3]}
        if code in surnames:
            surname_matches.append((driver_id, name))
        elif code in {_letters(word)[:3] for word in words}:
            word_matches.append((driver_id, name))  # e.g. a first name; only a last resort
    matches = surname_matches or word_matches
    if len(matches) != 1:
        raise ValueError(
            f"Could not match TracingInsights driver {entry['driver']} (name: {first} {last}) "
            f"to exactly one driver in this Grand Prix; candidates: {matches}"
        )
    return matches[0]


def event_folder(grandprix_name, year):
    """TracingInsights folder for a Grand Prix: the name without the year, with
    the same renames writedb.py applies for its lap scraping."""
    name = re.sub(r"^\d{4}\s+", "", grandprix_name).strip()
    if name == "Mexican Grand Prix" and year >= 2020:
        name = "Mexico City Grand Prix"
    elif name == "Barcelona-Catalunya Grand Prix" and year >= 2026:
        name = "Barcelona Grand Prix"
    return name


def session_folder(session_name, year):
    """TracingInsights calls the 2022 sprint 'Sprint'; our Sessions table calls it 'Sprint Qualifying'."""
    if session_name == "Sprint Qualifying" and year == 2022:
        return "Sprint"
    return session_name


_host_failures = {}  # host -> consecutive failed requests
_host_down_until = {}  # host -> time.time() until which it is skipped
HOST_GIVE_UP = 5  # consecutive failures before a host is skipped...
HOST_COOLDOWN = 600  # ...for this many seconds


def fetch_json(year, path, base_urls=BASE_URLS):
    """GET a JSON file, trying each host in turn.

    Returns None only if the last host (GitHub itself) says 404, meaning the file
    does not exist. A 404 or a refusal (403, 5xx, timeout) from an earlier host just
    moves on to the next one, and a host that keeps failing is skipped for a while
    so one blocked CDN does not slow down every file. If every host fails, raises."""
    errors = []
    last_was_404 = False
    for position, host in enumerate(base_urls):
        is_last = position == len(base_urls) - 1
        if not is_last and _host_down_until.get(host, 0) > time.time():
            continue
        url = host.format(year=year, path=urllib.parse.quote(path))
        last_was_404 = False
        attempts = RETRIES
        attempt = 0
        while attempt < attempts:
            attempt += 1
            time.sleep(random.uniform(*PAUSE_RANGE))
            try:
                with urllib.request.urlopen(url, timeout=30) as response:
                    data = json.loads(response.read())
                _host_failures[host] = 0
                return data
            except urllib.error.HTTPError as error:
                if error.code == 404:
                    last_was_404 = True
                    break
                errors.append(f"{url}: HTTP {error.code}")
                if error.code not in (429, 500, 502, 503, 504):
                    break  # e.g. 403: retrying the same host will not help
                time.sleep(5 * attempt)  # rate limited or server trouble: back off
            except OSError as error:  # unreachable, timeout, connection reset
                errors.append(f"{url}: {error}")
                attempts = 2  # one quick retry, then move on
                time.sleep(2)
        if not last_was_404:
            _host_failures[host] = _host_failures.get(host, 0) + 1
            if _host_failures[host] >= HOST_GIVE_UP:
                _host_down_until[host] = time.time() + HOST_COOLDOWN
                _host_failures[host] = 0
                print(f"Skipping {host.split('/')[2]} for {HOST_COOLDOWN // 60} minutes after repeated failures")
    if last_was_404:
        return None
    raise RuntimeError("Could not download " + path + ":\n  " + "\n  ".join(errors))


def _lap_frame(year, base, lap, columns, base_urls):
    data = fetch_json(year, f"{base}/{lap}_tel.json", base_urls)
    if data is None:
        return None
    tel = data["tel"]
    absent = [source for source in columns if source not in tel]
    if absent:
        raise ValueError(f"{base}/{lap}_tel.json is missing columns {absent}")
    frame = pd.DataFrame({source: tel[source] for source in columns})
    frame.insert(0, "time_ms", (pd.Series(tel["time"]) * 1000).round())
    frame.insert(0, "lap", lap)
    return frame


def driver_frame(year, event, folder, code, columns, base_urls=BASE_URLS):
    """All laps of one driver in one session as a single DataFrame.
    Returns (frame, missing_lap_files); frame is None if the driver has no data."""
    base = f"{event}/{folder}/{code}"
    laptimes = fetch_json(year, f"{base}/laptimes.json", base_urls)
    if laptimes is None:
        return None, 0
    laps = laptimes["lap"]
    with ThreadPoolExecutor(MAX_WORKERS) as pool:
        results = list(pool.map(lambda lap: _lap_frame(year, base, lap, columns, base_urls), laps))
    missing = sum(1 for result in results if result is None)
    frames = [result for result in results if result is not None and len(result)]
    if not frames:
        return None, missing

    df = pd.concat(frames, ignore_index=True)
    # TracingInsights writes the string 'None' for missing values in some columns
    for source in columns:
        df[source] = pd.to_numeric(df[source], errors="coerce")
    df = df.rename(columns={source: output for source, (output, _) in columns.items()})
    for output, dtype in columns.values():
        if dtype in ("bool", "int8", "uint8") and df[output].isna().any():
            raise ValueError(f"{base}: unexpected missing values in integer column '{output}'")
        df[output] = df[output].astype(dtype)
    df["lap"] = df["lap"].astype("uint8")
    df["time_ms"] = df["time_ms"].astype("int32")
    return df.sort_values(["lap", "time_ms"], kind="stable").reset_index(drop=True), missing


def write_parquet(df, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(df, preserve_index=False)
    floats = [field.name for field in table.schema if pa.types.is_floating(field.type)]
    # byte-stream-split on the float columns shrinks the files by ~40%; it needs
    # dictionary encoding switched off.
    pq.write_table(table, path, compression="zstd", compression_level=ZSTD_LEVEL,
                   use_dictionary=False, use_byte_stream_split=floats)


def selected_columns():
    return {**CORE_COLUMNS, **EXTRA_COLUMNS} if INCLUDE_EXTRA_COLUMNS else dict(CORE_COLUMNS)


def save_telemetry(cursor, year, grandprix_name, grandprix_id, out_root=TELEMETRY_DIR, base_urls=BASE_URLS):
    """Download and index telemetry for every session of one Grand Prix that
    does not have it yet. Safe to re-run. Returns the number of files written.
    Sessions TracingInsights has not published yet are skipped and picked up by
    updaters/update_telemetry.py later."""
    if year < FIRST_SEASON:
        return 0

    cursor.execute("SELECT ID, Name FROM Drivers")
    drivers = {normalize_driver_name(name): (driver_id, name) for driver_id, name in cursor.fetchall()}
    cursor.execute("SELECT DISTINCT driverid, driver FROM GrandPrixResults WHERE grandprixid = ?", (grandprix_id,))
    grandprix_drivers = cursor.fetchall()
    cursor.execute("SELECT ID, SessionName FROM Sessions WHERE GrandPrixID = ? ORDER BY SessionNumber", (grandprix_id,))
    sessions = [(session_id, name) for session_id, name in cursor.fetchall() if name in TELEMETRY_SESSIONS]

    event = event_folder(grandprix_name, year)
    columns = selected_columns()
    written = 0
    for session_id, session_name in sessions:
        folder = session_folder(session_name, year)
        meta = fetch_json(year, f"{event}/{folder}/drivers.json", base_urls)
        if meta is None:
            print(f"Telemetry not available yet for {grandprix_name} {session_name}")
            continue

        for entry in meta["drivers"]:
            driver_id, driver_name = resolve_driver(entry, drivers, grandprix_drivers)
            path = Path(out_root) / str(year) / str(grandprix_id) / str(session_id) / f"{driver_id}.parquet"

            cursor.execute("SELECT 1 FROM TelemetryIndex WHERE SessionID = ? AND DriverID = ?", (session_id, driver_id))
            if cursor.fetchone() and path.exists():
                continue

            df, missing = driver_frame(year, event, folder, entry["driver"], columns, base_urls)
            if df is None:
                continue
            write_parquet(df, path)
            cursor.execute("""
                INSERT OR REPLACE INTO TelemetryIndex (
                    GrandPrixName, SessionName, Driver, FilePath, RowCount, LapCount,
                    Source, RetrievedAtUTC, GrandPrixID, SessionID, DriverID
                )
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (
                grandprix_name, session_name, driver_name, path.as_posix(), len(df),
                int(df["lap"].nunique()), SOURCE,
                datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                grandprix_id, session_id, driver_id,
            ))
            written += 1
            note = f" ({missing} lap files missing upstream)" if missing else ""
            print(f"Telemetry saved for {grandprix_name} {session_name} {driver_name}{note}")
    return written


def main():
    parser = argparse.ArgumentParser(description="Write telemetry Parquet files without touching the database.")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--event", required=True, help='e.g. "Australian Grand Prix"')
    parser.add_argument("--session", required=True, help='e.g. "Race", "Qualifying", "Practice 1"')
    parser.add_argument("--drivers", nargs="*", help="three-letter codes; default is every driver in the session")
    parser.add_argument("--out", default=str(TELEMETRY_DIR))
    parser.add_argument("--base-url", dest="base_urls", nargs="+", default=BASE_URLS, help="URL templates with {year} and {path}, tried in order")
    args = parser.parse_args()

    folder = session_folder(args.session, args.year)
    codes = args.drivers
    if not codes:
        meta = fetch_json(args.year, f"{args.event}/{folder}/drivers.json", args.base_urls)
        if meta is None:
            raise SystemExit(f"No telemetry published for {args.event} {folder}")
        codes = [entry["driver"] for entry in meta["drivers"]]

    columns = selected_columns()
    for code in codes:
        df, missing = driver_frame(args.year, args.event, folder, code, columns, args.base_urls)
        if df is None:
            print(f"{code}: no data")
            continue
        path = Path(args.out) / str(args.year) / args.event / args.session / f"{code}.parquet"
        write_parquet(df, path)
        print(f"{code}: {df['lap'].nunique()} laps, {len(df):,} rows, {path.stat().st_size / 1024:.0f} KB, {missing} lap files missing")


if __name__ == "__main__":
    main()
