"""Approved desktop code runs in a child process with a deadline: `while True: pass`
used to hang the worker thread forever because exec() cannot be interrupted."""
import os
import sys
import time

import pytest

import actions.desktop as d


@pytest.fixture
def approve(monkeypatch):
    monkeypatch.setattr(d, "_confirm_generated_code", lambda code: True)


def _runner_processes():
    """PIDs of live desktop_runner children (Linux /proc; elsewhere returns None)."""
    if not os.path.isdir("/proc"):
        return None
    pids = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            argv = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")
        except OSError:
            continue
        # match the child's real argument list [python, .../desktop_runner.py], not any
        # process (e.g. a wrapping shell) whose command text merely mentions the name
        if len(argv) >= 2 and argv[1].endswith(b"desktop_runner.py") and int(pid) != os.getpid():
            pids.append(int(pid))
    return pids


def test_infinite_loop_is_stopped_at_the_deadline_and_the_process_is_gone(monkeypatch, approve):
    monkeypatch.setattr(d, "desktop_task_timeout", lambda: 2)
    t = time.monotonic()
    out = d._execute_generated_code("while True:\n    pass")
    elapsed = time.monotonic() - t
    assert "longer than 2 seconds and was stopped" in out
    assert 1.5 < elapsed < 8                      # killed at the deadline, not hanging
    pids = _runner_processes()
    if pids is not None:
        time.sleep(0.3)
        assert _runner_processes() == []          # the runaway child really is dead


def test_slow_but_finishing_code_is_not_cut_short(monkeypatch, approve):
    monkeypatch.setattr(d, "desktop_task_timeout", lambda: 10)
    out = d._execute_generated_code("time.sleep(1)\nprint('finished')")
    assert out == "finished"


def test_output_and_errors_come_back_from_the_child(approve):
    assert d._execute_generated_code("print('a')\nprint(2 * 21)") == "a\n42"
    assert d._execute_generated_code("x = 1\n") == "Done."
    assert d._execute_generated_code("print(1/0)").startswith("Execution error: division by zero")


def test_child_revalidates_the_code_even_if_the_parent_check_is_bypassed():
    """Defence in depth: call the isolated runner directly, skipping the parent's validation."""
    for bad in ("import os\nprint(1)", "print(Path.__init__.__globals__)",
                "Path('x').write_text('hi')", "open('x')"):
        out = d._run_isolated(bad, timeout=20)
        assert out.startswith("Blocked"), (bad, out)


def test_denied_confirmation_never_starts_a_child_process(monkeypatch):
    monkeypatch.setattr(d, "_confirm_generated_code", lambda code: False)
    monkeypatch.setattr(d, "_run_isolated", lambda *a, **k: pytest.fail("child must not start"))
    assert d._execute_generated_code("print(1)").startswith("Cancelled")


def test_blocked_code_never_starts_a_child_process(monkeypatch, approve):
    monkeypatch.setattr(d, "_run_isolated", lambda *a, **k: pytest.fail("child must not start"))
    assert d._execute_generated_code("import os").startswith("Blocked")


def test_result_parsing_ignores_noise_and_takes_the_last_tagged_line():
    good = d._RESULT_TAG + '{"ok": true, "output": "real"}'
    noise = "import-time chatter\n" + d._RESULT_TAG + "not json\nmore noise"
    assert d._parse_child_result("junk\n" + good + "\ntrailing") == {"ok": True, "output": "real"}
    assert d._parse_child_result(noise) is None
    assert d._parse_child_result("nothing tagged here") is None
    assert d._parse_child_result("") is None
    forged = d._RESULT_TAG + '{"ok": true, "output": 5}'
    assert d._parse_child_result(forged) is None            # output must be a string
    assert d._parse_child_result(good + "\n" + d._RESULT_TAG + '{"ok": true, "output": "later"}')["output"] == "later"


def test_no_result_from_the_child_is_reported_not_raised(monkeypatch):
    class P:
        stdout, stderr = "garbage", "Traceback...\nSomeError: boom"
    monkeypatch.setattr(d.subprocess, "run", lambda *a, **k: P())
    out = d._run_isolated("print(1)", timeout=5)
    assert out.startswith("Execution error: the task produced no result") and "boom" in out


def test_failure_to_start_the_child_is_reported_not_raised(monkeypatch):
    def boom(*a, **k): raise OSError("no interpreter")
    monkeypatch.setattr(d.subprocess, "run", boom)
    assert "could not start the task" in d._run_isolated("print(1)", timeout=5)


def test_frozen_build_falls_back_to_in_process_execution(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(d.subprocess, "run", lambda *a, **k: pytest.fail("no child when frozen"))
    assert d._run_isolated("print('in process')") == "in process"


def test_timeout_setting_is_configurable_and_clamped(monkeypatch):
    import core.llm_client as lc
    for cfg, want in (({}, 30), ({"desktop_task_timeout": 60}, 60), ({"desktop_task_timeout": 1}, 5),
                      ({"desktop_task_timeout": 99999}, 300), ({"desktop_task_timeout": "x"}, 30)):
        monkeypatch.setattr(lc, "_load_config", lambda c=cfg: dict(c))
        assert d.desktop_task_timeout() == want


def test_output_is_capped(approve):
    out = d._execute_generated_code("print('x' * 9000)")
    assert len(out) == d._MAX_OUTPUT
