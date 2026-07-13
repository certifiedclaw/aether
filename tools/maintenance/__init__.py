"""
Aether Maintenance Toolkit
===========================

A self-contained set of scripts that keep Aether's project documentation
and health metrics current *automatically*, instead of relying on hand
edited files like IMPROVEMENTS_V2.md ... V6.md that go stale.

Run everything with:

    python -m tools.maintenance.run_all

Or run a single piece:

    python -m tools.maintenance.project_state   # -> PROJECT_STATE.md
    python -m tools.maintenance.health_check     # -> HEALTH_REPORT.md
    python -m tools.maintenance.dep_watcher      # -> DEPENDENCY_REPORT.md
    python -m tools.maintenance.roadmap list     # -> prints roadmap
    python -m tools.maintenance.roadmap render   # -> ROADMAP.md

All reports are regenerated in place each run (they are build artifacts,
not hand-maintained files) except roadmap.json, which is the one piece of
hand-curated state and is only changed via the roadmap.py CLI.
"""

from pathlib import Path
import re

# Repo root = two levels up from this file (tools/maintenance/__init__.py -> aether/)
REPO_ROOT = Path(__file__).resolve().parents[2]
MAINT_DATA_DIR = Path(__file__).resolve().parent / "data"
MAINT_DATA_DIR.mkdir(exist_ok=True)

_VOLATILE_LINE_RE = re.compile(r"^(Generated:|Last full run:).*$", re.MULTILINE)


def _normalize(text: str) -> str:
    return _VOLATILE_LINE_RE.sub("", text)


def write_if_changed(path: Path, content: str) -> bool:
    """Writes content to path only if it differs from what's already there,
    ignoring volatile 'Generated: ...' timestamp lines.

    Every report has a timestamp line that changes on every run, which would
    otherwise mean every single run (including the one on every app launch)
    produces a git diff and a new commit even when nothing substantive
    changed. Returns True if the file was (re)written, False if left as-is.
    """
    if path.exists():
        try:
            if _normalize(path.read_text(encoding="utf-8")) == _normalize(content):
                return False
        except Exception:
            pass
    path.write_text(content, encoding="utf-8")
    return True
