"""
memory/memory_tools.py — Aether Memory Backup, Export, Import & Pruning

Tools exposed to the LLM:
  backup_memory    — snapshot current memory + SQLite DB to dated zip
  export_memory    — export SQLite memories to readable JSON
  import_memory    — import from a previous export JSON
  prune_memory     — remove duplicates + low-value entries from DB
  memory_stats     — detailed stats across all memory systems

Also exposed as chat commands: /backup, /export, /prune, /memstats
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from core.data_home import data_path, long_term_path, resolve_data_path

_BACKUP_DIR = data_path("backups")
_LONG_TERM_JSON = long_term_path()
_MEMORY_DB = resolve_data_path(None, "memory.db")
_GOALS_JSON = data_path("goals.json")
_RAG_INDEX = data_path("rag_index")


# ── Backup ────────────────────────────────────────────────────────────────

def backup_memory(tag: str = "") -> str:
    """
    Create a timestamped zip backup of:
      - long_term.json          (identity/preferences/notes)
      - memory.db               (episodic/hybrid memory SQLite)
      - goals.json              (active goals)
      - rag_index/              (RAG document index)

    The backup is written under the per-user data directory.
    """
    try:
        _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        label = f"_{tag}" if tag else ""
        zip_name = _BACKUP_DIR / f"memory_backup_{stamp}{label}"

        # Stage files
        stage = Path(f"/tmp/aether_backup_{stamp}")
        stage.mkdir(parents=True, exist_ok=True)

        copied = []
        for src in [_LONG_TERM_JSON, _MEMORY_DB, _GOALS_JSON, _RAG_INDEX]:
            src_exp = Path(os.path.expanduser(str(src)))
            if src_exp.exists():
                dest = stage / src_exp.name
                if src_exp.is_dir():
                    shutil.copytree(src_exp, dest)
                else:
                    shutil.copy2(src_exp, dest)
                copied.append(src_exp.name)

        shutil.make_archive(str(zip_name), "zip", stage)
        shutil.rmtree(stage, ignore_errors=True)

        zip_path = str(zip_name) + ".zip"
        size_kb  = Path(zip_path).stat().st_size // 1024
        return (f"✅ Backup created: {zip_path}\n"
                f"   Included: {', '.join(copied)}\n"
                f"   Size: {size_kb} KB")
    except Exception as e:
        return f"Backup failed: {e}"


# ── Export ────────────────────────────────────────────────────────────────

def export_memory(output_path: str = "") -> str:
    """
    Export all SQLite memories (episodic/hybrid) + long_term.json
    to a single readable JSON file.
    """
    try:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = Path(output_path) if output_path else \
              data_path(f"memory_export_{stamp}.json")
        out.parent.mkdir(parents=True, exist_ok=True)

        export: dict[str, Any] = {
            "exported_at": datetime.now().isoformat(),
            "version": "aether_v1",
            "long_term": {},
            "episodic": [],
        }

        # Long-term JSON
        if _LONG_TERM_JSON.exists():
            try:
                export["long_term"] = json.loads(
                    _LONG_TERM_JSON.read_text(encoding="utf-8"))
            except Exception:
                pass

        # SQLite episodic memories
        if _MEMORY_DB.exists():
            conn = sqlite3.connect(str(_MEMORY_DB))
            try:
                rows = conn.execute(
                    "SELECT id, role, content, timestamp FROM memories "
                    "ORDER BY timestamp ASC"
                ).fetchall()
                export["episodic"] = [
                    {"id": r[0], "role": r[1], "content": r[2],
                     "timestamp": r[3],
                     "date": datetime.fromtimestamp(r[3]).isoformat()}
                    for r in rows
                ]
            finally:
                conn.close()

        out.write_text(json.dumps(export, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        return (f"✅ Exported {len(export['episodic'])} memories to: {out}\n"
                f"   Long-term keys: "
                f"{sum(len(v) for v in export['long_term'].values() if isinstance(v, dict))}")
    except Exception as e:
        return f"Export failed: {e}"


# ── Import ────────────────────────────────────────────────────────────────

def import_memory(source_path: str) -> str:
    """
    Import memories from a previously exported JSON file.
    Merges into existing memory — does NOT overwrite.
    """
    try:
        src = Path(os.path.expanduser(source_path))
        if not src.exists():
            return f"File not found: {source_path}"

        data = json.loads(src.read_text(encoding="utf-8"))
        imported_episodic = 0
        imported_lt = 0

        # Merge long-term
        lt = data.get("long_term", {})
        if lt and _LONG_TERM_JSON.exists():
            try:
                from memory.memory_manager import load_memory, update_memory
                existing = load_memory()
                for category, entries in lt.items():
                    if isinstance(entries, dict):
                        for k, v in entries.items():
                            if category not in existing:
                                existing[category] = {}
                            if k not in existing[category]:
                                existing[category][k] = v
                                imported_lt += 1
                update_memory(existing)
            except Exception as e:
                logger.warning("import: long_term merge failed: %s", e)

        # Merge episodic (skip duplicates by content hash)
        episodic = data.get("episodic", [])
        if episodic and _MEMORY_DB.exists():
            conn = sqlite3.connect(str(_MEMORY_DB))
            try:
                existing_content = set(
                    r[0] for r in conn.execute(
                        "SELECT content FROM memories").fetchall()
                )
                for row in episodic:
                    content = row.get("content", "")
                    if content and content not in existing_content:
                        conn.execute(
                            "INSERT INTO memories(role, content, timestamp) "
                            "VALUES (?, ?, ?)",
                            (row.get("role", "exchange"), content,
                             row.get("timestamp", time.time()))
                        )
                        existing_content.add(content)
                        imported_episodic += 1
                conn.commit()
            finally:
                conn.close()

        return (f"✅ Import complete.\n"
                f"   Episodic memories added: {imported_episodic}\n"
                f"   Long-term entries added: {imported_lt}")
    except Exception as e:
        return f"Import failed: {e}"


# ── Prune ─────────────────────────────────────────────────────────────────

def prune_memory(max_age_days: int = 90, min_length: int = 20,
                 dry_run: bool = False) -> str:
    """
    Remove low-value entries from the episodic memory DB:
      - Content shorter than min_length characters
      - Entries older than max_age_days
      - Exact duplicate content

    dry_run=True shows what would be deleted without deleting.
    """
    try:
        if not _MEMORY_DB.exists():
            return "No memory database found."

        conn = sqlite3.connect(str(_MEMORY_DB))
        cutoff = time.time() - (max_age_days * 86400)

        # Count before
        total_before = conn.execute(
            "SELECT COUNT(*) FROM memories").fetchone()[0]

        # Find candidates
        too_short = conn.execute(
            "SELECT id FROM memories WHERE length(content) < ?",
            (min_length,)).fetchall()

        too_old = conn.execute(
            "SELECT id FROM memories WHERE timestamp < ?",
            (cutoff,)).fetchall()

        # Find duplicates (keep newest)
        dupes = conn.execute("""
            SELECT id FROM memories
            WHERE id NOT IN (
                SELECT MAX(id) FROM memories GROUP BY content
            )
        """).fetchall()

        to_delete = set(r[0] for r in too_short + too_old + dupes)

        if dry_run:
            conn.close()
            return (f"Dry run — would delete {len(to_delete)} / {total_before} entries:\n"
                    f"  Too short (<{min_length} chars): {len(too_short)}\n"
                    f"  Too old (>{max_age_days} days):  {len(too_old)}\n"
                    f"  Duplicates:                      {len(dupes)}")

        if to_delete:
            conn.execute(
                f"DELETE FROM memories WHERE id IN "
                f"({','.join('?' * len(to_delete))})",
                list(to_delete))
            conn.execute("VACUUM")
            conn.commit()

        total_after = conn.execute(
            "SELECT COUNT(*) FROM memories").fetchone()[0]
        conn.close()

        removed = total_before - total_after
        return (f"✅ Pruning complete.\n"
                f"   Removed: {removed} entries\n"
                f"   Remaining: {total_after} entries\n"
                f"   Criteria: <{min_length} chars, >{max_age_days} days old, duplicates")
    except Exception as e:
        return f"Prune failed: {e}"


# ── Stats ─────────────────────────────────────────────────────────────────

def memory_stats() -> str:
    """Comprehensive stats across all memory systems."""
    lines = ["## Memory Statistics", ""]

    # Long-term JSON
    try:
        lt = json.loads(_LONG_TERM_JSON.read_text(encoding="utf-8")) \
             if _LONG_TERM_JSON.exists() else {}
        total_lt = sum(len(v) for v in lt.values() if isinstance(v, dict))
        lines.append(f"**Long-term (JSON):** {total_lt} entries across "
                     f"{len(lt)} categories")
        for cat, entries in lt.items():
            if isinstance(entries, dict) and entries:
                lines.append(f"  - {cat}: {len(entries)} entries")
    except Exception:
        lines.append("Long-term: could not read")

    # SQLite episodic
    try:
        if _MEMORY_DB.exists():
            conn = sqlite3.connect(str(_MEMORY_DB))
            total = conn.execute(
                "SELECT COUNT(*) FROM memories").fetchone()[0]
            oldest = conn.execute(
                "SELECT MIN(timestamp) FROM memories").fetchone()[0]
            newest = conn.execute(
                "SELECT MAX(timestamp) FROM memories").fetchone()[0]
            size_kb = _MEMORY_DB.stat().st_size // 1024
            conn.close()
            oldest_str = datetime.fromtimestamp(oldest).strftime(
                "%Y-%m-%d") if oldest else "—"
            newest_str = datetime.fromtimestamp(newest).strftime(
                "%Y-%m-%d") if newest else "—"
            lines.append(f"\n**Episodic (SQLite):** {total} memories "
                         f"({size_kb} KB)")
            lines.append(f"  - Date range: {oldest_str} → {newest_str}")
        else:
            lines.append("\nEpisodic: database not yet created")
    except Exception as e:
        lines.append(f"\nEpisodic: error — {e}")

    # Backups
    try:
        backups = sorted(_BACKUP_DIR.glob("*.zip")) if _BACKUP_DIR.exists() else []
        if backups:
            latest = backups[-1]
            mod = datetime.fromtimestamp(
                latest.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            lines.append(f"\n**Backups:** {len(backups)} saved "
                         f"(latest: {mod})")
        else:
            lines.append("\n**Backups:** none — run backup_memory to create one")
    except Exception:
        pass

    return "\n".join(lines)


# ── Tool declarations ──────────────────────────────────────────────────────

MEMORY_TOOL_DECLARATIONS = [
    {
        "name": "backup_memory",
        "description": "Backup all memories (long-term JSON, episodic SQLite DB, goals, RAG index) to the per-user data folder.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "tag": {"type": "STRING", "description": "Optional label for the backup filename"},
            },
        },
    },
    {
        "name": "export_memory",
        "description": "Export all episodic and long-term memories to a readable JSON file.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "output_path": {"type": "STRING", "description": "Optional output file path"},
            },
        },
    },
    {
        "name": "import_memory",
        "description": "Import memories from a previously exported JSON file. Merges, does not overwrite.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "source_path": {"type": "STRING", "description": "Path to the exported JSON file"},
            },
            "required": ["source_path"],
        },
    },
    {
        "name": "prune_memory",
        "description": "Remove old, short, and duplicate entries from the episodic memory database. Use dry_run=true to preview.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "max_age_days": {"type": "INTEGER", "description": "Delete entries older than this many days (default 90)"},
                "min_length":   {"type": "INTEGER", "description": "Delete entries shorter than this many characters (default 20)"},
                "dry_run":      {"type": "BOOLEAN", "description": "If true, show what would be deleted without deleting"},
            },
        },
    },
    {
        "name": "memory_stats",
        "description": "Show detailed statistics for all memory systems (long-term, episodic, backups).",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
]
