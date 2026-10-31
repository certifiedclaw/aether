"""core/crash_logger.py sanitizes what it writes (roadmap #49)."""
import json
from pathlib import Path

from core import crash_logger as cl


def test_sanitize_strips_secrets_keys_email_and_home():
    home = str(Path.home())
    raw = (f'File "{home}/aether/x.py" failed; api_key=abcdef123456789 '
           'token: "zzzzzzzzzz" AIzaSyA1234567890abcdefghijklmnop mail me@example.com '
           'sk-abcdefghijklmnopqrstuv')
    out = cl.sanitize(raw)
    for leak in ("abcdef123456789", "zzzzzzzzzz", "AIzaSy", "me@example.com", "sk-abcdef", home):
        assert leak not in out
    assert "~/aether/x.py" in out and "[email]" in out


def test_sanitize_is_noop_on_plain_text():
    assert cl.sanitize("division by zero") == "division by zero"
    assert cl.sanitize("") == ""


def test_log_crash_writes_sanitized_report(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "_CRASH_DIR", tmp_path)
    try:
        raise ValueError("bad password=hunter2hunter2 for bob@example.com")
    except ValueError as e:
        path = cl.log_crash(e, user_message="my token=abcdefghijk", tool="t",
                            extra={"api_key": "SECRETSECRET1"})
    text = Path(path).read_text(encoding="utf-8")
    for leak in ("hunter2hunter2", "bob@example.com", "abcdefghijk", "SECRETSECRET1"):
        assert leak not in text
    data = json.loads(text)
    assert data["exception"] == "ValueError" and data["tool"] == "t"
