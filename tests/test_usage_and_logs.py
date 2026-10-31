"""core/usage_stats.py (#50) and core/log_analyzer.py (#85)."""
import json
import time
from datetime import datetime

from core import audit_log, log_analyzer, perf_stats, usage_stats


def test_usage_stats_counts_perf_audit_and_crashes(tmp_path):
    now = time.time()
    perf = tmp_path / "perf.jsonl"
    audit = tmp_path / "audit.jsonl"
    crashes = tmp_path / "crashes"
    crashes.mkdir()
    (crashes / "c.json").write_text("{}")
    for i in range(3):
        perf_stats.record("qwen", "local", 100, 50, None, 1.0, path=perf)
    perf_stats.record("gemini", "cloud", 100, 70, None, 1.0, path=perf)
    audit_log.record("file_controller", "delete", "ask", "approved", path=audit)
    audit_log.record("file_controller", "delete", "ask", "denied", path=audit)
    audit_log.record("web_search", "q", "ask", "approved", path=audit)
    audit_log.record("supervisor", "alert", "system", "sent", path=audit)

    s = usage_stats.collect(7, perf_path=perf, audit_path=audit, crash_dir=crashes, now=now + 5)
    assert s["rounds"] == 4 and s["routes"] == {"local": 3, "cloud": 1}
    assert s["output_tokens"] == 220 and s["crashes"] == 1
    assert dict(s["top_tools"]) == {"file_controller": 2, "web_search": 1}   # watchdog excluded
    assert abs(s["denied_rate"] - 1 / 3) < 1e-9
    text = usage_stats.render(s)
    assert "local only" in text and "file_controller=2" in text


def test_usage_stats_empty_window(tmp_path):
    s = usage_stats.collect(1, perf_path=tmp_path / "p", audit_path=tmp_path / "a", crash_dir=tmp_path / "c")
    assert "no activity" in usage_stats.render(s)


def _line(ts, lvl, msg):
    return f"{datetime.fromtimestamp(ts):%Y-%m-%d %H:%M:%S},123 {lvl:<8} aether — {msg}\n"


def test_log_analyzer_groups_normalizes_and_finds_exceptions(tmp_path):
    now = time.time()
    log = tmp_path / "aether.log"
    body = ""
    for i in range(4):
        body += _line(now - 600 + i, "WARNING", f"[LLM] Warmup failed for 'qwen{i}' after {i + 10}s")
    body += _line(now - 300, "ERROR", "Tool crashed: file C:\\Users\\ian\\x.txt")
    body += "Traceback (most recent call last):\n  File \"a.py\", line 1\nValueError: bad thing\n"
    body += _line(now - 200, "INFO", "all fine")
    body += _line(now - 100, "INFO", "WRN: ui warning line 7")
    body += _line(now - 100 - 48 * 3600, "ERROR", "ancient")
    log.write_text(body, encoding="utf-8")

    rep = log_analyzer.analyze(log, hours=24, now=now)
    by = {(g["level"], g["pattern"]): g for g in rep["groups"]}
    warm = by[("WARN", "[LLM] Warmup failed for <q> after Ns")]
    assert warm["count"] == 4
    assert rep["groups"][0]["level"] == "ERROR"                       # errors rank first
    err = next(g for g in rep["groups"] if g["level"] == "ERROR")
    assert err["exception"] == "ValueError: bad thing"
    assert all("ancient" not in g["pattern"] for g in rep["groups"])  # outside the window
    assert any(g["pattern"].startswith("ui warning") for g in rep["groups"])  # WRN: prefix counts
    text = log_analyzer.render(rep)
    assert "1 error(s), 5 warning(s)" in text and "busiest hour" in text


def test_log_analyzer_clean_and_missing_log(tmp_path):
    assert "no warnings" in log_analyzer.render(log_analyzer.analyze(tmp_path / "nope.log"))
