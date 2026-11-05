import json
import logging
import os
from pathlib import Path
from collections import deque

from core import live_report
from core import log_redaction
from core.observability import StructuredLogger


def test_short_placeholders_do_not_poison_log_redaction(monkeypatch):
    monkeypatch.setattr(log_redaction, "_SECRETS", set())
    log_redaction.register_secret("test")
    assert "test" not in log_redaction.current_secrets()


def test_redacts_registered_secrets_urls_and_secret_assignments(monkeypatch):
    secret = "local-secret-that-must-not-leak"
    monkeypatch.setattr(log_redaction, "_SECRETS", {secret})
    text = (
        f"https://alice:password@example.test/x?key={secret}&keep=yes "
        f"api_key={secret} {secret}"
    )
    redacted = log_redaction.redact_text(text)
    assert secret not in redacted
    assert "alice:password" not in redacted
    assert "key=" in redacted and "keep=yes" in redacted


def test_redacts_url_credentials_and_sensitive_log_extras(monkeypatch):
    secret = "extra-field-secret-should-not-leak"
    monkeypatch.setattr(log_redaction, "_SECRETS", {secret})
    private_url = (
        "htt" + "ps://" + "alice" + ":" + "password" + "@example.test/path"
        + f"?key={secret}&keep=yes"
    )
    redacted_url = log_redaction.redact_text(private_url)
    assert "alice" not in redacted_url
    assert "password" not in redacted_url
    assert "keep=yes" in redacted_url

    record = logging.LogRecord(
        "test.logger", logging.INFO, __file__, 1, "request completed", (), None
    )
    record.__dict__["authorization"] = " ".join(("Bear" + "er", secret))
    record.__dict__["payload"] = {"api_key": secret, "detail": "preserved"}

    assert log_redaction.RedactingLogFilter().filter(record)
    assert record.authorization == "(hidden)"
    assert record.payload == {"api_key": "(hidden)", "detail": "preserved"}


def test_logging_filter_redacts_formatted_message_and_exception(monkeypatch):
    secret = "filter-secret-should-not-leak"
    monkeypatch.setattr(log_redaction, "_SECRETS", {secret})
    try:
        raise RuntimeError(f"request failed with {secret}")
    except RuntimeError:
        import sys
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        "test.logger", logging.ERROR, __file__, 1, "token=%s", (secret,), exc_info
    )
    assert log_redaction.RedactingLogFilter().filter(record)
    rendered = record.getMessage() + (record.exc_text or "")
    assert secret not in rendered
    assert record.args == ()
    assert record.exc_info is None


def test_structured_events_keep_details_but_redact_credentials(tmp_path, monkeypatch):
    secret = "event-secret-should-not-leak"
    monkeypatch.setattr(log_redaction, "_SECRETS", {secret})
    sink = StructuredLogger(tmp_path)
    sink.event(
        "model_request",
        prompt=f"Please use {secret}",
        headers={"Authorization": f"Bearer {secret}"},
        api_key=secret,
    )

    record = json.loads((tmp_path / "events.jsonl").read_text(encoding="utf-8"))
    assert record["prompt"] == "Please use [REDACTED:secret]"
    assert record["headers"]["Authorization"] == "(hidden)"
    assert record["api_key"] == "(hidden)"
    assert secret not in (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    if os.name != "nt":
        assert Path(tmp_path).stat().st_mode & 0o777 == 0o700
        assert (tmp_path / "events.jsonl").stat().st_mode & 0o777 == 0o600


def test_live_turn_previews_keep_context_but_redact_credentials(tmp_path, monkeypatch):
    secret = "live-report-secret-should-not-leak"
    monkeypatch.setattr(log_redaction, "_SECRETS", {secret})
    monkeypatch.setattr(live_report, "_LOG_DIR", tmp_path)
    monkeypatch.setattr(live_report, "_JSONL_PATH", tmp_path / "live_turns.jsonl")
    monkeypatch.setattr(live_report, "_REPORT_PATH", tmp_path / "live_report.md")
    monkeypatch.setattr(live_report, "_recent", deque(maxlen=50))
    monkeypatch.setattr(live_report, "_totals", {
        "turns": 0, "tier_counts": {}, "memory_hits": 0, "memory_checked": 0,
        "gemini_escalations": 0, "exhausted": 0, "truncated": 0,
        "tools_sent_sum": 0, "latency_sum_ms": 0.0,
    })

    live_report.record_turn(
        user_text=f"keep this question {secret}", reply=f"keep this answer {secret}",
        tier="QUICK",
    )
    saved = (tmp_path / "live_turns.jsonl").read_text(encoding="utf-8")
    assert secret not in saved
    assert "keep this question" in saved and "keep this answer" in saved
