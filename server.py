#!/usr/bin/env python3
"""Browse the iRacing stats database in a browser.

Stdlib only -- no framework, no build step.  Reads data/stats.db, which is
built by load_iracing_data.py from the member site's Results Archive export.

    python3 server.py [--port 8090] [--db data/stats.db] [--cust-id N]

Then open http://localhost:8090/

The customer id is NOT hardcoded: it is read from the `meta` table the loader
writes, and only overridden by --cust-id / IRSTATS_CUST_ID.  That keeps the
image and the source free of any one driver's identity.

The whole dataset is a few thousand rows, so /api/bootstrap ships everything
the UI needs in one response and all filtering happens client-side.  Per-race
grids are fetched lazily because they are the only genuinely large part.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = {"db": os.path.join(HERE, "data", "stats.db"), "cust": None}


def rows(sql, args=()):
    conn = sqlite3.connect(f"file:{CFG['db']}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def one(sql, args=()):
    r = rows(sql, args)
    return r[0] if r else {}


def has_column(table, column):
    return any(r["name"] == column
               for r in rows(f"PRAGMA table_info({table})"))


def has_table(name):
    return bool(one("SELECT COUNT(*) n FROM sqlite_master "
                    "WHERE type IN ('table','view') AND name=?", (name,))["n"])


def resolve_cust(explicit=None):
    """Whose career is this?  Never hardcoded -- see the module docstring.

    Order: --cust-id, then IRSTATS_CUST_ID, then the `meta` row the loader
    writes, then the majority cust_id in career_results (the Results Archive
    export is one-row-per-session for the exporting member, so it is uniform
    in practice; the majority pick just survives a hand-merged database).
    """
    if explicit:
        return int(explicit)
    env = os.environ.get("IRSTATS_CUST_ID")
    if env and env.strip().isdigit():
        return int(env.strip())
    if has_table("meta"):
        r = one("SELECT value FROM meta WHERE key='cust_id'")
        if r.get("value"):
            return int(r["value"])
    if has_table("career_results"):
        r = one("SELECT cust_id FROM career_results WHERE cust_id IS NOT NULL "
                "GROUP BY cust_id ORDER BY COUNT(*) DESC LIMIT 1")
        if r.get("cust_id"):
            return int(r["cust_id"])
    return None


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def bootstrap():
    cust = CFG["cust"]
    # The capture layer (telemetry-derived grids, lap times, rivals) is
    # optional: a database built from the Results Archive export alone has the
    # tables but no rows, and a hand-made one may not have them at all.  Every
    # query that touches it is gated here rather than allowed to 500 the page.
    cap = all(has_table(t) for t in
              ("captures", "drivers", "results", "canonical_captures"))

    # The display name only exists in the capture layer (the Results Archive
    # export carries no name for the exporting member), so it is optional.
    name = None
    if has_table("drivers"):
        name = one("SELECT user_name FROM drivers WHERE cust_id=? "
                   "AND user_name IS NOT NULL LIMIT 1", (cust,)).get("user_name")
    profile = one("""
        SELECT ? AS cust_id,
               (SELECT MIN(start_time) FROM career_races) AS first_race,
               (SELECT MAX(start_time) FROM career_races) AS last_race""",
                  (cust,))
    profile["name"] = name or (one("SELECT value FROM meta WHERE key='driver_name'")
                               .get("value") if has_table("meta") else None)

    summary = one("""
        SELECT COUNT(*) starts, SUM(is_win) wins,
               SUM(class_position<=3) podiums, SUM(class_position<=5) top5,
               ROUND(AVG(position),1) avg_finish, SUM(laps_complete) laps,
               SUM(laps_led) laps_led, SUM(incidents) incidents,
               ROUND(1.0*SUM(incidents)/SUM(laps_complete),3) inc_per_lap,
               ROUND(AVG(event_strength_of_field)) avg_sof,
               COUNT(DISTINCT series_id) series_count
        FROM career_races""")

    summary["sessions_total"] = one(
        "SELECT COUNT(*) n FROM career_results")["n"]
    summary["telemetry_races"] = one(
        "SELECT COUNT(*) n FROM race_coverage WHERE has_telemetry")["n"] if cap else 0
    summary["rivals"] = one(
        "SELECT COUNT(DISTINCT cust_id) n FROM drivers "
        "WHERE cust_id<>? AND car_is_ai=0 AND car_is_pace_car=0",
        (cust,))["n"] if cap else 0
    summary["has_capture_layer"] = 1 if cap and summary["telemetry_races"] else 0

    seasons = rows("""
        SELECT season_year||' S'||season_quarter AS season, COUNT(*) races,
               SUM(is_win) wins, ROUND(AVG(position),1) avg_pos,
               SUM(laps_complete) laps, SUM(incidents) inc,
               ROUND(1.0*SUM(incidents)/SUM(laps_complete),3) ipl
        FROM career_races GROUP BY 1 ORDER BY 1""")

    monthly = rows("""
        SELECT substr(start_time,1,7) month, COUNT(*) races,
               SUM(laps_complete) laps, SUM(incidents) inc,
               ROUND(1.0*SUM(incidents)/SUM(laps_complete),3) ipl
        FROM career_races GROUP BY 1 ORDER BY 1""")

    positions = rows("""
        SELECT position, COUNT(*) n FROM career_races
        WHERE position IS NOT NULL GROUP BY 1 ORDER BY position""")

    # Safety rating at session start, from the capture layer.  This is the
    # value the sim reported when the session began -- there is no post-race
    # delta in the source, so it is a sampled series, not iRacing's own chart.
    sr = rows("""
        SELECT substr(cc.captured_at,1,10) day, cc.category,
               MIN(d.lic_sub_level)/100.0 sr_low,
               MAX(d.lic_sub_level)/100.0 sr_high,
               MAX(d.lic_string) lic
        FROM canonical_captures cc JOIN drivers d ON d.capture_dir=cc.capture_dir
        WHERE d.cust_id=? AND d.lic_sub_level IS NOT NULL
        GROUP BY 1,2 ORDER BY 1""", (cust,)) if cap else []

    # iRating and safety rating after each race. This only exists if the
    # per-race exports have been imported -- neither the Results Archive export
    # nor the telemetry capture carries a post-race value, so before that the
    # best available was the value at session start, which cannot show a gain
    # or a loss.
    progress = rows("""
        SELECT substr(cc.captured_at,1,10) day, cc.captured_at,
               cc.subsession_id, cc.category,
               d.irating AS ir_before, d.irating_new AS ir_after,
               d.lic_sub_level/100.0 AS sr_before,
               d.lic_sub_level_new/100.0 AS sr_after
        FROM canonical_captures cc
        JOIN drivers d ON d.capture_dir=cc.capture_dir
        WHERE d.cust_id=? AND d.irating_new IS NOT NULL
        ORDER BY cc.captured_at""", (cust,)) \
        if cap and has_column("drivers", "irating_new") else []

    races = rows("""
        SELECT r.subsession_id, substr(r.start_time,1,10) day, r.start_time,
               r.series_name, r.track_name, r.track_config_name, r.car_name,
               r.car_class_short_name, r.license_category,
               r.season_year, r.season_quarter, r.race_week_num,
               r.position, r.class_position, r.is_win,
               r.starting_position+1 AS start_pos,
               r.num_drivers, r.laps_complete, r.laps_led, r.incidents,
               r.event_strength_of_field AS sof, r.champ_points,
               r.event_laps_complete,
               CASE WHEN r.laps_complete>0
                    THEN ROUND(1.0*r.incidents/r.laps_complete,3) END AS ipl,
               {capture_dir} AS capture_dir
        FROM career_races r ORDER BY r.start_time DESC""".format(
        capture_dir=("(SELECT c.capture_dir FROM canonical_captures c "
                     "WHERE c.subsession_id=r.subsession_id LIMIT 1)")
        if cap else "NULL"))

    series = rows("""
        SELECT series_name name, license_category cat, COUNT(*) races,
               SUM(is_win) wins, ROUND(AVG(position),1) avg_pos,
               SUM(laps_complete) laps, SUM(incidents) inc,
               ROUND(1.0*SUM(incidents)/SUM(laps_complete),3) ipl,
               ROUND(AVG(event_strength_of_field)) sof,
               MAX(substr(start_time,1,10)) last_raced
        FROM career_races GROUP BY 1,2 ORDER BY races DESC""")

    tracks = rows("""
        SELECT track_name name, COUNT(*) races, SUM(is_win) wins,
               ROUND(AVG(position),1) avg_pos, SUM(laps_complete) laps,
               SUM(incidents) inc,
               ROUND(1.0*SUM(incidents)/SUM(laps_complete),3) ipl
        FROM career_races GROUP BY 1 ORDER BY races DESC""")

    cars = rows("""
        SELECT car_name name, COUNT(*) races, SUM(is_win) wins,
               ROUND(AVG(position),1) avg_pos, SUM(laps_complete) laps,
               SUM(incidents) inc,
               ROUND(1.0*SUM(incidents)/SUM(laps_complete),3) ipl
        FROM career_races GROUP BY 1 ORDER BY races DESC""")

    # Pace car and AI are excluded -- they are grid entries, not people.
    rivals = rows("""
        SELECT d.cust_id, d.user_name name, COUNT(DISTINCT d.capture_dir) sessions,
               MAX(d.irating) best_irating, MAX(d.lic_string) lic,
               MAX(d.club_name) club
        FROM drivers d
        WHERE d.cust_id<>? AND d.car_is_ai=0 AND d.car_is_pace_car=0
        GROUP BY d.cust_id, d.user_name
        ORDER BY sessions DESC, best_irating DESC LIMIT 200""", (cust,)) \
        if cap else []

    # Personal bests by track + config + car.
    #
    # Your own lap times exist ONLY in the capture layer -- the Results Archive
    # export carries `event_best_lap_time`, which is the fastest lap by anyone
    # in the event, not yours (verified: it matches the field best 11/12 on the
    # overlapping races, and your own only on the 2 you were fastest in).
    #
    # The benchmark is the best lap IN YOUR CAR CLASS from the same captured
    # grid.  Comparing against the outright field best is actively misleading in
    # multiclass races -- at Canadian Tire that reads +18.5% purely because a
    # prototype set the fastest lap while you were in a GT4.
    pace = rows("""
        WITH mine AS (
          SELECT cc.capture_dir, cc.track_id tid,
                 CASE WHEN cc.track_config_name IN ('','N/A')
                        OR cc.track_config_name IS NULL
                      THEN '' ELSE cc.track_config_name END cfg,
                 d.car_id cid, cc.track_display_name tname,
                 d.car_screen_name cname, d.car_class_short_name cls,
                 cc.num_car_classes ncls, substr(cc.captured_at,1,10) day,
                 MIN(r.fastest_time) my_best, SUM(r.laps_complete) laps,
                 (SELECT MIN(r2.fastest_time) FROM results r2
                    JOIN drivers d2 ON d2.capture_dir=r2.capture_dir
                                   AND d2.car_idx=r2.car_idx
                   WHERE r2.capture_dir=cc.capture_dir
                     AND r2.fastest_time IS NOT NULL
                     AND d2.car_class_id=d.car_class_id) class_best
          FROM canonical_captures cc
          JOIN drivers d ON d.capture_dir=cc.capture_dir AND d.cust_id=?
          JOIN results r ON r.capture_dir=cc.capture_dir AND r.car_idx=d.car_idx
          WHERE r.fastest_time IS NOT NULL
          GROUP BY cc.capture_dir
        ),
        pb AS (
          SELECT tid, cfg, cid, MIN(tname) tname, MIN(cname) cname,
                 MIN(cls) cls, MAX(ncls) ncls,
                 MIN(my_best) pb, MIN(class_best) class_best,
                 SUM(laps) laps, COUNT(*) tele_sessions, MAX(day) pb_last
          FROM mine GROUP BY tid, cfg, cid
        ),
        ev AS (
          SELECT track_id tid,
                 CASE WHEN track_config_name IN ('','N/A')
                        OR track_config_name IS NULL
                      THEN '' ELSE track_config_name END cfg,
                 car_id cid, MIN(track_name) tname, MIN(car_name) cname,
                 COUNT(*) races, MIN(event_best_lap_time)/10000.0 field_best,
                 MAX(substr(start_time,1,10)) last_raced
          FROM career_races WHERE event_best_lap_time IS NOT NULL
          GROUP BY 1,2,3
        ),
        keys AS (SELECT tid,cfg,cid FROM ev UNION SELECT tid,cfg,cid FROM pb)
        SELECT COALESCE(e.tname, p.tname) AS track, k.cfg AS config,
               COALESCE(e.cname, p.cname) AS car, p.cls AS car_class,
               p.ncls AS num_classes, p.pb, p.class_best, p.laps, p.tele_sessions,
               p.pb_last, e.field_best, e.races, e.last_raced,
               CASE WHEN p.pb IS NOT NULL AND p.class_best > 0
                    THEN ROUND(p.pb - p.class_best, 3) END AS gap,
               CASE WHEN p.pb IS NOT NULL AND p.class_best > 0
                    THEN ROUND((p.pb - p.class_best)/p.class_best*100, 2) END AS gap_pct
        FROM keys k
        LEFT JOIN ev e ON e.tid=k.tid AND e.cfg=k.cfg AND e.cid=k.cid
        LEFT JOIN pb p ON p.tid=k.tid AND p.cfg=k.cfg AND p.cid=k.cid
        """, (cust,)) if cap else []

    return dict(pace=pace, profile=profile, summary=summary, seasons=seasons,
                monthly=monthly, positions=positions, sr=sr, races=races,
                series=series, tracks=tracks, cars=cars, rivals=rivals,
                progress=progress)


def incidents():
    """Incident analytics. Lazily fetched — the tab is opt-in and the row set is
    bigger than the rest of the bootstrap put together."""
    cust = CFG["cust"]
    # Existence is not enough: the loader creates `incidents` empty so the
    # Incidents tab reports the same way every other capture-layer tab does.
    # An empty table would render a dashboard of nulls, and the queries below
    # also need `incident_detail`, which only a telemetry importer creates.
    if not (has_table("incidents") and has_table("incident_detail")):
        return {"available": False}
    if not one("SELECT COUNT(*) n FROM incidents")["n"]:
        return {"available": False}

    summary = one("""
        SELECT COUNT(*) n, SUM(points) pts,
               SUM(lap<=1) first_lap,
               ROUND(AVG(speed)*3.6,1) avg_kmh,
               COUNT(DISTINCT capture_dir) sessions
        FROM incidents""")

    # Group by type only -- 'Multiple in one sample' spans several point
    # values, so grouping by points as well splits it into fake categories.
    by_type = rows("""
        SELECT incident_type type, SUM(points) pts, COUNT(*) n,
               ROUND(AVG(speed)*3.6,1) avg_kmh,
               SUM(surface=0) off_surface
        FROM incidents GROUP BY 1 ORDER BY n DESC""")

    # Where on the lap, in 20 bins of 5%.
    by_pos = rows("""
        SELECT MIN(CAST(lap_dist_pct*20 AS INT), 19) bin, COUNT(*) n,
               SUM(points=1) p1, SUM(points=2) p2, SUM(points=4) p4
        FROM incidents WHERE lap_dist_pct IS NOT NULL
        GROUP BY 1 ORDER BY bin""")

    # Lap 1 is the classic danger lap; bucket the tail.
    by_lap = rows("""
        SELECT CASE WHEN lap<=1 THEN 1 WHEN lap<=9 THEN lap ELSE 10 END lap_bin,
               COUNT(*) n
        FROM incidents WHERE lap IS NOT NULL GROUP BY 1 ORDER BY lap_bin""")

    by_track = rows("""
        SELECT track_name, track_config_name config, track_num_turns turns,
               COUNT(*) n, SUM(points) pts,
               SUM(points=1) off_track, SUM(points=2) spin, SUM(points=4) contact,
               COUNT(DISTINCT capture_dir) sessions,
               ROUND(AVG(speed)*3.6,1) avg_kmh
        FROM incident_detail GROUP BY 1,2,3 ORDER BY n DESC""")

    by_session_type = rows("""
        SELECT COALESCE(session_type,'unknown') session_type, COUNT(*) n,
               SUM(points) pts
        FROM incident_detail GROUP BY 1 ORDER BY n DESC""")

    # Speed buckets say a lot: sub-30 km/h is usually a spin or a pit-lane
    # fumble; 150+ is a genuine high-speed moment.
    by_speed = rows("""
        SELECT CASE WHEN speed*3.6 < 30 THEN 'a <30'
                    WHEN speed*3.6 < 60 THEN 'b 30-60'
                    WHEN speed*3.6 < 100 THEN 'c 60-100'
                    WHEN speed*3.6 < 150 THEN 'd 100-150'
                    ELSE 'e 150+' END bucket,
               COUNT(*) n
        FROM incidents WHERE speed IS NOT NULL GROUP BY 1 ORDER BY 1""")

    recent = rows("""
        SELECT day, track_name, track_config_name config, car_name,
               session_type, lap, ROUND(lap_dist_pct*100,1) pct,
               incident_type type, points, ROUND(speed*3.6,1) kmh,
               surface, subsession_id
        FROM incident_detail ORDER BY day DESC, session_time DESC LIMIT 500""")

    return dict(available=True, summary=summary, by_type=by_type,
                by_pos=by_pos, by_lap=by_lap, by_track=by_track,
                by_session_type=by_session_type, by_speed=by_speed,
                recent=recent)


def race_detail(subsession_id):
    r = one("SELECT * FROM career_races WHERE subsession_id=?", (subsession_id,))
    if not r:
        return None
    cap = one("SELECT * FROM canonical_captures WHERE subsession_id=?",
              (subsession_id,)) if has_table("canonical_captures") else {}
    grid, segments = [], []
    if cap:
        segments = rows("""
            SELECT session_num, session_type, results_official,
                   results_laps_complete, results_num_caution_flags,
                   results_num_lead_changes, fastest_lap_time
            FROM segments WHERE capture_dir=? ORDER BY session_num""",
                        (cap["capture_dir"],))
        # Race segment if present, else the last segment captured.
        seg = next((s for s in segments if s["session_type"] == "Race"),
                   segments[-1] if segments else None)
        if seg:
            grid = rows("""
                SELECT res.position, res.class_position, d.user_name,
                       d.cust_id, d.car_number, d.car_screen_name,
                       d.car_class_short_name, d.irating, d.lic_string,
                       d.club_name, res.laps_complete, res.laps_led,
                       res.fastest_time, d.incident_count AS incidents,
                       res.reason_out_str, d.car_is_ai
                FROM results res
                JOIN drivers d ON d.capture_dir=res.capture_dir
                              AND d.car_idx=res.car_idx
                WHERE res.capture_dir=? AND res.session_num=?
                ORDER BY res.position""",
                        (cap["capture_dir"], seg["session_num"]))
    return dict(race=r, capture=cap, segments=segments, grid=grid)


# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/bootstrap":
                return self._send(200, json.dumps(bootstrap()),
                                  "application/json; charset=utf-8")
            if path == "/api/incidents":
                return self._send(200, json.dumps(incidents()),
                                  "application/json; charset=utf-8")
            if path.startswith("/api/race/"):
                sid = path.rsplit("/", 1)[-1]
                if not sid.isdigit():
                    return self._send(400, '{"error":"bad id"}',
                                      "application/json")
                d = race_detail(int(sid))
                if d is None:
                    return self._send(404, '{"error":"not found"}',
                                      "application/json")
                return self._send(200, json.dumps(d),
                                  "application/json; charset=utf-8")

            rel = "index.html" if path == "/" else path.lstrip("/")
            full = os.path.normpath(os.path.join(HERE, "web", rel))
            if not full.startswith(os.path.join(HERE, "web")):
                return self._send(403, "forbidden", "text/plain")
            if not os.path.isfile(full):
                return self._send(404, "not found", "text/plain")
            ctype = {".html": "text/html; charset=utf-8",
                     ".js": "text/javascript; charset=utf-8",
                     ".css": "text/css; charset=utf-8"}.get(
                         os.path.splitext(full)[1], "application/octet-stream")
            with open(full, "rb") as fh:
                return self._send(200, fh.read(), ctype)
        except Exception as exc:  # keep the server alive; surface in the browser
            return self._send(500, json.dumps({"error": str(exc)}),
                              "application/json")

    def log_message(self, fmt, *a):
        pass  # quiet


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--db", default=CFG["db"])
    ap.add_argument("--cust-id", type=int, default=None,
                    help="override the customer id; normally read from the db")
    a = ap.parse_args()
    CFG["db"] = a.db
    if not os.path.exists(CFG["db"]):
        print(f"no database at {CFG['db']} -- run load_iracing_data.py first")
        return 1
    if not has_table("career_results"):
        print(f"{CFG['db']} has no career_results table -- "
              f"run load_iracing_data.py against your Results Archive export")
        return 1
    CFG["cust"] = resolve_cust(a.cust_id)
    if not CFG["cust"]:
        print("could not determine the customer id -- pass --cust-id or set "
              "IRSTATS_CUST_ID")
        return 1
    n = one("SELECT COUNT(*) n FROM career_races")["n"]
    print(f"iRacingStats  ·  {n} career races  ·  cust {CFG['cust']}  ·  "
          f"http://localhost:{a.port}/")
    ThreadingHTTPServer(("", a.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
