#!/usr/bin/env python3
"""Render the whole site to static files, so hosting it needs no application.

    python3 build_static.py [--out site] [--db data/stats.db]

Then serve `site/` with anything -- nginx, Caddy, Cloudflare Pages, an S3
bucket. There is no process to exploit, because there is no process: every API
response is written once, as a file.

Why this rather than a better web server
----------------------------------------
Nothing here is dynamic. The database only changes when you re-run an importer,
and every endpoint is a pure function of it -- there are no writes, no sessions,
no user input beyond a race id. An application server would spend its life
recomputing the same answers and presenting a request parser to the internet in
order to do it.

Writing the answers out instead removes the whole class of question this skill
usually has to argue about: no request parsing, no path handling, no SQL, no
error surface, nothing running as anything. It also caches at the edge for free
and costs about 7 MB, of which the part every visitor loads gzips to ~50 KB.

The front end is not modified. Files are written at exactly the paths it already
fetches (`api/bootstrap`, `api/race/<id>`, extensionless), and `response.json()`
parses them regardless of what content type the host serves them with.

What this does NOT solve
------------------------
Publishing this puts every driver on your grids -- names, customer ids, iRatings,
clubs -- on whatever host you point at it. That is a question about what you are
comfortable sharing, not one a static export answers. Consider `--anonymise` if
the answer is "not that", and read what it does before trusting it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def anonymise(obj, cust, salt):
    """Replace other people's identities with stable pseudonyms.

    Names become "Driver 4f2a" and customer ids become a number derived from a
    salted hash, so the same person stays the same person across the site and
    remains linkable within it -- while not being who they are. Your own rows
    are left alone: it is your site.

    This is deliberately not reversible without the salt, and the salt is not
    written anywhere. It is also not a promise: finishing positions, lap times
    and club names still describe real sessions that exist publicly on
    iRacing's own site, and a determined reader could match them back. Treat it
    as removing the obvious, not as making anyone unfindable.
    """
    def pseudo(cid):
        h = hashlib.sha256(f"{salt}:{cid}".encode()).hexdigest()
        return f"Driver {h[:4]}", int(h[4:12], 16) % 9_000_000 + 1_000_000

    def walk(o):
        if isinstance(o, list):
            return [walk(x) for x in o]
        if isinstance(o, dict):
            out = dict(o)
            cid = out.get("cust_id")
            if isinstance(cid, int) and cid > 0 and cid != cust:
                name, fake = pseudo(cid)
                if "user_name" in out:
                    out["user_name"] = name
                if "name" in out and isinstance(out["name"], str):
                    out["name"] = name
                out["cust_id"] = fake
            return {k: walk(v) for k, v in out.items()}
        return o
    return walk(obj)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=os.path.join(HERE, "site"))
    ap.add_argument("--db", default=os.path.join(HERE, "data", "stats.db"))
    ap.add_argument("--cust-id", type=int, default=None)
    ap.add_argument("--anonymise", action="store_true",
                    help="replace other drivers' names and ids with stable "
                         "pseudonyms; read the module docstring first")
    a = ap.parse_args()

    sys.path.insert(0, HERE)
    import server  # the same code the live site runs, called directly

    server.CFG["db"] = a.db
    if not os.path.exists(a.db):
        print(f"no database at {a.db}")
        return 1
    server.CFG["cust"] = server.resolve_cust(a.cust_id)
    if not server.CFG["cust"]:
        print("could not determine the customer id -- pass --cust-id")
        return 1
    cust = server.CFG["cust"]
    salt = os.urandom(16).hex()

    out = os.path.abspath(a.out)
    api = os.path.join(out, "api")
    os.makedirs(os.path.join(api, "race"), exist_ok=True)

    for f in ("index.html", "app.js", "charts.js", "styles.css"):
        shutil.copy(os.path.join(HERE, "web", f), os.path.join(out, f))

    def write(rel, obj):
        if a.anonymise:
            obj = anonymise(obj, cust, salt)
        p = os.path.join(out, rel)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, separators=(",", ":"))
        return os.path.getsize(p)

    total = 0
    boot = server.bootstrap()
    total += write("api/bootstrap", boot)
    total += write("api/teams", server.teams())
    total += write("api/insights", server.insights())
    total += write("api/incidents", server.incidents())
    print(f"  bootstrap, teams, insights, incidents written")

    races = [r["subsession_id"] for r in boot["races"]]
    for n, sid in enumerate(races, 1):
        d = server.race_detail(sid)
        if d is not None:
            total += write(f"api/race/{sid}", d)
        if n % 100 == 0:
            print(f"  {n}/{len(races)} races")

    # A static host has no 404 handler of its own worth relying on, and the
    # front end deep-links by race id; anything unknown should land on the site
    # rather than on the host's own error page.
    shutil.copy(os.path.join(out, "index.html"), os.path.join(out, "404.html"))

    print(f"\n  {len(races)} races · {total/1024/1024:.1f} MB in {out}")
    if a.anonymise:
        print("  other drivers pseudonymised (your own rows untouched)")
    print("\nServe that directory with any static host. Nothing here executes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
