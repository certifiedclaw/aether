"""core/risk.py -- one declarative table of what each tool/action is allowed to do.

WHY
    Gates used to be scattered: a `confirmed=yes` parameter the model could fill in,
    a `requires_confirmation()` list whose entries no code consulted, and tools that
    executed model-written code with no gate at all. This module is the single place
    that says, per tool and action, whether it runs freely, is logged, needs the USER
    to approve, or is never allowed.

TIERS (strictest wins when several apply: block > ask > task > monitor > safe)
    safe     runs freely
    monitor  runs, and is logged (tool.action + parameter NAMES only, never values)
    task     ask ONCE per user request, quoting the user's own words; the approval covers
             that tool for THIS request only (an action cap and a time limit apply). For
             tools that drive the mouse/keyboard/browser, where a dialog per click would
             make the assistant unusable. A refusal sticks for the rest of the request.
    ask      needs the user to approve it in the UI (core.safety.confirm_action) every
             time; never covered by a task grant. Denied automatically if there is no UI,
             a timeout, or any error
    block    never runs

UNKNOWN MEANS ASK
    A tool that is not in RISK_TABLE, or an action a tool's entry does not list and has
    no "*" default for, is treated as `ask`. Forgetting to classify something fails
    closed, and tests/test_risk.py fails if a declared tool is missing from the table.

WHERE IT IS ENFORCED
    `@gated("tool")` wraps the ENTRY FUNCTION of each tool, so every path that reaches
    it is covered: the voice dispatcher, the agent executor (which imports actions
    directly and bypasses the dispatcher) and plugins. Tools that need richer detail
    in their dialog than a parameter dump (the plan a model just made, the code it
    wrote) enforce inline instead and are listed in SELF_GATED; the tests check they
    really do.

OVERRIDES CAN ONLY TIGHTEN
    config "risk_overrides": {"tool.action": "ask"} is combined with the table using
    strictest(). It can never loosen a tier: the model can write files, so a loosenable
    setting in a writable config would let it switch its own gates off.
"""
from __future__ import annotations

import functools
import logging
import re
import threading
import time

logger = logging.getLogger("aether")

SAFE, MONITOR, TASK, ASK, BLOCK = "safe", "monitor", "task", "ask", "block"
_ORDER = {SAFE: 0, MONITOR: 1, TASK: 2, ASK: 3, BLOCK: 4}
TIERS = tuple(_ORDER)


def strictest(*tiers: str) -> str:
    """The most restrictive valid tier; an invalid value counts as `ask` (fail closed)."""
    worst = SAFE
    for t in tiers:
        t = t if t in _ORDER else ASK
        if _ORDER[t] > _ORDER[worst]:
            worst = t
    return worst


# tool -> tier, or tool -> {action: tier, "*": default for unlisted actions}
RISK_TABLE: dict[str, str | dict[str, str]] = {
    # ── read-only / harmless ────────────────────────────────────────────────
    "web_search":      SAFE,
    "weather_report":  SAFE,
    "reminder":        SAFE,
    "youtube_video":   SAFE,
    "flight_finder":   SAFE,
    "undo":            SAFE,
    "save_memory":     SAFE,
    "pdf_tool":        SAFE,
    "rag_query":       SAFE,
    "diagnostics":     SAFE,
    "crash_report":    SAFE,
    # agent_task only QUEUES a plan; every step it runs goes through the gated leaf
    # actions below, so asking here as well would just double the dialogs.
    "agent_task":      SAFE,

    # ── allowed but recorded ────────────────────────────────────────────────
    "open_app":        MONITOR,
    "screen_process":  MONITOR,      # a screenshot is sent to a model
    "shutdown_aether": MONITOR,
    "game_updater":    {"list": SAFE, "download_status": SAFE, "schedule_status": SAFE,
                        "cancel_schedule": MONITOR, "*": MONITOR},

    # ── needs the user ──────────────────────────────────────────────────────
    "send_message":    ASK,          # leaves the machine and cannot be taken back

    # per-action
    "file_controller": {
        "": SAFE, "list": SAFE, "find": SAFE, "info": SAFE, "read": SAFE,
        "disk_usage": SAFE, "largest": SAFE,
        # mutating, but reversible through the undo tool / Recycle Bin
        "create_file": MONITOR, "create_folder": MONITOR, "write": MONITOR,
        "copy": MONITOR, "move": MONITOR, "rename": MONITOR,
        "delete": MONITOR, "organize_desktop": MONITOR,
    },
    "computer_control": {
        "": SAFE, "screenshot": SAFE, "screen_find": SAFE, "wait": SAFE, "focus_window": SAFE,
        # raw input: this tool can drive any program, so the user approves it once per
        # request (TASK) instead of once per click
        "click": TASK, "left_click": TASK, "double_click": TASK,
        "right_click": TASK, "screen_click": TASK, "move": TASK,
        "drag": TASK, "scroll": TASK, "type": TASK, "smart_type": TASK,
        "press": TASK, "hotkey": TASK, "paste": TASK, "copy": TASK,
        "clear_field": TASK, "user_data": TASK, "random_data": TASK,
    },
    "browser_control": {
        "": SAFE, "get_text": SAFE, "get_url": SAFE, "list_browsers": SAFE, "screenshot": SAFE,
        "search": SAFE, "back": SAFE, "forward": SAFE, "reload": SAFE, "scroll": SAFE,
        "go_to": MONITOR, "new_tab": MONITOR, "switch": MONITOR, "close": MONITOR,
        "close_tab": MONITOR, "close_all": MONITOR,
        # acting on a page (clicking, typing, submitting forms): once per request
        "click": TASK, "smart_click": TASK, "type": TASK, "smart_type": TASK,
        "press": TASK, "fill_form": TASK,
    },
    "file_processor": {
        # "run" executes a .py file: the same hole code_helper had. Every other action
        # is analysis/conversion (and any unrecognised action text is treated by the
        # tool as a free-form instruction for the model), so it is only recorded.
        "run": ASK, "*": MONITOR,
    },
    "memory_tool": {
        "": SAFE, "stats": SAFE, "backup": MONITOR, "export": MONITOR,
        "import": ASK, "prune": ASK,      # both rewrite or delete your memory
    },
    "extra_tool": {
        "": SAFE,
        "run_python": ASK,                # arbitrary model-written code
        "kill_process": ASK, "batch_rename": ASK,
        "delete_note": MONITOR, "type_text": MONITOR, "unzip": MONITOR, "zip": MONITOR,
        "play_file": MONITOR, "set_volume": MONITOR, "focus_window": MONITOR,
        "type": MONITOR, "auto_type": MONITOR,       # keystrokes into the focused window
        "clipboard_read": MONITOR,                   # clipboard can hold secrets
        "clipboard_write": MONITOR, "screenshot_region": MONITOR,
        "qr": MONITOR, "qr_code": MONITOR,           # writes a file
        "translate": MONITOR,                        # text may be sent to a translation service
        "wifi": SAFE, "wifi_list": SAFE,             # lists nearby networks only
        "save_note": MONITOR, "remind_me": MONITOR, "cancel_reminder": MONITOR,
        "timer": MONITOR,
        "wikipedia": SAFE, "define": SAFE, "battery": SAFE, "random_fact": SAFE,
        "datetime": SAFE, "list_notes": SAFE, "read_note": SAFE, "list_reminders": SAFE,
        "password": SAFE, "convert_units": SAFE, "hash_file": SAFE, "diff": SAFE,
        "large_files": SAFE, "word_count": SAFE, "list_processes": SAFE,
        "list_windows": SAFE, "calculate": SAFE, "format_json": SAFE, "lint": SAFE,
        "ping": SAFE, "check_port": SAFE, "local_ip": SAFE,
    },

    # ── enforced INSIDE the function (richer dialog); see SELF_GATED ─────────
    "computer_settings": {"shutdown": ASK, "restart": ASK, "*": SAFE},
    "desktop_control":   {"task": ASK, "*": SAFE},
    "code_helper":       {"run": ASK, "build": ASK, "*": SAFE},
    "dev_agent":         ASK,
    "osint_lookup":      {"port_scan": ASK, "breach_check": ASK, "*": MONITOR},
}

# Tools whose function asks for itself, with a dialog that shows the actual plan/code.
SELF_GATED = frozenset({
    "computer_settings", "desktop_control", "code_helper", "dev_agent", "osint_lookup",
})

# tool -> (module, function) for the coverage test: every tool with an ask/block tier
# must be SELF_GATED or have its entry point wrapped by @gated.
ENTRY_POINTS = {
    "send_message":    ("actions.send_message", "send_message"),
    "file_controller": ("actions.file_controller", "file_controller"),
    "computer_control": ("actions.computer_control", "computer_control"),
    "browser_control": ("actions.browser_control", "browser_control"),
    "file_processor":  ("actions.file_processor", "file_processor"),
    "game_updater":    ("actions.game_updater", "game_updater"),
    "open_app":        ("actions.open_app", "open_app"),
    "screen_process":  ("actions.screen_processor", "screen_process"),
    "extra_tool":      ("actions.extra_tools", "run_extra_tool"),
}


# Tools that exist only in the dispatcher (the executor cannot reach them): the gate
# is on the dispatch method. tool -> (module, class, method)
DISPATCH_ENTRIES = {
    "memory_tool": ("core.tool_dispatch", "ToolDispatchMixin", "_tool_memory_tool"),
}


def _overrides() -> dict:
    """User-configured extra restrictions (tighten-only); {} if unavailable."""
    try:
        from core.llm_client import _load_config
        v = _load_config().get("risk_overrides")
        return v if isinstance(v, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def tier_for(tool: str, action: str = "") -> str:
    """The effective tier for tool/action. Unknown tool or unlisted action -> ask."""
    entry = RISK_TABLE.get(tool)
    action = (action or "").strip().lower()
    if entry is None:
        base = ASK
    elif isinstance(entry, str):
        base = entry
    else:
        base = entry.get(action, entry.get("*", ASK))
    ov = _overrides()
    extra = [ov.get(f"{tool}.{action}"), ov.get(tool)]
    return strictest(base, *[t for t in extra if t is not None])


_SECRETISH = re.compile(r"pass(word|phrase)?|secret|token|api_?key|credential|^pin$", re.I)


def _detail(tool: str, action: str, params: dict) -> str:
    lines = [f"Tool: {tool}" + (f"  Action: {action}" if action else "")]
    for k, v in (params or {}).items():
        if k == "action":
            continue
        shown = "(hidden)" if _SECRETISH.search(str(k)) else str(v)
        if len(shown) > 600:
            shown = shown[:600] + "…"
        lines.append(f"  {k}: {shown}")
    return "\n".join(lines[:12])


def _find_params(args: tuple, kwargs: dict) -> dict:
    """The tool-parameters dict: `parameters`/`args`/`params` kwarg, else first dict arg."""
    for name in ("parameters", "args", "params"):
        if isinstance(kwargs.get(name), dict):
            return kwargs[name]
    for a in args:
        if isinstance(a, dict):
            return a
    return {}


# ── TASK tier: one approval per user request ─────────────────────────────────
_STATE_LOCK = threading.Lock()
_TURN = 0                      # increments at every new user request
_TURN_TEXT = ""                # what the user actually said, shown in the dialog
_GRANTS: dict[str, dict] = {}  # tool -> {"turn", "expires", "left"}
_DENIED: dict[str, int] = {}   # tool -> turn in which the user said no
_TOOL_LOCKS: dict[str, threading.Lock] = {}

_TASK_WHAT = {"computer_control": "mouse and keyboard", "browser_control": "web browser"}


def begin_turn(user_text: str = "") -> None:
    """Call when a new user request starts. Ends every earlier approval and refusal, so
    permission given for one request can never leak into the next."""
    global _TURN, _TURN_TEXT
    one_line = " ".join(str(user_text or "").split())
    with _STATE_LOCK:
        _TURN += 1
        _TURN_TEXT = one_line[:200]
        _GRANTS.clear()
        _DENIED.clear()


def reset_state() -> None:
    """Forget all grants/refusals (tests, and a clean slate on demand)."""
    global _TURN, _TURN_TEXT
    with _STATE_LOCK:
        _TURN, _TURN_TEXT = 0, ""
        _GRANTS.clear()
        _DENIED.clear()


def _cfg_int(key: str, default: int, lo: int, hi: int) -> int:
    try:
        from core.llm_client import _load_config
        v = int(_load_config().get(key, default))
    except Exception:  # noqa: BLE001
        v = default
    return max(lo, min(v, hi))


def task_ttl() -> int:
    """Seconds one approval stays valid (config "risk_task_ttl", 10-900)."""
    return _cfg_int("risk_task_ttl", 120, 10, 900)


def task_max_actions() -> int:
    """Actions one approval covers (config "risk_task_max_actions", 1-500)."""
    return _cfg_int("risk_task_max_actions", 60, 1, 500)


def _tool_lock(tool: str) -> threading.Lock:
    with _STATE_LOCK:
        return _TOOL_LOCKS.setdefault(tool, threading.Lock())


def _check_task(tool: str, action: str, params: dict, label: str) -> str | None:
    # one lock PER TOOL, held across the dialog: concurrent calls wait for the answer
    # instead of each popping their own dialog
    with _tool_lock(tool):
        with _STATE_LOCK:
            turn = _TURN
            if _DENIED.get(tool) == turn:
                return f"Cancelled: you did not approve {tool} for this request."
            g = _GRANTS.get(tool)
            if g and g["turn"] == turn and g["expires"] > time.monotonic() and g["left"] > 0:
                g["left"] -= 1
                covered, said = True, ""
            else:
                covered, said = False, _TURN_TEXT
        if covered:
            logger.info("RISK task-grant %s params=%s", label, sorted((params or {}).keys()))
            return None

        cap, ttl = task_max_actions(), task_ttl()
        what = _TASK_WHAT.get(tool, f"the {tool} tool")
        asked = f"\u201c{said}\u201d" if said else "(the request text is not available)"
        detail = (
            f"You asked: {asked}\n\n"
            f"Aether wants to control your {what} to carry that out. If you approve, it can "
            f"keep doing so for THIS request only (up to {cap} actions, {ttl} seconds).\n\n"
            f"First action:\n{_detail(tool, action, params)}")
        from core.safety import confirm_action
        approved = confirm_action(f"{tool}: control for this request", detail)

        with _STATE_LOCK:
            if _TURN != turn:
                return "Cancelled: a new request started while waiting, so nothing was done."
            if approved:
                _GRANTS[tool] = {"turn": turn, "expires": time.monotonic() + ttl, "left": cap - 1}
                return None
            _DENIED[tool] = turn
        return f"Cancelled: you did not approve {label}, so nothing was done."


def check(tool: str, action: str, params: dict) -> str | None:
    """Apply the tier. Returns None if the call may proceed, else the message to return."""
    tier = tier_for(tool, action)
    label = f"{tool}.{action}" if action else tool
    if tier == SAFE:
        return None
    if tier == MONITOR:
        logger.info("RISK monitor %s params=%s", label, sorted((params or {}).keys()))
        return None
    if tier == BLOCK:
        return f"Blocked: {label} is not allowed."
    if tier == TASK:
        return _check_task(tool, action, params or {}, label)
    from core.safety import confirm_action
    if confirm_action(f"{label}", _detail(tool, action, params)):
        return None
    return f"Cancelled: you did not approve {label}, so nothing was done."


def gated(tool: str, extract=None):
    """Decorator: enforce the tier for `tool` before the wrapped function runs.

    `extract(args, kwargs) -> (action, params)` overrides how the action and parameters
    are found; the default reads the first dict argument and its "action" key.
    """
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if extract is not None:
                action, params = extract(args, kwargs)
            else:
                params = _find_params(args, kwargs)
                action = str(params.get("action", "") or "")
            blocked = check(tool, str(action or "").strip().lower(), params or {})
            if blocked is not None:
                return blocked
            return fn(*args, **kwargs)
        wrapper.__risk_tool__ = tool
        return wrapper
    return deco


def extra_tool_extract(args: tuple, kwargs: dict):
    """For run_extra_tool(action: str, params: dict)."""
    action = kwargs.get("action", args[0] if args else "")
    params = kwargs.get("params", args[1] if len(args) > 1 else {})
    return action, (params if isinstance(params, dict) else {})
