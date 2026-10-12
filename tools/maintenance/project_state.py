"""
Generates PROJECT_STATE.md — a single, always-current snapshot of the
Aether codebase: module layout, size, entry points, and recent history.

This replaces the old pattern of hand-written IMPROVEMENTS_V2..V6.md files
that describe a moment in time and then rot. PROJECT_STATE.md is a build
artifact: rerun this script and it reflects *right now*, not last month.

Usage:
    python -m tools.maintenance.project_state
"""
from __future__ import annotations

import ast
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from tools.maintenance import REPO_ROOT, write_if_changed

IGNORE_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    "data", "logs", "assets",
}

KNOWN_ENTRY_POINTS = [
    "main.py", "ui.py", "run.sh", "run.bat", "self_test.py", "evaluator.py",
]


def _iter_py_files(root: Path):
    for path in root.rglob("*.py"):
        if any(part in IGNORE_DIRS for part in path.parts):
            continue
        yield path


def _module_summary(path: Path) -> tuple[int, str | None, int, int]:
    """Returns (line_count, module_docstring_first_line, num_classes, num_functions)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return (0, None, 0, 0)
    lines = text.count("\n") + 1
    doc = None
    n_classes = n_funcs = 0
    try:
        tree = ast.parse(text)
        raw_doc = ast.get_docstring(tree)
        if raw_doc:
            doc = raw_doc.strip().splitlines()[0][:100]
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                n_classes += 1
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                n_funcs += 1
    except SyntaxError:
        doc = "(syntax error — could not parse)"
    return (lines, doc, n_classes, n_funcs)


def _git_recent(n: int = 8) -> list[str]:
    try:
        # Fetch extra and filter, since our own auto-publish commits would
        # otherwise show up here -> get baked into this file's content ->
        # count as "changed" next run -> trigger another auto-commit ->
        # infinite loop. Real work commits are what's worth surfacing.
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "log", f"-{n * 3}", "--pretty=format:%h %ad %s", "--date=short"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            lines = out.stdout.strip().splitlines()
            filtered = [l for l in lines if "chore: refresh maintenance reports" not in l]
            return filtered[:n]
    except Exception:
        pass
    return []


def _version_string() -> str:
    try:
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        from version import version_string  # type: ignore
        return version_string()
    except Exception:
        return "(version.py not found or unreadable)"


def build_state() -> str:
    by_dir: dict[str, list[Path]] = {}
    for f in _iter_py_files(REPO_ROOT):
        top = f.relative_to(REPO_ROOT).parts[0] if f.parent != REPO_ROOT else "."
        by_dir.setdefault(top, []).append(f)

    total_files = sum(len(v) for v in by_dir.values())
    total_lines = 0

    lines_out: list[str] = []
    lines_out.append("# Aether — Project State")
    lines_out.append("")
    lines_out.append("> **Auto-generated.** Do not hand-edit — rerun "
                      "`python -m tools.maintenance.project_state` (or "
                      "`run_all`) to refresh. This is meant to be the one "
                      "file a new session (human or Claude) reads first to "
                      "get oriented.")
    lines_out.append("")
    lines_out.append(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines_out.append(f"Version: {_version_string()}")
    lines_out.append("")

    lines_out.append("## Entry points")
    for ep in KNOWN_ENTRY_POINTS:
        p = REPO_ROOT / ep
        lines_out.append(f"- `{ep}`" + (" (present)" if p.exists() else " (missing)"))
    lines_out.append("")

    lines_out.append("## Module layout")
    lines_out.append("")
    lines_out.append("| Directory | .py files | Total lines |")
    lines_out.append("|---|---|---|")
    for d in sorted(by_dir):
        n_lines = sum(_module_summary(f)[0] for f in by_dir[d])
        total_lines += n_lines
        lines_out.append(f"| `{d}/` | {len(by_dir[d])} | {n_lines:,} |")
    lines_out.append(f"| **Total** | **{total_files}** | **{total_lines:,}** |")
    lines_out.append("")

    lines_out.append("## Modules with docstrings (top-level summary)")
    lines_out.append("")
    for d in sorted(by_dir):
        if d in (".",):
            continue
        lines_out.append(f"### `{d}/`")
        for f in sorted(by_dir[d]):
            n_lines, doc, n_classes, n_funcs = _module_summary(f)
            rel = f.relative_to(REPO_ROOT)
            desc = f" — {doc}" if doc else ""
            lines_out.append(f"- `{rel}` ({n_lines} lines, {n_classes} classes, {n_funcs} functions){desc}")
        lines_out.append("")

    recent = _git_recent()
    lines_out.append("## Recent history (git log)")
    if recent:
        for entry in recent:
            lines_out.append(f"- {entry}")
    else:
        lines_out.append("_No git history available in this checkout._")
    lines_out.append("")

    lines_out.append("## See also")
    lines_out.append("- `HEALTH_REPORT.md` — automated code health scan")
    lines_out.append("- `DEPENDENCY_REPORT.md` — outdated / risky dependency scan")
    lines_out.append("- `ROADMAP.md` — hand-curated backlog (edit via `tools/maintenance/roadmap.py`)")
    lines_out.append("")

    return "\n".join(lines_out)


def main():
    content = build_state()
    out_path = REPO_ROOT / "PROJECT_STATE.md"
    if write_if_changed(out_path, content):
        print(f"Wrote {out_path} ({len(content.splitlines())} lines)")
    else:
        print(f"{out_path} unchanged")


if __name__ == "__main__":
    main()
