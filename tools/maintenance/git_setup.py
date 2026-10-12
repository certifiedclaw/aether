"""
One-time helper for getting this checkout properly connected to
https://github.com/certifiedclaw/aether, so publish.py has something to
commit/push to.

Why this exists: zip/file-transfer round-trips (e.g. handing a project to
an LLM chat and getting files back) don't carry `.git` history with them —
by design, since `.git` isn't meant to travel that way and can be large/
messy to zip. That means a checkout can look completely fine (imports
work, app runs, reports generate) while `publish.py` silently no-ops
forever with "Not a git repository." This script detects that and fixes it
in one step, or tells you exactly what's wrong if it can't.

Usage:
    python -m tools.maintenance.git_setup
    python -m tools.maintenance.git_setup --remote https://github.com/someone/else.git
"""
from __future__ import annotations

import argparse
import subprocess

from tools.maintenance import REPO_ROOT

DEFAULT_REMOTE = "https://github.com/certifiedclaw/aether.git"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=30)


def main():
    parser = argparse.ArgumentParser(description="Ensure this checkout is a git repo pointed at the right remote")
    parser.add_argument("--remote", default=DEFAULT_REMOTE, help=f"Remote URL to use (default: {DEFAULT_REMOTE})")
    args = parser.parse_args()

    git_dir = REPO_ROOT / ".git"

    if not git_dir.exists():
        print(f"No .git found in {REPO_ROOT} — this is expected after unzipping a")
        print("file hand-off (git history doesn't travel in zips). Initializing...")
        init = _run(["git", "init"])
        if init.returncode != 0:
            print(f"git init failed: {init.stderr.strip()}")
            raise SystemExit(1)
        print(init.stdout.strip() or "Initialized empty git repository.")

    remote_check = _run(["git", "remote", "get-url", "origin"])
    if remote_check.returncode != 0:
        add = _run(["git", "remote", "add", "origin", args.remote])
        if add.returncode != 0:
            print(f"Could not add remote: {add.stderr.strip()}")
            raise SystemExit(1)
        print(f"Added remote origin -> {args.remote}")
    elif remote_check.stdout.strip() != args.remote:
        current = remote_check.stdout.strip()
        set_url = _run(["git", "remote", "set-url", "origin", args.remote])
        if set_url.returncode != 0:
            print(f"Could not update remote: {set_url.stderr.strip()}")
            raise SystemExit(1)
        print(f"Updated remote origin: {current} -> {args.remote}")
    else:
        print(f"Remote origin already correct: {args.remote}")

    print()
    print("Next steps if this is a genuinely fresh checkout (no prior commits):")
    print("  1. git fetch origin")
    print("  2. git branch --set-upstream-to=origin/master master   (or the branch GitHub shows as default)")
    print("  3. git pull --allow-unrelated-histories   (only if this checkout has no history yet)")
    print()
    print("Once `git push` works normally by hand once, tools/maintenance/publish.py")
    print("(and the auto-push on app launch) will work too — they use the same")
    print("credentials your normal git already has configured.")


if __name__ == "__main__":
    main()
