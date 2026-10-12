"""
Scans the codebase for common maintenance smells and writes
HEALTH_REPORT.md + health_report.json (machine-readable, for run_all / CI).

Checks are intentionally simple, dependency-free heuristics (no external
linter required) so this keeps working even if the venv is broken:

  - TODO / FIXME / XXX comments (tracked so they don't get lost)
  - Files over a line-count threshold (candidates for splitting)
  - Functions over a line-count threshold (candidates for refactor)
  - Bare `except:` clauses (swallow-everything anti-pattern)
  - `print(` calls outside of scripts/tests (should probably be logging)
  - Files that fail to even parse (syntax errors)

Usage:
    python -m tools.maintenance.health_check
"""
from __future__ import annotations

import ast
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from tools.maintenance import REPO_ROOT, MAINT_DATA_DIR, write_if_changed

IGNORE_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", "data", "logs", "assets"}
FILE_LINE_LIMIT = 600
FUNC_LINE_LIMIT = 80
# Scripts where print() is expected/fine
PRINT_OK_FILES = {"main.py", "self_test.py", "evaluator.py", "dump.py", "code_dump.txt"}
TODO_RE = re.compile(r"#\s*(TODO|FIXME|XXX)\b[:\-]?\s*(.*)", re.IGNORECASE)


def _iter_py_files(root: Path):
    for path in root.rglob("*.py"):
        if any(part in IGNORE_DIRS for part in path.parts):
            continue
        yield path


def _iter_comments(text: str):
    """Yield (lineno, comment_text) for actual comment tokens only —
    a raw per-line regex also matches a bare '#' that happens to appear
    inside a string literal (this file's own '## TODO / FIXME / XXX'
    markdown heading string was tripping its own check that way)."""
    import io
    import tokenize as _tokenize
    try:
        tokens = _tokenize.generate_tokens(io.StringIO(text).readline)
        for tok in tokens:
            if tok.type == _tokenize.COMMENT:
                yield tok.start[0], tok.string
    except (_tokenize.TokenError, SyntaxError, IndentationError):
        return


def _check_file(path: Path, findings: dict):
    rel = str(path.relative_to(REPO_ROOT))
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        findings["unreadable"].append(f"{rel}: {e}")
        return

    lines = text.splitlines()
    if len(lines) > FILE_LINE_LIMIT:
        findings["large_files"].append({"file": rel, "lines": len(lines)})

    for lineno, comment in _iter_comments(text):
        m = TODO_RE.search(comment)
        if m:
            findings["todos"].append({"file": rel, "line": lineno, "tag": m.group(1).upper(), "text": m.group(2).strip()[:120]})

    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped == "except:" or stripped.startswith("except:  "):
            findings["bare_except"].append({"file": rel, "line": i})
        if path.name not in PRINT_OK_FILES and re.match(r"^\s*print\(", line) and "actions" not in path.parts:
            findings["print_calls"].append({"file": rel, "line": i})

    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        findings["syntax_errors"].append({"file": rel, "error": str(e)})
        return

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            span = (node.end_lineno or node.lineno) - node.lineno
            if span > FUNC_LINE_LIMIT:
                findings["large_functions"].append({"file": rel, "function": node.name, "lines": span, "at_line": node.lineno})


def run_health_check() -> dict:
    findings = {
        "todos": [], "large_files": [], "large_functions": [],
        "bare_except": [], "print_calls": [], "syntax_errors": [], "unreadable": [],
    }
    n_files = 0
    for f in _iter_py_files(REPO_ROOT):
        n_files += 1
        _check_file(f, findings)
    findings["_meta"] = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "files_scanned": n_files,
    }
    return findings


def render_report(findings: dict) -> str:
    meta = findings["_meta"]
    lines = []
    lines.append("# Aether — Health Report")
    lines.append("")
    lines.append("> **Auto-generated.** Rerun `python -m tools.maintenance.health_check` "
                  "(or `run_all`) to refresh. Fix things here, don't edit this file directly.")
    lines.append("")
    lines.append(f"Generated: {meta['generated']}")
    lines.append(f"Files scanned: {meta['files_scanned']}")
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append("| Check | Count |")
    lines.append("|---|---|")
    for key, label in [
        ("syntax_errors", "Files that fail to parse"),
        ("bare_except", "Bare `except:` clauses"),
        ("large_files", f"Files over {FILE_LINE_LIMIT} lines"),
        ("large_functions", f"Functions over {FUNC_LINE_LIMIT} lines"),
        ("print_calls", "Stray `print()` calls (outside scripts)"),
        ("todos", "TODO / FIXME / XXX comments"),
        ("unreadable", "Unreadable files"),
    ]:
        lines.append(f"| {label} | {len(findings[key])} |")
    lines.append("")

    if findings["syntax_errors"]:
        lines.append("## ⚠️ Syntax errors (fix first — these break imports)")
        for item in findings["syntax_errors"]:
            lines.append(f"- `{item['file']}`: {item['error']}")
        lines.append("")

    if findings["bare_except"]:
        lines.append("## Bare `except:` clauses")
        lines.append("Swallows all exceptions including `KeyboardInterrupt`/`SystemExit`. Prefer `except Exception:`.")
        for item in findings["bare_except"]:
            lines.append(f"- `{item['file']}:{item['line']}`")
        lines.append("")

    if findings["large_files"]:
        lines.append(f"## Files over {FILE_LINE_LIMIT} lines")
        for item in sorted(findings["large_files"], key=lambda x: -x["lines"]):
            lines.append(f"- `{item['file']}` — {item['lines']} lines")
        lines.append("")

    if findings["large_functions"]:
        lines.append(f"## Functions over {FUNC_LINE_LIMIT} lines")
        for item in sorted(findings["large_functions"], key=lambda x: -x["lines"]):
            lines.append(f"- `{item['file']}:{item['at_line']}` — `{item['function']}()` ({item['lines']} lines)")
        lines.append("")

    if findings["print_calls"]:
        lines.append("## Stray `print()` calls")
        lines.append("Found outside of scripts/tests — consider routing through `core/observability.py` or logging instead.")
        for item in findings["print_calls"][:50]:
            lines.append(f"- `{item['file']}:{item['line']}`")
        if len(findings["print_calls"]) > 50:
            lines.append(f"- ...and {len(findings['print_calls']) - 50} more")
        lines.append("")

    if findings["todos"]:
        lines.append("## TODO / FIXME / XXX")
        for item in findings["todos"]:
            suffix = f" — {item['text']}" if item["text"] else ""
            lines.append(f"- `[{item['tag']}]` `{item['file']}:{item['line']}`{suffix}")
        lines.append("")

    if not any(findings[k] for k in ("syntax_errors", "bare_except", "large_files", "large_functions", "print_calls", "todos", "unreadable")):
        lines.append("Nothing flagged. Codebase is clean by these checks.")
        lines.append("")

    return "\n".join(lines)


def main():
    findings = run_health_check()
    (MAINT_DATA_DIR / "health_report.json").write_text(json.dumps(findings, indent=2), encoding="utf-8")
    report = render_report(findings)
    out_path = REPO_ROOT / "HEALTH_REPORT.md"
    if write_if_changed(out_path, report):
        print(f"Wrote {out_path}")
    else:
        print(f"{out_path} unchanged")


if __name__ == "__main__":
    main()
