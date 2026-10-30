"""Roadmap #49: crash reports are sanitized, self-contained and linked to the trace."""
import json
from pathlib import Path

import pytest

from core import crash_logger, trace_log

KEY = "sk-abcdefghijklmnopqrstuvwxyz123456"


@pytest.fixture
def crash_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(crash_logger, "_CRASH_DIR", tmp_path / "crashes")
    trace_log.shutdown()
    yield tmp_path / "crashes"
    trace_log.shutdown()


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _boom() -> Exception:
    try:
        raise RuntimeError(f"failed calling api with {KEY}")
    except RuntimeError as e:
        return e


def test_secrets_removed_everywhere(crash_dir):
    exc = _boom()
    path = crash_logger.log_crash(
        exc, user_message=f"use key {KEY} please", tool="web_search",
        extra={"api_key": "hunter2hunter2", "url": "https://x/?token=abc123&q=1", "n": 3})
    text = Path(path).read_text(encoding="utf-8")
    assert KEY not in text
    assert "hunter2hunter2" not in text
    assert "abc123" not in text
    data = json.loads(text)
    assert data["sanitized"] is True and data["extra"]["n"] == 3
    assert data["exception"] == "RuntimeError"


def test_home_and_install_paths_scrubbed(crash_dir, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/home/alice_smith")))
    root = str(Path(crash_logger.__file__).resolve().parent.parent)
    exc = RuntimeError("could not open /home/alice_smith/Documents/secret.txt")
    path = crash_logger.log_crash(exc, extra={"where": f"{root}/core/x.py", "who": "alice_smith"})
    text = Path(path).read_text(encoding="utf-8")
    assert "alice_smith" not in text
    data = json.loads(text)
    assert data["message"].startswith("could not open ~/Documents")
    assert data["extra"]["where"] == "<aether>/core/x.py"


def test_traceback_present_outside_except_block(crash_dir):
    exc = _boom()                       # log_crash called after the except block has ended
    data = _load(crash_logger.log_crash(exc))
    assert "RuntimeError" in data["traceback"] and "_boom" in data["traceback"]


def test_report_links_to_trace_and_includes_tail(crash_dir, tmp_path):
    assert trace_log.start({}, trace_dir=tmp_path / "trace", install_hooks=False)
    with trace_log.turn("do something risky") as tid:
        trace_log.event("tool_request", tool="file_controller", args={"api_key": "zzz-secret-value"})
        data = _load(crash_logger.log_crash(_boom(), tool="file_controller"))
    assert data["trace"]["session"] == trace_log.session_id()
    assert data["trace"]["turn"] == tid
    kinds = [e["kind"] for e in data["trace"]["last_events"]]
    assert "turn_start" in kinds and "tool_request" in kinds
    assert "zzz-secret-value" not in json.dumps(data)


def test_works_without_a_trace(crash_dir):
    data = _load(crash_logger.log_crash(_boom()))
    assert data["trace"]["last_events"] == []


def test_context_param_and_rotation(crash_dir, monkeypatch):
    monkeypatch.setattr(crash_logger, "_MAX_CRASH_FILES", 3)
    for i in range(6):
        crash_logger.log_crash(_boom(), context=f"STT-{i}")
    assert crash_logger.crash_count() == 3
    latest = sorted(crash_dir.glob("*.json"))[-1]
    assert _load(str(latest))["extra"]["context"] == "STT-5"
