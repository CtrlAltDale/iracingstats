#!/usr/bin/env python3
"""Load your iRacing Results Archive export into stats.db.

This is the only step between "I have an iRacing account" and a working site.
Standard library only -- no pip install, no API key, no scraping.

--------------------------------------------------------------------------
Getting the data out of iRacing
--------------------------------------------------------------------------
1. Sign in at https://members.iracing.com/
2. Results & Stats -> Results Archive
3. Pick a date range and hit search.  The site caps a query at 90 days, so a
   full career is several searches -- do them back to back.
4. Above the results table, use the download icon -> "Download JSON" or
   "Download CSV".  Either works; JSON is preferred only because the CSV has
   no cust_id column, so a CSV-only load needs --cust-id.
5. Do the same on the "Hosted" tab if you race in hosted/league sessions.
6. Drop every downloaded file into one folder.  Names do not matter, the two
   formats can be mixed freely, and overlapping windows are fine -- rows
   dedupe on subsession_id regardless of which file they came from.

Then:

    python3 load_iracing_data.py --exports ~/Downloads/iracing --db data/stats.db

Re-run it any time with newer downloads; it merges rather than replaces, so
the database grows as you add windows.

CSV and JSON produce the same rows -- verified by loading the same 129
subsessions from each and comparing every column.  The only two fields the CSV
does not carry are `driver_changes` and `winner_group_id`, neither of which the
site uses.

--------------------------------------------------------------------------
What ends up where
--------------------------------------------------------------------------
career_results   one row per session you took part in, official and hosted
career_races     view: your official race starts, positions made 1-indexed
race_coverage    view: those races, flagged for telemetry coverage
meta             key/value -- who this database belongs to

The capture layer (`captures` / `segments` / `results` / `drivers` /
`incidents`) is created
empty.  It only fills in if you also have per-session telemetry, which the
Results Archive export does not contain; the site detects that it is empty and
hides the tabs that need it.  The tables have to exist either way, because the
views join against them.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

LOADER_VERSION = "1.0"

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
#
# Column types are declared, not inferred from the data.  That is not
# cosmetic: SQLite applies a column's affinity to the *other* operand of a
# comparison, so `finish_position < 5` against a TEXT column compares
# lexicographically -- '10' < '5' -- and silently returns nonsense.  A sparse
# export (a career with no hosted races, say) leaves whole columns NULL, and
# anything inferring types from values would call those TEXT.

INT_COLS = [
    "cust_id", "session_id", "event_type", "official_session", "season_id",
    "season_year", "season_quarter", "race_week_num", "series_id",
    "season_license_group", "event_strength_of_field", "champ_points",
    "drop_race", "license_category_id", "car_id", "car_class_id",
    "starting_position", "starting_position_in_class",
    "finish_position", "finish_position_in_class",
    "laps_complete", "laps_led", "incidents", "driver_changes",
    "event_laps_complete", "event_average_lap", "event_best_lap_time",
    "num_drivers", "num_cautions", "num_caution_laps", "num_lead_changes",
    "winner_ai", "winner_group_id", "league_id", "league_season_id",
    "private_session_id", "heat_race", "practice_length", "qualify_length",
    "qualify_laps", "race_length", "race_laps", "track_id",
]

TEXT_COLS = [
    "start_time", "end_time", "event_type_name", "season_license_group_name",
    "series_name", "series_short_name", "license_category", "car_name",
    "car_name_abbreviated", "car_class_name", "car_class_short_name",
    "winner_name", "league_name", "league_season_name", "session_name",
    "created", "track_name", "track_config_name", "source",
    "cars_json", "host_json",
]

# subsession_id is the primary key and is declared separately.
COLS = INT_COLS + TEXT_COLS

# Fields lifted out of nested objects rather than copied straight across.
DERIVED = {"source", "track_id", "track_name", "track_config_name",
           "cars_json", "host_json"}

CAREER_SCHEMA = """
CREATE TABLE IF NOT EXISTS career_results (
    subsession_id INTEGER PRIMARY KEY,
    {cols}
);
CREATE INDEX IF NOT EXISTS ix_cr_start  ON career_results(start_time);
CREATE INDEX IF NOT EXISTS ix_cr_type   ON career_results(event_type_name);
CREATE INDEX IF NOT EXISTS ix_cr_series ON career_results(series_id);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# The capture layer.  Created empty here so the views below have something to
# join to; a telemetry importer fills it in if you have one.
CAPTURE_SCHEMA = """
CREATE TABLE IF NOT EXISTS captures (
    capture_dir           TEXT PRIMARY KEY,
    captured_at           TEXT,
    subsession_id         INTEGER,
    session_id            INTEGER,
    series_id             INTEGER,
    season_id             INTEGER,
    league_id             INTEGER,
    race_week             INTEGER,
    event_type            TEXT,
    category              TEXT,
    official              INTEGER,
    sim_mode              TEXT,
    team_racing           INTEGER,
    num_car_classes       INTEGER,
    track_id              INTEGER,
    track_name            TEXT,
    track_display_name    TEXT,
    track_config_name     TEXT,
    track_length_km       REAL,
    track_length_official_km REAL,
    track_type            TEXT,
    track_num_turns       INTEGER,
    build_version         TEXT,
    player_car_idx        INTEGER,
    player_cust_id        INTEGER,
    player_incident_count INTEGER,
    driver_setup_name     TEXT
);
CREATE INDEX IF NOT EXISTS ix_captures_subsession ON captures(subsession_id);
CREATE INDEX IF NOT EXISTS ix_captures_track      ON captures(track_id);

CREATE TABLE IF NOT EXISTS segments (
    capture_dir              TEXT NOT NULL,
    session_num              INTEGER NOT NULL,
    session_type             TEXT,
    session_name             TEXT,
    session_sub_type         TEXT,
    session_laps             TEXT,
    session_time             TEXT,
    session_skipped          INTEGER,
    session_track_rubber     TEXT,
    results_official         INTEGER,
    results_laps_complete    INTEGER,
    results_avg_lap_time     REAL,
    results_num_caution_flags INTEGER,
    results_num_caution_laps  INTEGER,
    results_num_lead_changes  INTEGER,
    fastest_lap_car_idx      INTEGER,
    fastest_lap_num          INTEGER,
    fastest_lap_time         REAL,
    PRIMARY KEY (capture_dir, session_num)
);

CREATE TABLE IF NOT EXISTS results (
    capture_dir     TEXT NOT NULL,
    session_num     INTEGER NOT NULL,
    car_idx         INTEGER NOT NULL,
    position        INTEGER,
    class_position  INTEGER,
    lap             INTEGER,
    time            REAL,
    fastest_lap     INTEGER,
    fastest_time    REAL,
    last_time       REAL,
    laps_led        INTEGER,
    laps_complete   INTEGER,
    laps_driven     REAL,
    incidents       INTEGER,
    reason_out_id   INTEGER,
    reason_out_str  TEXT,
    PRIMARY KEY (capture_dir, session_num, car_idx)
);

CREATE TABLE IF NOT EXISTS drivers (
    capture_dir         TEXT NOT NULL,
    car_idx             INTEGER NOT NULL,
    cust_id             INTEGER,
    user_name           TEXT,
    abbrev_name         TEXT,
    team_id             INTEGER,
    team_name           TEXT,
    car_number          TEXT,
    car_id              INTEGER,
    car_path            TEXT,
    car_screen_name     TEXT,
    car_class_id        INTEGER,
    car_class_short_name TEXT,
    car_class_rel_speed INTEGER,
    irating             INTEGER,
    lic_level           INTEGER,
    lic_sub_level       INTEGER,
    lic_string          TEXT,
    club_id             INTEGER,
    club_name           TEXT,
    division_id         INTEGER,
    division_name       TEXT,
    flair_name          TEXT,
    incident_count      INTEGER,
    is_spectator        INTEGER,
    car_is_ai           INTEGER,
    car_is_pace_car     INTEGER,
    PRIMARY KEY (capture_dir, car_idx)
);
CREATE INDEX IF NOT EXISTS ix_drivers_cust ON drivers(cust_id);

-- Where each scoring incident happened.  Telemetry-only: the Results Archive
-- export carries a per-race count and nothing else, so this stays empty unless
-- you have a telemetry importer.  Created regardless, so the Incidents tab
-- reports "no rows" the same way every other capture-layer tab does rather
-- than "no such table".
CREATE TABLE IF NOT EXISTS incidents (
    id               INTEGER PRIMARY KEY,
    capture_dir      TEXT NOT NULL,
    session_num      INTEGER,
    session_time     REAL,
    lap              INTEGER,
    lap_dist_pct     REAL,      -- 0..1 around the lap
    points           INTEGER,   -- 1 off track / 2 wall or spin / 4 heavy contact
    incident_type    TEXT,
    running_total    INTEGER,
    surface          INTEGER,   -- 0 off track, 1 in pit stall, 2 approach, 3 on track
    surface_material INTEGER,
    speed            REAL,      -- m/s
    on_pit           INTEGER
);
"""

# Views last: every one of them depends on tables above.
VIEWS = """
DROP VIEW IF EXISTS race_coverage;
DROP VIEW IF EXISTS career_races;
DROP VIEW IF EXISTS canonical_captures;

-- One row per real subsession in the capture layer.  A telemetry logger can
-- restart mid-session and leave several capture dirs for one subsession, so
-- keep the first.  subsession_id = 0 means offline (AI race / test session).
CREATE VIEW canonical_captures AS
SELECT c.*
FROM   captures c
WHERE  c.subsession_id > 0
  AND  c.capture_dir = (SELECT MIN(c2.capture_dir)
                        FROM   captures c2
                        WHERE  c2.subsession_id = c.subsession_id);

-- Official race starts only: the career backbone.
--
-- iRacing's export is 0-indexed on both position fields, which is an easy
-- off-by-one to ship.  Expose 1-indexed `position` / `class_position` so
-- queries read naturally, and keep the raw fields alongside.
--
-- `is_win` uses CLASS position, because that is what iRacing counts as a win
-- and what its own career stats report.
CREATE VIEW career_races AS
SELECT *,
       finish_position + 1          AS position,
       finish_position_in_class + 1 AS class_position,
       (finish_position_in_class = 0) AS is_win
FROM   career_results
WHERE  event_type_name = 'Race' AND source = 'official';

-- Every career race, flagged with whether the capture layer has telemetry for
-- it.  Reads as all-zero until something fills `captures` in.
CREATE VIEW race_coverage AS
SELECT r.subsession_id, r.start_time, r.series_name, r.track_name,
       r.car_name, r.finish_position, r.num_drivers, r.incidents,
       r.laps_complete, r.event_strength_of_field,
       (c.capture_dir IS NOT NULL) AS has_telemetry,
       c.capture_dir
FROM   career_races r
LEFT JOIN canonical_captures c ON c.subsession_id = r.subsession_id;
"""


# ---------------------------------------------------------------------------
# Reading the export
# ---------------------------------------------------------------------------

def find_exports(paths):
    """Every .json / .csv under the given files/directories, sorted, deduped."""
    found = set()
    for p in paths:
        p = os.path.expanduser(p)
        if os.path.isfile(p):
            found.add(os.path.abspath(p))
        elif os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                for f in files:
                    if f.lower().endswith((".json", ".csv")):
                        found.add(os.path.abspath(os.path.join(root, f)))
    return sorted(found)


def harvest(doc):
    """Pull result rows out of whatever shape the download happens to be.

    The member site currently emits a list of chunks, each chunk a list of
    row dicts.  That has changed shape before and may again, so rather than
    hardcoding two levels of nesting, walk anything list-like and take every
    dict that carries a subsession_id.  A dict with a `results`/`data`/`items`
    key is unwrapped too, in case the download ever gains an envelope.
    """
    out, stack, seen = [], [doc], 0
    while stack:
        node = stack.pop()
        seen += 1
        if seen > 2_000_000:  # a malformed file should not spin forever
            break
        if isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, dict):
            if "subsession_id" in node:
                out.append(node)
            else:
                for key in ("results", "data", "items", "chunk_data"):
                    if isinstance(node.get(key), (list, dict)):
                        stack.append(node[key])
    return out


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------
#
# The Results Archive offers a CSV download beside the JSON one. It is the same
# field set with the column headings the web page shows, so the work is mapping
# headings onto the JSON field names and then reusing the JSON path unchanged.
#
# Two differences from the JSON matter and both were verified by loading the
# same 129 subsessions from each export and comparing them field by field:
#
#   1. CSV positions are 1-indexed; JSON positions are 0-indexed. Every one of
#      the 129 shared rows differed by exactly 1. Subtracting 1 also lands the
#      "did not qualify" case correctly: a CSV `Start Position` of 0 becomes
#      -1, which is the sentinel the JSON export uses there.
#   2. The CSV has no `cust_id` column at all, so whose career it is has to
#      come from somewhere else -- see resolve_csv_cust().
#
# `Event Best Lap` is already an integer count of 1/10000 s in both exports, so
# it needs no conversion; -1 sentinels are likewise left exactly as the JSON
# path leaves them, so a session loaded from either file produces the same row.

def _norm(h):
    return re.sub(r"[^a-z0-9]+", "_", (h or "").strip().lower()).strip("_")


# Normalised heading -> the JSON field it means. Taken from a real export
# rather than guessed; note that "Event Type" is the numeric id and "Event Type
# Name" the label, and likewise for License Category -- reading those the wrong
# way round puts a number where the site expects "Sports Car".
CSV_ALIASES = {
    "session_id": "session_id",
    "subsession_id": "subsession_id",
    "start_time": "start_time",
    "end_time": "end_time",
    "is_official": "official_session",
    "series_id": "series_id",
    "series_name": "series_name",
    "series_short_name": "series_short_name",
    "season_id": "season_id",
    "season_year": "season_year",
    "season_quarter": "season_quarter",
    "race_week_num": "race_week_num",
    "event_type": "event_type",
    "event_type_name": "event_type_name",
    "license_category": "license_category_id",
    "license_category_name": "license_category",
    "strength_of_field": "event_strength_of_field",
    "event_avg_lap": "event_average_lap",
    "event_best_lap": "event_best_lap_time",
    "num_drivers": "num_drivers",
    "num_cautions": "num_cautions",
    "num_caution_laps": "num_caution_laps",
    "num_lead_changes": "num_lead_changes",
    "event_laps_completed": "event_laps_complete",
    "winner_name": "winner_name",
    "winner_is_ai": "winner_ai",
    "track_id": "_track_id",
    "track_name": "_track_name",
    "track_config": "_track_config",
    "start_position": "_start_pos",
    "finish_position": "_finish_pos",
    "start_pos_in_class": "_start_pos_class",
    "finish_pos_in_class": "_finish_pos_class",
    "laps_completed": "laps_complete",
    "laps_led": "laps_led",
    "incidents": "incidents",
    "car_class_id": "car_class_id",
    "car_class_name": "car_class_name",
    "car_class_short_name": "car_class_short_name",
    "car_id": "car_id",
    "car_name": "car_name",
    "car_name_abbrev": "car_name_abbreviated",
    "points": "champ_points",
    "is_dropped": "drop_race",
    "season_license_group_id": "season_license_group",
    "season_license_group_name": "season_license_group_name",
    # Tolerated spellings the current export does not use.
    "cust_id": "cust_id", "customer_id": "cust_id",
    "date": "start_time", "official": "official_session",
    "sof": "event_strength_of_field",
    "laps": "laps_complete", "led": "laps_led",
    "car": "car_name", "track": "_track_name", "series": "series_name",
    "session_name": "session_name", "league_id": "league_id",
    "league_name": "league_name", "heat_race": "heat_race",
    "private_session_id": "private_session_id",
    "driver_changes": "driver_changes",
    "winner_group_id": "winner_group_id",
}

# Fields to store as integers. Everything unlisted stays as the text it was.
CSV_INTS = {
    "session_id", "subsession_id", "cust_id", "official_session", "series_id",
    "season_id", "season_year", "season_quarter", "race_week_num",
    "event_type", "license_category_id", "event_strength_of_field",
    "event_average_lap", "event_best_lap_time", "num_drivers", "num_cautions",
    "num_caution_laps", "num_lead_changes", "event_laps_complete",
    "winner_ai", "laps_complete", "laps_led", "incidents", "car_class_id",
    "car_id", "champ_points", "drop_race", "season_license_group",
    "league_id", "heat_race", "private_session_id", "driver_changes",
    "winner_group_id", "_track_id", "_start_pos", "_finish_pos",
    "_start_pos_class", "_finish_pos_class",
}

# The four position columns, and the 0-indexed field each becomes.
CSV_POSITIONS = (("_finish_pos", "finish_position"),
                 ("_start_pos", "starting_position"),
                 ("_finish_pos_class", "finish_position_in_class"),
                 ("_start_pos_class", "starting_position_in_class"))

_LAP = re.compile(r"^(?:(\d+):)?(\d+(?:\.\d+)?)$")


def _int(v):
    if v is None:
        return None
    m = re.match(r"^-?\d+", str(v).strip().replace(",", ""))
    return int(m.group(0)) if m else None


def parse_lap_time(v):
    '''"1:34.567" -> 945670, the ten-thousandths of a second both exports use.

    The current CSV already carries the integer, so this only runs on a value
    that is written as a time. Getting it wrong is not subtle -- the Pace tab
    divides by 10000, so a raw "94.567" stored as-is reads as a 0.009 s lap.
    '''
    if v is None:
        return None
    t = str(v).strip()
    if ":" not in t:
        return _int(t)          # already the integer count
    m = _LAP.match(t)
    if not m:
        return None
    total = (int(m.group(1)) * 60 if m.group(1) else 0) + float(m.group(2))
    return int(round(total * 10000)) if total > 0 else None


_DATE_FORMATS = ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
                 "%Y-%m-%d %H:%M", "%Y-%m-%d", "%m/%d/%Y %H:%M:%S",
                 "%m/%d/%Y %H:%M", "%m/%d/%Y", "%Y/%m/%d %H:%M")


def parse_date(v):
    """Whatever the file used -> the ISO shape the JSON export uses.

    Not cosmetic: the site slices this string (`substr(start_time,1,7)` for the
    monthly chart, `1,10` for a day), so an "08/15/2026" stored raw would group
    races by "08/15/2" and produce nonsense rather than an error. The current
    CSV is already ISO; anything unrecognised is dropped rather than kept.
    """
    if not v:
        return None
    t = str(v).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(t, fmt).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    return None


def read_csv_rows(path, force_index=None):
    """One CSV file -> rows shaped exactly like the JSON export's rows.

    Returns (rows, notes). Everything downstream -- classify, flatten, the
    schema, the views -- is shared with the JSON path.
    """
    notes = []
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        sample = fh.read(8192)
        fh.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        raw = [r for r in csv.DictReader(fh, dialect=dialect)]
    if not raw:
        return [], notes

    headings = {h: CSV_ALIASES.get(_norm(h), _norm(h))
                for h in raw[0].keys() if h}
    if "subsession_id" not in headings.values():
        notes.append("no 'Subsession ID' column; headings were: "
                     + ", ".join(sorted(h for h in raw[0].keys() if h)))
        return [], notes

    # Position indexing. The CSV download is 1-indexed and the JSON export is
    # 0-indexed -- verified on 129 subsessions present in both, every one of
    # which differed by exactly 1. So 1-indexed is the default, and nothing in
    # the data overrides it.
    #
    # It is tempting to infer this instead: "a 0 in the finish column means the
    # file is already 0-indexed". That inference is wrong and dangerous. CSV 0
    # corresponds to JSON -1, the "no position" sentinel -- the same
    # relationship the start-position column shows plainly -- so a single
    # unscored session would flip the whole file and shift every result by one,
    # silently. A default backed by 129 rows should not be overridable by one.
    # Hence: warn, and make the human decide.
    zero_based = force_index == "zero"
    if force_index:
        notes.append(f"positions taken as {force_index}-indexed (forced)")
    else:
        fins = [_int(r[h]) for r in raw for h, k in headings.items()
                if k == "_finish_pos"]
        if any(v == 0 for v in fins if v is not None):
            notes.append("a finish position of 0 appears, which is unexpected "
                         "in the 1-indexed CSV download (0 normally means "
                         "'no position'). Still read as 1-indexed; if your "
                         "results look shifted by one, re-run with "
                         "--csv-positions zero")

    out, bad_dates = [], []
    for r in raw:
        row = {}
        for h, key in headings.items():
            v = r.get(h)
            v = v.strip() if isinstance(v, str) else v
            if v in ("", None):
                continue
            row[key] = _int(v) if key in CSV_INTS else v

        for src, dst in CSV_POSITIONS:
            v = row.pop(src, None)
            if v is not None:
                # Subtracting 1 also maps the CSV's 0 ("did not qualify") onto
                # the -1 the JSON export uses for the same thing.
                row[dst] = v if zero_based else v - 1

        raw_start = row.get("start_time")
        row["start_time"] = parse_date(raw_start)
        if raw_start and not row["start_time"]:
            bad_dates.append(raw_start)
        if row.get("end_time"):
            row["end_time"] = parse_date(row["end_time"])
        if row.get("event_best_lap_time") is not None:
            row["event_best_lap_time"] = parse_lap_time(row["event_best_lap_time"])

        # Rebuilt into the nested object flatten() expects, so the CSV and the
        # JSON go through exactly the same code from here on.
        row["track"] = {"track_id": row.pop("_track_id", None),
                        "track_name": row.pop("_track_name", None),
                        "config_name": row.pop("_track_config", None)}

        if isinstance(row.get("subsession_id"), int):
            out.append(row)

    if bad_dates:
        # Every date-based view slices this string, so a silently nulled column
        # would show as an empty Overview rather than as an error.
        notes.append(f"{len(bad_dates)} row(s) had a start time this script "
                     f"cannot parse (e.g. {bad_dates[0]!r}) and will not appear "
                     f"on any date-based chart")
    return out, notes


def resolve_csv_cust(json_rows, explicit, db_path):
    """Fill in cust_id for CSV rows, which the CSV download does not carry.

    In order: --cust-id, the majority cust_id among any JSON rows loaded in the
    same run, then the `meta` row from an existing database. Without one the
    site has no idea whose career it is showing, so this fails loudly rather
    than writing rows with a NULL owner.
    """
    if explicit:
        return explicit, "--cust-id"
    tally = {}
    for r in json_rows:
        if r.get("cust_id"):
            tally[r["cust_id"]] = tally.get(r["cust_id"], 0) + 1
    if tally:
        return max(tally, key=tally.get), "the JSON export loaded alongside it"
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            row = conn.execute(
                "SELECT value FROM meta WHERE key='cust_id'").fetchone()
            conn.close()
            if row and row[0]:
                return int(row[0]), "the existing database"
        except sqlite3.Error:
            pass
    return None, None


def classify(row):
    """official vs hosted, from the row's own fields rather than the filename.

    A recipient's downloads are named whatever the browser called them, so
    keying off "hosted" in the filename is not safe.  The two exports differ in
    substance: an official row carries the season it belongs to
    (`season_id` / `series_id` / `event_type_name`), a hosted or league row has
    no season and carries a `host` object instead.
    """
    if row.get("season_id") is not None or row.get("series_id") is not None:
        return "official"
    if row.get("host") is not None or row.get("private_session_id") is not None:
        return "hosted"
    # No season and no host object: treat as hosted so it lands in
    # career_results but stays out of the official career view.
    return "hosted"


def _clean(v):
    """Trim stray whitespace off a text value.

    iRacing's own data carries it -- `Charlotte Motor Speedway ` has a trailing
    space in the JSON export and not in the CSV one. Left alone, the same track
    becomes two rows on the Tracks tab as soon as both exports are loaded,
    because every aggregate groups on the name.
    """
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


def flatten(row, source):
    out = {c: _clean(row.get(c)) for c in COLS if c not in DERIVED}
    out["source"] = source
    track = row.get("track") or {}
    if isinstance(track, dict):
        out["track_id"] = track.get("track_id")
        out["track_name"] = _clean(track.get("track_name"))
        out["track_config_name"] = _clean(track.get("config_name"))
    # A hosted row carries a multi-car class list and a host object.  There are
    # too few of them to earn their own tables, so keep the JSON verbatim.
    out["cars_json"] = json.dumps(row["cars"]) if row.get("cars") else None
    out["host_json"] = json.dumps(row["host"]) if row.get("host") else None
    return out


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def coverage_gaps(conn):
    """Months with no sessions at all, between the first and the last one.

    The 90-day cap means most people build a career from several downloads and
    it is easy to miss a window.  A month-sized hole in the middle of an
    otherwise continuous career is nearly always a download you have not done
    yet, so it is worth saying out loud rather than quietly charting.
    """
    months = [r[0] for r in conn.execute(
        "SELECT DISTINCT substr(start_time,1,7) FROM career_results "
        "WHERE start_time IS NOT NULL ORDER BY 1")]
    if len(months) < 2:
        return []
    have = set(months)
    y, m = (int(x) for x in months[0].split("-"))
    ly, lm = (int(x) for x in months[-1].split("-"))
    gaps = []
    while (y, m) < (ly, lm):
        m += 1
        if m == 13:
            y, m = y + 1, 1
        key = f"{y:04d}-{m:02d}"
        if key < months[-1] and key not in have:
            gaps.append(key)
    return gaps


def report(conn, files, rows_read, cust, name):
    q = lambda s: conn.execute(s).fetchone()[0]
    print()
    for path, source, n in files:
        print(f"  {os.path.basename(path)[:46]:46} {source:9} rows={n:5}")
    if not files:
        return

    total = q("SELECT COUNT(*) FROM career_results")
    races = q("SELECT COUNT(*) FROM career_races")
    span = conn.execute(
        "SELECT MIN(substr(start_time,1,10)), MAX(substr(start_time,1,10)) "
        "FROM career_results WHERE start_time IS NOT NULL").fetchone()
    print(f"\n  read           {rows_read} rows from {len(files)} file(s)")
    print(f"  career_results {total} sessions"
          f"{f'  ({span[0]} to {span[1]})' if span[0] else ''}")
    print(f"  career_races   {races} official race starts")
    print(f"  driver         cust {cust}" + (f" ({name})" if name else ""))

    gaps = coverage_gaps(conn)
    if gaps:
        shown = ", ".join(gaps[:8]) + (" ..." if len(gaps) > 8 else "")
        print(f"\n  NOTE  {len(gaps)} month(s) with no sessions inside your "
              f"career span:\n        {shown}")
        print("        If you raced then, that window has not been downloaded "
              "yet -- \n        go back to the Results Archive and export it, "
              "then re-run this.")


# ---------------------------------------------------------------------------

def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(
        description="Load an iRacing Results Archive export into stats.db.",
        epilog="See the module docstring for how to produce the export.")
    ap.add_argument("--exports", nargs="+",
                    default=[os.path.join(here, "data", "exports")],
                    help="folder(s) or file(s) of downloaded JSON "
                         "(default: data/exports)")
    ap.add_argument("--db", default=os.path.join(here, "data", "stats.db"),
                    help="database to create or update (default: data/stats.db)")
    ap.add_argument("--cust-id", type=int, default=None,
                    help="override the customer id; normally detected")
    ap.add_argument("--name", default=None,
                    help="display name to show in the site header (optional)")
    ap.add_argument("--csv-positions", choices=("zero", "one"), default=None,
                    help="force how CSV finishing positions are read. The "
                         "download is 1-indexed and that is the default; use "
                         "this only if your file disagrees")
    a = ap.parse_args()

    paths = find_exports(a.exports)
    if not paths:
        print(f"No .json or .csv found under: {', '.join(a.exports)}")
        print("Download your Results Archive export first -- see the header of "
              "this file for the steps.")
        return 1

    rows, files, skipped, rows_read = {}, [], [], 0
    json_rows, csv_sids = [], set()
    for path in paths:
        is_csv = path.lower().endswith(".csv")
        try:
            if is_csv:
                found, notes = read_csv_rows(path, a.csv_positions)
                for note in notes:
                    print(f"  note    {os.path.basename(path)[:46]:46} -- {note}")
            else:
                with open(path, encoding="utf-8") as fh:
                    found, notes = harvest(json.load(fh)), []
        except Exception as exc:
            skipped.append((path, f"could not be read ({exc})"))
            continue
        if not found:
            skipped.append((path, "no session rows in it"))
            continue
        counts = {}
        for r in found:
            sid = r.get("subsession_id")
            if not isinstance(sid, int):
                continue
            source = classify(r)
            counts[("csv " if is_csv else "") + source] = \
                counts.get(("csv " if is_csv else "") + source, 0) + 1
            rows_read += 1
            if is_csv:
                csv_sids.add(sid)
            else:
                json_rows.append(r)
            # A session present in both a JSON and a CSV export produces the
            # same row from either, so which one wins does not matter -- the
            # positions are normalised to the JSON's 0-indexing on the way in.
            rows[sid] = flatten(r, source)
        for source, n in sorted(counts.items()):
            files.append((path, source, n))

    for path, why in skipped:
        print(f"  skipped {os.path.basename(path)[:46]:46} -- {why}")

    if not rows:
        print("\nNo iRacing session rows found in those files.")
        print("Make sure you used the download control above the Results "
              "Archive table -> \"Download JSON\" or \"Download CSV\", not a "
              "page save.")
        return 1

    # The CSV download carries no cust_id column, so fill it in from whatever
    # does know -- otherwise those rows land with a NULL owner and the site
    # cannot tell whose career it is showing.
    if csv_sids:
        csv_cust, whence = resolve_csv_cust(json_rows, a.cust_id, a.db)
        if csv_cust is None:
            print(f"\n{len(csv_sids)} row(s) came from a CSV, which has no "
                  f"cust_id column, and\nnothing else in this run says whose "
                  f"they are.")
            print("Re-run with --cust-id <your customer id> -- it is on your "
                  "iRacing\nprofile page, and in the URL of your member "
                  "profile.")
            return 1
        for sid in csv_sids:
            rows[sid]["cust_id"] = csv_cust
        print(f"  cust_id {csv_cust} applied to {len(csv_sids)} CSV row(s), "
              f"from {whence}")

    # cust_id: the export is one-row-per-session for the exporting member, so
    # it is uniform in practice.  Take the majority anyway -- that survives
    # someone dropping a friend's export into the same folder, and lets us say
    # so out loud rather than mixing two careers silently.
    tally = {}
    for r in rows.values():
        if r.get("cust_id"):
            tally[r["cust_id"]] = tally.get(r["cust_id"], 0) + 1
    if a.cust_id:
        cust = a.cust_id
    elif tally:
        cust = max(tally, key=tally.get)
    else:
        print("\nNo cust_id in any row -- pass --cust-id to say whose this is.")
        return 1
    if len(tally) > 1:
        others = ", ".join(f"{c} ({n})" for c, n in
                           sorted(tally.items(), key=lambda kv: -kv[1])
                           if c != cust)
        print(f"\n  WARNING  more than one customer id in these files. "
              f"Using {cust}; also saw {others}.")
        print("           Those are someone else's races -- load them into a "
              "separate database.")

    db_dir = os.path.dirname(os.path.abspath(a.db))
    try:
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        conn = sqlite3.connect(a.db)
        conn.execute("PRAGMA user_version")  # forces the file open
    except (sqlite3.OperationalError, OSError) as exc:
        # Nearly always this is the container running unprivileged against a
        # directory the host user owns, which is worth naming rather than
        # letting a bare "unable to open database file" stand.
        print(f"\nCannot write {a.db}: {exc}")
        print(f"Check that {db_dir or '.'} exists and is writable.")
        if os.path.exists("/.dockerenv"):
            print("Running inside a container: add --user \"$(id -u):$(id -g)\" "
                  "to the docker run\ncommand so the file is created as you "
                  "rather than as the image's user.")
        return 1
    conn.executescript(CAPTURE_SCHEMA)
    conn.executescript(CAREER_SCHEMA.format(
        cols=",\n    ".join(
            f"{c} {'INTEGER' if c in INT_COLS else 'TEXT'}" for c in COLS)))

    ordered = ["subsession_id"] + COLS
    conn.executemany(
        f"INSERT OR REPLACE INTO career_results ({','.join(ordered)}) "
        f"VALUES ({','.join('?' * len(ordered))})",
        [[sid] + [r.get(c) for c in COLS] for sid, r in rows.items()])

    # Views are dropped and recreated every run so a schema change in a newer
    # copy of this script lands without anyone having to delete the database.
    conn.executescript(VIEWS)

    name = a.name
    if not name:
        prev = conn.execute(
            "SELECT value FROM meta WHERE key='driver_name'").fetchone()
        name = prev[0] if prev else None
    conn.executemany(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        [("cust_id", str(cust)),
         ("driver_name", name),
         ("loaded_at", datetime.now(timezone.utc)
                       .replace(microsecond=0).isoformat()),
         ("loader_version", LOADER_VERSION)])
    conn.commit()

    report(conn, files, rows_read, cust, name)
    print(f"\n  database       {os.path.abspath(a.db)}")
    print(f"\nNow run:  python3 server.py --db {a.db}"
          f"\n     then: http://localhost:8090/\n")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
