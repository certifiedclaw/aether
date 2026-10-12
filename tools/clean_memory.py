"""Find and remove junk rows from Aether's exchange memory (data/memory.db).

Dry run by default: lists suspect rows and changes nothing.
    python tools/clean_memory.py                 # list suspects
    python tools/clean_memory.py --all           # list every row (to pick ids)
    python tools/clean_memory.py --apply         # back up, then delete suspects
    python tools/clean_memory.py --apply --ids 22,24   # delete exactly these ids
Close Aether first. A timestamped .bak copy is written before any delete.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "memory.db"

SUSPECT = [
    (re.compile(r"selftest_(input|response)"), "test fixture"),
    (re.compile(r"<tool_call>|\bsave_memory\(|\"name\"\s*:\s*\"\w+\"\s*,\s*\"arguments\""), "raw tool-call text"),
    (re.compile(r"I wasn't able to finish that after a few tool attempts"), "loop-exhausted reply"),
    (re.compile(r"This will restart the computer"), "stale confirmation prompt"),
    (re.compile(r"\bFatih\b"), "invented name"),
    (re.compile(r"The current date and time is"), "off-topic date reply"),
]


def suspects(rows):
    out = []
    for rid, content in rows:
        for pat, why in SUSPECT:
            if pat.search(content or ""):
                out.append((rid, why, content))
                break
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=DB)
    ap.add_argument("--apply", action="store_true", help="delete (after making a backup)")
    ap.add_argument("--all", action="store_true", help="list every row")
    ap.add_argument("--ids", help="comma-separated row ids to delete instead of the heuristics")
    a = ap.parse_args(argv)

    if not a.db.exists():
        print(f"no database at {a.db}")
        return 1
    con = sqlite3.connect(a.db)
    rows = con.execute("SELECT id, content FROM memories ORDER BY id").fetchall()
    if a.all:
        for rid, c in rows:
            print(f"{rid:>5}  {(c or '').replace(chr(10), ' ')[:110]}")
        print(f"{len(rows)} rows")
        return 0

    if a.ids:
        want = {int(x) for x in a.ids.split(",") if x.strip()}
        targets = [(rid, "chosen by id", c) for rid, c in rows if rid in want]
    else:
        targets = suspects(rows)
    if not targets:
        print("nothing to clean")
        return 0
    for rid, why, c in targets:
        print(f"{rid:>5}  [{why}]  {(c or '').replace(chr(10), ' ')[:90]}")
    print(f"{len(targets)} of {len(rows)} rows")
    if not a.apply:
        print("dry run - re-run with --apply to delete (a backup is made first)")
        return 0
    bak = a.db.with_suffix(f".db.{time.strftime('%Y%m%d_%H%M%S')}.bak")
    shutil.copy2(a.db, bak)
    con.executemany("DELETE FROM memories WHERE id = ?", [(rid,) for rid, _, _ in targets])
    con.commit()
    con.execute("VACUUM")
    print(f"deleted {len(targets)} rows; backup: {bak}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
