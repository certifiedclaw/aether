"""verify_local must flag packages that requirements.txt declares but are missing."""
from pathlib import Path

from tools import req_check

ROOT = Path(__file__).resolve().parent.parent

SAMPLE = """\
# Aether deps
fastapi
uvicorn[standard]            # extras stripped
PyQt6>=6.5
pywinauto ; sys_platform == "win32"
linuxonly ; sys_platform == "linux"
-r other.txt
--index-url https://example.invalid/simple
git+https://github.com/x/y.git

watchdog                 # file system watcher
python_multipart
fastapi
"""


def test_declared_packages_parsing(monkeypatch):
    names = req_check.declared_packages(SAMPLE)
    low = [n.lower() for n in names]
    assert "uvicorn" in low and "pyqt6" in low and "watchdog" in low
    assert "uvicorn[standard]" not in low                 # extras stripped
    assert low.count("fastapi") == 1                      # de-duplicated
    assert not any(n.startswith(("-", "git+")) for n in low)
    # platform markers are honoured both ways
    import sys
    assert ("pywinauto" in low) == (sys.platform == "win32")
    assert ("linuxonly" in low) == sys.platform.startswith("linux")


def test_missing_detection_uses_the_installed_predicate(tmp_path):
    f = tmp_path / "r.txt"
    f.write_text("alpha\nbeta\ngamma\n")
    missing, total = req_check.missing_requirements(f, installed=lambda n: n != "beta")
    assert (missing, total) == (["beta"], 3)


def test_unreadable_file_is_reported_not_raised(tmp_path):
    assert req_check.missing_requirements(tmp_path / "nope.txt") is None
    assert "could not check" in req_check.render(None)[0]


def test_render_warns_for_core_but_only_informs_for_optional():
    out = "\n".join(req_check.render((["watchdog", "keyboard"], 10)))
    assert "WARN" in out and "watchdog, keyboard" in out and "pip install -r requirements.txt" in out
    out = "\n".join(req_check.render((["langchain", "llama-index-llms-ollama", "google_genai"], 10)))
    assert "WARN" not in out and "info: optional integrations" in out
    assert "ok:" in out
    assert req_check.render(([], 7)) == ["  ok: all 7 declared packages are installed"]
    mixed = "\n".join(req_check.render((["keyboard", "langchain"], 10)))
    assert "WARN" in mixed and "keyboard" in mixed and "info:" in mixed


def test_real_requirements_file_parses_and_has_the_packages_that_bit_us():
    names = [n.lower() for n in req_check.declared_packages((ROOT / "requirements.txt").read_text(encoding="utf-8"))]
    assert len(names) > 20
    assert "watchdog" in names and "keyboard" in names
    assert not req_check.is_optional("watchdog") and not req_check.is_optional("keyboard")
    assert req_check.is_optional("langchain-community") and req_check.is_optional("llama_index")


def test_verify_report_environment_section_includes_the_requirements_check(monkeypatch):
    import tools.verify_local as vl
    monkeypatch.setattr(req_check, "missing_requirements", lambda p: (["watchdog"], 5))
    monkeypatch.setattr(vl, "_resource_snapshot", lambda: None)
    monkeypatch.setattr(vl, "_other_aether_processes", lambda: [])
    monkeypatch.setattr(vl.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("no ollama")))
    vl.out.clear()
    vl.check_env()
    text = "\n".join(vl.out)
    assert "Requirements :" in text and "WARN" in text and "watchdog" in text
