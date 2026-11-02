If you're an F1 nerd like me, sometimes questions arise in your mind:

  - When was the last time two people shared a car in a Grand Prix?

  - Both the Toyotas were on dry tyres at the end of the 2008 Brazilian Grand Prix, so how did Glock's lap times compare to Trulli's?

  - What were the fastest and slowest lap times ever?

  - How many laps have been raced in Formula One history?

  - How much distance has Formula One raced? 

  - What was the race with the most shared cars ever?

  - How many laps in Formula One have been under the safety car/virtual safety car?

Some of this questions cannot be answered by search engines unfortunately.

So, if you're a programmer like me, your answer is just a few SELECTs away.

Or, you can do some cool data analysis with all this data.

---


This is the most comprehensive F1 database with:
- **Every World Championship Grand Prix** result since 1950.
- **Detailed lap-by-lap data** positions from 1950 onwards, lap times from 1996 onwards, sector times, tyre compounds, and stints from 2018 onwards.
- **Full session coverage**: Complete results for Practice, Qualifying, Sprints, and Races.
- **Accurate historical data**: Includes shared car results, pre-qualifying, and vintage scoring systems.
- **Pit stop summary**: Includes pit stop data from 1983 onwards, including pit stop times from 2018 onwards.
- **Technical Regulations**: Season-by-season rules on engines, weight, fuel, and more.
- **Circuit Metadata**: coordinates, elevation, country, and SVG layout paths for every circuit configuration in history.
- **Comprehensive Penalties**: A full database of penalties with official reasons and serving types.
- **In-Season Progress**: Track the championship standings race-by-race.
- **Race Reports**: Narrative reports for every Grand Prix, from Wikipedia.

---
#### Note:
This database has every single Grand Prix part of the _World Championship_. It does not contain non-Championship Grands Prix. When the word "Grand Prix" is used here, it refers to a World Championship Grand Prix. Similarly, when the word "Formula One", "F1", "Formula 1", or any of its variants have been used here, it refers to the _World Championship_, even though the 1952 and 1953 seasons were run to F2 regulations. The terms "Formula One", "Grand Prix", and "World Championship" are used synonymously.

## Table of Contents
- [Requirements](#requirements)
- [Download the latest version](#download-the-latest-version)
- [Update the database](#update-the-database)
- [Update race reports only](#update-race-reports-only)
- [Reset/initialise the database](#resetinitialise-the-database)
- [Delete a specific season](#delete-a-specific-season)
- [Delete a specific race](#delete-a-specific-race)
- [Update a specific race](#update-a-specific-race)
- [Tables](#tables)
  1. [Seasons](#1-seasons)
  2. [Circuits](#2-circuits)
  3. [CircuitLayouts](#3-circuitlayouts)
  4. [GrandsPrix](#4-grandsprix)
  5. [Sessions](#5-sessions)
  6. [Drivers](#6-drivers)
  7. [Teams](#7-teams)
  8. [Constructors](#8-constructors)
  9. [Engines](#9-engines)
  10. [Tyres](#10-tyres)
  11. [Chassis](#11-chassis)
  12. [EngineModels](#12-enginemodels)
  13. [GrandPrixResults](#13-grandprixresults)
  14. [PitStopSummary](#14-pitstopsummary)
  15. [LapByLap](#15-lapbylap)
  16. [DriversChampionship](#16-driverschampionship)
  17. [ConstructorsChampionship](#17-constructorschampionship)
  18. [InSeasonProgressDrivers](#18-inseasonprogressdrivers)
  19. [InSeasonProgressConstructors](#19-inseasonprogressconstructors)
  20. [Nationalities](#20-nationalities)
  21. [RaceReports](#21-racereports)
  22. [MaxSpeeds](#22-maxspeeds)
  23. [RaceControlMessages](#23-racecontrolmessages)
  24. [WeatherData](#24-weatherdata)
  25. [HistoricalWeather](#25-historicalweather)
  26. [FamilyRelations](#26-familyrelations)
  27. [ScheduleVersions](#27-scheduleversions)
  28. [Schedule](#28-schedule)
  29. [ScheduleSessions](#29-schedulesessions)
  30. [CircuitMarkers](#30-circuitmarkers)
  31. [RedFlags](#27-redflags)
  32. [TelemetryIndex](#32-telemetryindex)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [Code of Conduct](https://github.com/mclarenmp4-22/f1resultsdatabase/blob/main/CODE_OF_CONDUCT.md)
- [Licensing & Legal Terms](#-licensing--legal-terms)
- [Disclaimer & Limitation of Liability](#-disclaimer--limitation-of-liability)

---


## Requirements:
Install all the requirements using:
```bash
pip install -r requirements.txt
```

### Optional AI dependencies (circuit layouts & engine models)
The base database builds without AI. A single `F1_AI_TIER` environment variable controls the local models used by both scrapers. It accepts `none`, `low`, `medium`, or `high`, and defaults to `none` when unset:

| Tier | Engine matching | Circuit layout OCR |
|---|---|---|
| `none` | Deterministic matching; unresolved historical matches stay for review. | CPU image processing and Tesseract only. |
| `low` | `qwen3-vl:2b` | `qwen3-vl:2b` plus Tesseract. |
| `medium` | `llama3.1:8b` | Current multi-reader pipeline: `qwen3-vl:4b`, `minicpm-v4.6:1b`, PaddleOCR-VL, docTR, EasyOCR, and TrOCR. |
| `high` | `llama3.1:70b` | Current multi-reader pipeline with `qwen3-vl:8b` as the map reader. |

To select a tier for the current PowerShell session, set:

```powershell
$env:F1_AI_TIER = "medium"
```

To persist it for your Windows user account, run:

```powershell
[Environment]::SetEnvironmentVariable("F1_AI_TIER", "medium", "User")
```

Restart PowerShell after setting a user-level value. Model selection does not silently downgrade: the scraper reports the exact `ollama pull` commands if a selected model is missing. Tiers `low`, `medium`, and `high` require Ollama and a running local Ollama service.

The base database builds without these, but two columns can use local AI models:
- `CircuitLayouts.SVG` are traced from StatsF1 layout maps by `scrapers/circuit_layout_scraper.py`, which reads corner names and turn numbers off the map image with vision/OCR models.
- `EngineModels.StatsF1Data` is matched to the right StatsF1 engine entry by `scrapers/scrape_engine_models.py`, which uses an LLM as its last-resort matcher (after era, team-usage, and fuzzy matching).

Tier `none` still needs the Tesseract binary and Latin tessdata for circuit labels, but does not need Ollama or neural OCR packages. Pull the models required by your selected tier:

1. **Ollama** (https://ollama.com):
```bash
ollama pull qwen3-vl:2b
ollama pull qwen3-vl:4b
ollama pull qwen3-vl:8b
ollama pull openbmb/minicpm-v4.6:1b
ollama pull llama3.1:8b
ollama pull llama3.1:70b
```

2. **Medium and High OCR extras** (all in `requirements.txt`, plus one binary):
- `PaddleOCR-VL-1.6` (`PaddlePaddle/PaddleOCR-VL-1.6` via `transformers`), `python-doctr` (DBNet + PARSeq), `easyocr`, and `microsoft/trocr-large-printed` (via `transformers`). These plus qwen form the three first readers and the Tesseract/EasyOCR/TrOCR/minicpm arbiter stage in `read_map_labels`.
- The Tesseract binary (e.g. `winget install UB-Mannheim.TesseractOCR`) and the `tessdata_best` Latin model: place `script/Latin.traineddata` in `assets/tessdata/` (this path is gitignored). The scraper looks for `tesseract` on PATH, else `C:\Program Files\Tesseract-OCR\tesseract.exe`.
- `torch`/`torchvision`: install from the PyTorch CUDA index if you have an NVIDIA GPU (the pipeline was tuned for ~6 GB VRAM; models are parked to CPU between maps to free VRAM for qwen). CPU works but is much slower.

3. **StatsF1 rate limit**: both scrapers sleep between requests (up to ~20 s for engine pages, 4–15 s for circuit pages/images) and abort loudly on an IP block. Do not shorten the sleeps.

Normal scraping preserves existing circuit layout SVGs and metadata, updating only their usage dates. It generates a layout only when a new version is found.

## Download the latest version:
To download the latest version of the database, please go to [GitHub Releases](https://github.com/mclarenmp4-22/f1resultsdatabase/releases/latest) and download the latest version.

**Last updated: 2026 Italian Grand Prix**

## Update the database:
If you want to update the database, all you need to do is run this command:
```bash
python writedb.py
```

Car telemetry (see `TelemetryIndex`) is downloaded automatically for every new race, which adds a few minutes per race weekend. To skip it, for example to save disk space:
```bash
python writedb.py --notelemetry
```

To download telemetry for races that are already in the database, or for sessions that TracingInsights had not published yet when you ran `writedb.py`:
```bash
python updaters/update_telemetry.py 2026
```
This accepts a season, a full Grand Prix name such as `"2026 Australian Grand Prix"`, or `all` for every season since 2018 (several GB). Anything already downloaded is skipped, so it is safe to re-run or interrupt.

## Update race reports only:
If you only want to refresh race reports in the `RaceReports` table, you can use:
```bash
python writedb.py --updateracereport
```
It must be noted that the Wikipedia REST API only supports 500 requests per hour so you might hit rate limits.

To update race reports for a single season:
```bash
python writedb.py --updateracereport 2025
```

or:
```bash
python writedb.py --updateracereport season 2025
```

To update race reports from a season onward:
```bash
python writedb.py --updateracereport from 2025
```

or:
```bash
python writedb.py --updateracereport from season 2025
```

To update just one race report:
```bash
python writedb.py --updateracereport "2026 Japanese Grand Prix"
```

or:
```bash
python writedb.py --updateracereport race "2026 Japanese Grand Prix"
```

To update race reports from a specific race onward:
```bash
python writedb.py --updateracereport from "2026 Japanese Grand Prix"
```

or:
```bash
python writedb.py --updateracereport from race "2026 Japanese Grand Prix"
```

Race names are matched case-insensitively. This command updates only the `RaceReports` table and does not run the full database update flow.

## Reset/initialise the database:
If, for whatever reason, you want to wipe out all the data in the database or you want to create the database with all the tables and columns, run this command:
```bash
python updaters/reset.py
```

## Delete a specific season:
If you want to delete data for a single season (e.g. to re-scrape it), run:
```bash
python updaters/deleteseason.py <year>
```
This will remove all races, results, and standings for that year while keeping the rest of the database intact.

## Delete a specific race:
If you want to delete data for a single race (e.g. to re-scrape it), run:
```bash
python updaters/deleterace.py <race_name>
```

#### Note: This is meant to be a debugging tool only to fix bugs and does not delete all tables containing data for that specific season or Grand Prix. 

## Update a specific race:
Results change after the chequered flag: post-race penalties, disqualifications, appeals, or StatsF1 correcting its data. `writedb.py` only scrapes Grands Prix newer than the last one in the database, so to re-sync an older race with StatsF1 run:
```bash
python updaters/updaterace.py "2026 Monaco Grand Prix"
```
This re-scrapes the race (and the sprint, on a sprint weekend) of that one Grand Prix and rewrites:
- `GrandPrixResults`: classification, fastest laps and penalties columns.
- `GrandsPrix`: `RaceResultNotes` and `SprintNotes`.
- `InSeasonProgressDrivers` and `InSeasonProgressConstructors`: for that Grand Prix and every later Grand Prix of the season already in the database.
- `DriversChampionship` and `ConstructorsChampionship`: for the season.
- Derived statistics (`Drivers`, `Constructors`, `Seasons`, ...) by running `updaters/update_stats_alone.py` afterwards.

The standings are not scraped. For every season in which all scores count (1991 onwards) the points that changed are applied to the standings as deltas, positions are re-ranked (tied drivers share a position in the in-season tables; the championship tables break ties on points and then countback of race finishing positions, as StatsF1 does) and the championship's mathematical locks are re-applied. Historical deductions and exclusions stored from StatsF1 are left untouched. Seasons with dropped scores or top-car-only constructor points (1950 to 1990) are re-scraped from StatsF1 instead.

Every change is printed as `column: old -> new`, and everything is written in one transaction, so a failure part-way through writes nothing. Race names are matched case-insensitively.

Options:
- `--dry-run` scrapes and prints every change but writes nothing.
- `--skip-stats` skips the statistics recomputation; the affected entities stay flagged with `needstatsupdate = 1` and are picked up by the next `writedb.py` run.
- `--results-only` rewrites only `GrandPrixResults` and `GrandsPrix`. Use it only when the change cannot affect points, for example a corrected retirement reason.

Lap-by-lap data, pit stops, sessions and the race report are not touched, as they do not change when a result is reclassified. Refresh the race report with `python writedb.py --updateracereport "2026 Monaco Grand Prix"`.

## Circuit layouts and engine models (LLM/OCR scrapers):
Both run automatically inside `python writedb.py` for new circuits/engines (`generate_track_svg` + `parse_circuit_metadata` per new layout; `scrape_pending_engine_models` for new engine makes). You only need the standalone commands to backfill or to re-run a single make/layout.

Circuit layouts (`CircuitLayouts.SVG`, `TrackDirection`, `OfficialCircuitName`, `TrackType`):
- Source: the StatsF1 circuit page map (`GetImage.ashx?id=piste.xxx`) plus the page's `circuittype` box.
- Pipeline per new map in `generate_track_svg`: template-match the chequered flag and walk its leader line to the track for the start/finish bar; detect the red direction arrow(s) by colour/shape and read them against the track outline; trace the track into SVG; read labels according to `F1_AI_TIER`. Existing circuit versions have their usage dates refreshed without regenerating their SVG or metadata.
- Run the backfill directly:
```bash
python scrapers/circuit_layout_scraper.py
```
This walks the StatsF1 circuits index, updates usage dates for existing layout versions, and generates rows only for new versions. It needs the `assets/flag_template.png` asset and Tesseract; Ollama and the OCR stack depend on the selected `F1_AI_TIER`.

Engine models (`EngineModels.StatsF1Data`):
- Source: `https://www.statsf1.com/en/moteur-{slug}.aspx` per engine make, French keys/values translated to English with `deep_translator`.
- Matching per StatsF1 entry, in order: hardcoded multi-row aliases (`HARDCODED_MULTI_MATCH`), single candidate in the entry's era, team-usage overlap in the overlapping seasons, fuzzy match on the short code, then the model selected by `F1_AI_TIER` as a last resort. The `none` tier leaves unresolved entries for review; AI-tier runs abort loudly on unmatched entries rather than writing partial data.
- Run the backfill directly (from the repo root; defaults to `sessionresults.db`):
```bash
python scrapers/scrape_engine_models.py
```
Only rows with `StatsF1Data IS NULL` are scraped. Ollama is required only for `low`, `medium`, and `high` tiers. Expect long sleeps between makes — do not remove them; StatsF1 will IP-block you.


## Tables

#### Note: 
- For boolean values, SQLite takes `1` to be `true` and `0` to be `false`. `true` and `false` used here refers to `1` and `0`.
- "Entries" used here refers to a driver being on the entry list for a Grand Prix, even if the driver did not take part in any of the sessions, it gets counted as an "entry" for the driver, the constructor, the team, the engine make, the engine model, the chassis, and the tyre supplier.

1. ### Seasons
   This table contains a set of all seasons in F1, from 1950 to the present day.  
   **Columns:**
   - **Season**: The year (e.g. 1982). _INTEGER PRIMARY KEY_
   - **DriversRacesCounted**: How many races counted for drivers. _TEXT_ 

   From 1950 till 1990, you could drop your worst results. In other words, say, for a season, only the points collected for a driver's best 11 races would count towards the championship. It could also be like the best 6 races from the first 7 races, and the best 7 results from the last eight races counted towards the championship.

   Values can range from "Best 11 scores." to 
   
   "Best 5 scores from first 7 races.

   Best 5 scores from last 7 races."

   to "All scores and points are doubled for the last Grand Prix." for the 2014 season. If all races are counted, the value is "All scores."
   - **PointsSharedForSharedCars**: Whether points were shared for shared cars. _BOOLEAN_

   From 1950 till 1957, drivers who shared the same car would have their points shared proportionate to the laps they drove. In other words, Driver A could do 20 laps in a Grand Prix and Driver B would do the other 40 laps. Points would be shared accordingly to both drivers, although it would get a bit complicated. To see an example of this, you may see the results of the [1955 Argentine Grand Prix](https://www.formula1.com/en/results/1955/races/135/argentina/race-result).

   The value is ```true``` if points were shared for shared cars and ```false``` if points were not shared for shared cars.
   - **GrandPrixPointsSystemDrivers**: Points system for drivers. (JSON) _TEXT_
   
   For any row, the data is stored in the following format:

   **Keys**:
   Position (as a string, e.g, "1")/ "Fastest Lap"/ "Fastest Lap (only for finishing in the top 10)" (2019-2024, where the fastest lap point was only awarded for finishing in the top ten)

   **Values**: the points alloted for that specific position or fastest lap.

   For the 1959 season, the value would be:
   ``` 
   {"1": 8, "2": 6, "3": 4, "4": 3, "5": 2, "Fastest Lap": 1}
   ```

   - **SprintPointsSystemDrivers**: Sprint points system for drivers. (JSON) _TEXT_

   For any row, the data is stored in the following format:

   **Keys**:
   Position (as a string, e.g, "1")

   **Values**: the points alloted for that specific position.

   For the 2025 season, the value would be:
   ```
   {"1": 8, "2": 7, "3": 6, "4": 5, "5": 4, "6": 3, "7": 2, "8": 1}
   ```

   - **ConstructorsRacesCounted**: How many races counted for constructors. _TEXT_

   From 1958 (the first year the Constructors' Championship was awarded) till 1978, you could drop your worst results. In other words, say, for a season, only the points collected for a constructor's best 11 races would count towards the championship. It could also be like the best 6 races from the first 7 races, and the best 7 results from the last eight races counted towards the championship.   

   Values can range from "Best 11 scores." to 
   
   "Best 5 scores from first 7 races.

   Best 5 scores from last 7 races."
   to "All scores and points are doubled for the last Grand Prix." for the 2014 season. If all races are counted, the value is "All scores."   
   - **PointsOnlyForTopScoringCar**: Only top scoring car counted for constructors. _BOOLEAN_

   From 1958 till 1978 only the highest finishing car of a Constructor would be awarded points. For example, if the cars of a particular Constructor finished fourth and fifth, only the points scored for fourth place would count towards the Constructors' Championship.

   The value is ```true``` if points were only given towards the Constructors' Championship for the highest placed car and ```false``` if points were not given towards the Constructors' Championship for the highest placed car.   

   - **GrandPrixPointsSystemConstructors**: Points system for constructors. (JSON) _TEXT_

   It is important to note that the same points system wasn't always used for the Drivers' and Constructors' Championship. In 1959 and 1960, the point for Fastest Lap didn't count towards the Constructors' Championship, hence the necessity for this column.

   For any row, the data is stored in the following format:

   **Keys**:
   Position (as a string, e.g, "1")/ "Fastest Lap (only for finishing in the top 10)" (2019-2024, where the fastest lap point was only awarded for finishing in the top ten)

   **Values**: the points alloted for that specific position or fastest lap.

   For the 2024 season, the value would be:
   ``` 
   {"1": 8, "2": 7, "3": 6, "4": 5, "5": 4, "6": 3, "7": 2, "8": 1}
   ```
   - **SprintPointsSystemConstructors**: Sprint points system for constructors. (JSON) _TEXT_

   For any row, the data is stored in the following format:

   **Keys**:
   Position (as a string, e.g, "1")

   **Values**: the points alloted for that specific position.

   For the 2021 season, the value would be:
   ```
   {"1": 3, "2": 2, "3": 1}
   ```
  
   - **RegulationNotes**: Notes about regulations for that season. (JSON) _TEXT_

   The notes are in the form of an array. For the 2020 season, the value would be:
   ```
   ["On 7 August 2020, following a protest by Renault at the Styria GP, the International Automobile Federation's stewards withdrew 15 points from Racing Point for copying the design of Mercedes' brake scoops.", "Qualifying is divided into 3 sessions.", "At the end of the first 18-minute session (Q1), the 5 slowest drivers are eliminated and placed on positions 16 to 20.", "At the end of the second 15-minute session (Q2), the 5 slowest drivers are eliminated and placed on positions 11 to 15.", "A final session (Q3) lasting 12 minutes determines positions 1 to 10 for the 10 remaining drivers.", "At the end of Q1, drivers whose time exceeds 107% of the best time do not qualify."]
   ```
   - **MinimumWeightofCars**: Minimum car weight. _TEXT_

   The values can vary from  "500 kg" to "500 kg (naturally aspirated) or 540 kg (supercharged)" or even "595 kg with driver". It does not get into too much detail about how much weight must be shared by the front and rear tyres, how much the driver and the seat alone must weigh, and so on. For seasons with no weight limit, the value is "free".
   - **EngineType**: Engine type. _TEXT_

   For the turbo-hybrid era, the value is "hybrid, reciprocating 4-stroke with pistons and electric". For previous seasons, the value wold be "reciprocating 4-stroke with pistons". If there is no restriction to the type of engine, the value would be "free".
   - **Supercharging**: Whether forced induction (supercharging or turbocharging) is allowed for that season. _TEXT_

   For seasons where it is allowed, the value would be "authorized". For seasons where it is not allowed, the value would be "forbidden". For 1986, where every single team had turbos, even though there was no rule mandating turbos, the value is "imposed". From 2014 onwards, where turbos are mandatory, the value would be "madatatory".
   - **MaxCylinderCapacity**: Maximum engine displacement. Also can be the allowed engine displacement. _TEXT_

   Values can range from "1500 cc" to "3000 cc (naturally aspirated) or 1500 cc (supercharged)".
   - **NumberOfCylinders**: Number of cylinders, or engine configuration. _TEXT_

   For seasons with no restrictions, the value would be "free". For seasons with a cap on maximum number of cylinders, the value would be, for example, "maximum 12". For seasons where the configuration was mandated, the value could be "V10". For 2006, where every other team except Toro Rosso had V8s, because the FIA granted special permission for Toro Rosso to continue using V10s, the value would be "V8 or V10".
   - **MaxRPM**: Maximum RPM the engine can rev to. _TEXT_

   For seasons where there was no limit on the maximum RPM of the engine, the value would be "free". Otherwise, the value would be the maximum RPM, for example "19 000".
   - **NumberOfEnginesAllowedPerSeason**: Engine allocation per season. _TEXT_

   The values can range from "1 engine per Grand Prix" to  "1 engine for 2 Grand Prix" to "3 per season". For seasons with no cap on the number of engines used, the value would be "free".
   - **FuelType**: Fuel type. _TEXT_

   For seasons where teams could use any type of fuel they wanted, the value would be "free". Otherwise it would tell the type of fuel that the teams could use.
   - **RefuellingAllowed**: Whether refuelling was allowed during a race. _TEXT_

   For seasons where refuelling was allowed, the value would be "authorized". For seasons where refuelling was not allowed, the value would be "forbidden".
   - **MaxFuelConsumption**: Maximum fuel consumption. _TEXT_

   For seasons where there was no limit, the value would be "free". The values can also range from "200 litres" to "free (naturally aspirated) or 195 litres (supercharged)" to "100 kg and 100 kg/hour". In the last example, the first part ("100 kg") shows the fuel tank capacity (the maximum amount of fuel consumed per race) annd the second part ("100 kg/hour") shows the fuel flow limit. For the 2026 season, it is "3000 MJ/hour".
   - **TotalGrandPrix**: Total number of World Championship Grands Prix held in that season. _INTEGER DEFAULT 0_
   - **TotalDrivers**: Total number of drivers to have entered a World Championship Grand Prix in that season. _INTEGER DEFAULT 0_
   - **TotalConstructors**: Total number of constructors to have entered a World Championship Grand Prix in that season. _INTEGER DEFAULT 0_
   - **TotalEngines**: Total number of engine manufacturers to have powered a World Championship Grand Prix entry in that season. _INTEGER DEFAULT 0_
   - **TotalTeams**: Total number of teams to have entered a World Championship Grand Prix in that season. _INTEGER DEFAULT 0_
   - **TotalEngineModels**: Total number of engine models used in World Championship Grands Prix in that season. _INTEGER DEFAULT 0_
   - **TotalChassis**: Total number of chassis to have entered a World Championship Grand Prix in that season. _INTEGER DEFAULT 0_
   - **TotalNationalities**: Total number of nationalities represented by drivers in that season. _INTEGER DEFAULT 0_
   - **TotalUniqueWinners**: Total number of different drivers to win at least one race in that season. _INTEGER DEFAULT 0_
   - **TotalFirstTimeWinners**: Total number of drivers to score their first-ever race win in that season. _INTEGER DEFAULT 0_
   - **TotalUniquePodiumFinishers**: Total number of different drivers to finish on the podium (1st-3rd) at least once in that season. _INTEGER DEFAULT 0_
   - **TotalFirstTimePodiumFinishers**: Total number of drivers to score their first-ever podium finish in that season. _INTEGER DEFAULT 0_
   - **TotalUniquePolePositions**: Total number of different drivers to secure pole position at least once in that season. _INTEGER DEFAULT 0_
   - **TotalFirstTimePoleSitters**: Total number of drivers to score their first-ever pole position in that season. _INTEGER DEFAULT 0_
   - **TotalUniqueFastestLapGetters**: Total number of different drivers to set the fastest lap at least once in that season. _INTEGER DEFAULT 0_
   - **TotalFirstTimeFastestLapGetters**: Total number of drivers to score their first-ever fastest lap in that season. _INTEGER DEFAULT 0_
   - **TotalDriversWithPoints**: Total number of different drivers to score championship points in that season. _INTEGER DEFAULT 0_
   - **TotalFirstTimePointsScorers**: Total number of drivers to score championship points for the first time in that season. _INTEGER DEFAULT 0_
   - **needstatsupdate**: Internal flag for statistical calculations. _BOOLEAN_

2. ### Circuits
   This table contains all circuits which have hosted a Grand Prix.  
   #### Note: If a circuit changes its name, it is counted as a different circuit.
   **Columns:**
   - **ID**: Unique circuit ID. _INTEGER PRIMARY KEY AUTOINCREMENT_
   - **CircuitName**: Name of the circuit. _TEXT_
   - **FirstGrandPrix**: First Grand Prix held in that circuit. _TEXT_
   - **LastGrandPrix**: Last Grand Prix held in that circuit. _TEXT_
   - **GrandPrixCount**: Number of GPs held in that circuit. _INTEGER DEFAULT 0_
   - **FirstGrandPrixID**: Foreign key to GrandsPrix. _INTEGER_
   - **LastGrandPrixID**: Foreign key to GrandsPrix. _INTEGER_
   - **Latitude**: GPS Latitude of the circuit. _REAL_
   - **Longitude**: GPS Longitude of the circuit. _REAL_
   - **needstatsupdate**: Internal flag for statistical calculations. _BOOLEAN_

3. ### CircuitLayouts
   This table contains the different track configurations for each circuit used in Grand Prix history.
   **Columns:**
   - **ID**: Unique layout ID. _INTEGER PRIMARY KEY AUTOINCREMENT_
   - **Latitude**: GPS Latitude. _REAL_
   - **Longitude**: GPS Longitude. _REAL_
   - **Elevation**: Terrain elevation from sea level from Open-Meteo, in metres. _REAL_
   - **Country**: Country returned by reverse geocoding the circuit coordinates. _TEXT_
   - **CircuitVersion**: Version number of the layout for the given location. _TEXT_
   - **FirstGrandPrix**: Name of the first GP held on this layout. _TEXT_
   - **LastGrandPrix**: Name of the last GP held on this layout. _TEXT_
   - **GrandPrixCount**: Number of GPs held on this specific layout. _INTEGER DEFAULT 0_
   - **GrandPrixDates**: JSON list of all race dates held on this layout, used for mapping a circuit layout to a Grand Prix.  This is Adelaide, for example:
   ```["19851103", "19861026", "19871115", "19881113", "19891105", "19901104", "19911103", "19921108", "19931107", "19941113", "19951112"]``` _TEXT_
   - **SVG**: SVG drawing of the track layout traced from the StatsF1 map: the track outline, a yellow start/finish bar placed exactly where the map's chequered-flag leader line meets the track, the red direction arrow(s), and the corner names and turn numbers read off the map. The root element carries `data-*` diagnostics (flag match score, start/finish status, arrow count, both direction readings, label counts) used by `generate_circuit_layout_comparison.py`. _TEXT_
   - **CircuitChanges**: Description of changes from the previous version of that circuit. _TEXT_
   - **TrackDirection**: Direction the circuit is raced in, read from the directional arrow(s) on the layout image: `Clockwise`, `Anticlockwise`, or `Both`. A layout image carries a second arrow only when the circuit was raced in both directions, and the two arrows always oppose one another, so two arrows means `Both`. NULL where no arrow could be located. The arrow is found by colour and shape and read against the traced track outline beside it (whether it runs with or against the outline's traversal), with the layout's racing years and a short hand-set override table settling the few maps that cannot be read. Suzuka races in a unique figure-of-eight configuration (partly clockwise and partly anticlockwise). That is stored as `Figure of eight`. _TEXT_
   - **OfficialCircuitName**: The official full name of the circuit as listed by StatsF1, e.g. `Adelaide Street Circuit`. _TEXT_
   - **TrackType**: The type of the circuit venue: `Street Circuit`, `Semi-permanent Circuit`, or `Permanent Circuit`. _TEXT_
   - **FirstGrandPrixID**: Foreign key to GrandsPrix. _INTEGER_
   - **LastGrandPrixID**: Foreign key to GrandsPrix. _INTEGER_
   - **needstatsupdate**: Internal flag for statistical calculations. _BOOLEAN_
   - **Rotation**: Rotation of the circuit in degrees: the angle the circuit's X/Y coordinates need to be rotated by to match the orientation of the official track map. From MultiViewer, like the rows in CircuitMarkers. NULL if there are no markers for the layout. _REAL_
   - **MarkersSourceYear**: Season of the MultiViewer record the layout's markers were taken from. NULL if MultiViewer has no record for a season the layout was raced in. It is also set when no markers were saved for the layout because the record was empty, or identical to that of a later layout of the same circuit. _INTEGER_

4. ### GrandsPrix
   This table contains all Grands Prix.  
   **Columns:**
   - **ID**: Unique Grand Prix ID. The ID corresponds to which Grand Prix it is from the start of the world championship; the Grand Prix with the ID 1000 will be the 1000th world championship Grand Prix (2019 Chinese Grand Prix). _INTEGER PRIMARY KEY_
   - **Season**: Year of the Grand Prix. _INTEGER_
   - **GrandPrixName**: Name of the Grand Prix. This does not refer to the Grand Prix's name alone, i.e. "Monaco Grand Prix". Instead, it refers to the full name of the Grand Prix, e.g. "2021 Monaco Grand Prix". This must be kept in mind for all tables that reference Grand Prix names. _TEXT_
   - **RoundNumber**: Round number in the season. For example, if a race was the seventeenth Grand Prix of the season, the value would be "17".  _INTEGER_
   - **CircuitName**: Name of the circuit. _TEXT_
   - **Country**: Country of the Grand Prix venue. _TEXT_
   - **Date**: Date of the Grand Prix. For example, "Sunday, 2 August 2020" _TEXT_
   - **DateInDateTime**: Date as datetime. _TEXT_
   - **Laps**: Number of laps the race was held. _INTEGER_
   - **CircuitLength**: The length of the circuit in which the race was held. _TEXT_
   - **Offset**: Distance between the start line and the finish line, in metres. This is only available after 2003. In some circuits like Monaco, it may be zero as well. _REAL_
   _**Note**_: The start line here represents the start line of the race, before pole position. The finish line represents the start/finish line (timing loop at the end of the lap). The offset here is the distance between the start line (where the race starts) and the finish line (where the race ends or the start/finish line of the lap).
   - **Weather**: Weather conditions. For example "Sunny", "Overcast", "Cloudy", "Night", "Rain", etc. _TEXT_
   - **Notes**: Notes about the race. _TEXT_
   - **SprintWeekend**: Whether it was a sprint weekend. _BOOLEAN_
   - **CircuitLayoutID**: Foreign key to CircuitLayouts. _INTEGER_
   - **PoleSide**: Side of the track the pole position is on. _TEXT_
   - **GridFormation**: The formation of the starting grid (e.g. 2-2, 3-2-3). _TEXT_
   For example, here's the GridFormation for the 1950 Italian Grand Prix:
   ```4-4-4-4-4-4-4-4-4-4-4-4-3-3```
   - **SprintWeekend** Whether the weekend was a sprint weekend. _BOOLEAN_
   If it is a sprint weekend, the value is `true`, if it is not, the value is `false`.
   - **CircuitID**: Foreign key to Circuits. _INTEGER_
   - **EntrantsNotes**: Notes about the entrants to the race. _TEXT_
   - **QualifyingNotes**: Notes about qualifying. _TEXT_
   - **StartingGridNotes**: Notes about the starting grid. _TEXT_
   - **RaceResultNotes**: Notes about the race result. _TEXT_
   - **SprintNotes**: Notes about the sprint. _TEXT_
   - **SprintGridNotes**: Notes about the sprint grid. _TEXT_
   - **needstatsupdate**: Internal flag for statistical calculations. _BOOLEAN_

5. ### Sessions
   This table contains the schedule for every session (Practice, Qualifying, Sprint, Race) of a Grand Prix.
   **Columns:**
   - **ID**: Unique session ID. _INTEGER PRIMARY KEY AUTOINCREMENT_
   - **GrandPrix**: Name of the Grand Prix. _TEXT_
   - **SessionName**: Name of the session (e.g. "Practice 1", "Qualifying", "Race"). _TEXT_
   - **SessionNumber**: Order of the session in the weekend. _INTEGER_
   - **WasSessionCancelled**: Whether the session was cancelled. _BOOLEAN_
   - **StartTimeTimestampUTC**: Start time UTC timestamp. _INTEGER_
   - **EndTimeTimestampUTC**: End time UTC timestamp. _INTEGER_
   - **StartTimeTimestamp**: Start time local timestamp. _INTEGER_
   - **EndTimeTimestamp**: End time local timestamp. _INTEGER_
   - **IfPreciseStartTime**: Whether the start time is precise. _BOOLEAN_
   - **StartTimeinDatetimeUTC**: Start time in UTC datetime string. _TEXT_
   - **EndTimeinDatetimeUTC**: End time in UTC datetime string. _TEXT_
   - **StartTimeinDatetime**: Start time in local datetime string. _TEXT_
   - **EndTimeinDatetime**: End time in local datetime string. _TEXT_
   - **SessionTimezone**: IANA timezone identifier derived from circuit latitude/longitude (e.g. `Europe/London`). _TEXT_
   - **GrandPrixID**: Foreign key to GrandsPrix. _INTEGER_

6. ### Drivers
   This table contains all drivers who have entered a World Championship Grand Prix (this includes FP1 appearances and  third/substitute drivers).  
   **Columns:**
   - **ID**: Unique driver ID. _INTEGER PRIMARY KEY AUTOINCREMENT_
   - **Name**: Full name of that driver. _TEXT UNIQUE_
   - **Nationality**: Nationality of that driver. _TEXT_
   - **Birthdate**: Birth date of that driver. _TEXT_
   - **Wins**: Number of wins of that driver. _INTEGER_
   - **Podiums**: Number of podiums of that driver. _INTEGER_
   - **Poles**: Number of pole positions of that driver. _INTEGER_
   - **FastestLaps**: Number of fastest laps of that driver. _INTEGER_
   - **SprintWins**: Number of sprint wins of that driver. _INTEGER_
   - **SprintPodiums**: Number of sprint podiums of that driver. _INTEGER_
   - **SprintPoles**: Number of sprint pole positions of that driver. _INTEGER_
   - **SprintFastestLaps**: Number of sprint fastest laps of that driver. _INTEGER_
   - **Championships**: Number of championships of that driver. _INTEGER_
   - **SeasonsRaced**: Number of seasons in which that driver entered at least one World Championship Grand Prix weekend. This does not count FP1 appearances or drivers who were on the entry list but did not participate in the Grand Prix, but even drivers who did not pre-qualify are considered as having raced in that season. _INTEGER_
   - **Points**: Total points of that driver. _REAL_
   - **Starts**: Number of starts of that driver. _INTEGER_
   - **Entries**: Number of entries of that driver. _INTEGER_
   - **SprintStarts**: Number of sprint starts of that driver. _INTEGER_
   - **SprintEntries**: Number of sprint entries of that driver. _INTEGER_
   - **DNFs**: Number of DNFs of that driver. Only classified non-finishes are counted. For example, if a driver did not finish a race but has a classified finish in the race result, it is not counted here. _INTEGER_
   - **GrandPrixLapsLed**: Number of grand prix laps led of that driver. _INTEGER_
   - **SprintLapsLed**: Number of sprint laps led of that driver. _INTEGER_
   - **HatTricks**: Number of hat tricks (pole + win + fastest lap) of that driver. _INTEGER_
   - **GrandSlams**: Number of grand slams (pole + win + fastest lap + led every lap) of that driver. _INTEGER_
   - **BestGridPosition**: Best grid position of that driver. _INTEGER_
   - **BestSprintGridPosition**: Best sprint grid position of that driver. _INTEGER_
   - **BestQualifyingPosition**: Best qualifying position of that driver. _INTEGER_
   - **BestRacePosition**: Best race position of that driver. _INTEGER_
   - **BestSprintPosition**: Best sprint position of that driver. _INTEGER_
   - **BestSprintQualifyingPosition**: Best sprint qualifying position of that driver. _INTEGER_
   - **BestChampionshipPosition**: Best championship position of that driver. _INTEGER_
   - **FirstGrandPrix**: First Grand Prix of that driver. _TEXT_
   - **LastGrandPrix**: Last Grand Prix of that driver. _TEXT_

   FirstGrandPrix and LastGrandPrix does not count FP1 appearances or drivers on the entry list who did not participate in any session. Non-qualifications do count, though.
   - **NationalityID**: Foreign key to Nationalities. _INTEGER_
   - **FirstGrandPrixID**: Foreign key to GrandsPrix. _INTEGER_
   - **LastGrandPrixID**: Foreign key to GrandsPrix. _INTEGER_
   - **Gender**: Gender of that driver. _TEXT_
   - **DeathDate**: If the driver has passed away, the date of death.  _TEXT_,
   - **DeathAge**: If the driver has passed away, the age at death. _INTEGER_,
   - **DeathReason**: If the driver has passed away and reason is available, the cause of death. _TEXT_,
   - **needstatsupdate**: Internal flag for statistical calculations. _BOOLEAN_
   - **indy500only**: Whether this driver only competed in Indianapolis 500 races. Set to 1 (true) if all races ended with "Indianapolis 500", otherwise 0 (false). _BOOLEAN_

7. ### Teams
   This table contains all teams that have entered a Grand Prix. A constructor and a team are two very different things. A constructor is an entity that constructs a chassis for Formula One, while a team can also include private entries from people who don't construct a chassis. In a more modern sense, a constructor is just the chassis name (i.e, Mercedes), while a team also includes the sponsor names (i.e, Mercedes AMG Petronas Formula One Team). The team names are according to the entry list for that race.

   **Columns:**
   - **ID**: Unique team ID. _INTEGER PRIMARY KEY AUTOINCREMENT_
   - **TeamName**: Name of the team. _TEXT UNIQUE_
   - **FirstGrandPrix**: First Grand Prix of that team. _TEXT_
   - **LastGrandPrix**: Last Grand Prix of that team. _TEXT_
   - **Wins**: Number of wins of that team. _INTEGER_
   - **Podiums**: Number of podiums of that team. _INTEGER_
   - **Poles**: Number of pole positions of that team. _INTEGER_
   - **FastestLaps**: Number of fastest laps of that team. _INTEGER_
   - **SprintWins**: Number of sprint wins of that team. _INTEGER_
   - **SprintPodiums**: Number of sprint podiums of that team. _INTEGER_
   - **SprintPoles**: Number of sprint pole positions of that team. _INTEGER_
   - **SprintFastestLaps**: Number of sprint fastest laps of that team. _INTEGER_
   - **SeasonsRaced**: Number of seasons in which that team entered at least one World Championship Grand Prix weekend. _INTEGER_
   - **Starts**: Number of starts of that team. _INTEGER_
   - **Entries**: Number of entries of that team. _INTEGER_
   - **SprintStarts**: Number of sprint starts of that team. _INTEGER_
   - **SprintEntries**: Number of sprint entries of that team. _INTEGER_
   - **DNFs**: Number of DNFs of that team. _INTEGER_
   - **GrandPrixLapsLed**: Number of grand prix laps led. _INTEGER_
   - **SprintLapsLed**: Number of sprint laps led. _INTEGER_
   - **Points**: Total points. _REAL_
   - **BestGridPosition**: Best grid position. _INTEGER_
   - **BestSprintGridPosition**: Best sprint grid position. _INTEGER_
   - **BestQualifyingPosition**: Best qualifying position. _INTEGER_
   - **BestRacePosition**: Best race position. _INTEGER_
   - **BestSprintPosition**: Best sprint position. _INTEGER_
   - **BestSprintQualifyingPosition**: Best sprint qualifying position. _INTEGER_
   - **FirstGrandPrixID**: Foreign key to GrandsPrix. _INTEGER_
   - **LastGrandPrixID**: Foreign key to GrandsPrix. _INTEGER_
   - **needstatsupdate**: Internal flag for statistical calculations. _BOOLEAN_
   - **indy500only**: Whether this team only competed in Indianapolis 500 races. Set to 1 (true) if all races ended with "Indianapolis 500", otherwise 0 (false). _BOOLEAN_

8. ### Constructors
   This table contains all constructors who have made a chassis that entered a Grand Prix.  
   **Columns:**
   - **ID**: Unique constructor ID. _INTEGER PRIMARY KEY AUTOINCREMENT_
   - **ConstructorName**: Name of the constructor. _TEXT UNIQUE_
   - **Wins**: Number of wins of that constructor. _INTEGER_
   - **Podiums**: Number of podiums of that constructor. _INTEGER_
   - **Poles**: Number of pole positions of that constructor. _INTEGER_
   - **FastestLaps**: Number of fastest laps of that constructor. _INTEGER_
   - **SprintWins**: Number of sprint wins. _INTEGER_
   - **SprintPodiums**: Number of sprint podiums. _INTEGER_
   - **SprintPoles**: Number of sprint pole positions. _INTEGER_
   - **SprintFastestLaps**: Number of sprint fastest laps. _INTEGER_
   - **OneTwos**: Number of "1-2" finishes (both cars of the constructor finished 1st and 2nd). _INTEGER_
   - **Championships**: Number of championships of that constructor. _INTEGER_
   - **SeasonsRaced**: Number of seasons in which that constructor entered at least one World Championship Grand Prix weekend. _INTEGER_
   - **Points**: Total points of that constructor. _REAL_
   - **Starts**: Number of starts of that constructor. _INTEGER_
   - **Entries**: Number of entries of that constructor. _INTEGER_
   - **SprintStarts**: Number of sprint starts. _INTEGER_
   - **SprintEntries**: Number of sprint entries. _INTEGER_
   - **DNFs**: Number of DNFs of that constructor. _INTEGER_
   - **GrandPrixLapsLed**: Number of grand prix laps led. _INTEGER_
   - **SprintLapsLed**: Number of sprint laps led. _INTEGER_
   - **BestGridPosition**: Best grid position of that constructor. _INTEGER_
   - **BestSprintGridPosition**: Best sprint grid position of that constructor. _INTEGER_
   - **BestQualifyingPosition**: Best qualifying position of that constructor. _INTEGER_
   - **BestRacePosition**: Best race position of that constructor. _INTEGER_
   - **BestSprintPosition**: Best sprint position of that constructor. _INTEGER_
   - **BestSprintQualifyingPosition**: Best sprint qualifying position. _INTEGER_
   - **BestChampionshipPosition**: Best championship position of that constructor. _INTEGER_
   - **FirstGrandPrix**: First Grand Prix of that constructor. _TEXT_
   - **LastGrandPrix**: Last Grand Prix of that constructor. _TEXT_
   - **Nationalities**: List of nationalities (registered in) along with stints of that constructor. (JSON) _TEXT_
   - **Filiation**: Timeline of the team in its various avatars along with stints of that constructor. (JSON) _TEXT_
   - **TeamPrincipals**: Team principals of that constructor. (JSON) _TEXT_
   
   Here is Lotus' entries of `Nationalities`, `Filiation`, and `TeamPrincipals` to illustrate the format:
   ```
   Nationalities:
   [["United Kingdom", null], ["Malaysia", 2010], ["United Kingdom", 2012]]

   Filiation: 
   [[{"Lotus": "1958-1994"}], [{"Lotus": "2010-2011"}, {"Caterham": "2012-2014"}], [{"Toleman": "1981-1985"}, {"Benetton": "1986-2001"}, {"Renault": "2002-2011"}, {"Lotus": "2012-2015"}, {"Renault": "2016-2020"}, {"Alpine": "2021-"}]]

   TeamPrincipals:
   [[{"Colin Chapman": "1958-1982"}, {"Peter Warr": "1983-1989"}, {"Rupert Manwaring": "1989-1990"}, {"Peter Collins": "1991-1994"}], [{"Tony Fernandes": "2010-2011"}], [{"Éric Boullier": "2012-2013"}, {"Gérard Lopez": "2014-2015"}]]
   ```
   - **FirstGrandPrixID**: Foreign key to GrandsPrix. _INTEGER_
   - **LastGrandPrixID**: Foreign key to GrandsPrix. _INTEGER_
   - **needstatsupdate**: Internal flag for statistical calculations. _BOOLEAN_
   - **indy500only**: Whether this constructor only competed in Indianapolis 500 races. Set to 1 (true) if all races ended with "Indianapolis 500", otherwise 0 (false). _BOOLEAN_

9. ### Engines
   This table contains all engine manufacturers to power an entry in a Grand Prix. Rebadged engines like Acer, Mecachrome, etc. are also included.

   **Columns:**
   - **ID**: Unique engine manufacturer ID. _INTEGER PRIMARY KEY AUTOINCREMENT_
   - **EngineName**: Name of the engine manufacturer. _TEXT UNIQUE_
   - **Wins**: Number of wins of that engine manufacturer. _INTEGER_
   - **Podiums**: Number of podiums of that engine manufacturer. _INTEGER_
   - **Poles**: Number of pole positions of that engine manufacturer. _INTEGER_
   - **FastestLaps**: Number of fastest laps of that engine manufacturer. _INTEGER_
   - **SprintWins**: Number of sprint wins. _INTEGER_
   - **SprintPodiums**: Number of sprint podiums. _INTEGER_
   - **SprintPoles**: Number of sprint pole positions. _INTEGER_
   - **SprintFastestLaps**: Number of sprint fastest laps. _INTEGER_
   - **Championships**: Number of championships of that engine manufacturer. _INTEGER_
   - **SeasonsRaced**: Number of seasons in which that engine manufacturer entered at least one World Championship Grand Prix weekend. _INTEGER_
   - **Points**: Total points. _REAL_
   - **Starts**: Number of starts of that engine manufacturer. _INTEGER_
   - **Entries**: Number of entries of that engine manufacturer. _INTEGER_
   - **SprintStarts**: Number of sprint starts. _INTEGER_
   - **SprintEntries**: Number of sprint entries. _INTEGER_
   - **DNFs**: Number of DNFs of that engine manufacturer. _INTEGER_
   - **GrandPrixLapsLed**: Number of grand prix laps led. _INTEGER_
   - **SprintLapsLed**: Number of sprint laps led. _INTEGER_
   - **BestGridPosition**: Best grid position. _INTEGER_
   - **BestSprintGridPosition**: Best sprint grid position. _INTEGER_
   - **BestQualifyingPosition**: Best qualifying position. _INTEGER_
   - **BestRacePosition**: Best race position. _INTEGER_
   - **BestSprintPosition**: Best sprint position. _INTEGER_
   - **BestSprintQualifyingPosition**: Best sprint qualifying position. _INTEGER_
   - **FirstGrandPrix**: First Grand Prix of that engine manufacturer. _TEXT_
   - **LastGrandPrix**: Last Grand Prix of that engine manufacturer. _TEXT_
   - **FirstGrandPrixID**: Foreign key to GrandsPrix. _INTEGER_
   - **LastGrandPrixID**: Foreign key to GrandsPrix. _INTEGER_
   - **needstatsupdate**: Internal flag for statistical calculations. _BOOLEAN_
   - **indy500only**: Whether this engine manufacturer only competed in Indianapolis 500 races. Set to 1 (true) if all races ended with "Indianapolis 500", otherwise 0 (false). _BOOLEAN_

10. ### Tyres
   This table contains all tyre manufacturers that have provided tyres to an entry.  
   **Columns:**
   - **ID**: Unique tyre ID. _INTEGER PRIMARY KEY AUTOINCREMENT_
   - **TyreName**: Name of the tyre. _TEXT UNIQUE_
   - **Wins**: Number of wins of that tyre manufacturer. _INTEGER_
   - **Podiums**: Number of podiums of that tyre manufacturer. _INTEGER_
   - **Poles**: Number of pole positions of that tyre manufacturer. _INTEGER_
   - **FastestLaps**: Number of fastest laps of that tyre manufacturer. _INTEGER_
   - **SprintWins**: Number of sprint wins. _INTEGER_
   - **SprintPodiums**: Number of sprint podiums. _INTEGER_
   - **SprintPoles**: Number of sprint pole positions. _INTEGER_
   - **SprintFastestLaps**: Number of sprint fastest laps. _INTEGER_
   - **SeasonsRaced**: Number of seasons in which that tyre manufacturer entered at least one World Championship Grand Prix weekend. _INTEGER_
   - **Points**: Total points of that tyre manufacturer. _REAL_
   - **Starts**: Number of starts of that tyre manufacturer. _INTEGER_
   - **Entries**: Number of entries of that tyre manufacturer. _INTEGER_
   - **SprintStarts**: Number of sprint starts. _INTEGER_
   - **SprintEntries**: Number of sprint entries. _INTEGER_
   - **DNFs**: Number of DNFs of that tyre manufacturer. _INTEGER_
   - **GrandPrixLapsLed**: Number of grand prix laps led. _INTEGER_
   - **SprintLapsLed**: Number of sprint laps led. _INTEGER_
   - **BestGridPosition**: Best grid position. _INTEGER_
   - **BestSprintGridPosition**: Best sprint grid position. _INTEGER_
   - **BestQualifyingPosition**: Best qualifying position. _INTEGER_
   - **BestRacePosition**: Best race position. _INTEGER_
   - **BestSprintPosition**: Best sprint position. _INTEGER_
   - **BestSprintQualifyingPosition**: Best sprint qualifying position. _INTEGER_
   - **FirstGrandPrix**: First Grand Prix of that tyre manufacturer. _TEXT_
   - **LastGrandPrix**: Last Grand Prix of that tyre manufacturer. _TEXT_
   - **FirstGrandPrixID**: Foreign key to GrandsPrix. _INTEGER_
   - **LastGrandPrixID**: Foreign key to GrandsPrix. _INTEGER_
   - **needstatsupdate**: Internal flag for statistical calculations. _BOOLEAN_
   - **indy500only**: Whether this tyre manufacturer only competed in Indianapolis 500 races. Set to 1 (true) if all races ended with "Indianapolis 500", otherwise 0 (false). _BOOLEAN_

11. ### Chassis
    This table contains all chassis to enter a race in Formula One (e.g, the Lotus 72).  
    **Columns:**
    - **ID**: Unique chassis ID. _INTEGER PRIMARY KEY AUTOINCREMENT_
    - **ConstructorName**: Name of the constructor that made that chasis. _TEXT_
    - **ChassisName**: Name of the chassis. _TEXT_
    - **ConstructorID**: Foreign key to Constructors. _INTEGER_
    - **EngineMakesUsed** List of engine makes used (JSON) _TEXT_,
    - **TyresUsed** List of tyres used (JSON)  _TEXT_,
    - **Designers** List of designers along with departments (JSON) _TEXT_,
    - **EngineModelsUsed** List of engine models used (JSON) _TEXT_,
    - **Wins**: Number of wins of that chassis. _INTEGER_
    - **Podiums**: Number of podiums of that chassis. _INTEGER_
    - **Poles**: Number of pole positions of that chassis. _INTEGER_
    - **FastestLaps**: Number of fastest laps of that chassis. _INTEGER_
    - **SprintWins**: Number of sprint wins. _INTEGER_
    - **SprintPodiums**: Number of sprint podiums. _INTEGER_
    - **SprintPoles**: Number of sprint pole positions. _INTEGER_
    - **SprintFastestLaps**: Number of sprint fastest laps. _INTEGER_
    - **SeasonsRaced**: Number of seasons in which that chassis entered at least one World Championship Grand Prix weekend. _INTEGER_
    - **Points**: Total points of that chassis. _REAL_
    - **Starts**: Number of starts of that chassis. _INTEGER_
    - **Entries**: Number of entries of that chassis. _INTEGER_
    - **SprintStarts**: Number of sprint starts. _INTEGER_
    - **SprintEntries**: Number of sprint entries. _INTEGER_
    - **DNFs**: Number of DNFs of that chassis. _INTEGER_
    - **GrandPrixLapsLed**: Number of grand prix laps led. _INTEGER_
    - **SprintLapsLed**: Number of sprint laps led. _INTEGER_
    - **BestGridPosition**: Best grid position. _INTEGER_
    - **BestSprintGridPosition**: Best sprint grid position. _INTEGER_
    - **BestQualifyingPosition**: Best qualifying position. _INTEGER_
    - **BestRacePosition**: Best race position. _INTEGER_
    - **BestSprintPosition**: Best sprint position. _INTEGER_
    - **BestSprintQualifyingPosition**: Best sprint qualifying position. _INTEGER_
    - **FirstGrandPrix**: First Grand Prix. _TEXT_
    - **LastGrandPrix**: Last Grand Prix. _TEXT_
    - **ConstructorID**: Foreign key to Constructors. _INTEGER_
    - **FirstGrandPrixID**: Foreign key to GrandsPrix. _INTEGER_
    - **LastGrandPrixID**: Foreign key to GrandsPrix. _INTEGER_
    - **needstatsupdate**: Internal flag for statistical calculations. _BOOLEAN_
    - **indy500only**: Whether this chassis only competed in Indianapolis 500 races. Set to 1 (true) if all races ended with "Indianapolis 500", otherwise 0 (false). _BOOLEAN_

12. ### EngineModels
    This table contains all engine models used in F1 (e.g, the Cosworth DFV).  
    **Columns:**
    - **ID**: Unique engine model ID. _INTEGER PRIMARY KEY AUTOINCREMENT_
    - **EngineMake**: Engine make (e.g "Mercedes" for "M11 EQ Performance V6 t h 1.6"). _TEXT_
    - **EngineModel**: Engine model. It also includes some specifications of the engine like displacement, configuration, turbocharged or not, hybrid or not. For example, the DFV is stored as "DFV V8 3.0". _TEXT_
    - **EngineMakeID**: Foreign key to Engines. _INTEGER_
    - **Wins**: Number of wins of that engine manufacturer. _INTEGER_
    - **Podiums**: Number of podiums of that engine manufacturer. _INTEGER_
    - **Poles**: Number of pole positions of that engine manufacturer. _INTEGER_
    - **FastestLaps**: Number of fastest laps of that engine manufacturer. _INTEGER_
    - **SprintWins**: Number of sprint wins. _INTEGER_
    - **SprintPodiums**: Number of sprint podiums. _INTEGER_
    - **SprintPoles**: Number of sprint pole positions. _INTEGER_
    - **SprintFastestLaps**: Number of sprint fastest laps. _INTEGER_
    - **Championships**: Number of championships of that engine manufacturer. _INTEGER_
    - **SeasonsRaced**: Number of seasons in which that engine model entered at least one World Championship Grand Prix weekend. _INTEGER_
    - **Points**: Total points. _REAL_
    - **Starts**: Number of starts of that engine manufacturer. _INTEGER_
    - **Entries**: Number of entries of that engine manufacturer. _INTEGER_
    - **SprintStarts**: Number of sprint starts. _INTEGER_
    - **SprintEntries**: Number of sprint entries. _INTEGER_
    - **DNFs**: Number of DNFs of that engine manufacturer. _INTEGER_
    - **GrandPrixLapsLed**: Number of grand prix laps led. _INTEGER_
    - **SprintLapsLed**: Number of sprint laps led. _INTEGER_
    - **BestGridPosition**: Best grid position. _INTEGER_
    - **BestSprintGridPosition**: Best sprint grid position. _INTEGER_
    - **BestQualifyingPosition**: Best qualifying position. _INTEGER_
    - **BestRacePosition**: Best race position. _INTEGER_
    - **BestSprintPosition**: Best sprint position. _INTEGER_
    - **BestSprintQualifyingPosition**: Best sprint qualifying position. _INTEGER_
    - **FirstGrandPrix**: First Grand Prix of that engine manufacturer. _TEXT_
    - **LastGrandPrix**: Last Grand Prix of that engine manufacturer. _TEXT_
    - **StatsF1Data**: A bunch of JSON data scraped from statsf1 and then translated. This contains some additional information about the engine model like displacement, configuration, original engine (if this is a rebadged engine), power developed, and so on. (JSON) _TEXT_
    Some examples:
    ```
    {"statsf1_name": "Alfa Romeo 158", "years": "1950-1951", "era": null, "description": "When it was designed in 1935, this engine developed 195 hp at 7,200 rpm with a 0.8 bar compressor. After the war, the power increased to 260 hp and at the end of 1951, the power was 425 hp with a pressure of more than 3 bars.", "specs": {"Kind": "8 cylinders in line", "Configuration": "2 camshafts, 2 valves per cylinder, 2 compressors", "Displacement": "1,479 cc", "Power": "370 hp at 8,500 rpm (1950), 425 hp at 9,600 rpm (1951)", "Fuel": "Methanol", "Used by": "Alfa Romeo"}}

    {"statsf1_name": "BRM P60 1.5", "years": "1963-1965", "era": null, "description": null, "specs": {"Kind": "8 V-shaped cylinders", "Displacement": "1,498 cc", "Used by": "BRM"}}
    
    {"statsf1_name": "Mugen-Honda MF-301HA", "years": "1996", "era": null, "description": null, "specs": {"Kind": "10 V-shaped cylinders", "Displacement": "2,998 cc", "Power": "690 hp at 13,700 rpm", "Used by": "Ligier"}}

    {"statsf1_name": "Mercedes PU106B", "years": "2015", "era": "A NEW PROPELLER", "description": null, "specs": {"Kind": "6 turbocharged 90° V cylinders equipped with an electric motor", "Configuration": "24 valves (4 per cylinder)", "Displacement": "1,600 cm3", "Power": "600 hp (Thermal) + 160 hp (Electric)", "Maximum speed": "15,000 rpm", "Used by": "Mercedes, Force India, Lotus, Williams"}}
    ```
    - **EngineMakeID**: Foreign key to Engines of that engine manufacturer. _INTEGER_
    - **FirstGrandPrixID**: Foreign key to GrandsPrix of that engine manufacturer. _INTEGER_
    - **LastGrandPrixID**: Foreign key to GrandsPrix of that engine manufacturer. _INTEGER_
    - **needstatsupdate**: Internal flag for statistical calculations. _BOOLEAN_
    - **indy500only**: Whether this engine model only competed in Indianapolis 500 races. Set to 1 (true) if all races ended with "Indianapolis 500", otherwise 0 (false). _BOOLEAN_

13. ### GrandPrixResults
    This table contains all race entrants for each Grand Prix, with all session results and penalties. It then has all the results for each session that driver competed in. For shared cars, there are separate entries for each driver but it has the _same_ car number. 
    
    Q1 and Q2 can mean different things over different qualifying regulations. From 1950 till 1995, there were two different qualifying sessions, one on Friday (Q1), and one on Saturday (Q2). The best time from both sessions for each driver was counted towards the overall qualifying. In 2003 and 2004, during the first qualifying session (Q1), all the drivers set one lap in championship order, with the last going first and first going last. Then, on the second qualifying session (Q2), the drivers went on another lap, with the last on the first session going first, and the first going last. Then, for the first six races in 2005, there were two different sessions; one on low fuel (Q1), and one on race fuel (Q2). Then the two times would be aggregated to set the overall qualifying times. From 2006, the slowest drivers were eliminated in Q1 and Q2, now just part of qualifying, not two separate sessions. 

    Pre-qualifying in Formula 1 was an additional elimination session used mainly between 1987 and 1992 to manage situations where more cars entered a race weekend than the maximum allowed grid of 26. During this period, many small and underfunded teams joined the sport, leading to entry lists of over 30 cars. To control this, the slowest teams—usually determined by their previous results—had to participate in a Friday morning pre-qualifying session, where only a few of the fastest drivers (typically four) would advance to the main qualifying rounds, while the rest were eliminated from the weekend entirely. This system was often harsh, as some teams traveled long distances only to complete a handful of laps before being sent home. A much earlier example of a similar issue can be seen at the 1965 South African Grand Prix held at Kyalami Circuit, where a large number of local and private entrants led to an overcrowded field. To pre-qualify, a driver had to set a lap time below 1:37.0, unlike later pre-qualifying sessions where only a few of the fastest drivers would pre-qualify. For this session, the `prequalifyingtime` is given as "+1:37.0" or "-1:37.0". Between the late-1970s and the mid-1980s, pre-qualifying occured in a few races where there were too many entrants.


    We also have data for practice sessions. This must not be confused with qualifying being called practice previously. There were up to four practice sessions in previous years, with the pre-race warm ups as well. There have been only two practice sessions in some years, and in Sprint weekends we only have one. We currently have three practice sessions in normal weekends.

    From 2000 to 2003, a Grand Prix weekend had four practice sessions: two on Friday and two on Saturday. These are stored separately in the `fridaypractice1`, `fridaypractice2`, `saturdaypractice1`, and `saturdaypractice2` columns, which are scraped from newsonf1.com. They are independent of the `practice1` to `practice4` columns, which come from Formula1.com and, for this era, hold only a partial and inconsistent subset of those same four sessions. **The two sets of columns must not be conflated or assumed to correspond**; the Friday/Saturday columns are the ones that state which session a time actually came from.

    2003 is an exception: Formula 1 moved to single-lap knockout qualifying that season, and the second Friday session became Friday Qualifying rather than a practice session. There is therefore no Friday Practice 2 in 2003, and `fridaypractice2` is `NULL` for every race of that season. The 2003 Belgian Grand Prix was cancelled and so has no data at all.

    Monaco is the other exception: its first two practice sessions were held on Thursday rather than Friday, so they are stored in `thursdaypractice1` and `thursdaypractice2`. Those columns are `NULL` for every other Grand Prix, and both `fridaypractice` columns are `NULL` for Monaco. Every race, Monaco included, has both Saturday sessions.

    The source publishes only position, driver, team, and best lap for these sessions, so there is no laps-completed count and the Friday/Saturday practice columns have no `laps` equivalent. Times set in wet conditions are recorded as ordinary times, since there is no per-session conditions field. Not every entrant appears in every session, so a driver who did not run will have `NULL`s for that session.

    We have data for the two qualifying sessions on Friday and Saturday in the old format, Q1 and Q2 only after 1983. To find drivers who did not pre-qualify or qualify, it is there in overall qualifying. We have data for the Sunday morning warm-up session from 1984 till 2003, when the session no longer happened. 


    In 2021, the Qualifying session set the grid for the Sprint race, and the Sprint set the grid for the Grand Prix, with the top 3 finishers getting 3, 2, and 1 point respectively.

    In 2022, the Qualifying session set the grid for both the Sprint and the race.

    From 2023, there is one qualifying session for the race and one for the sprint.

    **Columns:**
    - **grandprix**: Grand Prix name. _TEXT_
    - **number**: Car number. _INTEGER_
    - **driver**: Driver name. _TEXT_
    - **nationality**: Nationality of the driver. _TEXT_
    - **team**: Team name. _TEXT_
    - **constructor**: Constructor name. _TEXT_
    - **chassis**: Chassis name. _TEXT_
    - **engine**: Engine name. _TEXT_
    - **enginemodel**: Engine model. _TEXT_
    - **tyre**: Tyre name. _TEXT_
    - **substituteorthirddriver**: Substitute or third driver. _BOOLEAN_
    - **qualifyingposition**: Qualifying position. _INTEGER_
    - **qualifyingtime**: Qualifying time. _TEXT_
    - **qualifyinggap**: Qualifying gap. _TEXT_
    - **qualifyingtimeinseconds**: Qualifying time in seconds. _REAL_
    - **qualifyinggapseconds**: Qualifying gap in seconds. _REAL_
    - **qualifyinginterval**: Gap to the car ahead in qualifying. _REAL_
    - **prequalifyingposition**: Pre-qualifying position. _INTEGER_
    - **prequalifyingtime**: Pre-qualifying time. _TEXT_
    - **prequalifyinggap**: Pre-qualifying gap. _TEXT_
    - **prequalifyinginterval**: Gap to the car ahead in pre-qualifying. _REAL_
    - **prequalifyingtimeinseconds**: Pre-qualifying time in seconds. _REAL_
    - **starting_grid_position**: Starting grid position. _INTEGER_
    - **gridpenalty**: Any grid penalty applied. It can be a pit lane start ("Start from pit lane"), start from the back of the grid ("Demoted to the back of the grid"), or a grid penalty by x number of postions (e.g, "Demoted by 7 places"). _TEXT_
    - **gridpenalty_reason**: Grid penalty reason (e.g "Modifying car under Parc Fermé conditions", "Exceeding quota of powertrain elements", and so on.). _TEXT_
    - **sprintstarting_grid_position**: Sprint starting grid position. _INTEGER_
    - **sprintgridpenalty**: Sprint grid penalty. Same format as `gridpenalty` _TEXT_
    - **sprintgridpenalty_reason**: Sprint grid penalty reason. Same format as  `gridpenalty_reason` _TEXT_
    - **fastestlap**: Fastest lap position (e.g, out of each drivers' fastest laps, this driver set the sixth fastest lap). _INTEGER_
    - **fastestlapinseconds**: Fastest lap in seconds. _REAL_
    - **fastestlapgapinseconds**: Fastest lap gap in seconds. _REAL_
    - **fastestlap_time**: Fastest lap time. _TEXT_
    - **fastestlap_gap**: Fastest lap gap. _TEXT_
    - **fastestlapinterval**: Gap to the car with the next fastest lap. _REAL_
    - **fastestlap_lap**: Lap of fastest lap (e.g, driver set their fastest lap on the 17th lap). _INTEGER_
    - **sprintfastestlap**: Sprint fastest lap position. _INTEGER_
    - **sprintfastestlapinseconds**: Sprint fastest lap in seconds. _REAL_
    - **sprintfastestlapgapinseconds**: Sprint fastest lap gap in seconds. _REAL_
    - **sprintfastestlap_time**: Sprint fastest lap time. _TEXT_
    - **sprintfastestlap_gap**: Sprint fastest lap time gap. _TEXT_
    - **sprintfastestlapinterval**: Gap to the car with the next fastest sprint lap. _REAL_
    - **sprintfastestlap_lap**: Sprint fastest lap lap. _INTEGER_
    - **qualifying2position**: Q2 position. _INTEGER_
    - **qualifying2time**: Q2 time. _TEXT_
    - **qualifying2gap**: Q2 gap. _REAL_
    - **qualifying2timeinseconds**: Q2 time in seconds. _REAL_
    - **qualifying2interval**: Gap to the car ahead in Q2. _REAL_
    - **qualifying2laps**: Q2 laps. _INTEGER_
    - **qualifying1position**: Q1 position. _INTEGER_
    - **qualifying1time**: Q1 time. _TEXT_
    - **qualifying1gap**: Q1 gap. _REAL_
    - **qualifying1timeinseconds**: Q1 time in seconds. _REAL_
    - **qualifying1interval**: Gap to the car ahead in Q1. _REAL_
    - **qualifying1laps**: Q1 laps. _INTEGER_
    - **qualifyinglaps**: Qualifying laps. _INTEGER_
    - **qualifying3time**: Q3 time. _TEXT_
    - **qualifying3gap**: Q3 gap string. _REAL_
    - **qualifying3interval**: Gap to the car ahead in Q3. _REAL_
    - **qualifying3timeinseconds**: Q3 time in seconds. _REAL_
    - **sprint_qualifyingposition**: Sprint qualifying position. _INTEGER_
    - **sprint_qualifying1time**: Sprint Q1 time. _TEXT_
    - **sprint_qualifying2time**: Sprint Q2 time. _TEXT_
    - **sprint_qualifying3time**: Sprint Q3 time. _TEXT_
    - **sprint_qualifying1gap**: Sprint Q1 gap. _REAL_
    - **sprint_qualifying1interval**: Gap to the car ahead in sprint qualifying Q1. _REAL_
    - **sprint_qualifying2gap**: Sprint Q2 gap. _REAL_
    - **sprint_qualifying2interval**: Gap to the car ahead in sprint qualifying Q2. _REAL_
    - **sprint_qualifying3gap**: Sprint Q3 gap. _REAL_
    - **sprint_qualifying3interval**: Gap to the car ahead in sprint qualifying Q3. _REAL_
    - **sprint_qualifyinggap**: Sprint qualifying gap. _REAL_
    - **sprint_qualifyinginterval**: Gap to the car ahead in sprint qualifying. _REAL_
    - **sprint_qualifying1timeinseconds**: Sprint Q1 time in seconds. _REAL_
    - **sprint_qualifying2timeinseconds**: Sprint Q2 time in seconds. _REAL_
    - **sprint_qualifying3timeinseconds**: Sprint Q3 time in seconds. _REAL_
    - **sprint_qualifyinglaps**: Sprint qualifying laps. _INTEGER_
    - **sprint_qualifyingtime**: Sprint qualifying time. _TEXT_
    - **sprint_qualifyingtimeinseconds**: Sprint qualifying time in seconds. _REAL_
    - **warmupposition**: Warmup position. _INTEGER_
    - **warmuptime**: Warmup time. It only shows the gap for those who are not the leader. _TEXT_
    - **warmupgap**: Warmup gap. _TEXT_
    - **warmuptimeinseconds**: Warmup time in seconds. _REAL_
    - **warmuplaps**: Warmup laps. _INTEGER_
    - **practice1position**: Practice 1 position. _INTEGER_
    - **practice1time**: Practice 1 time. It only shows the gap for those who are not the leader. _TEXT_
    - **practice1gap**: Practice 1 gap. _TEXT_
    - **practice1timeinseconds**: Practice 1 time in seconds. _REAL_
    - **practice1interval**: Gap to the car ahead in Practice 1. _REAL_
    - **practice1laps**: Practice 1 laps. _INTEGER_
    - **practice2position**: Practice 2 position. _INTEGER_
    - **practice2time**: Practice 2 time. It only shows the gap for those who are not the leader. _TEXT_
    - **practice2gap**: Practice 2 gap. _TEXT_
    - **practice2timeinseconds**: Practice 2 time in seconds. _REAL_
    - **practice2interval**: Gap to the car ahead in Practice 2. _REAL_
    - **practice2laps**: Practice 2 laps. _INTEGER_
    - **practice3position**: Practice 3 position. _INTEGER_
    - **practice3time**: Practice 3 time. It only shows the gap for those who are not the leader. _TEXT_
    - **practice3gap**: Practice 3 gap. _TEXT_
    - **practice3timeinseconds**: Practice 3 time in seconds. _REAL_
    - **practice3interval**: Gap to the car ahead in Practice 3. _REAL_
    - **practice3laps**: Practice 3 laps. _INTEGER_
    - **practice4position**: Practice 4 position. _INTEGER_
    - **practice4time**: Practice 4 time. It only shows the gap for those who are not the leader. _TEXT_
    - **practice4gap**: Practice 4 gap. _TEXT_
    - **practice4timeinseconds**: Practice 4 time in seconds. _REAL_
    - **practice4interval**: Gap to the car ahead in Practice 4. _REAL_
    - **practice4laps**: Practice 4 laps. _INTEGER_

    The following columns cover the four practice sessions of the 2000-2003 era, two on the first day of running and two on Saturday, and are sourced from newsonf1.com independently of `practice1` to `practice4` above. See the notes at the start of this table. There is no `laps` column for these sessions because the source does not publish a lap count.
    - **thursdaypractice1position**: Thursday Practice 1 position. Only used for Monaco, which practised on Thursday. `NULL` everywhere else. _INTEGER_
    - **thursdaypractice1time**: Thursday Practice 1 best lap. _TEXT_
    - **thursdaypractice1gap**: Thursday Practice 1 gap to the session leader. `NULL` for the leader. _TEXT_
    - **thursdaypractice1interval**: Gap to the car ahead in Thursday Practice 1. _REAL_
    - **thursdaypractice1timeinseconds**: Thursday Practice 1 best lap in seconds. _REAL_
    - **thursdaypractice2position**: Thursday Practice 2 position. Only used for Monaco. `NULL` everywhere else. _INTEGER_
    - **thursdaypractice2time**: Thursday Practice 2 best lap. _TEXT_
    - **thursdaypractice2gap**: Thursday Practice 2 gap to the session leader. `NULL` for the leader. _TEXT_
    - **thursdaypractice2interval**: Gap to the car ahead in Thursday Practice 2. _REAL_
    - **thursdaypractice2timeinseconds**: Thursday Practice 2 best lap in seconds. _REAL_
    - **fridaypractice1position**: Friday Practice 1 position. `NULL` at Monaco, which practised on Thursday. _INTEGER_
    - **fridaypractice1time**: Friday Practice 1 best lap. _TEXT_
    - **fridaypractice1gap**: Friday Practice 1 gap to the session leader. `NULL` for the leader. _TEXT_
    - **fridaypractice1interval**: Gap to the car ahead in Friday Practice 1. _REAL_
    - **fridaypractice1timeinseconds**: Friday Practice 1 best lap in seconds. _REAL_
    - **fridaypractice2position**: Friday Practice 2 position. Always `NULL` in 2003, when this session was Friday Qualifying instead. _INTEGER_
    - **fridaypractice2time**: Friday Practice 2 best lap. _TEXT_
    - **fridaypractice2gap**: Friday Practice 2 gap to the session leader. `NULL` for the leader. _TEXT_
    - **fridaypractice2interval**: Gap to the car ahead in Friday Practice 2. _REAL_
    - **fridaypractice2timeinseconds**: Friday Practice 2 best lap in seconds. _REAL_
    - **saturdaypractice1position**: Saturday Practice 1 position. _INTEGER_
    - **saturdaypractice1time**: Saturday Practice 1 best lap. _TEXT_
    - **saturdaypractice1gap**: Saturday Practice 1 gap to the session leader. `NULL` for the leader. _TEXT_
    - **saturdaypractice1interval**: Gap to the car ahead in Saturday Practice 1. _REAL_
    - **saturdaypractice1timeinseconds**: Saturday Practice 1 best lap in seconds. _REAL_
    - **saturdaypractice2position**: Saturday Practice 2 position. _INTEGER_
    - **saturdaypractice2time**: Saturday Practice 2 best lap. _TEXT_
    - **saturdaypractice2gap**: Saturday Practice 2 gap to the session leader. `NULL` for the leader. _TEXT_
    - **saturdaypractice2interval**: Gap to the car ahead in Saturday Practice 2. _REAL_
    - **saturdaypractice2timeinseconds**: Saturday Practice 2 best lap in seconds. _REAL_
    - **sprintposition**: Sprint position. _INTEGER_
    - **sprintlaps**: Sprint laps. _INTEGER_
    - **sprinttime**: Sprint time. _TEXT_
    - **sprintpoints**: Sprint points. _REAL_
    - **sprinttimeinseconds**: Sprint time in seconds. _REAL_
    - **sprintgap**: Sprint gap. _TEXT_
    - **sprintgapinseconds**: Sprint gap in seconds. _REAL_
    - **sprintinterval**: Gap to the car ahead in the Sprint. _REAL_
    - **sprintstatus**: Status of the car in the sprint (e.g. Did not finish, Disqualified, Classified, etc.). _TEXT_
    - **sprintstatusreason**: Reason for the status in the sprint (e.g. "Engine failure"). _TEXT_
    - **raceposition**: Race position. _INTEGER_
    - **racelaps**: Race laps. _INTEGER_
    - **racetime**: Race time.  _TEXT_
    - **racepoints**: Race points. _REAL_
    - **racetimeinseconds**: Race time in seconds. _REAL_
    - **racegap**: Race gap. _TEXT_
    - **racegapinseconds**: Race gap in seconds. _REAL_
    - **raceinterval**: Gap to the car ahead in the Race. _REAL_
    - **racestatus**: Status of the car in the race (Did not finish, Disqualified, Classified, etc.). _TEXT_
    - **racestatusreason**: Reason for the status in the race (e.g, "Collision with car ahead"). _TEXT_
    - **penalties**: Penalties (JSON). _TEXT_

      It has the following keys:

      - penalty: The nature and magnitude of the penalty. (e.g, "5 seconds", "10 seconds", "10 seconds Stop and Go", "Drive-through", etc.)
      - reason: The reason the penalty was given.
      - type: It has two types:

         - during_the_race: Served during the race (in a pit stop).
         - added_after_chequered_flag: Added to race time after the race.

      For example, here's Esteban Ocon's penalties at the 2023 Austrian Grand Prix:          
      ```
      [{"penalty": "5 seconds", "reason": "Unsafe release from pit stop", "type": "during_the_race"}, {"penalty": "5 seconds", "reason": "Crossing track limits", "type": "added_after_chequered_flag", "lost_position": 0}, {"penalty": "5 seconds", "reason": "Crossing track limits", "type": "added_after_chequered_flag", "lost_position": 0}, {"penalty": "10 seconds", "reason": "Crossing track limits", "type": "added_after_chequered_flag", "lost_position": 0}, {"penalty": "10 seconds", "reason": "Crossing track limits", "type": "added_after_chequered_flag", "lost_position": 2}]
      ```

    - **sprint_penalties**: Sprint penalties (JSON). Same format as `penalties`. _TEXT_
    - **driverid**: Foreign key to Drivers. _INTEGER_
    - **teamid**: Foreign key to Teams. _INTEGER_
    - **constructorid**: Foreign key to Constructors. _INTEGER_
    - **chassisid**: Foreign key to Chassis. _INTEGER_
    - **engineid**: Foreign key to Engines. _INTEGER_
    - **enginemodelid**: Foreign key to EngineModels. _INTEGER_
    - **tyreid**: Foreign key to Tyres. _INTEGER_
    - **grandprixid**: Foreign key to GrandsPrix. _INTEGER_
    - **nationalityid**: Foreign key to Nationalities. _INTEGER_

14. ### PitStopSummary
    This table shows the pit stops done during the race.  
    **Columns:**
    - **GrandPrix**: Name of the Grand Prix. _TEXT_
    - **Number**: Car number. _INTEGER_
    - **Driver**: Driver name. _TEXT_
    - **Constructor**: Constructor name. _TEXT_
    - **StopNumber**: Stop number (e.g, it's the driver's second stop). _INTEGER_
    - **Lap**: Lap of the stop. _INTEGER_
    - **DurationSpentInPitLane**: Duration in _pit lane_. Not to be confused as the duration stationary in the pit stop _TEXT_
    - **TimeInSeconds**: Duration in seconds. _REAL_
    - **TimeOfDayStopped**: Time of day in local track time. _TEXT_
    - **TotalTimeSpentInPitLane**: Total pit lane time (all the stops combined till that point). _TEXT_
    - **TotalTimeinSeconds**: Total pit lane time in seconds. _REAL_
    - **DurationStoppedInPitBox**: Duration stationary in the pit box. _REAL_
    - **type**: Type of pit stop, such as a normal tyre stop or another kind of service stop. _TEXT_
    - **SessionName**: Name of the session the pit stop belongs to, such as `Race`, `Sprint`, or `Sprint Qualifying`. _TEXT_
    - **GrandPrixID**: Foreign key to GrandsPrix. _INTEGER_
    - **DriverID**: Foreign key to Drivers. _INTEGER_
    - **ConstructorID**: Foreign key to Constructors. _INTEGER_

15. ### LapByLap
    This table has the lap-by-lap data for each race.  
    **Columns:**
    - **GrandPrix**: Name of the Grand Prix. _TEXT_
    - **Driver**: Driver name. _TEXT_
    - **Position**: Position on that lap. _INTEGER_
    - **Lap**: Lap number. _INTEGER_
    - **Session**: Name of the session. _TEXT_
    - **Time**: Lap time. Available from 1996. _TEXT_
    - **TimeInSeconds**: Lap time in seconds. _REAL_
    - **TyreCompound**: The tyre compound used on this lap (Soft, Medium, Hard, etc.). _TEXT_
    - **StintNumber**: The current stint number for the driver. A stint is a period of time during which the driver did not pass through the pit lane.  A new stint starts the lap in which the driver exits the pit lane. So, even if the driver does not change tyres, a new stint is started. _INTEGER_
    - **Sector1Time**: Time spent in Sector 1. _REAL_
    - **Sector2Time**: Time spent in Sector 2. _REAL_
    - **Sector3Time**: Time spent in Sector 3. _REAL_
    - **TyreAge**: Number of laps the current set of tyres has done, in all sessions, not just the current session. _INTEGER_
    - **TrackStatus**: Condition of the track (Green, Yellow, SC, VSC, etc.).  Safety Car info is available from the first ever deployment of the safety car (1973 Canadian Grand Prix), however from 2015 to 2017, if there is a safety car or virtual safety car, the track status will contain both "Safety Car" and "Virtual Safety Car" as there is no differentiation in our source data for this period. _TEXT_
    - **QualifyingSegment**: For qualifying laps, which segment (Q1, Q2, Q3, SQ1, SQ2, SQ3) it belongs to. _TEXT_
    - **SessionLapStartTime**: The session time in which the lap was started, for example, if the lap was started 30 minutes into the session, it would be 30:00.000 _TEXT_
    - **SessionLapStartTimeInSeconds**: Session lap start time in seconds. _REAL_
    - **SessionLapStartTimeInDateTimeUTC**: Session lap start time in datetime in the UTC time format. _TEXT_
    - **SessionLapEndTime**: The session time in which the lap was finished. _TEXT_
    - **SessionLapEndTimeInSeconds**: Session lap end time in seconds _REAL_
    - **Sector1EndSessionTime**: The session time in which sector 1 was finished. _TEXT_
    - **Sector1EndSessionTimeInSeconds**: Sector 1 end session time in seconds. _REAL_
    - **Sector2EndSessionTime**: The session time in which sector 2 was finished. _TEXT_
    - **Sector2EndSessionTimeInSeconds**: Sector 2 end session time in seconds _REAL_
    - **Sector3EndSessionTime**: The session time in which sector 3 was finished. _TEXT_
    - **Sector3EndSessionTimeInSeconds**: Sector 3 end session time in seconds _REAL_
    Speed Trap refers to the place on the circuit where the highest speeds in the circuit are achieved. It is generally at the end of the longest straight of the track just before the braking zone, however with the clipping in the new 2026 regulations, and to some extent even in the turbo-hybrid era of 2014-2025, the highest speed is achieved earlier in the straight because the MGU-K stops delivering power to the wheels or even starts recharging on the straight.

    Intermediate 1 refers to the point on the track where Sector 1 ends and Sector 2 begins.

    Intermediate 2 refers to the point on the track where Sector 2 ends and Sector 3 begins.

    Speed is also measured at the finishing line, at the end of the lap, or where Sector 3 ends and Sector 1 begins.    
    - **SpeedIntermediate1**: Speed in km/h in Intermediate 1. _REAL_
    - **SpeedIntermediate2**: Speed in km/h in Intermediate 2. _REAL_
    - **SpeedFinishLine**: Speed in km/h in the finish line. _REAL_
    - **SpeedSpeedTrap**: Speed in km/h in the Speed Trap. _REAL_
    - **PitInSessionTime**: The session time in which the driver entered the pit lane. If driver did not enter the pit lane, this will be NULL. _TEXT_
    - **PitInSessionTimeInSeconds**: Pit in session time in seconds. _REAL_
    - **PitOutSessionTime**: The session time in which the driver exited the pit lane. If driver did not exit the pit lane, this will be NULL. _TEXT_
    - **PitOutSessionTimeInSeconds**: Pit out session time in seconds. _REAL_
    - **IfDeletedLap**: `true` if the lap time was deleted, `false` otherwise. _BOOLEAN_
    - **DeleteReason**: Reason for deleting the lap time, if it was deleted. _TEXT_
    - **IfAccurate**: `true` if fastf1 considers the lap time accurate, `false` otherwise. _BOOLEAN_
    - **FastF1Generated**: If the lap was generated by fastf1 or not. _BOOLEAN_
    - **SessionID**: Foreign key to Sessions. _INTEGER_
    - **GrandPrixID**: Foreign key to GrandsPrix. _INTEGER_
    - **DriverID**: Foreign key to Drivers. _INTEGER_

16. ### DriversChampionship
    This table has the championship results for drivers for every year.  
    **Columns:**
    - **ID**: Unique ID (year+driver). _TEXT PRIMARY KEY_
    - **Season**: Year. _INTEGER_
    - **Position**: Position in standings. The position is *NULL* until the position is mathematically locked, i.e., no driver can overtake them in the standings, and they can't overtake any driver in the standings. _INTEGER_
    - **Driver**: Driver name. _TEXT_
    - **Points**: Points scored. _REAL_
    - **OutOf**: Points out of. _REAL_
    - **RaceByRace**: JSON of race-by-race points. _TEXT_

      It has the following format:

      **Key**: It is the Grand Prix.

      **Value**: In the form of an array. The 0th element has the number of points scored. The 1st element has the info whether the score was dropped(whether it did not count towards the championship). `true` means the score was dropped. `false` means the score was not dropped. `null` means the driver scored 0 points in that race. If there is a `[null, null]`, it indicates that the driver did not take part in that race in the season, or the event is upcoming. The third element in this list is "gp" or "sp". If it is "gp", it indicates it is a Grand Prix weekend, and if it is "sp", it indicates a sprint weekend.
      For example, here's Alain Prost's `RaceByRace` during the 1988 season:
      ```
      {"Brazil": [9.0, false, "gp"], "San Marino": [6.0, false, "gp"], "Monaco": [9.0, false, "gp"], "Mexico": [9.0, false, "gp"], "Canada": [6.0, false, "gp"], "Detroit": [6.0, false, "gp"], "France": [9.0, false, "gp"], "Britain": [0, null, "gp"], "Germany": [6.0, false, "gp"], "Hungary": [6.0, true, "gp"], "Belgium": [6.0, true, "gp"], "Italy": [0, null, "gp"], "Portugal": [9.0, false, "gp"], "gp", "Spain": [9.0, false, "gp"], "Japan": [6.0, true, "gp"], "Australia": [9.0, false, "gp"]}
      ```
      And here's Oscar Piastri's `RaceByRace` for the 2025 season:
      ```
      {"Australia": [2.0, false, "gp"], "China": [32.0, false, "sp"], "Japan": [15.0, false, "gp"], "Bahrain": [25.0, false, "gp"], "Saudi Arabia": [25.0, false, "gp"], "Miami": [32.0, false, "sp"], "Emilia-Romagna": [15.0, false, "gp"], "Monaco": [15.0, false, "gp"], "Spain": [25.0, false, "gp"], "Canada": [12.0, false, "gp"], "Austria": [18.0, false, "gp"], "Britain": [18.0, false, "gp"], "Belgium": [32.0, false, "sp"], "Hungary": [18.0, false, "gp"], "Netherlands": [25.0, false, "gp"], "Italy": [15.0, false, "gp"], "Azerbaijan": [0, null, "gp"], "Singapore": [12.0, false, "gp"], "USA": [10.0, false, "sp"], "Mexico City": [10.0, false, "gp"], "São Paulo": [10.0, false, "sp"], "Las Vegas": [0, null, "gp"], "Qatar": [26.0, false, "sp"], "Abu Dhabi": [18.0, false, "gp"]}
      ```
    - **DriverID**: Foreign key to Drivers. _INTEGER_
    - **NationalityID**: Foreign key to Nationalities. _INTEGER_

17. ### ConstructorsChampionship
    This table has the championship results for constructors for every year.  
    **Columns:**
    - **ID**: Unique ID (year+constructor+engine). _TEXT PRIMARY KEY_
    - **Season**: Year. _INTEGER_
    - **Position**: Position in standings. The position is *NULL* until the position is mathematically locked, i.e., no constructor can overtake them in the standings, and they can't overtake any constructor in the standings.  _INTEGER_
    - **Constructor**: Constructor name. _TEXT_
    - **Engine**: Engine name. _TEXT_
    - **Points**: Points scored. _REAL_
    - **OutOf**: Points out of. _REAL_
    - **RaceByRace**: JSON of race-by-race points. Same format as `RaceByRace` in `DriversChampionship`. _TEXT_
    - **ConstructorID**: Foreign key to Constructors. _INTEGER_
    - **EngineID**: Foreign key to Engines. _INTEGER_
    - **EngineModelID**: Foreign key to EngineModels. _INTEGER_

18. ### InSeasonProgressDrivers
    This table has the progress of each driver over the season. It is recorded after each race.  
    **Columns:**
    - **ID**: Unique ID. _INTEGER PRIMARY KEY AUTOINCREMENT_
    - **GrandPrix**: Name of the Grand Prix. _TEXT_
    - **PositionAtThisPoint**: Position at this point, that is, after this race. _INTEGER_
    - **Driver**: Driver name. _TEXT_
    - **PointsAtThisPoint**: Points at this point, that is, after this race. _REAL_
    - **GrandPrixID**: Foreign key to GrandsPrix. _INTEGER_
    - **DriverID**: Foreign key to Drivers. _INTEGER__

19. ### InSeasonProgressConstructors
    This table has the progress of each constructor over the season. It is recorded after each race.    
    **Columns:**
    - **ID**: Unique ID. _INTEGER PRIMARY KEY AUTOINCREMENT_
    - **GrandPrix**: Name of the Grand Prix. _TEXT_
    - **PositionAtThisPoint**: Position at this point, that is, after this race. _INTEGER_
    - **Constructor**: Constructor name. _TEXT_
    - **Engine**: Engine name. _TEXT_
    - **PointsAtThisPoint**: Points at this point, that is, after this race. _REAL_
    - **GrandPrixID**: Foreign key to GrandsPrix. _INTEGER_
    - **ConstructorID**: Foreign key to Constructors. _INTEGER_
    - **EngineID**: Foreign key to Engines. _INTEGER_

20. ### Nationalities
    This table contains all nationalities that have participated in F1.  
    **Columns:**
    - **ID**: Unique nationality ID. _INTEGER PRIMARY KEY AUTOINCREMENT_
    - **Nationality**: Nationality. _TEXT UNIQUE_
   FirstGrandPrix and LastGrandPrix does not count FP1 appearances or drivers on the entry list who did not participate in any session. Non-qualifications do count, though.    
    - **FirstGrandPrix**: First Grand Prix of that nationality. _TEXT_
    - **LastGrandPrix**: Last Grand Prix of that nationality. _TEXT_
    - **DriverCount**: Number of drivers of that nationality. _INTEGER DEFAULT 0_
    - **Wins**: Number of wins of that nationality. _INTEGER_
    - **Podiums**: Number of podiums of that nationality. _INTEGER_
    - **Poles**: Number of pole positions of that nationality. _INTEGER_
    - **FastestLaps**: Number of fastest laps of that nationality. _INTEGER_
    - **SprintWins**: Number of sprint wins. _INTEGER_
    - **SprintPodiums**: Number of sprint podiums. _INTEGER_
    - **SprintPoles**: Number of sprint pole positions. _INTEGER_
    - **SprintFastestLaps**: Number of sprint fastest laps. _INTEGER_
    - **Championships**: Number of championships of that nationality. _INTEGER_
    - **SeasonsRaced**: Number of seasons in which a driver of that nationality entered at least one World Championship Grand Prix weekend. _INTEGER_
    - **Points**: Total points. _REAL_
    - **Starts**: Number of starts of that nationality. _INTEGER_
    - **Entries**: Number of entries of that nationality. _INTEGER_
    - **SprintStarts**: Number of sprint starts. _INTEGER_
    - **SprintEntries**: Number of sprint entries. _INTEGER_
    - **DNFs**: Number of DNFs of that nationality. _INTEGER_
    - **GrandPrixLapsLed**: Number of grand prix laps led. _INTEGER_
    - **SprintLapsLed**: Number of sprint laps led. _INTEGER_
    - **BestGridPosition**: Best grid position of that nationality. _INTEGER_
    - **BestSprintGridPosition**: Best sprint grid position of that nationality. _INTEGER_
    - **BestQualifyingPosition**: Best qualifying position of that nationality. _INTEGER_
    - **BestRacePosition**: Best race position of that nationality. _INTEGER_
    - **BestSprintPosition**: Best sprint position of that nationality. _INTEGER_
    - **BestSprintQualifyingPosition**: Best sprint qualifying position. _INTEGER_    
    - **FirstGrandPrixID**: Foreign key to GrandsPrix. _INTEGER_
    - **LastGrandPrixID**: Foreign key to GrandsPrix. _INTEGER_
    - **FirstDriver**: First driver who represented this nationality. _TEXT_
    - **LastDriver**: Most recent driver who represented this nationality. _TEXT_
    - **FirstDriverID**: Foreign key to Drivers. _INTEGER_
    - **LastDriverID**: Foreign key to Drivers. _INTEGER_
    - **needstatsupdate**: Internal flag for statistical calculations. _BOOLEAN_

21. ### RaceReports
    This table contains the full race reports for every Grand Prix.
    Reports are stored as markdown-style text and may include headings and bulleted lists.

    **Columns:**
    - **ID**: Foreign key, matching the Grand Prix ID. _INTEGER PRIMARY KEY_
    - **GrandPrixName**: Name of the Grand Prix. _TEXT UNIQUE_
    - **RaceReport**: The full text of the race report. _TEXT_

22. ### MaxSpeeds
    This table contains the maximum speed of each driver in a particular measuring point (Speed Trap, Intermediate 1, Intermediate 2, Finishing Line) for sessions from 2006 onwards. 

    **Columns:**
    - **GrandPrixName** Name of the Grand Prix _TEXT_,
    - **Position** Position of the driver in the measuring point _INTEGER_,
    - **Driver** Name of the driver  _TEXT_,
    - **Constructor** Name of the constructor _TEXT_,
    - **Engine** Name of the engine _TEXT_,
    - **EngineModel** Name of the engine model _TEXT_,
    - **SessionName** Name of the session _TEXT_,
    - **TimingPoint** The point at the circuit where the speed is measured _TEXT_,
    - **SpeedKph** Speed in kilometers per hour _REAL_
    - **GrandPrixID** Foreign Key to GrandsPrix _INTEGER_
    - **SessionID** Foreign Key to Sessions _INTEGER_
    - **DriverID** Foreign Key to Drivers _INTEGER_
    - **ConstructorID** Foreign Key to Constructors _INTEGER_
    - **EngineID** Foreign Key to Engines _INTEGER_
    - **EngineModelID** Foreign Key to EngineModels  _INTEGER_

23. ### RaceControlMessages
This table contains the messages by race control. Data is available from 2018 onwards.

Race control messages are sent by race control to all teams to notify of decisions and statuses of the session.

**Columns**

- **GrandPrixName**: Name of the Grand Prix.
- **SessionName**: Name of the session.

Every message has the following attributes:

- **TimestampUTC**: Message timestamp _TEXT_

- **Category** : Type of message, “Other”, “Flag”, “Drs”, “CarEvent” _TEXT_

- **Message** : Content of message _TEXT_

- **GrandPrixID**: Foreign key to GrandsPrix
- **SessionID**: Foreign key to Sessions

Other possible attributes are:

- **Status** : Status of context, e.g. “DISABLED” for disabling DRS _TEXT_

- **Flag** : Type of flag being waved “GREEN”, “RED”, “YELLOW”, “CLEAR”, “CHEQUERED” _TEXT_

- **Scope** : Scope of message “Track”, “Sector”, “Driver” _TEXT_

- **MarshallingSector** : Affected marshalling sector (or mini-sector) for sector-scoped messages  _INTEGER_

- **AffectedDriverNumber**: Affected driver for CarEvent messages _INTEGER_

- **Lap** : Number of the lap in which the message was displayed _INTEGER_

24. ### WeatherData:
This table contains the weather data for each race. Data is available from 2018 onwards.

**Columns:**

- **GrandPrixName**: Name of the Grand Prix. _TEXT_
- **SessionName**: Name of the session. _TEXT_
- **SessionTimestamp**: The timestamp in which the data was recorded. Timestamp is represented in time elapsed since start of session. _TEXT_
- **SessionTimestampInSeconds** Session timestamp in seconds. _REAL_
- **AirTemperatureC**: Air temperature in degrees Celsius. _REAL_
- **HumidityPercent**: Humidity in percentage. _REAL_
- **AirPressureMbar**: Air pressure in millibars. _REAL_
- **IfRainfall**: Whether it is raining. _BOOLEAN_
- **TrackTemperatureC**: Track temperature in degrees Celsius. _REAL_
- **WindDirectionDegrees**: Wind direction in degrees. _REAL_
- **WindSpeedMs**: Wind speed in metres per second. _REAL_
- **GrandPrixID**: Foreign key to GrandsPrix. _INTEGER_
- **SessionID**: Foreign key to Sessions. _INTEGER_

25. ### HistoricalWeather:
This table contains weather data for all races, sourced from the [Open-Meteo Historical Weather API](https://open-meteo.com/en/docs/historical-weather-api) using each circuit's coordinates. Unlike `WeatherData`, which uses live per-session telemetry (2018+ only) and stores rainfall as a boolean, this data is hourly and gives quantitative precipitation, so a session may have multiple rows spanning its start and end time, and the two tables intentionally overlap in years covered.

**Columns:**

- **GrandPrixName**: Name of the Grand Prix. _TEXT_
- **SessionName**: Name of the session. _TEXT_
- **TimestampUTC**: The UTC timestamp of this hourly data point. _TEXT_
- **AirTemperatureC**: Air temperature in degrees Celsius. _REAL_
- **SoilTemperatureC**: Soil temperature at the surface in degrees Celsius, used as a substitute for track temperature since historical track temperature is not available. _REAL_
- **HumidityPercent**: Humidity in percentage. _REAL_
- **AirPressureMbar**: Air pressure in millibars. _REAL_
- **PrecipitationMm**: Precipitation in millimetres for the hour. _REAL_
- **WindSpeedMs**: Wind speed in metres per second. _REAL_
- **WindDirectionDegrees**: Wind direction in degrees. _REAL_
- **GrandPrixID**: Foreign key to GrandsPrix. _INTEGER_
- **SessionID**: Foreign key to Sessions. _INTEGER_

26. ### FamilyRelations:
This table contains family relationships between drivers in Formula 1 history. Each relationship is stored bidirectionally (e.g. Jacques Villeneuve is Gilles Villeneuve' son, then both "Jacques Villeneuve is Gilles Villeneuve's son" and "Gilles Villeneuve is Jacques Villeneuve's father" are stored).

**Columns:**

- **ID**: Unique family relation ID. _INTEGER PRIMARY KEY AUTOINCREMENT_
- **Driver**: Name of the driver. _TEXT_
- **RelatedDriver**: Name of the related driver. _TEXT_
- **RelationType**: Type of relationship (e.g., "Father", "Son", "Brother", "Sister", "Uncle", "Nephew", "Grandfather", "Grandson", "Half-Brother", "Great Uncle"). _TEXT_
- **DriverID**: Foreign key to Drivers table. _INTEGER_
- **RelatedDriverID**: Foreign key to Drivers table. _INTEGER_
- **UNIQUE constraint**: (DriverID, RelatedDriverID) to prevent duplicate relationships.

**Example relationships:**
- Jacques Villeneuve Villeneuve → Gilles Villeneuve Villeneuve: "Son"
- Gilles Villeneuve Villeneuve → Jacques Villeneuve Villeneuve: "Father"
- Michael Schumacher → Ralf Schumacher: "Brother"
- Ralf Schumacher → Michael Schumacher: "Brother"

27. ### ScheduleVersions:
This table contains snapshots of each season's **planned** calendar. The calendar can change during a season (races cancelled, postponed, or replaced), so instead of overwriting it, a new version is saved every time the calendar changes. Each run of `writedb.py` fetches the current season's calendar and only saves a new version if it differs from the latest one. Schedules are available from 2018 onwards, from FastF1 (which reads them from the [f1schedule](https://github.com/theOehrly/f1schedule) repository). Pre-season testing is not included.

A season that was already over when it was first snapshotted only has its final calendar (version 1), as earlier versions are not available from the source. Cancelled and postponed events of those seasons are therefore not recorded yet.

**Columns:**

- **ID**: Unique schedule version ID. _INTEGER PRIMARY KEY AUTOINCREMENT_
- **Season**: The season this calendar is for. _INTEGER_
- **VersionNumber**: Version of the season's calendar, starting at 1 for the first snapshot. _INTEGER_
- **RetrievedAtUTC**: When the calendar was fetched, as a UTC ISO 8601 timestamp. _TEXT_
- **Source**: Where the calendar came from (e.g. "fastf1"). _TEXT_
- **ContentHash**: SHA-256 hash of the fetched calendar, used to detect whether it has changed since the previous version. _TEXT_
- **UNIQUE constraint**: (Season, VersionNumber).

28. ### Schedule:
This table contains the events (Grands Prix) of each schedule version. Every version contains the full picture of the season as it was planned at that time: the events currently on the calendar, plus any events that were cancelled earlier in the season.

These are *planned* events and sessions. They are intentionally kept apart from `GrandsPrix` and `Sessions`, which hold what actually took place and what results reference.

An event is identified across versions by its **EventName and Location together**. A Grand Prix that keeps its name but moves venue is therefore a different event. For example, in 2026 the Bahrain Grand Prix at Sakhir was cancelled, and a Bahrain Grand Prix was later added at Sepang (Kuala Lumpur); these are two separate rows.

**Columns:**

- **ID**: Unique schedule event ID. _INTEGER PRIMARY KEY AUTOINCREMENT_
- **ScheduleVersionID**: Foreign key to ScheduleVersions. _INTEGER_
- **Season**: The season. _INTEGER_
- **RoundNumber**: Round number in that version's calendar. NULL for cancelled events, as they are no longer a round. _INTEGER_
- **EventName**: Name of the event (e.g. "Austrian Grand Prix"). _TEXT_
- **OfficialEventName**: Official name of the event. _TEXT_
- **Country**: Country of the event. _TEXT_
- **Location**: Location of the event (e.g. "Spielberg"). _TEXT_
- **EventDate**: Local date of race day, as YYYY-MM-DD. _TEXT_
- **EventFormat**: Weekend format (e.g. "conventional", "sprint", "sprint_shootout", "sprint_qualifying"). _TEXT_
- **Status**: One of:
  - `scheduled`: on the calendar (including events newly added to it).
  - `postponed`: on the calendar, but with a later date than in the previous version. This includes a cancelled event that comes back on a later date. Once an event is postponed, it stays `postponed` in later versions.
  - `cancelled`: was in the previous version, but is no longer on the calendar. It is carried forward, along with the sessions that were planned for it, into all later versions.

  _TEXT_
- **GrandPrixID**: Foreign key to GrandsPrix: the Grand Prix held on this event's date, or one day either side of it, as `EventDate` is the local date and a race run in the evening can fall on the next day in UTC (e.g. the Las Vegas Grand Prix). NULL if the event has not been held (yet) or was cancelled. _INTEGER_
- **UNIQUE constraint**: (ScheduleVersionID, EventName, Location).

**Example:** the latest version of the 2026 calendar, including cancelled events:
```sql
SELECT s.RoundNumber, s.EventName, s.Location, s.EventDate, s.Status
FROM Schedule s
JOIN ScheduleVersions v ON s.ScheduleVersionID = v.ID
WHERE v.Season = 2026
  AND v.VersionNumber = (SELECT MAX(VersionNumber) FROM ScheduleVersions WHERE Season = 2026)
ORDER BY s.EventDate;
```

29. ### ScheduleSessions:
This table contains the planned sessions of each event in the Schedule table, with their planned start times in both UTC and local time. A cancelled event keeps the sessions that had been planned for it.

**Columns:**

- **ID**: Unique schedule session ID. _INTEGER PRIMARY KEY AUTOINCREMENT_
- **ScheduleID**: Foreign key to Schedule. _INTEGER_
- **SessionName**: Name of the session (e.g. "Practice 1", "Sprint Qualifying", "Race"). _TEXT_
- **SessionNumber**: Position of the session within the event, from 1 to 5. _INTEGER_
- **StartTimeTimestampUTC**: Planned start time as a Unix timestamp in seconds, in UTC. NULL if the time is not known. _INTEGER_
- **StartTimeinDatetimeUTC**: Planned start time as a UTC ISO 8601 datetime. NULL if the time is not known. _TEXT_
- **StartTimeinDatetime**: Planned start time as a local ISO 8601 datetime at the circuit, including the UTC offset (e.g. `2023-04-28T13:30:00+04:00`). NULL if the time is not known. _TEXT_
- **UNIQUE constraint**: (ScheduleID, SessionNumber).

30. ### CircuitMarkers:
This table contains the positions of the corners, marshal lights and marshal sectors of a circuit layout, from [MultiViewer](https://multiviewer.app/) (the data FastF1 provides as `CircuitInfo`). It can be used to annotate a track map, and together with `CircuitLayouts.Rotation` to line telemetry coordinates up with the official track map. The data is drawn by hand, so it is good for visualisation but not precise.

Positions are in the same coordinate system as FastF1's position telemetry. They are not GPS coordinates, and are not in the coordinate system of `CircuitLayouts.SVG`.

MultiViewer only has records for seasons since 2018, so only layouts raced since then can have markers. Some layouts have none, and not every layout has all three types: for example, Mugello only has marshal sectors.

**Columns:**

- **ID**: Unique marker ID. _INTEGER PRIMARY KEY AUTOINCREMENT_
- **CircuitLayoutID**: Foreign key to CircuitLayouts. _INTEGER_
- **MarkerType**: `corner`, `marshal_light` or `marshal_sector`. _TEXT_
- **Number**: Number of the corner, marshal light or marshal sector. _INTEGER_
- **Letter**: Distinguishes corners that share a number on some circuits, e.g. the "A" in "2A". An empty string if there is none. _TEXT_
- **X**: X position on the track map. _REAL_
- **Y**: Y position on the track map. _REAL_
- **Angle**: Angle in degrees used to offset a marker's label on a track map, usually at right angles to the track. _REAL_
- **UNIQUE constraint**: (CircuitLayoutID, MarkerType, Number, Letter).
31. ### RedFlags
    This table contains details of all red flags shown during World Championship Grands Prix, scraped from Wikipedia.
    
    **Restarted Codes:**
    - `N`: Race was not restarted
    - `Y`: Race was restarted over the original distance
    - `R`: Race was resumed to complete the originally scheduled distance
    - `S`: Race was restarted or resumed with the originally scheduled distance not completed

    **Columns:**
    - **ID**: Unique red flag ID. _INTEGER PRIMARY KEY AUTOINCREMENT_
    - **GrandPrix**: Full Grand Prix name. _TEXT_
    - **Season**: Year of the Grand Prix. _INTEGER_
    - **Lap**: Lap number on which the red flag was displayed. _INTEGER_
    - **RestartedCode**: Single-letter code indicating restart classification (`N`, `Y`, `R`, or `S`). _TEXT_
    - **RestartedDescription**: Plain text description of the restart status code. _TEXT_
    - **LeaderAtStoppage**: Driver leading the race at the time of stoppage / red flag. _TEXT_
    - **Reason**: Incident or weather condition that prompted the red flag. _TEXT_
    - **Notes**: Additional context regarding drivers who failed to make the restart or specific race circumstances. _TEXT_
    - **GrandPrixID**: Foreign key to GrandsPrix. _INTEGER_
    - **LeaderID**: Foreign key to Drivers. _INTEGER_

32. ### TelemetryIndex:
This table indexes the car telemetry files in the `telemetry/` folder, one row per driver per session, from 2018 onwards. The telemetry itself is not stored in the SQLite database: it is hundreds of thousands of samples per race, so each driver's laps in a session are kept in one zstd-compressed [Parquet](https://parquet.apache.org/) file and this table says where to find it. The data comes from [TracingInsights](https://tracinginsights.com/), which processes the F1 live timing feed with FastF1, and is available a short while after each session.

Files are laid out as `telemetry/<Season>/<GrandPrixID>/<SessionID>/<DriverID>.parquet`, and can be read with any Parquet library, for example `pandas.read_parquet(path)`.

**Columns:**

- **ID**: Unique ID. _INTEGER PRIMARY KEY AUTOINCREMENT_
- **GrandPrixName**: Name of the Grand Prix. _TEXT_
- **SessionName**: Name of the session. _TEXT_
- **Driver**: Name of the driver. _TEXT_
- **FilePath**: Path of the Parquet file, relative to the repository root. _TEXT_
- **RowCount**: Number of samples in the file. _INTEGER_
- **LapCount**: Number of laps in the file. _INTEGER_
- **Source**: Where the telemetry was downloaded from (`TracingInsights`). _TEXT_
- **RetrievedAtUTC**: When the file was downloaded, as an ISO 8601 UTC timestamp. _TEXT_
- **GrandPrixID**: Foreign key to GrandsPrix. _INTEGER_
- **SessionID**: Foreign key to Sessions. _INTEGER_
- **DriverID**: Foreign key to Drivers. _INTEGER_

**Parquet file columns:**

- **lap**: Lap number, matching `LapByLap.Lap`. _uint8_
- **time_ms**: Time since the start of the lap in milliseconds. It restarts at 0 on every lap. _int32_
- **speed**: Speed in km/h. _float32_
- **rpm**: Engine speed in revolutions per minute. _float32_
- **throttle**: Throttle pedal position in percent. Values of 104 occasionally appear in the source data and are not valid pedal positions. _float32_
- **gear**: Gear selected. _int8_
- **brake**: Whether the brake is applied. _bool_
- **drs**: DRS state, 0 when closed. This is always 0 from 2026, when DRS was replaced by active aerodynamics, which is not recorded. _uint8_
- **distance**: Distance driven since the start of the lap in metres. _float32_
- **x**, **y**, **z**: Position of the car, in the same coordinate system as `CircuitMarkers`. _float32_

**About the sampling rate:** samples are not evenly spaced. The rate varies by season and session, from roughly 4 to 9 samples per second (most are around 7.5), because the car data and position streams are merged. Not every channel is measured at that rate (throttle, for example, changes about 4.5 times a second), so some values are interpolated between real readings. Use `time_ms` rather than assuming a fixed interval.


## Roadmap:
Please check our [roadmap](https://github.com/mclarenmp4-22/f1resultsdatabase/blob/main/ROADMAP.md) for the next steps.

## Contributing
Please check the [contributing guide](https://github.com/mclarenmp4-22/f1resultsdatabase/blob/main/CONTRIBUTING.md) for how to contribute to the project.

This project is governed by our [Code of Conduct](https://github.com/mclarenmp4-22/f1resultsdatabase/blob/main/CODE_OF_CONDUCT.md). By participating, you are expected to uphold it.

---

## ⚖️ Licensing & Legal Terms

This project uses a split-licensing model to ensure that both the scraper application and the compiled historical data remain open, public, and accessible to everyone forever.

* **Scraper Code:** Licensed under the **GNU General Public License v3 (GPLv3)**. Any modified versions of this code must also be made open-source under the same license. See [LICENSE.txt](https://github.com/f1database/f1database/blob/main/LICENSE.txt) for details.
* **F1 Database/Datasets:** Licensed under the **Open Database License (ODbL) v1.0**. You are free to share, modify, and use the database. However, any altered or derivative versions of this database must also be released under the ODbL to keep the facts open-source. See [DATA-LICENCE.txt](https://github.com/f1database/f1database/blob/main/DATA-LICENCE.txt) for details.

---

### 🛑 Disclaimer & Limitation of Liability

#### 1. No Affiliation
This project is an independent, community-driven historical archive. It is entirely unofficial and is not affiliated with, endorsed by, sponsored by, or associated in any way with the Formula One group of companies, the Fédération Internationale de l'Automobile (FIA), or any specific racing team or driver. All product and company names, logos, or trademarks are the property of their respective owners.

#### 2. Educational & Historical Purpose Only
The code and datasets in this repository are provided strictly for educational, research, and personal historical analysis purposes. This data may be used for commercial purposes at your own risk.

#### 3. Absolute Liability Waiver
THE SOFTWARE AND DATA ARE PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, AND NONINFRINGEMENT. 

IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES, OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT, OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE, THE DATA, THE USE OF THE SOFTWARE, OR ANY OTHER DEALINGS IN THE SOFTWARE.

#### 4. Scraping Accountability
Users are entirely responsible for their own compliance with local laws, rate limits, and the Terms of Service of any external website targeted by this scraper. The creator of this repository assumes no responsibility or liability for IP bans, server disruptions, or legal actions resulting from the execution of this code by third parties.

---
