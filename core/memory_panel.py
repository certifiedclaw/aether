"""core/memory_panel.py - view, edit and delete what Aether remembers (roadmap #57, #32).

Two stores:
  facts      long_term.json   - identity / preferences / projects / ... key -> value
  exchanges  memory.db        - past conversations Aether retrieves from (hybrid memory)

Item ids:  "f:<category>/<key>"  and  "e:<row id>".

Entries in long_term.json that have no usable "value" (the #32 symptom) are listed too,
flagged `problem`, so they can be repaired or deleted instead of sitting there invisibly;
nothing else in Aether ever touches them.

Used by /memory (chat) and the dashboard's /memory page. When Aether is running, edits go
through the live HybridMemory so its search index stays in step; with no live instance
(the standalone dashboard) they go straight to the database and the index is rebuilt at
the next start.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

CATEGORIES = ("identity", "preferences", "projects", "relationships", "wishes", "notes")
_live = None


def set_live(hybrid) -> None:
    global _live
    _live = hybrid


# ── facts ───────────────────────────────────────────────────────────────
def _facts(query: str = "") -> list[dict]:
    from memory import memory_manager as mm
    q = query.lower().strip()
    out = []
    for cat, items in mm.load_memory().items():
        if not isinstance(items, dict):
            continue
        for key, entry in items.items():
            value = entry.get("value") if isinstance(entry, dict) else None
            ok = isinstance(value, str) and bool(value.strip())
            if not ok and isinstance(entry, str) and entry.strip():
                value, ok = entry, True                      # legacy plain-string entry: usable
            text = value if ok else ""
            if q and q not in f"{cat} {key} {text}".lower():
                continue
            out.append({"id": f"f:{cat}/{key}", "kind": "fact", "category": cat, "key": key,
                        "value": text, "updated": entry.get("updated", "") if isinstance(entry, dict) else "",
                        "problem": "" if ok else "no value (repair or delete)"})
    return out


def _parse_fact_id(item_id: str) -> tuple[str, str]:
    body = item_id[2:]
    cat, _, key = body.partition("/")
    if cat not in CATEGORIES or not key:
        raise ValueError(f"unknown fact id {item_id!r}")
    return cat, key


# ── exchanges ───────────────────────────────────────────────────────────
def _db_path():
    from core.data_home import resolve_data_path
    return resolve_data_path(None, "memory.db")


def _exchanges(query: str, limit: int) -> list[dict]:
    if _live is not None:
        rows = _live.list_exchanges(query, limit)
    else:
        p = _db_path()
        if not p.exists():
            return []
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            q = ("%" + query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                 if query.strip() else "%")
            rows = [{"id": r[0], "role": r[1], "content": r[2], "ts": r[3]} for r in con.execute(
                "SELECT id, role, content, timestamp FROM memories WHERE content LIKE ? ESCAPE '\\' "
                "ORDER BY id DESC LIMIT ?", (q, limit))]
        finally:
            con.close()
    return [{"id": f"e:{r['id']}", "kind": "exchange", "value": r["content"],
             "updated": datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M") if r.get("ts") else "",
             "problem": ""} for r in rows]


# ── public API ──────────────────────────────────────────────────────────
def list_all(query: str = "", limit: int = 100) -> dict:
    facts = _facts(query)
    exch = _exchanges(query, max(1, min(int(limit), 500)))
    return {"facts": facts, "exchanges": exch,
            "problems": sum(1 for f in facts if f["problem"])}


def add_fact(category: str, key: str, value: str) -> tuple[bool, str]:
    key, value = str(key or "").strip(), str(value or "").strip()
    if category not in CATEGORIES:
        return False, f"category must be one of {', '.join(CATEGORIES)}"
    if not key or len(key) > 80 or not value:
        return False, "a key (max 80 chars) and a value are required"
    from memory import memory_manager as mm
    mm.remember(key, value, category)
    return True, f"Saved {category}/{key}."


def edit(item_id: str, new_text: str) -> tuple[bool, str]:
    new_text = str(new_text or "").strip()
    if not new_text:
        return False, "the new text is empty - use delete to remove it"
    try:
        if item_id.startswith("f:"):
            cat, key = _parse_fact_id(item_id)
            from memory import memory_manager as mm
            memory = mm.load_memory()
            if key not in memory.get(cat, {}):
                return False, f"no such fact {cat}/{key}"
            memory[cat][key] = {"value": mm._truncate_value(new_text),
                                "updated": datetime.now().strftime("%Y-%m-%d")}
            mm.save_memory(memory)
            return True, f"Updated {cat}/{key}."
        if item_id.startswith("e:"):
            rid = int(item_id[2:])
            if _live is not None:
                ok = _live.update_content(rid, new_text)
            else:
                con = sqlite3.connect(str(_db_path()))
                try:
                    ok = bool(con.execute("UPDATE memories SET content=?, embedding=NULL WHERE id=?",
                                          (new_text, rid)).rowcount)
                    con.commit()
                finally:
                    con.close()
            return (True, f"Updated exchange {rid}.") if ok else (False, f"no such exchange {rid}")
    except (ValueError, sqlite3.Error) as e:
        return False, str(e)
    return False, f"unknown id {item_id!r} (use f:<category>/<key> or e:<number>)"


def delete(item_id: str) -> tuple[bool, str]:
    try:
        if item_id.startswith("f:"):
            cat, key = _parse_fact_id(item_id)
            from memory import memory_manager as mm
            memory = mm.load_memory()
            if key not in memory.get(cat, {}):
                return False, f"no such fact {cat}/{key}"
            del memory[cat][key]            # not mm.forget(): that one only sees well-formed entries' dicts
            mm.save_memory(memory)
            return True, f"Forgot {cat}/{key}."
        if item_id.startswith("e:"):
            rid = int(item_id[2:])
            if _live is not None:
                n = _live.delete_ids([rid])
            else:
                con = sqlite3.connect(str(_db_path()))
                try:
                    n = con.execute("DELETE FROM memories WHERE id=?", (rid,)).rowcount
                    con.commit()
                finally:
                    con.close()
            return (True, f"Forgot exchange {rid}.") if n else (False, f"no such exchange {rid}")
    except (ValueError, sqlite3.Error) as e:
        return False, str(e)
    return False, f"unknown id {item_id!r} (use f:<category>/<key> or e:<number>)"


def render(listing: dict, max_exchanges: int = 8) -> str:
    lines = ["What Aether remembers about you:"]
    facts = listing["facts"]
    if not facts:
        lines.append("  (no saved facts)")
    for f in facts:
        tail = f"  <-- {f['problem']}" if f["problem"] else ""
        lines.append(f"  {f['id']} = {f['value'][:80]}{tail}")
    ex = listing["exchanges"]
    lines.append(f"Past conversations ({len(ex)} shown):")
    for e in ex[:max_exchanges]:
        lines.append(f"  {e['id']} [{e['updated']}] " + " ".join(e["value"].split())[:90])
    lines.append("Change: /memory forget <id> | /memory edit <id> <new text> | /memory add <category> <key> <value>"
                 "  |  /memory <words> to search")
    return "\n".join(lines)


def handle_command(text: str) -> str:
    rest = text.strip()[len("/memory"):].strip()
    verb, _, arg = rest.partition(" ")
    verb, arg = verb.lower(), arg.strip()
    if verb in ("forget", "delete", "rm") and arg:
        return delete(arg.split()[0])[1]
    if verb == "edit" and arg:
        item, _, new = arg.partition(" ")
        return edit(item, new)[1]
    if verb == "add" and arg:
        parts = arg.split(None, 2)
        if len(parts) == 3:
            return add_fact(parts[0].lower(), parts[1], parts[2])[1]
        return "Usage: /memory add <category> <key> <value>"
    return render(list_all(rest))
