"""core/behavior_rules.py - learn from corrections (roadmap #96).

When the user corrects Aether ("no, not that", "stop adding emojis", "from now on use PowerShell
commands"), Aether can turn it into a small standing rule, ask yes/no, and apply it to later
prompts. Rules live in their own file (behavior_rules.json in the data folder), not in the
personal-facts memory, and the user can list, edit, switch off and delete them (/learned, and the
dashboard memory page).

Not to be confused with core/rules.py (#66), which is trigger -> action automation.

Safety, because rule text goes into the system prompt:
  * nothing is saved without the user's yes (or an explicit /learned add);
  * a rule may not carry personal or sensitive facts, secrets, or an instruction to weaken
    approvals / safety checks (see rule_problem);
  * at most MAX_INJECT rules and MAX_BLOCK_CHARS characters reach any one prompt, so small models
    are not flooded; with no rules the prompt is byte-for-byte what it was before.

Config (config/api_keys.json):  "learn_corrections": true   (set false to stop proposing rules)
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import date

logger = logging.getLogger(__name__)

MAX_RULES = 40
MIN_RULE_CHARS = 8
MAX_RULE_CHARS = 160
MAX_INJECT = 3
MAX_BLOCK_CHARS = 420
PENDING_TTL_S = 600

_lock = threading.RLock()


class RuleError(ValueError):
    pass


# ── storage ─────────────────────────────────────────────────────────────────
def _path():
    from core.data_home import data_path
    return data_path("behavior_rules.json")


def _load() -> list[dict]:
    p = _path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as e:
        logger.warning("behavior_rules: unreadable %s (%s); treating as empty", p, e)
        return []
    items = data.get("rules") if isinstance(data, dict) else None
    out = []
    for r in items or []:
        if (isinstance(r, dict) and isinstance(r.get("id"), int) and isinstance(r.get("text"), str)
                and r["text"].strip()):
            out.append({"id": r["id"], "text": r["text"].strip(), "always": bool(r.get("always")),
                        "enabled": r.get("enabled", True) is not False, "created": str(r.get("created", ""))})
    return out


def _save(rules: list[dict]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps({"rules": rules}, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


# ── what may become a rule ──────────────────────────────────────────────────
_PERSONAL = re.compile(
    r"\bmy (?:name|address|phone|number|email|birthday|age|salary|income|wife|husband|partner|"
    r"girlfriend|boyfriend|son|daughter|kids?|child|children|mother|father|mom|dad|doctor|"
    r"medication|diagnosis|religion|password|passcode|pin|account|ssn)\b"
    r"|\bi (?:live|work|was born|am \d|have (?:cancer|diabetes|depression|anxiety|adhd))\b"
    r"|\bi'?m (?:\d{1,3}\b|from\b|a\b|an\b|married|single|diagnosed)"
    r"|\b(?:password|passcode|api[ _-]?key|secret|token|ssn|social security|credit card|iban|"
    r"routing number)\b"
    r"|[\w.+-]+@[\w-]+\.[\w.-]+"
    r"|\b\d{3}[\s.-]?\d{3}[\s.-]?\d{4}\b|\b\d{6,}\b",
    re.I)
# A rule must never read as an instruction to weaken the approval / safety layer.
_UNSAFE = re.compile(
    r"\b(?:approv\w*|confirm\w*|permission\w*|safety|safe mode|bypass\w*|disabl\w*|override\w*|"
    r"protected|sudo|admin\w*|(?:ignore|forget|disregard|override) (?:(?:the|all|any|your|my|previous|prior|"
    r"earlier|above|these|those|safety|system)\s+)*(?:rules?|instructions?|prompts?|limits?|"
    r"restrictions?|guidelines?)|system prompt|without asking|don'?t ask)\b",
    re.I)


def rule_problem(text: str) -> str:
    """Why this text may not be saved as a rule, or '' if it is fine."""
    t = (text or "").strip()
    if len(t) < MIN_RULE_CHARS:
        return "too short to be a useful rule"
    if len(t) > MAX_RULE_CHARS:
        return f"too long (max {MAX_RULE_CHARS} characters) - make it one short instruction"
    if "\n" in t or "\r" in t:
        return "must be a single line"
    if _PERSONAL.search(t):
        return "looks like it contains personal or sensitive information, which is not saved as a rule"
    if _UNSAFE.search(t):
        return "rules cannot change approvals or safety behaviour"
    try:
        from core import leak_guard
        if leak_guard.redact_text(t, [])[1]:
            return "looks like it contains a secret"
    except Exception:  # noqa: BLE001 - the regexes above still apply
        pass
    return ""


_WORD = re.compile(r"[a-z0-9']+")
_STOP = frozenset("""the a an and or but if then than that this these those to of in on at for with from by as is are
was were be been being do does did not no yes it its you your i me my we our they them their he she his her will would
can could should shall may might must just only also very more most some any all each every always never when where
what which who whom how why use using used please make keep give get say""".split())


def _tokens(text: str) -> set[str]:
    out = set()
    for w in _WORD.findall((text or "").lower()):
        w = w.strip("'")
        if len(w) >= 3 and w not in _STOP:
            out.add(w[:-1] if len(w) > 4 and w.endswith("s") else w)
    return out


def _similar(a: str, b: str) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return a.strip().lower() == b.strip().lower()
    return len(ta & tb) / len(ta | tb) >= 0.8


# ── the store: list / add / edit / remove ───────────────────────────────────
def list_rules() -> list[dict]:
    with _lock:
        return _load()


def add_rule(text: str, always: bool = False) -> dict:
    text = " ".join(str(text or "").split()).strip("\"'` ")
    why = rule_problem(text)
    if why:
        raise RuleError(why)
    with _lock:
        rules = _load()
        for r in rules:
            if _similar(r["text"], text):
                raise RuleError(f"already have a similar rule (#{r['id']}): {r['text']}")
        if len(rules) >= MAX_RULES:
            raise RuleError(f"rule limit reached ({MAX_RULES}); forget one first")
        rule = {"id": max((r["id"] for r in rules), default=0) + 1, "text": text,
                "always": bool(always), "enabled": True, "created": date.today().isoformat()}
        rules.append(rule)
        _save(rules)
        return rule


def edit_rule(rule_id: int, text: str) -> dict:
    text = " ".join(str(text or "").split()).strip("\"'` ")
    why = rule_problem(text)
    if why:
        raise RuleError(why)
    with _lock:
        rules = _load()
        for r in rules:
            if r["id"] == int(rule_id):
                r["text"] = text
                _save(rules)
                return r
    raise RuleError(f"no rule #{rule_id}")


def set_enabled(rule_id: int, enabled: bool) -> dict:
    with _lock:
        rules = _load()
        for r in rules:
            if r["id"] == int(rule_id):
                r["enabled"] = bool(enabled)
                _save(rules)
                return r
    raise RuleError(f"no rule #{rule_id}")


def remove_rule(rule_id: int) -> bool:
    with _lock:
        rules = _load()
        kept = [r for r in rules if r["id"] != int(rule_id)]
        if len(kept) == len(rules):
            return False
        _save(kept)
        return True


# ── applying rules to a prompt ──────────────────────────────────────────────
def relevant_rules(user_text: str, limit: int = MAX_INJECT) -> list[dict]:
    """Enabled rules for this message: 'always' (style) rules, then topical ones whose words overlap
    the message, best match first. Never more than `limit`, never over MAX_BLOCK_CHARS in total."""
    rules = [r for r in list_rules() if r["enabled"]]
    if not rules:
        return []
    want = _tokens(user_text)
    always = sorted((r for r in rules if r["always"]), key=lambda r: -r["id"])
    scored = []
    for r in rules:
        if r["always"]:
            continue
        hits = len(_tokens(r["text"]) & want)
        if hits:
            scored.append((hits, r["id"], r))
    scored.sort(key=lambda t: (-t[0], -t[1]))
    chosen, used = [], 0
    for r in always + [t[2] for t in scored]:
        if len(chosen) >= limit or used + len(r["text"]) > MAX_BLOCK_CHARS:
            continue
        chosen.append(r)
        used += len(r["text"])
    return chosen


def prompt_block(user_text: str) -> str:
    """The text to add to the system prompt for this message ('' when no rule applies)."""
    try:
        chosen = relevant_rules(user_text)
    except Exception as e:  # noqa: BLE001 - a broken rules file must never break a turn
        logger.debug("behavior_rules: skipped (%s)", e)
        return ""
    if not chosen:
        return ""
    return ("[LEARNED PREFERENCES - from the user's earlier corrections; follow them unless this "
            "message says otherwise]\n" + "\n".join(f"- {r['text']}" for r in chosen))


# ── spotting a correction ───────────────────────────────────────────────────
_NEGATION_START = re.compile(
    r"^\s*(?:no|nope|nah|wrong|incorrect|not that|that'?s (?:not|wrong|incorrect)|that (?:is|isn'?t) "
    r"(?:not |wrong|right)|actually,? (?:no|don'?t|stop))\b", re.I)
_STANDING = re.compile(
    r"\b(?:stop (?:doing|saying|using|adding|being|giving|asking|repeating|writing|calling)|"
    r"quit (?:doing|saying|using|adding)|"
    r"don'?t (?:ever|always|keep|say|use|add|give|call|write|do that|do this|include|explain)|"
    r"never (?:say|use|add|call|do|give|write|include|explain)|from now on|"
    r"always (?:use|say|reply|answer|give|keep|call|respond|write|include|explain|show)|"
    r"i (?:told|said to) you|i (?:didn'?t|did not) (?:ask|say|want|mean))\b", re.I)
_MAX_CORRECTION_CHARS = 240


def looks_like_correction(user_text: str, prior_reply: str = "") -> bool:
    """Cheap, conservative check on the user's message. A false positive only costs one model call
    and a yes/no question; a miss costs nothing, so this errs on the side of staying quiet."""
    t = (user_text or "").strip()
    if not t or len(t) > _MAX_CORRECTION_CHARS or t.startswith(("/", "[SYSTEM_ALERT]")) or t.endswith("?"):
        return False
    if _STANDING.search(t):
        return True
    return bool(prior_reply and prior_reply.strip() and _NEGATION_START.match(t))


def last_assistant_text(conversation: list[dict]) -> str:
    for m in reversed(conversation or []):
        if m.get("role") == "assistant" and isinstance(m.get("content"), str) and m["content"].strip() \
                and not m.get("tool_calls"):
            return m["content"].strip()
    return ""


# ── drafting a rule with the model ──────────────────────────────────────────
_DRAFT_SYSTEM = """You turn a user's correction of an AI assistant into ONE short standing instruction.

Return ONLY JSON: {"applies": true, "rule": "<instruction>", "always": false}

- "rule": imperative, one sentence, at most 20 words, general enough to help on FUTURE requests.
  Example: "Give Windows PowerShell commands, not bash."
- "applies": false if the message is a one-off (fixing a single fact or typo, a request about only
  this task) or is not really a lasting preference.
- "always": true for style, format or tone rules that apply to every reply (brevity, no emojis);
  false when tied to a topic (weather, code, files, music).
- NEVER put personal facts about the user (names, places, health, money, contacts, accounts) or
  secrets in the rule, and never mention approvals, confirmations or safety."""


def _default_llm(prompt: str, system: str) -> str:
    from core.llm_client import call_llm_text
    return call_llm_text(prompt, system=system, tier="fast", timeout=30)


def _parse_json(raw: str) -> dict | None:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def draft_rule(user_text: str, prior_reply: str = "", llm=None) -> dict | None:
    """{'text', 'always'} for a rule worth proposing, else None. Never raises."""
    try:
        prompt = (f"Assistant's previous reply:\n{(prior_reply or '(none)')[:300]}\n\n"
                  f"User's correction:\n{user_text.strip()[:_MAX_CORRECTION_CHARS]}")
        d = _parse_json((llm or _default_llm)(prompt, _DRAFT_SYSTEM))
        if not d or d.get("applies") is not True or not isinstance(d.get("rule"), str):
            return None
        text = " ".join(d["rule"].split()).strip("\"'` ")
        if rule_problem(text):
            return None
        if any(_similar(r["text"], text) for r in list_rules()):
            return None
        return {"text": text, "always": d.get("always") is True}
    except Exception as e:  # noqa: BLE001
        logger.debug("behavior_rules: draft failed (%s)", e)
        return None


# ── the yes/no question ─────────────────────────────────────────────────────
_pending: dict | None = None
_YES = re.compile(r"^\s*(?:yes|yeah|yep|yup|sure|ok|okay|please do|do it|save it|remember (?:it|that)|"
                  r"go ahead|y)\b[\s.!]*$", re.I)
_NO = re.compile(r"^\s*(?:no|nope|nah|don'?t|do not|skip|never ?mind|not really|n)\b[\s.!]*$", re.I)


def set_pending(rule: dict, now: float | None = None) -> None:
    global _pending
    with _lock:
        _pending = {"text": rule["text"], "always": bool(rule.get("always")),
                    "ts": time.monotonic() if now is None else now}


def clear_pending() -> None:
    global _pending
    with _lock:
        _pending = None


def has_pending() -> bool:
    with _lock:
        return _pending is not None


def question_for(rule: dict) -> str:
    return f"Want me to remember that as a rule? \"{rule['text']}\" (yes/no)"


def answer_pending(user_text: str, now: float | None = None) -> str | None:
    """Handle the user's next message after a proposal. Returns the reply to show if the message
    answered it (yes/no), or None if it was something else - in which case the proposal is dropped
    and the message is handled as a normal turn. The proposal never survives more than one message."""
    global _pending
    with _lock:
        p, _pending = _pending, None
    if p is None:
        return None
    if (time.monotonic() if now is None else now) - p["ts"] > PENDING_TTL_S:
        return None
    if _YES.match(user_text or ""):
        try:
            r = add_rule(p["text"], p["always"])
        except RuleError as e:
            return f"Couldn't save that rule: {e}."
        return f"Saved as rule #{r['id']}. /learned lists, edits or removes what I've learned."
    if _NO.match(user_text or ""):
        return "OK, not saved."
    return None


# ── /learned ────────────────────────────────────────────────────────────────
_USAGE = ("/learned  |  /learned add [always] <rule>  |  /learned edit <id> <rule>  |  "
          "/learned forget <id>  |  /learned off <id>  |  /learned on <id>")


def render() -> str:
    rules = list_rules()
    if not rules:
        return ("Nothing learned yet. When you correct me, I'll offer to save it as a rule. " + _USAGE)
    lines = ["What I've learned from your corrections:"]
    for r in rules:
        flags = ("always" if r["always"] else "topic") + ("" if r["enabled"] else ", OFF")
        lines.append(f"  #{r['id']} [{flags}] {r['text']}")
    lines.append(_USAGE)
    return "\n".join(lines)


def _id_of(arg: str) -> int:
    m = re.match(r"\s*#?(\d+)\b", arg or "")
    if not m:
        raise RuleError("give the rule number, e.g. /learned forget 3")
    return int(m.group(1))


def handle_command(text: str) -> str:
    rest = text.strip()[len("/learned"):].strip()
    verb, _, arg = rest.partition(" ")
    verb, arg = verb.lower(), arg.strip()
    try:
        if not verb:
            return render()
        if verb == "add" and arg:
            always = arg.lower().startswith("always ")
            r = add_rule(arg[7:] if always else arg, always)
            return f"Saved rule #{r['id']}: {r['text']}"
        if verb == "edit" and arg:
            rid = _id_of(arg)
            r = edit_rule(rid, re.sub(r"^\s*#?\d+\s*", "", arg, count=1))
            return f"Updated rule #{r['id']}: {r['text']}"
        if verb in ("forget", "delete", "remove", "rm") and arg:
            rid = _id_of(arg)
            return f"Forgot rule #{rid}." if remove_rule(rid) else f"No rule #{rid}."
        if verb in ("off", "on") and arg:
            r = set_enabled(_id_of(arg), verb == "on")
            return f"Rule #{r['id']} is {'on' if r['enabled'] else 'off'}."
    except RuleError as e:
        return f"Can't do that: {e}"
    return "Usage: " + _USAGE
