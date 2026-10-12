"""core/live_report.py — Live per-turn usage dump.

Answers the audit's "Evidence Standard and Limitations" gap: everything in
the audit was read from source, not observed running. This module records
one line per real turn to logs/live_turns.jsonl (so nothing is lost, ever
appendable, greppable) and rewrites logs/live_report.md after every turn
with a rolling snapshot — tier distribution, tool-router effectiveness,
memory hit rate, tool-loop health, latency — so you can just open
live_report.md at any time while Aether is running and see current real
usage, no script or command needed.

Usage (wired into core/conversation_core.py at the end of a real turn):

    from core.live_report import record_turn
    record_turn(
        user_text=user_text, reply=final_assistant_reply,
        tier=_route.tier.name, tier_reason=_route.reason,
        tools_sent=len(_turn_tools), memory_hit=_memory_hit,
        gemini_escalated=bool(_stream_kwargs), rounds_used=_rounds_used,
        exhausted=_loop_exhausted, truncated=_any_truncated,
        latency_ms=_latency_ms,
    )

Deliberately dependency-free and best-effort: a failure here must never
break a real turn, so every public function swallows its own exceptions.
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_LOG_DIR      = Path(__file__).resolve().parent.parent / "logs"
_JSONL_PATH   = _LOG_DIR / "live_turns.jsonl"
_REPORT_PATH  = _LOG_DIR / "live_report.md"
_MAX_JSONL_BYTES  = 5 * 1024 * 1024   # same rotation policy as core/observability.py
_JSONL_KEEP_LINES = 5000
_ROLLING_WINDOW   = 50                # how many recent turns the .md table shows

_lock    = threading.Lock()
_recent: deque[dict] = deque(maxlen=_ROLLING_WINDOW)

# Running totals since process start — not reset by rotation, so the
# "since start" percentages in the report stay accurate for the whole
# session even after old jsonl lines have been trimmed.
_totals = {
    "turns":       0,
    "tier_counts": {},   # tier name -> count
    "memory_hits": 0,
    "memory_checked": 0,   # turns where memory injection was even attempted (non-QUICK)
    "gemini_escalations": 0,
    "exhausted":   0,
    "truncated":   0,
    "tools_sent_sum": 0,
    "latency_sum_ms": 0.0,
}
_start_time = time.time()


def turn_count() -> int:
    """Number of real conversational turns recorded so far this process.
    Used by main.py's headless stdin test driver to know when one line
    of a scripted test file has actually finished processing (not just
    been dequeued) before sending the next one."""
    return _totals["turns"]


def record_turn(
    *,
    user_text: str,
    reply: str,
    tier: str,
    tier_reason: str = "",
    tools_sent: int = 0,
    memory_hit: bool | None = None,
    gemini_escalated: bool = False,
    rounds_used: int = 0,
    exhausted: bool = False,
    truncated: bool = False,
    latency_ms: float = 0.0,
) -> None:
    """Record one real turn. Safe to call from the turn thread — best-effort,
    never raises."""
    try:
        record = {
            "ts":           time.time(),
            "tier":         tier,
            "tier_reason":  tier_reason,
            "tools_sent":   tools_sent,
            "memory_hit":   memory_hit,   # None = not attempted (QUICK-tier turn)
            "gemini_escalated": gemini_escalated,
            "rounds_used":  rounds_used,
            "exhausted":    exhausted,
            "truncated":    truncated,
            "latency_ms":   round(latency_ms, 1),
            "user_preview": user_text[:200],
            "reply_preview": reply[:300],
        }
        with _lock:
            _append_jsonl(record)
            _recent.append(record)
            _update_totals(record)
            _rewrite_report()
    except Exception:
        pass  # a reporting bug must never break a real turn


def _append_jsonl(record: dict) -> None:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, default=str)
    if _JSONL_PATH.exists() and _JSONL_PATH.stat().st_size >= _MAX_JSONL_BYTES:
        try:
            lines = _JSONL_PATH.read_text(encoding="utf-8").splitlines()
            kept  = lines[-_JSONL_KEEP_LINES:]
            _JSONL_PATH.write_text("\n".join(kept) + "\n", encoding="utf-8")
        except Exception:
            pass
    with open(_JSONL_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _update_totals(record: dict) -> None:
    _totals["turns"] += 1
    _totals["tier_counts"][record["tier"]] = _totals["tier_counts"].get(record["tier"], 0) + 1
    if record["memory_hit"] is not None:
        _totals["memory_checked"] += 1
        if record["memory_hit"]:
            _totals["memory_hits"] += 1
    if record["gemini_escalated"]:
        _totals["gemini_escalations"] += 1
    if record["exhausted"]:
        _totals["exhausted"] += 1
    if record["truncated"]:
        _totals["truncated"] += 1
    _totals["tools_sent_sum"] += record["tools_sent"]
    _totals["latency_sum_ms"] += record["latency_ms"]


def _pct(n: int, d: int) -> str:
    return f"{(100 * n / d):.0f}%" if d else "—"


def _rewrite_report() -> None:
    n = _totals["turns"]
    uptime_min = (time.time() - _start_time) / 60

    lines = [
        "# Aether — Live Usage Report",
        "",
        f"_Auto-updated after every turn. Last update: "
        f"{time.strftime('%Y-%m-%d %H:%M:%S')}_",
        "",
        f"**Turns this session:** {n}  |  **Uptime:** {uptime_min:.0f} min",
        "",
        "## Tier routing",
        "",
    ]
    for tname, tcount in sorted(_totals["tier_counts"].items(), key=lambda x: -x[1]):
        lines.append(f"- {tname}: {tcount} ({_pct(tcount, n)})")

    lines += [
        "",
        "## Tool router",
        "",
        f"- Avg tools sent per turn: {(_totals['tools_sent_sum'] / n):.1f}" if n else "- Avg tools sent per turn: —",
        f"- Gemini escalations (HARD tier): {_totals['gemini_escalations']} ({_pct(_totals['gemini_escalations'], n)})",
        "",
        "## Memory retrieval",
        "",
        f"- Hit rate (of turns that checked memory): "
        f"{_pct(_totals['memory_hits'], _totals['memory_checked'])} "
        f"({_totals['memory_hits']}/{_totals['memory_checked']})",
        "",
        "## Tool-loop health",
        "",
        f"- Exhausted without a reply: {_totals['exhausted']} ({_pct(_totals['exhausted'], n)})",
        f"- Truncated at generation limit: {_totals['truncated']} ({_pct(_totals['truncated'], n)})",
        "",
        "## Latency",
        "",
        f"- Avg turn latency: {(_totals['latency_sum_ms'] / n):.0f} ms" if n else "- Avg turn latency: —",
        "",
        f"## Last {len(_recent)} turns",
        "",
        "| time | tier | tools | mem hit | rounds | latency | flags | reply |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in reversed(_recent):
        ts = time.strftime("%H:%M:%S", time.localtime(r["ts"]))
        mem = "—" if r["memory_hit"] is None else ("✅" if r["memory_hit"] else "❌")
        flags = " ".join(f for f, on in (
            ("gemini", r["gemini_escalated"]),
            ("exhausted", r["exhausted"]),
            ("truncated", r["truncated"]),
        ) if on) or "-"
        reply_preview = r["reply_preview"].replace("|", "/").replace("\n", " ")[:120]
        lines.append(
            f"| {ts} | {r['tier']} | {r['tools_sent']} | {mem} | "
            f"{r['rounds_used']} | {r['latency_ms']:.0f}ms | {flags} | {reply_preview} |"
        )

    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        _REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass
