"""
Backfill or catch up telemetry (issue #4).

writedb.py downloads telemetry for every new Grand Prix, but TracingInsights
publishes about 30 minutes after a session, and older seasons predate the
feature. This script fills those gaps. It skips anything already indexed, so it
is safe to re-run or interrupt.

Run from the repository root (it opens sessionresults.db in the current folder):

    python updaters/update_telemetry.py 2026                       # one season
    python updaters/update_telemetry.py "2026 Australian Grand Prix"   # one race
    python updaters/update_telemetry.py all                        # every season from 2018

Full backfill is several GB of Parquet files, so it is never the default.
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scrapers.telemetry_scraper import FIRST_SEASON, save_telemetry  # noqa: E402

if len(sys.argv) < 2:
    raise SystemExit(__doc__)

target = " ".join(sys.argv[1:]).strip()
conn = sqlite3.connect("sessionresults.db")
cur = conn.cursor()

if target.lower() == "all":
    cur.execute("SELECT ID, Season, GrandPrixName FROM GrandsPrix WHERE Season >= ? ORDER BY ID", (FIRST_SEASON,))
elif target.isdigit():
    cur.execute("SELECT ID, Season, GrandPrixName FROM GrandsPrix WHERE Season = ? ORDER BY ID", (int(target),))
else:
    cur.execute("SELECT ID, Season, GrandPrixName FROM GrandsPrix WHERE GrandPrixName = ? ORDER BY ID", (target,))
races = cur.fetchall()
if not races:
    raise SystemExit(f"No Grand Prix found for '{target}'")

total = 0
for grandprix_id, season, name in races:
    if season < FIRST_SEASON:
        continue
    total += save_telemetry(cur, season, name, grandprix_id)
    conn.commit()  # keep progress if interrupted

conn.close()
print(f"Telemetry update finished: {total} files written.")
