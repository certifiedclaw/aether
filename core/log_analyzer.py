"""core/log_analyzer.py - turn logs/aether.log into "what keeps going wrong" (roadmap #85).

Groups WARNING/ERROR lines (and the final line of tracebacks) by a normalized message --
numbers, ids, quoted names and paths replaced -- and reports count, first/last seen and one
example for each, worst first, plus an hourly error timeline so a spike is easy to spot.
Local and read-only. Chat command: /loganalyze [hours]
"""
from __future__ import annotations

import re
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})[,.]?\d* +(\w+) +([\w.\-]+) +[—-]+ (.*)$")
_EXC = re.compile(r"^([A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt|Timeout)):\s*(.*)$")
_PREFIX = re.compile(r"^(?:WRN|ERR|ERROR|WARN|WARNING)\s*:\s*", re.I)
_LEVELS = {"WARNING": "WARN", "WARN": "WARN", "ERROR": "ERROR", "CRITICAL": "ERROR"}


def normalize(msg: str) -> str:
    m = _PREFIX.sub("", msg.strip())
    m = re.sub(r"https?://\S+", "<url>", m)
    m = re.sub(r"[A-Za-z]:\\[^\s'\"]+|(?:/[\w.\-]+){2,}", "<path>", m)
    m = re.sub(r"\b[0-9a-f]{8,}\b", "<id>", m)
    m = re.sub(r"'[^']{1,80}'|\"[^\"]{1,80}\"", "<q>", m)
    m = re.sub(r"\d+(?:\.\d+)?", "N", m)
    return m[:140]


def _files(path: Path) -> list[Path]:
    rotated = sorted(path.parent.glob(path.name + ".*"), reverse=True)
    return [p for p in rotated if p.suffix.lstrip(".").isdigit()] + [path]


def analyze(path: Path | str | None = None, hours: float = 24.0, now: float | None = None) -> dict:
    path = Path(path) if path else Path(__file__).resolve().parent.parent / "logs" / "aether.log"
    cutoff = (time.time() if now is None else now) - hours * 3600
    groups: dict[tuple, dict] = {}
    timeline: Counter = Counter()
    total_lines = 0
    for p in _files(path):
        if not p.exists():
            continue
        last = None
        for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
            m = _LINE.match(raw)
            if m:
                ts_s, lvl, _name, msg = m.groups()
                ts = datetime.strptime(ts_s, "%Y-%m-%d %H:%M:%S").timestamp()
                total_lines += 1
                last = None
                level = _LEVELS.get(lvl.upper())
                if not level and _PREFIX.match(msg):
                    level = "ERROR" if re.match(r"(?:ERR|ERROR)", msg, re.I) else "WARN"
                if not level or ts < cutoff:
                    last = None
                    continue
                key = (level, normalize(msg))
                g = groups.setdefault(key, {"count": 0, "first": ts, "last": ts, "example": msg[:200]})
                g["count"] += 1
                g["first"], g["last"] = min(g["first"], ts), max(g["last"], ts)
                timeline[ts_s[:13]] += 1
                last = g
            else:
                e = _EXC.match(raw.strip())
                if e and last is not None and "exception" not in last:
                    last["exception"] = f"{e.group(1)}: {e.group(2)}"[:160]
    ranked = sorted(groups.items(), key=lambda kv: (kv[0][0] != "ERROR", -kv[1]["count"]))
    return {"hours": hours, "lines": total_lines,
            "groups": [{"level": k[0], "pattern": k[1], **v} for k, v in ranked],
            "timeline": dict(sorted(timeline.items()))}


def render(rep: dict, top: int = 8) -> str:
    gs = rep["groups"]
    if not gs:
        return f"Log analysis, last {rep['hours']:g}h: no warnings or errors in {rep['lines']} lines."
    n_err = sum(g["count"] for g in gs if g["level"] == "ERROR")
    n_warn = sum(g["count"] for g in gs if g["level"] == "WARN")
    out = [f"Log analysis, last {rep['hours']:g}h: {n_err} error(s), {n_warn} warning(s), {len(gs)} distinct"]
    for g in gs[:top]:
        f, l = (datetime.fromtimestamp(g[k]).strftime("%m-%d %H:%M") for k in ("first", "last"))
        out.append(f"  [{g['level']}] x{g['count']}  {g['pattern']}  ({f} - {l})")
        if g.get("exception"):
            out.append(f"      {g['exception']}")
    if len(gs) > top:
        out.append(f"  ... and {len(gs) - top} more")
    tl = rep["timeline"]
    if tl:
        peak = max(tl, key=tl.get)
        out.append(f"  busiest hour: {peak}:00 with {tl[peak]} problem lines")
    return "\n".join(out)


def report(hours: float = 24.0) -> str:
    return render(analyze(hours=hours))
