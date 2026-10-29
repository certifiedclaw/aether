"""Regression tests for roadmap #29: no shell=True call sites remain.

Covers dev_agent._open_vscode, computer_settings' Linux xrandr brightness
fallback, and a source-level guard so shell=True can't quietly come back.
"""
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO = Path(__file__).resolve().parent.parent


# ── source guard ───────────────────────────────────────────────────────────

def _code_lines(path: Path):
    """Yield (lineno, line) for lines that aren't pure comments."""
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.lstrip().startswith("#"):
            yield n, line


def test_no_shell_true_in_application_code():
    offenders = []
    for sub in ("actions", "core", "agent", "dashboard", "memory", "integrations", "security"):
        for path in (REPO / sub).rglob("*.py"):
            for n, line in _code_lines(path):
                if re.search(r"shell\s*=\s*True", line):
                    offenders.append(f"{path.relative_to(REPO)}:{n}")
    for name in ("main.py", "ui.py"):
        for n, line in _code_lines(REPO / name):
            if re.search(r"shell\s*=\s*True", line):
                offenders.append(f"{name}:{n}")
    assert not offenders, f"shell=True reintroduced at: {offenders}"


# ── dev_agent._open_vscode ─────────────────────────────────────────────────

@pytest.fixture
def dev_agent():
    import actions.dev_agent as da
    with patch.object(da.time, "sleep"):
        yield da


def test_open_vscode_passes_path_as_plain_argument(dev_agent, tmp_path):
    popen = MagicMock()
    with patch.object(dev_agent.shutil, "which", return_value="/usr/bin/code"), \
         patch.object(dev_agent.subprocess, "Popen", popen):
        assert dev_agent._open_vscode(tmp_path) is True
    args, kwargs = popen.call_args
    assert args[0] == ["/usr/bin/code", str(tmp_path)]
    assert not kwargs.get("shell")


def test_open_vscode_reports_failure_when_code_is_not_installed(dev_agent, tmp_path):
    """The old shell=True form never raised for a missing launcher and always
    claimed success."""
    popen = MagicMock()
    with patch.object(dev_agent.shutil, "which", return_value=None), \
         patch.object(dev_agent.os.path, "isfile", return_value=False), \
         patch.object(dev_agent.subprocess, "Popen", popen):
        assert dev_agent._open_vscode(tmp_path) is False
    popen.assert_not_called()


def test_open_vscode_falls_through_to_next_candidate_when_launch_fails(dev_agent, tmp_path):
    popen = MagicMock(side_effect=[OSError("boom"), MagicMock()])
    with patch.object(dev_agent, "_vscode_candidates", return_value=["/a/code", "/b/code"]), \
         patch.object(dev_agent.subprocess, "Popen", popen):
        assert dev_agent._open_vscode(tmp_path) is True
    assert [c.args[0][0] for c in popen.call_args_list] == ["/a/code", "/b/code"]


@pytest.mark.parametrize("bad", ["proj & calc", "proj|x", "a^b", "50%done", 'q"uote', "bang!"])
def test_open_vscode_refuses_cmd_metacharacters_for_batch_launchers(dev_agent, bad):
    popen = MagicMock()
    with patch.object(dev_agent, "_vscode_candidates", return_value=[r"C:\VS\bin\code.cmd"]), \
         patch.object(dev_agent.subprocess, "Popen", popen):
        assert dev_agent._open_vscode(Path(bad)) is False
    popen.assert_not_called()


def test_open_vscode_allows_same_characters_for_real_executables(dev_agent):
    """Metacharacters only matter when cmd.exe is in the loop (.cmd/.bat)."""
    popen = MagicMock()
    with patch.object(dev_agent, "_vscode_candidates", return_value=["/usr/bin/code"]), \
         patch.object(dev_agent.subprocess, "Popen", popen):
        assert dev_agent._open_vscode(Path("/tmp/a & b")) is True
    assert popen.call_args.args[0] == ["/usr/bin/code", "/tmp/a & b"]


# ── computer_settings xrandr fallback ──────────────────────────────────────

XRANDR_VERBOSE = """\
Screen 0: minimum 320 x 200, current 1920 x 1080, maximum 16384 x 16384
HDMI-1 disconnected (normal left inverted right x axis y axis)
\tIdentifier: 0x44
\tBrightness: 0.30
eDP-1 connected primary 1920x1080+0+0 (0x46) normal (normal) 344mm x 193mm
\tIdentifier: 0x45
\tGamma:      1.0:1.0:1.0
\tBrightness: 0.70
\tClones:
DP-2 connected 2560x1440+1920+0 (0x5b) normal (normal) 597mm x 336mm
\tBrightness: 1.00
"""


@pytest.fixture
def settings():
    import actions.computer_settings as cs
    return cs


def test_xrandr_picks_first_connected_output_and_its_own_brightness(settings):
    with patch.object(settings.subprocess, "check_output", return_value=XRANDR_VERBOSE):
        assert settings._xrandr_first_output() == ("eDP-1", 0.70)


def test_xrandr_brightness_up_uses_argv_not_a_shell(settings):
    run = MagicMock()
    with patch.object(settings.subprocess, "check_output", return_value=XRANDR_VERBOSE), \
         patch.object(settings.subprocess, "run", run):
        assert settings._xrandr_adjust_brightness(+0.1, 0.1, 1.0) is True
    args, kwargs = run.call_args
    assert args[0] == ["xrandr", "--output", "eDP-1", "--brightness", "0.80"]
    assert not kwargs.get("shell")


@pytest.mark.parametrize("delta,expected", [(+0.5, "1.00"), (-0.9, "0.10")])
def test_xrandr_brightness_is_clamped(settings, delta, expected):
    run = MagicMock()
    with patch.object(settings.subprocess, "check_output", return_value=XRANDR_VERBOSE), \
         patch.object(settings.subprocess, "run", run):
        settings._xrandr_adjust_brightness(delta, 0.1, 1.0)
    assert run.call_args.args[0][-1] == expected


def test_xrandr_no_connected_output_is_a_clean_noop(settings):
    run = MagicMock()
    out = "Screen 0: minimum 320 x 200\nHDMI-1 disconnected\n\tBrightness: 1.0\n"
    with patch.object(settings.subprocess, "check_output", return_value=out), \
         patch.object(settings.subprocess, "run", run):
        assert settings._xrandr_adjust_brightness(+0.1, 0.1, 1.0) is False
    run.assert_not_called()


def test_xrandr_missing_binary_does_not_raise(settings):
    with patch.object(settings.subprocess, "check_output", side_effect=FileNotFoundError("xrandr")):
        assert settings._xrandr_adjust_brightness(+0.1, 0.1, 1.0) is False


def test_linux_brightness_falls_back_to_xrandr_without_brightnessctl(settings):
    """brightness_up/down on Linux without brightnessctl must reach the new
    helper, in the right direction."""
    which_missing = MagicMock(returncode=1)
    with patch.object(settings, "_OS", "Linux"), \
         patch.object(settings.subprocess, "run", return_value=which_missing), \
         patch.object(settings, "_xrandr_adjust_brightness") as adjust:
        settings.brightness_up()
        settings.brightness_down()
    assert [c.args for c in adjust.call_args_list] == [(+0.1, 0.1, 1.0), (-0.1, 0.1, 1.0)]
