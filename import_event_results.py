#!/usr/bin/env python3
"""Import per-race iRacing result exports into the capture layer of stats.db.

These are the `eventresult_<subsession_id>_<simsession>.csv` files you get from
the download control on a single session's results modal -- NOT the Results
Archive export, which `load_iracing_data.py` handles.  Run that one first; this
one fills in the depth.

    python3 import_event_results.py [--dir ~/Downloads] [--db data/stats.db]

What it adds that the Results Archive cannot:

    the full grid          every driver in the race, not just your own row
    iRating before/after   the post-race delta, which no other source carries
    safety rating          licence level and sub-level, before and after
    lap times              each driver's fastest lap

--------------------------------------------------------------------------
The file's shape
--------------------------------------------------------------------------
Two blocks, which is what defeats an ordinary CSV reader:

    row 1   9 session-metadata headings
    row 2   the matching values
    row 3   BLANK
    row 4   36 grid headings
    row 5+  one row per entry

A `csv.DictReader` binds to row 1 and stops after row 2, so the entire grid is
invisible to it.  Here the file is read as raw rows and the two blocks are
sliced apart by position.

--------------------------------------------------------------------------
Things in this data that are not what they look like
--------------------------------------------------------------------------
* **A negative `Cust ID` is a team, not a person.**  In a team race the export
  lists the team entry *and* each of its drivers; the team row has a finishing
  position but no iRating.  A 238-row race here is 43 teams plus 195 drivers.
  Team rows are skipped -- their drivers carry the same finishing position, so
  nothing is lost, and counting them as people would corrupt the rivals list.
* **Lap times change format with duration**: `13.532` under a minute,
  `1:47.088` over one.  Both are parsed to seconds, matching `results
  .fastest_time`.
* **In-class positions are not in the file.**  They are computed the way
  iRacing does: rank within the same car class over *distinct teams*, so the
  several drivers sharing one car in an endurance race do not each consume a
  place.
* **There is no discipline/category column.**  Where the Results Archive layer
  already knows the race, category, track ids and official flag are taken from
  it; where it does not, they stay NULL rather than being guessed.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sqlite3
import sys

SIM_RACE = 0  # simsession 0 is the race; earlier segments count backwards

FNAME = re.compile(r"^eventresult[_-](\d+)[_-](-?\d+)\.csv$", re.I)

META_COLS = ["Start Time", "Track", "Series", "Season Year", "Season Quarter",
             "Rookie Season", "Race Week", "Strength of Field",
             "Special Event Type"]

# Columns added to the existing capture-layer tables. `CREATE TABLE IF NOT
# EXISTS` is a no-op against a database that already has these tables, so the
# new fields have to arrive by ALTER TABLE or they silently never appear.
ADD_COLUMNS = {
    "drivers": [("irating_new", "INTEGER"),
                ("lic_level_new", "INTEGER"),
                ("lic_sub_level_new", "INTEGER")],
    "captures": [("capture_source", "TEXT")],
}


def add_missing_columns(conn):
    for table, cols in ADD_COLUMNS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not have:
            continue  # table absent entirely; the loader creates it
        for name, decl in cols:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def _int(v):
    if v in (None, "", "-"):
        return None
    m = re.match(r"^-?\d+", str(v).strip().replace(",", ""))
    return int(m.group(0)) if m else None


def lap_seconds(v):
    """`13.532` or `1:47.088` -> seconds as a float, matching the capture layer.

    `results.fastest_time` holds seconds (71.3661 in the existing data), so
    this must not be confused with the Results Archive export's
    `event_best_lap_time`, which is an integer count of 1/10000 s.  Mixing the
    two is a 10,000x error that still looks like a number.
    """
    if v is None:
        return None
    t = str(v).strip()
    if not t or t.startswith("-"):
        return None
    if ":" in t:
        mins, _, secs = t.partition(":")
        try:
            return int(mins) * 60 + float(secs)
        except ValueError:
            return None
    try:
        f = float(t)
    except ValueError:
        return None
    return f if f > 0 else None


def read_event_file(path):
    """One eventresult CSV -> (metadata dict, list of grid row dicts).

    Returns (None, []) if the file is not this format, so a folder holding a
    mix of exports can be pointed at safely.
    """
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        rows = list(csv.reader(fh))
    if len(rows) < 5:
        return None, []
    head = [h.strip() for h in rows[0]]
    if head[:2] != META_COLS[:2]:
        return None, []
    meta = dict(zip(head, rows[1]))
    # Row 3 is the separator. Row 4 is the grid header; anything after it that
    # still has content is an entry.
    grid_head = [h.strip() for h in rows[3]]
    grid = [dict(zip(grid_head, r)) for r in rows[4:] if r and any(x.strip() for x in r)]
    return meta, grid


def in_class_positions(grid):
    """Finishing position within car class, 0-indexed to match `results`.

    Ranked over *distinct* team ids rather than rows: in an endurance race the
    drivers sharing one car all hold that car's finishing position, and
    counting each of them would inflate everyone behind them.

    `results.class_position` is 0-indexed in the existing capture layer
    (verified: position 1 -> class_position 0), so the count of better teams is
    the value directly, with no +1.
    """
    out = {}
    for i, r in enumerate(grid):
        cls, fin = r.get("Car Class ID"), _int(r.get("Fin Pos"))
        if fin is None:
            out[i] = None
            continue
        better = {r2.get("Team ID") for r2 in grid
                  if r2.get("Car Class ID") == cls
                  and (_int(r2.get("Fin Pos")) or 10 ** 6) < fin}
        out[i] = len(better)
    return out


def career_row(conn, subsession_id):
    """What the Results Archive layer already knows about this race.

    The per-event export has no discipline, no track id and no official flag.
    Rather than guess them, borrow them where the career layer has the race,
    and leave them NULL where it does not.
    """
    try:
        cur = conn.execute(
            "SELECT license_category, official_session, track_id, track_name, "
            "track_config_name, series_id, season_id, event_type_name "
            "FROM career_results WHERE subsession_id=?", (subsession_id,))
    except sqlite3.Error:
        return {}
    r = cur.fetchone()
    if not r:
        return {}
    return dict(zip([c[0] for c in cur.description], r))


def split_track(text):
    """`Daytona International Speedway - Road Course` -> (name, config).

    Only used when the career layer does not already hold the split, because a
    track name can itself contain a dash and this cannot tell the difference.
    """
    name, sep, cfg = (text or "").partition(" - ")
    return (name.strip() or None), (cfg.strip() or None if sep else None)


def import_file(conn, path, cust, stats):
    m = FNAME.match(os.path.basename(path))
    if not m:
        return "not an eventresult filename"
    sid, sim = int(m.group(1)), int(m.group(2))
    meta, grid = read_event_file(path)
    if not grid:
        return "not a per-race export, or no entries in it"

    # Teams are entries, not people. Their drivers carry the same finishing
    # position, so dropping them loses no result and keeps the rivals list to
    # actual humans.
    teams = sum(1 for r in grid if (_int(r.get("Cust ID")) or 0) < 0)
    people = [r for r in grid if (_int(r.get("Cust ID")) or 0) > 0]
    if not people:
        return "no driver rows (only team entries)"

    cls_pos = in_class_positions(grid)
    idx_of = {id(r): i for i, r in enumerate(grid)}

    known = career_row(conn, sid)
    t_name, t_cfg = split_track(meta.get("Track"))
    capture_dir = f"eventresult_{sid}"

    mine = next((r for r in people if _int(r.get("Cust ID")) == cust), None)

    conn.execute("DELETE FROM drivers  WHERE capture_dir=?", (capture_dir,))
    conn.execute("DELETE FROM results  WHERE capture_dir=?", (capture_dir,))
    conn.execute("DELETE FROM segments WHERE capture_dir=?", (capture_dir,))

    conn.execute("""
        INSERT OR REPLACE INTO captures
          (capture_dir, captured_at, subsession_id, series_id, season_id,
           race_week, event_type, category, official, team_racing,
           num_car_classes, track_id, track_name, track_display_name,
           track_config_name, player_car_idx, player_cust_id,
           player_incident_count, capture_source)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        capture_dir, meta.get("Start Time"), sid,
        known.get("series_id"), known.get("season_id"),
        _int(meta.get("Race Week")),
        known.get("event_type_name") or ("Race" if sim == SIM_RACE else None),
        known.get("license_category"), known.get("official_session"),
        1 if teams else 0,
        len({r.get("Car Class ID") for r in grid}),
        known.get("track_id"),
        known.get("track_name") or t_name,
        meta.get("Track") or t_name,
        known.get("track_config_name") or t_cfg,
        idx_of[id(mine)] if mine is not None else None,
        cust, _int(mine.get("Inc")) if mine is not None else None,
        "eventresult"))

    laps = [_int(r.get("Laps Comp")) or 0 for r in people]
    fastest = [lap_seconds(r.get("Fastest Lap Time")) for r in people]
    fastest = [f for f in fastest if f]
    conn.execute("""
        INSERT OR REPLACE INTO segments
          (capture_dir, session_num, session_type, results_official,
           results_laps_complete, fastest_lap_time)
        VALUES (?,?,?,?,?,?)""", (
        capture_dir, sim,
        # race_detail picks the segment whose type is 'Race'; without one the
        # grid never renders and the import looks like it failed.
        "Race" if sim == SIM_RACE else "Other",
        known.get("official_session"),
        max(laps) if laps else None,
        min(fastest) if fastest else None))

    for r in people:
        i = idx_of[id(r)]
        conn.execute("""
            INSERT OR REPLACE INTO drivers
              (capture_dir, car_idx, cust_id, user_name, team_id, car_number,
               car_id, car_screen_name, car_class_id, car_class_short_name,
               irating, irating_new, lic_level, lic_sub_level, lic_level_new,
               lic_sub_level_new, club_id, club_name, division_id,
               incident_count, is_spectator, car_is_ai, car_is_pace_car)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            capture_dir, i, _int(r.get("Cust ID")), (r.get("Name") or "").strip(),
            _int(r.get("Team ID")), (r.get("Car #") or "").strip() or None,
            _int(r.get("Car ID")), (r.get("Car") or "").strip() or None,
            _int(r.get("Car Class ID")),
            (r.get("Car Class") or "").strip() or None,
            _int(r.get("Old iRating")), _int(r.get("New iRating")),
            _int(r.get("Old License Level")), _int(r.get("Old License Sub-Level")),
            _int(r.get("New License Level")), _int(r.get("New License Sub-Level")),
            _int(r.get("Club ID")), (r.get("Club") or "").strip() or None,
            _int(r.get("Div")), _int(r.get("Inc")), 0,
            _int(r.get("AI")) or 0, 0))

        conn.execute("""
            INSERT OR REPLACE INTO results
              (capture_dir, session_num, car_idx, position, class_position,
               laps_complete, laps_led, incidents, fastest_lap, fastest_time,
               reason_out_id, reason_out_str)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (
            capture_dir, sim, i,
            # Fin Pos is 1-indexed and so is results.position -- no shift here,
            # unlike the Results Archive export which is 0-indexed.
            _int(r.get("Fin Pos")), cls_pos.get(i),
            _int(r.get("Laps Comp")), _int(r.get("Laps Led")),
            _int(r.get("Inc")), _int(r.get("Fast Lap#")),
            lap_seconds(r.get("Fastest Lap Time")),
            _int(r.get("Out ID")), (r.get("Out") or "").strip() or None))

    stats["races"] += 1
    stats["drivers"] += len(people)
    stats["teams"] += teams
    stats["with_delta"] += sum(1 for r in people if _int(r.get("New iRating")))
    if mine is None:
        stats["not_mine"].append(sid)
    if not known:
        stats["no_career_row"].append(sid)
    return None


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", nargs="+",
                    default=[os.path.join(here, "data", "eventresults"),
                             os.path.expanduser("~/Downloads")],
                    help="folder(s) holding eventresult_*.csv files "
                         "(default: data/eventresults, then ~/Downloads)")
    ap.add_argument("--db", default=os.path.join(here, "data", "stats.db"))
    ap.add_argument("--cust-id", type=int, default=None,
                    help="override the customer id; normally read from the db")
    a = ap.parse_args()

    if not os.path.exists(a.db):
        print(f"no database at {a.db} -- run load_iracing_data.py first")
        return 1
    conn = sqlite3.connect(a.db)

    cust = a.cust_id
    if not cust:
        try:
            r = conn.execute("SELECT value FROM meta WHERE key='cust_id'").fetchone()
            cust = int(r[0]) if r and r[0] else None
        except sqlite3.Error:
            cust = None
    if not cust:
        try:
            r = conn.execute(
                "SELECT cust_id FROM career_results WHERE cust_id IS NOT NULL "
                "GROUP BY cust_id ORDER BY COUNT(*) DESC LIMIT 1").fetchone()
            cust = r[0] if r else None
        except sqlite3.Error:
            cust = None
    if not cust:
        print("could not tell whose database this is -- pass --cust-id")
        return 1

    paths = []
    for d in a.dir:
        d = os.path.expanduser(d)
        if os.path.isfile(d):
            paths.append(d)
        elif os.path.isdir(d):
            for root, _dirs, files in os.walk(d):
                paths += [os.path.join(root, f) for f in files
                          if f.lower().startswith("eventresult")
                          and f.lower().endswith(".csv")]
    paths = sorted(set(paths))
    if not paths:
        print(f"no eventresult_*.csv found under: {', '.join(a.dir)}")
        return 1

    add_missing_columns(conn)

    stats = {"races": 0, "drivers": 0, "teams": 0, "with_delta": 0,
             "not_mine": [], "no_career_row": []}
    skipped = []
    for p in paths:
        try:
            why = import_file(conn, p, cust, stats)
        except Exception as exc:  # one bad file must not lose the whole run
            why = f"{type(exc).__name__}: {exc}"
        if why:
            skipped.append((os.path.basename(p), why))
    conn.commit()

    for name, why in skipped[:15]:
        print(f"  skipped {name[:44]:44} -- {why}")
    if len(skipped) > 15:
        print(f"  ... and {len(skipped) - 15} more skipped")

    print(f"\n  imported       {stats['races']} race(s) from {len(paths)} file(s)")
    print(f"  grid rows      {stats['drivers']} drivers "
          f"({stats['teams']} team entries skipped)")
    print(f"  iRating deltas {stats['with_delta']} driver rows carry one")
    if stats["not_mine"]:
        print(f"  NOTE  {len(stats['not_mine'])} race(s) have no row for cust "
              f"{cust}; imported anyway, but they are not your results")
    if stats["no_career_row"]:
        print(f"  NOTE  {len(stats['no_career_row'])} race(s) are not in the "
              f"career layer, so category/track ids are blank for them.\n"
              f"        Load the Results Archive export too and re-run.")

    q = lambda s, args=(): conn.execute(s, args).fetchone()[0]
    n_ev = q("SELECT COUNT(*) FROM captures WHERE capture_source=?",
             ("eventresult",))
    print(f"\n  captures       {q('SELECT COUNT(*) FROM captures')} "
          f"({n_ev} from per-race exports)")
    print(f"  drivers        {q('SELECT COUNT(*) FROM drivers')}")
    print(f"  distinct people {q('SELECT COUNT(DISTINCT cust_id) FROM drivers WHERE cust_id>0')}")
    print(f"\n  database       {os.path.abspath(a.db)}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
