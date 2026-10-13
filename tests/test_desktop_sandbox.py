"""Roadmap #23: the desktop task sandbox must not be escapable, and model-
written code must not run without the user approving the exact code."""
import pytest

import actions.desktop as d


@pytest.fixture
def approve(monkeypatch):
    """Confirmation auto-approved, recording whether it was even asked."""
    asked = []
    monkeypatch.setattr(d, "_confirm_generated_code",
                        lambda code: asked.append(code) or True)
    return asked


@pytest.mark.parametrize("code", [
    "getattr(Path, 'home')()",
    "hasattr(Path, 'x')",
    "Path.__init__.__globals__",
    "f'{Path.__init__}'",
    "Path('x').parser.os.system('echo hi')",
    "os_path.os.system('echo hi')",
    "pyautogui.sys.modules",
    "time.__spec__",
    "import os",
    "from os import system",
    "ctypes.windll",
    "winreg.OpenKey",
    "Path('x').unlink()",
    "Path('x').write_text('hi')",
    "Path('x').rename('y')",
    "Path('x').replace('y')",
    "open('x')",
    "eval('1')",
    "exec('1')",
    "__import__('os')",
    "type(1)",
    "class A: pass",
    "Path.home = 1",
    "x = lambda: Path.__class__",
    "[c for c in ().__class__.__mro__]",
    "(lambda: 0).__globals__",
])
def test_escape_attempts_are_blocked_and_never_confirmed(code, approve):
    out = d._execute_generated_code(code)
    assert out.startswith("Blocked"), (code, out)
    assert approve == []            # rejected before the user is even asked


def test_runtime_namespace_has_no_escape_hatches_even_without_validation():
    sb = d._build_sandbox()
    assert "getattr" not in sb["__builtins__"]
    assert "hasattr" not in sb["__builtins__"]
    assert "ctypes" not in sb and "winreg" not in sb
    with pytest.raises(NameError):
        exec("getattr(Path, 'home')", dict(sb))
    # proxies expose only functions, no module reachable via attributes
    for name in ("time", "shutil", "os_path"):
        obj = sb[name]
        assert not hasattr(obj, "os") and not hasattr(obj, "sys")


def test_denied_confirmation_runs_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(d, "_confirm_generated_code", lambda code: False)
    target = tmp_path / "should_not_exist"
    out = d._execute_generated_code(f"Path({str(target)!r}).mkdir()")
    assert out.startswith("Cancelled")
    assert not target.exists()


def test_confirmation_denied_by_default_without_ui_callback(monkeypatch):
    import core.safety as safety

    class NoUI:
        def request_confirmation(self, op, detail=""):
            return False            # what SafetyLayer does with no callback
    monkeypatch.setattr(safety, "get_safety", lambda config=None: NoUI())
    assert d._confirm_generated_code("print(1)") is False


def test_confirmation_error_means_deny(monkeypatch):
    import core.safety as safety

    def boom(config=None):
        raise RuntimeError("no config")
    monkeypatch.setattr(safety, "get_safety", boom)
    assert d._confirm_generated_code("print(1)") is False


def test_confirmation_receives_the_exact_code(monkeypatch):
    import core.safety as safety
    seen = {}

    class UI:
        def request_confirmation(self, op, detail=""):
            seen["op"], seen["detail"] = op, detail
            return True
    monkeypatch.setattr(safety, "get_safety", lambda config=None: UI())
    assert d._confirm_generated_code("print(42)") is True
    assert "print(42)" in seen["detail"]


def test_legitimate_code_still_works_after_approval(approve, tmp_path):
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "b.png").write_text("y")
    code = (
        f"p = Path({str(tmp_path)!r})\n"
        "names = sorted(x.name for x in p.iterdir())\n"
        "print(len(names), ', '.join(names))\n"
        "print(os_path.exists(str(p / 'a.txt')), p.stat().st_size >= 0)\n"
        "(p / 'newdir').mkdir()\n"
        "shutil.copy2(str(p / 'a.txt'), str(p / 'newdir' / 'a.txt'))\n"
        "t = time.strftime('%Y')\n"
        "print(len(t))\n"
    )
    out = d._execute_generated_code(code)
    assert out.splitlines()[0] == "2 a.txt, b.png"
    assert out.splitlines()[1] == "True True"
    assert (tmp_path / "newdir" / "a.txt").exists()
    assert len(approve) == 1


def test_markdown_fences_unsafe_marker_and_size_limit(approve):
    assert "cannot be performed safely" in d._execute_generated_code("UNSAFE")
    assert "cannot be performed safely" in d._execute_generated_code("")
    assert d._execute_generated_code("```python\nprint('hi')\n```") == "hi"
    big = "x = 1\n" * 2000
    assert d._execute_generated_code(big).startswith("Blocked")


def test_llm_error_string_is_not_executed(approve):
    out = d._execute_generated_code("ERROR: connection refused")
    assert out.startswith("Blocked") and approve == []


@pytest.mark.parametrize("action,params,needle", [
    ("wallpaper", {"path": "C:/pictures/wallpaper.png"}, "C:/pictures/wallpaper.png"),
    ("wallpaper_url", {"url": "https://example.invalid/wallpaper.png"}, "https://example.invalid/wallpaper.png"),
    ("organize", {"mode": "by_type"}, "file type"),
    ("clean", {}, "Desktop Archive"),
])
def test_desktop_mutations_require_explicit_approval(monkeypatch, action, params, needle):
    import core.safety as safety
    seen = []
    monkeypatch.setattr(safety, "confirm_action",
                        lambda operation, detail="": seen.append((operation, detail)) or False)
    monkeypatch.setattr(d, "set_wallpaper", lambda *a, **k: pytest.fail("wallpaper must not change"))
    monkeypatch.setattr(d, "set_wallpaper_from_url", lambda *a, **k: pytest.fail("URL must not be fetched"))
    monkeypatch.setattr(d, "organize_desktop", lambda *a, **k: pytest.fail("files must not move"))
    monkeypatch.setattr(d, "clean_desktop", lambda *a, **k: pytest.fail("files must not move"))
    out = d.desktop_control({"action": action, **params})
    assert out.startswith("Cancelled")
    assert len(seen) == 1 and needle in seen[0][1]


@pytest.mark.parametrize("action,params,helper,result", [
    ("wallpaper", {"path": "C:/pictures/wallpaper.png"}, "set_wallpaper", "wallpaper set"),
    ("wallpaper_url", {"url": "https://example.invalid/wallpaper.png"}, "set_wallpaper_from_url", "wallpaper downloaded"),
    ("organize", {"mode": "by_type"}, "organize_desktop", "organized"),
    ("clean", {}, "clean_desktop", "cleaned"),
])
def test_desktop_mutations_proceed_once_after_approval(monkeypatch, tmp_path, action, params, helper, result):
    import core.safety as safety
    asked = []
    calls = []
    monkeypatch.setattr(safety, "confirm_action",
                        lambda operation, detail="": asked.append((operation, detail)) or True)
    monkeypatch.setattr(d, "_get_desktop", lambda: tmp_path)
    def perform(*args, **kwargs):
        calls.append((args, kwargs))
        return result
    monkeypatch.setattr(d, helper, perform)
    assert d.desktop_control({"action": action, **params}) == result
    assert len(asked) == 1 and len(calls) == 1


def test_missing_desktop_mutation_arguments_and_read_only_actions_do_not_prompt(monkeypatch):
    import core.safety as safety
    monkeypatch.setattr(safety, "confirm_action", lambda *a, **k: pytest.fail("must not ask"))
    monkeypatch.setattr(d, "list_desktop", lambda: "listed")
    assert d.desktop_control({"action": "wallpaper"}) == "No image path provided."
    assert d.desktop_control({"action": "wallpaper_url"}) == "No URL provided."
    assert d.desktop_control({"action": "list"}) == "listed"


def test_wallpaper_command_failure_is_reported(monkeypatch, tmp_path):
    from types import SimpleNamespace

    image = tmp_path / "wallpaper.jpg"
    image.write_bytes(b"image")
    monkeypatch.setattr(d, "_OS", "Linux")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=1, stderr="permission denied", stdout="")

    monkeypatch.setattr(d.subprocess, "run", run)
    result = d.set_wallpaper(str(image))

    assert result == "Could not set wallpaper: permission denied"
    assert len(calls) == 1
    assert calls[0][1]["timeout"] == 10


def test_wallpaper_timeout_is_reported(monkeypatch, tmp_path):
    image = tmp_path / "wallpaper.jpg"
    image.write_bytes(b"image")
    monkeypatch.setattr(d, "_OS", "Darwin")
    monkeypatch.setattr(d.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(
        d.subprocess.TimeoutExpired("osascript", 10)))

    assert d.set_wallpaper(str(image)).startswith("Could not set wallpaper:")


def test_windows_wallpaper_api_failure_is_reported(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import ctypes

    image = tmp_path / "wallpaper.jpg"
    image.write_bytes(b"image")
    monkeypatch.setattr(d, "_OS", "Windows")
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(
        user32=SimpleNamespace(SystemParametersInfoW=lambda *args: False)), raising=False)

    assert d.set_wallpaper(str(image)) == "Could not set wallpaper: Windows rejected the wallpaper change."
