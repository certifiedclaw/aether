import os
import re
import time
import subprocess
import platform
import shutil

try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False

_SYSTEM = platform.system()
_URI_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]{1,30}:[A-Za-z0-9_./\-]*")

_APP_ALIASES: dict[str, dict[str, str]] = {

    "chrome":             {"Windows": "chrome",                  "Darwin": "Google Chrome",        "Linux": "google-chrome"},
    "google chrome":      {"Windows": "chrome",                  "Darwin": "Google Chrome",        "Linux": "google-chrome"},
    "firefox":            {"Windows": "firefox",                 "Darwin": "Firefox",              "Linux": "firefox"},
    "edge":               {"Windows": "msedge",                  "Darwin": "Microsoft Edge",       "Linux": "microsoft-edge"},
    "brave":              {"Windows": "brave",                   "Darwin": "Brave Browser",        "Linux": "brave-browser"},
    "safari":             {"Windows": "msedge",                  "Darwin": "Safari",               "Linux": "firefox"},
    "opera":              {"Windows": "opera",                   "Darwin": "Opera",                "Linux": "opera"},
    "whatsapp":           {"Windows": "WhatsApp",                "Darwin": "WhatsApp",             "Linux": "whatsapp"},
    "telegram":           {"Windows": "Telegram",                "Darwin": "Telegram",             "Linux": "telegram"},
    "discord":            {"Windows": "Discord",                 "Darwin": "Discord",              "Linux": "discord"},
    "slack":              {"Windows": "Slack",                   "Darwin": "Slack",                "Linux": "slack"},
    "zoom":               {"Windows": "Zoom",                    "Darwin": "zoom.us",              "Linux": "zoom"},
    "teams":              {"Windows": "msteams",                 "Darwin": "Microsoft Teams",      "Linux": "teams"},
    "skype":              {"Windows": "skype",                   "Darwin": "Skype",                "Linux": "skype"},
    "signal":             {"Windows": "signal",                  "Darwin": "Signal",               "Linux": "signal"},
    "spotify":            {"Windows": "Spotify",                 "Darwin": "Spotify",              "Linux": "spotify"},
    "vlc":                {"Windows": "vlc",                     "Darwin": "VLC",                  "Linux": "vlc"},
    "netflix":            {"Windows": "Netflix",                 "Darwin": "Netflix",              "Linux": "firefox"},
    "vscode":             {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "visual studio code": {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "code":               {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "terminal":           {"Windows": "wt",                      "Darwin": "Terminal",             "Linux": "gnome-terminal"},
    "cmd":                {"Windows": "cmd.exe",                 "Darwin": "Terminal",             "Linux": "bash"},
    "powershell":         {"Windows": "powershell.exe",          "Darwin": "Terminal",             "Linux": "bash"},
    "postman":            {"Windows": "Postman",                 "Darwin": "Postman",              "Linux": "postman"},
    "git":                {"Windows": "git-bash",                "Darwin": "Terminal",             "Linux": "bash"},
    "figma":              {"Windows": "Figma",                   "Darwin": "Figma",                "Linux": "figma"},
    "blender":            {"Windows": "blender",                 "Darwin": "Blender",              "Linux": "blender"},
    "word":               {"Windows": "winword",                 "Darwin": "Microsoft Word",       "Linux": "libreoffice --writer"},
    "excel":              {"Windows": "excel",                   "Darwin": "Microsoft Excel",      "Linux": "libreoffice --calc"},
    "powerpoint":         {"Windows": "powerpnt",                "Darwin": "Microsoft PowerPoint", "Linux": "libreoffice --impress"},
    "libreoffice":        {"Windows": "soffice",                 "Darwin": "LibreOffice",          "Linux": "libreoffice"},
    "notepad":            {"Windows": "notepad.exe",             "Darwin": "TextEdit",             "Linux": "gedit"},
    "textedit":           {"Windows": "notepad.exe",             "Darwin": "TextEdit",             "Linux": "gedit"},
    "explorer":           {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nautilus"},
    "file explorer":      {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nautilus"},
    "finder":             {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nautilus"},
    "task manager":       {"Windows": "taskmgr.exe",             "Darwin": "Activity Monitor",     "Linux": "gnome-system-monitor"},
    "settings":           {"Windows": "ms-settings:",            "Darwin": "System Preferences",   "Linux": "gnome-control-center"},
    "calculator":         {"Windows": "calc.exe",                "Darwin": "Calculator",           "Linux": "gnome-calculator"},
    "paint":              {"Windows": "mspaint.exe",             "Darwin": "Preview",              "Linux": "gimp"},
    "instagram":          {"Windows": "Instagram",               "Darwin": "Instagram",            "Linux": "firefox"},
    "tiktok":             {"Windows": "TikTok",                  "Darwin": "TikTok",               "Linux": "firefox"},
    "notion":             {"Windows": "Notion",                  "Darwin": "Notion",               "Linux": "notion"},
    "obsidian":           {"Windows": "Obsidian",                "Darwin": "Obsidian",             "Linux": "obsidian"},
    "capcut":             {"Windows": "CapCut",                  "Darwin": "CapCut",               "Linux": "capcut"},
    "steam":              {"Windows": "steam",                   "Darwin": "Steam",                "Linux": "steam"},
    "epic":               {"Windows": "EpicGamesLauncher",       "Darwin": "Epic Games Launcher",  "Linux": "legendary"},
    "epic games":         {"Windows": "EpicGamesLauncher",       "Darwin": "Epic Games Launcher",  "Linux": "legendary"},
}


def _normalize(raw: str) -> str:
    key = raw.lower().strip()

    if key in _APP_ALIASES:
        return _APP_ALIASES[key].get(_SYSTEM, raw)

    for alias_key, os_map in _APP_ALIASES.items():
        if alias_key in key or key in alias_key:
            return os_map.get(_SYSTEM, raw)

    return raw  

def _window_appeared(app_name: str, timeout: float = 3.0) -> bool:
    """Best-effort check that a top-level window mentioning app_name exists."""
    try:
        import pygetwindow as gw
    except Exception:
        return False
    words = [w for w in app_name.lower().split() if len(w) > 2] or [app_name.lower()]
    deadline = time.time() + timeout
    while True:
        try:
            for t in gw.getAllTitles():
                tl = (t or "").lower()
                if tl and any(w in tl for w in words):
                    return True
        except Exception:
            return False
        if time.time() >= deadline:
            return False
        time.sleep(0.5)


def _match_start_app(app_name: str, apps: list[dict]) -> dict | None:
    """Pick the Start-menu entry ({"Name","AppID"}) best matching app_name.
    Exact name beats prefix beats all-words-contained; None if nothing fits."""
    q = " ".join(app_name.lower().split())
    words = [w for w in re.split(r"\W+", q) if w]
    if not words:
        return None
    best, best_rank = None, 99
    for a in apps:
        n = " ".join(str(a.get("Name", "")).lower().split())
        if not n or not a.get("AppID"):
            continue
        if n == q:
            rank = 0
        elif n.startswith(q):
            rank = 1
        elif all(w in n for w in words):
            rank = 2
        else:
            continue
        if rank < best_rank or (rank == best_rank and len(n) < len(str(best.get("Name", "")))):
            best, best_rank = a, rank
    return best


_START_APPS_CACHE: list[dict] | None = None


def _start_apps() -> list[dict] | None:
    """Everything the Start menu can launch (classic + Store apps). None if the
    query itself failed, so callers can fall back to the old typing approach."""
    global _START_APPS_CACHE
    if _START_APPS_CACHE is not None:
        return _START_APPS_CACHE
    try:
        import json
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=8,
        ).stdout.strip()
        data = json.loads(out) if out else []
        if isinstance(data, dict):
            data = [data]
        _START_APPS_CACHE = [d for d in data if isinstance(d, dict)]
        return _START_APPS_CACHE
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Shortcuts (Desktop / Start Menu) and an explicit user mapping
# ---------------------------------------------------------------------------

_SHORTCUT_EXTS = (".lnk", ".url")
_SHORTCUT_SKIP = re.compile(r"uninstall|readme|release notes|license", re.IGNORECASE)
_SHORTCUT_MAX_DEPTH = 3
_SHORTCUT_MAX_FILES = 4000


def _configured_path(app_name: str) -> str | None:
    """Explicit mapping from config: {"app_paths": {"roblox": "C:\\...\\Roblox Player.lnk"}}.
    Only existing paths count; names compare case/space-insensitively."""
    try:
        from core import config_home
        paths = (config_home.load_config() or {}).get("app_paths") or {}
    except Exception:
        return None
    if not isinstance(paths, dict):
        return None
    key = " ".join(app_name.lower().split())
    for name, target in paths.items():
        if " ".join(str(name).lower().split()) == key and isinstance(target, str):
            target = os.path.expandvars(target)
            if os.path.exists(target):
                return target
    return None


def _shortcut_dirs() -> list[str]:
    """Folders whose .lnk/.url files the user can launch, most personal first:
    the real Desktop (it may be redirected into OneDrive), the public Desktop,
    then the Start Menu program folders."""
    dirs: list[str] = []
    try:
        import winreg  # Windows only
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
            dirs.append(os.path.expandvars(winreg.QueryValueEx(k, "Desktop")[0]))
    except Exception:
        pass
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    dirs.append(os.path.join(home, "Desktop"))
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        base = os.environ.get(var)
        if base:
            dirs.append(os.path.join(base, "Desktop"))
    dirs.append(os.path.join(os.environ.get("PUBLIC", r"C:\Users\Public"), "Desktop"))
    for var in ("APPDATA", "ProgramData"):
        base = os.environ.get(var)
        if base:
            dirs.append(os.path.join(base, "Microsoft", "Windows", "Start Menu", "Programs"))
    seen, out = set(), []
    for d in dirs:
        key = os.path.normcase(os.path.normpath(d))
        if key not in seen and os.path.isdir(d):
            seen.add(key)
            out.append(d)
    return out


def _scan_shortcuts(dirs: list[str]) -> list[tuple[str, str]]:
    """[(display name, path)] for launchable shortcuts under `dirs`, in directory
    order then name order. Shallow and capped so a huge folder can't stall a turn."""
    found: list[tuple[str, str]] = []
    for base in dirs:
        for root, subdirs, files in os.walk(base):
            depth = os.path.relpath(root, base).count(os.sep) + (0 if root == base else 1)
            if depth >= _SHORTCUT_MAX_DEPTH:
                subdirs[:] = []
            subdirs.sort()
            for f in sorted(files):
                stem, ext = os.path.splitext(f)
                if ext.lower() in _SHORTCUT_EXTS and not _SHORTCUT_SKIP.search(stem):
                    found.append((stem, os.path.join(root, f)))
                    if len(found) >= _SHORTCUT_MAX_FILES:
                        return found
    return found


def _find_shortcut(app_name: str, dirs: list[str] | None = None) -> str | None:
    """Path of the shortcut that best matches app_name (same ranking as the
    Start-menu match: exact name, then prefix, then all words), or None."""
    cands = _scan_shortcuts(_shortcut_dirs() if dirs is None else dirs)
    hit = _match_start_app(app_name, [{"Name": n, "AppID": path} for n, path in cands])
    return hit["AppID"] if hit else None


def _startfile(path: str) -> bool:
    """Open a file/shortcut the way a double-click does. Never invokes a shell."""
    try:
        os.startfile(path)  # type: ignore[attr-defined]  # Windows only
        time.sleep(1.0)
        return True
    except Exception as e:
        print(f"[open_app] startfile failed for {path!r}: {e}")
        return False


# ---------------------------------------------------------------------------
# Did it really open?
# ---------------------------------------------------------------------------

_GENERIC_WORDS = {"windows", "microsoft", "app", "the", "for", "and"}


def _app_words(app_name: str) -> list[str]:
    words = [w for w in re.split(r"\W+", app_name.lower())
             if len(w) > 2 and w not in _GENERIC_WORDS]
    return words or [app_name.lower().strip()]


def _process_names() -> set[str] | None:
    """Lower-cased names of running processes; None if they can't be listed."""
    if not _PSUTIL:
        return None
    try:
        return {str(p.info.get("name") or "").lower()
                for p in psutil.process_iter(["name"])} - {""}
    except Exception:
        return None


def _window_titles() -> list[str] | None:
    try:
        import pygetwindow as gw
        return [t or "" for t in gw.getAllTitles()]
    except Exception:
        return None


def _confirm_launched(app_name: str, timeout: float = 4.0, *,
                      procs=_process_names, titles=_window_titles,
                      sleep=time.sleep, clock=time.monotonic) -> bool | None:
    """True once a running process or window title matches app_name, False if
    none shows up within `timeout`, None if neither can be inspected at all.
    Spawning a launcher proves nothing (explorer.exe "succeeds" for any target)."""
    words = _app_words(app_name)
    deadline = clock() + timeout
    while True:
        names, wins = procs(), titles()
        if names is None and wins is None:
            return None
        if names and any(w in n for n in names for w in words):
            return True
        if wins and any(w in t.lower() for t in wins for w in words):
            return True
        if clock() >= deadline:
            return False
        sleep(0.5)


def _launch_windows(app_name: str) -> bool:

    # An explicit user mapping ("app_paths" in config) beats any guessing.
    _mapped = _configured_path(app_name)
    if _mapped and _startfile(_mapped):
        return True

    # Launch the RESOLVED executable path, never the raw string through a shell:
    # "calc.exe & other" used to match via the split(".")[0] lookup and the whole
    # string then ran under cmd.exe.
    _exe = shutil.which(app_name) or shutil.which(app_name.split(".")[0])
    if _exe:
        try:
            subprocess.Popen(
                [_exe],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(1.5)
            return True
        except Exception as e:
            print(f"[open_app] subprocess failed: {e}")

    # URI-style targets only (ms-settings:, spotify:, ...). The old code ran
    # f"start {app_name}" through the shell, so a model-supplied name such as
    # "x:& calc" executed arbitrary commands. os.startfile never invokes a shell.
    if _URI_RE.fullmatch(app_name):
        try:
            os.startfile(app_name)  # type: ignore[attr-defined]  # Windows only
            time.sleep(1.0)
            return True
        except Exception:
            pass

    # Desktop / Start Menu shortcuts: cheap file lookups, and they cover apps that
    # Get-StartApps doesn't list (a Desktop-only .lnk, a Steam .url, ...).
    _lnk = _find_shortcut(app_name)
    if _lnk and _startfile(_lnk):
        return True

    # Ask Windows what is actually installed BEFORE typing into Start. An unknown
    # name used to cost ~8s of keystrokes and then open a web search; now it
    # fails in about a second without touching the keyboard.
    _apps = _start_apps()
    if _apps is not None:
        _hit = _match_start_app(app_name, _apps)
        if not _hit:
            return False
        try:
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{_hit['AppID']}"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1.0)
            return True
        except Exception as e:
            print(f"[open_app] Start app launch failed: {e}")

    try:
        import pyautogui
        pyautogui.PAUSE = 0.1
        pyautogui.press("win")
        time.sleep(0.7)
        pyautogui.write(app_name, interval=0.05)
        time.sleep(0.9)
        pyautogui.press("enter")
        time.sleep(2.5)
        # Typing into Start proves nothing: an unknown name just opens a web
        # search. Only report success if a window with that name appeared.
        return _window_appeared(app_name)
    except Exception as e:
        print(f"[open_app] Start Menu search failed: {e}")

    return False


def _launch_macos(app_name: str) -> bool:

    try:
        result = subprocess.run(
            ["open", "-a", app_name],
            capture_output=True, timeout=8
        )
        if result.returncode == 0:
            time.sleep(1.0)
            return True
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["open", "-a", f"{app_name}.app"],
            capture_output=True, timeout=8
        )
        if result.returncode == 0:
            time.sleep(1.0)
            return True
    except Exception:
        pass

    binary = shutil.which(app_name) or shutil.which(app_name.lower())
    if binary:
        try:
            subprocess.Popen(
                [binary],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(1.0)
            return True
        except Exception:
            pass

    try:
        import pyautogui
        pyautogui.hotkey("command", "space")
        time.sleep(0.6)
        pyautogui.write(app_name, interval=0.05)
        time.sleep(0.8)
        pyautogui.press("enter")
        time.sleep(1.5)
        return _window_appeared(app_name)
    except Exception as e:
        print(f"[open_app] Spotlight failed: {e}")

    return False


def _launch_linux(app_name: str) -> bool:

    binary = (
        shutil.which(app_name) or
        shutil.which(app_name.lower()) or
        shutil.which(app_name.lower().replace(" ", "-")) or
        shutil.which(app_name.lower().replace(" ", "_"))
    )
    if binary:
        try:
            subprocess.Popen(
                [binary],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(1.0)
            return True
        except Exception:
            pass

    try:
        result = subprocess.run(
            ["xdg-open", app_name],
            capture_output=True, timeout=5
        )
        if result.returncode == 0:
            return True
    except Exception:
        pass

    for desktop_name in [
        app_name.lower(),
        app_name.lower().replace(" ", "-"),
        app_name.lower().replace(" ", ""),
    ]:
        try:
            result = subprocess.run(
                ["gtk-launch", desktop_name],
                capture_output=True, timeout=5
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass

    return False


_OS_LAUNCHERS = {
    "Windows": _launch_windows,
    "Darwin":  _launch_macos,
    "Linux":   _launch_linux,
}

def _opened_message(app_name: str) -> str:
    """"Opened X." only when something matching X is actually running or showing
    a window; otherwise say what is known. Checked on Windows, where the launchers
    spawn helpers (explorer.exe, shortcuts) that succeed for any target."""
    if _SYSTEM == "Windows" and _confirm_launched(app_name) is False:
        return (
            f"Launched {app_name}, but no matching window or process appeared. "
            f"It may still be loading, or it may need to be started another way - "
            f"check whether it opened."
        )
    return f"Opened {app_name}."


from core.risk import gated  # noqa: E402
@gated("open_app")
def open_app(
    parameters=None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    app_name = (parameters or {}).get("app_name", "").strip()

    if not app_name:
        return "No application name provided."

    launcher = _OS_LAUNCHERS.get(_SYSTEM)
    if launcher is None:
        return f"Unsupported operating system: {_SYSTEM}"

    normalized = _normalize(app_name)
    print(f"[open_app] Launching: '{app_name}' → '{normalized}' ({_SYSTEM})")

    if player:
        player.write_log(f"[open_app] {app_name}")

    try:
        if launcher(normalized) or (
                normalized.lower() != app_name.lower() and launcher(app_name)):
            return _opened_message(app_name)
        return (
            f"Could not confirm that {app_name} launched. "
            f"It may still be loading, or it might not be installed."
        )
    except Exception as e:
        print(f"[open_app] Error: {e}")
        return f"Failed to open {app_name}: {e}"