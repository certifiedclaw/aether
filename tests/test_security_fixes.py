"""calculate() must not allow attribute/dunder escapes or runaway powers; open_app must not shell out."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.extra_tools import calculate


def test_calc_normal():
    assert calculate("2+3*4") == "= 14"
    assert calculate("240 × 17") == "= 4080"
    assert calculate("sqrt(16) + abs(-2)") == "= 6.0"
    assert calculate("2^10") == "= 1024"
    assert calculate("pi").startswith("= 3.14159")


def test_calc_rejects_escapes():
    for bad in ("(1).__class__", "().__class__.__base__.__subclasses__()",
                "__import__('os')", "open('x')", "'a'*3", "[1,2]", "lambda: 1"):
        assert calculate(bad).startswith(("Unsafe", "Calculation error")), bad


def test_calc_power_dos_is_fast():
    t = time.time()
    assert calculate("9**9**9").startswith("Unsafe")
    assert calculate("10**100000").startswith("Unsafe")
    assert time.time() - t < 1


def test_open_app_has_no_shell_true():
    src = (Path(__file__).resolve().parent.parent / "actions" / "open_app.py").read_text(encoding="utf-8")
    assert "shell=True" not in src


def test_windows_volume_set_uses_new_and_fallback_apis(monkeypatch):
    import types
    import actions.computer_settings as cs
    monkeypatch.setattr(cs, "_OS", "Windows")
    presses = []
    monkeypatch.setattr(cs, "_PYAUTOGUI", True, raising=False)
    monkeypatch.setattr(cs, "pyautogui", types.SimpleNamespace(press=presses.append), raising=False)

    # new-style pycaw: GetSpeakers() returns an object with .EndpointVolume
    set_to = []
    ep = types.SimpleNamespace(SetMasterVolumeLevelScalar=lambda v, ctx: set_to.append(v))
    fake = types.ModuleType("pycaw.pycaw")
    fake.AudioUtilities = types.SimpleNamespace(GetSpeakers=lambda: types.SimpleNamespace(EndpointVolume=ep))
    monkeypatch.setitem(sys.modules, "pycaw", types.ModuleType("pycaw"))
    monkeypatch.setitem(sys.modules, "pycaw.pycaw", fake)
    cs.volume_set(30)
    assert set_to == [0.3] and presses == []

    # pycaw broken -> real keypress fallback (50 down, then 15 up), not a no-op
    def boom():
        raise AttributeError("no Activate")
    fake.AudioUtilities = types.SimpleNamespace(GetSpeakers=boom)
    cs.volume_set(30)
    assert presses.count("volumedown") == 50 and presses.count("volumeup") == 15
