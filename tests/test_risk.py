"""core/risk.py: one declarative tier table, enforced at the entry function of every tool
so the voice dispatcher, the agent executor and plugins are all covered."""
import importlib
import inspect
import logging

import pytest

import core.risk as risk
import core.safety as safety
from core.risk import ASK, BLOCK, MONITOR, SAFE, TASK


@pytest.fixture
def ui(monkeypatch):
    """Record what the user was asked; answer per `ui.answer`."""
    class UI:
        answer = False
        asked = []
    UI.asked = []
    monkeypatch.setattr(safety, "confirm_action",
                        lambda op, detail="": UI.asked.append((op, detail)) or UI.answer)
    monkeypatch.setattr(risk, "_overrides", lambda: {})
    return UI


# ── precedence / fail-closed ────────────────────────────────────────────────
def test_strictest_ordering_and_invalid_values_fail_closed():
    assert risk.strictest(SAFE) == SAFE
    assert risk.strictest(SAFE, MONITOR) == MONITOR
    assert risk.strictest(MONITOR, ASK, SAFE) == ASK
    assert risk.strictest(MONITOR, TASK) == TASK and risk.strictest(TASK, ASK) == ASK
    assert risk.strictest(TASK, BLOCK) == BLOCK
    assert risk.strictest(SAFE, ASK, BLOCK, MONITOR) == BLOCK
    assert risk.strictest("garbage") == ASK                 # unknown value -> ask
    assert risk.strictest(SAFE, "typo") == ASK


def test_unknown_tool_and_unlisted_action_mean_ask():
    assert risk.tier_for("brand_new_tool", "x") == ASK
    assert risk.tier_for("file_controller", "format_c_drive") == ASK     # no "*" default
    assert risk.tier_for("code_helper", "write") == ASK
    assert risk.tier_for("send_message", "") == ASK
    assert risk.tier_for("  EXTRA_tool".strip(), " Run_Python ") in (ASK,)  # case/space-insensitive action
    assert risk.tier_for("extra_tool", " Run_Python ") == ASK


@pytest.mark.parametrize("tool,action,expected", [
    ("extra_tool", "run_python", ASK), ("extra_tool", "kill_process", ASK),
    ("extra_tool", "calculate", SAFE),
    ("file_processor", "run", ASK), ("file_processor", "describe the picture", MONITOR),
    ("memory_tool", "prune", ASK), ("memory_tool", "import", ASK), ("memory_tool", "stats", SAFE),
    ("send_message", "", ASK), ("file_controller", "delete", MONITOR),
    ("file_controller", "list", SAFE), ("computer_control", "click", TASK),
    ("computer_control", "screenshot", SAFE), ("browser_control", "fill_form", TASK),
    ("browser_control", "go_to", MONITOR), ("browser_control", "get_text", SAFE),
    ("computer_settings", "shutdown", ASK), ("desktop_control", "task", ASK),
    ("dev_agent", "", ASK), ("code_helper", "build", ASK),
    ("code_helper", "write", ASK), ("code_helper", "edit", ASK),
    ("code_helper", "optimize", ASK), ("code_helper", "screen_debug", ASK),
    ("extra_tool", "unzip", ASK),
])
def test_table_values(tool, action, expected):
    assert risk.tier_for(tool, action) == expected


# ── overrides can only tighten (the model can write files) ──────────────────
def test_overrides_tighten_but_never_loosen(monkeypatch):
    monkeypatch.setattr(risk, "_overrides", lambda: {"computer_control.click": "ask", "web_search": "block",
                                                     "send_message": "safe", "extra_tool.run_python": "monitor"})
    assert risk.tier_for("computer_control", "click") == ASK            # tightened
    assert risk.tier_for("computer_control", "type") == TASK            # untouched
    assert risk.tier_for("web_search", "") == BLOCK
    assert risk.tier_for("send_message", "") == ASK                     # cannot loosen
    assert risk.tier_for("extra_tool", "run_python") == ASK             # cannot loosen
    monkeypatch.setattr(risk, "_overrides", lambda: {"web_search": "bogus"})
    assert risk.tier_for("web_search", "") == ASK                       # invalid override -> ask


def test_block_in_the_table_cannot_be_resurrected_by_an_override(monkeypatch):
    monkeypatch.setitem(risk.RISK_TABLE, "blocked_tool", BLOCK)
    monkeypatch.setattr(risk, "_overrides", lambda: {"blocked_tool": "safe"})
    assert risk.tier_for("blocked_tool", "") == BLOCK


# ── the decorator ───────────────────────────────────────────────────────────
def _probe(tool):
    calls = []

    @risk.gated(tool)
    def fn(parameters, response=None, player=None):
        calls.append(parameters)
        return "ran"
    return fn, calls


def test_safe_runs_monitor_logs_names_not_values(ui, caplog):
    fn, calls = _probe("web_search")
    assert fn({"query": "x"}) == "ran" and ui.asked == []
    fn, calls = _probe("open_app")
    with caplog.at_level(logging.INFO, logger="aether"):
        assert fn({"action": "x", "app_name": "my secret password 1234"}) == "ran"
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "open_app" in text and "app_name" in text
    assert "password" not in text and "1234" not in text            # names only, never values


def test_ask_is_denied_without_approval_and_nothing_runs(ui):
    fn, calls = _probe("send_message")
    ui.answer = False
    out = fn({"receiver": "mum", "message_text": "hi"})
    assert out.startswith("Cancelled") and calls == []
    ui.answer = True
    assert fn({"receiver": "mum", "message_text": "hi"}) == "ran" and len(calls) == 1


def test_block_never_runs_and_never_asks(ui, monkeypatch):
    monkeypatch.setitem(risk.RISK_TABLE, "nuke", BLOCK)
    fn, calls = _probe("nuke")
    assert fn({"action": "x"}).startswith("Blocked") and calls == [] and ui.asked == []


def test_dialog_masks_secretish_keys_and_truncates(ui):
    fn, _ = _probe("send_message")
    fn({"receiver": "a", "password": "hunter2", "api_key": "ABC", "message_text": "z" * 1500})
    detail = ui.asked[0][1]
    assert "hunter2" not in detail and "ABC" not in detail and "(hidden)" in detail
    assert "…" in detail and len(detail) < 1100


def test_decorator_finds_params_in_methods_kwargs_and_preserves_metadata(ui):
    ui.answer = False

    class D:
        @risk.gated("memory_tool")
        def handler(self, args):
            """doc"""
            return "ran"
    assert D().handler({"action": "prune"}).startswith("Cancelled")      # (self, args) form
    assert D().handler({"action": "stats"}) == "ran"
    fn, calls = _probe("send_message")
    assert fn(parameters={"x": 1}).startswith("Cancelled")               # keyword form
    assert D.handler.__doc__ == "doc" and D.handler.__name__ == "handler"
    assert D.handler.__risk_tool__ == "memory_tool"


# ── coverage: no dead config, no ungated declared tool ──────────────────────
def _declared():
    import core.tool_declarations as td
    return [d["name"] for d in td.TOOL_DECLARATIONS]


def test_every_declared_tool_is_classified():
    missing = [t for t in _declared() if t not in risk.RISK_TABLE]
    assert missing == [], f"declared but not in RISK_TABLE (unknown means ask): {missing}"


def test_every_table_entry_is_a_real_tool():
    extra = [t for t in risk.RISK_TABLE if t not in _declared()]
    assert extra == [], f"dead RISK_TABLE entries: {extra}"


def _has_gate(tool):
    if tool in risk.ENTRY_POINTS:
        mod, name = risk.ENTRY_POINTS[tool]
        return getattr(importlib.import_module(mod), name, None)
    if tool in risk.DISPATCH_ENTRIES:
        mod, cls, name = risk.DISPATCH_ENTRIES[tool]
        return getattr(getattr(importlib.import_module(mod), cls), name, None)
    return None


def _tiers(entry):
    return {entry} if isinstance(entry, str) else set(entry.values())


def test_every_tool_with_a_task_ask_or_block_tier_is_actually_enforced():
    """The bug this replaces: names in a confirm list that no code ever consulted."""
    problems = []
    for tool, entry in risk.RISK_TABLE.items():
        if not (_tiers(entry) & {TASK, ASK, BLOCK}):
            continue
        if tool in risk.SELF_GATED:
            continue
        fn = _has_gate(tool)
        if fn is None or getattr(fn, "__risk_tool__", None) != tool:
            problems.append(tool)
    assert problems == [], f"ask/block tier with no enforcement: {problems}"


def test_every_wrapped_entry_point_exists_and_is_wrapped():
    for tool in list(risk.ENTRY_POINTS) + list(risk.DISPATCH_ENTRIES):
        fn = _has_gate(tool)
        assert fn is not None and getattr(fn, "__risk_tool__", None) == tool, tool


def test_self_gated_tools_really_ask_for_themselves():
    src = {"computer_settings": "actions.computer_settings", "desktop_control": "actions.desktop",
           "code_helper": "actions.code_helper", "dev_agent": "actions.dev_agent"}
    for tool, mod in src.items():
        text = inspect.getsource(importlib.import_module(mod))
        assert ("confirm_action" in text or "_confirm_" in text or "_approve_execution" in text), tool
    import core.tool_dispatch as td
    assert "requires_confirmation" in inspect.getsource(td)          # osint_lookup


# ── end to end through the REAL functions (no stubs on the code being gated) ─
def test_file_processor_run_does_not_execute_without_approval(ui, tmp_path):
    import actions.file_processor as fp
    marker = tmp_path / "RAN.txt"
    script = tmp_path / "evil.py"
    script.write_text(f"open(r'{marker}','w').write('x')")
    ui.answer = False
    out = fp.file_processor({"file_path": str(script), "action": "run"})
    assert out.startswith("Cancelled") and not marker.exists()
    ui.answer = True
    fp.file_processor({"file_path": str(script), "action": "run"})
    assert marker.exists()                                               # runs once approved
    assert "evil.py" in ui.asked[-1][1]                                  # dialog names the file


def test_extra_tool_run_python_does_not_execute_without_approval(ui, tmp_path):
    import actions.extra_tools as et
    marker = tmp_path / "RAN2.txt"
    code = f"open(r'{marker}','w').write('x')"
    ui.answer = False
    out = et.run_extra_tool("run_python", {"action": "run_python", "code": code})
    assert out.startswith("Cancelled") and not marker.exists()
    ui.answer = True
    et.run_extra_tool("run_python", {"action": "run_python", "code": code})
    assert marker.exists()


def test_oversized_python_is_rejected_before_approval_or_execution(ui, monkeypatch):
    import actions.extra_tools as et
    monkeypatch.setattr(et, "run_python", lambda *a, **k: pytest.fail("must not execute"))
    out = et.run_extra_tool("run_python", {"action": "run_python", "code": "x" * 4001})
    assert out == "Code too long or invalid (maximum 4000 characters)."
    assert ui.asked == []


def test_extra_tool_harmless_actions_do_not_ask(ui):
    import actions.extra_tools as et
    assert "4" in et.run_extra_tool("calculate", {"action": "calculate", "expression": "2+2"})
    assert ui.asked == []


def test_send_message_denied_never_reaches_the_sender(ui, monkeypatch):
    import actions.send_message as sm
    ui.answer = False
    out = sm.send_message({"receiver": "mum", "message_text": "hi", "platform": "whatsapp"})
    assert out.startswith("Cancelled")
    assert "send_message" in ui.asked[0][0] and "mum" in ui.asked[0][1]


def test_memory_tool_prune_and_import_are_gated_in_the_dispatcher(ui):
    import core.tool_dispatch as td
    ui.answer = False

    class Fake(td.ToolDispatchMixin):
        pass
    for action in ("prune", "import"):
        assert Fake()._tool_memory_tool({"action": action}).startswith("Cancelled")


def test_agent_executor_path_is_gated_too(ui, tmp_path):
    """The executor imports actions directly and bypasses the dispatcher."""
    from agent import executor
    ui.answer = False
    out = executor._call_tool("extra_tool", {"action": "run_python", "code": "print(1)"}, None)
    assert "Cancelled" in out


# ── the table must cover every action a tool really implements ───────────────
def _implemented_actions(module, fn_name):
    import re
    fn = getattr(importlib.import_module(module), fn_name)
    src = inspect.getsource(getattr(fn, "__wrapped__", fn))
    acts = set(re.findall(r"""\b(?:a|action|act)\s*==\s*["']([a-z_]+)["']""", src))
    for grp in re.findall(r"""\b(?:a|action|act)\s+in\s*[\(\[]([^\)\]]+)[\)\]]""", src):
        acts |= set(re.findall(r"""["']([a-z_]+)["']""", grp))
    return acts


@pytest.mark.parametrize("tool", ["extra_tool", "file_controller", "computer_control", "browser_control"])
def test_table_covers_every_action_the_tool_implements(tool):
    """Found 10 unclassified extra_tool actions the first time this ran. A new action
    added to a tool must be classified, or this fails (and at runtime it would ask)."""
    mod, fn = risk.ENTRY_POINTS[tool]
    acts = _implemented_actions(mod, fn)
    assert len(acts) >= 10, "extraction regex stopped matching: fix the test, not the table"
    missing = sorted(a for a in acts if a not in risk.RISK_TABLE[tool])
    assert missing == [], f"{tool}: implemented but unclassified actions: {missing}"


def test_game_updater_shutdown_when_done_asks(ui):
    """game_updater is MONITOR, but shutdown_when_done powers the PC off later."""
    ui.answer = False
    out = risk.check("game_updater", "download", {"shutdown_when_done": True})
    assert out and out.startswith("Cancelled") and len(ui.asked) == 1
    ui.asked.clear()
    assert risk.check("game_updater", "download", {"shutdown_when_done": "false"}) is None
    assert risk.check("game_updater", "download", {}) is None
    assert ui.asked == []
