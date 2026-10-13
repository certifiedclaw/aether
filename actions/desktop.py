#desktop.py
import ast
import os
import sys
import types
import json
import shutil
import subprocess
import tempfile
import platform
from core.known_folders import known_folder as _known_folder
from pathlib import Path
from datetime import datetime

try:
    import pyautogui
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

def _get_desktop() -> Path:
    _kf = _known_folder("desktop")
    if _kf:
        return _kf
    if _OS == "Linux":
        xdg = os.environ.get("XDG_DESKTOP_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Desktop"

# ── Generated-code sandbox (roadmap #23) ────────────────────────────────────
# Model-written code is (1) statically validated against an ALLOWLIST, then
# (2) shown to the user for confirmation via core/safety (denied by default
# when no UI callback is registered), and only then (3) exec'd with a
# minimal namespace. Each layer is independent; none relies on the prompt.

_MAX_GENERATED_CODE = 4000

# Attribute names generated code may touch. Anything not listed is rejected,
# so a surprise module-valued attribute (e.g. Path(...).parser on 3.13+, which
# is the ntpath module and leads to os.system) can never be reached.
_ALLOWED_ATTRS = frozenset({
    # pathlib.Path (read / list / mkdir only: no write, rename, replace, unlink)
    "joinpath", "exists", "is_file", "is_dir", "name", "stem", "suffix",
    "suffixes", "parent", "parents", "parts", "home", "iterdir", "glob",
    "rglob", "stat", "mkdir", "read_text", "resolve", "expanduser", "absolute",
    "st_size", "st_mtime", "st_ctime",
    # proxies exposed below
    "copy2", "copytree", "disk_usage", "sleep", "time", "strftime",
    "join", "isfile", "isdir", "basename", "dirname", "splitext", "getsize",
    "click", "doubleClick", "rightClick", "moveTo", "moveRel", "dragTo",
    "typewrite", "write", "press", "hotkey", "scroll", "size", "position",
    # plain str / list / dict methods
    "append", "extend", "items", "keys", "values", "get", "sort", "split",
    "lower", "upper", "strip", "lstrip", "rstrip", "startswith", "endswith",
    "count", "index", "title", "pop", "setdefault", "update",
    # used on disk_usage() result
    "total", "used", "free",
})

_FORBIDDEN_NODES = (
    ast.Import, ast.ImportFrom, ast.ClassDef, ast.Global, ast.Nonlocal,
    ast.Await, ast.AsyncFunctionDef, ast.AsyncFor, ast.AsyncWith,
    ast.Yield, ast.YieldFrom,
    # `match` class patterns look attributes up BY STRING (case str(__class__=c)), which
    # skips the ast.Attribute / ast.Name checks below. No generated task needs match.
    *(getattr(ast, n) for n in ("Match", "MatchClass") if hasattr(ast, n)),
)
_FORBIDDEN_NAMES = frozenset({
    "exec", "eval", "compile", "open", "input", "globals", "locals", "vars",
    "getattr", "setattr", "delattr", "hasattr", "type", "super", "object",
    "breakpoint", "memoryview", "bytearray",
})


def _validate_generated_code(code: str) -> str | None:
    """Return None if the code passes the allowlist, else a short reason."""
    if len(code) > _MAX_GENERATED_CODE:
        return f"generated code is too long ({len(code)} > {_MAX_GENERATED_CODE} chars)"
    try:
        tree = ast.parse(code, "<aether_desktop>", "exec")
    except SyntaxError as e:
        return f"syntax error: {e.msg}"
    for node in ast.walk(tree):
        if isinstance(node, _FORBIDDEN_NODES):
            return f"{type(node).__name__} is not allowed"
        if isinstance(node, ast.Attribute):
            if not isinstance(node.ctx, ast.Load):
                return "assigning to attributes is not allowed"
            if node.attr.startswith("_"):
                return f"access to '{node.attr}' is not allowed"
            if node.attr not in _ALLOWED_ATTRS:
                return f"attribute '.{node.attr}' is not on the allowlist"
        elif isinstance(node, ast.Name):
            if node.id.startswith("_") or node.id in _FORBIDDEN_NAMES:
                return f"name '{node.id}' is not allowed"
        elif isinstance(node, (ast.FunctionDef, ast.Lambda)):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("_"):
                return f"function name '{node.name}' is not allowed"
    return None


def _confirm_generated_code(code: str) -> bool:
    """Ask the user to approve the exact code. Denied if anything goes wrong."""
    try:
        from core.safety import get_safety
        return bool(get_safety().request_confirmation(
            "desktop task (AI-generated code)",
            "Aether generated this code to run on your desktop (all of it is shown):\n\n" + code,
        ))
    except Exception as e:
        print(f"[Desktop] confirmation unavailable, denying: {e}")
        return False


def _confirm_desktop_action(operation: str, detail: str) -> bool:
    from core.safety import confirm_action
    return confirm_action(f"desktop {operation}", detail)


def _ns(**members) -> object:
    """Namespace exposing ONLY the given members (no module reachable from it)."""
    return types.SimpleNamespace(**members)


def _build_sandbox() -> dict:
    import time

    safe_builtins = {
        "print": print,
        "len": len, "str": str, "int": int, "float": float,
        "bool": bool, "list": list, "dict": dict, "tuple": tuple,
        "range": range, "enumerate": enumerate, "sorted": sorted,
        "isinstance": isinstance,
        "max": max, "min": min, "sum": sum, "abs": abs,
        "zip": zip, "map": map, "filter": filter,
    }

    sandbox = {
        "__builtins__": safe_builtins,
        "Path": Path,
        "time": _ns(sleep=time.sleep, time=time.time, strftime=time.strftime),
        "shutil": _ns(copy2=shutil.copy2, copytree=shutil.copytree,
                      disk_usage=shutil.disk_usage),
        "os_path": _ns(join=os.path.join, exists=os.path.exists,
                       isfile=os.path.isfile, isdir=os.path.isdir,
                       basename=os.path.basename, dirname=os.path.dirname,
                       splitext=os.path.splitext, getsize=os.path.getsize,
                       expanduser=os.path.expanduser),
    }

    if _PYAUTOGUI:
        sandbox["pyautogui"] = _ns(**{
            n: getattr(pyautogui, n)
            for n in ("click", "doubleClick", "rightClick", "moveTo", "moveRel",
                      "dragTo", "typewrite", "write", "press", "hotkey",
                      "scroll", "size", "position")
            if hasattr(pyautogui, n)
        })

    # ctypes / winreg are intentionally NOT exposed: ctypes is arbitrary native
    # code, and registry reads leak data. Use the dedicated tools instead.
    return sandbox


_RESULT_TAG = "\x00AETHER_DESKTOP_RESULT\x00"     # keep in sync with actions/desktop_runner.py
_MAX_OUTPUT = 4000


def desktop_task_timeout() -> int:
    """Seconds an approved desktop task may run (config "desktop_task_timeout", 5-300)."""
    try:
        from core.llm_client import _load_config
        v = int(_load_config().get("desktop_task_timeout", 30))
    except Exception:  # noqa: BLE001
        v = 30
    return max(5, min(v, 300))


def _exec_in_process(code: str) -> tuple[bool, str]:
    """Run already-validated, already-approved code in the minimal sandbox."""
    sandbox      = _build_sandbox()
    output_lines = []
    sandbox["__builtins__"]["print"] = (
        lambda *a, **k: output_lines.append(" ".join(str(x) for x in a)))
    try:
        exec(compile(code, "<aether_desktop>", "exec"), sandbox)
        return True, "\n".join(output_lines) if output_lines else "Done."
    except Exception as e:  # noqa: BLE001
        print(f"[Desktop] Exec error: {e}\nCode:\n{code[:300]}")
        return False, f"Execution error: {e}"


def _parse_child_result(stdout: str) -> dict | None:
    """The last tagged JSON line of the child's stdout; anything else is noise."""
    for line in reversed((stdout or "").splitlines()):
        if line.startswith(_RESULT_TAG):
            try:
                r = json.loads(line[len(_RESULT_TAG):])
                if isinstance(r, dict) and isinstance(r.get("output"), str):
                    return r
            except ValueError:
                return None
    return None


def _run_isolated(code: str, timeout: int | None = None) -> str:
    """Run approved code in a child process that is killed after `timeout` seconds."""
    timeout = timeout or desktop_task_timeout()
    if getattr(sys, "frozen", False):          # packaged exe: no separate interpreter to start
        return _exec_in_process(code)[1]
    runner = Path(__file__).resolve().with_name("desktop_runner.py")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            [sys.executable, str(runner)], input=code, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, env=env, **kwargs)
    except subprocess.TimeoutExpired:
        print(f"[Desktop] Task exceeded {timeout}s and was stopped.")
        return f"The desktop task ran longer than {timeout} seconds and was stopped."
    except Exception as e:  # noqa: BLE001
        return f"Execution error: could not start the task ({e})."
    result = _parse_child_result(proc.stdout)
    if result is None:
        tail = (proc.stderr or "").strip().splitlines()[-1:] or ["no output"]
        return f"Execution error: the task produced no result ({tail[0][:200]})."
    return result["output"][:_MAX_OUTPUT]


def _execute_generated_code(code: str, player=None) -> str:
    if not code or code.strip() == "UNSAFE":
        return "This action cannot be performed safely."

    code = code.strip()
    if code.startswith("```"):
        lines = code.split("\n")
        code  = "\n".join(lines[1:-1]).strip()

    problem = _validate_generated_code(code)
    if problem:
        print(f"[Desktop] Rejected generated code: {problem}")
        return f"Blocked: the generated code was not allowed ({problem})."

    if not _confirm_generated_code(code):
        return "Cancelled: the desktop task was not approved."

    return _run_isolated(code)


def _ask_gemini_for_desktop_action(task: str) -> str:
    from core.llm_client import call_llm_text

    desktop = str(_get_desktop())

    system = (
        "You are a desktop automation code generator. "
        "Output ONLY raw Python code — no explanation, no markdown, no backticks."
    )
    prompt = (
        f"Current OS: {_OS}\nDesktop path: {desktop}\n\n"
        "Available names (nothing else exists, no imports): "
        "Path (read/list/mkdir only), shutil.copy2/copytree/disk_usage, "
        "os_path.join/exists/isfile/isdir/basename/dirname/splitext/getsize, "
        "time.sleep/time/strftime, "
        "pyautogui.click/moveTo/typewrite/press/hotkey/scroll/size/position.\n"
        "Use print() to report results. Attributes starting with an underscore "
        "are forbidden.\n\n"
        "Rules: NO deletion, NO writing or renaming files, NO subprocess, "
        "NO exec/eval, NO imports. The user will review your code before it runs.\n"
        "If unsafe, output exactly: UNSAFE\n\n"
        f"Task: {task}"
    )
    try:
        code = call_llm_text(prompt, system=system).strip()
        if code.startswith("```"):
            lines = code.split("\n")
            code  = "\n".join(lines[1:-1]).strip()
        return code
    except Exception as e:
        return f"ERROR: {e}"


def _run_wallpaper_command(command: list[str]) -> str | None:
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    if result.returncode == 0:
        return None
    detail = (result.stderr or result.stdout).strip()
    return detail[:300] or f"command exited with status {result.returncode}"


def set_wallpaper(image_path: str) -> str:
    path = Path(image_path).expanduser().resolve()
    if not path.exists():
        return f"Image not found: {image_path}"
    if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}:
        return f"Unsupported format: {path.suffix}. Use jpg, png, bmp or webp."

    try:
        if _OS == "Windows":
            import ctypes
            if path.suffix.lower() in {".webp", ".png"}:
                try:
                    from PIL import Image
                    bmp_path = Path(tempfile.mktemp(suffix=".bmp"))
                    Image.open(path).convert("RGB").save(bmp_path, "BMP")
                    path = bmp_path
                except ImportError:
                    pass 
            ctypes.windll.user32.SystemParametersInfoW(20, 0, str(path), 3)
            return f"Wallpaper set: {path.name}"

        elif _OS == "Darwin":
            script = (
                f'tell application "System Events" to tell every desktop to '
                f'set picture to POSIX file "{path}"'
            )
            error = _run_wallpaper_command(["osascript", "-e", script])
            if error:
                return f"Could not set wallpaper: {error}"
            return f"Wallpaper set: {path.name}"

        else:
            desktop_env = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
            uri = f"file://{path}"

            if "gnome" in desktop_env or "unity" in desktop_env:
                error = _run_wallpaper_command([
                    "gsettings", "set", "org.gnome.desktop.background",
                    "picture-uri", uri
                ])
                if error:
                    return f"Could not set wallpaper: {error}"
                error = _run_wallpaper_command([
                    "gsettings", "set", "org.gnome.desktop.background",
                    "picture-uri-dark", uri
                ])
                if error:
                    return f"Wallpaper changed, but dark-mode wallpaper could not be set: {error}"

            elif "kde" in desktop_env:
                # KDE Plasma
                script = f"""
var allDesktops = desktops();
for (var i = 0; i < allDesktops.length; i++) {{
    d = allDesktops[i];
    d.wallpaperPlugin = "org.kde.image";
    d.currentConfigGroup = ["Wallpaper", "org.kde.image", "General"];
    d.writeConfig("Image", "file://{path}");
}}
"""
                error = _run_wallpaper_command(
                    ["qdbus", "org.kde.plasmashell", "/PlasmaShell",
                     "org.kde.PlasmaShell.evaluateScript", script]
                )
                if error:
                    return f"Could not set wallpaper: {error}"

            elif "xfce" in desktop_env:
                error = _run_wallpaper_command([
                    "xfconf-query", "-c", "xfce4-desktop",
                    "-p", "/backdrop/screen0/monitor0/workspace0/last-image",
                    "-s", str(path)
                ])
                if error:
                    return f"Could not set wallpaper: {error}"

            else:
                error = _run_wallpaper_command(["feh", "--bg-scale", str(path)])
                if error:
                    return (
                        f"Could not set wallpaper automatically on {desktop_env}. "
                        f"Try manually or install 'feh'. Details: {error}"
                    )

            return f"Wallpaper set: {path.name}"

    except Exception as e:
        return f"Could not set wallpaper: {e}"


def set_wallpaper_from_url(url: str) -> str:
    try:
        import urllib.request
        suffix = Path(url.split("?")[0]).suffix or ".jpg"
        tmp    = Path(tempfile.mktemp(suffix=suffix))
        urllib.request.urlretrieve(url, str(tmp))
        result = set_wallpaper(str(tmp))
        try:
            tmp.unlink()
        except Exception:
            pass
        return result
    except Exception as e:
        return f"Could not download wallpaper: {e}"


def get_current_wallpaper() -> str:
    try:
        if _OS == "Windows":
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop"
            )
            val, _ = winreg.QueryValueEx(key, "Wallpaper")
            winreg.CloseKey(key)
            return f"Current wallpaper: {val}"

        elif _OS == "Darwin":
            script = (
                'tell application "System Events" to get picture of desktop 1'
            )
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True
            )
            return f"Current wallpaper: {result.stdout.strip()}"

        else:
            desktop_env = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
            if "gnome" in desktop_env or "unity" in desktop_env:
                result = subprocess.run(
                    ["gsettings", "get", "org.gnome.desktop.background", "picture-uri"],
                    capture_output=True, text=True
                )
                return f"Current wallpaper: {result.stdout.strip()}"
            return "Wallpaper path retrieval not supported for this desktop environment."

    except Exception as e:
        return f"Could not get wallpaper: {e}"

FILE_TYPE_MAP = {
    "Images":      {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".ico", ".heic"},
    "Documents":   {".pdf", ".doc", ".docx", ".txt", ".xls", ".xlsx",
                    ".ppt", ".pptx", ".csv", ".odt", ".ods", ".odp"},
    "Videos":      {".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".m4v"},
    "Music":       {".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a"},
    "Archives":    {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"},
    "Code":        {".py", ".js", ".ts", ".html", ".css", ".json", ".xml",
                    ".cpp", ".java", ".cs", ".go", ".rs", ".sh", ".php"},
    "Executables": {".exe", ".msi", ".bat", ".cmd", ".sh", ".appimage", ".deb", ".rpm"},
}

_SKIP_EXTENSIONS = {
    "Windows": {".lnk", ".url"},
    "Darwin":  {".webloc"},
    "Linux":   {".desktop"},
}


def organize_desktop(mode: str = "by_type") -> str:
    desktop       = _get_desktop()
    skip_exts     = _SKIP_EXTENSIONS.get(_OS, set())
    moved, skipped = [], []

    for item in desktop.iterdir():
        if item.is_dir() or item.name.startswith("."):
            continue
        if item.suffix.lower() in skip_exts:
            continue

        if mode == "by_date":
            mtime       = datetime.fromtimestamp(item.stat().st_mtime)
            folder_name = mtime.strftime("%Y-%m")
        else:
            ext         = item.suffix.lower()
            folder_name = "Others"
            for folder, exts in FILE_TYPE_MAP.items():
                if ext in exts:
                    folder_name = folder
                    break

        target_dir = desktop / folder_name
        target_dir.mkdir(exist_ok=True)
        new_path = target_dir / item.name

        if new_path.exists():
            skipped.append(item.name)
            continue

        shutil.move(str(item), str(new_path))
        moved.append(f"{item.name} → {folder_name}/")

    result = f"Desktop organized ({mode}): {len(moved)} files moved."
    if moved:
        result += "\n" + "\n".join(moved[:8])
        if len(moved) > 8:
            result += f"\n... and {len(moved) - 8} more."
    if skipped:
        result += f"\n{len(skipped)} file(s) skipped (name conflict)."
    return result


def list_desktop() -> str:
    desktop = _get_desktop()
    items   = []
    for item in sorted(desktop.iterdir()):
        if item.name.startswith("."):
            continue
        if item.is_dir():
            try:
                count = len(list(item.iterdir()))
            except PermissionError:
                count = "?"
            items.append(f"📁 {item.name}/ ({count} items)")
        else:
            size     = item.stat().st_size
            size_str = (
                f"{size / 1024:.1f} KB" if size < 1024 * 1024
                else f"{size / 1024 / 1024:.1f} MB"
            )
            items.append(f"📄 {item.name} ({size_str})")

    if not items:
        return "Desktop is empty."
    return f"Desktop ({len(items)} items):\n" + "\n".join(items)


def clean_desktop() -> str:
    desktop     = _get_desktop()
    skip_exts   = _SKIP_EXTENSIONS.get(_OS, set())
    today       = datetime.now().strftime("%Y-%m-%d")
    archive_dir = desktop / f"Desktop Archive {today}"
    archive_dir.mkdir(exist_ok=True)

    moved = 0
    for item in desktop.iterdir():
        if item.is_dir() or item.name.startswith("."):
            continue
        if item.suffix.lower() in skip_exts:
            continue
        new_path = archive_dir / item.name
        if not new_path.exists():
            shutil.move(str(item), str(new_path))
            moved += 1

    return f"Desktop cleaned: {moved} files archived to '{archive_dir.name}'."


def get_desktop_stats() -> str:
    desktop    = _get_desktop()
    files      = [i for i in desktop.iterdir() if i.is_file()]
    folders    = [i for i in desktop.iterdir() if i.is_dir()]
    total_size = sum(f.stat().st_size for f in files if f.exists())
    size_str   = (
        f"{total_size / 1024:.1f} KB" if total_size < 1024 * 1024
        else f"{total_size / 1024 / 1024:.1f} MB"
    )
    return (
        f"Desktop stats ({_OS}):\n"
        f"  Files   : {len(files)}\n"
        f"  Folders : {len(folders)}\n"
        f"  Size    : {size_str}\n"
        f"  Path    : {desktop}"
    )

def desktop_control(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """
    parameters:
        action : wallpaper | wallpaper_url | current_wallpaper |
                 organize  | clean | list | stats |
                 task (AI-powered)
        path   : image path for 'wallpaper'
        url    : image URL for 'wallpaper_url'
        mode   : 'by_type' or 'by_date' for 'organize'
        task   : natural language description for AI-powered actions
    """
    params = parameters or {}
    action = params.get("action", "").lower().strip()
    task   = params.get("task", "").strip()

    if player:
        player.write_log(f"[desktop] {action or task[:40]}")

    try:
        if action == "wallpaper":
            path = params.get("path", "")
            if path and not _confirm_desktop_action(
                    "set wallpaper", f"Image file: {path}\nThis changes the desktop wallpaper."):
                return "Cancelled: the wallpaper was not changed."
            return set_wallpaper(path) if path else "No image path provided."

        elif action == "wallpaper_url":
            url = params.get("url", "")
            if url and not _confirm_desktop_action(
                    "download and set wallpaper",
                    f"Image URL: {url}\nAether will download this image and set it as the desktop wallpaper."):
                return "Cancelled: no image was downloaded or applied."
            return set_wallpaper_from_url(url) if url else "No URL provided."

        elif action == "current_wallpaper":
            return get_current_wallpaper()

        elif action == "organize":
            mode = params.get("mode", "by_type")
            mode_description = "modified month" if mode == "by_date" else "file type"
            if not _confirm_desktop_action(
                    "organize desktop files",
                    f"Desktop: {_get_desktop()}\nOrganization: {mode_description}\n"
                    "Aether will move non-hidden top-level files into folders. File contents are not deleted."):
                return "Cancelled: no desktop files were moved."
            return organize_desktop(mode)

        elif action == "clean":
            if not _confirm_desktop_action(
                    "archive desktop files",
                    f"Desktop: {_get_desktop()}\n"
                    "Aether will move non-hidden top-level files into a dated Desktop Archive folder. "
                    "File contents are not deleted."):
                return "Cancelled: no desktop files were moved."
            return clean_desktop()

        elif action == "list":
            return list_desktop()

        elif action == "stats":
            return get_desktop_stats()

        elif action == "task" or task:
            actual_task = task or params.get("description", "")
            if not actual_task:
                return "Please describe what you want to do on the desktop."

            print(f"[Desktop] Asking Gemini: {actual_task}")
            if player:
                player.write_log("[Desktop] Generating action...")

            code = _ask_gemini_for_desktop_action(actual_task)
            return _execute_generated_code(code, player=player)

        else:
            if action:
                code = _ask_gemini_for_desktop_action(action)
                return _execute_generated_code(code, player=player)
            return "No action or task specified."

    except Exception as e:
        print(f"[Desktop] Error: {e}")
        return f"Desktop control error: {e}"
