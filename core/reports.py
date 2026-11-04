"""core/reports.py - daily briefing and scheduled reports, pushed through ntfy (roadmap #63, #65).

Two reports, both off until configured (config/api_keys.json):

  briefing_time        "07:30"   local time for the daily briefing (weather, goals, server status)
  briefing_days        "daily"   or "weekdays", or e.g. "mon,wed,fri"
  briefing_city        "Boston"  adds a weather line (wttr.in, no key); blank = skip
  weekly_report_day    "sun"     weekly server-health report: server status, usage, log problems
  weekly_report_time   "09:00"
  ntfy_topic                     required, see core/notify.py

Delivery rules: a report is sent at most once per calendar day. If the machine was off at the
scheduled time it is still sent when Aether comes up within `report_catchup_hours` (default 6),
and skipped after that rather than arriving stale. A failed push is retried after 30 minutes.
The "last sent" state lives in the per-user data folder, so a restart never repeats a report.

Chat command: /briefing (shows today's briefing now, without sending), /report (weekly report).
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

logger = logging.getLogger("aether")

_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
RETRY_AFTER_S = 1800


@dataclass
class Schedule:
    name: str
    at: str                       # "HH:MM" local
    days: set[int] = field(default_factory=lambda: set(range(7)))   # Monday=0
    sections: tuple = ()
    title: str = ""


def parse_days(spec) -> set[int]:
    s = str(spec or "daily").strip().lower()
    if s in ("daily", "every day", "all", ""):
        return set(range(7))
    if s == "weekdays":
        return set(range(5))
    if s == "weekends":
        return {5, 6}
    out = {_DAYS.index(p.strip()[:3]) for p in s.split(",") if p.strip()[:3] in _DAYS}
    return out or set(range(7))


def _valid_time(v) -> str | None:
    try:
        h, m = str(v).strip().split(":")
        if 0 <= int(h) <= 23 and 0 <= int(m) <= 59:
            return f"{int(h):02d}:{int(m):02d}"
    except (ValueError, AttributeError):
        pass
    return None


def schedules_from_config(cfg: dict) -> list[Schedule]:
    out = []
    t = _valid_time(cfg.get("briefing_time"))
    if t:
        out.append(Schedule("daily_briefing", t, parse_days(cfg.get("briefing_days")),
                            ("weather", "goals", "server"), "Aether daily briefing"))
    t = _valid_time(cfg.get("weekly_report_time") or ("09:00" if cfg.get("weekly_report_day") else ""))
    if t and cfg.get("weekly_report_day"):
        out.append(Schedule("weekly_report", t, parse_days(cfg.get("weekly_report_day")),
                            ("server", "usage", "problems"), "Aether weekly report"))
    return out


def is_due(sch: Schedule, now: datetime, last_sent_date: str | None, catchup_hours: float = 6.0,
           retry_after: datetime | None = None) -> bool:
    if now.weekday() not in sch.days or last_sent_date == now.strftime("%Y-%m-%d"):
        return False
    if retry_after and now < retry_after:
        return False
    h, m = map(int, sch.at.split(":"))
    start = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return start <= now <= start + timedelta(hours=max(0.0, catchup_hours))


# ── sections ─────────────────────────────────────────────────────────────
def _weather(cfg, ctx) -> str:
    city = str(cfg.get("briefing_city") or "").strip()
    if not city:
        return ""
    fetch = ctx.get("weather") or _default_weather
    text = fetch(city, "today")
    return f"Weather: {text}" if text else "Weather: unavailable right now."


def _default_weather(city: str, when: str):
    from actions.weather_report import _fetch_weather
    return _fetch_weather(city, when)


def _goals(cfg, ctx) -> str:
    get = ctx.get("goals")
    goals = get() if get else []
    if not goals:
        return "Goals: nothing active."
    return "Goals:\n" + "\n".join("  " + g.format_brief() for g in goals[:8])


def _server(cfg, ctx) -> str:
    from core import supervisor
    sup = ctx.get("supervisor") or supervisor.build_from_config(cfg)
    lines, bad = [], 0
    for p in sup.probes:
        try:
            ok, detail = p.check()
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"check failed: {e}"
        bad += not ok
        lines.append(f"  {'OK  ' if ok else 'DOWN'} {p.name}: {detail}")
    return ("Server: all healthy" if not bad else f"Server: {bad} problem(s)") + "\n" + "\n".join(lines)


def _usage(cfg, ctx) -> str:
    from core import usage_stats
    return usage_stats.report(7)


def _problems(cfg, ctx) -> str:
    from core import log_analyzer
    return log_analyzer.report(24 * 7)


SECTIONS: dict[str, Callable] = {"weather": _weather, "goals": _goals, "server": _server,
                                 "usage": _usage, "problems": _problems}


def build_report(sch: Schedule, cfg: dict, ctx: dict | None = None, now: datetime | None = None) -> str:
    ctx = ctx or {}
    now = now or datetime.now()
    parts = [f"{sch.title or sch.name} - {now:%a %b %d}"]
    for name in sch.sections:
        try:
            text = SECTIONS[name](cfg, ctx)
        except Exception as e:  # noqa: BLE001 - one broken section must not lose the whole report
            logger.warning("[reports] section %s failed: %s", name, e)
            text = f"{name}: unavailable ({type(e).__name__})"
        if text:
            parts.append(text)
    return "\n\n".join(parts)


# ── runner ───────────────────────────────────────────────────────────────
class ReportRunner:
    def __init__(self, cfg: dict, ctx: dict | None = None, *, state_path=None,
                 send: Callable[..., bool] | None = None, clock: Callable[[], datetime] = datetime.now):
        self.cfg, self.ctx, self._clock = cfg, ctx or {}, clock
        self._send = send or _default_send
        self._state_path = state_path
        self._retry: dict[str, datetime] = {}

    def _path(self):
        if self._state_path:
            return self._state_path
        from core.data_home import data_path
        return data_path("reports_state.json")

    def _load(self) -> dict:
        try:
            return json.loads(self._path().read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    def _save(self, state: dict) -> None:
        try:
            p = self._path()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(state), encoding="utf-8")
        except OSError as e:
            logger.warning("[reports] could not save state: %s", e)

    def run_due(self) -> list[str]:
        """Send every report that is due. Returns the names sent."""
        now = self._clock()
        sent = []
        state = self._load()
        catchup = float(self.cfg.get("report_catchup_hours", 6))
        for sch in schedules_from_config(self.cfg):
            if not is_due(sch, now, state.get(sch.name), catchup, self._retry.get(sch.name)):
                continue
            text = build_report(sch, self.cfg, self.ctx, now)
            if self._send(text, title=sch.title or sch.name, priority="default"):
                state[sch.name] = now.strftime("%Y-%m-%d")
                self._retry.pop(sch.name, None)
                self._save(state)
                sent.append(sch.name)
                logger.info("[reports] sent %s", sch.name)
            else:
                self._retry[sch.name] = now + timedelta(seconds=RETRY_AFTER_S)
                logger.warning("[reports] %s not delivered (ntfy unset or failing); retry in 30 min", sch.name)
        return sent


def _default_send(message: str, **kw) -> bool:
    from core.notify import send_ntfy
    return send_ntfy(message, **kw)


def register_reports(scheduler, cfg: dict, goals_getter: Callable | None = None) -> ReportRunner | None:
    """Hook for main.py: one scheduler task that checks every minute. No-op when no report is configured."""
    if not schedules_from_config(cfg):
        return None
    ctx = {"goals": goals_getter} if goals_getter else {}
    runner = ReportRunner(cfg, ctx)
    scheduler.register("reports", runner.run_due, interval_sec=60.0)
    return runner


def preview(kind: str, cfg: dict, ctx: dict | None = None) -> str:
    """Text for /briefing or /report, built now with default sections even if unscheduled."""
    if kind == "report":
        sch = Schedule("weekly_report", "09:00", sections=("server", "usage", "problems"),
                       title="Aether weekly report")
    else:
        sch = Schedule("daily_briefing", "07:30", sections=("weather", "goals", "server"),
                       title="Aether daily briefing")
    return build_report(sch, cfg, ctx)
