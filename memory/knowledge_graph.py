"""Small local knowledge graph with explicit handling of conflicting facts."""
from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

from core.data_home import resolve_data_path

logger = logging.getLogger(__name__)

MAX_SUBJECT_LENGTH = 120
MAX_PREDICATE_LENGTH = 80
MAX_OBJECT_LENGTH = 380


def _db_path(path: str | Path | None = None) -> Path:
    return Path(path) if path is not None else resolve_data_path(None, "memory.db")


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def _validated(value: str, name: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    value = " ".join(value.split())
    if not value:
        raise ValueError(f"{name} is required")
    if len(value) > max_length:
        raise ValueError(f"{name} must be at most {max_length} characters")
    return value


def _connect(path: Path, create: bool) -> sqlite3.Connection | None:
    if not create and not path.exists():
        return None
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(str(path), timeout=5)


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS knowledge_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL,
            subject_norm TEXT NOT NULL,
            predicate TEXT NOT NULL,
            predicate_norm TEXT NOT NULL,
            object TEXT NOT NULL,
            object_norm TEXT NOT NULL,
            source TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('active', 'contested', 'superseded')),
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(subject_norm, predicate_norm, object_norm)
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_subject_predicate "
        "ON knowledge_facts(subject_norm, predicate_norm, status)"
    )


def _has_schema(conn: sqlite3.Connection) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='knowledge_facts'"
    ).fetchone() is not None


def _rows(conn: sqlite3.Connection, query: str = "", limit: int = 200,
          include_history: bool = True) -> list[dict]:
    clauses = []
    params: list[object] = []
    if not include_history:
        clauses.append("status IN ('active', 'contested')")
    if query.strip():
        clauses.append(
            "(subject LIKE ? ESCAPE '\\' OR predicate LIKE ? ESCAPE '\\' "
            "OR object LIKE ? ESCAPE '\\')"
        )
        escaped = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.extend([f"%{escaped}%"] * 3)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(max(1, min(int(limit), 500)))
    rows = conn.execute(
        "SELECT id, subject, predicate, object, source, status, created_at "
        "FROM knowledge_facts" + where + " ORDER BY id DESC LIMIT ?",
        params,
    ).fetchall()
    return [
        {"id": row[0], "subject": row[1], "predicate": row[2], "object": row[3],
         "source": row[4], "status": row[5], "created_at": row[6]}
        for row in rows
    ]


def list_facts(query: str = "", limit: int = 200, *, include_history: bool = True,
               db_path: str | Path | None = None) -> list[dict]:
    path = _db_path(db_path)
    conn = _connect(path, create=False)
    if conn is None:
        return []
    try:
        if not _has_schema(conn):
            return []
        return _rows(conn, query, limit, include_history)
    finally:
        conn.close()


def add_fact(subject: str, predicate: str, object: str, source: str = "user",
             *, db_path: str | Path | None = None) -> dict:
    subject = _validated(subject, "subject", MAX_SUBJECT_LENGTH)
    predicate = _validated(predicate, "predicate", MAX_PREDICATE_LENGTH)
    object = _validated(object, "object", MAX_OBJECT_LENGTH)
    source = _validated(source, "source", 80)
    path = _db_path(db_path)
    conn = _connect(path, create=True)
    assert conn is not None
    try:
        _ensure_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        subject_norm, predicate_norm, object_norm = (
            _normalize(subject), _normalize(predicate), _normalize(object)
        )
        candidates = conn.execute(
            "SELECT id, object, status FROM knowledge_facts "
            "WHERE subject_norm=? AND predicate_norm=? AND status IN ('active','contested') "
            "ORDER BY id",
            (subject_norm, predicate_norm),
        ).fetchall()
        same = next((row for row in candidates if _normalize(row[1]) == object_norm), None)
        if same:
            conn.commit()
            return {"status": "duplicate" if same[2] == "active" else "contested",
                    "id": same[0], "facts": _rows_for_key(conn, subject_norm, predicate_norm)}

        conflict = bool(candidates)
        status = "contested" if conflict else "active"
        now = time.time()
        historical = conn.execute(
            "SELECT id FROM knowledge_facts WHERE subject_norm=? AND predicate_norm=? "
            "AND object_norm=? AND status='superseded'",
            (subject_norm, predicate_norm, object_norm),
        ).fetchone()
        if conflict:
            conn.execute(
                "UPDATE knowledge_facts SET status='contested', updated_at=? "
                "WHERE subject_norm=? AND predicate_norm=? AND status='active'",
                (now, subject_norm, predicate_norm),
            )
        if historical:
            fact_id = historical[0]
            conn.execute(
                "UPDATE knowledge_facts SET status=?, source=?, updated_at=? WHERE id=?",
                (status, source, now, fact_id),
            )
        else:
            cur = conn.execute(
                "INSERT INTO knowledge_facts "
                "(subject, subject_norm, predicate, predicate_norm, object, object_norm, "
                "source, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (subject, subject_norm, predicate, predicate_norm, object, object_norm,
                 source, status, now, now),
            )
            fact_id = cur.lastrowid
        conn.commit()
        return {"status": "conflict" if conflict else "added",
                "id": fact_id, "facts": _rows_for_key(conn, subject_norm, predicate_norm)}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _rows_for_key(conn: sqlite3.Connection, subject_norm: str,
                  predicate_norm: str) -> list[dict]:
    return [
        {"id": row[0], "subject": row[1], "predicate": row[2], "object": row[3],
         "source": row[4], "status": row[5], "created_at": row[6]}
        for row in conn.execute(
            "SELECT id, subject, predicate, object, source, status, created_at "
            "FROM knowledge_facts WHERE subject_norm=? AND predicate_norm=? "
            "AND status IN ('active','contested') ORDER BY id",
            (subject_norm, predicate_norm),
        ).fetchall()
    ]


def resolve_fact(fact_id: int, *, db_path: str | Path | None = None) -> bool:
    """Make one contested candidate authoritative and retain the others as history."""
    path = _db_path(db_path)
    conn = _connect(path, create=False)
    if conn is None:
        return False
    try:
        if not _has_schema(conn):
            return False
        conn.execute("BEGIN IMMEDIATE")
        selected = conn.execute(
            "SELECT subject_norm, predicate_norm FROM knowledge_facts "
            "WHERE id=? AND status='contested'", (int(fact_id),)
        ).fetchone()
        if selected is None:
            conn.rollback()
            return False
        subject_norm, predicate_norm = selected
        now = time.time()
        count = conn.execute(
            "SELECT COUNT(*) FROM knowledge_facts WHERE subject_norm=? "
            "AND predicate_norm=? AND status='contested'",
            (subject_norm, predicate_norm),
        ).fetchone()[0]
        if count < 2:
            conn.rollback()
            return False
        conn.execute(
            "UPDATE knowledge_facts SET status='superseded', updated_at=? "
            "WHERE subject_norm=? AND predicate_norm=? AND status='contested'",
            (now, subject_norm, predicate_norm),
        )
        conn.execute(
            "UPDATE knowledge_facts SET status='active', updated_at=? WHERE id=?",
            (now, int(fact_id)),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_fact(fact_id: int, *, db_path: str | Path | None = None) -> bool:
    path = _db_path(db_path)
    conn = _connect(path, create=False)
    if conn is None:
        return False
    try:
        if not _has_schema(conn):
            return False
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT subject_norm, predicate_norm, status FROM knowledge_facts WHERE id=?",
            (int(fact_id),),
        ).fetchone()
        if row is None:
            conn.rollback()
            return False
        subject_norm, predicate_norm, status = row
        conn.execute("DELETE FROM knowledge_facts WHERE id=?", (int(fact_id),))
        if status == "contested":
            remaining = conn.execute(
                "SELECT id FROM knowledge_facts WHERE subject_norm=? AND predicate_norm=? "
                "AND status='contested' ORDER BY id",
                (subject_norm, predicate_norm),
            ).fetchall()
            if len(remaining) == 1:
                conn.execute(
                    "UPDATE knowledge_facts SET status='active', updated_at=? WHERE id=?",
                    (time.time(), remaining[0][0]),
                )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def format_for_prompt(*, db_path: str | Path | None = None,
                      max_facts: int = 3, max_conflicts: int = 2) -> str:
    facts = list_facts(include_history=False, db_path=db_path)
    active = [fact for fact in facts if fact["status"] == "active"][:max_facts]
    contested: dict[tuple[str, str], list[dict]] = {}
    for fact in facts:
        if fact["status"] == "contested":
            contested.setdefault(
                (_normalize(fact["subject"]), _normalize(fact["predicate"])), []
            ).append(fact)

    lines = ["[Structured memory — local user-provided or imported facts]"]
    for candidates in list(contested.values())[:max_conflicts]:
        sample = candidates[0]
        values = "; ".join(
            f"{fact['object'][:80]} (g:{fact['id']})" for fact in candidates[:3]
        )
        if len(candidates) > 3:
            values += "; more candidates are listed in /memory"
        lines.append(
            f"- Unresolved conflict for {sample['subject'][:60]} — "
            f"{sample['predicate'][:40]}: {values}. "
            "Do not assume either is current; ask the user if it matters."
        )
    lines.extend(
        f"- {fact['subject'][:80]} — {fact['predicate'][:50]} → {fact['object'][:100]}"
        for fact in active
    )
    result = "\n".join(lines) if active or contested else ""
    return result[:1597] + "…" if len(result) > 1600 else result
