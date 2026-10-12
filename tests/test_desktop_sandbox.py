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
