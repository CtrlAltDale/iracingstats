# iRacingStats

A self-hosted stats site for your own iRacing career. Point it at the files the
member site already exports and it gives you a browsable record of every race
you have started — seasons, series, tracks, cars, finishing positions, incident
rate over time — and, if you export the per-race results too, the whole grid,
your team entries, and what every race did to your iRating and safety rating.

No API key, no scraping, no account credentials anywhere. No dependencies
either — the loader and the server are both standard-library Python, and the
front end is hand-written HTML/CSS/JS with no build step.

**Your data is never in the image.** The container ships the code only; the
database lives on your machine and is mounted in at run time.

---

## Quick start

```bash
# 1. build your database from whatever you have downloaded
python3 load_iracing_data.py --exports ~/Downloads --with-event-results

# 2. run it
docker compose up -d      # or: python3 server.py

# 3. open it
open http://localhost:8090/
```

Requires Python 3.8+ for the loader. Docker is optional — `server.py` runs
standalone and takes `--port` and `--db`.

The loader takes **either** kind of export, mixed in one folder, and sorts them
out by content:

| You downloaded | From | What it gives |
|---|---|---|
| `search_results*.json` / `.csv` | Results & Stats → Results Archive | your whole career, one row per session |
| `eventresult_<id>_0.csv` | the download control on a single race's result | that one race in full — every driver, lap times, rating before and after |

Start with the Results Archive: it is one download per 90 days and it builds the
career. The per-race exports are one file per race, so they are more work, but
they carry things the archive simply does not have — see **Going deeper** below.

Without `--with-event-results` the loader tells you the per-race files are there
and how to load them, rather than loading them; the flag says do it in one pass.
`import_event_results.py` does that half on its own if you prefer.

If you would rather not install Python at all, run the loader inside the image:

```bash
docker compose build
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$PWD/data:/app/data" -v "$HOME/Downloads:/in:ro" \
  iracingstats:latest python3 load_iracing_data.py --exports /in \
    --with-event-results --db /app/data/stats.db
```

The `--user` flag matters: the image runs unprivileged, so without it the
container cannot write into a directory your account owns. The server itself
needs no such flag — it only ever reads.

---

## Getting your data

### The career: Results Archive

1. Sign in at <https://members.iracing.com/>
2. **Results & Stats → Results Archive**
3. Choose a date range and search. **The site caps a query at 90 days**, so a
   full career takes several searches — work backwards in 90-day windows.
4. Above the results table, click the download icon → **Download JSON** or
   **Download CSV**. Either works.
5. Repeat on the **Hosted** tab if you race hosted or league sessions.
6. Put every downloaded file in one folder and point the loader at it.

Filenames do not matter — the loader identifies each file by its contents, and
you can mix JSON and CSV. Overlapping windows are fine; rows dedupe on
`subsession_id`. Re-running **merges**, so the career fills in behind you as you
download older windows.

Stop when a window comes back empty: that is the start of your career, and every
earlier window will be empty too.

### The detail: per-race exports

Open a single race's result and use its download control. You get
`eventresult_<subsession>_<simsession>.csv` — one file per race, containing the
whole grid.

These are worth the effort because **nothing else has this data**. Import them
and the site gains a **Teams** page, an **Insights** tab, real per-discipline
**iRating and safety-rating history**, your own **lap times** on the Pace tab,
and a populated **Rivals** list.

`check_event_exports.py` tells you which races you are still missing, and
`--ids` prints the bare list:

```bash
python3 check_event_exports.py
python3 check_event_exports.py --ids
```

It also distinguishes the two failure shapes, which matters if you automate the
downloading: scattered gaps mean individual exports failed, while one unbroken
run to the end means the process stopped there and everything after it never
happened — whatever it reported at the time.

### If you only have CSV

The Results Archive CSV has no `cust_id` column, so tell the loader whose career
it is:

```bash
python3 load_iracing_data.py --exports ~/Downloads --cust-id 123456
```

Your customer id is on your iRacing profile page. You only need it once — it is
stored in the database and later runs pick it up from there.

One consequence: because a CSV carries no owner, the loader cannot tell whose
races are in it. With JSON it warns if it sees more than one customer id in a
folder; with CSV it has nothing to check, so keep one person's exports per
database.

Two other things the loader handles for you, both verified by loading the same
129 sessions from each format and comparing every column:

- **CSV positions are 1-indexed, JSON positions are 0-indexed.** The loader
  normalises to the JSON convention, so a session loaded from either file
  produces the same row and the two dedupe properly. If a future CSV layout
  disagrees, `--csv-positions zero|one` forces it.
- **`driver_changes` and `winner_group_id` are absent from the CSV.** Nothing on
  the site uses them; every other column matches exactly.

---

## What you get

| Tab | Built from |
|---|---|
| **Overview** | Stat tiles, incidents-per-lap by month against the 0.20 break-even line, finishing-position distribution, season-by-season table |
| **Races** | Every official race start. Filter by category, season, or wins; free-text search; sortable; click a row for the full result |
| **Series** / **Tracks** / **Cars** | Aggregates — starts, wins, average finish, laps, incident rate |
| **Pace** | Best lap per track+car. Fastest lap of the event comes from the export; **your own lap times do not** — see below |
| **Insights** | What a mistake costs in iRating, where you finish, race pace against your class, qualifying vs racecraft. Needs per-race exports |
| **Teams** | Every team entry you have driven, expandable per race to the whole crew — laps each driver did, laps led, incidents, best lap, rating change. Needs per-race exports |
| **Incidents** | Where on the lap each incident happened. Needs telemetry |
| **Rivals** | Everyone you have shared a grid with. Needs per-race exports or telemetry |

Deep links: `?tab=races`, `?theme=light|dark`, `?race=<subsession_id>` — the
last opens straight into one race, so a specific result is shareable.

Light and dark are both defined explicitly and the in-page toggle wins over the
OS setting in either direction.

---

## Going deeper: per-race exports

These carry what the Results Archive cannot, and it is worth knowing exactly
what changes:

- **Teams** — the whole crew per race, because a team entry lists the team *and*
  each of its drivers.
- **Insights** — every question there compares your rating before and after a
  race, which no other source records.
- **iRating and safety rating over time**, per discipline, from real post-race
  values rather than a scatter of session-start samples.
- **Pace** — your own lap times beside the class best. The Results Archive's
  `event_best_lap_time` is the fastest lap by *anyone* in the event, not yours.
- **Rivals** — everyone on the grid, not just your own result line.

```bash
python3 import_event_results.py --dir ~/Downloads
```

Run `load_iracing_data.py` first. The per-race file has no discipline, track id
or official flag, so the importer takes those from the career layer where it
already knows the race, and leaves them blank where it does not.

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
- **A team entry and a solo start are told apart properly.** The two sources
  disagree: a telemetry capture writes team id `0` with your own name as the
  "team", while the export writes the team id equal to your customer id. Take
  either test alone and every solo race collapses into one enormous fake team.
- **Team ids differ in sign between the sources** — positive from telemetry,
  negative from the export. Everything keys on the absolute value.

### Ratings are per discipline

iRacing keeps a separate iRating and safety rating for **each** discipline —
sports car, formula, oval, dirt road, dirt oval. So the site draws one small
chart per discipline rather than a single line: the scales genuinely differ, and
splicing them would draw a jump every time you changed category, as though your
rating had moved when it had not.

A discipline with fewer than three races gets its numbers but no plot, because
there is no shape to read.

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
| `check_event_exports.py` | Which races still have no per-race export. `--ids` for a plain list. |
| `data/eventresults/` | Where per-race exports are kept once downloaded. |
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
