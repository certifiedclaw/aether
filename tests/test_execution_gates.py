"""Tools that execute model-written or model-chosen code must be approved by the USER
first, on every path that reaches them (voice tool, agent executor, plugin)."""
import pytest
from pathlib import Path
from types import SimpleNamespace

import agent.executor as agent_executor
import actions.code_helper as ch
import actions.dev_agent as da
import core.safety as safety


# ── the shared fail-closed helper ────────────────────────────────────────────
def test_confirm_action_is_fail_closed(monkeypatch):
    class UI:
        def __init__(self, answer): self.answer, self.seen = answer, []
        def request_confirmation(self, op, detail=""):
            self.seen.append((op, detail)); return self.answer
    ui = UI(True)
    monkeypatch.setattr(safety, "get_safety", lambda config=None: ui)
    assert safety.confirm_action("do thing", "details") is True and ui.seen == [("do thing", "details")]
    monkeypatch.setattr(safety, "get_safety", lambda config=None: UI(False))
    assert safety.confirm_action("x") is False

    def boom(config=None): raise RuntimeError("no config")
    monkeypatch.setattr(safety, "get_safety", boom)
    assert safety.confirm_action("x") is False


def test_confirm_action_denies_with_no_ui_callback_registered():
    layer = safety.SafetyLayer({}) if hasattr(safety, "SafetyLayer") else None
    if layer is None:
        pytest.skip("SafetyLayer not importable in this environment")
    assert layer.request_confirmation("anything", "d") is False


@pytest.mark.parametrize("tool", ["generated_code", "unknown_tool"])
def test_agent_generated_code_requires_approval_before_temp_file_or_execution(
        monkeypatch, tool):
    code = "print('review this exact code')"
    seen = []
    temp_calls = []
    monkeypatch.setattr(agent_executor, "call_llm_text", lambda *a, **k: code)
    monkeypatch.setattr(safety, "confirm_action",
                        lambda op, detail="": seen.append((op, detail)) or False)
    original_temp = agent_executor.tempfile.NamedTemporaryFile
    monkeypatch.setattr(agent_executor.tempfile, "NamedTemporaryFile",
                        lambda *a, **k: temp_calls.append(True) or original_temp(*a, **k))
    monkeypatch.setattr(agent_executor.subprocess, "run",
                        lambda *a, **k: pytest.fail("generated code must not run"))

    with pytest.raises(agent_executor.GeneratedCodeNotRun, match="Cancelled"):
        agent_executor._call_tool(tool, {"description": "inspect files"}, None)

    assert len(seen) == 1
    assert seen[0][0] == "run model-generated Python code"
    assert "inspect files" in seen[0][1]
    assert code in seen[0][1]
    assert temp_calls == []


def test_agent_generated_code_runs_only_after_approval_of_exact_code(monkeypatch):
    code = "print('approved code')"
    seen = []
    executed = []
    monkeypatch.setattr(agent_executor, "call_llm_text", lambda *a, **k: code)
    monkeypatch.setattr(safety, "confirm_action",
                        lambda op, detail="": seen.append((op, detail)) or True)

    def run(command, **kwargs):
        executed.append(Path(command[1]).read_text(encoding="utf-8"))
        return SimpleNamespace(returncode=0, stdout="completed", stderr="")

    monkeypatch.setattr(agent_executor.subprocess, "run", run)
    result = agent_executor._call_tool("generated_code", {"description": "do task"}, None)

    assert result == "completed"
    assert code in seen[0][1]
    assert executed == [code]


def test_agent_generated_code_over_review_limit_is_never_approved_or_run(monkeypatch):
    seen = []
    monkeypatch.setattr(agent_executor, "call_llm_text",
                        lambda *a, **k: "x" * (agent_executor._MAX_GENERATED_CODE + 1))
    monkeypatch.setattr(safety, "confirm_action",
                        lambda *a, **k: seen.append(True) or True)
    monkeypatch.setattr(agent_executor.subprocess, "run",
                        lambda *a, **k: pytest.fail("oversized code must not run"))

    with pytest.raises(agent_executor.GeneratedCodeNotRun, match="review limit"):
        agent_executor._call_tool("generated_code", {"description": "do task"}, None)
    assert seen == []


def test_agent_executor_reports_generated_code_denial_as_cancelled(monkeypatch):
    monkeypatch.setattr(agent_executor, "create_plan", lambda goal: {
        "steps": [{"step": 1, "tool": "generated_code", "description": "run code",
                   "parameters": {"description": "do task"}}]})
    monkeypatch.setattr(agent_executor, "call_llm_text", lambda *a, **k: "print('no')")
    monkeypatch.setattr(safety, "confirm_action", lambda *a, **k: False)
    monkeypatch.setattr(agent_executor.AgentExecutor, "_summarize",
                        lambda *a, **k: pytest.fail("denied tasks must not be summarized as success"))

    result = agent_executor.AgentExecutor().execute("goal")

    assert result.startswith("Cancelled")


# ── code_helper ──────────────────────────────────────────────────────────────
@pytest.fixture
def ch_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(ch, "_run_action", lambda *a, **k: calls.append("run") or "ran")
    monkeypatch.setattr(ch, "_build", lambda *a, **k: calls.append("build") or "built")
    monkeypatch.setattr(ch, "_write_action", lambda *a, **k: calls.append("write") or "wrote")
    monkeypatch.setattr(ch, "_explain_action", lambda *a, **k: calls.append("explain") or "explained")
    return calls


@pytest.mark.parametrize("params,verb", [
    ({"action": "run", "file_path": "x.py"}, "run"),
    ({"action": "build", "description": "a clock", "language": "python"}, "build"),
])
def test_run_and_build_do_nothing_unless_approved(monkeypatch, ch_calls, params, verb):
    asked = []
    monkeypatch.setattr(ch, "_approve_execution", lambda op, detail: asked.append((op, detail)) or False)
    out = ch.code_helper(params)
    assert ch_calls == [] and out.startswith("Cancelled")
    assert len(asked) == 1


def test_approved_run_and_build_execute_and_dialog_shows_what_will_run(monkeypatch, ch_calls):
    seen = []
    monkeypatch.setattr(ch, "_approve_execution", lambda op, detail: seen.append(detail) or True)
    assert ch.code_helper({"action": "run", "file_path": "C:/x/evil.ps1", "args": ["-f"]}) == "ran"
    assert ch.code_helper({"action": "build", "description": "tiny game", "language": "python"}) == "built"
    assert ch_calls == ["run", "build"]
    assert "evil.ps1" in seen[0] and "-f" in seen[0]
    assert "tiny game" in seen[1] and "RUN" in seen[1]


def test_auto_that_resolves_to_run_or_build_is_gated_too(monkeypatch, ch_calls):
    monkeypatch.setattr(ch, "_approve_execution", lambda op, detail: False)
    for resolved in ("run", "build"):
        monkeypatch.setattr(ch, "_detect_intent", lambda *a, _r=resolved, **k: _r)
        assert ch.code_helper({"action": "auto", "description": "x", "file_path": "a.py"}).startswith("Cancelled")
    assert ch_calls == []


def test_non_mutating_explain_action_is_not_gated(monkeypatch, ch_calls):
    monkeypatch.setattr(ch, "_approve_execution", lambda op, detail: pytest.fail("must not ask"))
    assert ch.code_helper({"action": "explain", "code": "print(1)"}) == "explained"


def test_write_action_requires_approval_before_saving(monkeypatch, tmp_path):
    target = tmp_path / "generated.py"
    monkeypatch.setattr(ch, "_llm", lambda *a, **k: "print('generated')")
    monkeypatch.setattr(ch, "_resolve_save_path", lambda *a: target)
    seen = []
    monkeypatch.setattr(ch, "_approve_file_change",
                        lambda operation, path, content: seen.append((operation, path, content)) or False)
    out = ch.code_helper({"action": "write", "description": "generate a script"})
    assert out.startswith("Cancelled")
    assert seen == [("write a code file", target, "print('generated')")]
    assert not target.exists()


def test_write_action_saves_only_approved_content(monkeypatch, tmp_path):
    target = tmp_path / "generated.py"
    monkeypatch.setattr(ch, "_llm", lambda *a, **k: "print('generated')")
    monkeypatch.setattr(ch, "_resolve_save_path", lambda *a: target)
    monkeypatch.setattr(ch, "_approve_file_change", lambda *a: True)
    out = ch.code_helper({"action": "write", "description": "generate a script"})
    assert "Code written" in out
    assert target.read_text(encoding="utf-8") == "print('generated')"


def test_code_helper_refuses_protected_reads_and_writes(monkeypatch, tmp_path):
    secret = tmp_path / "api_keys.json"
    secret.write_text('{"example": "not-secret"}', encoding="utf-8")
    assert ch._read_file(str(secret)) == ("", "Access denied: that file holds secrets and is not available to tools.")
    assert ch._save_file(secret, "overwritten") == "Access denied: that file holds secrets and is not available to tools."
    assert secret.read_text(encoding="utf-8") == '{"example": "not-secret"}'


def test_code_helper_refuses_plugin_folder_writes(tmp_path, monkeypatch):
    target = ch.BASE_DIR / "plugins" / "audit_test_plugin.py"
    assert "cannot write executable plugin" in ch._save_file(target, "raise SystemExit")
    assert not target.exists()


# ── dev_agent ────────────────────────────────────────────────────────────────
@pytest.fixture
def project(monkeypatch, tmp_path):
    rec = {"installed": [], "ran": [], "asked": []}
    monkeypatch.setattr(da, "PROJECTS_DIR", tmp_path)
    monkeypatch.setattr(da, "_plan_project", lambda d, l: {
        "project_name": "demo", "entry_point": "main.py",
        "run_command": "python main.py --go", "dependencies": ["requests", "rich"],
        "files": [{"path": "main.py", "imports": []}]})
    monkeypatch.setattr(da, "_write_file", lambda **k: "print('hi')")
    monkeypatch.setattr(da, "_install_dependencies", lambda deps, d: rec["installed"].append(deps) or "ok")
    monkeypatch.setattr(da, "_run_project", lambda cmd, d, timeout=30: rec["ran"].append(cmd) or "output")
    monkeypatch.setattr(da, "_has_error", lambda *a, **k: False)
    monkeypatch.setattr(da, "_open_vscode", lambda d: True)
    monkeypatch.setattr(da.time, "sleep", lambda s: None)
    return rec


def test_dev_agent_installs_and_runs_nothing_without_approval(monkeypatch, project, tmp_path):
    monkeypatch.setattr(safety, "confirm_action",
                        lambda op, detail="": project["asked"].append(detail) or False)
    out = da.dev_agent({"description": "demo", "language": "python"})
    assert project["installed"] == [] and project["ran"] == []
    assert "did not" in out
    assert (tmp_path / "demo").is_dir()                      # planned/written only, never run


def test_dev_agent_dialog_lists_the_exact_packages_and_command(monkeypatch, project):
    monkeypatch.setattr(safety, "confirm_action",
                        lambda op, detail="": project["asked"].append(detail) or False)
    da.dev_agent({"description": "demo", "language": "python"})
    detail = project["asked"][0]
    assert "requests, rich" in detail and "python main.py --go" in detail and "demo" in detail


def test_dev_agent_proceeds_after_approval(monkeypatch, project):
    monkeypatch.setattr(safety, "confirm_action", lambda op, detail="": True)
    da.dev_agent({"description": "demo", "language": "python"})
    assert project["installed"] == [["requests", "rich"]] and project["ran"] == ["python main.py --go"]
