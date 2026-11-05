"""open_app: find Desktop/Start Menu shortcuts, honour config app_paths, and only say
"Opened X." when something matching X is really running.

Regression for a live log: "open roblox" answered "Opened roblox." in one second while
nothing opened (the launcher only spawned explorer.exe), and the app's Desktop-only
.lnk was never looked at.
"""
import os

import pytest

import actions.open_app as oa


def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    return str(path)


# ── shortcut search ──────────────────────────────────────────────────────────

@pytest.fixture
def folders(tmp_path):
    desktop, start = tmp_path / "Desktop", tmp_path / "Programs"
    _touch(desktop / "Roblox Player.lnk")
    _touch(desktop / "Uninstall Roblox.lnk")
    _touch(desktop / "notes.txt")
    _touch(desktop / "Half-Life 2.url")
    _touch(start / "Roblox" / "Roblox Studio.lnk")
    _touch(start / "Roblox" / "Roblox Player.lnk")        # duplicate name, later directory
    return [str(desktop), str(start)]


def test_finds_the_desktop_shortcut_by_partial_name(folders):
    assert _norm(oa._find_shortcut("roblox", folders)) == _norm(os.path.join(folders[0], "Roblox Player.lnk"))


def test_exact_and_multiword_names_pick_the_right_shortcut(folders):
    assert oa._find_shortcut("roblox studio", folders).endswith("Roblox Studio.lnk")
    assert oa._find_shortcut("Roblox Player", folders).startswith(folders[0])   # Desktop beats Start Menu


def test_internet_shortcuts_count(folders):
    assert oa._find_shortcut("half-life 2", folders).endswith("Half-Life 2.url")


def test_uninstallers_and_non_shortcuts_are_never_matched(folders):
    assert oa._find_shortcut("uninstall roblox", folders) is None
    assert oa._find_shortcut("notes", folders) is None


def test_unknown_app_finds_nothing(folders):
    assert oa._find_shortcut("Zorblax Quantum Editor", folders) is None
    assert oa._find_shortcut("", folders) is None


def test_missing_folders_are_ignored():
    assert oa._find_shortcut("anything", ["/no/such/dir"]) is None


def test_scan_is_depth_and_count_capped(tmp_path, monkeypatch):
    deep = tmp_path / "a" / "b" / "c" / "d" / "Deep App.lnk"
    _touch(deep)
    assert oa._find_shortcut("deep app", [str(tmp_path)]) is None           # below the depth cap
    monkeypatch.setattr(oa, "_SHORTCUT_MAX_FILES", 2)
    for i in range(5):
        _touch(tmp_path / f"App{i}.lnk")
    assert len(oa._scan_shortcuts([str(tmp_path)])) == 2


def _norm(p):
    return os.path.normcase(os.path.normpath(p))


# ── explicit mapping from config ─────────────────────────────────────────────

def _cfg(monkeypatch, cfg):
    from core import config_home
    monkeypatch.setattr(config_home, "load_config", lambda *a, **k: cfg)


def test_configured_path_is_used_when_it_exists(monkeypatch, tmp_path):
    target = _touch(tmp_path / "Roblox Player.lnk")
    _cfg(monkeypatch, {"app_paths": {"Roblox": target}})
    assert oa._configured_path("roblox") == target
    assert oa._configured_path("  ROBLOX ") == target


def test_configured_path_ignored_when_missing_or_malformed(monkeypatch, tmp_path):
    _cfg(monkeypatch, {"app_paths": {"roblox": str(tmp_path / "gone.lnk")}})
    assert oa._configured_path("roblox") is None
    for bad in ({"app_paths": ["roblox"]}, {"app_paths": None}, {}):
        _cfg(monkeypatch, bad)
        assert oa._configured_path("roblox") is None


def test_configured_path_survives_a_broken_config_loader(monkeypatch):
    from core import config_home

    def boom(*a, **k):
        raise RuntimeError("bad json")

    monkeypatch.setattr(config_home, "load_config", boom)
    assert oa._configured_path("roblox") is None


# ── launch order on Windows ──────────────────────────────────────────────────

@pytest.fixture
def win(monkeypatch):
    opened = []
    monkeypatch.setattr(oa, "_configured_path", lambda n: None)
    monkeypatch.setattr(oa.shutil, "which", lambda *_: None)
    monkeypatch.setattr(oa, "_startfile", lambda path: opened.append(path) or True)
    return opened


def test_desktop_shortcut_launches_before_start_apps_are_queried(win, monkeypatch):
    monkeypatch.setattr(oa, "_find_shortcut", lambda n: r"C:\Desktop\Roblox Player.lnk")

    def no_query():
        raise AssertionError("Get-StartApps should not run when a shortcut matched")

    monkeypatch.setattr(oa, "_start_apps", no_query)
    assert oa._launch_windows("roblox") is True
    assert win == [r"C:\Desktop\Roblox Player.lnk"]


def test_configured_path_beats_everything(win, monkeypatch):
    monkeypatch.setattr(oa, "_configured_path", lambda n: r"D:\Games\Roblox.lnk")
    monkeypatch.setattr(oa.shutil, "which", lambda *_: r"C:\bin\roblox.exe")
    assert oa._launch_windows("roblox") is True
    assert win == [r"D:\Games\Roblox.lnk"]


def test_no_shortcut_falls_through_to_start_apps(win, monkeypatch):
    import subprocess
    monkeypatch.setattr(oa, "_find_shortcut", lambda n: None)
    monkeypatch.setattr(oa, "_start_apps", lambda: [{"Name": "Roblox Player", "AppID": "RB!App"}])
    launched = []
    monkeypatch.setattr(subprocess, "Popen", lambda args, **k: launched.append(args))
    monkeypatch.setattr(oa.time, "sleep", lambda s: None)
    assert oa._launch_windows("roblox") is True
    assert launched and "RB!App" in launched[0][1] and win == []


# ── "did it really open?" ────────────────────────────────────────────────────

class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _confirm(app, procs=None, titles=None, timeout=4.0):
    c = Clock()
    return oa._confirm_launched(app, timeout, procs=lambda: procs, titles=lambda: titles,
                                sleep=c.sleep, clock=c)


def test_running_process_confirms():
    assert _confirm("roblox", procs={"explorer.exe", "robloxplayerbeta.exe"}, titles=[]) is True


def test_window_title_confirms():
    assert _confirm("Windows Media Player", procs={"explorer.exe"}, titles=["Windows Media Player"]) is True


def test_nothing_matching_is_reported_as_not_confirmed():
    assert _confirm("roblox", procs={"explorer.exe", "chrome.exe"}, titles=["Chrome"]) is False


def test_waits_for_a_slow_app_then_confirms():
    c = Clock()
    seen = []

    def procs():
        seen.append(c.t)
        return {"robloxplayerbeta.exe"} if c.t >= 2.0 else {"explorer.exe"}

    assert oa._confirm_launched("roblox", 4.0, procs=procs, titles=lambda: [],
                                sleep=c.sleep, clock=c) is True
    assert seen[0] == 0.0 and seen[-1] >= 2.0


def test_unverifiable_environment_returns_none():
    assert _confirm("roblox", procs=None, titles=None) is None


def test_generic_words_do_not_cause_false_matches():
    # every word is generic -> fall back to the whole name, so "windowsterminal" is no match
    assert _confirm("Windows Microsoft App", procs={"windowsterminal.exe"}, titles=[]) is False
    assert oa._app_words("Windows Media Player") == ["media", "player"]
    assert oa._app_words("a") == ["a"]


# ── what the user is told ────────────────────────────────────────────────────

def _as_windows(monkeypatch, confirm):
    monkeypatch.setattr(oa, "_SYSTEM", "Windows")
    monkeypatch.setitem(oa._OS_LAUNCHERS, "Windows", lambda name: True)
    monkeypatch.setattr(oa, "_confirm_launched", confirm)


def test_unconfirmed_launch_is_not_reported_as_opened(monkeypatch):
    _as_windows(monkeypatch, lambda name: False)
    out = oa.open_app({"app_name": "roblox"})
    assert "Opened" not in out and out.startswith("Launched roblox") and "check whether it opened" in out


@pytest.mark.parametrize("verdict", [True, None])
def test_confirmed_or_unverifiable_launch_says_opened(monkeypatch, verdict):
    _as_windows(monkeypatch, lambda name: verdict)
    assert oa.open_app({"app_name": "roblox"}) == "Opened roblox."


def test_other_platforms_are_not_second_guessed(monkeypatch):
    monkeypatch.setattr(oa, "_SYSTEM", "Linux")
    monkeypatch.setitem(oa._OS_LAUNCHERS, "Linux", lambda name: True)

    def boom(name):
        raise AssertionError("verification is Windows-only")

    monkeypatch.setattr(oa, "_confirm_launched", boom)
    assert oa.open_app({"app_name": "vlc"}) == "Opened vlc."
