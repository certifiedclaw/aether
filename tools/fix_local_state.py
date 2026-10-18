"""Clean up local state damaged by the old prompt.txt few-shot examples.

Run from the aether folder:  python tools/fix_local_state.py        (dry run)
                             python tools/fix_local_state.py --apply

Removes the hallucinated identity.name / identity.city (Fatih / Istanbul) from
memory/long_term.json (backing it up first) and reports -- but does not edit --
any chat history rows in data/memory.db that mention them.
"""
import argparse
import json
import shutil
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BAD = ("fatih", "istanbul")


def _walk(d, path=()):
    if isinstance(d, dict):
        for k, v in d.items():
            yield from _walk(v, path + (k,))
    else:
        yield path, d


def purge_db(db: Path, apply: bool) -> int:
    """Delete memories rows that mention the bad identity (backs up first). Returns rows matched."""
    con = sqlite3.connect(db)
    try:
        has = con.execute("select 1 from sqlite_master where type='table' and name='memories'").fetchone()
        if not has:
            return 0
        import sys
        sys.path.insert(0, str(ROOT))
        from core.memory_hygiene import stored_row_is_poisoned
        rows = con.execute("select id, content from memories").fetchall()
        bad_ids = [i for i, c in rows
                   if any(b in (c or "").lower() for b in BAD) or stored_row_is_poisoned(c)]
        if bad_ids and apply:
            con.close()
            shutil.copy2(db, db.with_suffix(".db.bak"))
            con = sqlite3.connect(db)
            con.executemany("delete from memories where id = ?", [(i,) for i in bad_ids])
            con.commit()
        return len(bad_ids)
    finally:
        con.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--purge-db", action="store_true",
                    help="with --apply: also delete memories rows that mention Fatih/Istanbul or record failed turns (filler/tool-call text); backup: memory.db.bak")
    args = ap.parse_args()

    import sys
    sys.path.insert(0, str(ROOT))
    from core.data_home import data_dir, long_term_path

    lt = long_term_path()
    if lt.exists():
        data = json.loads(lt.read_text(encoding="utf-8"))
        hits = [(p, v) for p, v in _walk(data) if isinstance(v, str) and any(b in v.lower() for b in BAD)]
        print(f"long_term.json: {len(hits)} suspicious value(s)")
        for p, v in hits:
            print("  ", ".".join(p), "=", repr(v))
        if hits and args.apply:
            shutil.copy2(lt, lt.with_suffix(".json.bak"))
            for p, _ in hits:
                node = data
                for k in p[:-1]:
                    node = node[k]
                node.pop(p[-1], None)
            lt.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            print("  removed (backup: long_term.json.bak)")
    else:
        print("long_term.json not found")

    db = data_dir() / "memory.db"
    if db.exists():
        con = sqlite3.connect(db)
        try:
            tables = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
            for t in tables:
                cols = [r[1] for r in con.execute(f'pragma table_info("{t}")') if "CHAR" in (r[2] or "").upper() or "TEXT" in (r[2] or "").upper()]
                for c in cols:
                    n = con.execute(
                        f'select count(*) from "{t}" where lower("{c}") like ? or lower("{c}") like ?',
                        tuple(f"%{b}%" for b in BAD)).fetchone()[0]
                    if n:
                        print(f"memory.db: {n} row(s) in {t}.{c} mention Fatih/Istanbul (not modified)")
        finally:
            con.close()
    if db.exists() and args.apply and args.purge_db:
        print(f"memory.db: deleted {purge_db(db, True)} row(s) (backup: memory.db.bak)")
    print("Also: ollama pull qwen2.5:7b  (or set the model back to qwen3:8b); Gemini needs an API key.")


if __name__ == "__main__":
    main()
