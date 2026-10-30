"""Flight recorder (core/trace_log.py) and its viewer (tools/trace_view.py)."""
import json
import logging
import subprocess
import sys

import pytest

from core import trace_log
from tools import trace_view


@pytest.fixture
def trace(tmp_path, monkeypatch):
    monkeypatch.delenv("AETHER_TRACE", raising=False)
    trace_log.shutdown()
    assert trace_log.start({}, trace_dir=tmp_path, install_hooks=False)
    yield tmp_path
    trace_log.shutdown()


def _events(path):
    trace_log.flush()
    files = sorted(path.glob("trace-*.jsonl"))
    return [json.loads(ln) for f in files for ln in f.read_text(encoding="utf-8").splitlines() if ln]


def test_every_line_has_order_time_thread_kind(trace):
    trace_log.event("hello", a=1)
    trace_log.event("hello", a=2)
    ev = [e for e in _events(trace) if e["kind"] == "hello"]
    assert [e["a"] for e in ev] == [1, 2]
    assert ev[0]["seq"] < ev[1]["seq"]
    for e in ev:
        assert {"seq", "ts", "t", "thread", "kind"} <= set(e)
    assert ev[0]["ts"][-6] in "+-"            # UTC offset present


def test_session_start_records_environment(trace):
    start = _events(trace)[0]
    assert start["kind"] == "session_start"
    assert "python" in start and "platform" in start and "git" in start


def test_secrets_are_hidden_but_counters_survive(trace):
    trace_log.event("x", api_key="sk-abcdefghijklmnopqrstuvwxyz", prompt_tokens=42,
                    url="https://h/?key=SECRET123&q=1",
                    note="auth sk-abcdefghijklmnopqrstuvwxyz1234 end")
    e = [e for e in _events(trace) if e["kind"] == "x"][0]
    assert e["api_key"] == "(hidden)"
    assert e["prompt_tokens"] == 42
    assert "sk-abcdefghijklmnop" not in e["note"]


def test_long_fields_are_clipped_visibly(tmp_path, monkeypatch):
    trace_log.shutdown()
    assert trace_log.start({"logging.trace_max_field_chars": 200}, trace_dir=tmp_path,
                           install_hooks=False)
    try:
        trace_log.event("big", text="a" * 1000)
        e = [e for e in _events(tmp_path) if e["kind"] == "big"][0]
        assert "clipped" in e["text"] and len(e["text"]) < 300
    finally:
        trace_log.shutdown()


def test_turn_tags_events_and_records_failure(trace):
    with pytest.raises(ValueError):
        with trace_log.turn("do the thing") as tid:
            trace_log.event("inside")
            raise ValueError("boom")
    ev = _events(trace)
    inside = [e for e in ev if e["kind"] == "inside"][0]
    assert inside["turn"] == tid
    end = [e for e in ev if e["kind"] == "turn_end"][0]
    assert end["ok"] is False and "boom" in end["error"] and "Traceback" in end["traceback"]
    assert [e for e in ev if e["kind"] == "turn_start"][0]["user_text"] == "do the thing"


def test_span_records_duration_args_and_error(trace):
    with trace_log.span("tool", tool="t", args={"q": 1}) as s:
        s["result"] = "fine"
    with pytest.raises(RuntimeError):
        with trace_log.span("tool", tool="bad"):
            raise RuntimeError("nope")
    ends = [e for e in _events(trace) if e["kind"] == "tool.end"]
    assert ends[0]["ok"] and ends[0]["result"] == "fine" and ends[0]["dur_ms"] >= 0
    assert not ends[1]["ok"] and "nope" in ends[1]["error"]


def test_unserialisable_values_never_raise(trace):
    class Weird:
        def __repr__(self):
            return "<weird>"
    trace_log.event("w", obj=Weird(), data=b"abc", s={1, 2}, f=float("nan"))
    e = [e for e in _events(trace) if e["kind"] == "w"][0]
    assert e["obj"] == "<weird>" and e["data"] == "<3 bytes>"


def test_event_is_a_noop_when_not_started():
    trace_log.shutdown()
    trace_log.event("nothing", a=1)          # must not raise
    assert not trace_log.is_enabled()


def test_env_switch_disables(tmp_path, monkeypatch):
    trace_log.shutdown()
    monkeypatch.setenv("AETHER_TRACE", "0")
    assert trace_log.start({}, trace_dir=tmp_path) is False


def test_hooks_capture_logging_stdio_and_subprocess(tmp_path):
    trace_log.shutdown()
    assert trace_log.start({}, trace_dir=tmp_path)
    try:
        logging.getLogger("aether.test").warning("careful %s", "now")
        print("a printed line")
        subprocess.run([sys.executable, "-c", "print('child')"], capture_output=True, text=True)
        ev = _events(tmp_path)
    finally:
        trace_log.shutdown()
    kinds = {e["kind"] for e in ev}
    assert {"log", "stdout", "subprocess_end"} <= kinds
    assert any(e["kind"] == "log" and e["msg"] == "careful now" for e in ev)
    assert any(e["kind"] == "stdout" and e["text"] == "a printed line" for e in ev)
    sub = [e for e in ev if e["kind"] == "subprocess_end"][0]
    assert sub["returncode"] == 0 and "child" in sub["stdout"]
    # hooks are restored afterwards
    assert not isinstance(sys.stdout, type(sys.stdout)) or sys.stdout.__class__.__name__ != "_Tee"


def test_gate_decision_is_recorded(trace):
    from core import risk
    risk._audit("files", "delete", "ask", "denied", {"path": "/x", "api_key": "k"}, "test")
    e = [e for e in _events(trace) if e["kind"] == "gate_decision"][0]
    assert e["decision"] == "denied" and e["params"]["path"] == "/x"
    assert e["params"]["api_key"] == "(hidden)"


def test_viewer_round_trip(trace, capsys):
    with trace_log.turn("hi there") as tid:
        trace_log.event("llm_request", call=1, model="m")
        with trace_log.span("tool", tool="web_search", args={"q": "x"}):
            pass
    trace_log.flush()
    assert trace_view.main(["--dir", str(trace), "list"]) == 0
    assert trace_view.main(["--dir", str(trace), "summary"]) == 0
    out = capsys.readouterr().out
    assert "web_search" in out and "turns: 1" in out
    assert trace_view.main(["--dir", str(trace), "turn", tid]) == 0
    out = capsys.readouterr().out
    assert "llm_request" in out and "tool.end" in out
    assert trace_view.main(["--dir", str(trace), "show", "--kind", "tool", "--grep", "web_search"]) == 0
