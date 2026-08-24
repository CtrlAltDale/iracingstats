#!/usr/bin/env python3
"""Which races are still missing a per-race export?

Compares the races in stats.db against the `eventresult_*.csv` files on disk and
prints what is absent.  The expected set comes from the database, so this needs
no list to be kept in step by hand.

    python3 check_event_exports.py [--dir ~/Downloads] [--db data/stats.db]
    python3 check_event_exports.py --ids        # just the ids, one per line

Exporting these one race at a time is slow and easy to get wrong, and the
failure worth catching is not a file that errored -- it is a run that stopped
partway and reported success anyway.  So this reports two things:

  * which ids are missing, ready to feed back into another pass, and
  * whether the missing ids form one unbroken run at the end, which is the
    signature of a run that died rather than one that dropped races at random.

The distinction matters.  Scattered gaps mean individual exports failed and are
worth retrying.  A contiguous tail means everything after some point never
happened, whatever the exporting side believed.
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys

FNAME = re.compile(r"^eventresult[_-](\d+)[_-](-?\d+)\.csv$", re.I)


def on_disk(dirs):
    found = set()
    for d in dirs:
        d = os.path.expanduser(d)
        if not os.path.isdir(d):
            continue
        for root, _dirs, files in os.walk(d):
            for f in files:
                m = FNAME.match(f)
                if m:
                    found.add(int(m.group(1)))
    return found


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", nargs="+", default=[os.path.expanduser("~/Downloads")])
    ap.add_argument("--db", default=os.path.join(here, "data", "stats.db"))
    ap.add_argument("--ids", action="store_true",
                    help="print only the missing ids, for piping into a list")
    ap.add_argument("--all-sessions", action="store_true",
                    help="expect an export for every session, not just races")
    a = ap.parse_args()

    if not os.path.exists(a.db):
        print(f"no database at {a.db}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    sql = ("SELECT subsession_id FROM career_results WHERE source='official' "
           "ORDER BY start_time") if a.all_sessions else \
          "SELECT subsession_id FROM career_races ORDER BY start_time"
    expected = [r[0] for r in conn.execute(sql)]
    conn.close()
    if not expected:
        print("no races in the database yet -- run load_iracing_data.py first",
              file=sys.stderr)
        return 1

    have = on_disk(a.dir)
    missing = [i for i in expected if i not in have]

    if a.ids:
        print("\n".join(str(i) for i in missing))
        return 0

    print(f"  races in database  {len(expected)}")
    print(f"  exports on disk    {len(expected) - len(missing)}")
    print(f"  missing            {len(missing)}")
    extra = len(have) - len([i for i in expected if i in have])
    if extra:
        print(f"  ({extra} export(s) on disk are not races in this database)")

    if not missing:
        print("\n  complete -- every race has an export.")
        return 0

    pos = {sid: n for n, sid in enumerate(expected)}
    idx = sorted(pos[i] for i in missing)
    contiguous_tail = idx == list(range(idx[0], len(expected)))

    print()
    if contiguous_tail:
        print(f"  Every race from position {idx[0] + 1} onward is missing, with")
        print(f"  nothing after it, starting at {expected[idx[0]]}.")
        print()
        print(f"  If the export run has finished, that shape means it STOPPED")
        print(f"  there rather than dropping races here and there -- so treat")
        print(f"  any success count it reported as unreliable, and check what")
        print(f"  changed at that race.")
        print(f"  If it is still running, this is simply how far it has got.")
    else:
        runs, start = [], idx[0]
        for a_, b in zip(idx, idx[1:] + [None]):
            if b != a_ + 1:
                runs.append((start, a_))
                start = b
        print(f"  {len(runs)} gap(s). The last one may just be how far a")
        print(f"  still-running export has reached; earlier ones are races that")
        print(f"  were passed over and are worth retrying:")
        for lo, hi in runs[:10]:
            n = hi - lo + 1
            print(f"    positions {lo + 1}-{hi + 1} ({n} race{'' if n == 1 else 's'})")
        if len(runs) > 10:
            print(f"    ... and {len(runs) - 10} more")

    print(f"\n  re-run with --ids to get the list to feed back in.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
