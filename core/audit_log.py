"""core/audit_log.py - searchable record of every gated tool call and decision (roadmap #44).

One JSON object per line in logs/audit.jsonl (mode 0600, rotated at 5 MB, 3 kept):
    {"ts": 1789000000.1, "tool": "file_controller", "action": "delete", "tier": "ask",
     "decision": "approved", "params": {"path": "C:/x"}, "turn": "delete the file", "prev": "<hash>"}

decision: allowed | approved | denied | blocked
Each line carries `prev`, the SHA-256 of the previous line, so deleting or editing
an entry breaks the chain and verify() reports where. (Tamper-EVIDENT, not tamper-proof:
someone who can rewrite the whole file can rebuild the chain.)

Secrets are redacted before writing (secret-looking parameter names, API keys/tokens in
values) and long values are truncated. Safe-tier calls are skipped unless
config audit_log_safe=true; set audit_log=false to turn the log off.
Writing never raises: auditing must not break a tool call.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = _ROOT / "logs"
LOG_FILE = LOG_DIR / "audit.jsonl"
MAX_BYTES = 5 * 1024 * 1024
KEEP = 3
GENESIS = "genesis"
_VALUE_LIMIT = 300
_CODE_LIMIT = 600
_CODE_KEYS = frozenset({"code", "command", "script"})
_SECRETISH = re.compile(r"pass(word|phrase)?|secret|token|api_?key|credential|^pin$", re.I)

_lock = threading.Lock()
_last_hash: dict[str, str] = {}      # per log path


def _cfg() -> dict:
    try:
        from core.llm_client import _load_config
        return _load_config() or {}
    except Exception:  # noqa: BLE001
        return {}


def _flag(cfg: dict, key: str, default: bool) -> bool:
    v = cfg.get(key, default)
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def _scrub(value, key: str = ""):
    if _SECRETISH.search(str(key)):
        return "(hidden)"
    if isinstance(value, dict):
        return {str(k): _scrub(v, str(k)) for k, v in list(value.items())[:20]}
    if isinstance(value, (list, tuple)):
        return [_scrub(v) for v in list(value)[:20]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    text = str(value)
    try:
        from core.leak_guard import redact_text
        text, _ = redact_text(text)
    except Exception:  # noqa: BLE001
        pass
    limit = _CODE_LIMIT if key in _CODE_KEYS else _VALUE_LIMIT
    if len(text) > limit:
        text = text[:limit] + f"... [{len(text)} chars]"
    return text


def _hash(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def _tail_hash(path: Path) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 65536))
            lines = f.read().decode("utf-8", "replace").splitlines()
        for line in reversed(lines):
            if line.strip():
                return _hash(line)
    except OSError:
        pass
    return GENESIS


def _rotate(path: Path) -> None:
    try:
        if path.exists() and path.stat().st_size >= MAX_BYTES:
            for i in range(KEEP, 0, -1):
                src = path.with_name(f"{path.name}.{i - 1}" if i > 1 else path.name)
                dst = path.with_name(f"{path.name}.{i}")
                if src.exists():
                    if dst.exists():
                        dst.unlink()
                    src.rename(dst)
    except OSError:
        pass


def record(tool: str, action: str, tier: str, decision: str, params: dict | None = None,
           note: str = "", turn: str = "", path: Path | None = None) -> None:
    """Append one entry. Never raises."""
    try:
        cfg = _cfg()
        if not _flag(cfg, "audit_log", True):
            return
        if tier == "safe" and not _flag(cfg, "audit_log_safe", False):
            return
        path = Path(path) if path else LOG_FILE
        entry = {"ts": round(time.time(), 3), "tool": str(tool), "action": str(action or ""),
                 "tier": str(tier), "decision": str(decision),
                 "params": _scrub(params or {})}
        if note:
            entry["note"] = _scrub(note, "note")
        if turn:
            entry["turn"] = _scrub(turn, "turn")[:120]
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            key = str(path)
            before = path.stat().st_size if path.exists() else 0
            _rotate(path)
            if key not in _last_hash or (path.exists() and path.stat().st_size < before) or not path.exists():
                _last_hash[key] = _tail_hash(path) if path.exists() else _carry_hash(path)
            entry["prev"] = _last_hash[key]
            line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
            new = not path.exists()
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            if new:
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass
            _last_hash[key] = _hash(line)
    except Exception:  # noqa: BLE001 - auditing must never break the action it records
        pass


def _carry_hash(path: Path) -> str:
    """First entry after a rotation chains to the last line of the rotated file."""
    rotated = path.with_name(path.name + ".1")
    return _tail_hash(rotated) if rotated.exists() else GENESIS


def _files(path: Path) -> list[Path]:
    """Oldest first."""
    out = [path.with_name(f"{path.name}.{i}") for i in range(KEEP, 0, -1)] + [path]
    return [p for p in out if p.exists()]


def _iter_lines(path: Path):
    for p in _files(path):
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line.strip():
                        yield line.rstrip("\n")
        except OSError:
            continue


def read(since: float | None = None, tool: str = "", decision: str = "", tier: str = "",
         text: str = "", limit: int = 200, path: Path | None = None) -> list[dict]:
    """Newest first. `text` matches case-insensitively anywhere in the entry."""
    path = Path(path) if path else LOG_FILE
    needle = text.lower().strip()
    out: list[dict] = []
    for line in _iter_lines(path):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        if since is not None and e.get("ts", 0) < since:
            continue
        if tool and e.get("tool") != tool:
            continue
        if decision and e.get("decision") != decision:
            continue
        if tier and e.get("tier") != tier:
            continue
        if needle and needle not in line.lower():
            continue
        out.append(e)
    out.reverse()
    return out[:max(1, min(int(limit or 200), 2000))]


def verify(path: Path | None = None) -> tuple[bool, str]:
    """Check the hash chain across all kept files. Returns (ok, message)."""
    path = Path(path) if path else LOG_FILE
    prev = None
    n = 0
    for line in _iter_lines(path):
        try:
            e = json.loads(line)
        except ValueError:
            return False, f"entry {n + 1} is not valid JSON"
        if prev is not None and e.get("prev") != prev:
            return False, f"chain broken at entry {n + 1}: an earlier entry was edited or removed"
        prev = _hash(line)
        n += 1
    return True, f"{n} entries, chain intact" if n else "no entries"


def summarize(entries: list[dict]) -> dict:
    by_decision: dict[str, int] = {}
    by_tool: dict[str, int] = {}
    for e in entries:
        by_decision[e.get("decision", "?")] = by_decision.get(e.get("decision", "?"), 0) + 1
        by_tool[e.get("tool", "?")] = by_tool.get(e.get("tool", "?"), 0) + 1
    return {"count": len(entries), "by_decision": by_decision,
            "top_tools": sorted(by_tool.items(), key=lambda kv: -kv[1])[:5]}


def render(entries: list[dict]) -> str:
    if not entries:
        return "Audit log: no matching entries."
    lines = []
    for e in entries:
        when = time.strftime("%m-%d %H:%M:%S", time.localtime(e.get("ts", 0)))
        label = f"{e.get('tool')}.{e['action']}" if e.get("action") else str(e.get("tool"))
        params = e.get("params") or {}
        brief = ", ".join(f"{k}={str(v)[:40]}" for k, v in list(params.items())[:3])
        lines.append(f"{when}  {e.get('decision', '?'):8} {e.get('tier', ''):7} {label}"
                     + (f"  [{brief}]" if brief else "")
                     + (f"  - {e['note']}" if e.get("note") else ""))
    s = summarize(entries)
    head = f"Audit log: {s['count']} entries (newest first) " + ", ".join(
        f"{k}={v}" for k, v in sorted(s["by_decision"].items()))
    return head + "\n" + "\n".join(lines)


def parse_query(argv: str) -> dict:
    """'/audit' arguments -> read() kwargs. Accepts: N, tool=x, decision=x, tier=x, hours=N, any other words = text."""
    kw: dict = {"limit": 20}
    words = []
    for tok in argv.split():
        if tok.isdigit():
            kw["limit"] = int(tok)
        elif "=" in tok:
            k, v = tok.split("=", 1)
            if k in ("tool", "decision", "tier"):
                kw[k] = v
            elif k == "hours" and v.replace(".", "", 1).isdigit():
                kw["since"] = time.time() - float(v) * 3600
            else:
                words.append(tok)
        else:
            words.append(tok)
    if words:
        kw["text"] = " ".join(words)
    return kw
