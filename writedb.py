from os import link
import sqlite3
import urllib.request, urllib.parse, urllib.error
from curl_cffi import requests
from bs4 import BeautifulSoup
import time
import re
import datetime
import json
import hashlib
import sys
import unicodedata
from decimal import Decimal
from collections import defaultdict
import cv2
import numpy as np
import pandas as pd
from timezonefinder import TimezoneFinder
from zoneinfo import ZoneInfo
import fastf1
from deep_translator import GoogleTranslator
import random # for random pauses to avoid hitting rate limits
import datetime
import sqlite3
from scrapers.circuit_layout_scraper import (
    parse_circuit_metadata,
    save_circuit_layout_version,
)
from scrapers.telemetry_scraper import save_telemetry as download_telemetry

# 1. Convert Python date/time objects to ISO strings when saving to SQLite
def adapt_datetime(dt):
    return dt.isoformat()

# 2. Convert SQLite text strings back to Python datetime objects when reading
def convert_datetime(val):
    # Decode SQLite bytes back to string
    date_str = val.decode("utf-8").strip()
    
    # Fallback: if it's only a date (10 chars), append midnight time
    if len(date_str) == 10:
        date_str += " 00:00:00"
        
    return datetime.datetime.fromisoformat(date_str)

# 3. Register the custom functions
sqlite3.register_adapter(datetime.date, adapt_datetime)
sqlite3.register_adapter(datetime.datetime, adapt_datetime)
sqlite3.register_converter("datetime", convert_datetime)
sqlite3.register_converter("timestamp", convert_datetime)

conn = sqlite3.connect('sessionresults.db')
cur = conn.cursor()
#cur.execute("PRAGMA foreign_keys = ON")

timezone_finder = TimezoneFinder()
elevation_cache = {}
country_cache = {}
constructors_processed_cache = set()


def scrape_new_engine_models(engine_makes):
    if not engine_makes:
        return
    from scrapers.scrape_engine_models import scrape_pending_engine_models

    conn.commit()
    scrape_pending_engine_models(engine_makes=set(engine_makes))


def scrape_historical_practice(year, gp):
    """
    Fills the Thursday/Friday/Saturday practice columns for the 2000-2003 era
    from newsonf1.com. Those seasons ran four practice sessions, two on each
    day, which the practice1-4 columns do not capture. (Monaco practised on
    Thursday rather than Friday.)

    A normal run resumes forward from the latest season and never reaches these
    years; this fires on a from-scratch rebuild, keeping one in step with a
    database that has already been backfilled by running that module directly.
    """
    if not 2000 <= year <= 2003:
        return
    from scrapers.scrape_historical_practice import scrape_historical_practice_sessions

    conn.commit()
    scrape_historical_practice_sessions(years={year}, grandprix_names=[gp])


def save_red_flags(cursor, grandprix_name, grandprix_id):
    """
    Refreshes the RedFlags rows for a single Grand Prix as part of the race
    pipeline, using writedb.py's own cursor (no commit here; the season-end
    commit covers it). The Wikipedia list is fetched once and cached inside
    the scraper module, so this stays cheap per race.
    """
    from scrapers.scrape_red_flags import save_red_flags_for_race

    save_red_flags_for_race(cursor, grandprix_name, grandprix_id)


def parse_coordinate(value):
    match = re.search(r'-?\d+(?:\.\d+)?', str(value).strip())
    if not match:
        raise ValueError(f"Invalid coordinate: {value}")
    return float(match.group(0))


def get_timezone_from_coords(lat, lng):
    if lat is None or lng is None:
        return None
    lat = parse_coordinate(lat)
    lng = parse_coordinate(lng)
    try:
        return timezone_finder.timezone_at(lng=lng, lat=lat)
    except Exception:
        try:
            return timezone_finder.closest_timezone_at(lng=lng, lat=lat)
        except Exception:
            return None


def get_elevation_from_coords(lat, lng):
    key = (parse_coordinate(lat), parse_coordinate(lng))
    if key not in elevation_cache:
        url = f"https://api.open-meteo.com/v1/elevation?latitude={key[0]}&longitude={key[1]}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'})
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
        elevation_cache[key] = data["elevation"][0]
    return elevation_cache[key]


def get_country_from_coords(lat, lng):
    key = (parse_coordinate(lat), parse_coordinate(lng))
    if key not in country_cache:
        url = f"https://api.bigdatacloud.net/data/reverse-geocode-client?latitude={key[0]}&longitude={key[1]}&localityLanguage=en"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'})
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
        country_cache[key] = data["countryName"]
    return country_cache[key]


def build_local_datetime(timestamp_seconds, timezone_name):
    if timestamp_seconds is None:
        return None
    if timezone_name:
        try:
            tz = ZoneInfo(timezone_name)
            utc_dt = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc) + datetime.timedelta(seconds=timestamp_seconds)
            return utc_dt.astimezone(tz)
        except Exception:
            pass
    return datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=timestamp_seconds)


def normalize_name(name):
  """
  Normalizes a name for comparison by converting to lowercase and decomposing
  unicode characters. e.g., 'José' becomes 'jose'.
  """
  if not name:
    return ""
  if name.lower() == "gianmaria bruni":
    return "gimmi bruni"
  elif name.lower() == "zhou guanyu":
    return "guanyu zhou"
  # Normalizes to decomposed form, converts to lowercase, and removes accents
  return unicodedata.normalize('NFKD', name.lower()).encode('ascii', 'ignore').decode('ascii').replace('-', ' ')


def canonicalize_session_name(session_name, year=None):
    if not session_name:
        return session_name

    cancelled_suffix = ""
    if session_name.endswith(" - Cancelled"):
        cancelled_suffix = " - Cancelled"
        session_name = session_name[:-12]

    replacements = {
        "1st Free Practice": "Practice 1",
        "2nd Free Practice": "Practice 2",
        "3rd Free Practice": "Practice 3",
        "4th Free Practice": "Practice 4",
        "1st Qualifying": "Qualifying 1",
        "2nd Qualifying": "Qualifying 2",
        "3rd Qualifying": "Qualifying 3",
        "Combined Qualifying": "Qualifying",
        "Sprint Combined Qualifying": "Sprint Qualifying",
        "Combined Sprint Qualifying": "Sprint Qualifying",
        "1st Sprint Qualifying": "Sprint Qualifying 1",
        "2nd Sprint Qualifying": "Sprint Qualifying 2",
        "3rd Sprint Qualifying": "Sprint Qualifying 3",
        "Sprint Race": "Sprint",
    }

    canonical = replacements.get(session_name, session_name)
    return canonical + cancelled_suffix


def slugify_statsf1_constructor_name(name):
    if "?" in name:
        name = name.replace("?", "-")
    name = name.replace("°", "-")
    return re.sub(r"[^a-z0-9.]", "-", normalize_name(name)).replace("/", "-").replace("(", "-").replace(")", "-")

def parse_designers(html):
    soup = BeautifulSoup(html, "html.parser")
    designers = []
    strong = soup.select_one("fieldset.modeldes .field strong")
    if not strong:
        return []

    # Preserve <br> as line breaks
    text = strong.get_text("\n", strip=True)

    # Split on newlines and slashes
    parts = []
    for line in text.split("\n"):
        parts.extend(
            p.strip()
            for p in re.split(r"\s*/\s*", line)
            if p.strip()
        )

    for part in parts:
        # Match: Name (Role)
        m = re.match(r"^(.*?)\s*\((.*?)\)\s*$", part)

        if m:
            name, role = m.groups()

            designers.append({
                "name": translate_reason(name.strip()),
                "role": translate_reason(role.strip())
            })
        else:
            designers.append({
                "name": translate_reason(part) if part != "Conrero" else "Conrero",
                "role": None
            })

    return designers

def parse_constructor_nationalities_from_page(page_soup):
    page_text = page_soup.get_text("\n", strip=True)
    match = re.search(r"Nation\s*:\s*([^\n]+)", page_text)
    if not match:
        return []

    nationalities = []
    for item in match.group(1).split(","):
        item = item.strip()
        if not item:
            continue

        nationality_match = re.match(r"^(.*?)\s*\((\d{4})\)$", item)
        if nationality_match:
            nationalities.append([nationality_match.group(1).strip(), int(nationality_match.group(2))])
        else:
            nationalities.append([item, None])

    return nationalities


def parse_constructor_lineage_section_v2(page_soup, heading):
    heading_text = page_soup.find(string=lambda s: s and heading in s)
    if not heading_text:
        return []

    parsed = []
    chain = []
    current_name = None

    for child in heading_text.next_siblings:
        if getattr(child, "name", None) == "hr":
            break

        if isinstance(child, str):
            text = child.replace("\n", " ").strip()
            if not text:
                continue

            if "•" in text:
                if chain:
                    parsed.append(chain)
                    chain = []
                current_name = None
                text = text.replace("•", "").strip()

            text = text.replace("➜", "").strip()
            if text:
                current_name = text
            continue

        if child.name in {"a", "strong"}:
            current_name = child.get_text(" ", strip=True)
            continue

        if child.name == "span":
            if current_name is None:
                continue

            eras = re.findall(r"\(([^()]*)\)", child.get_text(" ", strip=True))
            for era in eras:
                chain.append({current_name: era.strip()})

    if chain:
        parsed.append(chain)

    return parsed



#Functions:
headers = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/151.0.7922.138 Safari/537.36"
    )
}

#This is the shared fetcher every request goes through. It returns the raw response so
#both the HTML scraping (open_url) and the track map images (imread_from_url) get the
#same Chrome TLS fingerprint, the same retry/backoff, and — most importantly — the same
#throttle. Images used to bypass all of this on plain urllib, which meant a circuit page
#with five layouts fired five unthrottled requests back to back and got the connection reset.
def fetch_url(url, retries=3, extra_headers=None):
    url = "https://" + url.replace("https://", "").replace("//", "/")
    request_headers = dict(headers)
    if extra_headers:
        request_headers.update(extra_headers)
    last_exception = None

    for attempt in range(retries):
        try:
            response = requests.get(
                url,
                headers=request_headers,
                impersonate="chrome",
                timeout=30
            )
            if (
                response.status_code == 302
                and response.headers.get("location")
                == "https://www.statsf1.com/errors/GenericErrorPage.htm"
            ):
                raise Exception(
                    "You have been IP blocked by statsf1.com. Please wait and try again after 24 hours."
                )
            response.raise_for_status()
            if "statsf1.com" in url:
                time.sleep(random.uniform(4, 15))
            return response
        except Exception as e:
            last_exception = e
            print(f"Attempt {attempt + 1} failed for URL {url}: {e}")

            if attempt < retries - 1:
                time.sleep(
                    random.expovariate(1 / (5 * (2 ** attempt)))
                )
    if isinstance(last_exception, (requests.exceptions.Timeout, TimeoutError)):
        print("Timed out after all retries. Sleeping for 5 minutes...")
        time.sleep(300)
    raise RuntimeError(
        f"Failed to open URL {url} after {retries} attempts."
    ) from last_exception

def open_url(url, retries=3):
    response = fetch_url(url, retries=retries)
    global soup
    soup = BeautifulSoup(response.content, "html.parser")
    return soup

#This is the function we use to open json files. We use this when we scrape APIs
def open_json(url):
    retries = 0
    while retries < 3:
        try:
            req = urllib.request.Request(url, headers=headers)
            data = json.loads(urllib.request.urlopen(req).read())
            return data
        except Exception as e:
            retries += 1
            print(f"Failed to open url {url} due to error {e}. \n Available retries: {retries}")
            if retries == 3:
                raise RuntimeError (f"Failed to open URL {url} due to error {e} after 3 retries.")
            time.sleep(1)


def sprint_pitstop_slug_candidates(session_slug, year):
    candidates = []
    if session_slug:
        candidates.append(session_slug)

        if year in (2021, 2022):
            if session_slug.endswith("_sprint"):
                candidates.append(session_slug[:-7] + "_sprint-qualifying")
            elif session_slug.endswith("_sprint-qualifying"):
                candidates.append(session_slug[:-19] + "_sprint")

    seen = set()
    ordered = []
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)
    return ordered


def fetch_sprint_pitstop_content(session_slug, year):
    last_error = None
    for candidate_slug in sprint_pitstop_slug_candidates(session_slug, year):
        url = (
            "https://motorsportstats.com/api/result-statistics"
            f"?sessionSlug={candidate_slug}&sessionFact=PitStop&size=100000"
        )
        try:
            response = open_json(url)
        except Exception as exc:
            last_error = exc
            continue

        content = response.get('content') or []
        if content:
            return content

    if last_error is not None:
        print(f"Unable to fetch sprint pit stops for session slug {session_slug}: {last_error}")
    return []


def safe_int(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != value:
            return None
        return int(value)

    value_str = str(value).strip()
    if not value_str or value_str.lower() == "nan":
        return None

    try:
        return int(float(value_str))
    except ValueError:
        return None


def format_timestamp_utc(value):
    if value is None:
        return None
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def format_session_timestamp(value):
    if value is None:
        return None
    if hasattr(value, "to_pytimedelta"):
        value = value.to_pytimedelta()
    return str(value)


def session_timestamp_to_seconds(value):
    if value is None:
        return None
    if hasattr(value, "to_pytimedelta"):
        value = value.to_pytimedelta()
    if hasattr(value, "total_seconds"):
        return value.total_seconds()
    return None


def normalize_session_name_for_race_control(session_name, year):
    if not session_name:
        return None

    normalized = re.sub(r'[^a-z0-9]+', ' ', session_name.casefold()).strip()
    normalized = normalized.replace(' cancelled', '').strip()

    if not normalized or 'grid' in normalized:
        return None

    if normalized in ('race', 'grand prix', 'grand prix race'):
        return 'grandprix'

    if 'free practice' in normalized or normalized.startswith('practice '):
        if '1st' in normalized or normalized.endswith(' 1') or 'practice 1' in normalized:
            return 'practice1'
        if '2nd' in normalized or normalized.endswith(' 2') or 'practice 2' in normalized:
            return 'practice2'
        if '3rd' in normalized or normalized.endswith(' 3') or 'practice 3' in normalized:
            return 'practice3'

    if normalized in ('sprint', 'sprint race'):
        return 'sprint'

    if 'sprint shootout' in normalized:
        return 'sprintshootout'

    if 'sprint qualifying' in normalized:
        if any(token in normalized for token in ('1st', '2nd', '3rd', 'qualifying 1', 'qualifying 2', 'qualifying 3')):
            return None
        if year in (2021, 2022) and normalized == 'sprint qualifying':
            return 'sprint'
        if year == 2023:
            return 'sprintshootout'
        return 'sprintqualifying'

    if 'qualifying' in normalized:
        if any(token in normalized for token in ('1st', '2nd', '3rd', 'qualifying 1', 'qualifying 2', 'qualifying 3')):
            return None
        return 'qualifying'

    return None


def get_race_control_session_lookup(cursor, grandprix_id, year):
    cursor.execute("SELECT ID, SessionName FROM Sessions WHERE GrandPrixID = ?", (grandprix_id,))
    lookup = {}

    for session_id, session_name in cursor.fetchall():
        session_key = normalize_session_name_for_race_control(session_name, year)
        if session_key and session_key not in lookup:
            lookup[session_key] = {'id': session_id, 'name': session_name}

    return lookup


def require_session_record(session_lookup, session_key, grand_prix_name):
    session_record = session_lookup.get(session_key)
    if session_record is None:
        raise ValueError(f"Missing session mapping for '{session_key}' in {grand_prix_name}")
    return session_record


def upsert_session_record(cursor, grandprix_name, session_data, grandprix_id):
    canonical_session_name = canonicalize_session_name(session_data['name'])
    values = (
        grandprix_name,
        canonical_session_name,
        session_data['sessionnumber'],
        session_data['wassessioncancelled'],
        session_data['starttimetimestamputc'],
        session_data['endtimetimestamputc'],
        session_data['starttimetimestamp'],
        session_data['endtimetimestamp'],
        session_data['precisestarttime'],
        session_data['startdtutc'],
        session_data['enddtutc'],
        session_data['startdt'],
        session_data['enddt'],
        session_data.get('timezone'),
        grandprix_id,
    )

    # Re-running the importer for the same GP should refresh the same session row,
    # not create a second copy with a new autoincremented ID.
    cursor.execute(
        "SELECT ID FROM Sessions WHERE GrandPrixID = ? AND SessionNumber = ? ORDER BY ID LIMIT 1",
        (grandprix_id, session_data['sessionnumber'])
    )
    existing_row = cursor.fetchone()
    if existing_row:
        session_id = existing_row[0]
        cursor.execute(
            """SELECT GrandPrix, SessionName, SessionNumber, WasSessionCancelled,
                    StartTimeTimestampUTC, EndTimeTimestampUTC, StartTimeTimestamp,
                    EndTimeTimestamp, IfPreciseStartTime, StartTimeinDatetimeUTC,
                    EndTimeinDatetimeUTC, StartTimeinDatetime, EndTimeinDatetime,
                    SessionTimezone, GrandPrixID
               FROM Sessions
               WHERE ID = ?""",
            (session_id,)
        )
        existing_values = cursor.fetchone()
        if existing_values:
            merged_values = [old_value if new_value is None else new_value
                             for new_value, old_value in zip(values, existing_values)]
            merged_values.append(session_id)
            cursor.execute(
                """UPDATE Sessions
                   SET GrandPrix = ?, SessionName = ?, SessionNumber = ?, WasSessionCancelled = ?,
                       StartTimeTimestampUTC = ?, EndTimeTimestampUTC = ?, StartTimeTimestamp = ?,
                       EndTimeTimestamp = ?, IfPreciseStartTime = ?, StartTimeinDatetimeUTC = ?,
                       EndTimeinDatetimeUTC = ?, StartTimeinDatetime = ?, EndTimeinDatetime = ?,
                       SessionTimezone = ?, GrandPrixID = ?
                   WHERE ID = ?""",
                tuple(merged_values)
            )
            return session_id
        return session_id

    cursor.execute(
        """INSERT INTO Sessions (GrandPrix, SessionName, SessionNumber, WasSessionCancelled, StartTimeTimestampUTC, EndTimeTimestampUTC, StartTimeTimestamp,
                    EndTimeTimestamp, IfPreciseStartTime, StartTimeinDatetimeUTC, EndTimeinDatetimeUTC, StartTimeinDatetime, EndTimeinDatetime, SessionTimezone, GrandPrixID)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        values
    )
    return cursor.lastrowid


def ensure_combined_qualifying_session(cursor, grandprix_name, grandprix_id, year):
    cursor.execute(
        """
        SELECT ID, SessionName, SessionNumber, WasSessionCancelled,
               StartTimeTimestampUTC, EndTimeTimestampUTC, StartTimeTimestamp,
               EndTimeTimestamp, IfPreciseStartTime, StartTimeinDatetimeUTC,
               EndTimeinDatetimeUTC, StartTimeinDatetime, EndTimeinDatetime, SessionTimezone
        FROM Sessions
        WHERE GrandPrixID = ?
        ORDER BY SessionNumber, ID
        """,
        (grandprix_id,)
    )
    rows = cursor.fetchall()
    if not rows:
        return

    if any(normalize_session_name_for_race_control(session_name, year) == 'qualifying' for _, session_name, *_ in rows):
        return

    qualifying_segments = []
    for row in rows:
        session_name = row[1]
        normalized = re.sub(r'[^a-z0-9]+', ' ', session_name.casefold()).strip()
        if 'qualifying' not in normalized:
            continue
        if any(token in normalized for token in ('1st', '2nd', '3rd', 'qualifying 1', 'qualifying 2', 'qualifying 3')):
            qualifying_segments.append(row)

    if not qualifying_segments:
        return

    def first_non_null(index):
        for segment in qualifying_segments:
            value = segment[index]
            if value is not None:
                return value
        return None

    def last_non_null(index):
        for segment in reversed(qualifying_segments):
            value = segment[index]
            if value is not None:
                return value
        return None

    combined_name = 'Qualifying'
    next_session_number = max(row[2] for row in rows if row[2] is not None) + 1
    synthetic_session = {
        'name': combined_name,
        'sessionnumber': next_session_number,
        'wassessioncancelled': 1 if all(row[3] for row in qualifying_segments) else 0,
        'starttimetimestamputc': first_non_null(4),
        'endtimetimestamputc': last_non_null(5),
        'starttimetimestamp': first_non_null(6),
        'endtimetimestamp': last_non_null(7),
        'precisestarttime': first_non_null(8),
        'startdtutc': first_non_null(9),
        'enddtutc': last_non_null(10),
        'startdt': first_non_null(11),
        'enddt': last_non_null(12),
        'timezone': first_non_null(13),
    }
    upsert_session_record(cursor, grandprix_name, synthetic_session, grandprix_id)


def save_race_control_messages(cursor, year, round_number, grandprix_name, grandprix_id):
    if year < 2018:
        return

    session_lookup = get_race_control_session_lookup(cursor, grandprix_id, year)
    if not session_lookup:
        return

    fastf1_codes = ['FP1', 'FP2', 'FP3', 'Q']
    if year == 2023:
        fastf1_codes.append('SS')
    elif year >= 2024:
        fastf1_codes.append('SQ')
    fastf1_codes.extend(['S', 'R'])

    code_to_key = {
        'FP1': 'practice1',
        'FP2': 'practice2',
        'FP3': 'practice3',
        'Q': 'qualifying',
        'SS': 'sprintshootout',
        'SQ': 'sprintqualifying',
        'S': 'sprint',
        'R': 'grandprix'
    }

    try:
        event = fastf1.get_event(year, round_number)
    except Exception as exc:
        print(f"FastF1 event lookup failed for {grandprix_name}: {exc}")
        return

    for session_code in fastf1_codes:
        matched_session = session_lookup.get(code_to_key[session_code])
        if not matched_session:
            continue
        try:
            event.get_session_name(session_code)
            session = event.get_session(session_code)
            session.load(laps=False, telemetry=False, weather=False, messages=True)
            race_control_messages = session.race_control_messages
        except ValueError:
            continue

        if race_control_messages is None or race_control_messages.empty:
            continue

        for _, message in race_control_messages.iterrows():
            cursor.execute("""
                INSERT INTO RaceControlMessages (
                    GrandPrixName, SessionName, TimestampUTC, Category, Message,
                    Status, Flag, Scope, MarshallingSector, AffectedDriverNumber,
                    Lap, GrandPrixID, SessionID
                )
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                grandprix_name,
                matched_session['name'],
                format_timestamp_utc(message.get('Time')),
                message.get('Category'),
                message.get('Message'),
                message.get('Status'),
                message.get('Flag'),
                message.get('Scope'),
                safe_int(message.get('Sector')),
                safe_int(message.get('RacingNumber')),
                safe_int(message.get('Lap')),
                grandprix_id,
                matched_session['id']
            ))

        print(f"Race control messages saved for {grandprix_name} {matched_session['name']}")


def save_telemetry_data(cursor, year, grandprix_name, grandprix_id):
    # Telemetry is on by default; run `python writedb.py --notelemetry` to skip it.
    if "--notelemetry" in sys.argv:
        return
    download_telemetry(cursor, year, grandprix_name, grandprix_id)


def save_weather_data(cursor, year, round_number, grandprix_name, grandprix_id):
    if year < 2018:
        return

    session_lookup = get_race_control_session_lookup(cursor, grandprix_id, year)
    if not session_lookup:
        return

    fastf1_codes = ['FP1', 'FP2', 'FP3', 'Q']
    if year == 2023:
        fastf1_codes.append('SS')
    elif year >= 2024:
        fastf1_codes.append('SQ')
    fastf1_codes.extend(['S', 'R'])

    code_to_key = {
        'FP1': 'practice1',
        'FP2': 'practice2',
        'FP3': 'practice3',
        'Q': 'qualifying',
        'SS': 'sprintshootout',
        'SQ': 'sprintqualifying',
        'S': 'sprint',
        'R': 'grandprix'
    }

    try:
        event = fastf1.get_event(year, round_number)
    except Exception as exc:
        print(f"FastF1 event lookup failed for {grandprix_name}: {exc}")
        return

    for session_code in fastf1_codes:
        matched_session = session_lookup.get(code_to_key[session_code])
        if not matched_session:
            continue

        try:
            event.get_session_name(session_code)
            session = event.get_session(session_code)
            session.load(laps=False, telemetry=False, weather=True, messages=False)
            weather_data = session.weather_data
        except ValueError:
            continue

        if weather_data is None or weather_data.empty:
            continue

        for _, weather_row in weather_data.iterrows():
            session_time = weather_row.get('Time')
            cursor.execute("""
                INSERT INTO WeatherData (
                    GrandPrixName, SessionName, SessionTimestamp,
                    SessionTimestampInSeconds, AirTemperatureC, HumidityPercent,
                    AirPressureMbar, IfRainfall, TrackTemperatureC,
                    WindDirectionDegrees, WindSpeedMs, GrandPrixID, SessionID
                )
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                grandprix_name,
                matched_session['name'],
                format_session_timestamp(session_time),
                session_timestamp_to_seconds(session_time),
                weather_row.get('AirTemp'),
                weather_row.get('Humidity'),
                weather_row.get('Pressure'),
                weather_row.get('Rainfall'),
                weather_row.get('TrackTemp'),
                weather_row.get('WindDirection'),
                weather_row.get('WindSpeed'),
                grandprix_id,
                matched_session['id']
            ))

        print(f"Weather data saved for {grandprix_name} {matched_session['name']}")


ARCHIVE_API_URL = "https://archive-api.open-meteo.com/v1/archive"

HISTORICAL_WEATHER_HOURLY_VARS = [
    "temperature_2m",
    "soil_temperature_0cm",
    "relative_humidity_2m",
    "surface_pressure",
    "precipitation",
    "wind_speed_10m",
    "wind_direction_10m",
]


def fetch_historical_weather(lat, lng, date_str):
    """
    date_str: 'YYYY-MM-DD'. start_date == end_date == the race date gives us
    the full day at hourly resolution. windspeed_unit=ms matches the existing
    WindSpeedMs column, so no km/h -> m/s conversion is needed downstream.
    """
    params = {
        "latitude": lat,
        "longitude": lng,
        "start_date": date_str,
        "end_date": date_str,
        "hourly": ",".join(HISTORICAL_WEATHER_HOURLY_VARS),
        "timezone": "UTC",
        "windspeed_unit": "ms",
    }
    url = f"{ARCHIVE_API_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def save_historical_weather_data(cursor, year, round_number, grandprix_name, grandprix_id):
    # Runs for all years, not just pre-2018. WeatherData (FastF1 telemetry,
    # 2018+) stores rainfall as a boolean at session resolution; this table
    # exists to give quantitative precipitation (and hourly resolution) on
    # top of that, so the two intentionally overlap in years covered rather
    # than splitting by era.

    cursor.execute("""
        SELECT cl.Latitude, cl.Longitude
        FROM GrandsPrix gp
        JOIN CircuitLayouts cl ON gp.CircuitLayoutID = cl.ID
        WHERE gp.ID = ?
    """, (grandprix_id,))
    coords_row = cursor.fetchone()
    if not coords_row or coords_row[0] is None or coords_row[1] is None:
        print(f"No circuit coordinates found for {grandprix_name}, skipping historical weather")
        return
    lat, lng = coords_row

    cursor.execute("""
        SELECT ID, SessionName, StartTimeTimestampUTC, EndTimeTimestampUTC
        FROM Sessions
        WHERE GrandPrixID = ?
    """, (grandprix_id,))
    sessions = cursor.fetchall()
    if not sessions:
        return

    # Cache one day's hourly data per date so multiple sessions on the same
    # date (e.g. qualifying + race) only cost one API call.
    day_cache = {}

    for session_id, session_name, start_ts_utc, end_ts_utc in sessions:
        if start_ts_utc is None:
            # Skipped: sessions with only a date and no start/end time.
            # Per maintainer, this is contingent on issue #10 (Sessions
            # table upgrade) -- revisit once that lands.
            continue

        session_start = datetime.datetime.fromtimestamp(start_ts_utc, tz=datetime.timezone.utc).replace(tzinfo=None)
        session_end = (
            datetime.datetime.fromtimestamp(end_ts_utc, tz=datetime.timezone.utc).replace(tzinfo=None) if end_ts_utc else session_start + datetime.timedelta(hours=2)
        )
        date_str = session_start.strftime("%Y-%m-%d")

        if date_str not in day_cache:
            try:
                day_cache[date_str] = fetch_historical_weather(lat, lng, date_str)
            except Exception as exc:
                print(f"Open-Meteo archive lookup failed for {grandprix_name} on {date_str}: {exc}")
                day_cache[date_str] = None

        data = day_cache[date_str]
        if data is None or "hourly" not in data:
            continue

        hourly = data["hourly"]
        times = hourly.get("time", [])

        for i, time_str in enumerate(times):
            hour_dt = datetime.datetime.fromisoformat(time_str)
            if not (session_start <= hour_dt <= session_end):
                continue

            cursor.execute("""
                INSERT INTO HistoricalWeather (
                    GrandPrixName, SessionName, TimestampUTC,
                    AirTemperatureC, SoilTemperatureC, HumidityPercent,
                    AirPressureMbar, PrecipitationMm, WindSpeedMs,
                    WindDirectionDegrees, GrandPrixID, SessionID
                )
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                grandprix_name,
                session_name,
                time_str,
                hourly.get("temperature_2m", [None] * len(times))[i],
                hourly.get("soil_temperature_0cm", [None] * len(times))[i],
                hourly.get("relative_humidity_2m", [None] * len(times))[i],
                hourly.get("surface_pressure", [None] * len(times))[i],
                hourly.get("precipitation", [None] * len(times))[i],
                hourly.get("wind_speed_10m", [None] * len(times))[i],
                hourly.get("wind_direction_10m", [None] * len(times))[i],
                grandprix_id,
                session_id,
            ))

        print(f"Historical weather saved for {grandprix_name} {session_name}")


# --- Planned schedule ---------------------------------------------------------
# Snapshots of the planned calendar, kept as versions so that calendar changes
# during a season (cancellations, postponements, replacements) are not lost.
# This is deliberately separate from Sessions: Sessions holds sessions that
# actually ran and that results reference, whereas these are planned sessions.

SCHEDULE_FIRST_SEASON = 2018  # earliest season FastF1's own backend has a full session schedule for
SCHEDULE_SOURCE = 'fastf1'


def get_planned_schedule(year):
    # backend='fastf1' is forced on purpose. Left on its default, FastF1 silently
    # falls back to Ergast when its primary source is unreachable, and Ergast has
    # no session times, so the fallback would be recorded as a (false) change to
    # the calendar instead of failing loudly.
    # Testing is excluded: this is the race calendar.
    schedule = fastf1.get_event_schedule(year, include_testing=False, backend='fastf1')

    events = []
    for _, row in schedule.iterrows():
        sessions = []
        for number in range(1, 6):
            session_name = row[f'Session{number}']
            if pd.isna(session_name) or not str(session_name).strip():
                continue  # this event has fewer than 5 sessions

            start_utc = row[f'Session{number}DateUtc']
            start_local = row[f'Session{number}Date']
            if pd.isna(start_utc):
                # Session is on the calendar but its time is not known (yet).
                sessions.append({'number': number, 'name': session_name,
                                 'timestamp_utc': None, 'start_utc': None, 'start_local': None})
                continue

            start_utc = start_utc.to_pydatetime().replace(tzinfo=datetime.timezone.utc)
            sessions.append({
                'number': number,
                'name': session_name,
                'timestamp_utc': int(start_utc.timestamp()),
                'start_utc': start_utc.isoformat(),
                'start_local': None if pd.isna(start_local) else start_local.isoformat(),
            })

        events.append({
            'round_number': int(row['RoundNumber']),
            'event_name': row['EventName'],
            'official_event_name': row['OfficialEventName'],
            'country': row['Country'],
            'location': row['Location'],
            'event_date': row['EventDate'].strftime('%Y-%m-%d'),  # local date of race day
            'event_format': row['EventFormat'],
            'sessions': sessions,
        })

    # An event is identified across versions by (EventName, Location). A Grand Prix
    # that keeps its name but changes venue is therefore a different event: the
    # 2026 Bahrain Grand Prix was cancelled at Sakhir and a Bahrain Grand Prix was
    # then added at Sepang.
    keys = [(event['event_name'], event['location']) for event in events]
    if len(keys) != len(set(keys)):
        raise ValueError(f"The {year} schedule has two events with the same EventName and Location, so they cannot be told apart between versions.")

    return events


def hash_schedule_events(events):
    canonical = json.dumps(
        sorted(events, key=lambda event: (event['round_number'], event['event_name'])),
        sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def save_schedule_snapshot(cursor, year):
    """
    Saves the season's current planned calendar as a new version, but only if it
    differs from the latest stored version. Returns True if a version was saved.

    Status of each event in the new version:
    - 'cancelled': it was in the previous version but is no longer on the calendar.
      It is carried forward (with its planned sessions) into this and all later
      versions, with a NULL RoundNumber as it is no longer a round.
    - 'postponed': it is on the calendar with a later date than in the previous
      version (this includes a cancelled event that comes back on a later date).
      Once postponed it stays postponed in later versions.
    - 'scheduled': everything else, including events added to the calendar.
    """
    events = get_planned_schedule(year)
    content_hash = hash_schedule_events(events)

    cursor.execute("SELECT ID, VersionNumber, ContentHash FROM ScheduleVersions WHERE Season = ? ORDER BY VersionNumber DESC LIMIT 1", (year,))
    previous = cursor.fetchone()
    if previous and previous[2] == content_hash:
        return False

    previous_events = {}
    if previous:
        cursor.execute("SELECT ID, EventName, Location, EventDate, Status FROM Schedule WHERE ScheduleVersionID = ?", (previous[0],))
        for schedule_id, event_name, location, event_date, status in cursor.fetchall():
            previous_events[(event_name, location)] = (schedule_id, event_date, status)

    version_number = previous[1] + 1 if previous else 1
    cursor.execute("""
        INSERT INTO ScheduleVersions (Season, VersionNumber, RetrievedAtUTC, Source, ContentHash)
        VALUES (?,?,?,?,?)
    """, (year, version_number, datetime.datetime.now(datetime.timezone.utc).isoformat(), SCHEDULE_SOURCE, content_hash))
    version_id = cursor.lastrowid

    current_keys = set()
    for event in events:
        key = (event['event_name'], event['location'])
        current_keys.add(key)

        status = 'scheduled'
        earlier = previous_events.get(key)
        if earlier and (earlier[2] == 'postponed' or event['event_date'] > earlier[1]):
            status = 'postponed'

        cursor.execute("""
            INSERT INTO Schedule (ScheduleVersionID, Season, RoundNumber, EventName, OfficialEventName,
                                  Country, Location, EventDate, EventFormat, Status)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (version_id, year, event['round_number'], event['event_name'], event['official_event_name'],
              event['country'], event['location'], event['event_date'], event['event_format'], status))
        schedule_id = cursor.lastrowid

        for session in event['sessions']:
            cursor.execute("""
                INSERT INTO ScheduleSessions (ScheduleID, SessionName, SessionNumber, StartTimeTimestampUTC,
                                              StartTimeinDatetimeUTC, StartTimeinDatetime)
                VALUES (?,?,?,?,?,?)
            """, (schedule_id, session['name'], session['number'], session['timestamp_utc'],
                  session['start_utc'], session['start_local']))

    for key, (old_schedule_id, _, _) in previous_events.items():
        if key in current_keys:
            continue
        cursor.execute("""
            INSERT INTO Schedule (ScheduleVersionID, Season, RoundNumber, EventName, OfficialEventName,
                                  Country, Location, EventDate, EventFormat, Status)
            SELECT ?, Season, NULL, EventName, OfficialEventName, Country, Location, EventDate, EventFormat, 'cancelled'
            FROM Schedule WHERE ID = ?
        """, (version_id, old_schedule_id))
        cancelled_schedule_id = cursor.lastrowid
        cursor.execute("""
            INSERT INTO ScheduleSessions (ScheduleID, SessionName, SessionNumber, StartTimeTimestampUTC,
                                          StartTimeinDatetimeUTC, StartTimeinDatetime)
            SELECT ?, SessionName, SessionNumber, StartTimeTimestampUTC, StartTimeinDatetimeUTC, StartTimeinDatetime
            FROM ScheduleSessions WHERE ScheduleID = ?
        """, (cancelled_schedule_id, old_schedule_id))

    return True


def link_schedule_to_grandsprix(cursor):
    # Links an event to the Grand Prix held on its date, allowing one day of difference.
    # EventDate is the local date, and a race run in the evening can already be on the
    # next day in UTC: the Las Vegas Grand Prix is raced on Saturday night local time.
    # Race weekends are at least a week apart, so this cannot match the wrong race.
    # Events that have not been held (yet), and cancelled events, are left NULL;
    # nothing else is guessed.
    # Two passes, exact date first, so that an exact match always wins.
    cursor.execute("""
        UPDATE Schedule
        SET GrandPrixID = (
            SELECT GrandsPrix.ID FROM GrandsPrix
            WHERE GrandsPrix.Season = Schedule.Season AND GrandsPrix.DateInDateTime = Schedule.EventDate
        )
        WHERE GrandPrixID IS NULL AND Status != 'cancelled'
    """)
    cursor.execute("""
        UPDATE Schedule
        SET GrandPrixID = (
            SELECT GrandsPrix.ID FROM GrandsPrix
            WHERE GrandsPrix.Season = Schedule.Season
              AND ABS(julianday(GrandsPrix.DateInDateTime) - julianday(Schedule.EventDate)) <= 1
            ORDER BY GrandsPrix.ID
            LIMIT 1
        )
        WHERE GrandPrixID IS NULL AND Status != 'cancelled'
    """)


def update_schedules(cursor):
    current_year = datetime.datetime.now().year
    for year in range(SCHEDULE_FIRST_SEASON, current_year + 1):
        cursor.execute("SELECT 1 FROM ScheduleVersions WHERE Season = ? LIMIT 1", (year,))
        has_version = cursor.fetchone() is not None
        # A finished season's calendar can no longer change, so it is only snapshotted
        # once. The current season is checked on every run.
        if has_version and year != current_year:
            continue
        if save_schedule_snapshot(cursor, year):
            print(f"Schedule snapshot saved for {year}")
    link_schedule_to_grandsprix(cursor)


# --- Circuit markers ----------------------------------------------------------
# Positions of corners, marshal lights and marshal sectors, and the rotation of each circuit's
# coordinate system, from MultiViewer (the data FastF1 exposes as CircuitInfo). It is drawn by
# hand, so it is good for annotating maps but not precise.

MULTIVIEWER_API_URL = 'https://api.multiviewer.app/api/v1/circuits'
MULTIVIEWER_FIRST_SEASON = 2018  # MultiViewer has no records for earlier seasons

# Circuits.ID -> MultiViewer circuit key. A circuit without an entry has no MultiViewer data
# (yet): Circuito de Madring, for example. A layout can belong to more than one circuit (the
# Red Bull Ring layout was the A1-Ring's), so a layout uses the entry of the circuit it was
# raced as most recently.
MULTIVIEWER_CIRCUIT_KEYS = {
    1030: 147,  # Algarve International Circuit
    454: 6,     # Imola
    1027: 146,  # Mugello (marshal sectors only)
    7: 39,      # Monza
    435: 65,    # Mexico City
    486: 14,    # Interlagos
    716: 63,    # Bahrain
    943: 144,   # Baku
    327: 23,    # Montreal
    22: 55,     # Zandvoort
    202: 28,    # Paul Ricard
    514: 15,    # Barcelona-Catalunya
    2: 22,      # Monaco
    5: 7,       # Spa-Francorchamps
    877: 9,     # Austin
    192: 34,    # Hockenheim
    431: 4,     # Hungaroring
    1032: 59,   # Istanbul
    1075: 149,  # Jeddah
    1139: 152,  # Las Vegas
    1074: 150,  # Lusail
    800: 61,    # Marina Bay
    582: 10,    # Melbourne
    1081: 151,  # Miami
    13: 72,     # Nurburgring
    905: 19,    # Red Bull Ring
    729: 49,    # Shanghai
    1: 2,       # Silverstone
    913: 79,    # Sochi
    451: 46,    # Suzuka
    820: 70,    # Yas Marina
}

# A layout that is a different MultiViewer circuit from the rest of its circuit, keyed by
# CircuitLayouts.FirstGrandPrix. The 2020 Sakhir Grand Prix was raced on Bahrain's outer track,
# which MultiViewer lists separately (and without any markers).
MULTIVIEWER_LAYOUT_KEY_OVERRIDES = {'2020 Sakhir Grand Prix': 148}

MULTIVIEWER_MARKER_TYPES = (('corners', 'corner'), ('marshalLights', 'marshal_light'), ('marshalSectors', 'marshal_sector'))


def get_multiviewer_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode('utf-8'))


def get_multiviewer_markers(key, year):
    """One MultiViewer record as (marker rows, rotation). Each marker row is
    (type, number, letter, x, y, angle)."""
    record = get_multiviewer_json(f"{MULTIVIEWER_API_URL}/{key}/{year}")
    # For a year it has no record for, MultiViewer silently answers with its nearest record.
    # The circuit list says which years exist, so this should never differ, but data from
    # another season must not be stored by accident.
    if record['circuitKey'] != key or record['year'] != year:
        raise ValueError(f"Asked MultiViewer for circuit {key} in {year}, but got circuit {record['circuitKey']} in {record['year']}")

    markers = []
    for field, marker_type in MULTIVIEWER_MARKER_TYPES:
        for entry in record.get(field) or []:
            angle = entry.get('angle')
            markers.append((marker_type, int(entry['number']), str(entry.get('letter') or ''),
                            float(entry['trackPosition']['x']), float(entry['trackPosition']['y']),
                            None if angle is None else float(angle)))
    rotation = record.get('rotation')
    return markers, None if rotation is None else float(rotation)


def update_circuit_markers(cursor):
    listing = get_multiviewer_json(MULTIVIEWER_API_URL)

    cursor.execute("""
        SELECT cl.ID, cl.FirstGrandPrix, MIN(g.Season), MAX(g.Season), cl.MarkersSourceYear
        FROM GrandsPrix g
        JOIN CircuitLayouts cl ON g.CircuitLayoutID = cl.ID
        GROUP BY cl.ID
        HAVING MAX(g.Season) >= ?
    """, (MULTIVIEWER_FIRST_SEASON,))

    circuits = {}  # MultiViewer circuit key -> the layouts of it that MultiViewer has a record for
    for layout_id, first_grandprix, first_season, last_season, stored_year in cursor.fetchall():
        cursor.execute("""
            SELECT g.CircuitID, c.CircuitName
            FROM GrandsPrix g
            JOIN Circuits c ON g.CircuitID = c.ID
            WHERE g.CircuitLayoutID = ?
            GROUP BY g.CircuitID
            ORDER BY MAX(g.Season) DESC
        """, (layout_id,))
        layout_circuits = cursor.fetchall()
        circuit_name = layout_circuits[0][1]

        key = MULTIVIEWER_LAYOUT_KEY_OVERRIDES.get(first_grandprix)
        if key is None:
            key = next((MULTIVIEWER_CIRCUIT_KEYS[circuit_id] for circuit_id, _ in layout_circuits if circuit_id in MULTIVIEWER_CIRCUIT_KEYS), None)
        if key is None:
            print(f"No MultiViewer circuit for {circuit_name}, no markers saved")
            continue
        if str(key) not in listing:
            raise ValueError(f"MultiViewer has no circuit {key}, which is the one set for {circuit_name}")

        # The latest record from a season this layout was raced in. MultiViewer's records
        # are per race, so this is the most recent race held on the layout that it has.
        years = [year for year in listing[str(key)]['years'] if first_season <= year <= last_season]
        if not years:
            continue
        circuits.setdefault(key, []).append({
            'layout_id': layout_id, 'circuit_name': circuit_name, 'key': key, 'record_year': max(years),
            'first_season': first_season, 'last_season': last_season, 'stored_year': stored_year,
        })

    saved = 0
    for layouts in circuits.values():
        if all(layout['stored_year'] == layout['record_year'] for layout in layouts):
            continue  # nothing new from MultiViewer since the last run
        for layout in layouts:
            layout['markers'], layout['rotation'] = get_multiviewer_markers(layout['key'], layout['record_year'])
            time.sleep(0.25)

        # MultiViewer can copy a circuit's newest markers onto its records for older seasons:
        # its 2019 Australian Grand Prix record has exactly the markers of the 2022 one, though
        # the circuit was redesigned in between. Two layouts of the same circuit cannot both
        # have identical markers, so only the layout raced most recently keeps them.
        same_markers = {}
        for layout in layouts:
            if layout['markers']:
                geometry = tuple((marker[0], marker[1], marker[2], round(marker[3]), round(marker[4])) for marker in layout['markers'])
                same_markers.setdefault(geometry, []).append(layout)
        keep = {max(group, key=lambda layout: (layout['last_season'], layout['first_season']))['layout_id'] for group in same_markers.values()}

        for layout in layouts:
            cursor.execute("DELETE FROM CircuitMarkers WHERE CircuitLayoutID = ?", (layout['layout_id'],))
            rotation = None
            if layout['layout_id'] in keep:
                cursor.executemany("""
                    INSERT INTO CircuitMarkers (CircuitLayoutID, MarkerType, Number, Letter, X, Y, Angle)
                    VALUES (?,?,?,?,?,?,?)
                """, [(layout['layout_id'], *marker) for marker in layout['markers']])
                rotation = layout['rotation']
                saved += 1
            elif layout['markers']:
                print(f"No markers saved for {layout['circuit_name']} layout {layout['layout_id']}: MultiViewer gives it the same markers as a later layout")
            else:
                print(f"No markers saved for {layout['circuit_name']} layout {layout['layout_id']}: MultiViewer has none for it")
            # The year is stored even when nothing was saved, so that this layout is not fetched again.
            cursor.execute("UPDATE CircuitLayouts SET Rotation = ?, MarkersSourceYear = ? WHERE ID = ?",
                           (rotation, layout['record_year'], layout['layout_id']))
    if saved:
        print(f"Circuit markers saved for {saved} layouts")


#This function gets the birthdate and nationality of a driver from their statsf1.com page. 
def fetch_driver_info(driver_name):
    # Convert "George Russell" → "george-russell"
    slug = normalize_name(driver_name).replace(" ", "-")
    url = f"https://www.statsf1.com/en/{slug}.aspx"

    open_url(url)  # You provide this and it returns a BeautifulSoup object

    # --- Nationality ---
    nationality_tag = soup.find("a", id="ctl00_CPH_Main_HL_Pays")
    nationality = nationality_tag.text.strip() if nationality_tag else None
    # Normalise French/other localized names to English equivalents
    if nationality:
        if nationality == "Irlande":
            nationality = "Ireland"

    # --- Birthdate ---
    birthdate = None
    birth_field = soup.find("div", class_="field", string=re.compile(r"Born the"))
    if not birth_field:
        for div in soup.find_all("div", class_="field"):
            if "Born the" in div.text:
                birth_field = div
                break

    if birth_field:
        match = re.search(r"Born the ([0-9]{1,2} [a-zA-Z]+ [0-9]{4})", birth_field.text)
        if match:
            try:
                birthdate_obj = datetime.datetime.strptime(match.group(1), "%d %B %Y")
                birthdate = birthdate_obj.strftime("%Y-%m-%d")
            except ValueError:
                print(f"Date format error for {driver_name}: {match.group(1)}")

    return nationality, birthdate


def translate_reason(text):
    if not text:
        return None
    text = text.strip()
    if not text:
        return None
    try:
        translated = GoogleTranslator(source='auto', target='en').translate(text)
        return translated
    except Exception:
        return text


def fetch_deaths_from_statsf1():
    url = 'https://www.statsf1.com/en/statistiques/pilote/divers/decede.aspx'
    open_url(url)

    table = soup.find('table', id='ctl00_CPH_Main_GV_Stats')
    if not table:
        # Try finding by class
        table = soup.find('table', class_='sortable')
    if not table:
        return []

    rows = table.find_all('tr')
    deaths = []
    for tr in rows[1:-1]:
        cells = tr.find_all('td')
        if len(cells) < 4:
            continue
        name = cells[0].get_text(strip=True)
        date_text = cells[1].get_text(strip=True)
        age_text = cells[2].get_text(strip=True)
        reason_text = cells[3].get_text(strip=True)

        # Parse date (e.g., '4 may 2026') — be forgiving if empty or unexpected
        death_date = None
        # Normalize whitespace and NBSPs
        date_text_clean = unicodedata.normalize('NFKC', date_text).replace('\xa0', ' ').strip()
        if date_text_clean:
            try:
                death_date = datetime.datetime.strptime(date_text_clean.title(), "%d %B %Y").strftime('%Y-%m-%d')
            except Exception:
                try:
                    death_date = datetime.datetime.strptime(date_text_clean, "%d/%m/%Y").strftime('%Y-%m-%d')
                except Exception as exc:
                    print(f"Warning: failed to parse death date '{date_text}' for '{name}': {exc}")
                    death_date = None

        death_age = None
        m = re.search(r'(\d+)', age_text)
        if m:
            try:
                death_age = int(m.group(1))
            except Exception:
                death_age = None

        reason_raw = reason_text if reason_text and reason_text.strip() else None

        deaths.append({
            'name': name,
            'date': death_date,
            'age': death_age,
            'reason_raw': reason_raw
        })

    return deaths


def update_deaths_db(cursor, conn):

    cursor.execute("SELECT ID, Name, DeathDate FROM Drivers")
    db_rows = cursor.fetchall()
    db_map = {normalize_name(r[1]): (r[0], r[1], r[2]) for r in db_rows}

    deaths = fetch_deaths_from_statsf1()
    for item in deaths:
        name = item['name']
        norm = normalize_name(format_name_from_caps(name))
        date = item['date']
        age = item['age']
        reason_raw = item.get('reason_raw')

        if norm in db_map:
            driver_id, db_name, existing_date = db_map[norm]
            if not existing_date and date:
                reason_translated = translate_reason(reason_raw) if reason_raw else None
                cursor.execute("UPDATE Drivers SET DeathDate = ?, DeathAge = ?, DeathReason = ? WHERE ID = ?",
                               (date, age, reason_translated, driver_id))
                conn.commit()
                print(f"New death recorded: {db_name} ({date})")
            elif existing_date and existing_date != date:
                reason_translated = translate_reason(reason_raw) if reason_raw else None
                cursor.execute("UPDATE Drivers SET DeathDate = ?, DeathAge = ?, DeathReason = ? WHERE ID = ?",
                               (date, age, reason_translated, driver_id))
                conn.commit()
                print(f"Updated death info for: {db_name} ({date})")


def fetch_family_relations_from_statsf1():
    """Fetch family relationships data from sampledata.html"""
    open_url("https://www.statsf1.com/en/statistiques/pilote/divers/famille.aspx")

    table = soup.find("table", {"class": "sortable"})

    if not table:
        print("Warning: Could not find the family relationships")
        return []

    rows = table.find_all("tr")[1:]  # Skip header
    global family_pairs
    family_pairs = []

    for row in rows:
        tds = row.find_all("td")
        if len(tds) >= 3:
            # Extract driver names from links
            first_driver_link = tds[0].find("a")
            second_driver_link = tds[1].find("a")
            relation_type = tds[2].get_text(strip=True).replace("&amp;", "&")

            if first_driver_link and second_driver_link:
                first_driver = first_driver_link.get_text(strip=True)
                second_driver = second_driver_link.get_text(strip=True)
                family_pairs.append({
                    'driver1': first_driver,
                    'driver2': second_driver,
                    'relation_type': relation_type
                })

    return family_pairs



#This function parses the points system from the statsf1.com seasons page.
def parse_points_system(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')

    drivers_info = {
        "scores": "All Scores",
        "pointssharedforsharedcars": False,
        "grandprix": {},
        "sprint": None
    }

    constructors_info = {
        "scores": "All Scores",
        "topscoring": False,
        "grandprix": {},
        "sprint": None
    }

    drivers_found = False
    constructors_found = False

    for section in soup.find_all('div', class_='aligncenter'):
        table = section.find('table', class_='bareme')
        if not table:
            continue

        rows = table.find_all('tr')
        full_text = section.get_text(separator=' ').lower()

        # -------- DRIVERS --------
        if not drivers_found and not constructors_found:
            drivers_found = True

            # Get all score-related lines before the table
            score_lines = []
            for elem in section.contents:
                if getattr(elem, 'name', None) == 'table':
                    break
                if isinstance(elem, str):
                    line = elem.strip()
                    if "score" in line.lower():
                        score_lines.append(line)
            drivers_info["scores"] = "\n".join(score_lines) if score_lines else "All Scores"

            if "points shared for shared drives" in full_text:
                drivers_info["pointssharedforsharedcars"] = True

            if len(rows) == 2:
                # Use header row to determine keys
                header_cells = [cell.get_text(strip=True) for cell in rows[0].find_all('td')[1:]]
                point_cells = rows[1].find_all('td')[1:]
                for header, cell in zip(header_cells, point_cells):
                    header_clean = re.sub(r'\s*\d+(st|nd|rd|th)?\.?$', '', header, flags=re.IGNORECASE)
                    value = cell.get_text(strip=True).replace("*", "")
                    # Use header as key, or map to integer if it's a position
                    if header_clean.lower().startswith('fastest'):
                        if "(* only by finishing in the top ten)" or "(* only by finishing in the top 10)" in full_text:
                            key = "Fastest Lap (only for finishing in the top 10)"
                        else:
                            key = "Fastest Lap"
                        drivers_info["grandprix"][key] = value
                    else:
                        # Try to extract the position number
                        match = re.match(r'(\d+)', header)
                        key = match.group(1) if match else header
                    drivers_info["grandprix"][key] = int(value) if value.isdigit() else 0

            if len(rows) >= 3:
                # Use header row to determine keys
                header_cells = [cell.get_text(strip=True) for cell in rows[0].find_all('td')[1:]]
                point_cells = rows[1].find_all('td')[2:]
                for header, cell in zip(header_cells, point_cells):
                    header_clean = re.sub(r'\s*\d+(st|nd|rd|th)?\.?$', '', header, flags=re.IGNORECASE)
                    value = cell.get_text(strip=True).replace("*", "")
                    # Use header as key, or map to integer if it's a position
                    if header_clean.lower().startswith('fastest'):
                        if "(* only by finishing in the top ten)" or "(* only by finishing in the top 10)" in full_text:
                            key = "Fastest Lap (only for finishing in the top 10)"
                        else:
                            key = "Fastest Lap"
                        drivers_info["grandprix"][key] = value
                    else:
                        # Try to extract the position number
                        match = re.match(r'(\d+)', header)
                        key = match.group(1) if match else header
                    drivers_info["grandprix"][key] = int(value) if value.isdigit() else 0                
                drivers_info["sprint"] = {}
                for i, cell in enumerate(rows[2].find_all('td')[2:], start=1):
                    value = cell.get_text(strip=True)
                    if value != "":
                        drivers_info["sprint"][str(i)] = int(value) if value.isdigit() else 0

        # -------- CONSTRUCTORS --------
        elif not constructors_found and drivers_found:
            constructors_found = True

            constructor_score_lines = []
            for elem in section.contents:
                if getattr(elem, 'name', None) == 'table':
                    break
                if isinstance(elem, str):
                    line = elem.strip()
                    if "score" in line.lower():
                        constructor_score_lines.append(line)
            constructors_info["scores"] = "\n".join(constructor_score_lines) if constructor_score_lines else "All Scores"

            if "point only for highest placed car" in full_text:
                constructors_info["topscoring"] = True

            if len(rows) == 2:
                # Use header row to determine keys (skip only the first column for constructors)
                header_cells = [cell.get_text(strip=True) for cell in rows[0].find_all('td')[1:]]
                point_cells = rows[1].find_all('td')[1:]
                for header, cell in zip(header_cells, point_cells):
                    header_clean = re.sub(r'\s*\d+(st|nd|rd|th)?\.?$', '', header, flags=re.IGNORECASE)
                    value = cell.get_text(strip=True).replace("*", "")
                    if header_clean.lower().startswith('fastest'):
                        if "(* only by finishing in the top ten)" or "(* only by finishing in the top 10)" in full_text:
                            key = "Fastest Lap (only for finishing in the top 10)"
                        else:
                            key = "Fastest Lap"
                        drivers_info["grandprix"][key] = value
                    else:
                        match = re.match(r'(\d+)', header)
                        key = match.group(1) if match else header
                    constructors_info["grandprix"][key] = int(value) if value.isdigit() else 0

            if len(rows) >= 3:
                # Use header row to determine keys (skip only the first column for constructors)
                header_cells = [cell.get_text(strip=True) for cell in rows[0].find_all('td')[1:]]
                point_cells = rows[1].find_all('td')[2:]
                for header, cell in zip(header_cells, point_cells):
                    header_clean = re.sub(r'\s*\d+(st|nd|rd|th)?\.?$', '', header, flags=re.IGNORECASE)
                    value = cell.get_text(strip=True).replace("*", "")
                    if header_clean.lower().startswith('fastest'):
                        if "(* only by finishing in the top ten)" or "(* only by finishing in the top 10)" in full_text:
                            key = "Fastest Lap (only for finishing in the top 10)"
                        else:
                            key = "Fastest Lap"
                        drivers_info["grandprix"][key] = value
                    else:
                        match = re.match(r'(\d+)', header)
                        key = match.group(1) if match else header
                    constructors_info["grandprix"][key] = int(value) if value.isdigit() else 0                
                constructors_info["sprint"] = {}
                for i, cell in enumerate(rows[2].find_all('td')[1:], start=1):
                    value = cell.get_text(strip=True)
                    if value != "":
                        constructors_info["sprint"][str(i)] = int(value) if value.isdigit() else 0

    # Fallback if nothing found
    if not drivers_found:
        print("Drivers' points table not found.")
        return None, None

    if not constructors_found:
        print("Constructors' points table not found.")
        return drivers_info, {}

    return drivers_info, constructors_info

def extract_regulations_notes(span):
    """
    Extracts only the textual notes from the regulations <span>, before the 'Regulations' label or <table>.
    """
    notes = []
    for element in span.contents:
        if element.name == 'br':
            continue
        if isinstance(element, str):
            stripped = element.strip()
            if stripped:
                notes.append(stripped)
        elif element.name == 'strong' and 'regulations' in element.get_text(strip=True).lower():
            break  # Stop when we hit the start of the actual 'Regulations' section
        else:
            break  # Stop at anything else unexpected (e.g., table)
    return notes if notes else None

def standardize_circuit_name_with_year(circuit, year):
    """
    Converts a circuit name to its original/standard name based on the year F1 raced there.
    Only applies name changes for the specific years the circuit actually hosted F1.
    
    Args:
        circuit (str): The circuit name
        year (int): The year F1 raced at the circuit
        
    Returns:
        str: The circuit name during that specific year
    """
    
    # AUSTRIA - Red Bull Ring / Österreichring / A1-Ring / Zeltweg
    if circuit == "Red Bull Ring":
        if 1970 <= year <= 1987:
            circuit = "Österreichring"
        elif 1997 <= year <= 2003:
            circuit = "A1-Ring"
        elif year >= 2014:
            circuit = "Red Bull Ring"
        elif year == 1964:
            circuit = "Zeltweg Airfield"
    
    # MEXICO - Autódromo Hermanos Rodríguez / Magdalena Mixhuca
    if circuit == "Autódromo Hermanos Rodríguez":
        if 1963 <= year <= 1970:
            circuit = "Autódromo Magdalena Mixhuca"
        elif year >= 1986:
            circuit = "Autódromo Hermanos Rodríguez"
    
    # CANADA - Canadian Tire Motorsport Park / Mosport Park
    elif circuit == "Canadian Tire Motorsport Park":
        if 1967 <= year <= 1977:
            circuit = "Mosport Park"
        elif year >= 2007:
            circuit = "Canadian Tire Motorsport Park"
    
    elif circuit == "Mosport Park":
        if 1967 <= year <= 1977:
            circuit = "Mosport Park"
        elif year >= 2007:
            circuit = "Canadian Tire Motorsport Park"
    
    # BRAZIL - Autódromo José Carlos Pace / Interlagos
    elif circuit == "Autódromo José Carlos Pace":
        if 1973 <= year <= 1989:
            circuit = "Autódromo de Interlagos"
        elif year >= 1990:
            circuit = "Autódromo José Carlos Pace"
    
    #BRAZIL - Autódromo Internacional Nelson Piquet/ Autódromo de Jacarepaguá
    elif circuit == "Autódromo Internacional Nelson Piquet":
        if 1978 <= year <= 1987:
            circuit = "Autódromo de Jacarepaguá"
        elif 1988 <= year:
            circuit = "Autódromo Internacional Nelson Piquet"
    
    # ITALY (IMOLA) - Autodromo Enzo e Dino Ferrari / Autodromo Dino Ferrari
    elif circuit == "Autodromo Internazionale Enzo e Dino Ferrari":
        if 1980 <= year <= 1987:
            circuit = "Autodromo Dino Ferrari"
        elif year >= 1988:
            circuit = "Autodromo Enzo e Dino Ferrari"
    
    #JAPAN - TI Circuit Aida / Okayama International Circuit
    elif circuit == "Okayama International Circuit":
        if 1994 <= year <= 1995:
            circuit = "TI Circuit Aida"
        elif year >= 2005:
            circuit = "Okayama International Circuit"
    #ARGENTINA - Autódromo Oscar y Juan Gálvez / Autódromo 17 de Octubre
    elif circuit == "Autódromo Juan y Oscar Gálvez":
        if 1952 <= year <= 1954:
            circuit = "Autódromo 17 de Octubre"
        elif 1955 <= year <= 1960:
            circuit = "Autódromo Municipal Ciudad de Buenos Aires"
        elif 1961 <= year <= 1989:
            circuit = "Autódromo Municipal del Parque Almirante Brown de la Ciudad de Buenos Aires"
        elif year >= 1995:
            circuit = "Autódromo Oscar Alfredo Gálvez"
    #FRANCE - rename
    elif circuit == "Charade Circuit":
        circuit = "Circuit de Charade"
    # CANADA (MONTREAL) - Circuit Gilles Villeneuve / Circuit Île Notre-Dame
    elif circuit == "Circuit Gilles Villeneuve":
        if year == 1978:
            circuit = "Circuit Île Notre-Dame"
        elif year >= 1982:
            circuit = "Circuit Gilles Villeneuve"
    
    elif circuit == "Circuit Île Notre-Dame":
        if year == 1978:
            circuit = "Circuit Île Notre-Dame"
        elif year >= 1982:
            circuit = "Circuit Gilles Villeneuve"
    
    # TURKEY - Istanbul Park / Istanbul Otodrom
    elif circuit == "Istanbul Park":
        if 2005 <= year <= 2011:
            circuit = "Istanbul Otodrom"
        elif 2020 <= year <= 2021:
            circuit = "Istanbul Park"
    
    # RUSSIA - Sirius Autodrom / Sochi Autodrom (F1 only raced 2014-2021)
    elif circuit == "Sirius Autodrom":
        if 2014 <= year <= 2021:
            circuit = "Sochi Autodrom"
        # Note: Sirius Autodrom is post-2024 rename, F1 never raced there
    
    elif circuit == "Sochi Autodrom":
        if 2014 <= year <= 2021:
            circuit = "Sochi Autodrom"
    
    # AZERBAIJAN - Baku City Circuit (circuit name never changed, only race name)
    # 2016: European Grand Prix
    # 2017+: Azerbaijan Grand Prix
    # Circuit name remained "Baku City Circuit" throughout
    
    return circuit

# =========================
# Driver matching utilities
# =========================

def compute_best_time_from_lapdata(lap_json):
    """
    Return the best (minimum) lap time in seconds from a TracingInsights laptimes JSON.
    Returns None if no valid times found.
    """
    times = []
    for t in lap_json.get("time", []):
        if t in (None, "None"):
            continue
        try:
            times.append(float(Decimal(str(t)).quantize(Decimal('0.001'))))
        except Exception:
            continue
    return min(times) if times else None


def generate_abbr_from_name(fullname, existing_abbrs):
    """
    Generate a deterministic, unique 3-letter abbreviation from a driver's full name.
    Disambiguates shared surnames (LEC → LEL), then given-name fallback.
    """
    parts = [p for p in fullname.split() if p]
    if not parts:
        raise ValueError("Empty name for abbreviation")

    surname = parts[-1].upper()
    given = parts[0].upper()

    # Base: first 3 letters of surname
    base = (surname + "XXX")[:3]
    if base not in existing_abbrs:
        return base

    # Replace 3rd character with later surname letters
    for i in range(2, len(surname)):
        cand = surname[:2] + surname[i]
        if cand not in existing_abbrs:
            return cand

    # Fallback: surname + given initial
    cand = (surname[:2] + given[0])[:3]
    if cand not in existing_abbrs:
        return cand

    raise ValueError(f"Unable to generate unique abbreviation for {fullname}")


def match_tracing_abbr_to_entrant(
    abbr,
    entrants,
    tracing_lap_json=None,
    f1_best_times=None,
    lap_by_lap_map=None,
    tracing_team=None,
):
    """
    Match a TracingInsights driver abbreviation to a session entrant.

    Matching priority:
    1) Existing lap_by_lap / race / sprint mapping (authoritative)
    2) Best lap time match (±0.05s)
    3) Direct entrant code comparison
    4) Deterministic abbreviation generation
    4.5) Initial + surname-prefix + team (guarded, handles 'de Vries' → DEV)
    5) Team-only (ONLY if unique, excluding already-mapped drivers)
    Otherwise → raise ValueError
    """

    abbr = abbr.upper()

    # --------------------------------
    # Track already-mapped drivers
    # --------------------------------
    already_mapped_drivers = set()
    if lap_by_lap_map:
        for v in lap_by_lap_map.values():
            already_mapped_drivers.add(normalize_name(v["driver"]))

    # -----------------
    # 1) Trusted mapping
    # -----------------
    if lap_by_lap_map and abbr in lap_by_lap_map:
        mapped_driver = normalize_name(lap_by_lap_map[abbr]["driver"])
        for e in entrants:
            if normalize_name(e.get("driver", "")) == mapped_driver:
                return e

    # -----------------
    # 2) Best lap match
    # -----------------
    tracing_best = (
        compute_best_time_from_lapdata(tracing_lap_json)
        if tracing_lap_json
        else None
    )

    if tracing_best is not None and f1_best_times:
        tol = 0.05
        time_keys = [
            k for k, t in f1_best_times.items()
            if t is not None and abs(float(t) - tracing_best) <= tol
        ]

        if len(time_keys) == 1:
            key = str(time_keys[0]).upper()
            for e in entrants:
                if (
                    str(e.get("driver", "")).upper() == key
                    or str(e.get("id", "")).upper() == key
                    or str(e.get("name", "")).upper() == key
                ):
                    return e

        if len(time_keys) > 1 and tracing_team:
            candidates = []
            for key in time_keys:
                key = str(key).upper()
                for e in entrants:
                    if (
                        (
                            str(e.get("driver", "")).upper() == key
                            or str(e.get("id", "")).upper() == key
                            or str(e.get("name", "")).upper() == key
                        )
                        and tracing_team.lower() in str(e.get("team", "")).lower()
                    ):
                        candidates.append(e)

            if len(candidates) == 1:
                return candidates[0]

            if len(candidates) > 1:
                raise ValueError(
                    f"Ambiguous best-lap match for {abbr} "
                    f"(team={tracing_team}): {[c.get('driver') for c in candidates]}"
                )

    # --------------------------------
    # 3) Direct entrant code comparison
    # --------------------------------
    for e in entrants:
        if str(e.get("driver", "")).upper() == abbr:
            return e

    # --------------------------------
    # 4) Deterministic abbreviation gen
    # --------------------------------
    entrants_sorted = sorted(
        entrants,
        key=lambda e: (e.get("name") or e.get("driver") or "").upper()
    )

    existing_abbrs = {
        str(e.get("driver")).upper()
        for e in entrants
        if isinstance(e.get("driver"), str) and len(e["driver"]) == 3
    }

    for e in entrants_sorted:
        name = e.get("name") or e.get("driver")
        cand = generate_abbr_from_name(
            str(normalize_name(name)), existing_abbrs
        )
        existing_abbrs.add(cand)
        if cand == abbr:
            return e

    # --------------------------------
    # 4.5) Initial + surname-prefix + team
    # --------------------------------
    if tracing_team:
        particles = {"de", "da", "di", "van", "von", "le", "la"}
        candidates = []

        for e in entrants:
            name = e.get("driver") or e.get("name")
            team = str(e.get("team", ""))

            if not name or tracing_team.lower() not in team.lower():
                continue

            norm = normalize_name(name)
            if norm in already_mapped_drivers:
                continue

            parts = norm.split()
            if len(parts) < 2:
                continue

            first_initial = parts[0][0]

            if parts[-2] in particles:
                last = parts[-2] + parts[-1]   # de + vries → devries
            else:
                last = parts[-1]

            last_two = last[:2]
            generated = (first_initial + last_two).upper()

            if generated == abbr:
                candidates.append(e)

        if len(candidates) == 1:
            return candidates[0]

        if len(candidates) > 1:
            raise ValueError(
                f"Ambiguous initial+surname match for {abbr} "
                f"(team={tracing_team}): {[c.get('driver') for c in candidates]}"
            )

    '''
    {"drivers": [{"driver": "ARO", "team": "Red Bull Racing"}, {"driver": "BEG", "team": "Williams"}, {"driver": "OSU", "team": "AlphaTauri"}, 
    {"driver": "FOR", "team": "McLaren"}, {"driver": "FCO", "team": "Alpine"}, {"driver": "BOY", "team": "Red Bull Racing"}, 
    {"driver": "MON", "team": "Aston Martin"}, {"driver": "BRO", "team": "Ferrari"}, {"driver": "VIL", "team": "Aston Martin"}, 
    {"driver": "GRA", "team": "Haas F1 Team"}, {"driver": "COH", "team": "AlphaTauri"}, {"driver": "MAR", "team": "Williams"}, 
    {"driver": "MAN", "team": "Alfa Romeo"}, {"driver": "TBA", "team": "Haas F1 Team"}, {"driver": "SHI", "team": "Alpine"}, 
    {"driver": "HAM", "team": "Mercedes"}, {"driver": "SAI", "team": "Ferrari"}, {"driver": "RUS", "team": "Mercedes"}, 
    {"driver": "BOT", "team": "Alfa Romeo"}, {"driver": "PIA", "team": "McLaren"}]}
    '''
    if gp == "2023 Hungarian Grand Prix":
        for ent in entrants:
            if abbr == "FOR":
                if ent['driver'] == "Lando Norris":
                    return ent
            elif abbr == "BRO":
                if ent['driver'] == "Charles Leclerc":
                    return ent
            elif abbr == "MAN":
                if ent['driver'] == "Zhou Guanyu":
                    return ent
            elif abbr == "ARO":
                if ent['driver'] == "Max Verstappen":
                    return ent
            elif abbr == "BEG":
                if ent['driver'] == "Logan Sargeant":
                    return ent
            elif abbr == "BOY":
                if ent['driver'] == "Sergio Perez":
                    return ent
            elif abbr == "COH":
                if ent['driver'] == "Yuki Tsunoda":
                    return ent  
            elif abbr == "FCO":
                if ent['driver'] == "Pierre Gasly":
                    return ent    
            elif abbr == "GRA":
                if ent['driver'] == "Kevin Magnussen":
                    return ent
            elif abbr == "MAR":
                if ent['driver'] == "Alexander Albon":
                    return ent  
            elif abbr == "MON":
                if ent['driver'] == "Fernando Alonso":
                    return ent
            elif abbr == "OSU":
                if ent['driver'] == "Daniel Ricciardo":
                    return ent
            elif abbr == "SHI":
                if ent['driver'] == "Esteban Ocon":
                    return ent 
            elif abbr == "TBA":
                if ent['driver'] == "Nico Hülkenberg":
                    return ent  
            elif abbr == "VIL":
                if ent['driver'] == "Lance Stroll":
                    return ent     
    '''
    {"drivers": [{"driver": "BAD", "team": "Red Bull Racing"}, {"driver": "GAS", "team": "Alpine"}, {"driver": "ANT", "team": "Mercedes"}, 
    {"driver": "ALO", "team": "Aston Martin"}, {"driver": "LEC", "team": "Ferrari"}, {"driver": "STR", "team": "Aston Martin"}, {"driver": "TSU", "team": "Red Bull Racing"}, 
    {"driver": "VOI", "team": "Williams"}, {"driver": "MAR", "team": "Kick Sauber"}, {"driver": "LAW", "team": "Racing Bulls"}, {"driver": "CHO", "team": "Haas F1 Team"}, 
    {"driver": "NOR", "team": "McLaren"}, {"driver": "HAM", "team": "Ferrari"}, {"driver": "CAM", "team": "Kick Sauber"}, 
    {"driver": "SAI", "team": "Williams"}, {"driver": "HAD", "team": "Racing Bulls"}, {"driver": "ARO", "team": "Alpine"}, {"driver": "RUS", "team": "Mercedes"}, 
    {"driver": "BEA", "team": "Haas F1 Team"}, {"driver": "DUN", "team": "McLaren"}]}
    '''
    if gp == "2025 Italian Grand Prix": 
        for en in entrants:
            if abbr == "BAD":
                if en['driver'] == "Max Verstappen":
                    return en       
            elif abbr == "VOI":
                if en['driver'] == "Alexander Albon":
                    return en
            elif abbr == "MAR":
                if en['driver'] == "Nico Hülkenberg":
                    return en            
            elif abbr == "CAM":
                if en['driver'] == "Gabriel Bortoleto":
                    return en            
            elif abbr == "CHO":
                if en['driver'] == "Esteban Ocon":
                    return en    
            
    # -------------------------
    # 5) Team-only (guarded!)
    # -------------------------
    if tracing_team:
        candidates = [
            e for e in entrants
            if (
                tracing_team.lower() in str(e.get("team", "")).lower()
                and normalize_name(e.get("driver", "") or e.get("name", ""))
                not in already_mapped_drivers
            )
        ]

        if len(candidates) == 1:
            return candidates[0]

        if len(candidates) > 1:
            raise ValueError(
                f"Ambiguous team-only match for {abbr} "
                f"(team={tracing_team}): {[c.get('driver') for c in candidates]}"
            )

    # -----------------
    # Final hard fail
    # -----------------
    raise ValueError(f"Could not match tracing abbreviation '{abbr}'")


def parse_regulations(html_content):
    """
    Parses the regulations for a given season from the provided HTML content.
    Groups technical regulations under their categories (e.g., 'Engine', 'Fuel').
    """
    soup = BeautifulSoup(html_content, 'html.parser')

    # Find the regulations section
    regulations_section = soup.find('div', id='ctl00_CPH_Main_P_Note1', class_='yearinfo')
    if not regulations_section:
        print("Regulations section not found.")
        return None

    regulations = {}

    # Extract notes and tables
    regulations_notes = regulations_section.find('span', id='ctl00_CPH_Main_LB_Note1')
    if regulations_notes:
        notes = extract_regulations_notes(regulations_notes)
        regulations['notes'] = notes if notes else None

        # Find all tables in the span
        tables = regulations_notes.find_all('table')
        # If there are two tables, first is trophies, second is technical
        # If only one, it's technical
        tech_table = None
        if len(tables) == 2:
            # Trophies table (first)
            trophy_rows = tables[0].find_all('tr')
            for row in trophy_rows:
                cells = row.find_all('td')
                if len(cells) == 2:
                    key = cells[0].get_text(strip=True).rstrip(':')
                    value = cells[1].get_text(strip=True)
                    regulations[key] = value
            tech_table = tables[1]
        elif len(tables) == 1:
            tech_table = tables[0]

        # Parse technical regulations table
        if tech_table:
            rows = tech_table.find_all('tr')
            current_category = None
            for row in rows:
                th = row.find('th')
                if th and th.has_attr('colspan') and th['colspan'] == '2':
                    current_category = th.get_text(strip=True)
                    regulations[current_category] = {}
                else:
                    cells = row.find_all('td')
                    if len(cells) == 2:
                        key = cells[0].get_text(strip=True).rstrip(':')
                        value = cells[1].get_text(strip=True)
                        if current_category:
                            regulations[current_category][key] = value
                        else:
                            regulations[key] = value

    return regulations


def format_name_from_caps(raw_name):
    raw_name = (raw_name or "").strip()
    # Special case mappings for known corrections
    name_corrections = {
        "GIMMI BRUNI": "Gianmaria Bruni",
        "Gimmi Bruini": "Gianmaria Bruni",
        "Jose-Froilan Gonzalez": "José Froilán González",
        "Kimi Raikkonen": "Kimi Räikkönen",
        "Nico Hulkenberg": "Nico Hülkenberg",
        "Guanyu Zhou": "Zhou Guanyu"
    }
    
    # Check for exact matches (case-insensitive)
    for pattern, replacement in name_corrections.items():
        if raw_name.upper().replace("*", '') == pattern.upper():
            return replacement

    tokens = raw_name.split()
    def is_surname_like_token(token):
        letters = re.sub(r'[^A-Za-z]', '', token)
        if not letters:
            return False
        if letters == letters.upper():
            return True
        if len(letters) > 2 and letters[0].isupper() and any(ch.isupper() for ch in letters[1:]):
            return True
        return False

    if len(tokens) > 1:
        trailing_given = []
        for token in reversed(tokens):
            if is_surname_like_token(token):
                break
            trailing_given.insert(0, token)

        surname_block = tokens[:len(tokens) - len(trailing_given)]
        if trailing_given and surname_block and any(is_surname_like_token(tok) for tok in surname_block):
            tokens = trailing_given + surname_block

    parts = tokens
    formatted = []

    for part in parts:
        # Handle apostrophes (O'Connor, d'Ambrosio, etc.)
        if "'" in part:
            subparts = part.split("'")
            formatted_subparts = []
            for i, subpart in enumerate(subparts):
                # Handle hyphens within each apostrophe-separated part
                hyphen_parts = subpart.split('-')
                formatted_hyphen_parts = []
                for hyphen_part in hyphen_parts:
                    if hyphen_part:  # Skip empty strings
                        formatted_hyphen_parts.append(format_subpart(hyphen_part))
                formatted_subparts.append('-'.join(formatted_hyphen_parts))
            part = "'".join(formatted_subparts)
        else:
            # Handle hyphenated parts (no apostrophe)
            subparts = part.split('-')
            formatted_subparts = []
            for subpart in subparts:
                if subpart:  # Skip empty strings
                    formatted_subparts.append(format_subpart(subpart))
            part = '-'.join(formatted_subparts)
        
        formatted.append(part)

    return " ".join(formatted).replace("*", '')


def format_subpart(subpart):
    """Helper function to format a single name part"""
    if not subpart:
        return subpart
    
    # Convert to lowercase first, then capitalize
    subpart = subpart.lower().capitalize()
    
    # Handle Mc/Mac prefix
    if subpart.startswith("Mc") and len(subpart) > 2:
        subpart = "Mc" + subpart[2].upper() + subpart[3:]
    elif subpart.startswith("Mac") and len(subpart) > 3:
        subpart = "Mac" + subpart[3].upper() + subpart[4:]
    
    return subpart


_FEMALE_DRIVERS_CACHE = None

def fetch_female_drivers():
    """Fetch list of female drivers from statsf1 and return a set of normalized names."""
    global _FEMALE_DRIVERS_CACHE
    if _FEMALE_DRIVERS_CACHE is not None:
        return _FEMALE_DRIVERS_CACHE
    url = 'https://www.statsf1.com/en/statistiques/pilote/divers/femmes.aspx'
    try:
        open_url(url)
        table = soup.find('table', id='ctl00_CPH_Main_GV_Stats') or soup.find('table', class_='sortable')
        if not table:
            _FEMALE_DRIVERS_CACHE = set()
            return _FEMALE_DRIVERS_CACHE
        rows = table.find_all('tr')
        names = set()
        for tr in rows[1:-1]:
            cols = tr.find_all('td')
            if not cols:
                continue
            a = cols[1].find('a') if len(cols) > 1 else cols[0].find('a')
            if not a:
                continue
            raw = a.get_text(strip=True)
            try:
                formatted = format_name_from_caps(raw)
            except Exception:
                formatted = raw
            names.add(normalize_name(formatted))
        _FEMALE_DRIVERS_CACHE = names
        return names
    except Exception:
        _FEMALE_DRIVERS_CACHE = set()
        return _FEMALE_DRIVERS_CACHE


def is_female_driver(name):
    """Return True if driver is listed on statsf1 women page."""
    try:
        formatted = format_name_from_caps(name)
        return normalize_name(formatted) in fetch_female_drivers()
    except Exception:
        return False


def check_and_add_family_relations(driver_name, driver_id, cursor, family_pairs):
    """
    Check if a newly added driver is related to any drivers in the fetched family relations data.
    If a relation exists, add it bidirectionally to the FamilyRelations table.
    """
    # Normalize the new driver name for comparison
    new_driver_norm = normalize_name(driver_name)
    
    # Relationship type mapping
    relation_mapping = {
        "Father & Son": ("Father", "Son"),
        "Brothers": ("Brother", "Brother"),
        "Brother & Sister": ("Brother", "Sister"),
        "Half-Brother": ("Half-Brother", "Half-Brother"),
        "Grandfather & Grandson": ("Grandfather", "Grandson"),
        "Uncle & Nephew": ("Uncle", "Nephew"),
        "Great Uncle & Little Nephew": ("Great Uncle", "Grand Nephew"),
    }
    
    # Check if this driver appears in any family relationships
    for pair in family_pairs:
        driver1_html = pair['driver1']
        driver2_html = pair['driver2']
        relation_type = pair['relation_type']
        
        # Format names
        driver1_formatted = format_name_from_caps(driver1_html)
        driver2_formatted = format_name_from_caps(driver2_html)
        
        # Normalize for comparison
        driver1_norm = normalize_name(driver1_formatted)
        driver2_norm = normalize_name(driver2_formatted)
        
        if relation_type not in relation_mapping:
            continue
        
        rel1, rel2 = relation_mapping[relation_type]
        
        # Check if new driver matches driver1
        if new_driver_norm == driver1_norm:
            # Find driver2 in database
            cursor.execute("SELECT ID FROM Drivers WHERE LOWER(Name) = ?", (driver2_formatted.lower(),))
            result = cursor.fetchone()
            if result:
                related_driver_id = result[0]
                try:
                    cursor.execute(
                        "INSERT OR IGNORE INTO FamilyRelations (Driver, RelatedDriver, RelationType, DriverID, RelatedDriverID) VALUES (?, ?, ?, ?, ?)",
                        (driver_name, driver2_formatted, rel1, driver_id, related_driver_id)
                    )
                    cursor.execute(
                        "INSERT OR IGNORE INTO FamilyRelations (Driver, RelatedDriver, RelationType, DriverID, RelatedDriverID) VALUES (?, ?, ?, ?, ?)",
                        (driver2_formatted, driver_name, rel2, related_driver_id, driver_id)
                    )
                    print(f"  Family relation added: {driver_name} ({rel1}) <-> {driver2_formatted} ({rel2})")
                except Exception as e:
                    print(f"  Warning: Could not add family relation for {driver_name}: {e}")
        
        # Check if new driver matches driver2
        elif new_driver_norm == driver2_norm:
            # Find driver1 in database
            cursor.execute("SELECT ID FROM Drivers WHERE LOWER(Name) = ?", (driver1_formatted.lower(),))
            result = cursor.fetchone()
            if result:
                related_driver_id = result[0]
                try:
                    cursor.execute(
                        "INSERT OR IGNORE INTO FamilyRelations (Driver, RelatedDriver, RelationType, DriverID, RelatedDriverID) VALUES (?, ?, ?, ?, ?)",
                        (driver_name, driver1_formatted, rel2, driver_id, related_driver_id)
                    )
                    cursor.execute(
                        "INSERT OR IGNORE INTO FamilyRelations (Driver, RelatedDriver, RelationType, DriverID, RelatedDriverID) VALUES (?, ?, ?, ?, ?)",
                        (driver1_formatted, driver_name, rel1, related_driver_id, driver_id)
                    )
                    print(f"  Family relation added: {driver_name} ({rel2}) <-> {driver1_formatted} ({rel1})")
                except Exception as e:
                    print(f"  Warning: Could not add family relation for {driver_name}: {e}")




update_deaths_db(cur, conn)



def parse_race_info(html_content, someelements):
    """
    Parses the race information from the given HTML content.
    """
    soup = BeautifulSoup(html_content, 'html.parser')

    # Initialize the race info dictionary
    race_info = {
        "race_number": None,
        "track_name": None,
        "date": None,
        "dateindatetime": someelements[1],
        "circuit_name": standardize_circuit_name_with_year(someelements[3], year),
        "laps": None,
        "circuit_distance": None,
        "offset": None,
        "weather": None,
        "notes": None
    }
    # Extract the race number
    race_number_tag = soup.find('h4')
    if race_number_tag:
        race_number_text = race_number_tag.get_text(strip=True)
        race_number = ''.join(filter(str.isdigit, race_number_text.split()[0]))
        race_info["race_number"] = int(race_number) if race_number.isdigit() else None

    # Extract the circuit name and date and lap info from GPinfo
    gpinfo_tag = soup.find('div', class_='GPinfo')
    if gpinfo_tag:
        gpinfo_text = gpinfo_tag.get_text(separator=' ', strip=True)

        # Extract circuit name (assumed to be the first word or phrase before a day/date)
        circuit_match = re.search(r'^(.*?)\s+(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),', gpinfo_text)
        if circuit_match:
            race_info["track_name"] = circuit_match.group(1).strip()
        

        # Extract date
        date_match = re.search(r'(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+\d{1,2}\s+\w+\s+\d{4}', gpinfo_text)
        if date_match:
            race_info["date"] = date_match.group(0)

        # Extract laps and circuit distance
        laps_dist_match = re.search(r'(\d+)\s+laps?\s*x\s*([\d.]+\s*km)', gpinfo_text)
        if laps_dist_match:
            race_info["laps"] = int(laps_dist_match.group(1))
            race_info["circuit_distance"] = laps_dist_match.group(2).strip()

        offset_match = re.search(r'Offset:\s*([\d.,\s]+)\s*m', gpinfo_text)
        if offset_match:
            race_info["offset"] = float(offset_match.group(1).replace(",", "").replace(" ", ""))

    # Extract the weather
    weather_tag = soup.find('div', class_='GPmeteo')
    if weather_tag:
        img_tag = weather_tag.find('img')
        if img_tag:
            race_info["weather"] = img_tag.get('title', None)

    # Extract any notes
    notes_span = soup.find('span', id='ctl00_CPH_Main_LB_Commentaire')
    if notes_span:
        race_info["notes"] = notes_span.get_text(strip=True)
    else:
        race_info["notes"] = None

    return race_info


def parse_race_time(time_str):
    """Convert strings like '1hr 42m 06.304s' to total seconds (float)."""
    h = m = s = 0.0

    # Normalize variants like "1hr" to "1h"
    time_str = time_str.replace("hr", "h").replace("min", "m")

    match_h = re.search(r"(\d+)\s*h", time_str)
    match_m = re.search(r"(\d+)\s*m", time_str)
    match_s = re.search(r"([\d.]+)\s*s", time_str)

    if match_h:
        h = int(match_h.group(1))
    if match_m:
        m = int(match_m.group(1))
    if match_s:
        s = float(match_s.group(1))

    return float(Decimal(str(h * 3600 + m * 60 + s)).quantize(Decimal('0.001')))

def tts(t):
    parts = t.strip().split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return float(Decimal(str(int(hours) * 3600 + int(minutes) * 60 + float(seconds))).quantize(Decimal('0.001')))
    elif len(parts) == 2:
        minutes, seconds = parts
        return float(Decimal(str(int(minutes) * 60 + float(seconds))).quantize(Decimal('0.001')))
    elif len(parts) == 1:
        return float(Decimal(str(float(parts[0]))).quantize(Decimal('0.001'))) if parts[0].replace('.', '', 1).isdigit() else None
    else:
        return None


def parse_penalties(soup, is_sprint=False):
    penalties = []

    # Penalties during the race
    race_penalty_table = soup.find('table', id='ctl00_CPH_Main_GV_PenaltyP', class_='datatable')
    if race_penalty_table:
        rows = race_penalty_table.find('tbody').find_all('tr')
        for row in rows:
            cells = row.find_all('td')
            if len(cells) == 3:
                penalties.append({
                    "driver": format_name_from_caps(cells[0].get_text(strip=True)),
                    "penalty": cells[1].get_text(strip=True),
                    "reason": cells[2].get_text(strip=True),
                    "type": "during_the_race",
                    "is_sprint": is_sprint
                })

    # Penalties after the race
    after_race_penalty_table = soup.find('table', id='ctl00_CPH_Main_GV_PenaltyA', class_='datatable')
    if after_race_penalty_table:
        rows = after_race_penalty_table.find('tbody').find_all('tr')
        for row in rows:
            cells = row.find_all('td')
            if len(cells) == 4:
                penalties.append({
                    "driver": format_name_from_caps(cells[0].get_text(strip=True)),
                    "penalty": cells[1].get_text(strip=True),
                    "reason": cells[2].get_text(strip=True),
                    "lost_position": int(cells[3].get_text(strip=True)) if cells[3].get_text(strip=True).isdigit() else None,
                    "type": "added_after_chequered_flag",
                    "is_sprint": is_sprint
                })

    return penalties


def parse_statsf1_grid(soup, entrants, prefix="", return_metadata=False):
    """
    Extracts grid data (either main or sprint) and adds to entrants list.
    Prefix should be "" for main race, or "sprint" for sprint grid.
    """

    grid_field = f"{prefix}starting_grid_position"
    penalty_field = f"{prefix}gridpenalty"
    penalty_reason_field = f"{prefix}gridpenalty_reason"

    # 1. Grid positions
    grid_table = soup.find('table', id='ctl00_CPH_Main_TBL_Grille', class_='GPgrid')
    if grid_table:
        grid_divs = grid_table.find_all('div', id=lambda x: x and x.startswith('Grd'))
    # --- EXTRA METADATA ---
    pole_side = None
    grid_formation = None

    if grid_divs:
        # ---- GRID FORMATION ----
        rows = []

        for tr in grid_table.find_all("tr"):
            row_positions = []

            for div in tr.find_all("div", id=lambda x: x and x.startswith("Grd")):
                raw_text = div.get_text(strip=True)
                match = re.match(r"(\d+)\.", raw_text)
                if not match:
                    continue

                row_positions.append(int(match.group(1)))

            if row_positions:
                rows.append(row_positions)

        grid_formation = "-".join(str(len(r)) for r in rows)


        # ---- POLE SIDE ----
        first_row = rows[0] if rows else []

        if first_row:
            first_row_divs = [
                div for div in grid_divs
                if int(re.match(r'(\d+)\.', div.get_text(strip=True)).group(1)) in first_row
            ]

            if first_row_divs:
                if first_row_divs[0].get_text(strip=True).startswith("1."):
                    pole_side = "Left"
                else:
                    pole_side = "Right"
        if return_metadata:
            return pole_side, grid_formation
        for div in grid_divs:
            raw_text = div.get_text(strip=True)
            match = re.match(r'(\d+)\.', raw_text)
            if not match:
                continue
            grid_position = int(match.group(1))
            anchor = div.find('a')
            if anchor and 'title' in anchor.attrs:
                driver_name = anchor['title'].strip()
                for entrant in entrants:
                    if normalize_name(entrant['driver']) in normalize_name(format_name_from_caps(driver_name)):
                        entrant[grid_field] = grid_position
                        break

    # 2. Pit lane starters (from JavaScript)
    pitlane_script = soup.find('script', string=re.compile(r'var pitlane *= *\[.*?\];'))
    if pitlane_script:
        match = re.search(r'var pitlane *= *\[(.*?)\];', pitlane_script.string)
        if match:
            pitlane_ids = [int(i.strip()) for i in match.group(1).split(',') if i.strip().isdigit()]
            for pit_id in pitlane_ids:
                div = soup.find('div', id=f'Grd{pit_id}')
                if div:
                    anchor = div.find('a')
                    if anchor and 'title' in anchor.attrs:
                        driver_name = anchor['title'].strip()
                        for entrant in entrants:
                            if normalize_name(entrant['driver']) in normalize_name(format_name_from_caps(driver_name)):
                                entrant[grid_field] = None  # pit lane start
                                break

    # 3. Penalties table (if present)
    penalty_table = soup.find('table', id='ctl00_CPH_Main_GV_PenaltyG', class_='datatable')
    if penalty_table:
        rows = penalty_table.find('tbody').find_all('tr')
        for row in rows:
            cells = row.find_all('td')
            if len(cells) == 3:
                driver_info = cells[0].get_text(strip=True)
                penalty = cells[1].get_text(strip=True)
                reason = cells[2].get_text(strip=True)
                driver_name = driver_info.split('(')[0].strip()
                for entrant in entrants:
                    if normalize_name(entrant['driver']) in normalize_name(format_name_from_caps(driver_name)):
                        entrant[penalty_field] = penalty
                        entrant[penalty_reason_field] = reason
                        if 'Start from pit lane' in penalty:
                            entrant[grid_field] = None
                        break


def fetch_race_report(gp):
    title = gp.replace(" ", "_")
    encoded = urllib.parse.quote(title)
    url = f"https://en.wikipedia.org/api/rest_v1/page/html/{encoded}"
    retries = 0
    while retries < 3:
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "F1DB/1.0"}
            )
        except Exception as e:
            print(f"Error creating request for {title}: {e}")
            retries += 1
        else:
            break
    #raise error if request creation failed after retries
    if retries == 3:
        raise ValueError(f"Failed to create request for {title} after multiple attempts")

    try:
        with urllib.request.urlopen(req) as response:
            html = response.read().decode("utf-8")
    except Exception as e:
        raise ValueError(f"Failed to fetch {title}: {e}")

    soup = BeautifulSoup(html, "html.parser")

    # Remove citation markers and other inline noise before extracting text.
    for ref in soup.find_all("sup"):
        ref.decompose()

    for noisy_tag in soup.find_all(["style", "script", "math"]):
        noisy_tag.decompose()

    def normalize_report_text(text):
        if not text:
            return ""
        text = text.replace("\xa0", " ")
        text = re.sub(r"\s+", " ", text).strip()
        # Remove spaces that appear before punctuation after HTML flattening.
        text = re.sub(r"\s+([,.;:!?%)\]])", r"\1", text)
        text = re.sub(r"([(\[])\s+", r"\1", text)
        # Keep dash-separated phrases readable without introducing duplicate spaces.
        text = re.sub(r"\s*-\s*", " - ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text.strip()

    def element_text(elem):
        return normalize_report_text(elem.get_text(" ", strip=True))

    ignored_section_terms = (
        "reference", "citation", "footnote",
        "external links", "see also", "bibliography", "further reading"
    )

    temp_output = []
    skip_section = False

    for elem in soup.find_all(["h2", "h3", "h4", "p", "ul", "ol", "li"]):
        if elem.find_parent(["table", "aside", "cite", "footer", "nav"]):
            continue

        if elem.name.startswith("h"):
            header_text = element_text(elem)
            if not header_text:
                continue

            header_lower = header_text.lower()
            skip_section = any(term in header_lower for term in ignored_section_terms)
            if skip_section:
                continue

            level = "##" if elem.name == "h2" else "###"
            temp_output.append({"type": "header", "content": f"{level} {header_text}"})

        elif skip_section:
            continue

        elif elem.name == "p":
            text = element_text(elem)
            if text:
                temp_output.append({"type": "para", "content": text})

        elif elem.name in {"ul", "ol"}:
            list_items = []
            for li in elem.find_all("li", recursive=False):
                text = element_text(li)
                if text:
                    list_items.append(f"- {text}")
            if list_items:
                temp_output.append({"type": "list", "content": "\n".join(list_items)})

    final_content = []
    for i, item in enumerate(temp_output):
        if item["type"] == "header":
            has_content_below = False
            for next_item in temp_output[i+1:]:
                if next_item["type"] in {"para", "list"}:
                    has_content_below = True
                    break
                if next_item["type"] == "header" and next_item["content"].startswith("## "):
                    break
            if has_content_below:
                final_content.append(item["content"])
        else:
            if final_content and final_content[-1] == item["content"]:
                continue
            final_content.append(item["content"])

    content = "\n\n".join(block for block in final_content if block).strip()
    content = re.sub(r"\n{3,}", "\n\n", content)

    if not content:
        raise ValueError(f"No usable content extracted for {title}")

    return content

def tts_to_normal(tts):
    if tts is None:
        return None
    # change 60.429 to 1:00.429
    minutes = int(tts / 60)
    seconds = float(Decimal(str(tts % 60)).quantize(Decimal('0.001')))
    return f"{minutes}:{seconds:06.3f}"


def get_race_report_update_rows(target=None):
    query = """
        SELECT RR.ID, COALESCE(G.GrandPrixName, RR.GrandPrixName), G.Season
        FROM RaceReports RR
        LEFT JOIN GrandsPrix G ON G.ID = RR.ID
    """
    params = ()

    if target is None:
        query += " ORDER BY RR.ID"
    else:
        target = str(target).strip()
        target_lower = target.lower()
        from_mode = False

        if target_lower.startswith("from "):
            target = target[5:].strip()
            target_lower = target.lower()
            from_mode = True

        if target_lower.startswith("season "):
            season_value = target[7:].strip()
            operator = ">=" if from_mode else "="
            query += f" WHERE G.Season {operator} ? ORDER BY RR.ID"
            params = (int(season_value),)
        elif target_lower.startswith("race "):
            race_name = target[5:].strip()
            if from_mode:
                cur.execute(
                    "SELECT ID, GrandPrixName FROM GrandsPrix WHERE LOWER(GrandPrixName) = ?",
                    (race_name.lower(),)
                )
                race_row = cur.fetchone()
                if race_row is None:
                    raise ValueError(f"No race report found for race '{race_name}'.")
                race_id, _canonical_name = race_row
                query += " WHERE RR.ID >= ? ORDER BY RR.ID"
                params = (race_id,)
            else:
                query += """
                    WHERE LOWER(COALESCE(G.GrandPrixName, RR.GrandPrixName)) = ?
                    ORDER BY RR.ID
                """
                params = (race_name.lower(),)
        elif target.isdigit():
            operator = ">=" if from_mode else "="
            query += f" WHERE G.Season {operator} ? ORDER BY RR.ID"
            params = (int(target),)
        else:
            if from_mode:
                cur.execute(
                    "SELECT ID, GrandPrixName FROM GrandsPrix WHERE LOWER(GrandPrixName) = ?",
                    (target.lower(),)
                )
                race_row = cur.fetchone()
                if race_row is None:
                    raise ValueError(f"No race report found for race '{target}'.")
                race_id, _canonical_name = race_row
                query += " WHERE RR.ID >= ? ORDER BY RR.ID"
                params = (race_id,)
            else:
                query += """
                    WHERE LOWER(COALESCE(G.GrandPrixName, RR.GrandPrixName)) = ?
                    ORDER BY RR.ID
                """
                params = (target.lower(),)

    cur.execute(query, params)
    return cur.fetchall()


def update_race_reports(target=None):
    rows = get_race_report_update_rows(target)

    if not rows:
        if target is None:
            print("No race reports found to update.")
        elif str(target).strip().isdigit() or str(target).strip().lower().startswith("season "):
            print(f"No race reports found for season {target}.")
        else:
            print(f"No race report found for race '{target}'.")
        return

    if target is None:
        scope = f"{len(rows)} races"
    elif str(target).strip().lower().startswith("from "):
        scope = f"{len(rows)} races from {str(target).strip()[5:]}"
    elif str(target).strip().isdigit() or str(target).strip().lower().startswith("season "):
        scope = f"{len(rows)} races in season {target}"
    else:
        scope = f"{len(rows)} race(s) matching {target}"

    print(f"Updating race reports for {scope}...")
    updated = 0
    failed = []

    for race_id, gp_name, _season in rows:
        report = fetch_race_report(gp_name)
        cur.execute(
            "UPDATE RaceReports SET GrandPrixName = ?, RaceReport = ? WHERE ID = ?",
            (gp_name, report, race_id)
        )
        updated += 1
        print(f"[{updated}/{len(rows)}] Updated {gp_name}")
        if updated % 25 == 0:
            conn.commit()

    conn.commit()
    print(f"Race report update complete. Updated {updated}/{len(rows)} races.")
    if failed:
        print(f"{len(failed)} races failed:")
        for race_id, gp_name, error in failed:
            print(f"- {race_id} {gp_name}: {error}")

def assign_qualifying_positions_by_session(entrants, session_num):
    """
    Sorts entrants by qualifying session time and assigns positions.
    session_num: 1, 2, or 3 for qualifying1, qualifying2, qualifying3
    """
    time_key = f'qualifying{session_num}timeinseconds'
    position_key = f'qualifying{session_num}position'
    
    # Filter entrants with valid times in this session
    valid_entrants = [(i, ent) for i, ent in enumerate(entrants) 
                     if ent.get(time_key) is not None]
    
    # Sort by time (ascending - fastest first)
    valid_entrants.sort(key=lambda x: x[1][time_key])
    
    # Assign positions
    for position, (original_idx, ent) in enumerate(valid_entrants, start=1):
        entrants[original_idx][position_key] = position


def assign_sprint_qualifying_positions_by_session(entrants, session_num):
    """
    Sorts entrants by sprint qualifying session time and assigns positions.
    session_num: 1, 2, or 3 for sprint_qualifying1, sprint_qualifying2, sprint_qualifying3
    """
    time_key = f'sprint_qualifying{session_num}timeinseconds'
    position_key = f'sprint_qualifying{session_num}position'
    
    # Filter entrants with valid times in this session
    valid_entrants = [(i, ent) for i, ent in enumerate(entrants) 
                     if ent.get(time_key) is not None]
    
    # Sort by time (ascending - fastest first)
    valid_entrants.sort(key=lambda x: x[1][time_key])
    
    # Assign positions
    for position, (original_idx, ent) in enumerate(valid_entrants, start=1):
        entrants[original_idx][position_key] = position


def parse_race_results(links):
    parseprequalifyingflag = False
    """
    Parses the race results from the given links.
    """
    entrants = []
    abbreviations = {
        "ab": "Did not finish",
        "dsq": "Disqualified",
        "nc": "Not classified",
        "exc": "Excluded",
        "np": "Did not start",
        "f": "Withdrew",
        "nq": "Did not qualify",
        "tf": "Formation lap",
        "npq": "Did not pre-qualify",
        "t": "Substitute, third driver"
    }

    """    
    qualifying = []
    starting_grid = []
    race_results = []
    fastest_laps = []
    lap_by_lap = []"""
    sprintweekend = False
    for link in links:
        if link['href'].endswith('engages.aspx'):
            open_url(f"https://www.statsf1.com{link['href']}")
            table = soup.find('table', class_ = 'sortable')
            # Parse the table rows
            rows = table.find('tbody').find_all('tr')
            for row in rows:
                cells = row.find_all('td')
                if len(cells) >= 8:  # Ensure there are enough columns
                    entrant = {
                        "number": int(cells[0].get_text(strip=True)),
                        "driver": format_name_from_caps(cells[1].get_text(strip=True)),
                        "team": cells[2].get_text(strip=True),
                        "constructor": cells[3].get_text(strip=True),
                        "chassis": cells[4].get_text(strip=True),
                        "engine": cells[5].get_text(strip=True),
                        "enginemodel": cells[6].get_text(strip=True),
                        "tyre": cells[7].get_text(strip=True),
                        "substituteorthirddriver": True if cells[1].get_text(strip=True).endswith('*') else False
                    }
                    if entrant['team'] == 'Privé':
                        entrant['team'] = 'Privateer'
                    entrants.append(entrant)
                    #print (entrants)
                    
        elif link['href'].endswith('/qualification.aspx'):
            open_url(f"https://www.statsf1.com{link['href']}")
            table = soup.find('table', class_ = 'datatable')
            # Parse the table rows
            rows = table.find('tbody').find_all('tr')
            for row in rows:
                cells = row.find_all('td')
                if len(cells) >= 8:
                    for entrant in entrants:
                        #again normalise the diacritics to compare
                        if normalize_name(entrant['driver']) == normalize_name(format_name_from_caps(cells[1].get_text(strip=True))):
                            x = entrants.index(entrant)
                            #We are going to cross the values now.
                            entrant['qualifyingposition'] = int(cells[0].get_text(strip=True))
                            entrant['qualifyingtime'] = cells[4].get_text(strip=True).replace("'", ":")
                            entrant['qualifyinggap'] = cells[5].get_text(strip=True)
                            entrant['qualifyingtimeinseconds'] = tts(cells[4].get_text(strip=True).replace("'", ":"))
                            entrant['qualifyinggapseconds'] = tts(cells[5].get_text(strip=True))
                            entrants[x] = entrant                         
                            break  # Exit the loop once the entrant is found
        elif link['href'].endswith('/grille.aspx'):
            open_url(f"https://www.statsf1.com{link['href']}")
            parse_statsf1_grid(soup, entrants, prefix="")
        elif link['href'].endswith('/sprint.aspx?grille'):
            open_url(f"https://www.statsf1.com{link['href']}")
            parse_statsf1_grid(soup, entrants, prefix="sprint")
        elif link['href'].endswith('/meilleur-tour.aspx'):
            open_url(f"https://www.statsf1.com{link['href']}")
            table = soup.find('table', class_ = 'datatable')
            # Parse the table rows
            if table: #This line exists because the 2021 Belgian Grand Prix exists, even though it should not exist.
                rows = table.find('tbody').find_all('tr')
            else:
                break
            for row in rows:
                cells = row.find_all('td')
                if len(cells) >= 8:
                    for entrant in entrants:
                        if normalize_name(entrant['driver']) == normalize_name(format_name_from_caps(cells[1].get_text(strip=True))):
                            x = entrants.index(entrant)
                            #We are going to cross the values now.
                            entrant['fastestlap'] = int(cells[0].get_text(strip=True)) if cells[0].get_text(strip=True).isdigit() else None
                            entrant['fastestlapinseconds'] = tts(cells[4].get_text(strip=True).replace("'", ":"))
                            entrant['fastestlapgapinseconds'] = tts(cells[5].get_text(strip=True))
                            entrant['fastestlap_time'] = cells[4].get_text(strip=True).replace("'", ":")
                            entrant['fastestlap_gap'] = cells[5].get_text(strip=True)
                            entrant['fastestlap_lap'] = int(cells[6].get_text(strip=True)) if cells[6].get_text(strip=True).isdigit() else None
                            entrants[x] = entrant 
                            break  # Exit the loop once the entrant is found
        elif link['href'].endswith('/sprint.aspx?mt'):
            open_url(f"https://www.statsf1.com{link['href']}")
            table = soup.find('table', class_ = 'datatable')
            # Parse the table rows
            rows = table.find('tbody').find_all('tr')
            for row in rows:
                cells = row.find_all('td')
                if len(cells) >= 8:
                    for entrant in entrants:
                        if normalize_name(entrant['driver']) == normalize_name(format_name_from_caps(cells[1].get_text(strip=True))):
                            x = entrants.index(entrant)
                            #We are going to cross the values now.
                            entrant['sprintfastestlap'] = int(cells[0].get_text(strip=True))
                            entrant['sprintfastestlapinseconds'] = tts(cells[4].get_text(strip=True).replace("'", ":"))
                            entrant['sprintfastestlapgapinseconds'] = tts(cells[5].get_text(strip=True))
                            entrant['sprintfastestlap_time'] = cells[4].get_text(strip=True).replace("'", ":")
                            entrant['sprintfastestlap_gap'] = cells[5].get_text(strip=True)
                            entrant['sprintfastestlap_lap'] = int(cells[6].get_text(strip=True))
                            entrants[x] = entrant  
                            break  # Exit the loop once the entrant is found                        
        elif link['href'].endswith('/qualifying/2'):
                entrant['qualifying2laps'] = None
                open_url(f"https://formula1.com{link['href']}")
                table = soup.find('table', class_ = 'Table-module_table__cKsW2')
                rows = table.find('tbody').find_all('tr')
                first_qualifying_time = None
                for row in rows:
                    cells = row.find_all('td')
                    if len(cells) == 5:
                        for entrant in entrants:
                            if entrant['number'] == int(cells[1].get_text(strip=True)) and not entrant['substituteorthirddriver']:
                                x = entrants.index(entrant)
                                entrant['qualifying2position'] = int(cells[0].get_text(strip=True))
                                entrant['qualifying2time'] = cells[4].get_text(strip=True)
                                t = tts(cells[4].get_text(strip=True))
                                entrant['qualifying2timeinseconds'] = float(Decimal(str(t)).quantize(Decimal('0.001'))) if t else None
                                entrant['qualifying2gap'] = float((Decimal(str(tts(first_qualifying_time))) - Decimal(str(t))).quantize(Decimal('0.001'))) if first_qualifying_time and t else None
                                entrant['qualifying2laps'] = None
                                entrants[x] = entrant
                                first_qualifying_time = cells[4].get_text(strip=True)
                                break
                    elif len(cells) == 6:
                        for entrant in entrants:
                            if entrant['number'] == int(cells[1].get_text(strip=True)) and not entrant['substituteorthirddriver']:
                                x = entrants.index(entrant)
                                entrant['qualifying2position'] = int(cells[0].get_text(strip=True)) if cells[0].text.strip().isdigit() else None
                                entrant['qualifying2time'] = cells[4].get_text(strip=True)
                                t = tts(cells[4].get_text(strip=True))
                                entrant['qualifying2timeinseconds'] = float(Decimal(str(t)).quantize(Decimal('0.001'))) if t else None
                                entrant['qualifying2gap'] = float((Decimal(str(tts(first_qualifying_time))) - Decimal(str(t))).quantize(Decimal('0.001'))) if first_qualifying_time and t else None
                                entrant['qualifying2laps'] = int(cells[5].get_text(strip=True)) if cells[5].get_text(strip=True).isdigit() else None
                                entrants[x] = entrant
                                first_qualifying_time = cells[4].get_text(strip=True)
                                break
        elif link['href'].endswith('/qualifying/1'):
            open_url(f"https://formula1.com{link['href']}")
            table = soup.find('table', class_ = 'Table-module_table__cKsW2')
            rows = table.find('tbody').find_all('tr')
            first_qualifying_time = None
            for row in rows:
                cells = row.find_all('td')
                if len(cells) == 5:
                    for entrant in entrants:
                        if entrant['number'] == int(cells[1].get_text(strip=True)) and not entrant['substituteorthirddriver']:
                            x = entrants.index(entrant)
                            entrant['qualifying1position'] = int(cells[0].get_text(strip=True))
                            entrant['qualifying1time'] = cells[4].get_text(strip=True)
                            t = tts(cells[4].get_text(strip=True))
                            entrant['qualifying1timeinseconds'] = float(Decimal(str(t)).quantize(Decimal('0.001'))) if t else None
                            entrant['qualifying1gap'] = float((Decimal(str(tts(first_qualifying_time))) - Decimal(str(t))).quantize(Decimal('0.001'))) if first_qualifying_time and t else None
                            entrants[x] = entrant
                            if entrant['qualifying1position'] == 1:
                                first_qualifying_time = cells[4].get_text(strip=True)
                            break
                elif len(cells) == 6:
                    for entrant in entrants:
                        if entrant['number'] == int(cells[1].get_text(strip=True)) and not entrant['substituteorthirddriver']:
                            x = entrants.index(entrant)
                            entrant['qualifying1position'] = int(cells[0].get_text(strip=True))
                            entrant['qualifying1time'] = cells[4].get_text(strip=True)
                            t = tts(cells[4].get_text(strip=True))
                            entrant['qualifying1timeinseconds'] = float(Decimal(str(t)).quantize(Decimal('0.001'))) if t else None
                            entrant['qualifying1gap'] = float((Decimal(str(tts(first_qualifying_time))) - Decimal(str(t))).quantize(Decimal('0.001'))) if first_qualifying_time and t else None
                            entrant['qualifying1laps'] = int(cells[5].get_text(strip=True)) if cells[5].get_text(strip=True).isdigit() else None
                            entrants[x] = entrant
                            if entrant['qualifying1position'] == 1:
                                first_qualifying_time = cells[4].get_text(strip=True)
                            break                         
        elif link['href'].endswith('/qualifying/0'):
                open_url(f"https://formula1.com{link['href']}")
                table = soup.find('table', class_ = 'Table-module_table__cKsW2')          #soup.find('table', class_ = 'f1-table f1-table-with-data w-full')
                rows = table.find('tbody').find_all('tr')
                for row in rows:
                    cells = row.find_all('td')
                    if len(cells) == 6:  
                        for entrant in entrants:
                            if entrant['number'] == int(cells[1].get_text(strip=True)) and not entrant['substituteorthirddriver']:
                                x = entrants.index(entrant)
                                entrant['qualifyinglaps'] = int(cells[5].get_text(strip=True)) if cells[5].get_text(strip=True).isdigit() else None
                                entrants[x] = entrant
                                break 
        elif link['href'].endswith('/qualifying'):
            open_url(f"https://formula1.com{link['href']}")
            table = soup.find('table', class_ = 'Table-module_table__cKsW2')
            rows = table.find('tbody').find_all('tr')
            fastest_q1 = None
            fastest_q2 = None
            fastest_q3 = None
            for row in rows:
                cells = row.find_all('td')
                if len(cells) == 6:  #1996-2003 qualifying format
                    for entrant in entrants:
                        if entrant['number'] == int(cells[1].get_text(strip=True)) and not entrant['substituteorthirddriver']:
                            x = entrants.index(entrant)
                            entrant['qualifyinglaps'] = int(cells[5].get_text(strip=True)) if cells[5].get_text(strip=True).isdigit() else None
                            entrants[x] = entrant
                            break
                elif len(cells) == 8: #current qualifying format
                    for entrant in entrants:
                        if entrant['number'] == int(cells[1].get_text(strip=True)) and not entrant['substituteorthirddriver']:
                            x = entrants.index(entrant)
                            t1 = tts(cells[4].get_text(strip=True))
                            t2 = tts(cells[5].get_text(strip=True))
                            t3 = tts(cells[6].get_text(strip=True))
                            entrant['qualifying1time'] = cells[4].get_text(strip=True)
                            entrant['qualifying2time'] = cells[5].get_text(strip=True)
                            entrant['qualifying3time'] = cells[6].get_text(strip=True)
                            entrant['qualifying1timeinseconds'] = float(Decimal(str(t1)).quantize(Decimal('0.001'))) if t1 else None
                            entrant['qualifying2timeinseconds'] = float(Decimal(str(t2)).quantize(Decimal('0.001'))) if t2 else None
                            entrant['qualifying3timeinseconds'] = float(Decimal(str(t3)).quantize(Decimal('0.001'))) if t3 else None
                            # Track fastest times (leaders)
                            if t1 and (fastest_q1 is None or t1 < fastest_q1):
                                fastest_q1 = t1
                            if t2 and (fastest_q2 is None or t2 < fastest_q2):
                                fastest_q2 = t2
                            if t3 and (fastest_q3 is None or t3 < fastest_q3):
                                fastest_q3 = t3
                            entrant['qualifyinglaps'] = int(cells[7].get_text(strip=True)) if cells[7].get_text(strip=True).isdigit() else None
                            entrants[x] = entrant
                            break
            # Second pass: calculate gaps from leader
            for row in rows:
                cells = row.find_all('td')
                if len(cells) == 8:
                    for entrant in entrants:
                        if entrant['number'] == int(cells[1].get_text(strip=True)) and not entrant['substituteorthirddriver']:
                            x = entrants.index(entrant)
                            t1 = tts(cells[4].get_text(strip=True))
                            t2 = tts(cells[5].get_text(strip=True))
                            t3 = tts(cells[6].get_text(strip=True))
                            entrant['qualifying1gap'] = float((Decimal(str(t1)) - Decimal(str(fastest_q1))).quantize(Decimal('0.001'))) if t1 and fastest_q1 else None
                            entrant['qualifying2gap'] = float((Decimal(str(t2)) - Decimal(str(fastest_q2))).quantize(Decimal('0.001'))) if t2 and fastest_q2 else None
                            entrant['qualifying3gap'] = float((Decimal(str(t3)) - Decimal(str(fastest_q3))).quantize(Decimal('0.001'))) if t3 and fastest_q3 else None
                            entrants[x] = entrant
                            break
            # Third pass: assign positions based on timeinseconds
            assign_qualifying_positions_by_session(entrants, 1)
            assign_qualifying_positions_by_session(entrants, 2)
            assign_qualifying_positions_by_session(entrants, 3)

        elif link['href'].endswith('/sprint-qualifying'):
            open_url(f"https://formula1.com{link['href']}")
            table = soup.find('table', class_ = 'Table-module_table__cKsW2')
            rows = table.find('tbody').find_all('tr')
            fastest_q1 = None
            fastest_q2 = None
            fastest_q3 = None
            fastest_sq = None
            for row in rows:
                cells = row.find_all('td')
                if len(cells) == 8:
                    for entrant in entrants:
                        if entrant['number'] == int(cells[1].get_text(strip=True)) and not entrant['substituteorthirddriver']:
                            x = entrants.index(entrant)
                            t1 = tts(cells[4].get_text(strip=True))
                            t2 = tts(cells[5].get_text(strip=True))
                            t3 = tts(cells[6].get_text(strip=True))
                            entrant['sprint_qualifyingposition'] = int(cells[0].get_text(strip=True)) if cells[0].get_text(strip=True).isdigit() else None
                            entrant['sprint_qualifying1time'] = cells[4].get_text(strip=True)
                            entrant['sprint_qualifying2time'] = cells[5].get_text(strip=True)
                            entrant['sprint_qualifying3time'] = cells[6].get_text(strip=True)
                            entrant['sprint_qualifying1timeinseconds'] = float(Decimal(str(t1)).quantize(Decimal('0.001'))) if t1 else None
                            entrant['sprint_qualifying2timeinseconds'] = float(Decimal(str(t2)).quantize(Decimal('0.001'))) if t2 else None
                            entrant['sprint_qualifying3timeinseconds'] = float(Decimal(str(t3)).quantize(Decimal('0.001'))) if t3 else None
                            if t1 and (fastest_q1 is None or t1 < fastest_q1):
                                fastest_q1 = t1
                            if t2 and (fastest_q2 is None or t2 < fastest_q2):
                                fastest_q2 = t2
                            if t3 and (fastest_q3 is None or t3 < fastest_q3):
                                fastest_q3 = t3
                            if entrant['sprint_qualifying3time']:
                                entrant['sprint_qualifyingtime'] = entrant['sprint_qualifying3time']
                            elif entrant['sprint_qualifying2time']:
                                entrant['sprint_qualifyingtime'] = entrant['sprint_qualifying2time']
                            elif entrant['sprint_qualifying1time']:
                                entrant['sprint_qualifyingtime'] = entrant['sprint_qualifying1time']
                            t_sq = tts(entrant['sprint_qualifyingtime']) if entrant.get('sprint_qualifyingtime') else None
                            entrant['sprint_qualifyingtimeinseconds'] = float(Decimal(str(t_sq)).quantize(Decimal('0.001'))) if t_sq else None
                            if t_sq and (fastest_sq is None or t_sq < fastest_sq):
                                fastest_sq = t_sq
                            entrant['sprint_qualifyinglaps'] = int(cells[7].get_text(strip=True)) if cells[7].get_text(strip=True).isdigit() else None
                            entrants[x] = entrant
                            break
            # Second pass: calculate gaps from leader
            for row in rows:
                cells = row.find_all('td')
                if len(cells) == 8:
                    for entrant in entrants:
                        if entrant['number'] == int(cells[1].get_text(strip=True)) and not entrant['substituteorthirddriver']:
                            x = entrants.index(entrant)
                            t1 = tts(cells[4].get_text(strip=True))
                            t2 = tts(cells[5].get_text(strip=True))
                            t3 = tts(cells[6].get_text(strip=True))
                            t_sq = tts(entrant.get('sprint_qualifyingtime')) if entrant.get('sprint_qualifyingtime') else None
                            entrant['sprint_qualifying1gap'] = float((Decimal(str(t1)) - Decimal(str(fastest_q1))).quantize(Decimal('0.001'))) if t1 and fastest_q1 else None
                            entrant['sprint_qualifying2gap'] = float((Decimal(str(t2)) - Decimal(str(fastest_q2))).quantize(Decimal('0.001'))) if t2 and fastest_q2 else None
                            entrant['sprint_qualifying3gap'] = float((Decimal(str(t3)) - Decimal(str(fastest_q3))).quantize(Decimal('0.001'))) if t3 and fastest_q3 else None
                            entrant['sprint_qualifyinggap'] = float((Decimal(str(t_sq)) - Decimal(str(fastest_sq))).quantize(Decimal('0.001'))) if t_sq and fastest_sq else None
                            entrants[x] = entrant
                            break
            # Third pass: assign positions based on timeinseconds
            assign_sprint_qualifying_positions_by_session(entrants, 1)
            assign_sprint_qualifying_positions_by_session(entrants, 2)
            assign_sprint_qualifying_positions_by_session(entrants, 3)                     
        elif link['href'].endswith('/practice/0'):
            open_url(f"https://formula1.com{link['href']}")
            table = soup.find('table', class_='Table-module_table__cKsW2')
            rows = table.find('tbody').find_all('tr')
            for row in rows:
                cells = row.find_all('td')

                if len(cells) == 6:
                    for entrant in entrants:
                        if entrant['number'] == int(cells[1].get_text(strip=True)) and not entrant['substituteorthirddriver']:
                            x = entrants.index(entrant)
                            entrant['warmupposition'] = int(cells[0].get_text(strip=True))
                            gap = cells[4].get_text(strip=True) if cells[4].get_text(strip=True).startswith('+') else None
                            entrant['warmuptime'] = cells[4].get_text(strip=True) if entrant['warmupposition'] == 1 else tts_to_normal(
                                leadertime + tts(cells[4].get_text(strip=True).replace('+', '').replace('s', '')) if cells[4].get_text(strip=True) else None
                            )
                            entrant['warmupgap'] = gap
                            entrant['warmuptimeinseconds'] = tts(entrant['warmuptime']) if entrant['warmuptime'] is not None else None
                            if entrant['warmupposition'] == 1:
                                leadertime = entrant['warmuptimeinseconds']
                            entrant['warmuplaps'] = int(cells[5].get_text(strip=True)) 
                            entrants[x] = entrant
                            break

                elif len(cells) == 7:
                    for entrant in entrants:
                        if entrant['number'] == int(cells[1].get_text(strip=True)) and not entrant['substituteorthirddriver']:
                            x = entrants.index(entrant)
                            entrant['warmupposition'] = int(cells[0].get_text(strip=True))
                            gap = cells[4].get_text(strip=True) if cells[4].get_text(strip=True).startswith('+') else None
                            entrant['warmuptime'] = cells[4].get_text(strip=True) if entrant['warmupposition'] == 1 else tts_to_normal(
                                leadertime + tts(cells[4].get_text(strip=True).replace('+', '').replace('s', ''))
                            )
                            entrant['warmupgap'] = gap
                            entrant['warmuptimeinseconds'] = tts(entrant['warmuptime'])
                            if entrant['warmupposition'] == 1:
                                leadertime = entrant['warmuptimeinseconds']
                            entrant['warmuplaps'] = int(cells[5].get_text(strip=True)) if cells[5].get_text(strip=True).isdigit() else None
                            entrants[x] = entrant
                            break
                elif len(cells) == 5:
                    for entrant in entrants:
                        if entrant['number'] == int(cells[1].get_text(strip=True)) and not entrant['substituteorthirddriver']:
                            x = entrants.index(entrant)
                            entrant['warmupposition'] = int(cells[0].get_text(strip=True))
                            entrant['warmuptime'] = cells[4].get_text(strip=True) if entrant['warmupposition'] == 1 else tts_to_normal(
                                leadertime + tts(cells[4].get_text(strip=True).replace('+', '').replace('s', '')) if cells[4].get_text(strip=True) else None
                            )
                            entrant['warmupgap'] = cells[4].get_text(strip=True)
                            entrant['warmuptimeinseconds'] = tts(entrant['warmuptime']) if entrant['warmuptime'] is not None else None
                            if entrant['warmupposition'] == 1:
                                leadertime = entrant['warmuptimeinseconds']
                            entrants[x] = entrant
                            break

        elif link['href'].endswith('/practice/1'):
            open_url(f"https://formula1.com{link['href']}")
            table = soup.find('table', class_='Table-module_table__cKsW2')
            rows = table.find('tbody').find_all('tr')
            for row in rows:
                cells = row.find_all('td')
                if len(cells) == 6:
                    for entrant in entrants:
                        if entrant['number'] == int(cells[1].get_text(strip=True)):
                            x = entrants.index(entrant)
                            entrant['practice1position'] = int(cells[0].get_text(strip=True))
                            gap = cells[4].get_text(strip=True) if cells[4].get_text(strip=True).startswith('+') else None
                            entrant['practice1time'] = cells[4].get_text(strip=True) if entrant['practice1position'] == 1 else tts_to_normal(
                                leadertime + tts(cells[4].get_text(strip=True).replace('+', '').replace('s', '')) if cells[4].get_text(strip=True) else None
                            )
                            entrant['practice1gap'] = gap
                            entrant['practice1timeinseconds'] = tts(entrant['practice1time']) if entrant['practice1time'] is not None else None
                            if entrant['practice1position'] == 1:
                                leadertime = entrant['practice1timeinseconds']
                            entrant['practice1laps'] = int(cells[5].get_text(strip=True))
                            entrants[x] = entrant
                            break

                elif len(cells) == 7:
                    for entrant in entrants:
                        if entrant['number'] == int(cells[1].get_text(strip=True)):
                            x = entrants.index(entrant)
                            entrant['practice1position'] = int(cells[0].get_text(strip=True))
                            gap = cells[4].get_text(strip=True) if cells[4].get_text(strip=True).startswith('+') else None
                            entrant['practice1time'] = cells[4].get_text(strip=True) if entrant['practice1position'] == 1 else tts_to_normal(
                                leadertime + tts(cells[4].get_text(strip=True).replace('+', '').replace('s', ''))
                            )
                            entrant['practice1gap'] = gap
                            entrant['practice1timeinseconds'] = tts(entrant['practice1time'])
                            if entrant['practice1position'] == 1:
                                leadertime = entrant['practice1timeinseconds']
                            entrant['practice1laps'] = int(cells[5].get_text(strip=True)) if cells[5].get_text(strip=True).isdigit() else None
                            entrants[x] = entrant
                            break
                elif len(cells) == 5:
                    for entrant in entrants:
                        if entrant['number'] == int(cells[1].get_text(strip=True)):
                            x = entrants.index(entrant)
                            entrant['practice1position'] = int(cells[0].get_text(strip=True))
                            entrant['practice1time'] = cells[4].get_text(strip=True) if entrant['practice1position'] == 1 else tts_to_normal(
                                leadertime + tts(cells[4].get_text(strip=True).replace('+', '').replace('s', '')) if cells[4].get_text(strip=True) else None
                            )
                            entrant['practice1gap'] = cells[4].get_text(strip=True)
                            entrant['practice1timeinseconds'] = tts(entrant['practice1time']) if entrant['practice1time'] is not None else None
                            if entrant['practice1position'] == 1:
                                leadertime = entrant['practice1timeinseconds']
                            entrants[x] = entrant
                            break

        elif link['href'].endswith('/practice/2'):
            open_url(f"https://formula1.com{link['href']}")
            table = soup.find('table', class_='Table-module_table__cKsW2')
            rows = table.find('tbody').find_all('tr')
            for row in rows:
                cells = row.find_all('td')

                if len(cells) == 6:
                    for entrant in entrants:
                        if entrant['number'] == int(cells[1].get_text(strip=True)):
                            x = entrants.index(entrant)
                            entrant['practice2position'] = int(cells[0].get_text(strip=True))
                            gap = cells[4].get_text(strip=True) if cells[4].get_text(strip=True).startswith('+') else None
                            entrant['practice2time'] = cells[4].get_text(strip=True) if entrant['practice2position'] == 1 else tts_to_normal(
                                leadertime + tts(cells[4].get_text(strip=True).replace('+', '').replace('s', '')) if cells[4].get_text(strip=True) else None
                            )
                            entrant['practice2gap'] = gap
                            entrant['practice2timeinseconds'] = tts(entrant['practice2time']) if entrant['practice2time'] is not None else None
                            if entrant['practice2position'] == 1:
                                leadertime = entrant['practice2timeinseconds']
                            entrant['practice2laps'] = int(cells[5].get_text(strip=True))
                            entrants[x] = entrant
                            break

                elif len(cells) == 7:
                    for entrant in entrants:
                        if entrant['number'] == int(cells[1].get_text(strip=True)):
                            x = entrants.index(entrant)
                            entrant['practice2position'] = int(cells[0].get_text(strip=True))
                            gap = cells[4].get_text(strip=True) if cells[4].get_text(strip=True).startswith('+') else None
                            entrant['practice2time'] = cells[4].get_text(strip=True) if entrant['practice2position'] == 1 else tts_to_normal(
                                leadertime + tts(cells[4].get_text(strip=True).replace('+', '').replace('s', ''))
                            )
                            entrant['practice2gap'] = gap
                            entrant['practice2timeinseconds'] = tts(entrant['practice2time'])
                            if entrant['practice2position'] == 1:
                                leadertime = entrant['practice2timeinseconds']
                            entrant['practice2laps'] = int(cells[5].get_text(strip=True)) if cells[5].get_text(strip=True).isdigit() else None
                            entrants[x] = entrant
                            break
                elif len(cells) == 5:
                    for entrant in entrants:
                        if entrant['number'] == int(cells[1].get_text(strip=True)):
                            x = entrants.index(entrant)
                            entrant['practice2position'] = int(cells[0].get_text(strip=True))
                            entrant['practice2time'] = cells[4].get_text(strip=True) if entrant['practice2position'] == 1 else tts_to_normal(
                                leadertime + tts(cells[4].get_text(strip=True).replace('+', '').replace('s', '')) if cells[4].get_text(strip=True) else None
                            )
                            entrant['practice2gap'] = cells[4].get_text(strip=True)
                            entrant['practice2timeinseconds'] = tts(entrant['practice2time']) if entrant['practice2time'] is not None else None
                            if entrant['practice2position'] == 1:
                                leadertime = entrant['practice2timeinseconds']
                            entrants[x] = entrant
                            break
        elif link['href'].endswith('/practice/3'):
            open_url(f"https://formula1.com{link['href']}")
            table = soup.find('table', class_='Table-module_table__cKsW2')
            rows = table.find('tbody').find_all('tr')
            for row in rows:
                cells = row.find_all('td')

                if len(cells) == 6:
                    for entrant in entrants:
                        if entrant['number'] == int(cells[1].get_text(strip=True)):
                            x = entrants.index(entrant)
                            entrant['practice3position'] = int(cells[0].get_text(strip=True))
                            gap = cells[4].get_text(strip=True) if cells[4].get_text(strip=True).startswith('+') else None
                            entrant['practice3time'] = cells[4].get_text(strip=True) if entrant['practice3position'] == 1 else tts_to_normal(
                                leadertime + tts(cells[4].get_text(strip=True).replace('+', '').replace('s', '')) if cells[4].get_text(strip=True) else None
                            )
                            entrant['practice3gap'] = gap
                            entrant['practice3timeinseconds'] = tts(entrant['practice3time']) if entrant['practice3time'] is not None else None
                            if entrant['practice3position'] == 1:
                                leadertime = entrant['practice3timeinseconds']
                            entrant['practice3laps'] = int(cells[5].get_text(strip=True))
                            entrants[x] = entrant
                            break

                elif len(cells) == 7:
                    for entrant in entrants:
                        if entrant['number'] == int(cells[1].get_text(strip=True)):
                            x = entrants.index(entrant)
                            entrant['practice3position'] = int(cells[0].get_text(strip=True))
                            gap = cells[4].get_text(strip=True) if cells[4].get_text(strip=True).startswith('+') else None
                            entrant['practice3time'] = cells[4].get_text(strip=True) if entrant['practice3position'] == 1 else tts_to_normal(
                                leadertime + tts(cells[4].get_text(strip=True).replace('+', '').replace('s', ''))
                            )
                            entrant['practice3gap'] = gap
                            entrant['practice3timeinseconds'] = tts(entrant['practice3time'])
                            if entrant['practice3position'] == 1:
                                leadertime = entrant['practice3timeinseconds']
                            entrant['practice3laps'] = int(cells[5].get_text(strip=True)) if cells[5].get_text(strip=True).isdigit() else None
                            entrants[x] = entrant
                            break

        elif link['href'].endswith('/practice/4'):
            open_url(f"https://formula1.com{link['href']}")
            table = soup.find('table', class_='Table-module_table__cKsW2')
            rows = table.find('tbody').find_all('tr')
            for row in rows:
                cells = row.find_all('td')

                if len(cells) == 6:
                    for entrant in entrants:
                        if entrant['number'] == int(cells[1].get_text(strip=True)):
                            x = entrants.index(entrant)
                            entrant['practice4position'] = int(cells[0].get_text(strip=True))
                            gap = cells[4].get_text(strip=True) if cells[4].get_text(strip=True).startswith('+') else None
                            entrant['practice4time'] = cells[4].get_text(strip=True) if entrant['practice4position'] == 1 else tts_to_normal(
                                leadertime + tts(cells[4].get_text(strip=True).replace('+', '').replace('s', '')) if cells[4].get_text(strip=True) else None
                            )
                            entrant['practice4gap'] = gap
                            entrant['practice4timeinseconds'] = tts(entrant['practice4time']) if entrant['practice4time'] is not None else None
                            if entrant['practice4position'] == 1:
                                leadertime = entrant['practice4timeinseconds']
                            entrant['practice4laps'] = int(cells[5].get_text(strip=True))
                            entrants[x] = entrant
                            break

                elif len(cells) == 7:
                    for entrant in entrants:
                        if entrant['number'] == int(cells[1].get_text(strip=True)):
                            x = entrants.index(entrant)
                            entrant['practice4position'] = int(cells[0].get_text(strip=True))
                            gap = cells[4].get_text(strip=True) if cells[4].get_text(strip=True).startswith('+') else None
                            entrant['practice4time'] = cells[4].get_text(strip=True) if entrant['practice4position'] == 1 else tts_to_normal(
                                leadertime + tts(cells[4].get_text(strip=True).replace('+', '').replace('s', ''))
                            )
                            entrant['practice4gap'] = gap
                            entrant['practice4timeinseconds'] = tts(entrant['practice4time'])
                            if entrant['practice4position'] == 1:
                                leadertime = entrant['practice4timeinseconds']
                            entrant['practice4laps'] = int(cells[5].get_text(strip=True)) if cells[5].get_text(strip=True).isdigit() else None
                            entrants[x] = entrant
                            break
        elif link['href'].endswith('/sprint.aspx'):
            SPRINT_TIME_REGEX = re.compile(r'(?:\d+:)?\d{1,2}:\d{2}\.\d+')  #33'38.998 -> 33:38.998 after the apostrophe swap; may be preceded by a reason
            sprintweekend = True
            open_url(f"https://www.statsf1.com{link['href']}")
            sprintpenalties = parse_penalties(soup, is_sprint=True)
            table = soup.find('table', class_ = 'datatable')
            #The sprint table has no car number column: pos | driver | constructor | engine | laps | time (gap) | places gained | points.
            #Times are printed as 33'38.998, so they go through tts() rather than the race TIME_REGEX, which would truncate 33:38.998 to 33:38.
            rows = table.find('tbody').find_all('tr')
            for row in rows:
                cells = row.find_all('td')
                for entrant in entrants:
                    if  normalize_name(entrant['driver'].lower()) in normalize_name(cells[1].get_text(strip=True).lower()):
                        x = entrants.index(entrant)
                        raw = cells[5].get_text(strip=True).replace("'", ":")
                        time_part = (raw.split('(')[0] if '(' in raw else raw).strip()
                        entrant['sprintgap'] = raw.split('(')[1].replace(')', '') if '(' in raw and raw.split('(')[1].replace(')', '').strip().endswith('s') else None
                        entrant['sprintgapinseconds'] = parse_race_time(entrant['sprintgap'].replace("+", "")) if entrant['sprintgap'] else None
                        #A disqualified driver's cell carries the reason and the time together ("Technical non-compliance30'03.483").
                        sprint_time_match = SPRINT_TIME_REGEX.search(time_part)
                        if sprint_time_match:
                            entrant['sprinttime'] = sprint_time_match.group(0)
                            entrant['sprintstatusreason'] = time_part[:sprint_time_match.start()].strip() or None
                            entrant['sprinttimeinseconds'] = tts(entrant['sprinttime'])
                        else:
                            entrant['sprinttime'] = None
                            entrant['sprintstatusreason'] = time_part or None
                            entrant['sprinttimeinseconds'] = None
                        if cells[0].get_text(strip=True) in ['ab', 'nc', 'np', 'nq', 'npq', 'dsq', 'exc', 'f', 'tf', 't']:
                            entrant['sprintposition'] = None
                            entrant['sprintlaps'] = int(cells[4].get_text(strip=True)) if cells[4].get_text(strip=True).isdigit() else None
                            entrant['sprintstatus'] = abbreviations[cells[0].get_text(strip=True)]
                            entrant['sprintpoints'] = 0
                        else:
                            entrant['sprintposition'] = int(cells[0].get_text(strip=True))
                            entrant['sprintlaps'] = int(cells[4].get_text(strip=True))
                            entrant['sprintstatus'] = 'Classified'
                            entrant['sprintpoints'] = float(cells[7].get_text(strip=True)) if cells[7].get_text(strip=True).replace('.', '', 1).isdigit() else None
                        entrants[x] = entrant
                        break
        elif link['href'].endswith('/classement.aspx'):
            TIME_REGEX = re.compile(
                r'(\d+h\s*)?(\d+m\s*)?(\d+(?:\.\d+)?s|\d+:\d{2}(?::\d{2}(?:\.\d+)?)?)'
            )            
            #print(entrants[19])
            open_url(f"https://www.statsf1.com{link['href']}")
            table = soup.find('table', class_ = 'datatable')
            penalties = parse_penalties(soup, is_sprint=False)
            if sprintweekend == True:
                for penalty in sprintpenalties:
                    penalties.append(penalty)
            #You need to do shared cars and avoid exceptions when the race position is "ab" or something
            rows = table.find('tbody').find_all('tr')
            main_car = None  # Keep track of the main car for shared cars
            for row in rows:
                #print (row)
                cells = row.find_all('td')
                if len(cells) >= 8:
                    if not cells[1].get_text(strip=True).isdigit() and cells[2].get_text(strip=True) == '':
                        #print("")
                        #print (row)
                        continue  # Skip this row if the "No" column is blank or invalid                    
                    # Check if the row represents a shared car
                    if cells[0].get_text(strip=True) == '&':  # Shared car entry
                        #print("DEBUG: Shared car detected.")
                        #print("DEBUG: Current main car before processing shared car:", main_car)                        
                        if main_car:
                            for entrant in entrants:
                                if normalize_name(entrant['driver'].lower()) in normalize_name(cells[2].get_text(strip=True).lower()) and entrant['number'] == main_car['number']:
                                    # Create a new entry for the shared car
                                    entrant['raceposition'] = main_car["raceposition"]
                                    entrant['racelaps'] = int(cells[5].get_text(strip=True)) if cells[5].get_text(strip=True).isdigit() else None
                                    entrant['racetime'] = cells[6].get_text(strip=True)
                                    entrant['racepoints'] = float(cells[7].get_text(strip=True)) if cells[7].get_text(strip=True).replace('.', '', 1).isdigit() else None
                                    if cells[0].get_text(strip=True) in ['ab', 'nc', 'np', 'nq', 'npq', 'dsq', 'exc', 'f', 'tf', 't']:
                                        if cells[0].get_text(strip=True) == 'npq':
                                            parseprequalifyingflag = True                                         
                                        entrant['raceposition'] = None
                                        entrant['racelaps'] = int(cells[5].get_text(strip=True)) if cells[5].get_text(strip=True).isdigit() else None
                                        entrant['racetime'] = cells[6].get_text(strip=True) 
                                        entrant['racestatus'] = main_car['racestatus']
                                        entrant['racegap'] = cells[6].get_text(strip=True).split('(')[1].replace(')', '') if '(' in cells[6].get_text(strip=True) and cells[6].get_text(strip=True).split('(')[1].replace(')', '').strip().endswith('s')  else None
                                        raw = cells[6].get_text(strip=True)
                                        m = TIME_REGEX.search(raw)
                                        if m:
                                            entrant['racetime'] = m.group(0).strip().replace("h ", ":").replace("m ", ":") if m.group(0).strip() else None
                                            entrant['racestatusreason'] = raw[:m.start()].strip() or None
                                        else:
                                            entrant['racetime'] = None
                                            entrant['racestatusreason'] = raw.strip() or None                            
                                        entrant['racepoints'] = 0  
                                    elif main_car['raceposition'] is None:
                                        entrant['raceposition'] = None
                                        entrant['raceposition'] = None
                                        entrant['racelaps'] = int(cells[5].get_text(strip=True)) if cells[5].get_text(strip=True).isdigit() else None
                                        entrant['racetime'] = cells[6].get_text(strip=True)
                                        entrant['racestatus'] = main_car['racestatus'] if main_car.get('racestatus') else abbreviations[cells[0].get_text(strip=True)]
                                        entrant['racegap'] = cells[6].get_text(strip=True).split('(')[1].replace(')', '') if '(' in cells[6].get_text(strip=True) and cells[6].get_text(strip=True).split('(')[1].replace(')', '').strip().endswith('s')  else None
                                        raw = cells[6].get_text(strip=True)
                                        m = TIME_REGEX.search(raw)
                                        if m:
                                            entrant['racetime'] = m.group(0).strip().replace("h ", ":").replace("m ", ":") if m.group(0).strip() else None
                                            entrant['racestatusreason'] = raw[:m.start()].strip() or None
                                        else:
                                            entrant['racetime'] = None
                                            entrant['racestatusreason'] = raw.strip() or None   
                                        entrant['racepoints'] = 0  
                                    if entrant["racetime"] is not None:
                                        entrant["racetime"] = entrant["racetime"].split('(')[0] if '(' in entrant["racetime"] else entrant["racetime"]
                                        entrant['racetimeinseconds'] = parse_race_time(entrant['racetime'])
                                        entrant['racegap'] = cells[6].get_text(strip=True).split('(')[1].replace(')', '') if '(' in cells[6].get_text(strip=True) and cells[6].get_text(strip=True).split('(')[1].replace(')', '').strip().endswith('s')  else None
                                        entrant['racegapinseconds'] = parse_race_time(entrant['racegap'].replace("+", "")) if entrant['racegap'] else None
                                        raw = cells[6].get_text(strip=True)
                                        m = TIME_REGEX.search(raw)
                                        if m:
                                            entrant['racetime'] = m.group(0).strip().replace("h ", ":").replace("m ", ":") if m.group(0).strip() else None
                                            entrant['racestatusreason'] = raw[:m.start()].strip() or None
                                        else:
                                            entrant['racetime'] = None
                                            entrant['racestatusreason'] = raw.strip() or None  
                                        entrant['racestatus'] = 'Classified'                                          
                                    # Append the shared car entry to the race results
                                    #print ("Main car: ", main_car)
                                    #print ("Shared car: ",  entrant)
                                    #entrants[x] = entrant
                                    break
                                elif normalize_name(entrant['driver'].lower()) in normalize_name(cells[2].get_text(strip=True).lower()) and entrant['number'] != main_car['number']:
                                    shared_car = {
                                        "driver": entrant['driver'],
                                        "team": main_car["team"],
                                        "constructor": main_car["constructor"],
                                        "raceposition": main_car["raceposition"],
                                        "number": main_car["number"],
                                        "chassis": main_car["chassis"],
                                        "engine": main_car["engine"],
                                        "enginemodel": main_car['enginemodel'],
                                        "tyre": main_car['tyre'],
                                        "raceposition": main_car["raceposition"],
                                        "racelaps": int(cells[5].get_text(strip=True)) if cells[5].get_text(strip=True).isdigit() else None,
                                        "racepoints": float(cells[7].get_text(strip=True)) if cells[7].get_text(strip=True).replace('.', '', 1).isdigit() else None,
                                        "racetime": cells[6].get_text(strip=True)
                                    }
                                    if cells[0].get_text(strip=True) in ['ab', 'nc', 'np', 'nq', 'npq', 'dsq', 'exc', 'f', 'tf', 't']:
                                        if cells[0].get_text(strip=True) == 'npq':
                                            parseprequalifyingflag = True                                        
                                        shared_car['raceposition'] = None
                                        shared_car['racelaps'] = int(cells[5].get_text(strip=True)) if cells[5].get_text(strip=True).isdigit() else None
                                        shared_car['racetime'] = cells[6].get_text(strip=True) 
                                        shared_car['racestatus'] = main_car['racestatus']
                                        shared_car['racegap'] = cells[6].get_text(strip=True).split('(')[1].replace(')', '') if '(' in cells[6].get_text(strip=True) and cells[6].get_text(strip=True).split('(')[1].replace(')', '').strip().endswith('s')  else None
                                        raw = cells[6].get_text(strip=True)
                                        m = TIME_REGEX.search(raw)
                                        if m:
                                            shared_car['racetime'] = m.group(0).strip().replace("h ", ":").replace("m ", ":") if m.group(0).strip() else None
                                            shared_car['racestatusreason'] = raw[:m.start()].strip() or None
                                        else:
                                            shared_car['racetime'] = None
                                            shared_car['racestatusreason'] = raw.strip() or None                                              
                                        shared_car['racepoints'] = None
                                    elif main_car['raceposition'] is None:
                                        shared_car['raceposition'] = None
                                        shared_car['racelaps'] = int(cells[5].get_text(strip=True)) if cells[5].get_text(strip=True).isdigit() else None
                                        shared_car['racetime'] = cells[6].get_text(strip=True) 
                                        shared_car['racestatus'] = main_car['racestatus'] 
                                        shared_car['racegap'] = cells[6].get_text(strip=True).split('(')[1].replace(')', '') if '(' in cells[6].get_text(strip=True) and cells[6].get_text(strip=True).split('(')[1].replace(')', '').strip().endswith('s')  else None
                                        raw = cells[6].get_text(strip=True)
                                        m = TIME_REGEX.search(raw)
                                        if m:
                                            shared_car['racetime'] = m.group(0).strip().replace("h ", ":").replace("m ", ":") if m.group(0).strip() else None
                                            shared_car['racestatusreason'] = raw[:m.start()].strip() or None
                                        else:
                                            shared_car['racetime'] = None
                                            shared_car['racestatusreason'] = raw.strip() or None                                              
                                        shared_car['racepoints'] = None                                        
                                    if shared_car["racetime"] is not None:
                                        shared_car["racetime"] = shared_car["racetime"].split('(')[0] if '(' in shared_car["racetime"] else shared_car["racetime"]
                                        shared_car['racetimeinseconds'] = parse_race_time(shared_car['racetime'])
                                        shared_car['racegap'] = cells[6].get_text(strip=True).split('(')[1].replace(')', '') if '(' in cells[6].get_text(strip=True) and cells[6].get_text(strip=True).split('(')[1].replace(')', '').strip().endswith('s')  else None
                                        shared_car['racegapinseconds'] = parse_race_time(shared_car['racegap'].replace("+", "")) if shared_car['racegap'] else None
                                        raw = cells[6].get_text(strip=True)
                                        m = TIME_REGEX.search(raw)
                                        if m:
                                            shared_car['racetime'] = m.group(0).strip().replace("h ", ":").replace("m ", ":") if m.group(0).strip() else None
                                            shared_car['racestatusreason'] = raw[:m.start()].strip() or None
                                        else:
                                            shared_car['racetime'] = None
                                            shared_car['racestatusreason'] = raw.strip() or None  
                                        shared_car['racestatus'] = 'Classified'                                        
                                    entrants.append(shared_car)
                                    break
                    else:  # Main car entry
                        for entrant in entrants:
                            if entrant['number'] == int(cells[1].get_text(strip=True)) and normalize_name(entrant['driver'].lower()) in normalize_name(cells[2].get_text(strip=True).lower()):
                                # Process the main car entry
                                if cells[0].get_text(strip=True) in ['ab', 'nc', 'np', 'nq', 'npq', 'dsq', 'exc', 'f', 'tf', 't']:
                                    if cells[0].get_text(strip=True) == 'npq':
                                        parseprequalifyingflag = True                                     
                                    entrant['raceposition'] = None
                                    entrant['racelaps'] = int(cells[5].get_text(strip=True)) if cells[5].get_text(strip=True).isdigit() else None
                                    entrant['racetime'] = cells[6].get_text(strip=True) + f" ({abbreviations[cells[0].get_text(strip=True)]})"
                                    entrant['racestatus'] = abbreviations[cells[0].get_text(strip=True)]
                                    entrant['racegap'] = cells[6].get_text(strip=True).split('(')[1].replace(')', '') if '(' in cells[6].get_text(strip=True) and cells[6].get_text(strip=True).split('(')[1].replace(')', '').strip().endswith('s')  else None
                                    raw = cells[6].get_text(strip=True)
                                    m = TIME_REGEX.search(raw)
                                    if m:
                                        entrant['racetime'] = m.group(0).strip().replace("h ", ":").replace("m ", ":") if m.group(0).strip() else None
                                        entrant['racestatusreason'] = raw[:m.start()].strip() or None
                                    else:
                                        entrant['racetime'] = None
                                        entrant['racestatusreason'] = raw.strip() or None                                          
                                    entrant['racepoints'] = 0
                                else:
                                    entrant['raceposition'] = int(cells[0].get_text(strip=True))
                                    entrant['racelaps'] = int(cells[5].get_text(strip=True))
                                    entrant['racetime'] = cells[6].get_text(strip=True).split('(')[0] if '(' in cells[6].get_text(strip=True) else cells[6].get_text(strip=True)
                                    entrant['racetimeinseconds'] = parse_race_time(entrant['racetime'])
                                    entrant['racegap'] = cells[6].get_text(strip=True).split('(')[1].replace(')', '') if '(' in cells[6].get_text(strip=True) and cells[6].get_text(strip=True).split('(')[1].replace(')', '').strip().endswith('s')  else None
                                    entrant['racegapinseconds'] = parse_race_time(entrant['racegap'].replace("+", "")) if entrant['racegap'] else None
                                    entrant['racepoints'] = float(cells[7].get_text(strip=True)) if cells[7].get_text(strip=True).replace('.', '', 1).isdigit() else None
                                    raw = cells[6].get_text(strip=True)
                                    m = TIME_REGEX.search(raw)
                                    if m:
                                        entrant['racetime'] = m.group(0).strip().replace("h ", ":").replace("m ", ":") if m.group(0).strip() else None
                                        entrant['racestatusreason'] = raw[:m.start()].strip() or None
                                    else:
                                        entrant['racetime'] = None
                                        entrant['racestatusreason'] = raw.strip() or None  
                                    entrant['racestatus'] = 'Classified'                                                              
                                main_car = entrant  # Update the main car reference                                   
                                break  # Exit the loop once the entrant is found
                else:
                    raise ValueError(f"Unexpected number of cells in row: {len(cells)}. Expected at least 8 cells.")
        elif link['href'].startswith('https://motorsportstats.com') and link['href'].endswith('_race'):
            b = open_json(link['href'])
            for driver in b['details']:
                for entrant in entrants:
                    if int(driver['carNumber']) == entrant['number'] and not entrant['substituteorthirddriver']:
                        if entrant['racetime'] is None:
                            timestewer = driver['time']
                            timestr = int(timestewer)
                            h = timestr // (3600000)
                            m = (timestr % 3600000) // 60000
                            s = (timestr % 60000) / 1000
                            if timestr == 0:
                                entrant['racetime'] = None
                                entrant['racetimeinseconds'] = None
                                break
                            entrant['racetime'] = f"{h:02d}:{m:02d}.{s:03.0f}s"
                            entrant['racetimeinseconds'] = float(Decimal(str(timestr / 1000)).quantize(Decimal('0.001')))
                        if entrant['racegap'] is None:
                            gapstewer = driver['gap']['timeToLead']   
                            gapstr = int(gapstewer)
                            h = gapstr // (3600000)
                            m = (gapstr % 3600000) // 60000
                            s = (gapstr % 60000) / 1000
                            if gapstr != 0:
                                entrant['racegap'] = f"{h:02d}:{m:02d}.{s:03.0f}s"
                                entrant['racegapinseconds'] = float(Decimal(str(gapstr / 1000)).quantize(Decimal('0.001')))
        elif link['href'].startswith('https://motorsportstats.com') and link['href'].endswith('_sprint'):
            b = open_json(link['href'])
            for driver in b['details']:
                for entrant in entrants:
                    if int(driver['carNumber']) == entrant['number'] and not entrant['substituteorthirddriver']:
                        if entrant['sprinttime'] is None:
                            timestewer = driver['time']
                            timestr = int(timestewer)
                            if timestr == 0:
                                entrant['sprinttime'] = None
                                entrant['sprinttimeinseconds'] = None
                                break
                            h = timestr // (3600000)
                            m = (timestr % 3600000) // 60000
                            s = (timestr % 60000) / 1000
                            entrant['sprinttime'] = f"{h:02d}:{m:02d}.{s:03.0f}s"
                            entrant['sprinttimeinseconds'] = float(Decimal(str(timestr / 1000)).quantize(Decimal('0.001')))  
                        if entrant['sprintgap'] is None:
                            gapstewer = driver['gap']['timeToLead']   
                            gapstr = int(gapstewer)
                            h = gapstr // (3600000)
                            m = (gapstr % 3600000) // 60000
                            s = (gapstr % 60000) / 1000
                            if gapstr != 0:
                                entrant['sprintgap'] = f"{h:02d}:{m:02d}.{s:03.0f}s"
                                entrant['sprintgapinseconds'] = float(Decimal(str(gapstr / 1000)).quantize(Decimal('0.001')))
        elif link['href'].startswith('https://motorsportstats.com') and (link['href'].endswith ('qualifying-1') or link['href'].endswith('1st-qualifying')):
            b = open_json(link['href'])
            for driver in b['details']:
                for entrant in entrants:
                    if int(driver['carNumber']) == entrant['number'] and not entrant['substituteorthirddriver']:
                        if year < 1958:
                            if not normalize_name(driver['drivers'][0]['name'].lower()) == normalize_name(entrant['driver'].lower()):
                                continue #because of shared cars.
                        if entrant.get('qualifying1position') is None:
                            entrant['qualifying1position'] = driver['finishPosition']
                        if entrant.get('qualifying1time') is None and driver.get('time') is not None:
                            entrant['qualifying1timeinseconds'] = float(Decimal(str(driver['time'] / 1000)).quantize(Decimal('0.001')))
                            entrant['qualifying1time'] = tts_to_normal(entrant['qualifying1timeinseconds'])
                        q1_gap_to_lead = (driver.get('gap') or {}).get('timeToLead')
                        if entrant.get('qualifying1gap') is None and driver['finishPosition'] != 1 and q1_gap_to_lead is not None:
                            entrant['qualifying1gap'] = float(Decimal(str(q1_gap_to_lead / 1000)).quantize(Decimal('0.001')))
                        if year > 2011 and entrant.get('qualifying1laps') is None:
                            entrant['qualifying1laps'] = driver['laps']
        elif link['href'].startswith('https://motorsportstats.com') and (link['href'].endswith ('qualifying-2') or link['href'].endswith('2nd-qualifying')):
            b = open_json(link['href'])
            for driver in b['details']:
                for entrant in entrants:
                    if gp == "1956 Italian Grand Prix" and driver['carNumber'] == '?':
                        driver['carNumber'] == '12'
                        if entrant['driver'].lower() != "joao rezende dos santos":
                            continue #because of shared cars. 
                    if int(driver['carNumber']) == entrant['number'] and not entrant['substituteorthirddriver']:
                        if year < 1958:
                            if not normalize_name(driver['drivers'][0]['name'].lower()) == normalize_name(entrant['driver'].lower()):
                                continue #because of shared cars.                        
                        if entrant.get('qualifying2position') is None:
                            entrant['qualifying2position'] = driver['finishPosition']
                        if entrant.get('qualifying2time') is None and driver.get('time') is not None:
                            entrant['qualifying2timeinseconds'] = float(Decimal(str(driver['time'] / 1000)).quantize(Decimal('0.001')))
                            entrant['qualifying2time'] = tts_to_normal(entrant['qualifying2timeinseconds'])
                        q2_gap_to_lead = (driver.get('gap') or {}).get('timeToLead')
                        if entrant.get('qualifying2gap') is None and driver['finishPosition'] != 1 and q2_gap_to_lead is not None:
                            entrant['qualifying2gap'] = float(Decimal(str(q2_gap_to_lead / 1000)).quantize(Decimal('0.001')))
                        if year > 2011 and entrant.get('qualifying2laps') is None:
                            entrant['qualifying2laps'] = driver['laps']
                        if gp == "2005 Australian Grand Prix":
                            entrant['qualifying2position'] = driver['finishPosition'] # Correcting known data issue for 2005 AUS GP Q2
        elif link['href'].startswith('https://motorsportstats.com') and (link['href'].endswith ('qualifying-3') or link['href'].endswith('3rd-qualifying')):
            if link['href'] == "https://motorsportstats.com/api/results-classification?sessionSlug=fia-formula-one-world-championship_2015_united-states-grand-prix_qualifying-3":
                continue #q3 skipped that race due to rain.
            b = open_json(link['href'])
            for driver in b['details']:
                for entrant in entrants:
                    if int(driver['carNumber']) == entrant['number'] and not entrant['substituteorthirddriver']:
                        if entrant.get('qualifying3position') is None:
                            entrant['qualifying3position'] = driver['finishPosition']
                        entrant['qualifying3gap'] = float(Decimal(str(driver['gap']['timeToLead'] / 1000)).quantize(Decimal('0.001'))) if driver['finishPosition'] != 1 else None
                        if year > 2011 and entrant.get('qualifying3laps') is None:
                            entrant['qualifying3laps'] = driver['laps']
        elif link['href'].startswith('https://motorsportstats.com') and link['href'].endswith ('1st-sprint-qualifying'):
            b = open_json(link['href'])
            for driver in b['details']:
                for entrant in entrants:
                    if int(driver['carNumber']) == entrant['number'] and not entrant['substituteorthirddriver']:
                        if entrant.get('sprintqualifying1position') is None:
                            entrant['sprintqualifying1position'] = driver['finishPosition']
                        entrant['sprintqualifying1gapseconds'] = float(Decimal(str(driver['gap']['timeToLead'] / 1000)).quantize(Decimal('0.001'))) if driver['finishPosition'] != 1 else None
                        if year > 2011 and entrant.get('sprintqualifying1laps') is None:
                            entrant['sprintqualifying1laps'] = driver['laps']
        elif link['href'].startswith('https://motorsportstats.com') and link['href'].endswith ('2nd-sprint-qualifying'):
            b = open_json(link['href'])
            for driver in b['details']:
                for entrant in entrants:
                    if int(driver['carNumber']) == entrant['number'] and not entrant['substituteorthirddriver']:
                        if entrant.get('sprintqualifying2position') is None:
                            entrant['sprintqualifying2position'] = driver['finishPosition']
                        entrant['sprintqualifying2gapseconds'] = float(Decimal(str(driver['gap']['timeToLead'] / 1000)).quantize(Decimal('0.001'))) if driver['finishPosition'] != 1 else None
                        if year > 2011 and entrant.get('sprintqualifying2laps') is None:
                            entrant['sprintqualifying2laps'] = driver['laps']
        elif link['href'].startswith('https://motorsportstats.com') and link['href'].endswith ('3rd-sprint-qualifying'):
            b = open_json(link['href'])
            for driver in b['details']:
                for entrant in entrants:
                    if int(driver['carNumber']) == entrant['number'] and not entrant['substituteorthirddriver']:
                        if entrant.get('sprintqualifying3position') is None:
                            entrant['sprintqualifying3position'] = driver['finishPosition']
                        entrant['sprintqualifying3gapseconds'] = float(Decimal(str(driver['gap']['timeToLead'] / 1000)).quantize(Decimal('0.001'))) if driver['finishPosition'] != 1 else None
                        if year > 2011 and entrant.get('sprintqualifying3laps') is None:
                            entrant['sprintqualifying3laps'] = driver['laps']
    for penalty in penalties:
        for entrant in entrants:
            if normalize_name(entrant['driver'].lower()) == normalize_name(penalty['driver'].lower()):
                key = 'sprint_penalties' if penalty['is_sprint'] else 'penalties'
                if key not in entrant:
                    entrant[key] = []
                penalty_entry = {
                    "penalty": penalty["penalty"],
                    "reason": penalty["reason"],
                    "type": penalty["type"]
                }
                if "lost_position" in penalty:
                    penalty_entry["lost_position"] = penalty["lost_position"]
                entrant[key].append(penalty_entry)
                break
    
    if parseprequalifyingflag:
        title = gp.replace(" ", "_")
        encoded = urllib.parse.quote(title)
        url = f"https://en.wikipedia.org/api/rest_v1/page/html/{encoded}"
        preq_table = None
        retries = 0
        while retries < 3:
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "F1DB/1.0"}
                )
            except Exception as e:
                print(f"Error creating request for {title}: {e}")
                retries += 1
            else:
                break
        #raise error if request creation failed after retries
        if retries == 3:
            raise ValueError(f"Failed to create request for {title} after multiple attempts")

        try:
            with urllib.request.urlopen(req) as response:
                html = response.read().decode("utf-8")
        except Exception as e:
            raise ValueError(f"Failed to fetch {title}: {e}")

        wiki_soup = BeautifulSoup(html, "html.parser")            
        
        # Look for "Pre-qualifying" heading
        preq_heading = None
        for tag in wiki_soup.find_all(["h2", "h3", "h4"]):
            if "pre-qualifying" in tag.get_text(strip=True).lower() or "pre-qualification" in tag.get_text(strip=True).lower():
                preq_heading = tag
                # Find the next table after the heading
                preq_table = None
                for sibling in preq_heading.find_all_next():
                    if sibling.name == "table":
                        preq_table = sibling
                        break
                    # Stop if we hit another heading of same or higher level
                    if sibling.name in ["h2", "h3", "h4"] and sibling != preq_heading:
                        break
                if preq_table is not None:
                    rows = preq_table.find_all("tr")
                        
                    # Parse header to understand column layout
                    header_row = rows[0] if rows else None
                    headers_ = []
                    if header_row:
                        headers_ = [th.get_text(strip=True).lower() for th in header_row.find_all(["th", "td"])]
                    
                    # Detect column indices dynamically
                    def find_col(keywords):
                        for i, h in enumerate(headers_):
                            for kw in keywords:
                                if kw in h:
                                    return i
                        return None
                    
                    pos_idx  = find_col(["pos"])
                    no_idx   = find_col(["no", "#"])
                    drv_idx  = find_col(["driver"])
                    con_idx  = find_col(["constructor"])
                    
                    # Check for combined "Time/Gap" column first, before separate time/gap
                    timegap_idx = find_col(["time/gap"])
                    if timegap_idx is not None:
                        time_idx = timegap_idx
                        gap_idx  = None
                        combined_timegap = True
                    else:
                        combined_timegap = False
                        time_idx = find_col(["time"])
                        gap_idx  = find_col(["gap"])
                    
                    leadertime = None
                    previoustime = None

                    for row in rows[1:]:
                        cells = row.find_all(["th", "td"])
                        if not cells:
                            continue

                        def cell_text(idx):
                            if idx is not None and idx < len(cells):
                                return cells[idx].get_text(strip=True)
                            return ""

                        pos         = cell_text(pos_idx)
                        car_no      = cell_text(no_idx)
                        driver      = cell_text(drv_idx)
                        constructor = cell_text(con_idx)
                        raw_timegap = cell_text(time_idx)

                        if not driver:
                            continue

                        pos = pos.replace("—", "").strip()

                        # Image 1 style: relative time like "-1:37.0" or "+1:37.0" in a plain Time column
                        # These are not real lap times so skip time/gap calculations entirely
                        if (raw_timegap.startswith("+") or raw_timegap.startswith("-")) and ":" in raw_timegap:
                            time = raw_timegap
                            timeinseconds = None
                            gap = None
                            interval = None

                        elif combined_timegap:
                            if raw_timegap.startswith("+") or raw_timegap.startswith("-"):
                                gap_val = float(Decimal(raw_timegap.replace("+", "").replace("-", "").strip().rstrip('s')).quantize(Decimal('0.001')))
                                if leadertime is not None:
                                    timeinseconds = leadertime + gap_val
                                    time = tts_to_normal(timeinseconds)
                                else:
                                    timeinseconds = None
                                    time = None
                                gap = raw_timegap
                                interval = float(Decimal(str(timeinseconds - previoustime)).quantize(Decimal('0.001'))) if timeinseconds and previoustime else None
                            else:
                                time = raw_timegap
                                timeinseconds = tts(time) if time else None
                                gap = None
                                interval = None
                                if pos == "1" and timeinseconds:
                                    leadertime = timeinseconds

                        else:
                            time = raw_timegap
                            timeinseconds = tts(time) if time else None
                            gap = cell_text(gap_idx)
                            interval = float(Decimal(str(timeinseconds - previoustime)).quantize(Decimal('0.001'))) if timeinseconds and previoustime else None
                            if pos == "1" and timeinseconds:
                                leadertime = timeinseconds

                        if timeinseconds:
                            previoustime = timeinseconds
                        # Skip empty rows
                        if not driver:
                            continue
                        for entrant in entrants:
                            if car_no and entrant['number'] == int(car_no):
                                entrant['prequalifyingposition'] = int(pos) if pos.isdigit() else None
                                entrant['prequalifyingtime'] = time
                                entrant['prequalifyingtimeinseconds'] = timeinseconds
                                entrant['prequalifyinggap'] = gap
                                entrant['prequalifyinginterval'] = interval                                
                            elif normalize_name(entrant['driver'].lower()) in normalize_name(driver.lower()):
                                entrant['prequalifyingposition'] = int(pos) if pos.isdigit() else None
                                entrant['prequalifyingtime'] = time
                                entrant['prequalifyingtimeinseconds'] = timeinseconds
                                entrant['prequalifyinggap'] = gap
                                entrant['prequalifyinginterval'] = interval
                    break
        if preq_table is None:
            print("Pre-qualifying section not found.")
                    

    # Calculate intervals for all sessions
    def calculate_intervals(entrants_list, session_type):
        """Calculate intervals (gap to person in front) for a session."""
        # Sort by position
        if session_type == 'race':
            sorted_entrants = sorted([e for e in entrants_list if e.get('raceposition')], key=lambda x: x['raceposition'])
            gap_key = 'racegapinseconds'
            interval_key = 'raceinterval'
        elif session_type == 'sprint':
            sorted_entrants = sorted([e for e in entrants_list if e.get('sprintposition')], key=lambda x: x['sprintposition'])
            gap_key = 'sprintgapinseconds'
            interval_key = 'sprintinterval'
        elif session_type == 'qualifying':
            sorted_entrants = sorted([e for e in entrants_list if e.get('qualifyingposition')], key=lambda x: x['qualifyingposition'])
            gap_key = 'qualifyinggapseconds'
            interval_key = 'qualifyinginterval'
        elif session_type == 'sprint_qualifying':
            sorted_entrants = sorted([e for e in entrants_list if e.get('sprint_qualifyingposition')], key=lambda x: x['sprint_qualifyingposition'])
            gap_key = 'sprint_qualifyinggap'
            interval_key = 'sprint_qualifyinginterval'            
        elif session_type.startswith('sprint_qualifying'):
            qual_num = session_type.split('_')[2]
            pos_key = f'sprint_qualifying{qual_num}position'
            gap_key = f'sprint_qualifying{qual_num}gap'
            interval_key = f'sprint_qualifying{qual_num}interval'
            sorted_entrants = sorted([e for e in entrants_list if e.get(pos_key)], key=lambda x: x[pos_key])
        elif session_type.startswith('qualifying'):
            qual_num = session_type.split('_')[1]
            pos_key = f'qualifying{qual_num}position'
            gap_key = f'qualifying{qual_num}gap'
            interval_key = f'qualifying{qual_num}interval'
            sorted_entrants = sorted([e for e in entrants_list if e.get(pos_key)], key=lambda x: x[pos_key])
        elif session_type.startswith('practice'):
            prac_num = session_type.split('_')[1]
            pos_key = f'practice{prac_num}position'
            gap_key = f'practice{prac_num}timeinseconds'
            interval_key = f'practice{prac_num}interval'
            sorted_entrants = sorted([e for e in entrants_list if e.get(pos_key)], key=lambda x: x[pos_key])
        elif session_type == 'fastestlap':
            sorted_entrants = sorted([e for e in entrants_list if e.get('fastestlap')], key=lambda x: x['fastestlap'])
            gap_key = 'fastestlapgapinseconds'
            interval_key = 'fastestlapinterval'
        elif session_type == 'sprintfastestlap':
            sorted_entrants = sorted([e for e in entrants_list if e.get('sprintfastestlap')], key=lambda x: x['sprintfastestlap'])
            gap_key = 'sprintfastestlapgapinseconds'
            interval_key = 'sprintfastestlapinterval'
        else:
            return
        
        prev_gap = None
        for entrant in sorted_entrants:
            if gap_key in entrant and entrant[gap_key] is not None:
                if prev_gap is None:
                    entrant[interval_key] = None
                else:
                    interval = float(Decimal(str(entrant[gap_key] - prev_gap)).quantize(Decimal('0.001')))
                    entrant[interval_key] = interval
                prev_gap = entrant[gap_key]
            else:
                entrant[interval_key] = None
    
    # Calculate intervals for all sessions
    calculate_intervals(entrants, 'race')
    calculate_intervals(entrants, 'sprint')
    calculate_intervals(entrants, 'qualifying')
    calculate_intervals(entrants, 'qualifying_1')
    calculate_intervals(entrants, 'qualifying_2')
    calculate_intervals(entrants, 'qualifying_3')
    calculate_intervals(entrants, 'sprint_qualifying')
    calculate_intervals(entrants, 'sprint_qualifying_1')
    calculate_intervals(entrants, 'sprint_qualifying_2')
    calculate_intervals(entrants, 'sprint_qualifying_3')
    calculate_intervals(entrants, 'practice_1')
    calculate_intervals(entrants, 'practice_2')
    calculate_intervals(entrants, 'practice_3')
    calculate_intervals(entrants, 'practice_4')
    calculate_intervals(entrants, 'fastestlap')
    calculate_intervals(entrants, 'sprintfastestlap')
                    
    return entrants.copy()


def fetch_tracinginsights_pitstops(year, grandprix_name):
    """
    Fetches pit stop data from TracingInsights GitHub archive.
    Returns list of pit stop dicts or None if not found.
    """
    # Format Grand Prix name for URL (e.g., "Bahrain Grand Prix" -> "Bahrain%20Grand%20Prix")
    if year >= 2021 and grandprix_name.endswith("Mexican Grand Prix"):
        grandprix_name = grandprix_name.replace("Mexican Grand Prix", "Mexico City Grand Prix")
    elif year >= 2026 and grandprix_name.endswith("Barcelona-Catalunya Grand Prix"):
        grandprix_name = grandprix_name.replace("Barcelona-Catalunya Grand Prix", "Barcelona Grand Prix")
    gp_encoded = urllib.parse.quote(grandprix_name.replace(str(year), '').strip())
    url = f"https://raw.githubusercontent.com/TracingInsights-Archive/PitStops/main/{year}/{gp_encoded}.json"
    retries = 0
    while retries < 3:
        try:
            req = urllib.request.Request(url, headers=headers)
            response = urllib.request.urlopen(req, timeout=10)
            break
        except urllib.error.URLError as e:
            retries += 1
            if retries == 3:
                raise RuntimeError(f"Failed to open URL {url} after three attempts due to error {e}")
    data = json.loads(response.read().decode('utf-8'))
    return data



def parse_pit_stop_summary (pit_table, entrants, year=None, grandprix_name=None):
    rows = pit_table.find('tbody').find_all('tr')
    pitstops = []
    for row in rows:
        cells = row.find_all('td')
        if len(cells) == 8:
            pitstopdetails = {
                'stopnumber': cells[0].text.strip(),
                'carnumber': cells[1].text.strip(),
                'lapstopped': cells[4].text.strip(),
                'timeofday': cells[5].text.strip(),
                'durationspentinpitlane': cells[6].text.strip(),
                'timeinseconds': tts(cells[6].text.strip()) if cells[6].text.strip() else None,
                'totaltimeforthewholerace': cells[7].text.strip(),
                'totaltimeinseconds': tts(cells[7].text.strip()) if cells[7].text.strip() else None
            }
            for entrant in entrants:
                if entrant['number'] == int(pitstopdetails['carnumber']):
                    pitstopdetails['driver'] = entrant['driver']
                    pitstopdetails['constructor'] = entrant['constructor']
                    pitstops.append(pitstopdetails)
                    break
    
    # Try to enhance with TracingInsights data if available (only for years > 2018)
    if year and grandprix_name:
        try:
            year_int = int(year)
            if year_int > 2018:
                ti_data = fetch_tracinginsights_pitstops(year_int, grandprix_name)
                if ti_data:
                    
                    # Build lookup map from TI data: (lap, driver_name, team) -> pit box time
                    ti_lookup = {}
                    for ti_stop in ti_data:
                        lap = int(ti_stop.get('Lap', 0))
                        driver = ti_stop.get('Driver', '').strip()
                        team = ti_stop.get('Team', '').strip()
                        pitbox_time = ti_stop.get('Time (sec)', None)
                        
                        if lap > 0 and driver and team and pitbox_time is not None:
                            key = (lap, driver.lower(), team.lower())
                            ti_lookup[key] = pitbox_time
                    
                    # Match pit stops with TI data by exact match first, then by subparts
                    # Store pit box time separately (don't overwrite timeinseconds)
                    matched_count = 0
                    for pitstop in pitstops:
                        lap = int(pitstop.get('lapstopped', 0))
                        driver = pitstop.get('driver', '').strip()
                        constructor = pitstop.get('constructor', '').strip()
                        
                        if lap > 0 and driver and constructor:
                            driver_lower = driver.lower()
                            constructor_lower = constructor.lower()
                            
                            # Try exact match first
                            key = (lap, driver_lower, constructor_lower)
                            if key in ti_lookup:
                                pitstop['durationstoppedinpitbox'] = ti_lookup[key]
                                pitstop['durationstoppedinpitbox_text'] = f"{ti_lookup[key]:.2f}"
                                matched_count += 1
                                continue
                            
                            # Try subpart matching: find TI entries with same lap
                            driver_parts_list = driver_lower.split()
                            driver_parts_set = set(driver_parts_list)
                            constructor_parts_list = constructor_lower.split()
                            constructor_parts_set = set(constructor_parts_list)
                            
                            best_match = None
                            best_score = 0
                            
                            for (ti_lap, ti_driver, ti_team), ti_pitbox_time in ti_lookup.items():
                                if ti_lap != lap:
                                    continue
                                
                                # Check driver subpart match
                                ti_driver_parts_list = ti_driver.split()
                                ti_driver_parts_set = set(ti_driver_parts_list)
                                driver_match = False
                                if driver_parts_set & ti_driver_parts_set:  # Check for common words
                                    driver_match = True
                                elif len(driver_parts_list) > 0 and len(ti_driver_parts_list) > 0:
                                    # Check if last names match (last part of name)
                                    if driver_parts_list[-1] == ti_driver_parts_list[-1]:
                                        driver_match = True
                                
                                # Check team/constructor subpart match
                                ti_team_parts_list = ti_team.split()
                                ti_team_parts_set = set(ti_team_parts_list)
                                team_match = False
                                if constructor_parts_set & ti_team_parts_set:  # Check for common words
                                    team_match = True
                                elif len(constructor_parts_list) > 0 and len(ti_team_parts_list) > 0:
                                    # Check if one contains the other (e.g., "Red Bull" in "Red Bull Racing")
                                    constructor_str = ' '.join(constructor_parts_list)
                                    team_str = ' '.join(ti_team_parts_list)
                                    if constructor_str in team_str or team_str in constructor_str:
                                        team_match = True
                                
                                # Score: driver match = 2 points, team match = 1 point
                                score = 0
                                if driver_match:
                                    score += 2
                                if team_match:
                                    score += 1
                                
                                # Require at least driver match
                                if score > best_score and score >= 2:
                                    best_score = score
                                    best_match = ti_pitbox_time
                            
                            if best_match is not None:
                                pitstop['durationstoppedinpitbox'] = best_match
                                pitstop['durationstoppedinpitbox_text'] = f"{best_match:.2f}"
                                matched_count += 1
        except (ValueError, TypeError):
            pass  # Skip if year cannot be converted to int
    
    return pitstops.copy()

def scrape_mss_laptimes(url, entrants):
    dqedj= open_json(url)
    laps = []  
    for driver in dqedj['content']:
        for entrant in entrants:
            if int(driver['carNumber']) == entrant['number']:
                for lapdata in driver['laps']:
                    lap = {
                        'driver': entrant['driver'],
                        'lap': lapdata['lap'],
                        'timeinseconds': float(Decimal(str(lapdata['time'])).quantize(Decimal('0.001'))) if lapdata['time'] is not None else None,
                        'time': tts_to_normal(lapdata['time']) if lapdata['time'] is not None else None
                    }
                    laps.append(lap)

    return laps

def readlapcharts(url):
    lapchartdata = open_json(url)['content']
    entrant_map = defaultdict(list)
    for lap_entry in lapchartdata:
        lap = lap_entry['lap']
        for car in lap_entry['cars']:
            if not car:
                continue
            position = lap_entry['cars'].index(car) + 1
            entrant_map[int(car)].append((lap, position))
    return entrant_map
            

def scrape_tracinginsights (url, type=None, driver_abbr=None, fastf1_session=None):
    #find the session we are in from the url, example sprint qualifying, etc. 
    
    session_name = re.search(r'/([^/]+)/[^/]+/laptimes\.json$', url).group(1)
    session_name = urllib.parse.unquote(session_name).lower().replace(" ","")
    if session_name == "race":
        session_name = "grandprix"
    retries = 0
    while retries < 3:
        try:
            data = urllib.request.urlopen(url).read().decode('utf-8')
            break
        except:
            retries += 1
            time.sleep(10)
            if retries == 3:
                numberoflaps = list(fastf1_session.laps.pick_drivers(driver_abbr)['LapNumber'])
                data = json.dumps({"lap": numberoflaps})
    data = json.loads(data)

    # Mapping from TracingInsights keys to fastf1 field names with conversion logic
    tracinginsightskeysmappedtofastf1 = {
        'time': ('Time', lambda x: x),  # Return as-is for now, handled by get_float_value
        'compound': ('Compound', lambda x: x),
        'stint': ('Stint', lambda x: x),
        's1': ('Sector1Time', lambda x: x.total_seconds() if hasattr(x, 'total_seconds') and not pd.isna(x) else None),
        's2': ('Sector2Time', lambda x: x.total_seconds() if hasattr(x, 'total_seconds') and not pd.isna(x) else None),
        's3': ('Sector3Time', lambda x: x.total_seconds() if hasattr(x, 'total_seconds') and not pd.isna(x) else None),
        'life': ('TyreLife', lambda x: x),
        'pos': ('Position', lambda x: x),
        'status': ('TrackStatus', lambda x: x),
        'lST': ('LapStartDate', lambda x: x.total_seconds() if hasattr(x, 'total_seconds') and not pd.isna(x) else None),
        'lSD': ('LapStartDate', lambda x: str(x) if not pd.isna(x) else None),
        'sesT': ('Time', lambda x: x.total_seconds() if hasattr(x, 'total_seconds') and not pd.isna(x) else None),
        's1T': ('Sector1SessionTime', lambda x: x.total_seconds() if hasattr(x, 'total_seconds') and not pd.isna(x) else None),
        's2T': ('Sector2SessionTime', lambda x: x.total_seconds() if hasattr(x, 'total_seconds') and not pd.isna(x) else None),
        's3T': ('Sector3SessionTime', lambda x: x.total_seconds() if hasattr(x, 'total_seconds') and not pd.isna(x) else None),
        'vi1': ('SpeedI1', lambda x: x),
        'vi2': ('SpeedI2', lambda x: x),
        'vfl': ('SpeedFL', lambda x: x),
        'vst': ('SpeedST', lambda x: x),
        'pin': ('PitInTime', lambda x: x.total_seconds() if hasattr(x, 'total_seconds') and not pd.isna(x) else None),
        'pout': ('PitOutTime', lambda x: x.total_seconds() if hasattr(x, 'total_seconds') and not pd.isna(x) else None),
        'del': ("Deleted", lambda x: x),
        "delR": ("DeletedReason", lambda x: x),
        "iacc": ("IsAccurate", lambda x: x),
        "ff1G": ("FastF1Generated", lambda x: x)
    }

    def get_fastf1_fallback(ti_key, lap_number):
        """Fetch value from fastf1 session if tracinginsights data is missing"""
        if not fastf1_session or not driver_abbr:
            return None
        try:
            # Get the driver's laps from fastf1 session
            driver_laps = fastf1_session.laps.pick_drivers(driver_abbr)
            if driver_laps.empty:
                return None
            
            # Find the lap with matching lap number
            lap_row = driver_laps[driver_laps['LapNumber'] == lap_number]
            if lap_row.empty:
                return None
            
            # Get the fastf1 field name and conversion function
            if ti_key not in tracinginsightskeysmappedtofastf1:
                return None
            
            ff1_field, converter = tracinginsightskeysmappedtofastf1[ti_key]
            value = lap_row[ff1_field].iloc[0]
            
            # Check for NaN/NaT values
            if pd.isna(value):
                return None
            
            # Apply conversion function (timedelta to seconds, etc.)
            converted = converter(value)
            return converted
        except Exception:
            return None

    def get_value(key, index):
        values = data.get(key)
        if not values:
            # Try fallback to fastf1 if available
            if fastf1_session and driver_abbr and key in tracinginsightskeysmappedtofastf1:
                try:
                    lap_num = int(data['lap'][index]) 
                    if lap_num:
                        fallback_value = get_fastf1_fallback(key, lap_num)
                        if fallback_value is not None:
                            return fallback_value
                except KeyError:
                    fastf1_lap = fastf1_session.laps.pick_drivers(driver_abbr).pick_laps(lap_num)
                    if not fastf1_lap.empty:
                        ff1_field, converter = tracinginsightskeysmappedtofastf1[key]
                        value = fastf1_lap[ff1_field].iloc[0]
                        if pd.isna(value):
                            return None
                        return converter(value)
            return None
        value = values[index]
        if value in ('None', 'nan', 'NaN', 'UNKNOWN', 'TEST_UNKNOWN'):
            return None
        return value

    def get_float_value(key, index):
        value = get_value(key, index)
        if value is None:
            return None
        return float(Decimal(str(value)).quantize(Decimal('0.001')))

    def get_int_value(key, index):
        value = get_value(key, index)
        if value is None:
            return None
        return int(value)

    def get_session_time_text(key, index):
        value = get_float_value(key, index)
        return tts_to_normal(value) if value is not None else None
    
    def get_status(index):
        value = get_value('status', index)
        if not value:
            return None
        return value


    laps = []
    for index in range(len(data['lap'])):
        lap = {}
        lap['timeinseconds'] = get_float_value('time', index)
        lap['time_in_seconds'] = lap['timeinseconds']
        lap['time'] = tts_to_normal(lap['timeinseconds']) if lap['timeinseconds'] is not None else None
        lap['compound'] = get_value('compound', index)
        lap['lap'] = int(data['lap'][index]) 
        lap['stint'] = get_int_value('stint', index)
        lap['s1'] = get_float_value('s1', index)
        lap['s2'] = get_float_value('s2', index)
        lap['s3'] = get_float_value('s3', index)
        lap['life'] = get_int_value('life', index)
        lap['position'] = get_int_value('pos', index)
        lap['status'] = []
        for statuschar in get_status(index) or []:
            if statuschar == '1':
                lap['status'].append('Green Flag')
            elif statuschar == '2':
                lap['status'].append('Yellow Flag')
            elif statuschar == '4':
                lap['status'].append('Safety Car')
            elif statuschar == '5':
                lap['status'].append('Red Flag')
            elif statuschar == '6':
                lap['status'].append('Virtual Safety Car')
            elif statuschar == '7':
                lap['status'].append('Virtual Safety Car Ending')
        lap['status'] = json.dumps(lap['status'])
        lap['qs'] = data.get('qs')[index] if data.get('qs') else None
        lap['sessionlapstarttime'] = get_session_time_text('lST', index)
        lap['sessionlapstarttimeinseconds'] = get_float_value('lST', index)
        lap['sessionlapstarttimeindatetimeutc'] = get_value('lSD', index)
        lap['sessionlapendtime'] = get_session_time_text('sesT', index)
        lap['sessionlapendtimeinseconds'] = get_float_value('sesT', index)
        lap['sector1endtime'] = get_session_time_text('s1T', index)
        lap['sector1endtimeinseconds'] = get_float_value('s1T', index)
        lap['sector2endtime'] = get_session_time_text('s2T', index)
        lap['sector2endtimeinseconds'] = get_float_value('s2T', index)
        lap['sector3endtime'] = get_session_time_text('s3T', index)
        lap['sector3endtimeinseconds'] = get_float_value('s3T', index)
        lap['speedintermediate1'] = get_int_value('vi1', index)
        lap['speedintermediate2'] = get_int_value('vi2', index)
        lap['speedfinishline'] = get_int_value('vfl', index)
        lap['speedspeedtrap'] = get_int_value('vst', index)
        lap['pitinsessiontime'] = get_session_time_text('pin', index)
        lap['pitinsessiontimeinseconds'] = get_float_value('pin', index)
        lap['pitoutsessiontime'] = get_session_time_text('pout', index)
        lap['pitoutsessiontimeinseconds'] = get_float_value('pout', index)
        lap['ifdeletedlap'] = get_value('del', index)
        lap['deletereason'] = get_value('delR', index)
        lap['ifaccurate'] = get_value('iacc', index)
        lap['fastf1generated'] = get_value('ff1G', index)
        laps.append(lap)
    return laps


def insert_lap_by_lap_row(cur, gp, driver_name, session_name, session_id, lap_data, grandprix_id, driver_id, qualifying_segment=None):
    def pick(*keys):
        for key in keys:
            if key in lap_data:
                return lap_data.get(key)
        return None

    cur.execute("""
    INSERT INTO LapByLap (
        GrandPrix, Driver, Position, Lap, Session, Time, TimeInSeconds, TyreCompound, StintNumber,
        Sector1Time, Sector2Time, Sector3Time, TyreAge, TrackStatus, QualifyingSegment,
        SessionLapStartTime, SessionLapStartTimeInSeconds, SessionLapStartTimeDateTimeUTC,
        SessionLapEndTime, SessionLapEndTimeInSeconds,
        Sector1EndSessionTime, Sector1EndSessionTimeInSeconds,
        Sector2EndSessionTime, Sector2EndSessionTimeInSeconds,
        Sector3EndSessionTime, Sector3EndSessionTimeInSeconds,
        SpeedIntermediate1, SpeedIntermediate2, SpeedFinishLine, SpeedSpeedTrap,
        PitInSessionTime, PitInSessionTimeInSeconds, PitOutSessionTime, PitOutSessionTimeInSeconds,
        IfDeletedLap, DeleteReason, IfAccurate, FastF1Generated,
        SessionID, GrandPrixID, DriverID
    )
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        gp,
        driver_name,
        pick('position'),
        pick('lap'),
        session_name,
        pick('time'),
        pick('time_in_seconds', 'timeinseconds'),
        pick('compound'),
        pick('stint'),
        pick('s1'),
        pick('s2'),
        pick('s3'),
        pick('life'),
        pick('status'),
        qualifying_segment if qualifying_segment is not None else pick('qs'),
        pick('sessionlapstarttime', 'sessionlapstartttime'),
        pick('sessionlapstarttimeinseconds', 'sessionlapstartttimeinseconds'),
        pick('sessionlapstarttimeindatetimeutc'),
        pick('sessionlapendtime'),
        pick('sessionlapendtimeinseconds'),
        pick('sector1endtime'),
        pick('sector1endtimeinseconds'),
        pick('sector2endtime'),
        pick('sector2endtimeinseconds'),
        pick('sector3endtime'),
        pick('sector3endtimeinseconds'),
        pick('speedintermediate1'),
        pick('speedintermediate2'),
        pick('speedfinishline'),
        pick('speedspeedtrap'),
        pick('pitinsessiontime'),
        pick('pitinsessiontimeinseconds'),
        pick('pitoutsessiontime'),
        pick('pitoutsessiontimeinseconds'),
        pick('ifdeletedlap'),
        pick('deletereason'),
        pick('ifaccurate'),
        pick('fastf1generated'),
        session_id,
        grandprix_id,
        driver_id
    ))

def parse_lap_by_lap(linkhref, entrants, dataid=None, dataidrace=None, year=None, grandprix_name=None, fastf1_session=None):
    open_url(linkhref)
    output = []
    global name_map
    # Find the lap-by-lap table (using class name, could refine further if needed)
    table = soup.find("table", class_="GPtpt")
    if table.text.strip() != "":
        # Step 1: Extract raw abbreviation → {raw name, number}
        raw_driver_map = {}
        header_cells = table.find("thead").find_all("td")[1:]  # skip first column
        for cell in header_cells:
            a_tag = cell.find("a")
            if a_tag and "title" in a_tag.attrs:
                abbrev = a_tag.text.strip()
                raw_name = a_tag["title"].strip()
                contents = list(cell.stripped_strings)
                number = int(contents[-1]) if contents[-1].isdigit() else None
                raw_driver_map[abbrev] = {"raw_name": raw_name, "number": number}

        # Step 2: Resolve to actual full names from entrants
        resolved_driver_map = {}
        for abbr, info in raw_driver_map.items():
            parts = info["raw_name"].lower().replace('.', ' ').split()
            if parts:
                first_initial = parts[0][0]
                last_name = parts[-1]
                for entrant in entrants:
                    full = normalize_name(entrant["driver"])
                    first_initial = normalize_name(first_initial)
                    last_name = normalize_name(last_name)
                    if full.startswith(first_initial) and full.endswith(last_name):
                        resolved_driver_map[abbr] = {
                            "driver": entrant["driver"],
                            "number": info["number"]
                        }
                        break
            
            if abbr not in resolved_driver_map:
                raise ValueError(f"Could not resolve driver abbreviation '{abbr}' to a full name in entrants.")
                resolved_driver_map[abbr] = {
                    "driver": info["raw_name"],
                    "number": info["number"]
                }
            drivers_in_session = {
                info["driver"] for info in resolved_driver_map.values()
            }

        # Step 3: Parse lap data
        lap_rows = table.find("tbody").find_all("tr", class_="lap")
        for row in lap_rows:
            lap_td = row.find("td", class_="numlap")
            lap_number = int(lap_td.text.strip())
            # Check for safety car
            safetycar = "sc" in lap_td.get("class", [])
            if year > 2015 and safetycar:
                track_status = ["Safety Car"]
            elif 2015 <= year <= 2017 and safetycar:
                track_status = ["Safety Car", "Virtual Safety Car"]  # no differentiaton in this period
            position_cells = row.find_all("td")[1:]  # Skip lap number
            for position, cell in enumerate(position_cells, start=1):
                code = cell.text.strip()
                if code in resolved_driver_map:
                    output.append({
                        "position": position,
                        "driver": resolved_driver_map[code]["driver"],
                        "number": resolved_driver_map[code]["number"],
                        "lap": lap_number,
                        "type": "grandprix" if linkhref.endswith('/tour-par-tour.aspx') else "sprint",
                    })
    else:
        if gp.endswith('Indianapolis 500'):
            print("Lap by lap data not found")
            resolved_driver_map = {}
        else:
            raise ValueError("Lap by lap data not found")

    if dataid is not None:
        # https://pitwall.app/analysis/compare-lap-times?season=76&race=1148&main_driver=541&compare_driver=614&button=
        open_url(f"https://pitwall.app/analysis/compare-lap-times?season={dataid}&race={dataidrace}")
        lxc = soup.find_all('span', {'input-id': 'main_driver'})
        comparecounter = 1
        constructedurl = f"https://pitwall.app/analysis/compare-lap-times?season={dataid}&race={dataidrace}"
        if len(lxc) % 2 == 1:
            lxc.append(lxc[0])
        for x in lxc:
            if comparecounter == 1:
                constructedurl += f"&main_driver={x['data-id']}"
                comparecounter += 1
            elif comparecounter == 2:
                open_url(constructedurl + f"&compare_driver={x['data-id']}")
                comparecounter = 1
                constructedurl = f"https://pitwall.app/analysis/compare-lap-times?season={dataid}&race={dataidrace}"
                laps = soup.find_all('div', class_='lap')
                cacheddrivers = []
                for lap in laps:
                    findlapnumber = int(lap.find('div', class_='lap-number').text.strip().replace('Lap ', ''))

                    maindriver = lap.find('div', class_='main-driver')
                    if maindriver.find('div', class_ = 'time').text.strip() != '':
                        driverposition = int(re.sub(r'\D', '', maindriver.find('span', class_='label').text.strip()))
                        for entrant in output:
                            if entrant['position'] == driverposition and entrant['lap'] == findlapnumber:
                                entrant['time'] = maindriver.find('div', class_='time').text.strip()
                                entrant['time_in_seconds'] = tts(maindriver.find('div', class_='time').text.strip())
                                break
                    comparedriver = lap.find('div', class_='compare-driver')
                    if comparedriver.find('div', class_ = 'time').text.strip() != '':
                        driverposition = int(re.sub(r'\D', '', comparedriver.find('span', class_='label').text.strip()))
                        for entrant in output:
                            if entrant['position'] == driverposition and entrant['lap'] == findlapnumber:
                                entrant['time'] = comparedriver.find('div', class_='time').text.strip()
                                entrant['time_in_seconds'] = tts(comparedriver.find('div', class_='time').text.strip())
                                break   
    lap_index = {}
    for e in output:
        lap_index[(e["driver"], e["lap"])] = e

    if year is not None and grandprix_name is not None:
        retries = 0
        if int(year) >= 2018:
            while retries < 3:
                try:
                    year_int = int(year)
                    if year_int >= 2018:
                        # Load fastf1 session for fallback data if not provided
                        ff1_session = fastf1_session
                        if not ff1_session:
                            try:
                                session_type = "Race" if linkhref.endswith('/tour-par-tour.aspx') else "Sprint"
                                # Extract circuit name from grandprix_name
                                gp_name = grandprix_name
                                if gp_name == "Mexican Grand Prix":
                                    gp_name = "Mexico City Grand Prix" if year_int >= 2021 else "Mexico"
                                elif gp_name == "Barcelona-Catalunya Grand Prix" and year_int >= 2026:
                                    gp_name = "Barcelona Grand Prix"
                                circuit_name = gp_name.replace(" Grand Prix", "").strip()
                                
                                # Map session type to fastf1 session name
                                if session_type == "Race":
                                    ff1_session = fastf1.get_session(year_int, circuit_name, 'R')
                                else:
                                    ff1_session = fastf1.get_session(year_int, circuit_name, 'S')
                                ff1_session.load()
                            except Exception as e:
                                print(f"Warning: Could not load fastf1 session for fallback: {e}")
                                ff1_session = None
                        
                        # Try to get data from TracingInsights
                        #https://cdn.jsdelivr.net/gh/TracingInsights/2020/Sakhir%20Grand%20Prix/Qualifying/VER/laptimes.json"
                        session = "Race" if linkhref.endswith('/tour-par-tour.aspx') else "Sprint"
                        session_ = session
                        if year == 2021 and session == "Sprint":
                            session_ = "Sprint%20Qualifying"
                        #for each driver, get abbr and insert it and get times:
                        for entrant in entrants:
                            driver_fullname = entrant['driver']
                            driver_abbr = None
                            # skip drivers not in this race/sprint
                            if driver_fullname not in drivers_in_session:
                                continue  
                            #skip drivers who did not start:
                            if grandprix_name == "Mexican Grand Prix" and year >= 2020:
                                grandprix_name = "Mexico City Grand Prix"  
                            elif grandprix_name == "Barcelona-Catalunya Grand Prix" and year >= 2026:  
                                grandprix_name = "Barcelona Grand Prix"                        
                            driversintracinginsightssession = json.loads(urllib.request.urlopen(f"https://cdn.jsdelivr.net/gh/TracingInsights/{year_int}@main/{urllib.parse.quote(grandprix_name)}/{session_}/drivers.json").read().decode('utf-8'))          
                            for THEABBREVIATION in resolved_driver_map:
                                if normalize_name(resolved_driver_map[THEABBREVIATION]['driver'].lower()) == normalize_name(driver_fullname.lower()):
                                    driver_abbr = THEABBREVIATION
                                    break
                            if driver_abbr is None:
                                raise ValueError(f"Could not find abbreviation for driver '{driver_fullname}' in name_map.")
                            if not any(driver_abbr == driver['driver'] for driver in driversintracinginsightssession['drivers']):
                                print(f"{driver_abbr} not in session {session_}")
                                continue                            
                            ti_url = f"https://cdn.jsdelivr.net/gh/TracingInsights/{year_int}@main/{urllib.parse.quote(grandprix_name)}/{session_}/{driver_abbr}/laptimes.json"
                            ti_laps = scrape_tracinginsights(ti_url, driver_abbr=driver_abbr, fastf1_session=ff1_session)
                            if session == "Race":
                                driver_name = resolved_driver_map[driver_abbr]['driver']

                                for ti_lap in ti_laps:
                                    key = (driver_name, ti_lap["lap"])
                                    entrant_entry = lap_index.get(key)
                                    if not entrant_entry:
                                        continue

                                    entrant_entry["compound"] = ti_lap["compound"]
                                    entrant_entry["stint"] = ti_lap["stint"]
                                    entrant_entry["s1"] = ti_lap["s1"]
                                    entrant_entry["s2"] = ti_lap["s2"]
                                    entrant_entry["s3"] = ti_lap["s3"]
                                    entrant_entry["life"] = ti_lap["life"]
                                    entrant_entry["status"] = ti_lap["status"]
                                    entrant_entry["sessionlapstarttime"] = ti_lap["sessionlapstarttime"]
                                    entrant_entry["sessionlapstarttimeinseconds"] = ti_lap["sessionlapstarttimeinseconds"]
                                    entrant_entry["sessionlapstarttimeindatetimeutc"] = ti_lap["sessionlapstarttimeindatetimeutc"]
                                    entrant_entry["sessionlapendtime"] = ti_lap["sessionlapendtime"]
                                    entrant_entry["sessionlapendtimeinseconds"] = ti_lap["sessionlapendtimeinseconds"]
                                    entrant_entry["sector1endtime"] = ti_lap["sector1endtime"]
                                    entrant_entry["sector1endtimeinseconds"] = ti_lap["sector1endtimeinseconds"]
                                    entrant_entry["sector2endtime"] = ti_lap["sector2endtime"]
                                    entrant_entry["sector2endtimeinseconds"] = ti_lap["sector2endtimeinseconds"]
                                    entrant_entry["sector3endtime"] = ti_lap["sector3endtime"]
                                    entrant_entry["sector3endtimeinseconds"] = ti_lap["sector3endtimeinseconds"]
                                    entrant_entry["speedintermediate1"] = ti_lap["speedintermediate1"]
                                    entrant_entry["speedintermediate2"] = ti_lap["speedintermediate2"]
                                    entrant_entry["speedfinishline"] = ti_lap["speedfinishline"]
                                    entrant_entry["speedspeedtrap"] = ti_lap["speedspeedtrap"]
                                    entrant_entry["pitinsessiontime"] = ti_lap["pitinsessiontime"]
                                    entrant_entry["pitinsessiontimeinseconds"] = ti_lap["pitinsessiontimeinseconds"]
                                    entrant_entry["pitoutsessiontime"] = ti_lap["pitoutsessiontime"]
                                    entrant_entry["pitoutsessiontimeinseconds"] = ti_lap["pitoutsessiontimeinseconds"]
                                    entrant_entry["ifdeletedlap"] = ti_lap["ifdeletedlap"]
                                    entrant_entry["deletereason"] = ti_lap["deletereason"]
                                    entrant_entry["ifaccurate"] = ti_lap["ifaccurate"]
                                    entrant_entry["fastf1generated"] = ti_lap["fastf1generated"]


                                    if entrant_entry.get("time") is None:
                                        entrant_entry["time"] = ti_lap["time"]
                                        entrant_entry["time_in_seconds"] = ti_lap["timeinseconds"]

                            elif session == "Sprint":
                                driver_name = resolved_driver_map[driver_abbr]["driver"]

                                for ti_lap in ti_laps:
                                    key = (driver_name, ti_lap["lap"])
                                    entrant_entry = lap_index.get(key)

                                    if not entrant_entry:
                                        continue

                                    # Common fields
                                    entrant_entry["compound"] = ti_lap.get("compound")
                                    entrant_entry["stint"] = ti_lap.get("stint")
                                    entrant_entry["s1"] = ti_lap.get("s1")
                                    entrant_entry["s2"] = ti_lap.get("s2")
                                    entrant_entry["s3"] = ti_lap.get("s3")
                                    entrant_entry["life"] = ti_lap.get("life")
                                    entrant_entry["status"] = json.dumps(ti_lap.get("status"))
                                    entrant_entry["time"] = ti_lap["time"]
                                    entrant_entry["time_in_seconds"] = ti_lap.get("timeinseconds")
                                    entrant_entry["sessionlapstartttime"] =  ti_lap["sessionlapstarttime"]
                                    entrant_entry["sessionlapstartttimeinseconds"] = ti_lap["sessionlapstarttimeinseconds"]
                                    entrant_entry["sessionlapstarttime"] =  ti_lap["sessionlapstarttime"]
                                    entrant_entry["sessionlapstarttimeinseconds"] = ti_lap["sessionlapstarttimeinseconds"]
                                    entrant_entry["sessionlapstarttimeindatetimeutc"] = ti_lap["sessionlapstarttimeindatetimeutc"]
                                    entrant_entry["sessionlapendtime"] = ti_lap["sessionlapendtime"]
                                    entrant_entry["sessionlapendtimeinseconds"] = ti_lap["sessionlapendtimeinseconds"]
                                    entrant_entry["sector1endtime"] = ti_lap["sector1endtime"]
                                    entrant_entry["sector1endtimeinseconds"] = ti_lap["sector1endtimeinseconds"]
                                    entrant_entry["sector2endtime"] = ti_lap["sector2endtime"]
                                    entrant_entry["sector2endtimeinseconds"] = ti_lap["sector2endtimeinseconds"]
                                    entrant_entry["sector3endtime"] = ti_lap["sector3endtime"]
                                    entrant_entry["sector3endtimeinseconds"] = ti_lap["sector3endtimeinseconds"]
                                    entrant_entry["speedintermediate1"] = ti_lap["speedintermediate1"]
                                    entrant_entry["speedintermediate2"] = ti_lap["speedintermediate2"]
                                    entrant_entry["speedfinishline"] = ti_lap["speedfinishline"]
                                    entrant_entry["speedspeedtrap"] = ti_lap["speedspeedtrap"]
                                    entrant_entry["pitinsessiontime"] = ti_lap["pitinsessiontime"]
                                    entrant_entry["pitinsessiontimeinseconds"] = ti_lap["pitinsessiontimeinseconds"]
                                    entrant_entry["pitoutsessiontime"] = ti_lap["pitoutsessiontime"]
                                    entrant_entry["pitoutsessiontimeinseconds"] = ti_lap["pitoutsessiontimeinseconds"]
                                    entrant_entry["ifdeletedlap"] = ti_lap["ifdeletedlap"]
                                    entrant_entry["deletereason"] = ti_lap["deletereason"]
                                    entrant_entry["ifaccurate"] = ti_lap["ifaccurate"]
                                    entrant_entry["fastf1generated"] = ti_lap["fastf1generated"]                                    
                                
                    break  # Exit retry loop on success               
                except Exception as e:
                    print (f"Error fetching TracingInsights data for {grandprix_name} {year}, retrying... \n Error: {e}")
                    retries += 1
                    time.sleep(2)  
                    if retries == 3:
                        raise ValueError(f"Failed to fetch TracingInsights data for {grandprix_name} {year} after 3 retries.")                           
    
    return output, resolved_driver_map



def parse_in_season_progress(racelink):
    open_url(racelink)
    
    drivers_table = soup.find('div', id='ctl00_CPH_Main_DIV_ChpPilote')
    constructors_table = soup.find('div', id='ctl00_CPH_Main_DIV_ChpConstructeur')

    driversprogress = []
    constructorsprogress = []

    # Parse Drivers
    if drivers_table:
        table = drivers_table.find('table', class_='datatable')
        if table:
            rows = table.find('tbody').find_all('tr')
            for row in rows:
                cells = row.find_all('td')
                if len(cells) == 3:
                    driversprogress.append({
                        "positionatthispoint": int(cells[0].text.strip().replace('.', '')) if cells[0].text.strip().replace('.', '').isdigit() else (driversprogress[-1]["positionatthispoint"] if driversprogress else None),
                        "driver": format_name_from_caps(cells[1].text.strip()),
                        "pointsatthispoint": float(cells[2].text.strip())
                    })

    # Parse Constructors
    if constructors_table:
        table = constructors_table.find('table', class_='datatable')
        if table:
            rows = table.find('tbody').find_all('tr')
            for row in rows:
                cells = row.find_all('td')
                if len(cells) == 3:
                    links = cells[1].find_all('a')
                    constructorsprogress.append({
                        "positionatthispoint": int(cells[0].text.strip().replace('.', '')) if cells[0].text.strip().replace('.', '').isdigit() else (constructorsprogress[-1]["positionatthispoint"] if constructorsprogress else None),
                        "constructor": links[0].text.strip() if len(links) > 0 else "",
                        "engine": links[1].text.strip() if len(links) > 1 else (links[0].text.strip() if len(links) > 0 else ""),
                        "pointsatthispoint": float(cells[2].text.strip())
                    })

    return [driversprogress, constructorsprogress]

#if driver/constructor started previous race, the remaining races are in RaceByRace, and by the class of the element i extract racebyrace from, i can get the remaining sprints and races. then from last place in championship to first: next we get the points system from Seasons, get 1st for races, 1st for sprints (if exists), and 'Fastest Lap', if exists. then we add them up and see if you can go higher, if so, then don't assign position for driver ahead and yourself. if you can't go ahead, and driver behind you can't overtake, you get assigned a position. if driver did not participate last race, come back to them, and then check whether higher/lower from next race.
def parse_championship_results (year, drivermap):
    open_url(f"https://www.statsf1.com/en/{year}.aspx")
    #print (drivermap)
    output = []
    driverschampionship = []
    constructorschampionship = []
    drivers_table = soup.find('table', id = 'ctl00_CPH_Main_TBL_CHP_Drv')
    constructors_table = soup.find('table', id = 'ctl00_CPH_Main_TBL_CHP_Cst')
    driversrows = drivers_table.find_all('tr')[1:]
    headercells = drivers_table.find_all('tr')[0].find_all('td')
    cur.execute("SELECT MAX(GrandPrixID) FROM GrandPrixResults")
    latest_gp_id = cur.fetchone()[0]
    for row in driversrows:
        cells = row.find_all('td')
        if len(cells) < 3 or cells[0].get('colspan'):  # StatsF1's one-cell separator row ('cannot be world champions anymore'); its colspan is races + 3
            continue
        driverindo = {
            'position': int(cells[0].text.strip().replace('.', '')) if cells[0].get_text(strip=True).replace('.', '').isdigit() else driverschampionship[-1]["position"],
            'driver': drivermap[cells[1].text.strip()]
        }
        if headercells[-1].text.strip() == 'Out of' and headercells[-2].text.strip() == 'Pts':
            driverindo['points'] = float(cells[-2].text.strip())
            driverindo['outof'] = float(cells[-1].text.strip()) if cells[-1].text.strip() != '' else driverindo['points']
            seasonprogress = {}
            for i, race in enumerate(cells[2:-2]):
                therace = headercells[1:-2][i].find('span', class_ = 'codegp')['title']
                racepoints = race.text.strip()
                if racepoints == "-":
                    racepoints = (0, None, "gp")
                elif racepoints == "":
                    racepoints = (None, None, "gp") 
                elif racepoints.startswith('(') and racepoints.endswith (')'):
                    racepoints = (float(racepoints.replace('(', '').replace(')', '').replace(',', '.')), True, "gp")       
                else:
                    racepoints = (float(racepoints.replace(',', '.')), False, "gp")                 
                seasonprogress[therace] = racepoints
        elif headercells[-1].text.strip() == 'Pts':
            driverindo['points'] = float(cells[-1].text.strip())
            driverindo['outof'] = None
            seasonprogress = {}
            for i, race in enumerate(cells[2:-1]):
                therace = headercells[1:-1][i].find('span', class_ = ['codegp', 'codesp'])['title']
                racepoints = race.text.strip()
                rtype = "gp" if headercells[1:-1][i].find('span', class_ = ['codegp', 'codesp'])['class'][0] == 'codegp' else "sp"
                if racepoints == "-":
                    racepoints = (0, None, rtype)
                elif racepoints == "":
                    racepoints =( None, None, rtype)
                elif racepoints.startswith('(') and racepoints.endswith (')'):
                    racepoints = (float(racepoints.replace('(', '').replace(')', '').replace(',', '.')), True, rtype)    
                else:
                    racepoints =(float(racepoints.replace(',', '.')), False, rtype)                 
                seasonprogress[therace] = racepoints   
        driverindo['racebyrace'] = seasonprogress     
        driverschampionship.append(driverindo)
    output.append(driverschampionship)
    if constructors_table:
        constructorsrows = constructors_table.find_all('tr')[1:]
        headercells = constructors_table.find_all('tr')[0].find_all('td')  # Get headers from first row
        for row in constructorsrows:
            cells = row.find_all('td')
            if len(cells) < 3 or cells[0].get('colspan'):  # StatsF1's one-cell separator row ('cannot be world champions anymore'); its colspan is races + 3
                continue
            constructorindo = {
                'position': int(cells[0].text.strip().replace('.', '')) if cells[0].get_text(strip=True).replace('.', '').isdigit() else constructorschampionship[-1]["position"],
                'constructor': cells[1].find_all('a')[0].text.strip(),
                'engine': cells[1].find_all('a')[1].text.strip() if len(cells[1].find_all('a')) == 2 else cells[1].find_all('a')[0].text.strip()
            }
            if headercells[-1].text.strip() == 'Out of' and headercells[-2].text.strip() == 'Pts':
                constructorindo['points'] = float(cells[-2].text.strip())
                constructorindo['outof'] = float(cells[-1].text.strip()) if cells[-1].text.strip() != '' else constructorindo['points']
                seasonprogress = {}
                for i, race in enumerate(cells[2:-2]):
                    therace = headercells[1:-2][i].find('span', class_ = 'codegp')['title']
                    racepoints = race.text.strip()
                    if racepoints == "-":
                        racepoints = (0, None, "gp")
                    elif racepoints == "":
                        racepoints =(None, None, "gp")
                    elif racepoints.startswith('(') and racepoints.endswith (')'):
                        racepoints = (float(racepoints.replace('(', '').replace(')', '')), True, "gp")    
                    else:
                        racepoints =(float(racepoints.replace(',', '.')), False, "gp")                
                    seasonprogress[therace] = racepoints
            elif headercells[-1].text.strip() == 'Pts':
                constructorindo['points'] = float(cells[-1].text.strip())
                constructorindo['outof'] = None
                seasonprogress = {}
                for i, race in enumerate(cells[2:-1]):
                    therace = headercells[1:-1][i].find('span', class_ = ['codegp', 'codesp'])['title']
                    racepoints = race.text.strip()
                    rtype = "gp" if headercells[1:-1][i].find('span', class_ = ['codegp', 'codesp'])['class'][0] == 'codegp' else "sp"
                    if racepoints == "-":
                        racepoints = (0, None, rtype)
                    elif racepoints == "":
                        racepoints =( None, None, rtype)
                    elif racepoints.startswith('(') and racepoints.endswith (')'):
                        racepoints = (float(racepoints.replace('(', '').replace(')', '').replace(',', '.')), True, rtype)    
                    else:
                        racepoints =(float(racepoints.replace(',', '.')), False, rtype)                              
                    seasonprogress[therace] = racepoints   
            constructorindo['racebyrace'] = seasonprogress
            constructorschampionship.append(constructorindo)
        output.append(constructorschampionship) 
    else:
        constructorschampionship = []
        output.append(constructorschampionship)       
    return output.copy()

def get_tiebreaker_key(entrant):
    results = []

    for _, data in entrant['racebyrace'].items():
        if data[0] is not None:
            results.append(data[0])

    results.sort(reverse=True)
    return tuple(results)


def apply_mathematical_locks(standings, points_system):
    if not standings:
        return standings

    # 1. Identify Future Races / Weekends
    first_entry = standings[0]
    race_names = list(first_entry['racebyrace'].keys())
    future_gps, future_sprints = 0, 0

    for race in race_names:
        # A race is future only if NO entrant has a result yet
        if all(thing['racebyrace'][race][0] is None for thing in standings):
            if race == "Indianapolis":
                continue 
            rtype = first_entry['racebyrace'][race][2]
            if rtype == 'gp':
                future_gps += 1

            elif rtype == 'sp':
                # Sprint weekend still has a Grand Prix
                future_gps += 1
                future_sprints += 1

    # Season finished → standings already final
    if future_gps == 0 and future_sprints == 0:
        return standings

    # 2. Maximum Remaining Points (Constructor-aware)
    gp_scores = points_system.get('grandprix', {})
    gp_positions = sorted(
        [v for k, v in gp_scores.items() if k.isdigit()],
        reverse=True
    )

    max_gp = (
        gp_positions[0] +
        (gp_positions[1] if len(gp_positions) > 1 else 0) +
        gp_scores.get('Fastest Lap', 0)
    )

    sp_scores = points_system.get('sprint', {})
    sp_positions = sorted(
        [v for k, v in sp_scores.items() if k.isdigit()],
        reverse=True
    )

    max_sp = (
        sp_positions[0] +
        (sp_positions[1] if len(sp_positions) > 1 else 0) +
        sp_scores.get('Fastest Lap', 0)
    )

    total_remaining = (future_gps * max_gp) + (future_sprints * max_sp)

    # 3. Apply Mathematical Locks
    n = len(standings)

    for i in range(n):
        me = standings[i]
        locked = True  # assume locked unless proven otherwise

        # Can I catch the entry ahead?
        if i > 0:
            ahead = standings[i - 1]
            my_max = me['points'] + total_remaining

            if my_max > ahead['points']:
                locked = False
            elif my_max == ahead['points']:
                if get_tiebreaker_key(me) > get_tiebreaker_key(ahead):
                    locked = False

        # Can someone behind catch me?
        if i < n - 1:
            behind = standings[i + 1]
            if behind['points'] + total_remaining >= me['points']:
                locked = False

        if not locked:
            me['position'] = None
    return standings


def parsenotes (links):
    notes = {}
    for link in links:
        if link['href'].startswith('https://motorsportstats.com'):
            continue
        if not link['href'].startswith('/en/results/'):            
            open_url(f"https://www.statsf1.com{link['href']}")
            notesdiv = soup.find('div', id='ctl00_CPH_Main_P_Commentaire')
            if link['href'].endswith('engages.aspx'):
                notes["EntrantsNotes"] = notesdiv.text.strip() if notesdiv else ''
            elif link['href'].endswith('qualification.aspx'):
                notes["QualifyingNotes"] = notesdiv.text.strip() if notesdiv else ''
            elif link['href'].endswith('grille.aspx'):
                notes["StartingGridNotes"] = notesdiv.text.strip() if notesdiv else ''
            elif link['href'].endswith('classement.aspx'):
                notes["RaceResultNotes"] = notesdiv.text.strip() if notesdiv else ''
            elif link['href'].endswith('sprint.aspx?grille'):
                notes["SprintGridNotes"] = notesdiv.text.strip() if notesdiv else ''
            elif link['href'].endswith('sprint.aspx'):
                notes["SprintNotes"] = notesdiv.text.strip() if notesdiv else ''
    return notes.copy()

if "--updateracereport" in sys.argv:
    updaterace_index = sys.argv.index("--updateracereport")
    updaterace_target = " ".join(sys.argv[updaterace_index + 1:]).strip() or None
    update_race_reports(updaterace_target)
    print("Closing database connection...")
    conn.close()
    raise SystemExit(0)


months = {
    'Jan': 1,
    'Feb': 2,
    'Mar': 3,
    'Apr': 4,
    'May': 5,
    'Jun': 6,
    'Jul': 7,
    'Aug': 8,
    'Sep': 9,
    'Oct': 10,
    'Nov': 11,
    'Dec': 12
}

# Check if there are no races in the database
#Also, check if there are are no circuit layouts in the CircuitLayouts table. 
# If there are, then we can skip the circuit scraping and just do the mapping of grand prix to circuit layout id. 
# If there are no circuit layouts, then we need to do the full scrape of circuits and circuit layouts.

cur.execute("SELECT COUNT(*) FROM CircuitLayouts")
if cur.fetchone()[0] == 0:
    cur.execute("SELECT COUNT(*) FROM GrandsPrix")
    mappings = {}
    if cur.fetchone()[0] == 0:
        open_url("https://www.statsf1.com/en/circuits.aspx")
        table = soup.find('table')
        trs = table.find_all('tr')
        for tr in trs[1:-1]:
            p = tr.find_all('td')[0]
            v = p.find('a')
            print ("Processing circuit: ", v.get_text(strip=True))
            twd = v['href']
            open_url(f'https://www.statsf1.com/{twd}')
            a_tag = soup.find('a', id='ctl00_CPH_Main_HL_GMaps')['href']
            coord_str = re.search(r'@([^,]+),([^,]+)', a_tag).groups()
            lat, lng = coord_str
            lat = parse_coordinate(lat)
            lng = parse_coordinate(lng)
            country = get_country_from_coords(lat, lng)
            elevation = get_elevation_from_coords(lat, lng)
            circuitlayoutdivs = soup.find_all('div', class_ = 'circuitversion')
            for layoutdiv in circuitlayoutdivs:
                circuittable = layoutdiv.find('table', class_ = 'sortable circuittable').find_all('tr')
                dates = [tr.find_all('td')[0]['sorttable_customkey'] for tr in circuittable[1:-1]]                
                version = circuitlayoutdivs.index(layoutdiv) + 1
                layoutimg = layoutdiv.find('img')['src']
                circuit_text_div = layoutdiv.find('div', class_='circuitversiontxt')
                circuit_text = circuit_text_div.get_text(strip=True).replace('\n', '').replace('"', '').replace('\r', '')            
                circuitlayoutid, _ = save_circuit_layout_version(
                    cur, lat, lng, elevation, country, dates, version,
                    f'https://www.statsf1.com{layoutimg}', circuit_text,
                )
                
else:
    cur.execute("SELECT COUNT(*) FROM GrandsPrix")
    #if there are less than 1149 grands prix in the database, this will suffice.
    count = cur.fetchone()[0]
    if count < 1163:
        #Madrid is race 1163
        mappings = {'Adelaide': ('-34.9269993', '138.6172609'), 'Aida': ('34.9152924', '134.2207281'), 'Ain-Diab': ('33.5805298', '-7.68909'), 'Aintree': ('53.4759049', '-2.9413726'), 'Anderstorp': ('57.2639056', '13.6030116'), 'Austin': ('30.1336367', '-97.6345729'), 'AVUS': ('52.48658', '13.26549'), 'Baku': ('40.3712591', '49.8476935'), 'Barcelona': ('41.5682768', '2.2582064'), 'Brands Hatch': ('51.3555433', '0.2629122'), 'Bremgarten': ('46.9581878', '7.402719'), 'Buenos Aires': ('-34.6922845', '-58.4550501'), 'Caesars Palace': ('36.119148', '-115.1771724'), 'Clermont-Ferrand': ('45.7456087', '3.0402848'), 'Dallas': ('32.7767709', '-96.7593567'), 'Detroit': ('42.3280081', '-83.0405242'), 'Dijon-Prenois': ('47.3619752', '4.8995105'), 'Donington': ('52.8296656', '-1.3749465'), 'East London': ('-33.0491788', '27.8745906'), 'Estoril': ('38.7491677', '-9.394149'), 'Fuji': ('35.3689657', '138.9281865'), 'Hockenheim': ('49.3308527', '8.5790215'), 'Hungaroring': ('47.5817579', '19.2507484'), 'Imola': ('44.3398577', '11.7133288'), 'Indianapolis': ('39.7955151', '-86.2362663,4080a,20y'), 'Interlagos': ('-23.7032926', '-46.696961'), 'Istanbul': ('40.9552661', '29.4096963'), 'Jacarepagua': ('-22.9779874', '-43.3958898'), 'Jarama': ('40.6147065', '-3.5860519'), 'Jeddah': ('21.6380938', '39.0994433'), 'Jerez de la Frontera': ('36.7075431', '-6.0326723'), 'Kuala Lumpur': ('2.7597106', '101.7371379'), 'Kyalami': ('-25.9976867', '28.067626'), 'Las Vegas': ('36.1170295', '-115.1649455'), 'Le Castellet': ('43.2511291', '5.7894971'), 'Le Mans': ('47.951629', '0.2101078'), 'Long Beach': ('33.7640562', '-118.1887551'), 'Lusail': ('25.4904378', '51.4538568'), 'Magny-Cours': ('46.8619335', '3.1655065'), 'Melbourne': ('-37.8491401', '144.9698726'), 'Mexico City': ('19.4002574', '-99.0902792'), 'Miami': ('25.9577398', '-80.238678'), 'Monaco': ('43.7380948', '7.4260503'), 'Monsanto': ('38.7165398', '-9.2007324'), 'Mont-Tremblant': ('46.1865295', '-74.6096944'), 'Montjuïc Park': ('41.3680722', '2.1517021'), 'Montreal': ('45.50589', '-73.52411'), 'Monza': ('45.6184477', '9.2876756'), 'Mosport Park': ('44.0468166', '-78.6744209'), 'Mugello': ('43.9971945', '11.3722542'), 'New Delhi': ('28.3473368', '77.5337797'), 'Nivelles': ('50.6197164', '4.3277699'), 'Nürburgring': ('50.3292676', '6.942535'), 'Österreichring': ('47.22244', '14.7595'), 'Pedralbes': ('41.3881241', '2.1173237'), 'Pescara': ('42.4773755', '14.1548578'), 'Phoenix': ('33.44701', '-112.07681'), 'Portimão': ('37.2288843', '-8.627678'), 'Porto': ('41.1685927', '-8.6786295'), 'Reims': ('49.2583768', '3.9230837'), 'Riverside': ('33.93438', '-117.27276'), 'Rouen-les-Essarts': ('49.3333869', '1.0022022'), 'Sakhir': ('26.0303897', '50.513543'), 'Sebring': ('27.4548527', '-81.3513999'), 'Shanghai': ('31.341322', '121.21911'), 'Silverstone': ('52.07159', '-1.01615'), 'Singapore': ('1.2875346', '103.8554768'), 'Sochi': ('43.4067237', '39.9569962'), 'Spa-Francorchamps': ('50.4386283', '5.9666064'), 'Spielberg': ('47.2235862', '14.7555404'), 'Suzuka': ('34.8431556', '136.5266977'), 'Valencia': ('39.458529', '-0.330191'), 'Watkins Glen': ('42.336931', '-76.924553'), 'Yas Marina': ('24.4717208', '54.6018358'), 'Yeongam': ('34.7372903', '126.4123532'), 'Zandvoort': ('52.3878911', '4.5430609'), 'Zeltweg': ('47.200012', '14.741163'), 'Zolder': ('50.9900265', '5.254855')}    
    else:
        # Do not scrape the complete circuits index during an update. The race
        # loop below fetches the individual circuit page when it needs one.
        mappings = {}

    
conn.commit()   

def link_circuitlayout(lat, lng, date):
    yyyymmdd = date.strftime('%Y%m%d')
    cur.execute(
        "SELECT ID FROM CircuitLayouts "
        "WHERE Latitude = ? AND Longitude = ? AND GrandPrixDates LIKE ?",
        (lat, lng, f'%{yyyymmdd}%')
    )
    circuitlayoutids = cur.fetchall()
    return circuitlayoutids[0][0] if circuitlayoutids else None


cur.execute("SELECT Season FROM Seasons ORDER BY season DESC LIMIT 1")
row = cur.fetchone()
last_season = row[0] if row else 1950
index = range(1950, last_season + 1).index(last_season)  

executed = False
open_url("https://www.statsf1.com/en/saisons.aspx")
divs = soup.find('div', class_='saison')
soup = BeautifulSoup(str(divs), 'html.parser')
seasons = soup.find_all('a')
for season in seasons[index:]: 
    driverids = {}
    teamids = {}
    constructorids = {}
    chassisids = {}
    engineids = {}
    enginemodelids = {}
    tyreids = {} 
    nationalityids = {}    
    dataid = None
    name_map = {}   
    year = int(season['href'][4:8])  
    if year > datetime.date.today().year:
        continue
    #req = urllib.request.Request(f"https://gpracingstats.com/seasons/{year}-world-championship/", headers=headersfr)
    #html = urllib.request.urlopen(req).read() 
    #soup = BeautifulSoup(html, 'html.parser')
    open_url(f"https://pitwall.app/races/archive/{year}")
    tags = soup.find('table', class_= 'data-table').find('tbody').find_all('tr')  # find all links
    #now we open motorsportstats.com's data.  
    mslinks = [t['name'].replace('  ', ' ') for t in open_json(f"https://motorsportstats.com/_next/data/c2LGT9ym-c6f1pXlf9hjs/series/fia-formula-one-world-championship/calendar/{year}.json")['pageProps']['calendar']['events'] if "season test" not in t['name'].lower() and t.get('status', '').lower() != 'cancelled'] 
    #print (tags)
    gps = []
    thelist = []
    for tag in tags:
        cells = tag.find_all('td')
        if datetime.date.today() < datetime.date(year, months[cells[0].text.strip().split()[0]], int(cells[0].text.strip().split()[1])):
           break
        else:
            currentgrandprix = cells[1].find('a').text.strip()
            if year > 2020:
                currentgrandprix = currentgrandprix.replace("Mexican Grand Prix", "Mexico City Grand Prix")
            gps.append(currentgrandprix)
            thedate = cells[0].text.strip()
            dateindatetime = datetime.date(year, months[cells[0].text.strip().split()[0]], int(cells[0].text.strip().split()[1]))
            thecircuit = cells[2].text.strip()
            thelist.append((thedate, dateindatetime, currentgrandprix, thecircuit))
    last_grandprix = None
    if not executed:
        cur.execute("SELECT GrandPrixName, ID FROM GrandsPrix ORDER BY ID DESC LIMIT 1")
        xsqqwfejk = cur.fetchone()
        last_grandprix = xsqqwfejk[0] if xsqqwfejk else None
        last_grandprix_id = xsqqwfejk[1] if xsqqwfejk else -1
        executed = True
        if last_grandprix == gps[-1]:
            continue
    
    gpindex = gps.index(last_grandprix) + 1 if last_grandprix else 0            
    #year = 1983 #For debugging purposes, we set the year to 1983 🚫✔️
    print(year)
    if year > 1982: #We only scrape from F1.com from 1983 onwards, before that StatsF1 has more data than F1.com, from 1983, F1.com has Q1, Q2 (and drivers Q1, Q2 and Q3 times) and pit stop summary, which StatsF1 does not have
        open_url(f"https://www.formula1.com/en/results/{year}/races")
        #print ("IF EXECUTED")
        f1websiteraces = soup.find_all('a', class_ = 'flex gap-px-10 items-center')
    #Finds the scoring systems for drivers and constructors
    open_url(f"https://www.statsf1.com{season['href']}") 
    save = f"https://www.statsf1.com{season['href']}"
    driverschampionshiptable = soup.find('table', id = 'ctl00_CPH_Main_TBL_CHP_Drv')
    tablerows = driverschampionshiptable.find_all('tr')
    drivernames = []
    for row in tablerows[1:]:
        if len(row.find_all("td")) ==  1:
            continue
        cells = row.find_all("td")
        drivername = cells[1].text.strip()
        # Apply name corrections to drivernames from championship table
        if drivername == "Guanyu Zhou":
            drivername = "Zhou Guanyu"
        drivernames.append(drivername)
    points_system_drivers, points_system_constructors = parse_points_system(str(soup))
    #print(points_system_drivers, points_system_constructors)
    print ("Points System Parsed")            
        #print("Drivers' Points System:", points_system_drivers)
        #print("Constructors' Points System:", points_system_constructors)
    if year > 1995: #we have lap-by-lap data from 1996
        # https://pitwall.app/analysis/compare-lap-times?season=76&race=1148&main_driver=541&compare_driver=614&button=
        open_url("https://pitwall.app/analysis/compare-lap-times")
        seasonsan = soup.find_all("div", class_='items cols cols-4')
        seasonsan = seasonsan[0].find_all('span', class_='item') if seasonsan else []
        for season in seasonsan:
            if int(season.text.strip()) == year:
                dataid = season['data-id']
                break 
    #Finds the regulations (technical) for the season 
    open_url(save)  
    regs = soup.find_all('div', class_ = 'yearinfo')
    for reg in regs:
        regulations = parse_regulations(str(reg)) if reg else None
        #print("Regulations:", regulations)
        print ("Regulations Parsed") 
    cur.execute("""INSERT OR IGNORE INTO Seasons (Season, DriversRacesCounted, PointsSharedForSharedCars, GrandPrixPointsSystemDrivers, SprintPointsSystemDrivers,
                ConstructorsRacesCounted, PointsOnlyForTopScoringCar, GrandPrixPointsSystemConstructors, SprintPointsSystemConstructors,
                RegulationNotes, MinimumWeightofCars, EngineType, Supercharging, MaxCylinderCapacity, NumberOfCylinders, MaxRPM, NumberOfEnginesAllowedPerSeason,
                FuelType, RefuellingAllowed, MaxFuelConsumption) 
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (year, points_system_drivers['scores'], points_system_drivers['pointssharedforsharedcars'], json.dumps(points_system_drivers['grandprix']), json.dumps(points_system_drivers['sprint']),
                points_system_constructors.get('scores'), points_system_constructors.get('topscoring'), json.dumps(points_system_constructors.get('grandprix')), json.dumps(points_system_constructors.get('sprint')),
                json.dumps(regulations.get('notes')), regulations['Weight (min)'], regulations['Engine']['Type'], regulations['Engine']['Supercharging'], regulations['Engine']['Cylinder capacity (max)'], regulations['Engine']['Cylinders'], regulations['Engine']['Rpm (max)'], regulations['Engine']['Number'],
                regulations['Fuel']['Type'], regulations['Fuel']['Refuelling'], regulations['Fuel']['Consumption (max)']))  
    cur.execute("UPDATE Seasons SET needstatsupdate = 1 WHERE Season = ?", (year,))
    print ("Regulations data saved to database")      
    trigger2 = False
    #quit() #For debugging purposes, we quit after the first season  🚫✔️
    #Finds all the grands prix for the season   
    div = soup.find('div', class_ = 'gpaffiche')
    soup = BeautifulSoup(str(div), 'html.parser')
    grandsprix = soup.find_all('a')[:len(gps)] 
    for grandprix in grandsprix[gpindex:]:
        sxs = None
        gp = gps[grandsprix.index(grandprix)]   
        # Convert Mexican Grand Prix to Mexico City Grand Prix from 2021 onwards
        if year >= 2021 and gp.endswith("Mexican Grand Prix"):
            gp = gp.replace("Mexican Grand Prix", "Mexico City Grand Prix")
        print (gp)
        theneeded = thelist[grandsprix.index(grandprix)]
        #print (gps[-1])
        if gp == gps[-1]:
            #print ("happening")
            trigger2 = True         
        #break #To see whether the f1.com data will be executed now 🚫✔️
        open_url(f"https://www.statsf1.com{grandprix['href']}")
        raceinfo = soup.find('div', class_ = 'border-top')
        soup_bypass = soup
        race_info = parse_race_info(str(raceinfo), theneeded)
        if race_info['track_name'] in mappings.keys():
            race_info['latitude'], race_info['longitude'] = mappings[race_info['track_name']]
            race_info['timezone'] = get_timezone_from_coords(race_info['latitude'], race_info['longitude'])
            race_info['country'] = get_country_from_coords(race_info['latitude'], race_info['longitude'])
            #If this is being updated, not reset, and this start off from the last grand prix, then we check if there are new circuit layouts for the circuit since the last grand prix, and if there are, we add them to the database.
            cur.execute(
                "SELECT 1 FROM CircuitLayouts "
                "WHERE Latitude = ? AND Longitude = ? "
                "AND GrandPrixDates LIKE ? LIMIT 1",
                (race_info['latitude'], race_info['longitude'], f"%{race_info['dateindatetime'].strftime('%Y%m%d')}%"),
            )
            layout_exists_for_race = cur.fetchone() is not None
            if (race_info['race_number'] > last_grandprix_id 
                    and not layout_exists_for_race):
                open_url(f"https://www.statsf1.com/en/circuit-{race_info['track_name'].replace(' ', '-').lower()}.aspx")  
                official_circuit_name, track_type = parse_circuit_metadata(soup, fallback_name=race_info['track_name'])
                lat, lng = mappings[race_info['track_name']]
                lat = parse_coordinate(lat)
                lng = parse_coordinate(lng)
                mappings[race_info['track_name']] = (lat, lng)
                race_info['latitude'], race_info['longitude'] = lat, lng
                race_info['country'] = country
                elevation = get_elevation_from_coords(lat, lng)
                circuitlayoutdivs = soup.find_all('div', class_ = 'circuitversion')
                print(f"Processing circuit: {race_info['track_name']} and checking for new layout versions.")
                for layoutdiv in circuitlayoutdivs:
                    circuittable = layoutdiv.find('table', class_ = 'sortable circuittable').find_all('tr')
                    dates = [tr.find_all('td')[0]['sorttable_customkey'] for tr in circuittable[1:-1]]
                    version = circuitlayoutdivs.index(layoutdiv) + 1
                    layoutimg = layoutdiv.find('img')['src']
                    circuit_text_div = soup.find('div', class_='circuittext')
                    circuit_text = circuit_text_div.get_text(strip=True).replace('\n', '').replace('"', '').replace('\r', '')                     
                    circuitlayoutid, _ = save_circuit_layout_version(
                        cur, lat, lng, elevation, country, dates, version,
                        f'https://www.statsf1.com{layoutimg}', circuit_text,
                        official_circuit_name, track_type,
                    )
        else:
            open_url(f"https://www.statsf1.com/en/circuit-{race_info['track_name'].replace(' ', '-').lower()}.aspx")
            official_circuit_name, track_type = parse_circuit_metadata(soup, fallback_name=race_info['track_name'])
            a_tag = soup.find('a', id='ctl00_CPH_Main_HL_GMaps')['href']
            coord_str = a_tag[a_tag.index('@') + 1 : a_tag.rindex(',')]
            lat = coord_str[:coord_str.index(',')]
            lng = coord_str[coord_str.index(',') + 1:]  
            lat = parse_coordinate(lat)
            lng = parse_coordinate(lng)
            mappings[race_info['track_name']] = (lat, lng)
            race_info['latitude'], race_info['longitude'] = lat, lng
            race_info['timezone'] = get_timezone_from_coords(lat, lng)
            country = get_country_from_coords(lat, lng)
            race_info['country'] = country
            elevation = get_elevation_from_coords(lat, lng)
            print(f"Processing circuit: {race_info['track_name']}")
            circuitlayoutdivs = soup.find_all('div', class_ = 'circuitversion')
            for layoutdiv in circuitlayoutdivs:
                circuittable = layoutdiv.find('table', class_ = 'sortable circuittable').find_all('tr')
                dates = [tr.find_all('td')[0]['sorttable_customkey'] for tr in circuittable[1:-1]]
                version = circuitlayoutdivs.index(layoutdiv) + 1
                layoutimg = layoutdiv.find('img')['src']
                circuit_text_div = soup.find('div', class_='circuittext')
                circuit_text = circuit_text_div.get_text(strip=True).replace('\n', '').replace('"', '').replace('\r', '')                
                circuitlayoutid, _ = save_circuit_layout_version(
                    cur, lat, lng, elevation, country, dates, version,
                    f'https://www.statsf1.com{layoutimg}', circuit_text,
                    official_circuit_name, track_type,
                )
        #print(race_info)
        print ("Race Info Parsed")
        #print("Race Info:", race_info)        
        ##fi.write(raceinfo.prettify())
        #Finds all the links for the grand prix: race entrants, results, qualifying, fastest laps, lap by lap, etc.
        divs = soup_bypass.find('div', class_ = 'GPlink')
        soup = BeautifulSoup(str(divs), 'html.parser')
        grandprixlinks = soup.find_all('a')
        ##DONT GIVE JUST THE HREF, GIVE THE WHOLE A TAG
        trigger = False
        if any(item['href'].endswith("/sprint.aspx") for item in grandprixlinks):
            grandprixlinks.append({'href': f"{grandprix['href'].replace('.aspx', '')}/sprint.aspx?grille"})
            grandprixlinks.append({'href': f"{grandprix['href'].replace('.aspx', '')}/sprint.aspx?mt"})
            trigger = True
        for item in grandprixlinks:
            if item['href'].endswith("/grille.aspx"):
                open_url(f"https://www.statsf1.com{item['href']}")
                poleside, gridformation = parse_statsf1_grid(soup, [], prefix="", return_metadata=True)
                break
        cur.execute("INSERT OR IGNORE INTO Circuits (CircuitName, Latitude, Longitude, FirstGrandPrix, FirstGrandPrixID) VALUES (?,?,?,?,?)", (race_info['circuit_name'], race_info['latitude'], race_info['longitude'], gp, race_info['race_number']))
        cur.execute("SELECT ID FROM Circuits WHERE CircuitName = ?", (race_info['circuit_name'],))
        circuitid = cur.fetchone()[0]
        corresponding_circuit_layout = link_circuitlayout(race_info['latitude'], race_info['longitude'], race_info['dateindatetime'])
        cur.execute("""INSERT INTO GrandsPrix (ID, Season, GrandPrixName, RoundNumber, CircuitName, Country, Date, DateInDateTime, Laps, CircuitLength, Offset, Weather, Notes, SprintWeekend, PoleSide, GridFormation, CircuitID, CircuitLayoutID)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, 
        (race_info['race_number'], year, gp, grandsprix.index(grandprix) + 1, race_info['circuit_name'], race_info['country'], race_info['date'], race_info['dateindatetime'], race_info['laps'], race_info['circuit_distance'], race_info['offset'], race_info['weather'], race_info.get('notes'), trigger, poleside, gridformation, circuitid, corresponding_circuit_layout))   
        cur.execute("UPDATE Seasons SET needstatsupdate = 1 WHERE Season = ?", (year,))
        cur.execute('UPDATE Circuits SET LastGrandPrix = ?, LastGrandPrixID = ? WHERE CircuitName = ?', (gp, race_info['race_number'], race_info['circuit_name']))
        #add one to grandprixcount in circuits table
        cur.execute('UPDATE Circuits SET GrandPrixCount = GrandPrixCount + 1 WHERE CircuitName = ?', (race_info['circuit_name'],))
        if corresponding_circuit_layout:
            cur.execute("SELECT FirstGrandPrixID FROM CircuitLayouts WHERE ID = ?", (corresponding_circuit_layout,))
            if cur.fetchone()[0] is None:
                cur.execute("UPDATE CircuitLayouts SET FirstGrandPrix = ?, FirstGrandPrixID = ? WHERE ID = ?", (gp, race_info['race_number'], corresponding_circuit_layout))
            cur.execute("UPDATE CircuitLayouts SET LastGrandPrix = ?, LastGrandPrixID = ?, GrandPrixCount = GrandPrixCount + 1 WHERE ID = ?", (gp, race_info['race_number'], corresponding_circuit_layout))
        print ("Grand Prix info saved to database")    
        cur.execute("INSERT INTO RaceReports (ID, GrandPrixName, RaceReport) VALUES (?,?,?)", (race_info['race_number'], gp, fetch_race_report(gp))) 
        print ("Race Report saved to database")               
        if year > 1982:    
            racii = f1websiteraces[grandsprix.index(grandprix)]
            open_url (f"https://www.formula1.com/{racii['href']}".replace('/../../', ''))
            #theplacewithallthelinks = soup.find_all('div', class_ = 'relative text-nowrap')[3]
            for x in soup.find_all('div', class_ = 'relative text-nowrap'):
                if "Race Result" in x.text:
                    theplacewithallthelinks = x
                    break  
            #print (theplacewithallthelinks)
            soup = BeautifulSoup(str(theplacewithallthelinks), 'html.parser')
            f1links = soup.find_all('a', class_ = 'DropdownMenuItem-module_dropdown-menu-item__6Y3-v typography-module_body-s-semibold__O2lOH')
            for link in f1links:
                if not link['href'].endswith('/pit-stop-summary'):
                    grandprixlinks.append(link)
                else:
                    sxs = link['href'] 
                    #print(sxs) 
        msgrandprix = unicodedata.normalize('NFKD', mslinks[grandsprix.index(grandprix)].replace(' ', '-').lower().replace("'", '-')).encode('ascii', 'ignore').decode('ascii')
        info = open_json(f"https://motorsportstats.com/_next/data/c2LGT9ym-c6f1pXlf9hjs/results/fia-formula-one-world-championship/{year}/{msgrandprix}/info.json")['pageProps']['sessions']
        ix = 1
        dest = []
        lapchartstoberead = []
        flaggingmaxspeeds = []
        sprint_session_slug = None
        for s in info:
            ix += 1
            tde = {
                'name': s['session'].get('shortName') or s['session']['name'],
                'openurl': s['session']['slug'],
                'sessionnumber': ix,
                'wassessioncancelled': s['cancelled'],
                'starttimetimestamputc': s['startTimeUtc'],
                'endtimetimestamputc': s['endTimeUtc'],
                'starttimetimestamp': s['startTime'],
                'endtimetimestamp': s['endTime'],  
                'precisestarttime': s['preciseStartTime'],
                'startdtutc': datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc) + datetime.timedelta(seconds=s['startTimeUtc']) if s['startTimeUtc'] else None,
                'enddtutc': datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc) + datetime.timedelta(seconds=s['endTimeUtc']) if s['endTimeUtc'] else None,
                'startdt': build_local_datetime(s['startTime'], race_info.get('timezone')) if s['startTime'] else None,
                'enddt': build_local_datetime(s['endTime'], race_info.get('timezone')) if s['endTime'] else None,
                'timezone': race_info.get('timezone'),
            }
            if "Sprint Shootout" in tde['name'] and year >= 2024:
                tde['name'] = tde['name'].replace('Sprint Shootout', 'Sprint Qualifying')
            session_id = upsert_session_record(cur, gp, tde, race_info['race_number'])
            if year > 2005:
                session_facts = open_json(f"https://motorsportstats.com/api/session-facts?sessionSlug={tde['openurl']}")
                if "MaxSpeed" in session_facts:
                    timing_points = open_json(f"https://motorsportstats.com/api/timing-points?sessionSlug={tde['openurl']}")
                    flaggingmaxspeeds.append({'session': tde['name'], 'sessionSlug': tde['openurl'], 'timingPoints': timing_points, 'sessionid': session_id})             
            openurl = tde['openurl']
            canonical_session_key = normalize_session_name_for_race_control(tde['name'], year)
            if canonical_session_key == 'sprint':
                sprint_session_slug = openurl
            
            if not tde['wassessioncancelled'] and s['hasResults']:
                if 'qualifying' in openurl:
                    grandprixlinks.append({'href': f'https://motorsportstats.com/api/results-classification?sessionSlug={openurl}'}) #remember to parse sprint qualifying also
                elif 'sprint' in openurl:
                    grandprixlinks.append({'href': f'https://motorsportstats.com/api/results-classification?sessionSlug={openurl}'})
                if 'practice' in openurl and (1076 < race_info['race_number'] < 1079 or race_info['race_number'] > 1106):
                    dest.append(f'https://motorsportstats.com/api/result-statistics?sessionSlug={openurl}&sessionFact=LapTime&size=100000')
                if 'qualifying' in openurl and (race_info['race_number'] > 1106):
                    dest.append(f'https://motorsportstats.com/api/result-statistics?sessionSlug={openurl}&sessionFact=LapTime&size=100000')
                if 'sprint-qualifying' in openurl and (race_info['race_number'] > 1106):
                    dest.append(f'https://motorsportstats.com/api/result-statistics?sessionSlug={openurl}&sessionFact=LapTime&size=100000')
                if 'sprint' in openurl and race_info['race_number'] > 1106:
                    dest.append(f'https://motorsportstats.com/api/result-statistics?sessionSlug={openurl}&sessionFact=LapTime&size=100000')
                if 'sprint-qualifying' in openurl or ('race' not in openurl and 'sprint' not in openurl):
                    lapchartstoberead.append(f'https://motorsportstats.com/api/result-statistics?sessionSlug={openurl}&sessionFact=LapChart&size=100000')
        ensure_combined_qualifying_session(cur, gp, race_info['race_number'], year)
        save_race_control_messages(cur, year, grandsprix.index(grandprix) + 1, gp, race_info['race_number'])
        save_weather_data(cur, year, grandsprix.index(grandprix) + 1, gp, race_info['race_number'])
        save_historical_weather_data(cur, year, grandsprix.index(grandprix) + 1, gp, race_info['race_number'])
        save_red_flags(cur, gp, race_info['race_number'])
        if race_info['race_number'] > 344: #only after 1981 argentine grand prix
            grandprixlinks.append({'href': f"https://motorsportstats.com/api/results-classification?sessionSlug=fia-formula-one-world-championship_{year}_{msgrandprix}_race"})

        if year > 1995: #we have lap-by-lap data from 1996
            open_url(f"https://pitwall.app/analysis/compare-lap-times?season={dataid}")
            race_divs = soup.find("div", id='dropdown-select-race')
            if race_divs:
                races = race_divs.find_all('span', class_='item')
            else:
                races = []
            for race in races:
                if race.text.strip() == gp:
                    dataidforrace = race['data-id']
                    break  
        else:
            dataidforrace = None   
        resultnotes = parsenotes(grandprixlinks)
        for key, value in resultnotes.items():
            cur.execute(f"UPDATE GrandsPrix SET {key} = ? WHERE GrandPrixName = ?", (value, gp))   
        results = parse_race_results(grandprixlinks)
        # Apply name corrections to results
        name_corrections_results = {
            "Guanyu Zhou": "Zhou Guanyu"
        }
        for result in results:
            if result['driver'] in name_corrections_results:
                result['driver'] = name_corrections_results[result['driver']]
        print ("Results Parsed")
        entrant_keys = [
            "grandprix" ,"number", "driver", "nationality", "nationalityid", "team", "constructor", "chassis", "engine", "enginemodel", "tyre",
            "substituteorthirddriver", "qualifyingposition", "qualifyingtime", "qualifyinggap",
            "qualifyingtimeinseconds", "qualifyinggapseconds", "qualifyinginterval", "prequalifyingposition",
            "prequalifyingtime", "prequalifyinggap", "prequalifyinginterval", "prequalifyingtimeinseconds", "starting_grid_position",
            "gridpenalty", "gridpenalty_reason", "sprintstarting_grid_position", "sprintgridpenalty",
            "sprintgridpenalty_reason", "fastestlap", "fastestlapinseconds", "fastestlapgapinseconds",
            "fastestlapinterval", "fastestlap_time", "fastestlap_gap", "fastestlap_lap", "sprintfastestlap",
            "sprintfastestlapinseconds", "sprintfastestlapgapinseconds", "sprintfastestlapinterval", "sprintfastestlap_time",
            "sprintfastestlap_gap", "sprintfastestlap_lap", "qualifying2position", "qualifying2time",
            "qualifying2gap", "qualifying2interval", "qualifying2timeinseconds", "qualifying2laps", "qualifying1position",
            "qualifying1time", "qualifying1gap", "qualifying1interval", "qualifying1timeinseconds", "qualifying1laps",
            "qualifyinglaps", "qualifying1time", "qualifying2time", "qualifying3time",
            "qualifying1gap", "qualifying2gap", "qualifying3gap", "qualifying1interval", "qualifying2interval", "qualifying3interval",
            "qualifying1timeinseconds", "qualifying2timeinseconds", "qualifying3timeinseconds", "sprint_qualifyingposition",
            "sprint_qualifying1time", "sprint_qualifying2time", "sprint_qualifying3time",
            "sprint_qualifying1gap", "sprint_qualifying2gap", "sprint_qualifying3gap", "sprint_qualifying1interval", "sprint_qualifying2interval", "sprint_qualifying3interval",
            "sprint_qualifyinggap", "sprint_qualifyinginterval", "sprint_qualifying1timeinseconds", "sprint_qualifying2timeinseconds",
            "sprint_qualifying3timeinseconds", "sprint_qualifyinglaps", "sprint_qualifyingtime",
            "sprint_qualifyingtimeinseconds", "warmupposition", "warmuptime", "warmupgap",
            "warmuptimeinseconds", "warmuplaps", "practice1position", "practice1time", "practice1gap",
            "practice1interval", "practice1timeinseconds", "practice1laps", "practice2position", "practice2time",
            "practice2gap", "practice2interval", "practice2timeinseconds", "practice2laps", "practice3position",
            "practice3time", "practice3gap", "practice3interval", "practice3timeinseconds", "practice3laps",
            "practice4position", "practice4time", "practice4gap", "practice4interval", "practice4timeinseconds",
            "practice4laps", "sprintposition", "sprintlaps", "sprinttime", "sprintpoints", "sprintstatus", "sprintstatusreason",
            "sprinttimeinseconds", "sprintgap", "sprintgapinseconds", "sprintinterval", "raceposition", "racelaps",
            "racetime", "racepoints", "racestatus", "racestatusreason", "racetimeinseconds", "racegap", "racegapinseconds", 
            "raceinterval", "penalties", "sprint_penalties",
            "driverid", "teamid", "constructorid", "chassisid", "engineid", "enginemodelid", "tyreid", "grandprixid"
        ]
        pending_engine_makes = set()
       
        for result in results:
            #print (result['driver'])
            cur.execute("SELECT ID FROM GrandsPrix WHERE GrandPrixName = ?", (gp,))
            grandprix_id = cur.fetchone()[0]            
            cur.execute("INSERT OR IGNORE INTO Teams (TeamName, FirstGrandPrix, FirstGrandPrixID) VALUES (?,?,?)", (result['team'], gp, grandprix_id))
            constructor_name = result['constructor']
            if constructor_name not in constructors_processed_cache:
                constructor_slug = slugify_statsf1_constructor_name(constructor_name)
                open_url(f"https://www.statsf1.com/en/{constructor_slug}.aspx")
                constructor_nationalities = parse_constructor_nationalities_from_page(soup)
                filiations = parse_constructor_lineage_section_v2(soup, "Filiation")
                team_principals = parse_constructor_lineage_section_v2(soup, "Team Principal")                               
                cur.execute("INSERT OR IGNORE INTO Constructors (ConstructorName, FirstGrandPrix, FirstGrandPrixID) VALUES (?,?,?)", (constructor_name, gp, grandprix_id))
                cur.execute("SELECT ID FROM Constructors WHERE ConstructorName = ?", (constructor_name,))
                constructor_id = cur.fetchone()[0]
                cur.execute("""
                    UPDATE Constructors SET
                        Nationalities = ?,
                        Filiation = CASE WHEN ? != '[]' THEN ? ELSE Filiation END,
                        TeamPrincipals = CASE WHEN ? != '[]' THEN ? ELSE TeamPrincipals END
                    WHERE ID = ?
                """, (
                    json.dumps(constructor_nationalities),
                    json.dumps(filiations), json.dumps(filiations),
                    json.dumps(team_principals), json.dumps(team_principals),
                    constructor_id
                ))
                constructors_processed_cache.add(constructor_name)
            cur.execute("INSERT OR IGNORE INTO Engines (EngineName, FirstGrandPrix, FirstGrandPrixID) VALUES (?,?,?)", (result['engine'], gp, grandprix_id))
            cur.execute("INSERT OR IGNORE INTO Tyres (TyreName, FirstGrandPrix, FirstGrandPrixID) VALUES (?,?,?)", (result['tyre'], gp, grandprix_id))
            cur.execute("SELECT 1 FROM drivers WHERE name = ?", (result['driver'],))
            exists = cur.fetchone()
            nationality_id = None
            if not exists:
                fetch_family_relations_from_statsf1()
                # If not in DB, scrape or fetch their nationality and birthdate
                #change gianmaria bruni back to gimmi bruni:
                name_corrections = {
                    "Gianmaria Bruni": "Gimmi Bruni",
                    "PATRICIO O'WARD": "Pato O'Ward",
                    "Pato O'Ward": "Patricio O'Ward",
                    "Zhou Guanyu": "Guanyu Zhou"
                }    
                b = name_corrections.get(result['driver'], result['driver'])
                driver_name_clean = b.replace('*', '').replace('æ', '-').replace("'", '-').replace('Ø', 'O').replace('ø', 'o')
                if ' ' not in driver_name_clean:
                    driver_name_for_url = f'--{driver_name_clean}'
                else:
                    driver_name_for_url = driver_name_clean
                nationality, birthdate = fetch_driver_info(driver_name_for_url)
                first_grandprix = gp if not result["substituteorthirddriver"] else None
                first_grandprix_id = grandprix_id if not result["substituteorthirddriver"] else None                
                cur.execute("INSERT OR IGNORE INTO Nationalities (Nationality, FirstGrandPrix, FirstGrandPrixID) VALUES (?, ?, ?)", (nationality, first_grandprix, first_grandprix_id))
                cur.execute("SELECT ID FROM Nationalities WHERE Nationality = ?", (nationality,))
                nationality_id = cur.fetchone()[0]                     
                gender = None
                try:
                    gender = 'female' if is_female_driver(result['driver']) else 'male'
                except Exception:
                    gender = None

                cur.execute("""
                    INSERT INTO Drivers (name, nationality, birthdate, FirstGrandPrix, FirstGrandPrixID, NationalityID, Gender)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (result['driver'], nationality, birthdate, first_grandprix, first_grandprix_id, nationality_id, gender))          
                cur.execute('UPDATE Nationalities SET DriverCount = DriverCount + 1 WHERE ID = ?', (nationality_id,))
                # Check and add family relationships for the newly added driver
                cur.execute("SELECT ID FROM Drivers WHERE Name = ?", (result['driver'],))
                new_driver_id = cur.fetchone()[0]
                check_and_add_family_relations(result['driver'], new_driver_id, cur, family_pairs)
            else:
                driver_firstgrandprix = cur.execute("SELECT FirstGrandPrix FROM Drivers WHERE Name = ?", (result['driver'],)).fetchone()[0]
                if driver_firstgrandprix is None and not result["substituteorthirddriver"]:
                    cur.execute("UPDATE Drivers SET FirstGrandPrix = ?, FirstGrandPrixID = ? WHERE Name = ?", (gp, grandprix_id, result['driver']))
                nationality_firstgrandprix = cur.execute("SELECT FirstGrandPrix FROM Nationalities WHERE ID = (SELECT NationalityID FROM Drivers WHERE Name = ?)", (result['driver'],)).fetchone()[0]
                if nationality_firstgrandprix is None and not result["substituteorthirddriver"]:
                    cur.execute("UPDATE Nationalities SET FirstGrandPrix = ?, FirstGrandPrixID = ? WHERE ID = (SELECT NationalityID FROM Drivers WHERE Namechassis_name = ?)", (gp, grandprix_id, result['driver']))
            cur.execute("SELECT ID FROM Drivers WHERE Name = ?", (result['driver'],))
            driver_id = cur.fetchone()[0]
            #print (result['driver'], driver_id)
            cur.execute("SELECT ID FROM Teams WHERE TeamName = ?", (result['team'],))
            team_id = cur.fetchone()[0]
            cur.execute("SELECT ID FROM Constructors WHERE ConstructorName = ?", (result['constructor'],))
            constructor_id = cur.fetchone()[0]
            cur.execute("""
                INSERT OR IGNORE INTO Chassis (ConstructorName, ChassisName, ConstructorID, FirstGrandPrix, FirstGrandPrixID)
                VALUES (?,?,?,?,?)
            """, (result['constructor'], result['chassis'], constructor_id, gp, grandprix_id))
            cur.execute("SELECT ID FROM Chassis WHERE ConstructorName = ? AND ChassisName = ?", (result['constructor'], result['chassis']))
            chassis_id = cur.fetchone()[0]
            open_url(f"https://www.statsf1.com/en/{slugify_statsf1_constructor_name(result['constructor'])}-{slugify_statsf1_constructor_name(result['chassis'])}.aspx")
            engines_used = soup.find("div", {"id": "ctl00_CPH_Main_P_Moteurs"}).get_text(strip=True).split(",") if soup.find("div", {"id": "ctl00_CPH_Main_P_Moteurs"}) else None
            tyres_used = soup.find("div", {"id": "ctl00_CPH_Main_P_Pneus"}).get_text(strip=True).split(",") if soup.find("div", {"id": "ctl00_CPH_Main_P_Pneus"}) else None
            designers = parse_designers(str(soup))
            cur.execute("""
                SELECT DISTINCT EngineModel
                FROM GrandPrixResults
                WHERE Constructor = ? AND Chassis = ?
            """, (result['constructor'], result['chassis']))

            engine_models = [row[0] for row in cur.fetchall()]
            cur.execute("""
                UPDATE Chassis
                SET EngineMakesUsed = ?, TyresUsed = ?, Designers = ?, EngineModelsUsed = ?
                WHERE ConstructorName = ? AND ChassisName = ?
            """, (
                json.dumps(engines_used) if engines_used else None,
                json.dumps(tyres_used) if tyres_used else None,
                json.dumps(designers) if designers else None,
                json.dumps(engine_models) if engine_models else None,
                result['constructor'],
                result['chassis']
            ))
            cur.execute("SELECT ID FROM Engines WHERE EngineName = ?", (result['engine'],))
            engine_id = cur.fetchone()[0]
            cur.execute("""
                INSERT OR IGNORE INTO EngineModels (EngineMake, EngineModel, EngineMakeID, FirstGrandPrix, FirstGrandPrixID)
                VALUES (?,?,?,?,?)
            """, (result['engine'], result['enginemodel'], engine_id, gp, grandprix_id))
            if cur.rowcount == 1:
                pending_engine_makes.add(result['engine'])
            cur.execute("SELECT ID FROM EngineModels WHERE EngineMake = ? AND EngineModel = ?", (result['engine'], result['enginemodel']))
            engine_model_id = cur.fetchone()[0]
            cur.execute("SELECT ID FROM Tyres WHERE TyreName = ?", (result['tyre'],))
            tyre_id = cur.fetchone()[0]
            cur.execute("SELECT Nationality FROM Drivers WHERE Name = ?", (result['driver'],))
            nationality = cur.fetchone()[0]
            cur.execute("SELECT ID FROM Nationalities WHERE Nationality = ?", (nationality,))
            nationality_id = cur.fetchone()[0] 
            if not result["substituteorthirddriver"]:
                cur.execute("UPDATE Nationalities SET LastGrandPrix = ?, LastGrandPrixID = ? WHERE ID = ?", (gp, grandprix_id, nationality_id))
            cur.execute("UPDATE Nationalities SET needstatsupdate = 1 WHERE ID = ?", (nationality_id,))
            driverids[result['driver']] = driver_id
            teamids[result['team']] = team_id
            constructorids[result['constructor']] = constructor_id
            chassisids[(result['constructor'], result['chassis'])] = chassis_id
            engineids[result['engine']] = engine_id
            enginemodelids[(result['engine'], result['enginemodel'])] = engine_model_id
            tyreids[result['tyre']] = tyre_id

            if nationality_id is not None:
                nationalityids[nationality] = nationality_id


            result['driverid'] = driver_id
            result['teamid'] = team_id
            result['constructorid'] = constructor_id
            result['chassisid'] = chassis_id
            result['engineid'] = engine_id
            result['enginemodelid'] = engine_model_id
            result['tyreid'] = tyre_id
            result['grandprixid'] = grandprix_id
            result['grandprix'] = gp
            result['nationality'] = nationality
            result['nationalityid'] = nationality_id

            values = [result.get(key) for key in entrant_keys]
            penalties_idx = entrant_keys.index("penalties")
            sprint_penalties_idx = entrant_keys.index("sprint_penalties")
            values[penalties_idx] = json.dumps(values[penalties_idx]) if values[penalties_idx] else None
            values[sprint_penalties_idx] = json.dumps(values[sprint_penalties_idx]) if values[sprint_penalties_idx] else None
            placeholders = ', '.join(['?'] * len(entrant_keys))
            columns = ', '.join(entrant_keys)

            cur.execute(f'''
                INSERT INTO GrandPrixResults ({columns})
                VALUES ({placeholders})
            ''', values)
            if not result['substituteorthirddriver']:
                cur.execute("UPDATE Drivers SET LastGrandPrix = ?, LastGrandPrixID = ? WHERE ID = ?", (gp, grandprix_id, driver_id)) 
            cur.execute("UPDATE Teams SET LastGrandPrix = ?, LastGrandPrixID = ? WHERE ID = ?", (gp, grandprix_id, team_id))
            cur.execute("UPDATE Constructors SET LastGrandPrix = ?, LastGrandPrixID = ? WHERE ID = ?", (gp, grandprix_id, constructor_id))
            cur.execute("UPDATE Chassis SET LastGrandPrix = ?, LastGrandPrixID = ? WHERE ConstructorName = ? AND ChassisName = ?", (gp, grandprix_id, result['constructor'], result['chassis']))
            cur.execute("UPDATE Engines SET LastGrandPrix = ?, LastGrandPrixID = ? WHERE ID = ?", (gp, grandprix_id, engine_id))
            cur.execute("UPDATE EngineModels SET LastGrandPrix = ?, LastGrandPrixID = ? WHERE EngineMake = ? AND EngineModel = ?", (gp, grandprix_id, result['engine'], result['enginemodel']))
            cur.execute("UPDATE Tyres SET LastGrandPrix = ?, LastGrandPrixID = ? WHERE ID = ?", (gp, grandprix_id, tyre_id))
            cur.execute("UPDATE Drivers SET needstatsupdate = 1 WHERE ID = ?", (driver_id,))
            cur.execute("UPDATE Teams SET needstatsupdate = 1 WHERE ID = ?", (team_id,))
            cur.execute("UPDATE Constructors SET needstatsupdate = 1 WHERE ID = ?", (constructor_id,))
            cur.execute("UPDATE Chassis SET needstatsupdate = 1 WHERE ConstructorName = ? AND ChassisName = ?", (result['constructor'], result['chassis']))
            cur.execute("UPDATE Engines SET needstatsupdate = 1 WHERE ID = ?", (engine_id,))
            cur.execute("UPDATE EngineModels SET needstatsupdate = 1 WHERE EngineMake = ? AND EngineModel = ?", (result['engine'], result['enginemodel']))
            cur.execute("UPDATE Tyres SET needstatsupdate = 1 WHERE ID = ?", (tyre_id,))
        scrape_new_engine_models(pending_engine_makes)
        scrape_historical_practice(year, gp)
        print ("Results saved to database")
        session_lookup = get_race_control_session_lookup(cur, grandprix_id, year)

        for drivername in drivernames:
            fulfilled = False
            while not fulfilled:
                parts = drivername.lower().replace('.', '').split()
                if len(parts) >= 2:
                    first_initial = parts[0][0]
                    first_initial = normalize_name(first_initial)
                    last_name = ' '.join(parts[1:])
                    last_name = normalize_name(last_name)
                    # Try to match in results
                    for entrant in results:
                        full = normalize_name(entrant["driver"])
                        if full.startswith(first_initial) and full.endswith(last_name):
                            name_map[drivername] = entrant['driver']
                            fulfilled = True
                            break
                    # If not found, try to match in DB
                    if not fulfilled:
                        cur.execute("SELECT Driver FROM DriversChampionship WHERE Season = ?", (year,))
                        rows = cur.fetchall()
                        for row in rows:
                            full = normalize_name(row[0])
                            if full.startswith(first_initial) and full.endswith(last_name):
                                name_map[drivername] = row[0]
                                fulfilled = True
                                break
                    # If still not found, log and break to avoid infinite loop
                    if not fulfilled:
                        fulfilled = True  # Exit loop even if not found   
        #print (results)
        for item in grandprixlinks[6:]:
            if item['href'].endswith("/tour-par-tour.aspx"):
                lapbylaps = parse_lap_by_lap(f"https://www.statsf1.com{item['href']}", results, dataid, dataidforrace, year=year, grandprix_name=gp.replace(str(year), '').strip())
                lapbylap = lapbylaps[0]
                gp_ = gp
                if year >= 2021 and gp.endswith('Mexican Grand Prix'):
                    gp_ = gp.replace('Mexican Grand Prix', 'Mexico City Grand Prix')
                elif year >= 2026 and gp.endswith('Barcelona-Catalunya Grand Prix'):
                    gp_ = gp.replace('Barcelona-Catalunya Grand Prix', 'Barcelona Grand Prix')
                if year >= 2018:
                    fp1, fp2, fp3, q1, q2, q3, sq1, sq2, sq3, s = None, None, None, None, None, None, None, None, None, None
                    if dest:
                       for laah in dest:
                            if '1st-free-practice' in laah.lower():
                               fp1 = laah
                            elif '2nd-free-practice' in laah.lower():
                                fp2 = laah
                            elif '3rd-free-practice' in laah.lower():
                                fp3 = laah
                            elif '1st-qualifying' in laah.lower():
                                q1 = laah
                            elif '2nd-qualifying' in laah.lower():
                                q2 = laah
                            elif '3rd-qualifying' in laah.lower():
                                q3 = laah
                            elif '1st-sprint-qualifying' in laah.lower():
                               sq1 = laah
                            elif '2nd-sprint-qualifying' in laah.lower():
                                sq2 = laah
                            elif '3rd-sprint-qualifying' in laah.lower():
                                sq3 = laah
                            elif 'sprint' in laah.lower() or 'sprint-qualifying' in laah.lower():
                                s = laah

                    # Practice 1
                    if any(link['href'].endswith('practice/1') for link in theplacewithallthelinks.find_all('a', class_ = 'DropdownMenuItem-module_dropdown-menu-item__6Y3-v typography-module_body-s-semibold__O2lOH')):
                        practice1_session = require_session_record(session_lookup, 'practice1', gp)
                        qwertyuiop = urllib.request.Request(f"https://cdn.jsdelivr.net/gh/TracingInsights/{year}@main/{urllib.parse.quote(gp_.replace(str(year), '').strip())}/Practice%201/drivers.json", headers=headers)
                        driverdatafortracinginsights = json.loads(urllib.request.urlopen(qwertyuiop).read())['drivers']
                        
                        # Get MSS laptimes for FP1 as fallback
                        fp1_mss_laps = {}
                        if fp1:
                            fp1_mss_data = scrape_mss_laptimes(fp1, results)
                            for lap in fp1_mss_data:
                                key = (lap['driver'], lap['lap'])
                                fp1_mss_laps[key] = lap
                        
                        # Get lap chart data for FP1 positions
                        fp1_lap_chart = {}
                        if fp1:
                            fp1_chart_data = readlapcharts(fp1.replace('&sessionFact=LapTime', '&sessionFact=LapChart'))
                            # Convert to driver name lookup
                            for driver_num, lap_positions in fp1_chart_data.items():
                                for entrant in results:
                                    if entrant['number'] == driver_num:
                                        for lap, position in lap_positions:
                                            key = (entrant['driver'], lap)
                                            fp1_lap_chart[key] = position
                                        break
                        
                        for driver in driverdatafortracinginsights:
                            driver_abbr = driver['driver']
                            driver_team = driver['team']
                            matched_entrant = match_tracing_abbr_to_entrant(
                                abbr=driver_abbr,
                                entrants=results,
                                tracing_lap_json=driver.get("laps"),
                                f1_best_times={inst['driver']: inst.get('practice1timeinseconds') for inst in results},
                                lap_by_lap_map=lapbylaps[1],
                                tracing_team=driver_team
                            )  
                            if matched_entrant:
                                practice1data = scrape_tracinginsights(f"https://cdn.jsdelivr.net/gh/TracingInsights/{year}@main/{urllib.parse.quote(gp_.replace(str(year), '').strip())}/Practice%201/{driver_abbr}/laptimes.json", 'practice1', driver_abbr=driver_abbr)
                                for practice1lap in practice1data:
                                    # Use MSS data as fallback if TracingInsights time is None
                                    time_val = practice1lap.get('time')
                                    time_in_sec = practice1lap.get('time_in_seconds')
                                    position_val = practice1lap.get('position')
                                    
                                    mss_key = (matched_entrant['driver'], practice1lap['lap'])
                                    
                                    if time_val is None or time_in_sec is None:
                                        if mss_key in fp1_mss_laps:
                                            time_val = time_val or fp1_mss_laps[mss_key]['time']
                                            time_in_sec = time_in_sec or fp1_mss_laps[mss_key]['timeinseconds']
                                    
                                    # Use lap chart data for position if None
                                    if position_val is None:
                                        if mss_key in fp1_lap_chart:
                                            position_val = fp1_lap_chart[mss_key]
                                    
                                    practice1lap['time'] = time_val
                                    practice1lap['time_in_seconds'] = time_in_sec
                                    practice1lap['position'] = position_val
                                    insert_lap_by_lap_row(cur, gp, matched_entrant['driver'], practice1_session['name'], practice1_session['id'], practice1lap, grandprix_id, driver_id)
                    
                    # Practice 2
                    if any(link['href'].endswith('practice/2') for link in theplacewithallthelinks.find_all('a', class_ = 'DropdownMenuItem-module_dropdown-menu-item__6Y3-v typography-module_body-s-semibold__O2lOH')):
                        practice2_session = require_session_record(session_lookup, 'practice2', gp)
                        asdfghjkl = urllib.request.Request(f"https://cdn.jsdelivr.net/gh/TracingInsights/{year}@main/{urllib.parse.quote(gp_.replace(str(year), '').strip())}/Practice%202/drivers.json", headers=headers)
                        driverdatafortracinginsights2 = json.loads(urllib.request.urlopen(asdfghjkl).read())['drivers']
                        
                        # Get MSS laptimes for FP2 as fallback
                        fp2_mss_laps = {}
                        if fp2:
                            fp2_mss_data = scrape_mss_laptimes(fp2, results)
                            for lap in fp2_mss_data:
                                key = (lap['driver'], lap['lap'])
                                fp2_mss_laps[key] = lap
                        
                        # Get lap chart data for FP2 positions
                        fp2_lap_chart = {}
                        if fp2:
                            fp2_chart_data = readlapcharts(fp2.replace('&sessionFact=LapTime', '&sessionFact=LapChart'))
                            for driver_num, lap_positions in fp2_chart_data.items():
                                for entrant in results:
                                    if entrant['number'] == driver_num:
                                        for lap, position in lap_positions:
                                            key = (entrant['driver'], lap)
                                            fp2_lap_chart[key] = position
                                        break
                        
                        for driver in driverdatafortracinginsights2:
                            driver_abbr = driver['driver']
                            driver_team = driver['team']
                            matched_entrant = match_tracing_abbr_to_entrant(
                                abbr=driver_abbr,
                                entrants=results,
                                tracing_lap_json=driver.get("laps"),
                                f1_best_times={inst['driver']: inst.get('practice2timeinseconds') for inst in results},
                                lap_by_lap_map=lapbylaps[1],
                                tracing_team=driver_team
                            )  
                            if matched_entrant:
                                practice2data = scrape_tracinginsights(f"https://cdn.jsdelivr.net/gh/TracingInsights/{year}@main/{urllib.parse.quote(gp_.replace(str(year), '').strip())}/Practice%202/{driver_abbr}/laptimes.json", 'practice2', driver_abbr=driver_abbr)
                                for practice2lap in practice2data:
                                    # Use MSS data as fallback
                                    time_val = practice2lap.get('time')
                                    time_in_sec = practice2lap.get('time_in_seconds')
                                    position_val = practice2lap.get('position')
                                    
                                    mss_key = (matched_entrant['driver'], practice2lap['lap'])
                                    
                                    if time_val is None or time_in_sec is None:
                                        if mss_key in fp2_mss_laps:
                                            time_val = time_val or fp2_mss_laps[mss_key]['time']
                                            time_in_sec = time_in_sec or fp2_mss_laps[mss_key]['timeinseconds']
                                    
                                    # Use lap chart data for position if None
                                    if position_val is None:
                                        if mss_key in fp2_lap_chart:
                                            position_val = fp2_lap_chart[mss_key]
                                    
                                    practice2lap['time'] = time_val
                                    practice2lap['time_in_seconds'] = time_in_sec
                                    practice2lap['position'] = position_val
                                    insert_lap_by_lap_row(cur, gp, matched_entrant['driver'], practice2_session['name'], practice2_session['id'], practice2lap, grandprix_id, driver_id)
                    
                    # Practice 3
                    if any(link['href'].endswith('practice/3') for link in theplacewithallthelinks.find_all('a', class_ = 'DropdownMenuItem-module_dropdown-menu-item__6Y3-v typography-module_body-s-semibold__O2lOH')):
                        practice3_session = require_session_record(session_lookup, 'practice3', gp)
                        zxcvbnm = urllib.request.Request(f"https://cdn.jsdelivr.net/gh/TracingInsights/{year}@main/{urllib.parse.quote(gp_.replace(str(year), '').strip())}/Practice%203/drivers.json", headers=headers)
                        driverdatafortracinginsights3 = json.loads(urllib.request.urlopen(zxcvbnm).read())['drivers']
                        
                        # Get MSS laptimes for FP3 as fallback
                        fp3_mss_laps = {}
                        if fp3:
                            fp3_mss_data = scrape_mss_laptimes(fp3, results)
                            for lap in fp3_mss_data:
                                key = (lap['driver'], lap['lap'])
                                fp3_mss_laps[key] = lap
                        
                        # Get lap chart data for FP3 positions
                        fp3_lap_chart = {}
                        if fp3:
                            fp3_chart_data = readlapcharts(fp3.replace('&sessionFact=LapTime', '&sessionFact=LapChart'))
                            for driver_num, lap_positions in fp3_chart_data.items():
                                for entrant in results:
                                    if entrant['number'] == driver_num:
                                        for lap, position in lap_positions:
                                            key = (entrant['driver'], lap)
                                            fp3_lap_chart[key] = position
                                        break
                        
                        for driver in driverdatafortracinginsights3:
                            driver_abbr = driver['driver']
                            driver_team = driver['team']
                            matched_entrant = match_tracing_abbr_to_entrant(
                                abbr=driver_abbr,
                                entrants=results,
                                tracing_lap_json=driver.get("laps"),
                                f1_best_times={inst['driver']: inst.get('practice3timeinseconds') for inst in results},
                                lap_by_lap_map=lapbylaps[1],
                                tracing_team=driver_team
                            )  
                            if matched_entrant:
                                practice3data = scrape_tracinginsights(f"https://cdn.jsdelivr.net/gh/TracingInsights/{year}@main/{urllib.parse.quote(gp_.replace(str(year), '').strip())}/Practice%203/{driver_abbr}/laptimes.json", 'practice3', driver_abbr=driver_abbr)
                                for practice3lap in practice3data:
                                    # Use MSS data as fallback
                                    time_val = practice3lap.get('time')
                                    time_in_sec = practice3lap.get('time_in_seconds')
                                    position_val = practice3lap.get('position')
                                    
                                    mss_key = (matched_entrant['driver'], practice3lap['lap'])
                                    
                                    if time_val is None or time_in_sec is None:
                                        if mss_key in fp3_mss_laps:
                                            time_val = time_val or fp3_mss_laps[mss_key]['time']
                                            time_in_sec = time_in_sec or fp3_mss_laps[mss_key]['timeinseconds']
                                    
                                    # Use lap chart data for position if None
                                    if position_val is None:
                                        if mss_key in fp3_lap_chart:
                                            position_val = fp3_lap_chart[mss_key]
                                    
                                    practice3lap['time'] = time_val
                                    practice3lap['time_in_seconds'] = time_in_sec
                                    practice3lap['position'] = position_val
                                    insert_lap_by_lap_row(cur, gp, matched_entrant['driver'], practice3_session['name'], practice3_session['id'], practice3lap, grandprix_id, driver_id)
                    
                    # Sprint Qualifying
                    if any(link['href'].endswith('sprint-qualifying') for link in theplacewithallthelinks.find_all('a', class_ = 'DropdownMenuItem-module_dropdown-menu-item__6Y3-v typography-module_body-s-semibold__O2lOH')):
                        sessiontype = 'sprintshootout' if year == 2023 else 'sprintqualifying'
                        sprint_qualifying_session = require_session_record(session_lookup, sessiontype, gp)
                        urlforlink = 'Sprint%20Shootout' if year == 2023 else 'Sprint%20Qualifying'
                        
                        # Get MSS laptimes for sprint qualifying (q1, q2, q3)
                        sq_mss_laps = {}
                        sq_lap_chart = {}
                        if sq1 and sq2 and sq3:
                            for session_url, segment in [(sq1, 'SQ1'), (sq2, 'SQ2'), (sq3, 'SQ3')]:
                                session_mss_data = scrape_mss_laptimes(session_url, results)
                                for lap in session_mss_data:
                                    key = (lap['driver'], lap['lap'], segment)
                                    sq_mss_laps[key] = lap
                                
                                # Get lap chart for this segment
                                chart_data = readlapcharts(session_url.replace('&sessionFact=LapTime', '&sessionFact=LapChart'))
                                for driver_num, lap_positions in chart_data.items():
                                    for entrant in results:
                                        if entrant['number'] == driver_num:
                                            for lap, position in lap_positions:
                                                key = (entrant['driver'], lap, segment)
                                                sq_lap_chart[key] = position
                                            break
                        
                        qwertyasdf = urllib.request.Request(f"https://cdn.jsdelivr.net/gh/TracingInsights/{year}@main/{urllib.parse.quote(gp_.replace(str(year), '').strip())}/{urlforlink}/drivers.json", headers=headers)
                        driverdatafortracinginsights4 = json.loads(urllib.request.urlopen(qwertyasdf).read())['drivers']
                        
                        for driver in driverdatafortracinginsights4:
                            driver_abbr = driver['driver']
                            driver_team = driver['team']
                            matched_entrant = match_tracing_abbr_to_entrant(
                                abbr=driver_abbr,
                                entrants=results,
                                tracing_lap_json=driver.get("laps"),
                                f1_best_times={inst['driver']: inst.get('sprint_qualifyingtimeinseconds') for inst in results},
                                lap_by_lap_map=lapbylaps[1],
                                tracing_team=driver_team
                            )  
                            if matched_entrant:
                                sprintqualifyingdata = scrape_tracinginsights(f"https://cdn.jsdelivr.net/gh/TracingInsights/{year}@main/{urllib.parse.quote(gp_.replace(str(year), '').strip())}/{urlforlink}/{driver_abbr}/laptimes.json", driver_abbr=driver_abbr)
                                
                                # Group laps by driver to determine qs
                                driver_laps = [lap for lap in sprintqualifyingdata if lap]
                                total_laps = len(driver_laps)
                                
                                for idx, sprintqualifyinglap in enumerate(driver_laps):
                                    # Use MSS data as fallback
                                    time_val = sprintqualifyinglap.get('time')
                                    time_in_sec = sprintqualifyinglap.get('time_in_seconds')
                                    qs_val = sprintqualifyinglap.get('qs')
                                    position_val = sprintqualifyinglap.get('position')
                                    
                                    # Determine qs if None by matching lap count
                                    if qs_val is None and total_laps > 0:
                                        lap_num = sprintqualifyinglap['lap']
                                        # Try to find matching MSS lap with segment info
                                        for segment in ['SQ1', 'SQ2', 'SQ3']:
                                            mss_key = (matched_entrant['driver'], lap_num, segment)
                                            if mss_key in sq_mss_laps:
                                                qs_val = segment
                                                if time_val is None or time_in_sec is None:
                                                    time_val = time_val or sq_mss_laps[mss_key]['time']
                                                    time_in_sec = time_in_sec or sq_mss_laps[mss_key]['timeinseconds']
                                                # Use lap chart position
                                                if position_val is None and mss_key in sq_lap_chart:
                                                    position_val = sq_lap_chart[mss_key]
                                                break
                                    
                                    sprintqualifyinglap['time'] = time_val
                                    sprintqualifyinglap['time_in_seconds'] = time_in_sec
                                    sprintqualifyinglap['position'] = position_val
                                    insert_lap_by_lap_row(cur, gp, matched_entrant['driver'], sprint_qualifying_session['name'], sprint_qualifying_session['id'], sprintqualifyinglap, grandprix_id, driver_id, qualifying_segment=qs_val)
                    
                    # Qualifying
                    if any(link['href'].endswith('qualifying') for link in theplacewithallthelinks.find_all('a', class_ = 'DropdownMenuItem-module_dropdown-menu-item__6Y3-v typography-module_body-s-semibold__O2lOH')):
                        qualifying_session = require_session_record(session_lookup, 'qualifying', gp)
                        # Get MSS laptimes for qualifying (q1, q2, q3)
                        q_mss_laps = {}
                        q_lap_chart = {}
                        if q1 and q2 and q3:
                            for session_url, segment in [(q1, 'Q1'), (q2, 'Q2'), (q3, 'Q3')]:
                                session_mss_data = scrape_mss_laptimes(session_url, results)
                                for lap in session_mss_data:
                                    key = (lap['driver'], lap['lap'], segment)
                                    q_mss_laps[key] = lap
                                
                                # Get lap chart for this segment
                                chart_data = readlapcharts(session_url.replace('&sessionFact=LapTime', '&sessionFact=LapChart'))
                                for driver_num, lap_positions in chart_data.items():
                                    for entrant in results:
                                        if entrant['number'] == driver_num:
                                            for lap, position in lap_positions:
                                                key = (entrant['driver'], lap, segment)
                                                q_lap_chart[key] = position
                                            break
                        
                        zxcvbnmasdf = urllib.request.Request(f"https://cdn.jsdelivr.net/gh/TracingInsights/{year}@main/{urllib.parse.quote(gp_.replace(str(year), '').strip())}/Qualifying/drivers.json", headers=headers)
                        driverdatafortracinginsights5 = json.loads(urllib.request.urlopen(zxcvbnmasdf).read())['drivers']
                        
                        for driver in driverdatafortracinginsights5:
                            driver_abbr = driver['driver']
                            driver_team = driver['team']
                            matched_entrant = match_tracing_abbr_to_entrant(
                                abbr=driver_abbr,
                                entrants=results,
                                tracing_lap_json=driver.get("laps"),
                                f1_best_times={inst['driver']: inst.get('qualifyingtimeinseconds') for inst in results},
                                lap_by_lap_map=lapbylaps[1],
                                tracing_team=driver_team
                            )  
                            if matched_entrant:
                                qualifyingdata = scrape_tracinginsights(f"https://cdn.jsdelivr.net/gh/TracingInsights/{year}@main/{urllib.parse.quote(gp_.replace(str(year), '').strip())}/Qualifying/{driver_abbr}/laptimes.json", driver_abbr=driver_abbr)
                                
                                # Group laps by driver to determine qs
                                driver_laps = [lap for lap in qualifyingdata if lap]
                                total_laps = len(driver_laps)
                                
                                for idx, qualifyinglap in enumerate(driver_laps):
                                    # Use MSS data as fallback
                                    time_val = qualifyinglap.get('time')
                                    time_in_sec = qualifyinglap.get('time_in_seconds')
                                    qs_val = qualifyinglap.get('qs')
                                    position_val = qualifyinglap.get('position')
                                    
                                    # Determine qs if None by matching lap count and driver
                                    if qs_val is None and total_laps > 0:
                                        lap_num = qualifyinglap['lap']
                                        # Try to find matching MSS lap with segment info
                                        for segment in ['Q1', 'Q2', 'Q3']:
                                            mss_key = (matched_entrant['driver'], lap_num, segment)
                                            if mss_key in q_mss_laps:
                                                qs_val = segment
                                                if time_val is None or time_in_sec is None:
                                                    time_val = time_val or q_mss_laps[mss_key]['time']
                                                    time_in_sec = time_in_sec or q_mss_laps[mss_key]['timeinseconds']
                                                # Use lap chart position
                                                if position_val is None and mss_key in q_lap_chart:
                                                    position_val = q_lap_chart[mss_key]
                                                break
                                    
                                    qualifyinglap['time'] = time_val
                                    qualifyinglap['time_in_seconds'] = time_in_sec
                                    qualifyinglap['position'] = position_val
                                    insert_lap_by_lap_row(cur, gp, matched_entrant['driver'], qualifying_session['name'], qualifying_session['id'], qualifyinglap, grandprix_id, driver_id, qualifying_segment=qs_val)
                
                if trigger == True:
                    sprint_session = require_session_record(session_lookup, 'sprint', gp)
                    sprintlapbylap = parse_lap_by_lap(f"https://www.statsf1.com{grandprix['href'].replace('.aspx', '')}/sprint.aspx?tpt", results, year=year, grandprix_name=gp.replace(str(year), '').strip())[0]
                    
                    # Get MSS laptimes for sprint race as fallback
                    sprint_mss_laps = {}
                    if s:  # Assuming you have a 'sprint' variable with the MSS URL
                        sprint_mss_data = scrape_mss_laptimes(s, results)
                        for lap in sprint_mss_data:
                            key = (lap['driver'], lap['lap'])
                            sprint_mss_laps[key] = lap
                    
                    for instance in sprintlapbylap:
                        driver_id = driverids[instance['driver']]
                        
                        # Use MSS data as fallback if TracingInsights/StatsF1 time is None
                        time_val = instance.get('time')
                        time_in_sec = instance.get('time_in_seconds')
                        
                        if time_val is None or time_in_sec is None:
                            mss_key = (instance['driver'], instance['lap'])
                            if mss_key in sprint_mss_laps:
                                time_val = time_val or sprint_mss_laps[mss_key]['time']
                                time_in_sec = time_in_sec or sprint_mss_laps[mss_key]['timeinseconds']
                        
                        instance['time'] = time_val
                        instance['time_in_seconds'] = time_in_sec
                        insert_lap_by_lap_row(cur, gp, instance['driver'], sprint_session['name'], sprint_session['id'], instance, grandprix_id, driver_id)
                    if sprint_session_slug:
                        sprintpitstopsummaryjson = fetch_sprint_pitstop_content(sprint_session_slug, year)
                        for pitstop in sprintpitstopsummaryjson:
                            for entrant in results:
                                if entrant['number'] == int(pitstop['carNumber']):
                                    driver_name = entrant['driver']
                                    driver_id = driverids[driver_name]
                                    constructor_id = constructorids[entrant['constructor']]
                                    
                                    timeofday = pitstop['timeOfDay']
                                    lap = pitstop['lap']
                                    stopnumber = pitstop['stopNumber']
                                    duration = float(Decimal(str(pitstop['duration'] / 1000)).quantize(Decimal('0.001'))) if pitstop.get('duration') is not None else None
                                    totalduration = float(Decimal(str(pitstop['totalDuration'] / 1000)).quantize(Decimal('0.001'))) if pitstop.get('totalDuration') is not None else None
                                    cur.execute("""
                                    INSERT INTO PitStopSummary (GrandPrix, Number, Driver, Constructor, StopNumber, Lap, DurationSpentInPitLane, TimeInSeconds, TimeOfDayStopped, TotalTimeSpentInPitLane, TotalTimeinSeconds, SessionName, SessionID, GrandPrixID, DriverID, ConstructorID)
                                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                                    """,
                                    (gp, entrant['number'], driver_name, entrant['constructor'], stopnumber, lap, duration, duration, timeofday, totalduration, totalduration, sprint_session['name'], sprint_session['id'], grandprix_id, driver_id, constructor_id))
                                    break
                #print (lapbylap)  
                #print (name_map)  
                print ("Lap by lap Parsed")
                
                race_session = require_session_record(session_lookup, 'grandprix', gp)
                for instance in lapbylap:
                    driver_id = driverids[instance['driver']]
                    insert_lap_by_lap_row(cur, gp, instance['driver'], race_session['name'], race_session['id'], instance, grandprix_id, driver_id)
                print("Lap by lap data saved to database")    
            elif item['href'].endswith("/championnat.aspx"): 
                driversprogress, construtorsprogress = parse_in_season_progress(f"https://www.statsf1.com{item['href']}")
                #print (progress)
                print ("In season progress Parsed")
                
                for driver in driversprogress:
                    driver_id = driverids.get(driver['driver'], cur.execute("SELECT ID FROM Drivers WHERE Name = ?", (driver['driver'],)).fetchone()[0])
                    cur.execute("""
                     INSERT INTO InSeasonProgressDrivers (GrandPrix, PositionAtThisPoint, Driver, PointsAtThisPoint, GrandPrixID, DriverID)
                     VALUES (?,?,?,?,?,?)
                    """, 
                    (gp, driver['positionatthispoint'], driver['driver'], driver['pointsatthispoint'], grandprix_id, driver_id))
                if construtorsprogress != []:
                    for driver in construtorsprogress:
                        constructor_id = constructorids[driver['constructor']]
                        engine_id = engineids[driver['engine']]
                        cur.execute("""
                            INSERT INTO InSeasonProgressConstructors (GrandPrix, PositionAtThisPoint, Constructor, Engine, PointsAtThisPoint, GrandPrixID, ConstructorID, EngineID)
                            VALUES (?,?,?,?,?,?,?,?)
                            """, 
                            (gp, driver['positionatthispoint'], driver['constructor'], driver['engine'], driver['pointsatthispoint'], grandprix_id, constructor_id, engine_id))
                        
                print ("In season progress data saved to database") 
        if flaggingmaxspeeds:
            for speed_session in flaggingmaxspeeds:
                session_name = speed_session['session']
                session_slug = speed_session['sessionSlug']
                timing_point_names = speed_session['timingPoints']
                session_id = speed_session['sessionid']
                
                
                # For each timing point, fetch the max speed data
                for timing_point in timing_point_names:
                    if not timing_point:
                        continue
                        
                    max_speed_url = f"https://motorsportstats.com/api/result-statistics?sessionSlug={session_slug}&sessionFact=MaxSpeed&timingPoint={urllib.parse.quote(timing_point)}&size=10000"
                    max_speed_data = open_json(max_speed_url)
                    
                    if not max_speed_data or 'content' not in max_speed_data:
                        continue
                    
                    for speed_entry in max_speed_data['content']:
                        position = speed_entry.get('position')
                        car_number = int(speed_entry.get('carNumber', 0))
                        speed_kph = speed_entry.get('speed')
                        
                        # Find matching entrant by car number
                        matched_entrant = None
                        for entrant in results:
                            if entrant['number'] == car_number:
                                matched_entrant = entrant
                                break
                        
                        if not matched_entrant:
                            continue
                        
                        driver_name = matched_entrant['driver']
                        constructor_name = matched_entrant['constructor']
                        engine_name = matched_entrant['engine']
                        engine_model_name = matched_entrant['enginemodel']
                        
                        # Get IDs
                        driver_id = driverids.get(driver_name)
                        constructor_id = constructorids.get(constructor_name)
                        engine_id = engineids.get(engine_name)
                        engine_model_id = enginemodelids.get((engine_name, engine_model_name))
                        
                        cur.execute("""
                            INSERT INTO MaxSpeeds (GrandPrixName, Position, Driver, Constructor, Engine, EngineModel, SessionName, TimingPoint, SpeedKph, GrandPrixID, SessionID, DriverID, ConstructorID, EngineID, EngineModelID)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """, (
                            gp,
                            position,
                            driver_name,
                            constructor_name,
                            engine_name,
                            engine_model_name,
                            session_name,
                            timing_point,
                            speed_kph,
                            grandprix_id,
                            session_id,
                            driver_id,
                            constructor_id,
                            engine_id,
                            engine_model_id
                        ))   
            print("Max speeds data saved to database")
        

                                     
        if sxs:
            open_url(f"https://formula1.com{sxs}")
            tablex = soup.find('table', class_ = 'Table-module_table__cKsW2')          #soup.find('table', class_ = 'f1-table f1-table-with-data w-full')
            pitstopsummary = parse_pit_stop_summary(tablex, results, year=year, grandprix_name=gp) 
            print ("Pit stop summary Parsed") 
            for pitstop in pitstopsummary:
                driver_id = driverids[pitstop['driver']]
                constructor_id = constructorids[pitstop['constructor']]
                duration_stopped_in_pitbox = pitstop.get('durationstoppedinpitbox', None)
                                
                # Try to insert with DurationStoppedInPitBox column, fall back if column doesn't exist
                cur.execute("""
                INSERT INTO PitStopSummary (GrandPrix, Number, Driver, Constructor, StopNumber, Lap, DurationSpentInPitLane, TimeInSeconds, TimeOfDayStopped, TotalTimeSpentInPitLane, TotalTimeinSeconds, DurationStoppedInPitBox, SessionName, SessionID, GrandPrixID, DriverID, ConstructorID)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (gp, pitstop['carnumber'], pitstop['driver'], pitstop['constructor'], pitstop['stopnumber'], pitstop['lapstopped'], pitstop['durationspentinpitlane'], pitstop['timeinseconds'], pitstop['timeofday'], pitstop['totaltimeforthewholerace'], pitstop['totaltimeinseconds'], duration_stopped_in_pitbox, race_session['name'], race_session['id'], grandprix_id, driver_id, constructor_id))
            #print("Pit Stop Summary:", pitstopsummary) 

        save_telemetry_data(cur, year, gp, race_info['race_number'])

        if trigger2 == True:
            driverschampionship, constructorschampionship = parse_championship_results(year, name_map)
            #check for locked:
            driverschampionship = apply_mathematical_locks(driverschampionship, points_system_drivers)
            constructorschampionship = apply_mathematical_locks(constructorschampionship, points_system_constructors)
            #print (championship)
            print ("Championship results Parsed")
            for driver in driverschampionship:
                try:
                    driver_id = driverids[driver['driver']]
                except KeyError:
                    cur.execute("SELECT ID FROM Drivers WHERE Name = ?", (driver['driver'],))
                    row = cur.fetchone()
                    if row:
                        driver_id = row[0]
                    else:
                        raise ValueError(f"Driver ID not found for {driver['driver']}")                
                cur.execute("""
                INSERT OR REPLACE INTO DriversChampionship (ID, Season, Position, Driver, Points, OutOf, RaceByRace, DriverID)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (str(year) + driver['driver'], year, driver['position'], driver['driver'], driver['points'], driver['outof'], json.dumps(driver['racebyrace']), driver_id))
            if constructorschampionship != []:
                for constructor in constructorschampionship:
                    try:
                        constructor_id = constructorids[constructor['constructor']]
                    except KeyError:
                        cur.execute("SELECT ID FROM Constructors WHERE ConstructorName = ?", (constructor['constructor'],))
                        row = cur.fetchone()
                        if row:
                            constructor_id = row[0]
                        else:
                            raise ValueError(f"Constructor ID not found for {constructor['constructor']}")                    
                    try:
                        engine_id = engineids[constructor['engine']]
                    except KeyError:
                        cur.execute("SELECT ID FROM Engines WHERE EngineName = ?", (constructor['engine'],))
                        row = cur.fetchone()
                        if row:
                            engine_id = row[0]
                        else:
                            raise ValueError(f"Engine not found in DB: {constructor['engine']}")
                    cur.execute("""
                    INSERT OR REPLACE INTO ConstructorsChampionship (ID, Season, Position, Constructor, Engine, Points, OutOf, RaceByRace, ConstructorID, EngineID)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (str(year) + constructor['constructor'] + constructor['engine'], year, constructor['position'], constructor['constructor'], constructor['engine'], constructor['points'], constructor['outof'], json.dumps(constructor['racebyrace']), constructor_id, engine_id))
            print ("Championship results saved to database")
    conn.commit()


print ("All seasons processed and saved to database. Updating subtables...")


def update_entity_stats(cur, table_name, fk_column, has_substitute_check=False, champ_table=None, indy500_check=True):
    """
    Dynamically generates and executes a single-pass optimized UPDATE query 
    for F1 entities (Drivers, Constructors, Teams, etc.).
    """
    
    # Only Drivers have the substitute check for SeasonsRaced
    sub_check = "AND gr.substituteorthirddriver = 0" if has_substitute_check else ""
    
    # Determine how to calculate championships
    champ_cte = ""
    champ_joins = ""
    champ_sets = ""
    
    if champ_table:
        # Drivers use DriverID, Constructors/Engines use ConstructorID/EngineID
        champ_fk = "DriverID" if champ_table == "DriversChampionship" else fk_column
        champ_cte = f"""
        , ChampStats AS (
            SELECT {champ_fk} AS id, 
                   SUM(CASE WHEN Position = 1 THEN 1 ELSE 0 END) AS championships,
                   MIN(Position) AS best_champ_pos
            FROM {champ_table}
            GROUP BY {champ_fk}
        )
        """
        champ_joins = f"LEFT JOIN ChampStats cs ON base.id = cs.id"
        champ_sets = """
            Championships = COALESCE(cs.championships, 0),
            BestChampionshipPosition = cs.best_champ_pos,
        """
    query = f"""
    WITH BaseStats AS (
        SELECT 
            {fk_column} AS id,
            SUM(CASE WHEN raceposition = 1 THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN raceposition <= 3 THEN 1 ELSE 0 END) AS podiums,
            SUM(CASE WHEN starting_grid_position = 1 THEN 1 ELSE 0 END) AS poles,
            SUM(CASE WHEN fastestlap = 1 THEN 1 ELSE 0 END) AS fastestlaps,
            SUM(CASE WHEN sprintposition = 1 THEN 1 ELSE 0 END) AS sprintwins,
            SUM(CASE WHEN sprintposition <= 3 THEN 1 ELSE 0 END) AS sprintpodiums,
            SUM(CASE WHEN sprintstarting_grid_position = 1 THEN 1 ELSE 0 END) AS sprintpoles,
            SUM(CASE WHEN sprintfastestlap = 1 THEN 1 ELSE 0 END) AS sprintfastestlaps,
            SUM(IFNULL(racepoints, 0) + IFNULL(sprintpoints, 0)) AS points,
            
            -- FIXED: Ensure racestatus isn't DNS or Withdrew
            SUM(CASE WHEN racestatus IS NOT NULL 
                      AND racestatus NOT LIKE '%Did not start%' 
                      AND racestatus NOT LIKE '%Withdrew%' 
                 THEN 1 ELSE 0 END) AS starts,
                 
            COUNT(*) AS entries,
            
            -- FIXED: Ensure sprintstatus actually exists, and isn't DNS or Withdrew
            SUM(CASE WHEN sprintstatus IS NOT NULL 
                      AND sprintstatus NOT LIKE '%Did not start%' 
                      AND sprintstatus NOT LIKE '%Withdrew%' 
                 THEN 1 ELSE 0 END) AS sprintstarts,
                 
            SUM(CASE WHEN racestatus LIKE '%Did not finish%' THEN 1 ELSE 0 END) AS dnfs,
            MIN(starting_grid_position) AS best_grid,
            MIN(sprintstarting_grid_position) AS best_sprint_grid,
            MIN(qualifyingposition) AS best_qual,
            MIN(raceposition) AS best_race,
            MIN(sprintposition) AS best_sprint,
            MIN(sprint_qualifyingposition) AS best_sprint_qual
        FROM GrandPrixResults
        WHERE {fk_column} IS NOT NULL
        GROUP BY {fk_column}
    ),
    SeasonStats AS (
        SELECT gr.{fk_column} AS id, COUNT(DISTINCT gp.Season) AS seasons_raced
        FROM GrandPrixResults gr
        JOIN GrandsPrix gp ON gr.grandprixid = gp.ID
        WHERE gr.{fk_column} IS NOT NULL {sub_check}
        GROUP BY gr.{fk_column}
    ),
    Indy500Stats AS (
        SELECT gr.{fk_column} AS id,
               COUNT(*) AS total_races,
               SUM(CASE WHEN gp.GrandPrixName LIKE '%Indianapolis 500' THEN 1 ELSE 0 END) AS indy_races
        FROM GrandPrixResults gr
        JOIN GrandsPrix gp ON gr.grandprixid = gp.ID
        WHERE gr.{fk_column} IS NOT NULL
        GROUP BY gr.{fk_column}
    )
    {champ_cte}

    UPDATE {table_name}
    SET 
        Wins = COALESCE(base.wins, 0),
        Podiums = COALESCE(base.podiums, 0),
        Poles = COALESCE(base.poles, 0),
        FastestLaps = COALESCE(base.fastestlaps, 0),
        SprintWins = COALESCE(base.sprintwins, 0),
        SprintPodiums = COALESCE(base.sprintpodiums, 0),
        SprintPoles = COALESCE(base.sprintpoles, 0),
        SprintFastestLaps = COALESCE(base.sprintfastestlaps, 0),
        Points = COALESCE(base.points, 0),
        Starts = COALESCE(base.starts, 0),
        Entries = COALESCE(base.entries, 0),
        SprintStarts = COALESCE(base.sprintstarts, 0),
        DNFs = COALESCE(base.dnfs, 0),
        BestGridPosition = base.best_grid,
        BestSprintGridPosition = base.best_sprint_grid,
        BestQualifyingPosition = base.best_qual,
        BestRacePosition = base.best_race,
        BestSprintPosition = base.best_sprint,
        BestSprintQualifyingPosition = base.best_sprint_qual,
        SeasonsRaced = COALESCE(ss.seasons_raced, 0),
        {"indy500only = CASE WHEN i.total_races > 0 AND i.total_races = i.indy_races THEN 1 ELSE 0 END," if indy500_check else ""}
        {champ_sets}
        needstatsupdate = 0
    FROM BaseStats base
    LEFT JOIN SeasonStats ss ON base.id = ss.id
    LEFT JOIN Indy500Stats i ON base.id = i.id
    {champ_joins}
    WHERE {table_name}.ID = base.id 
      AND {table_name}.needstatsupdate = 1; 
    """
    
    cur.execute(query)
    print(f"{table_name} stats updated.")


# --- 1. Update Standard Entity Tables ---
update_entity_stats(cur, "Drivers", "driverid", has_substitute_check=True, champ_table="DriversChampionship")
update_entity_stats(cur, "Constructors", "constructorid", champ_table="ConstructorsChampionship")
update_entity_stats(cur, "Engines", "engineid")
update_entity_stats(cur, "Teams", "teamid")
update_entity_stats(cur, "Chassis", "chassisid")
update_entity_stats(cur, "EngineModels", "enginemodelid")
update_entity_stats(cur, "Tyres", "tyreid")
update_entity_stats(cur, "Nationalities", "nationalityid", indy500_check=False)

# --- 2. Update Highly Specific Driver Stats (HatTricks & GrandSlams) ---
# Hat Tricks
cur.execute("""
    UPDATE Drivers
    SET HatTricks = COALESCE(ht.hattricks, 0)
    FROM (
        SELECT driverid, COUNT(*) AS hattricks
        FROM GrandPrixResults
        WHERE raceposition = 1 AND starting_grid_position = 1 AND fastestlap = 1
        GROUP BY driverid
    ) AS ht
    WHERE Drivers.ID = ht.driverid AND Drivers.needstatsupdate = 1;
""")

# Grand Slams (Led Every Lap)
cur.execute("""
    WITH RaceLaps AS (
        SELECT L.grandprixid, L.driverid, COUNT(*) AS total_laps, 
               SUM(CASE WHEN L.position = 1 THEN 1 ELSE 0 END) AS laps_led
        FROM LapByLap L
        JOIN Sessions S ON L.SessionID = S.ID
        WHERE S.SessionName = 'Race'
        GROUP BY L.grandprixid, L.driverid
    ),
    LedEveryLap AS (
        SELECT driverid, grandprixid 
        FROM RaceLaps 
        WHERE total_laps = laps_led AND total_laps > 0
    ),
    GrandSlams AS (
        SELECT g.driverid, COUNT(*) AS grand_slams
        FROM GrandPrixResults g
        JOIN LedEveryLap lel ON g.driverid = lel.driverid AND g.grandprixid = lel.grandprixid
        WHERE g.raceposition = 1 AND g.starting_grid_position = 1 AND g.fastestlap = 1
        GROUP BY g.driverid
    )
    UPDATE Drivers 
    SET GrandSlams = COALESCE(gs.grand_slams, 0)
    FROM GrandSlams gs
    WHERE Drivers.ID = gs.driverid AND Drivers.needstatsupdate = 1;
""")

# --- 3. Update Constructors Specific Stats (1-2 Finishes) ---
cur.execute("""
    UPDATE Constructors
    SET OneTwos = COALESCE(ot.one_twos, 0)
    FROM (
        SELECT constructorid, COUNT(*) AS one_twos
        FROM (
            SELECT constructorid, grandprixid
            FROM GrandPrixResults
            WHERE raceposition IN (1, 2)
            GROUP BY constructorid, grandprixid
            HAVING COUNT(DISTINCT raceposition) = 2
        )
        GROUP BY constructorid
    ) AS ot
    WHERE Constructors.ID = ot.constructorid AND Constructors.needstatsupdate = 1;
""")

# --- 4. Update Laps Led (LapsByLap iteration) ---
def update_laps_led_for_component(cur, component_table, component_id_column):
    query = f"""
    WITH LapsLedStats AS (
        SELECT 
            G.{component_id_column} AS id,
            SUM(CASE WHEN S.SessionName = 'Race' THEN 1 ELSE 0 END) AS gp_laps_led,
            SUM(CASE WHEN S.SessionName IN ('Sprint', 'Sprint Qualifying') THEN 1 ELSE 0 END) AS sprint_laps_led
        FROM LapByLap AS L
        JOIN Sessions AS S ON L.SessionID = S.ID
        JOIN GrandPrixResults AS G ON L.driverid = G.driverid AND L.grandprixid = G.grandprixid
        WHERE L.position = 1 AND G.{component_id_column} IS NOT NULL
        GROUP BY G.{component_id_column}
    )
    UPDATE {component_table}
    SET 
        GrandPrixLapsLed = COALESCE(lls.gp_laps_led, 0),
        SprintLapsLed = COALESCE(lls.sprint_laps_led, 0)
    FROM LapsLedStats lls
    WHERE {component_table}.ID = lls.id AND {component_table}.needstatsupdate = 1;
    """
    cur.execute(query)

cur.execute("SELECT Season FROM Seasons WHERE needstatsupdate = 1 ORDER BY Season")
seasons_to_update = [row[0] for row in cur.fetchall()]

if seasons_to_update:
    max_season = max(seasons_to_update)

    cur.execute("SELECT Season, COUNT(*) FROM GrandsPrix WHERE Season <= ? GROUP BY Season", (max_season,))
    grand_prix_counts = {row[0]: row[1] for row in cur.fetchall()}

    cur.execute("""
        SELECT gp.Season,
               gr.driverid, gr.constructorid, gr.engineid, gr.teamid, gr.enginemodelid,
               gr.chassisid, gr.nationalityid, gr.raceposition, gr.qualifyingposition,
               gr.fastestlap, gr.racepoints, gr.sprintpoints, gr.sprintposition,
               gr.sprint_qualifyingposition, gr.sprintfastestlap
        FROM GrandPrixResults gr
        JOIN GrandsPrix gp ON gr.grandprixid = gp.ID
        WHERE gp.Season <= ?
    """, (max_season,))

    season_stats = {}
    def add_if_not_none(target_set, value):
        if value is not None:
            target_set.add(value)

    for (season, driverid, constructorid, engineid, teamid, enginemodelid,
         chassisid, nationalityid, raceposition, qualifyingposition, fastestlap,
         racepoints, sprintpoints, sprintposition, sprint_qualifyingposition,
         sprintfastestlap) in cur.fetchall():
        
        stats = season_stats.setdefault(season, {
            'drivers': set(), 'constructors': set(), 'engines': set(), 'teams': set(),
            'enginemodels': set(), 'chassis': set(), 'nationalities': set(),
            'unique_winners': set(), 'unique_podiums': set(), 'unique_poles': set(),
            'unique_fastest': set(), 'points_scorers': set(),
            'unique_sprint_winners': set(), 'unique_sprint_podiums': set(),
            'unique_sprint_poles': set(), 'unique_sprint_fastest': set(),
            'sprint_points_scorers': set()
        })

        add_if_not_none(stats['drivers'], driverid)
        add_if_not_none(stats['constructors'], constructorid)
        add_if_not_none(stats['engines'], engineid)
        add_if_not_none(stats['teams'], teamid)
        add_if_not_none(stats['enginemodels'], enginemodelid)
        add_if_not_none(stats['chassis'], chassisid)
        add_if_not_none(stats['nationalities'], nationalityid)

        if raceposition == 1: add_if_not_none(stats['unique_winners'], driverid)
        if raceposition is not None and raceposition <= 3: add_if_not_none(stats['unique_podiums'], driverid)
        if qualifyingposition == 1: add_if_not_none(stats['unique_poles'], driverid)
        if fastestlap == 1: add_if_not_none(stats['unique_fastest'], driverid)
        if (racepoints or 0) > 0 or (sprintpoints or 0) > 0: add_if_not_none(stats['points_scorers'], driverid)
        
        if sprintposition == 1: add_if_not_none(stats['unique_sprint_winners'], driverid)
        if sprintposition is not None and sprintposition <= 3: add_if_not_none(stats['unique_sprint_podiums'], driverid)
        if sprint_qualifyingposition == 1: add_if_not_none(stats['unique_sprint_poles'], driverid)
        if sprintfastestlap == 1: add_if_not_none(stats['unique_sprint_fastest'], driverid)
        if (sprintpoints or 0) > 0: add_if_not_none(stats['sprint_points_scorers'], driverid)

    prior_sets = {
        'points_scorers': set(), 'winners': set(), 'podiums': set(), 'poles': set(),
        'fastest': set(), 'sprint_winners': set(), 'sprint_podiums': set(),
        'sprint_poles': set(), 'sprint_fastest': set(), 'sprint_points_scorers': set()
    }

    for season in sorted(seasons_to_update):
        stats = season_stats.get(season, {
            'drivers': set(), 'constructors': set(), 'engines': set(), 'teams': set(),
            'enginemodels': set(), 'chassis': set(), 'nationalities': set(),
            'unique_winners': set(), 'unique_podiums': set(), 'unique_poles': set(),
            'unique_fastest': set(), 'points_scorers': set(),
            'unique_sprint_winners': set(), 'unique_sprint_podiums': set(),
            'unique_sprint_poles': set(), 'unique_sprint_fastest': set(),
            'sprint_points_scorers': set()
        })

        cur.execute("""
            UPDATE Seasons SET
                TotalGrandPrix = ?, TotalDrivers = ?, TotalConstructors = ?, TotalEngines = ?,
                TotalTeams = ?, TotalEngineModels = ?, TotalChassis = ?, TotalNationalities = ?,
                TotalUniqueWinners = ?, TotalUniquePodiumFinishers = ?, TotalUniquePolePositions = ?, TotalUniqueFastestLapGetters = ?,
                TotalDriversWithPoints = ?, TotalFirstTimePointsScorers = ?, TotalFirstTimeWinners = ?, TotalFirstTimePodiumFinishers = ?,
                TotalFirstTimePoleSitters = ?, TotalFirstTimeFastestLapGetters = ?, TotalUniqueSprintWinners = ?, TotalUniqueSprintPodiumFinishers = ?,
                TotalUniqueSprintPolePositions = ?, TotalUniqueSprintFastestLapGetters = ?, TotalDriversWithSprintPoints = ?, TotalFirstTimeSprintWinners = ?,
                TotalFirstTimeSprintPodiumFinishers = ?, TotalFirstTimeSprintPoleSitters = ?, TotalFirstTimeSprintFastestLapGetters = ?, TotalFirstTimeSprintPointsScorers = ?
            WHERE Season = ?
        """, (
            grand_prix_counts.get(season, 0), len(stats['drivers']), len(stats['constructors']), len(stats['engines']),
            len(stats['teams']), len(stats['enginemodels']), len(stats['chassis']), len(stats['nationalities']),
            len(stats['unique_winners']), len(stats['unique_podiums']), len(stats['unique_poles']), len(stats['unique_fastest']),
            len(stats['points_scorers']), len(stats['points_scorers'] - prior_sets['points_scorers']), len(stats['unique_winners'] - prior_sets['winners']), len(stats['unique_podiums'] - prior_sets['podiums']),
            len(stats['unique_poles'] - prior_sets['poles']), len(stats['unique_fastest'] - prior_sets['fastest']), len(stats['unique_sprint_winners']), len(stats['unique_sprint_podiums']),
            len(stats['unique_sprint_poles']), len(stats['unique_sprint_fastest']), len(stats['sprint_points_scorers']), len(stats['unique_sprint_winners'] - prior_sets['sprint_winners']),
            len(stats['unique_sprint_podiums'] - prior_sets['sprint_podiums']), len(stats['unique_sprint_poles'] - prior_sets['sprint_poles']), len(stats['unique_sprint_fastest'] - prior_sets['sprint_fastest']), len(stats['sprint_points_scorers'] - prior_sets['sprint_points_scorers']),
            season
        ))

        prior_sets['points_scorers'].update(stats['points_scorers'])
        prior_sets['winners'].update(stats['unique_winners'])
        prior_sets['podiums'].update(stats['unique_podiums'])
        prior_sets['poles'].update(stats['unique_poles'])
        prior_sets['fastest'].update(stats['unique_fastest'])
        prior_sets['sprint_winners'].update(stats['unique_sprint_winners'])
        prior_sets['sprint_podiums'].update(stats['unique_sprint_podiums'])
        prior_sets['sprint_poles'].update(stats['unique_sprint_poles'])
        prior_sets['sprint_fastest'].update(stats['unique_sprint_fastest'])
        prior_sets['sprint_points_scorers'].update(stats['sprint_points_scorers'])

print("Seasons stats updated.")

update_laps_led_for_component(cur, "Constructors", "constructorid")
update_laps_led_for_component(cur, "Engines", "engineid")
update_laps_led_for_component(cur, "Chassis", "chassisid")
update_laps_led_for_component(cur, "EngineModels", "enginemodelid")
update_laps_led_for_component(cur, "Tyres", "tyreid")
update_laps_led_for_component(cur, "Teams", "teamid")
update_laps_led_for_component(cur, "Drivers", "driverid")
update_laps_led_for_component(cur, "Nationalities", "nationalityid")
print("GrandPrixLapsLed and SprintLapsLed stats updated.")

# Flip all things back to 0 now that we're done
tables_to_reset = ["Drivers", "Constructors", "Engines", "Chassis", "EngineModels", "Tyres", "Teams", "Nationalities", "Seasons"]
for table in tables_to_reset:
    cur.execute(f"UPDATE {table} SET needstatsupdate = 0 WHERE needstatsupdate = 1")

conn.commit()
print("All stats successfully updated and flags reset.")

update_schedules(cur)
conn.commit()
print("Schedule updated.")

update_circuit_markers(cur)
conn.commit()
print("Circuit markers updated.")

print("Running database optimization (this may take a moment)...")
cur.execute("VACUUM")
print("Database optimization complete.")

conn.close()
#fi.close()
