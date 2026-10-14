"""
Commits the generated maintenance reports (and roadmap.json) to git and
pushes to origin, so https://github.com/certifiedclaw/aether always shows
current PROJECT_STATE.md / HEALTH_REPORT.md / DEPENDENCY_REPORT.md /
ROADMAP.md / MAINTENANCE.md — not just whatever was true at the last manual
commit.

IMPORTANT: this uses *your* local git credentials (whatever `git push`
already uses on your machine — SSH key, credential manager, etc). It does
not contain, request, or store any token/password itself. If `git push`
would normally ask you to authenticate, it will still ask here, or fail
with a normal git auth error if run non-interactively (e.g. from the
in-app auto-snapshot, which passes push=False by default for exactly this
reason — see main.py).

Usage:
    python -m tools.maintenance.publish                # commit + push
    python -m tools.maintenance.publish --no-push       # commit only
    python -m tools.maintenance.publish --message "..."  # custom commit message
"""
from __future__ import annotations

import argparse
import subprocess
from datetime import datetime, timezone

from tools.maintenance import REPO_ROOT

TRACKED_FILES = [
    "PROJECT_STATE.md",
    "HEALTH_REPORT.md",
    "DEPENDENCY_REPORT.md",
    "ROADMAP.md",
    "HANDOFF.md",
    "MAINTENANCE.md",
    "tools/maintenance/data/roadmap.json",
    "tools/maintenance/data/handoff.json",
]


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=30)


def _is_git_repo() -> bool:
    return (REPO_ROOT / ".git").exists()


def commit_and_push(message: str | None = None, push: bool = True) -> tuple[bool, str]:
    """Returns (success, human-readable status message). Never raises."""
    if not _is_git_repo():
        return False, (
            "Not a git repository — skipping commit/push. "
            "If this checkout came from a zip/file hand-off (git history doesn't "
            "travel that way), run `python -m tools.maintenance.git_setup` once to fix it."
        )

    existing = [f for f in TRACKED_FILES if (REPO_ROOT / f).exists()]
    if not existing:
        return False, "No maintenance reports found to commit (run run_all first)."

    add = _run(["git", "add"] + existing)
    if add.returncode != 0:
        return False, f"git add failed: {add.stderr.strip()}"

    diff = _run(["git", "diff", "--cached", "--quiet", "--"] + existing)
    if diff.returncode == 0:
        return True, "No changes to maintenance reports since last commit — nothing to push."

    msg = message or f"chore: refresh maintenance reports ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')})"
    commit = _run(["git", "commit", "--only", "-m", msg, "--"] + existing)
    if commit.returncode != 0:
        return False, f"git commit failed: {commit.stderr.strip()}"

    if not push:
        return True, f"Committed locally: {msg}"

    push_res = _run(["git", "push", "origin", "HEAD"])
    if push_res.returncode != 0:
        return False, (
            f"Committed locally but push failed (this is normal if you're not "
            f"authenticated for {_origin_url()} in this environment):\n{push_res.stderr.strip()}"
        )
    return True, f"Committed and pushed: {msg}"


def _origin_url() -> str:
    res = _run(["git", "remote", "get-url", "origin"])
    return res.stdout.strip() if res.returncode == 0 else "(no origin configured)"


def main():
    parser = argparse.ArgumentParser(description="Commit + push maintenance reports")
    parser.add_argument("--no-push", action="store_true", help="Commit locally only, don't push")
    parser.add_argument("--message", default=None, help="Custom commit message")
    args = parser.parse_args()

    ok, status = commit_and_push(message=args.message, push=not args.no_push)
    print(status)
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
