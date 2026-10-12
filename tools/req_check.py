"""tools/req_check.py -- which packages declared in requirements.txt are not installed?

Used by tools/verify_local.py. A missing declared package used to be invisible:
watchdog (plugin hot-reload) and keyboard (global hotkey) were absent for several
verify runs and the only symptom was a log line about a disabled feature.

Pure functions, no network. Platform markers are honoured, so win32-only
packages are not reported as missing on Linux/macOS.
"""
from __future__ import annotations

from importlib import metadata
from pathlib import Path

try:  # packaging ships with pip; if absent we say so instead of guessing
    from packaging.requirements import InvalidRequirement, Requirement
except Exception:  # noqa: BLE001
    Requirement = None  # type: ignore[assignment]
    InvalidRequirement = Exception  # type: ignore[assignment,misc]


def declared_packages(text: str) -> list[str] | None:
    """Package names declared in requirements text that apply to THIS platform.

    Returns None when `packaging` is unavailable. Ignores blank lines, comments,
    inline comments, pip options (-r, -e, --index-url ...) and URL/path lines.
    Extras ("uvicorn[standard]") and version specifiers are stripped.
    """
    if Requirement is None:
        return None
    names: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.split(" #", 1)[0].split("\t#", 1)[0].strip()
        if not line or line.startswith(("#", "-")) or "://" in line:
            continue
        try:
            req = Requirement(line)
        except InvalidRequirement:
            continue
        if req.marker is not None and not req.marker.evaluate():
            continue
        key = req.name.lower().replace("_", "-")
        if key not in seen:
            seen.add(key)
            names.append(req.name)
    return names


# Optional integrations: a missing one is reported as info, not WARN, because many
# installs deliberately skip them (edit this tuple to taste). Prefix match, lowercase.
OPTIONAL_PREFIXES = (
    "langchain", "llama-index", "google-generativeai", "google-genai",
    "wikipedia-api", "qrcode",
)


def is_optional(name: str) -> bool:
    return name.lower().replace("_", "-").startswith(OPTIONAL_PREFIXES)


def _is_installed(name: str) -> bool:
    try:
        metadata.version(name)
        return True
    except metadata.PackageNotFoundError:
        return False


def missing_requirements(path: Path, installed=_is_installed) -> tuple[list[str], int] | None:
    """(missing package names, number declared), or None if it can't be checked."""
    try:
        names = declared_packages(Path(path).read_text(encoding="utf-8"))
    except OSError:
        return None
    if names is None:
        return None
    return [n for n in names if not installed(n)], len(names)


def render(result: tuple[list[str], int] | None) -> list[str]:
    """Report lines for verify_report.txt."""
    if result is None:
        return ["  (could not check: requirements.txt unreadable or 'packaging' missing)"]
    missing, total = result
    core = [n for n in missing if not is_optional(n)]
    optional = [n for n in missing if is_optional(n)]
    lines: list[str] = []
    if core:
        lines += [
            f"  WARN: {len(core)} package(s) declared in requirements.txt are NOT installed:",
            f"        {', '.join(core)}",
            "        fix: python -m pip install -r requirements.txt",
        ]
    elif optional:
        lines.append(f"  ok: all {total - len(optional)} core declared packages are installed")
    else:
        lines.append(f"  ok: all {total} declared packages are installed")
    if optional:
        lines.append(f"  info: optional integrations not installed (fine if unused): {', '.join(optional)}")
    return lines
