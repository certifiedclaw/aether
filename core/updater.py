"""core/updater.py -- update from git with automatic rollback (roadmap #48).

    python -m core.updater check              # fetch and report; changes nothing
    python -m core.updater apply              # fast-forward, run tests, roll back on failure
    python -m core.updater apply --test-cmd "python -m pytest tests/test_core.py -q"

Never runs on its own; the owner starts it. Safety rules:

  * Only fast-forwards. A dirty tree, local commits, or a diverged branch are
    refused, so an update can never discard or overwrite your work.
  * Records the commit it started from. If the tests fail or time out after
    the fast-forward, it resets back to that commit. The reset is safe
    because a clean tracked tree was verified first, so nothing of yours can
    be lost by it.
  * Does not install packages. pip changes cannot be rolled back, so when
    requirements*.txt changed in the update it says so and leaves
    `pip install -r requirements.txt` to you.
  * Each attempt is appended as one JSON line to the update log.

Restart Aether after an update: running code is not reloaded.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GIT_TIMEOUT = 120
DEFAULT_TEST_TIMEOUT = 900
DEFAULT_TEST_CMD = [sys.executable, "-m", "pytest", "tests", "-q", "-x", "-p", "no:cacheprovider"]
_TAIL_LINES = 15


@dataclass
class UpdateResult:
    status: str                  # up_to_date | available | updated | rolled_back | refused | error
    message: str
    before: str = ""
    after: str = ""
    behind: int = 0
    changed_files: list = field(default_factory=list)
    deps_changed: bool = False
    test_output_tail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("up_to_date", "available", "updated")


def _git(root: Path, *args: str, timeout: int = GIT_TIMEOUT) -> subprocess.CompletedProcess:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")  # never hang on a credential prompt
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          text=True, timeout=timeout, env=env)


def _out(proc: subprocess.CompletedProcess) -> str:
    return (proc.stdout or "").strip()


def _err(proc: subprocess.CompletedProcess) -> str:
    lines = (proc.stderr or proc.stdout or "").strip().splitlines()
    return lines[-1][:200] if lines else f"exit {proc.returncode}"


def _short(sha: str) -> str:
    return sha[:8]


def default_log_path() -> Path:
    try:
        from core.data_home import data_path
        return Path(data_path("update_log.jsonl"))
    except Exception:
        return REPO_ROOT / "data" / "update_log.jsonl"


def _log(result: UpdateResult, log_path: Path | None) -> None:
    try:
        path = Path(log_path) if log_path else default_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        row = asdict(result)
        row["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:
        pass  # logging must never break an update or a rollback


def _is_dep_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.startswith("requirements") and name.endswith(".txt")


def _inspect(root: Path, remote: str):
    """Fetch and compare. Returns (result_if_stop, context). context holds
    branch/head/upstream/behind when the update may proceed."""
    if _git(root, "rev-parse", "--is-inside-work-tree").returncode != 0:
        return UpdateResult("error", f"{root} is not a git checkout. "
                            "Run `python -m tools.maintenance.git_setup` first."), None
    branch = _out(_git(root, "branch", "--show-current"))
    if not branch:
        return UpdateResult("refused", "HEAD is detached; check out a branch before updating."), None
    head = _out(_git(root, "rev-parse", "HEAD"))
    fetch = _git(root, "fetch", "--quiet", remote, branch)
    if fetch.returncode != 0:
        return UpdateResult("error", f"git fetch {remote} {branch} failed: {_err(fetch)}", before=head), None
    upstream = _out(_git(root, "rev-parse", "FETCH_HEAD"))
    counts = _git(root, "rev-list", "--left-right", "--count", f"{head}...{upstream}")
    try:
        ahead, behind = (int(x) for x in _out(counts).split())
    except ValueError:
        return UpdateResult("error", "could not compare with the remote branch.", before=head), None
    if ahead and behind:
        return UpdateResult("refused", f"{branch} has diverged from {remote} "
                            f"({ahead} local, {behind} remote commits). Update by hand.",
                            before=head, behind=behind), None
    if ahead:
        return UpdateResult("refused", f"{branch} has {ahead} local commit(s) not on {remote}; "
                            "nothing to fast-forward.", before=head), None
    if not behind:
        return UpdateResult("up_to_date", f"Already up to date ({_short(head)}).", before=head), None
    return None, {"branch": branch, "head": head, "upstream": upstream, "behind": behind}


def check(root: Path = REPO_ROOT, remote: str = "origin", log_path: Path | None = None) -> UpdateResult:
    """Fetch and report. Does not modify the working tree."""
    stop, ctx = _inspect(Path(root), remote)
    if stop:
        return stop
    files = _out(_git(Path(root), "diff", "--name-only", ctx["head"], ctx["upstream"])).splitlines()
    return UpdateResult("available", f"{ctx['behind']} new commit(s) on {remote}/{ctx['branch']}.",
                        before=ctx["head"], after=ctx["upstream"], behind=ctx["behind"],
                        changed_files=files, deps_changed=any(_is_dep_file(f) for f in files))


def apply(root: Path = REPO_ROOT, remote: str = "origin", test_cmd: list | None = None,
          test_timeout: int = DEFAULT_TEST_TIMEOUT, log_path: Path | None = None) -> UpdateResult:
    root = Path(root)
    result = _apply(root, remote, test_cmd or DEFAULT_TEST_CMD, test_timeout)
    _log(result, log_path)
    return result


def _apply(root: Path, remote: str, test_cmd: list, test_timeout: int) -> UpdateResult:
    stop, ctx = _inspect(root, remote)
    if stop:
        return stop
    # Tracked changes only: gitignored config/data and untracked files are not at risk.
    if _out(_git(root, "status", "--porcelain", "--untracked-files=no")):
        return UpdateResult("refused", "You have uncommitted changes to tracked files. "
                            "Commit or stash them first.", before=ctx["head"], behind=ctx["behind"])
    before, upstream = ctx["head"], ctx["upstream"]
    files = _out(_git(root, "diff", "--name-only", before, upstream)).splitlines()
    deps = any(_is_dep_file(f) for f in files)

    merge = _git(root, "merge", "--ff-only", upstream)
    if merge.returncode != 0:
        return UpdateResult("error", f"fast-forward failed, nothing changed: {_err(merge)}",
                            before=before, behind=ctx["behind"])

    tail, failure = _run_tests(root, test_cmd, test_timeout)
    if failure is None:
        msg = f"Updated {_short(before)} -> {_short(upstream)} ({ctx['behind']} commit(s)). Restart Aether."
        if deps:
            msg += " Dependencies changed: run `pip install -r requirements.txt`."
        return UpdateResult("updated", msg, before=before, after=upstream, behind=ctx["behind"],
                            changed_files=files, deps_changed=deps, test_output_tail=tail)

    reset = _git(root, "reset", "--hard", before)
    now = _out(_git(root, "rev-parse", "HEAD"))
    if reset.returncode != 0 or now != before:
        return UpdateResult("error", f"Tests failed ({failure}) AND the rollback did not complete: "
                            f"HEAD is {_short(now)}, expected {_short(before)}. Run "
                            f"`git reset --hard {before}` by hand.", before=before, after=now,
                            behind=ctx["behind"], test_output_tail=tail)
    return UpdateResult("rolled_back", f"Update failed its tests ({failure}); rolled back to "
                        f"{_short(before)}. Nothing was changed.", before=before, after=upstream,
                        behind=ctx["behind"], changed_files=files, deps_changed=deps,
                        test_output_tail=tail)


def _run_tests(root: Path, cmd: list, timeout: int):
    """Returns (output_tail, failure_reason). failure_reason is None on success."""
    try:
        proc = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raw = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return "\n".join(raw.strip().splitlines()[-_TAIL_LINES:]), f"timed out after {timeout}s"
    except OSError as e:
        return "", f"could not start the test command: {e}"
    tail = "\n".join((proc.stdout or proc.stderr or "").strip().splitlines()[-_TAIL_LINES:])
    return tail, (None if proc.returncode == 0 else f"exit code {proc.returncode}")


def main(argv: list | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m core.updater",
                                description="Update Aether from git, rolling back if tests fail.")
    p.add_argument("action", choices=["check", "apply"])
    p.add_argument("--remote", default="origin")
    p.add_argument("--test-cmd", help="command that must exit 0 for the update to stay "
                                      "(default: the full pytest suite)")
    p.add_argument("--test-timeout", type=int, default=DEFAULT_TEST_TIMEOUT)
    args = p.parse_args(argv)

    if args.action == "check":
        r = check(root=REPO_ROOT, remote=args.remote)
    else:
        cmd = shlex.split(args.test_cmd) if args.test_cmd else None
        r = apply(root=REPO_ROOT, remote=args.remote, test_cmd=cmd, test_timeout=args.test_timeout)
    print(r.message)
    if r.status in ("available", "updated", "rolled_back") and r.changed_files:
        shown = r.changed_files[:10]
        print("Files: " + ", ".join(shown) + (f" (+{len(r.changed_files) - 10} more)" if len(r.changed_files) > 10 else ""))
    if r.status == "available" and r.deps_changed:
        print("Note: requirements changed in this update.")
    if r.status == "rolled_back" and r.test_output_tail:
        print("--- last test output ---\n" + r.test_output_tail)
    return 0 if r.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
