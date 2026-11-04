"""core/usage_stats.py - owner-only usage statistics, computed locally (roadmap #50).

Reads logs Aether already writes (logs/perf.jsonl, logs/audit.jsonl, logs/crashes/) and
summarizes them: model rounds per day and route, busiest hours, most-used tools, how often
approvals were denied, crash count. Nothing is collected anew, nothing is sent anywhere, and
no message text is read -- only model names, tool names, counts and timestamps.

Chat command: /stats [days]
"""
from __future__ import annotations

import time
from collections import Counter
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def collect(days: float = 7.0, *, perf_path: Path | None = None, audit_path: Path | None = None,
            crash_dir: Path | None = None, now: float | None = None) -> dict:
    from core import audit_log, perf_stats
    now = time.time() if now is None else now
    since = now - max(0.01, float(days)) * 86400

    perf = perf_stats.read(since=since, path=perf_path)
    by_day: Counter = Counter()
    by_hour: Counter = Counter()
    by_route: Counter = Counter()
    by_model: Counter = Counter()
    out_tokens = 0
    for e in perf:
        t = datetime.fromtimestamp(e.get("ts", 0))
        by_day[t.strftime("%Y-%m-%d")] += 1
        by_hour[t.hour] += 1
        by_route[e.get("route") or "?"] += 1
        by_model[e.get("model") or "?"] += 1
        out_tokens += int(e.get("output_tokens") or 0)

    audit = audit_log.read(since=since, limit=2000, path=audit_path)
    tools: Counter = Counter(e.get("tool", "?") for e in audit if e.get("tool") != "supervisor")
    decisions: Counter = Counter(e.get("decision", "?") for e in audit if e.get("tool") != "supervisor")
    gated = sum(decisions[d] for d in ("approved", "denied"))

    cdir = Path(crash_dir) if crash_dir else _ROOT / "logs" / "crashes"
    crashes = 0
    if cdir.exists():
        crashes = sum(1 for f in cdir.glob("*.json") if f.stat().st_mtime >= since)

    return {"days": days, "rounds": len(perf), "by_day": dict(sorted(by_day.items())),
            "busiest_hours": [h for h, _ in by_hour.most_common(3)],
            "routes": dict(by_route), "models": dict(by_model.most_common(5)),
            "output_tokens": out_tokens, "top_tools": tools.most_common(8),
            "decisions": dict(decisions), "denied_rate": (decisions["denied"] / gated) if gated else None,
            "crashes": crashes}


def render(s: dict) -> str:
    d = s["days"]
    lines = [f"Usage, last {d:g} day(s) - local only, nothing leaves this machine",
             f"  model rounds: {s['rounds']}  (output tokens: {s['output_tokens']})"]
    if s["by_day"]:
        lines.append("  per day: " + ", ".join(f"{k[5:]}={v}" for k, v in s["by_day"].items()))
    if s["busiest_hours"]:
        lines.append("  busiest hours: " + ", ".join(f"{h:02d}:00" for h in s["busiest_hours"]))
    if s["routes"]:
        lines.append("  routes: " + ", ".join(f"{k}={v}" for k, v in s["routes"].items()))
    if s["models"]:
        lines.append("  models: " + ", ".join(f"{k}={v}" for k, v in s["models"].items()))
    if s["top_tools"]:
        lines.append("  tools: " + ", ".join(f"{k}={v}" for k, v in s["top_tools"]))
    if s["denied_rate"] is not None:
        lines.append(f"  approvals denied: {s['denied_rate']:.0%}")
    lines.append(f"  crashes: {s['crashes']}")
    if not s["rounds"] and not s["top_tools"]:
        lines.append("  (no activity logged in this window)")
    return "\n".join(lines)


def report(days: float = 7.0) -> str:
    return render(collect(days))
