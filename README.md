# iRacingStats

A self-hosted stats site for your own iRacing career. Point it at the JSON or
CSV your member-site Results Archive already exports and it gives you a browsable record
of every race you have started: seasons, series, tracks, cars, finishing
positions, incident rate over time.

No API key, no scraping, no account credentials anywhere. No dependencies
either — the loader and the server are both standard-library Python, and the
front end is hand-written HTML/CSS/JS with no build step.

**Your data is never in the image.** The container ships the code only; the
database lives on your machine and is mounted in at run time.

---

## Quick start

```bash
# 1. build your database from the export (see "Getting your data" below)
python3 load_iracing_data.py --exports ~/Downloads/iracing

# 2. run it
docker compose up -d      # or: python3 server.py

# 3. open it
open http://localhost:8090/
```

Requires Python 3.8+ for the loader. Docker is optional — `server.py` runs
standalone and takes `--port` and `--db`.

If you would rather not install Python at all, run the loader inside the image:

```bash
docker compose build
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$PWD/data:/app/data" -v "$HOME/Downloads/iracing:/in:ro" \
  iracingstats:latest python3 load_iracing_data.py --exports /in --db /app/data/stats.db
```

The `--user` flag matters: the image runs unprivileged, so without it the
container cannot write into a directory your account owns. The server itself
needs no such flag — it only ever reads.

---

## Getting your data

1. Sign in at <https://members.iracing.com/>
2. **Results & Stats → Results Archive**
3. Choose a date range and search. **The site caps a query at 90 days**, so a
   full career takes several searches — work backwards in 90-day windows.
4. Above the results table, click the download icon → **Download JSON** or
   **Download CSV**. Either format works.
5. Repeat on the **Hosted** tab if you race hosted or league sessions.
6. Put every downloaded file in one folder and point the loader at it.

Filenames do not matter — the loader identifies each file by its contents, and
you can mix JSON and CSV in the same folder. Overlapping windows are fine; rows
dedupe on `subsession_id` whichever format they arrived in. Re-running the
loader **merges**, so as you download older windows the career fills in behind
you.

### If you only have CSV

The CSV download has no `cust_id` column, so tell the loader whose career it is:

```bash
python3 load_iracing_data.py --exports ~/Downloads/iracing --cust-id 123456
```

Your customer id is on your iRacing profile page. You only need it once — it is
stored in the database, and later runs pick it up from there.

One consequence: because a CSV carries no owner, the loader cannot tell whose
races are in it. With JSON it warns if it sees more than one customer id in a
folder; with CSV it has nothing to check, so keep one person's exports per
database.

Two other things the loader handles for you, both verified by loading the same
129 sessions from each format and comparing every column:

- **CSV positions are 1-indexed, JSON positions are 0-indexed.** The loader
  normalises to the JSON convention, so a session loaded from either file
  produces the same row and the two dedupe against each other properly. If a
  future CSV layout disagrees, `--csv-positions zero|one` forces it.
- **`driver_changes` and `winner_group_id` are absent from the CSV.** Nothing on
  the site uses them; every other column matches exactly.

The loader tells you if it finds a month with no sessions inside your career
span. That is almost always a window you have not exported yet rather than a
month you did not race.

```
  read           1234 rows from 5 file(s)
  career_results 1200 sessions  (2024-03-01 to 2025-06-30)
  career_races   500 official race starts
  driver         cust 999999
```

---

## What you get

| Tab | Built from |
|---|---|
| **Overview** | Stat tiles, incidents-per-lap by month against the 0.20 break-even line, finishing-position distribution, season-by-season table |
| **Races** | Every official race start. Filter by category, season, or wins; free-text search; sortable; click a row for the full result |
| **Series** / **Tracks** / **Cars** | Aggregates — starts, wins, average finish, laps, incident rate |
| **Pace** | Best lap per track+car. Fastest lap of the event comes from the export; **your own lap times do not** — see below |
| **Incidents** | Where on the lap each incident happened. Needs telemetry; empty otherwise |
| **Rivals** | Who else was on your grids. Needs telemetry; empty otherwise |

Deep links: `?tab=races`, `?theme=light|dark`, `?race=<subsession_id>` — the
last opens straight into one race, so a specific result is shareable.

Light and dark are both defined explicitly and the in-page toggle wins over the
OS setting in either direction.

---

## Going deeper: per-race exports

The Results Archive gives you one summary row per race. iRacing also lets you
export a **single race in full** — open that race's result and use the download
control in the modal, which saves `eventresult_<subsession>_<simsession>.csv`.

That file is a different shape and carries what the archive cannot: the whole
grid, every driver's **iRating and safety rating before and after**, and lap
times. One file per race, so it is only worth it for races you care about — or
all of them, if you are willing to click.

```bash
python3 import_event_results.py --dir ~/Downloads
```

Run `load_iracing_data.py` first. The per-race file has no discipline, track id
or official flag, so the importer takes those from the career layer where it
already knows the race, and leaves them blank where it does not.

Import them and the site changes: **Rivals** fills with everyone you have shared
a grid with, **Pace** gains your own lap times next to the class best, and an
**iRating chart** appears on the Overview showing the gain or loss for every
race — which no other source can produce.

### Quirks the importer handles for you

- **A negative `Cust ID` is a team, not a person.** In a team race the export
  lists the team entry *and* each of its drivers. Team rows are skipped; their
  drivers carry the same finishing position, so nothing is lost, and counting
  them would put teams in your rivals list.
- **Lap times change format with length** — `13.532` under a minute,
  `1:47.088` over one. Both become seconds.
- **In-class positions are not in the file.** They are computed by ranking over
  *distinct teams* within a car class, so the several drivers sharing one car
  in an endurance race do not each consume a place.
- **`has_telemetry` still means telemetry.** A per-race export fills the same
  tables but involves no telemetry, so it is counted separately and the
  Overview tile does not start claiming coverage that is not there.

### What the export cannot tell you

Two tabs stay empty on an export-only database, and it is worth knowing why
rather than assuming something is broken.

- **Your own lap times are not in the export.** `event_best_lap_time` is the
  fastest lap set by *anyone* in that event, not by you. Verified on a career
  where both sources were available: it matched the field best in 11 of 12
  overlapping races, and the driver's own lap only in the 2 they were fastest
  in. So the Pace tab shows field best from the export, and fills in a personal
  best column only if you supply per-session telemetry.
- **The export is your own result line, not the grid.** Nobody else's finishing
  position, iRating or licence is in it, so the Rivals tab has nothing to show
  without telemetry.
- **Incident *type* and position are telemetry-only.** The export carries a
  count and nothing more.

The database has the tables for all of that (`captures`, `segments`, `results`,
`drivers`, `incidents`); the loader creates them empty. The site detects that
and adjusts what it shows rather than displaying a wall of zeroes.

---

## Schema

```
career_results   one row per session you took part in, official and hosted
career_races     view: official race starts, positions 1-indexed
race_coverage    view: those races, flagged for telemetry coverage
meta             key/value — whose career this is
```

Plus the empty capture-layer tables described above.

```sql
SELECT substr(start_time,1,10) day, series_name, track_name,
       position, incidents
FROM   career_races
ORDER  BY start_time DESC LIMIT 20;
```

### Things that will bite you if you query it yourself

These are handled in the code, but they are easy to get wrong from scratch.

- **iRacing's position fields are 0-indexed.** `finish_position = 0` is a win.
  The `career_races` view exposes 1-indexed `position` and `class_position`
  alongside the raw fields; use those and the numbers read the way you expect.
- **A win is a *class* win.** `is_win` uses `finish_position_in_class`, which is
  what iRacing counts and what its own career stats report. Overall-P1
  finishes are a different, smaller number in multiclass series.
- **Declare integer columns as INTEGER.** SQLite applies a column's affinity to
  the other side of a comparison, so `finish_position < 5` against a TEXT
  column compares lexicographically — `'10' < '5'` — and returns nonsense with
  no error. The loader declares types explicitly rather than inferring them
  from values, precisely because a sparse export leaves whole columns NULL.
- **Hosted rows are shaped differently.** They carry no season or series and a
  `host` object instead; their multi-car class list is kept as JSON in
  `cars_json`. `career_races` filters to `source = 'official'` so hosted and
  league sessions stay in `career_results` without distorting career stats.

---

## Files

| Path | What |
|---|---|
| `load_iracing_data.py` | Results Archive JSON or CSV → `data/stats.db`. Start here. |
| `import_event_results.py` | Per-race `eventresult_*.csv` → the capture layer. Optional, adds depth. |
| `server.py` | The web server. Stdlib only; reads SQLite read-only. |
| `web/` | The UI — `index.html`, `app.js`, `charts.js`, `styles.css`. |
| `Dockerfile`, `compose.yaml` | Container build and run. |
| `data/stats.db` | Your database. Created by the loader; never in the image. |

## Privacy

The image contains code and nothing else — no database, no customer id, no
hostnames. The customer id is read at run time from the `meta` table the loader
writes into *your* database, and can be overridden with `--cust-id` or
`IRSTATS_CUST_ID`.

The app makes no outbound network requests. Nothing is sent anywhere; it reads
one local file and serves it on a port you choose. It has no authentication of
its own, so if you expose it beyond localhost, put it behind something that
does.
