"""Secrets files must not be readable through any tool; port scans are bounded."""
from pathlib import Path

import pytest

from core import protected_paths as pp

ROOT = Path(pp.__file__).resolve().parent.parent


def test_secret_files_are_protected():
    assert pp.is_protected(ROOT / "config" / "api_keys.json")
    assert pp.is_protected("config/../config/api_keys.json".replace("config/..", str(ROOT)))
    assert pp.is_protected(ROOT / "config" / "certs" / "server.crt")
    assert pp.is_protected(ROOT / "data" / "security" / "anything")
    assert pp.is_protected("/somewhere/else/api_keys.json")
    assert pp.is_protected("/x/id.pem") and pp.is_protected("/x/k.key")
    assert pp.is_protected(ROOT / ".env")
    assert pp.is_protected(ROOT / "config" / ".env.production")


def test_ordinary_files_are_not_protected(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("hi")
    assert not pp.is_protected(f)
    assert not pp.is_protected(ROOT / "config" / "config.example.json")


def test_symlink_to_secret_is_protected(tmp_path):
    target = tmp_path / "api_keys.json"
    target.write_text("{}")
    link = tmp_path / "innocent.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable")
    assert pp.is_protected(link)


def test_diff_and_word_count_refuse_secrets(tmp_path):
    from actions import extra_tools as et
    other = tmp_path / "a.txt"
    other.write_text("x")
    secret = ROOT / "config" / "api_keys.json"
    assert et.diff_files(str(secret), str(other)) == pp.DENIED
    assert et.diff_files(str(other), str(secret)) == pp.DENIED
    secret_made = not secret.exists()
    secret.parent.mkdir(exist_ok=True)
    if secret_made:
        secret.write_text('{"gemini_api_key": "LEAK"}')
    try:
        assert et.word_count(str(secret)) == pp.DENIED
    finally:
        if secret_made:
            secret.unlink()


def test_file_processor_and_pdf_refuse_secrets():
    from actions import file_processor as fp, pdf_tools
    assert fp.file_processor({"file_path": str(ROOT / "config" / "api_keys.json")}) == pp.DENIED
    assert "Access denied" in pdf_tools.extract_text(str(ROOT / "config" / "api_keys.json"))


@pytest.mark.parametrize("ports", ["1-999999999", "0-10", "1-70000", "abc", "1-5000", "70000", ""])
def test_port_scan_rejects_unbounded_or_invalid_ranges(ports):
    from actions import osint_tools as ot
    out = ot.port_scan("127.0.0.1", ports)
    assert out.startswith(("Invalid", "Too many", "No ports")), out


def test_port_scan_small_range_still_runs():
    from actions import osint_tools as ot
    out = ot.port_scan("127.0.0.1", "1-3")
    assert not out.startswith(("Invalid", "Too many", "No ports"))
