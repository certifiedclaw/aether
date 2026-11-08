"""prune_memory deletes and vacuums."""
import sqlite3
import time


def test_prune_memory_deletes_and_vacuums(tmp_path, monkeypatch):
    from core import memory_tools as mt
    db = tmp_path / "memory.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, role TEXT, content TEXT, timestamp REAL)")
    now = time.time()
    con.executemany("INSERT INTO memories(role,content,timestamp) VALUES (?,?,?)", [
        ("user", "x", now),                                  # too short
        ("user", "a perfectly reasonable memory line", now),
        ("user", "a perfectly reasonable memory line", now),  # duplicate
    ])
    con.commit(); con.close()
    monkeypatch.setattr(mt, "_MEMORY_DB", db)
    out = mt.prune_memory()
    assert "Pruning complete" in out, out
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1
