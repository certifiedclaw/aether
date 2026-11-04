"""core/rules.py - plain-language rules: "when X, do Y" (roadmap #66).

    /rule add every weekday at 7:30 send me the briefing
    /rule add when the disk is below 10% notify me
    /rule add every 2 hours remind me to stretch
    /rule add if ollama is down alert me
    /rules                    list        /rule off|on|remove <id>

Deliberately NOT free-form: the sentence is parsed by fixed patterns into a trigger and an
action, shown back to you ("when: ... / do: ..."), and anything it cannot parse is refused
with the supported phrasings rather than guessed at.

Triggers   time      "[every day|weekdays|monday] at 7:30[pm]"
           interval  "every 30 minutes" / "every 2 hours"
           condition "disk below 10%", "ram below 500 mb / 2 gb", "ollama|server is down"
Actions    notify    "notify / tell / alert / ping me", "remind me to ..."   (ntfy push)
           report    "send me the briefing" / "the weekly report"           (core/reports.py)
Rules cannot run tools, shell commands or prompts: the only things they can do are send you a
push and send you a report, so a rule can never act on your files or accounts.
Condition rules fire when the condition BECOMES true, re-arm when it clears, and wait at least
`rule_cooldown_s` (default 3600) before firing again. State lives in the per-user data dir.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime
from typing import Callable

logger = logging.getLogger("aether")

HELP = ("Rules understand: a time ('every day at 7:30', 'weekdays at 6pm', 'mondays at 9am'), "
        "an interval ('every 30 minutes', 'every 2 hours'), or a condition ('when the disk is "
        "below 10%', 'ram below 500 mb', 'if ollama is down'), plus an action: 'notify me', "
        "'remind me to <thing>', 'send me the briefing' or 'the weekly report'.")

_DAYNAMES = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_WORDS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4,
          "saturday": 5, "sunday": 6}


class RuleError(ValueError):
    pass


def _days(text: str) -> list[int]:
    if re.search(r"\bweekdays?\b", text):
        return [0, 1, 2, 3, 4]
    if re.search(r"\bweekends?\b", text):
        return [5, 6]
    found = sorted({i for w, i in _WORDS.items() if re.search(rf"\b{w}s?\b", text)})
    return found or list(range(7))


def parse(text: str) -> dict:
    """Sentence -> {"trigger": {...}, "action": {...}}. Raises RuleError with guidance."""
    raw = " ".join(str(text or "").split())
    t = raw.lower().strip(" .!")
    if not t:
        raise RuleError(HELP)

    # ── trigger ──
    trigger = None
    m = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", t)
    if m:
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        if not (0 <= h <= 23 and 0 <= mi <= 59):
            raise RuleError(f"'{m.group(0)}' is not a valid time.")
        if not ap and not m.group(2) and h > 12:
            pass
        trigger = {"type": "time", "at": f"{h:02d}:{mi:02d}", "days": _days(t)}
    if trigger is None:
        m = re.search(r"\bevery\s+(\d+|other)?\s*(minute|min|hour|hr)s?\b", t)
        if m:
            n = 1 if not m.group(1) or m.group(1) == "other" else int(m.group(1))
            secs = n * (60 if m.group(2).startswith("min") else 3600)
            if secs < 300:
                raise RuleError("Intervals shorter than 5 minutes are not allowed.")
            trigger = {"type": "interval", "every_s": secs}
    if trigger is None:
        m = re.search(r"\b(?:disk|storage|drive)\b.*?\b(?:below|under|less than)\s+(\d{1,2})\s*%", t)
        if m:
            trigger = {"type": "condition", "what": "disk_free_pct", "below": float(m.group(1))}
    if trigger is None:
        m = re.search(r"\b(?:ram|memory)\b.*?\b(?:below|under|less than)\s+(\d+(?:\.\d+)?)\s*(mb|gb)\b", t)
        if m:
            mb = float(m.group(1)) * (1024 if m.group(2) == "gb" else 1)
            trigger = {"type": "condition", "what": "ram_free_mb", "below": mb}
    if trigger is None:
        m = re.search(r"\b(ollama|server|aether|dashboard)\b.*?\b(?:is|goes|went|gets)?\s*down\b", t)
        if m:
            name = {"ollama": "ollama", "aether": "aether", "dashboard": "aether",
                    "server": "any"}[m.group(1)]
            trigger = {"type": "condition", "what": "probe_down", "name": name}
    if trigger is None:
        raise RuleError("I could not find when this should happen. " + HELP)

    # ── action ──
    m = re.search(r"\bremind me (?:to|that|about)\s+(.+)$", t)
    if m:
        action = {"type": "notify", "text": "Reminder: " + m.group(1).strip()}
    elif re.search(r"\bweekly (?:server )?report\b", t):
        action = {"type": "report", "kind": "report"}
    elif re.search(r"\b(?:daily |morning )?briefing\b", t):
        action = {"type": "report", "kind": "briefing"}
    elif re.search(r"\b(?:notify|tell|alert|ping|message|warn|let) (?:me|us)\b|\bremind me\b", t):
        action = {"type": "notify", "text": ""}
    else:
        raise RuleError("I could not find what to do. " + HELP)
    return {"trigger": trigger, "action": action}


def describe(rule: dict) -> str:
    tr, ac = rule["trigger"], rule["action"]
    if tr["type"] == "time":
        d = tr["days"]
        dd = "every day" if len(d) == 7 else ("weekdays" if d == [0, 1, 2, 3, 4] else
              ", ".join(list(_DAYNAMES)[i] for i in d))
        when = f"{dd} at {tr['at']}"
    elif tr["type"] == "interval":
        s = tr["every_s"]
        when = f"every {s // 3600} h" if s % 3600 == 0 else f"every {s // 60} min"
    else:
        w = tr["what"]
        when = {"disk_free_pct": f"disk free below {tr.get('below'):g}%",
                "ram_free_mb": f"RAM free below {tr.get('below'):g} MB",
                "probe_down": f"{tr.get('name')} is down"}[w]
    do = {"notify": "push: " + (ac.get("text") or "(condition message)"),
          "report": f"send the {ac.get('kind')}"}[ac["type"]]
    return f"when: {when}  /  do: {do}"


# ── condition probes (real values; injectable for tests) ────────────────────────────
def _read_condition(trigger: dict, cfg: dict) -> tuple[bool, str]:
    what = trigger["what"]
    if what == "disk_free_pct":
        import shutil
        u = shutil.disk_usage(cfg.get("supervisor_disk_path", "."))
        pct = 100.0 * u.free / u.total
        return pct < trigger["below"], f"disk is {pct:.1f}% free (rule: below {trigger['below']:g}%)"
    if what == "ram_free_mb":
        import psutil
        mb = psutil.virtual_memory().available / (1024 * 1024)
        return mb < trigger["below"], f"RAM free {mb:.0f} MB (rule: below {trigger['below']:g} MB)"
    from core import supervisor
    want = trigger.get("name", "any")
    for p in supervisor.build_from_config(cfg).probes:
        if p.name in ("disk", "ram") or (want != "any" and p.name != want):
            continue
        ok, detail = p.check()
        if not ok:
            return True, f"{p.name} is down: {detail}"
    return False, ""


class RuleEngine:
    def __init__(self, cfg: dict, *, path=None, clock: Callable[[], datetime] = datetime.now,
                 notify: Callable[..., bool] | None = None, report: Callable[[str], str] | None = None,
                 condition: Callable[[dict, dict], tuple[bool, str]] = _read_condition,
                 now_s: Callable[[], float] = time.time):
        self.cfg, self._clock, self._now_s, self._condition = cfg, clock, now_s, condition
        self._notify = notify or _default_notify
        self._report = report or _default_report
        self._path = path
        self._lock = threading.Lock()

    # storage
    def _file(self):
        if self._path:
            return self._path
        from core.data_home import data_path
        return data_path("rules.json")

    def load(self) -> list[dict]:
        try:
            data = json.loads(self._file().read_text(encoding="utf-8"))
            return [r for r in data if isinstance(r, dict) and "trigger" in r]
        except Exception:  # noqa: BLE001
            return []

    def _save(self, rules: list[dict]) -> None:
        p = self._file()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(rules, indent=1), encoding="utf-8")
        tmp.replace(p)

    # management (used by /rule)
    def add(self, sentence: str) -> dict:
        parsed = parse(sentence)
        with self._lock:
            rules = self.load()
            if len(rules) >= 30:
                raise RuleError("Too many rules (30). Remove one first.")
            rule = {"id": uuid.uuid4().hex[:6], "text": " ".join(sentence.split())[:200],
                    "enabled": True, "created": self._now_s(), "last_fired": 0.0,
                    "last_date": "", "armed": True, **parsed}
            rules.append(rule)
            self._save(rules)
        return rule

    def set_enabled(self, rid: str, on: bool) -> bool:
        with self._lock:
            rules, hit = self.load(), False
            for r in rules:
                if r["id"] == rid:
                    r["enabled"], hit = on, True
            if hit:
                self._save(rules)
        return hit

    def remove(self, rid: str) -> bool:
        with self._lock:
            rules = self.load()
            keep = [r for r in rules if r["id"] != rid]
            if len(keep) != len(rules):
                self._save(keep)
            return len(keep) != len(rules)

    def render(self) -> str:
        rules = self.load()
        if not rules:
            return "No rules yet. Try: /rule add every weekday at 7:30 send me the briefing"
        return "Rules:\n" + "\n".join(
            f"  {r['id']} [{'on ' if r.get('enabled', True) else 'off'}] {describe(r)}" for r in rules)

    # evaluation
    def tick(self) -> list[str]:
        """Check every enabled rule once; returns the ids that fired."""
        with self._lock:
            rules, fired = self.load(), []
            now, ts = self._clock(), self._now_s()
            cooldown = float(self.cfg.get("rule_cooldown_s", 3600))
            for r in rules:
                if not r.get("enabled", True):
                    continue
                try:
                    message = self._should_fire(r, now, ts, cooldown)
                except Exception as e:  # noqa: BLE001 - a broken probe must not stop other rules
                    logger.warning("[rules] %s check failed: %s", r.get("id"), e)
                    continue
                if message is None:
                    continue
                if self._execute(r, message):
                    r["last_fired"], r["last_date"] = ts, now.strftime("%Y-%m-%d")
                    if r["trigger"]["type"] == "condition":
                        r["armed"] = False
                    fired.append(r["id"])
            if rules:
                self._save(rules)
            return fired

    def _should_fire(self, r: dict, now: datetime, ts: float, cooldown: float) -> str | None:
        tr = r["trigger"]
        if tr["type"] == "time":
            from core.reports import Schedule, is_due
            sch = Schedule(r["id"], tr["at"], set(tr["days"]))
            return "" if is_due(sch, now, r.get("last_date") or None, catchup_hours=0.25) else None
        if tr["type"] == "interval":
            base = r.get("last_fired") or r.get("created", ts)
            return "" if ts - base >= tr["every_s"] else None
        hit, detail = self._condition(tr, self.cfg)
        if not hit:
            r["armed"] = True
            return None
        if not r.get("armed", True) or ts - r.get("last_fired", 0) < cooldown:
            return None
        return detail

    def _execute(self, r: dict, message: str) -> bool:
        ac = r["action"]
        if ac["type"] == "report":
            return bool(self._notify(self._report(ac["kind"]), title=f"Aether rule {r['id']}",
                                     priority="default"))
        text = ac.get("text") or message or r["text"]
        return bool(self._notify(text, title=f"Aether rule {r['id']}", priority="default"))


def _default_notify(message: str, **kw) -> bool:
    from core.notify import send_ntfy
    return send_ntfy(message, **kw)


def _default_report(kind: str) -> str:
    from core import reports
    from memory.config_manager import load_api_keys
    return reports.preview(kind, load_api_keys())


_engine: RuleEngine | None = None


def get_engine() -> RuleEngine:
    global _engine
    if _engine is None:
        from memory.config_manager import load_api_keys
        _engine = RuleEngine(load_api_keys())
    return _engine


def register_rules(scheduler) -> RuleEngine:
    """Hook for main.py: one scheduler task, checked every minute (cheap when there are no rules)."""
    eng = get_engine()
    scheduler.register("rules", eng.tick, interval_sec=60.0)
    return eng


def handle_command(text: str) -> str | None:
    """/rules and /rule add|on|off|remove ...  Returns the reply, or None if not a rule command."""
    t = text.strip()
    low = t.lower()
    if low == "/rules":
        return get_engine().render()
    if not (low == "/rule" or low.startswith("/rule ")):
        return None
    rest = t[5:].strip()
    verb, _, arg = rest.partition(" ")
    verb, arg = verb.lower(), arg.strip()
    eng = get_engine()
    try:
        if verb == "add":
            r = eng.add(arg)
            return f"Rule {r['id']} added - {describe(r)}"
        if verb in ("on", "off") and arg:
            return (f"Rule {arg} turned {verb}." if eng.set_enabled(arg, verb == "on")
                    else f"No rule {arg}.")
        if verb in ("remove", "delete", "rm") and arg:
            return f"Rule {arg} removed." if eng.remove(arg) else f"No rule {arg}."
    except RuleError as e:
        return f"Rule not added: {e}"
    return "Usage: /rule add <sentence> | /rule on|off|remove <id> | /rules\n" + HELP
