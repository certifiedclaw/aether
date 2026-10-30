"""core/updater.py -- update from git with automatic rollback (roadmap #48).

    python -m core.updater check                 # fetch and report; changes nothing
    python -m core.updater apply                 # fast-forward, run tests, roll back on failure
    python -m core.updater apply -v              # ...and stream the test output live
    python -m core.updater apply --test-cmd "python -m pytest tests/test_core.py -q"
    python -m core.updater log                   # recent attempts
    python -m core.updater log --run latest      # one attempt in full: timeline + test output

Never runs on its own; the owner starts it. Safety rules:

  * Only fast-forwards. A dirty tree, local commits, or a diverged branch are
    refused, so an update can never discard or overwrite your work.
  * Records the commit it started from. If the tests fail, time out, cannot
    start, or you press Ctrl-C, it resets back to that commit. The reset is
    safe because a clean tracked tree was verified first.
  * Does not install packages. pip changes cannot be rolled back, so when
    requirements*.txt changed in the update it says so and leaves
    `pip install -r requirements.txt` to you.

Logging (everything stays on this machine, in your Aether data folder):

  * update_log.jsonl       one summary line per run -- `check` runs included
  * update_runs/<id>.jsonl  every step of a run, flushed as it happens, so a
                            crash or power loss still leaves a record
  * update_runs/<id>.tests.log  the complete test output, not just a tail

A run that never wrote its "finished" event (killed mid-update) is reported
as interrupted the next time the updater starts. Credentials embedded in a
remote URL are masked everywhere they could be logged.

Restart Aether after an update: running code is not reloaded.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import secrets
import shlex
import signal
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GIT_TIMEOUT = 120
DEFAULT_TEST_TIMEOUT = 900
DEFAULT_TEST_CMD = [sys.executable, "-m", "pytest", "tests", "-q", "-x", "-p", "no:cacheprovider"]

RUNS_DIRNAME = "update_runs"
KEEP_RUNS = 25                       # per-run files kept; older runs are pruned
INDEX_MAX_BYTES = 512 * 1024         # update_log.jsonl rotates to .1 past this
TEST_LOG_MAX_BYTES = 2 * 1024 * 1024
STALE_AFTER_S = DEFAULT_TEST_TIMEOUT + 300   # unfinished and older than this = interrupted
HEARTBEAT_S = 15
_TAIL_LINES = 15
_MAX_FAILED_LISTED = 20
_MAX_FILES_LOGGED = 50

_FAILED_RE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)")
_SUMMARY_RE = re.compile(r"^=*\s*(.*\b\d+\s+(?:passed|failed|errors?)\b.*\bin\s+[\d.]+s.*?)\s*=*$")
_URL_CREDS_RE = re.compile(r"(//)[^/@\s]+@")


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
    run_id: str = ""
    duration_s: float = 0.0
    failed_tests: list = field(default_factory=list)
    test_summary: str = ""
    log_file: str = ""           # this run's step-by-step event log
    test_log: str = ""           # this run's full test output (apply only)

    @property
    def ok(self) -> bool:
        return self.status in ("up_to_date", "available", "updated")


# ---------------------------------------------------------------- helpers

def _redact(text: str) -> str:
    """Mask credentials in URLs (https://user:token@host) before they reach a log."""
    return _URL_CREDS_RE.sub(r"\1***@", text or "")


def _git(root: Path, *args: str, timeout: int = GIT_TIMEOUT) -> subprocess.CompletedProcess:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")  # never hang on a credential prompt
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          text=True, timeout=timeout, env=env)


def _out(proc: subprocess.CompletedProcess) -> str:
    return (proc.stdout or "").strip()


def _err(proc: subprocess.CompletedProcess) -> str:
    lines = _redact((proc.stderr or proc.stdout or "")).strip().splitlines()
    return lines[-1][:200] if lines else f"exit {proc.returncode}"


def _short(sha: str) -> str:
    return sha[:8]


def _is_dep_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.startswith("requirements") and name.endswith(".txt")


def default_log_path() -> Path:
    try:
        from core.data_home import data_path
        return Path(data_path("update_log.jsonl"))
    except Exception:
        return REPO_ROOT / "data" / "update_log.jsonl"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------- run log

class RunLog:
    """One run's record. Every event is appended to its own file and flushed
    immediately, so the log survives a crash. Every failure to write is
    swallowed: logging must never break, or block the rollback of, an update."""

    def __init__(self, action: str, index_path: Path | None = None, echo=None):
        self.action = action
        self.index_path = Path(index_path) if index_path else default_log_path()
        self.runs_dir = self.index_path.parent / RUNS_DIRNAME
        self.run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
        self.events: list[dict] = []
        self.timings: dict[str, int] = {}
        self._echo = echo
        self._t0 = time.monotonic()
        self._fh = None
        self._open_failed = False

    @property
    def events_path(self) -> Path:
        return self.runs_dir / f"{self.run_id}.jsonl"

    @property
    def test_log_path(self) -> Path:
        return self.runs_dir / f"{self.run_id}.tests.log"

    def elapsed(self) -> float:
        return time.monotonic() - self._t0

    def say(self, message: str) -> None:
        if self._echo:
            try:
                self._echo(f"[{self.elapsed():6.1f}s] {message}")
            except Exception:
                pass

    def event(self, name: str, say: str | None = None, **fields) -> None:
        rec = {"ts": _now_iso(), "t_ms": int(self.elapsed() * 1000), "event": name, **fields}
        self.events.append(rec)
        if self._fh is None and not self._open_failed:
            try:
                self.runs_dir.mkdir(parents=True, exist_ok=True)
                self._fh = self.events_path.open("a", encoding="utf-8")
            except OSError:
                self._open_failed = True
        if self._fh is not None:
            try:
                self._fh.write(json.dumps(rec, default=str) + "\n")
                self._fh.flush()
            except (OSError, ValueError):
                pass
        if say:
            self.say(say)

    @contextmanager
    def phase(self, name: str):
        start = time.monotonic()
        try:
            yield
        finally:
            self.timings[f"{name}_ms"] = int((time.monotonic() - start) * 1000)

    def finish(self, result: UpdateResult) -> None:
        result.run_id = self.run_id
        result.duration_s = round(self.elapsed(), 2)
        result.log_file = str(self.events_path) if not self._open_failed else ""
        self.event("finished", status=result.status, message=_redact(result.message),
                   duration_ms=int(self.elapsed() * 1000), timings=self.timings)
        _append_index(self.index_path, _summary_row(self, result))
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None
        _prune(self.runs_dir, keep=KEEP_RUNS)


def _summary_row(log: RunLog, r: UpdateResult) -> dict:
    return {
        "v": 2, "ts": _now_iso(), "run_id": log.run_id, "action": log.action,
        "status": r.status, "message": _redact(r.message),
        "before": r.before, "after": r.after, "behind": r.behind,
        "files_changed": len(r.changed_files), "files": r.changed_files[:_MAX_FILES_LOGGED],
        "deps_changed": r.deps_changed, "duration_ms": int(r.duration_s * 1000),
        "timings": log.timings, "test_summary": r.test_summary,
        "failed_tests": r.failed_tests, "test_log": r.test_log,
        "test_output_tail": r.test_output_tail,
    }


def _append_index(path: Path, row: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > INDEX_MAX_BYTES:
            os.replace(path, Path(str(path) + ".1"))
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")
    except OSError:
        pass


def _prune(runs_dir: Path, keep: int) -> None:
    """Keep the newest `keep` runs' files (run ids sort chronologically)."""
    try:
        ids = sorted({p.name.split(".", 1)[0] for p in runs_dir.iterdir() if p.is_file()})
        for rid in ids[:-keep] if len(ids) > keep else []:
            for p in runs_dir.glob(f"{rid}.*"):
                p.unlink(missing_ok=True)
    except OSError:
        pass


def _read_jsonl(path: Path) -> list:
    rows = []
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue   # a torn last line from a crash must not hide the rest
    except OSError:
        pass
    return rows


def _reap_interrupted(log: RunLog) -> list:
    """Find earlier runs that never wrote 'finished' and are old enough that
    they cannot still be running. Marks each so it is only reported once."""
    reaped = []
    try:
        for p in sorted(log.runs_dir.glob("*.jsonl")):
            rid = p.name[:-len(".jsonl")]
            if rid == log.run_id or time.time() - p.stat().st_mtime < STALE_AFTER_S:
                continue
            events = _read_jsonl(p)
            if not events or events[-1].get("event") == "finished":
                continue
            started = next((e for e in events if e.get("event") == "started"), {})
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": _now_iso(), "event": "finished", "status": "interrupted",
                                    "message": "run never finished (process killed or machine lost power)"}) + "\n")
            _append_index(log.index_path, {
                "v": 2, "ts": _now_iso(), "run_id": rid, "action": started.get("action", "?"),
                "status": "interrupted", "message": "run never finished; check `git status` and `git log`",
                "before": started.get("head", ""), "after": "", "behind": 0, "files_changed": 0, "files": [],
                "deps_changed": False, "duration_ms": 0, "timings": {}, "test_summary": "",
                "failed_tests": [], "test_log": "", "test_output_tail": ""})
            reaped.append(rid)
    except OSError:
        pass
    return reaped


# ---------------------------------------------------------------- inspect / check

def _begin(log: RunLog, root: Path, remote: str, **extra) -> None:
    reaped = _reap_interrupted(log)
    git_ver = ""
    try:
        git_ver = _out(subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=10))
    except Exception:
        pass
    remote_url = _redact(_out(_git(root, "remote", "get-url", remote)))
    log.event("started", action=log.action, root=str(root), remote=remote, remote_url=remote_url,
              python=platform.python_version(), platform=platform.platform(), git=git_ver, **extra)
    for rid in reaped:
        log.event("previous_run_interrupted", run=rid,
                  say=f"WARNING: an earlier run ({rid}) never finished. "
                      f"Check `git status`; see `python -m core.updater log --run {rid}`.")


def _inspect(root: Path, remote: str, log: RunLog):
    """Fetch and compare. Returns (result_if_stop, context). context holds
    branch/head/upstream/behind when the update may proceed."""
    if _git(root, "rev-parse", "--is-inside-work-tree").returncode != 0:
        log.event("not_a_repo", root=str(root))
        return UpdateResult("error", f"{root} is not a git checkout. "
                            "Run `python -m tools.maintenance.git_setup` first."), None
    branch = _out(_git(root, "branch", "--show-current"))
    head = _out(_git(root, "rev-parse", "HEAD"))
    log.event("repo_state", branch=branch or "(detached)", head=head)
    if not branch:
        return UpdateResult("refused", "HEAD is detached; check out a branch before updating.", before=head), None
    log.event("fetch_start", say=f"Fetching {remote}/{branch}")
    with log.phase("fetch"):
        fetch = _git(root, "fetch", "--quiet", remote, branch)
    if fetch.returncode != 0:
        log.event("git_failed", cmd="fetch", stderr=_redact(fetch.stderr or "")[:1000])
        return UpdateResult("error", f"git fetch {remote} {branch} failed: {_err(fetch)}", before=head), None
    upstream = _out(_git(root, "rev-parse", "FETCH_HEAD"))
    counts = _git(root, "rev-list", "--left-right", "--count", f"{head}...{upstream}")
    try:
        ahead, behind = (int(x) for x in _out(counts).split())
    except ValueError:
        log.event("git_failed", cmd="rev-list", stderr=_redact(counts.stderr or "")[:1000])
        return UpdateResult("error", "could not compare with the remote branch.", before=head), None
    log.event("compared", ahead=ahead, behind=behind, local=head, remote_head=upstream,
              say=f"Compared with {remote}/{branch}: {behind} behind, {ahead} ahead")
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


def check(root: Path = REPO_ROOT, remote: str = "origin", log_path: Path | None = None,
          echo=None) -> UpdateResult:
    """Fetch and report. Does not modify the working tree."""
    root = Path(root)
    log = RunLog("check", log_path, echo)
    result = None
    try:
        _begin(log, root, remote)
        stop, ctx = _inspect(root, remote, log)
        if stop:
            result = stop
        else:
            files = _out(_git(root, "diff", "--name-only", ctx["head"], ctx["upstream"])).splitlines()
            result = UpdateResult("available", f"{ctx['behind']} new commit(s) on {remote}/{ctx['branch']}.",
                                  before=ctx["head"], after=ctx["upstream"], behind=ctx["behind"],
                                  changed_files=files, deps_changed=any(_is_dep_file(f) for f in files))
        return result
    except Exception as e:
        log.event("crashed", error=repr(e), traceback=traceback.format_exc()[-2000:])
        result = UpdateResult("error", f"unexpected error: {e}")
        return result
    finally:
        if result is not None:
            log.finish(result)


# ---------------------------------------------------------------- apply

def apply(root: Path = REPO_ROOT, remote: str = "origin", test_cmd: list | None = None,
          test_timeout: int = DEFAULT_TEST_TIMEOUT, log_path: Path | None = None,
          echo=None, stream_tests: bool = False) -> UpdateResult:
    root = Path(root)
    cmd = test_cmd or DEFAULT_TEST_CMD
    log = RunLog("apply", log_path, echo)
    result = None
    try:
        _begin(log, root, remote, test_cmd=shlex.join(cmd), test_timeout=test_timeout)
        result = _apply(root, remote, cmd, test_timeout, log, stream_tests)
        return result
    except Exception as e:
        log.event("crashed", error=repr(e), traceback=traceback.format_exc()[-2000:])
        result = UpdateResult("error", f"unexpected error: {e} (see {log.events_path})")
        return result
    finally:
        if result is not None:
            log.finish(result)


def _apply(root: Path, remote: str, cmd: list, test_timeout: int, log: RunLog, stream: bool) -> UpdateResult:
    stop, ctx = _inspect(root, remote, log)
    if stop:
        return stop
    dirty = _out(_git(root, "status", "--porcelain", "--untracked-files=no"))
    if dirty:
        log.event("refused_dirty", files=dirty.splitlines()[:20])
        return UpdateResult("refused", "You have uncommitted changes to tracked files. "
                            "Commit or stash them first.", before=ctx["head"], behind=ctx["behind"])
    before, upstream = ctx["head"], ctx["upstream"]
    files = _out(_git(root, "diff", "--name-only", before, upstream)).splitlines()
    deps = any(_is_dep_file(f) for f in files)
    log.event("plan", files_changed=len(files), files=files[:_MAX_FILES_LOGGED], deps_changed=deps,
              say=f"{ctx['behind']} commit(s), {len(files)} file(s) change"
                  + ("; requirements changed" if deps else ""))

    log.event("merge_start", before=before, target=upstream,
              say=f"Fast-forwarding {_short(before)} -> {_short(upstream)}")
    with log.phase("merge"):
        merge = _git(root, "merge", "--ff-only", upstream)
    if merge.returncode != 0:
        log.event("git_failed", cmd="merge --ff-only", stderr=_redact(merge.stderr or "")[:1000])
        return UpdateResult("error", f"fast-forward failed, nothing changed: {_err(merge)}",
                            before=before, behind=ctx["behind"])
    log.event("merge_done", head=_out(_git(root, "rev-parse", "HEAD")))

    # From here HEAD has moved: ANY exit that is not a passing test run must roll back.
    tr = _TestRun()
    try:
        with log.phase("tests"):
            tr = _run_tests(root, cmd, test_timeout, log, stream)
    except BaseException as e:   # includes KeyboardInterrupt: never leave unvalidated code in place
        tr.failure = "interrupted by user (Ctrl-C)" if isinstance(e, KeyboardInterrupt) else f"updater error: {e}"
        log.event("tests_aborted", error=repr(e))

    common = dict(before=before, behind=ctx["behind"], changed_files=files, deps_changed=deps,
                  test_output_tail=tr.tail, failed_tests=tr.failed, test_summary=tr.summary,
                  test_log=str(log.test_log_path) if tr.logged else "")
    if tr.failure is None:
        msg = f"Updated {_short(before)} -> {_short(upstream)} ({ctx['behind']} commit(s)). Restart Aether."
        if deps:
            msg += " Dependencies changed: run `pip install -r requirements.txt`."
        return UpdateResult("updated", msg, after=upstream, **common)

    log.event("rollback_start", reason=tr.failure, target=before,
              say=f"Rolling back to {_short(before)}")
    with log.phase("rollback"):
        reset = _git(root, "reset", "--hard", before)
    now = _out(_git(root, "rev-parse", "HEAD"))
    if reset.returncode != 0 or now != before:
        log.event("rollback_failed", head=now, expected=before, stderr=_redact(reset.stderr or "")[:1000])
        return UpdateResult("error", f"Tests failed ({tr.failure}) AND the rollback did not complete: "
                            f"HEAD is {_short(now)}, expected {_short(before)}. Run "
                            f"`git reset --hard {before}` by hand.", after=now, **{**common, "before": before})
    log.event("rollback_verified", head=now, say=f"Rolled back; HEAD is {_short(now)} again")
    return UpdateResult("rolled_back", f"Update failed its tests ({tr.failure}); rolled back to "
                        f"{_short(before)}. Nothing was changed.", after=upstream, **common)


@dataclass
class _TestRun:
    tail: str = ""
    failure: str | None = None       # None = passed
    failed: list = field(default_factory=list)
    summary: str = ""
    logged: bool = False


def _kill(proc: subprocess.Popen) -> None:
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)   # the test runner's children too
        else:
            proc.kill()
    except (OSError, ProcessLookupError):
        pass
    try:
        proc.wait(timeout=10)
    except Exception:
        pass


def _run_tests(root: Path, cmd: list, timeout: int, log: RunLog, stream: bool) -> _TestRun:
    """Run the test command, capturing ALL output to the run's test log while
    keeping a tail, the failed test ids and the summary line. Returns a
    _TestRun; failure is None only when the command exited 0."""
    tr = _TestRun()
    tail: deque = deque(maxlen=_TAIL_LINES)
    state = {"bytes": 0, "truncated": False}
    out_fh = None
    try:
        log.runs_dir.mkdir(parents=True, exist_ok=True)
        out_fh = log.test_log_path.open("w", encoding="utf-8")
        tr.logged = True
    except OSError:
        out_fh = None

    log.event("tests_start", cmd=shlex.join(cmd), timeout_s=timeout, say=f"Running tests: {shlex.join(cmd)} (timeout {timeout}s)")
    kwargs = {"start_new_session": True} if os.name == "posix" else {}
    try:
        proc = subprocess.Popen(cmd, cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", bufsize=1, **kwargs)
    except OSError as e:
        tr.failure = f"could not start the test command: {e}"
        log.event("tests_done", passed=False, reason=tr.failure)
        if out_fh:
            out_fh.close()
        return tr

    def pump():
        for line in proc.stdout:
            text = line.rstrip("\n")
            tail.append(text)
            m = _FAILED_RE.match(text)
            if m and len(tr.failed) < _MAX_FAILED_LISTED:
                tr.failed.append(m.group(1))
            s = _SUMMARY_RE.match(text.strip())
            if s:
                tr.summary = s.group(1).strip()
            if out_fh and not state["truncated"]:
                state["bytes"] += len(line)
                if state["bytes"] > TEST_LOG_MAX_BYTES:
                    state["truncated"] = True
                    out_fh.write("\n[output truncated: test log size limit reached]\n")
                else:
                    out_fh.write(line)
                out_fh.flush()
            if stream:
                log.say("  | " + text)

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout
    started = time.monotonic()
    try:
        while True:
            remaining = deadline - time.monotonic()
            try:
                proc.wait(timeout=max(0.05, min(HEARTBEAT_S, remaining)))
                break
            except subprocess.TimeoutExpired:
                if time.monotonic() >= deadline:
                    _kill(proc)
                    tr.failure = f"timed out after {timeout}s"
                    break
                if not stream:
                    log.say(f"  ...tests still running ({int(time.monotonic() - started)}s)")
    finally:
        if proc.poll() is None:      # Ctrl-C or error: never leave the runner behind
            _kill(proc)
        reader.join(timeout=5)
        if out_fh:
            try:
                out_fh.close()
            except OSError:
                pass
    tr.tail = "\n".join(tail)
    if tr.failure is None and proc.returncode != 0:
        tr.failure = f"exit code {proc.returncode}"
    log.event("tests_done", passed=tr.failure is None, reason=tr.failure, exit_code=proc.returncode,
              summary=tr.summary, failed_tests=tr.failed, duration_ms=int((time.monotonic() - started) * 1000),
              say=("Tests passed" if tr.failure is None else f"Tests failed: {tr.failure}")
                  + (f" - {tr.summary}" if tr.summary else ""))
    return tr


# ---------------------------------------------------------------- viewing the log

def load_index(path: Path | None = None) -> list:
    """All summary rows, oldest first (includes the rotated .1 file)."""
    p = Path(path) if path else default_log_path()
    return _read_jsonl(Path(str(p) + ".1")) + _read_jsonl(p)


def _fmt_ms(ms: int) -> str:
    return f"{ms}ms" if ms < 1000 else f"{ms / 1000:.1f}s"


def _fmt_when(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return (ts or "?")[:16]


def format_runs(rows: list) -> str:
    if not rows:
        return "No update runs logged yet."
    lines = [f"{'WHEN':<16}  {'RUN':<20}  {'ACTION':<6}  {'STATUS':<12}  {'TIME':>6}  DETAIL"]
    for r in rows:
        change = f"{_short(r.get('before', ''))}->{_short(r['after'])}" if r.get("after") and r.get("action") == "apply" \
            else (_short(r.get("before", "")) or "-")
        detail = r.get("test_summary") or r.get("message", "")
        lines.append(f"{_fmt_when(r.get('ts')):<16}  {r.get('run_id', '?'):<20}  {r.get('action', '?'):<6}  "
                     f"{r.get('status', '?'):<12}  {r.get('duration_ms', 0) / 1000:>5.1f}s  {change}  {detail}"[:220])
    return "\n".join(lines)


def run_matches(ref: str, rows: list) -> list:
    """Rows whose run id equals `ref`, else whose id starts with it."""
    exact = [r for r in rows if r.get("run_id") == ref]
    return exact or [r for r in rows if str(r.get("run_id", "")).startswith(ref)]


def find_run(ref: str, rows: list) -> dict | None:
    """'latest', a full run id, or a prefix. An ambiguous prefix returns None:
    quietly showing the wrong run in a diagnostic tool is worse than no answer."""
    if not rows:
        return None
    if ref in ("latest", "last", ""):
        return rows[-1]
    matches = run_matches(ref, rows)
    return matches[0] if len(matches) == 1 else None


def format_run(row: dict, index_path: Path | None = None, tail_lines: int = 30) -> str:
    p = Path(index_path) if index_path else default_log_path()
    rid = row.get("run_id", "")
    out = [f"Run {rid}  {row.get('action')}  ->  {row.get('status')}  ({row.get('duration_ms', 0) / 1000:.1f}s)",
           f"  {row.get('message', '')}"]
    if row.get("before"):
        out.append(f"  commits: {_short(row['before'])}" + (f" -> {_short(row['after'])}" if row.get("after") else ""))
    if row.get("timings"):
        out.append("  timings: " + ", ".join(f"{k[:-3]} {_fmt_ms(v)}" for k, v in row["timings"].items()))
    if row.get("test_summary"):
        out.append(f"  tests:   {row['test_summary']}")
    if row.get("failed_tests"):
        out.append("  failed:  " + ", ".join(row["failed_tests"][:10]))
    events = _read_jsonl(p.parent / RUNS_DIRNAME / f"{rid}.jsonl")
    if events:
        out.append("")
        out.append("Timeline:")
        skip = {"ts", "t_ms", "event", "traceback"}
        for e in events:
            extra = ", ".join(f"{k}={_short(v) if k in ('before', 'target', 'head', 'local', 'remote_head', 'expected') and isinstance(v, str) else v}"
                              for k, v in e.items() if k not in skip and v not in ("", None, [], {}))
            out.append(f"  +{e.get('t_ms', 0) / 1000:6.1f}s  {e.get('event', '?'):<26} {extra}"[:200])
    else:
        out.append("  (no step-by-step file for this run: it was pruned or never written)")
    tl = row.get("test_log")
    if tl and Path(tl).exists():
        out.append("")
        out.append(f"Test output: {tl}")
        try:
            body = Path(tl).read_text(encoding="utf-8", errors="replace").splitlines()
            out.append(f"  (last {min(tail_lines, len(body))} of {len(body)} lines)")
            out.extend("  | " + ln for ln in body[-tail_lines:])
        except OSError:
            pass
    return "\n".join(out)


# ---------------------------------------------------------------- CLI

def main(argv: list | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m core.updater",
                                description="Update Aether from git, rolling back if tests fail.")
    p.add_argument("action", choices=["check", "apply", "log"])
    p.add_argument("--remote", default="origin")
    p.add_argument("--test-cmd", help="command that must exit 0 for the update to stay "
                                      "(default: the full pytest suite)")
    p.add_argument("--test-timeout", type=int, default=DEFAULT_TEST_TIMEOUT)
    p.add_argument("-v", "--verbose", action="store_true", help="stream the test output live")
    p.add_argument("-n", type=int, default=10, help="log: how many recent runs to list")
    p.add_argument("--run", help="log: show one run in full (an id, a prefix, or 'latest')")
    p.add_argument("--json", action="store_true", help="log: machine-readable output")
    args = p.parse_args(argv)

    if args.action == "log":
        rows = load_index()
        if args.run:
            row = find_run(args.run, rows)
            if row is None:
                n = len(run_matches(args.run, rows))
                print(f"'{args.run}' is ambiguous: {n} runs match. Use more of the id." if n > 1
                      else f"No run matching '{args.run}'.")
                return 1
            print(json.dumps(row, indent=2) if args.json else format_run(row))
        else:
            shown = rows[-max(1, args.n):]
            print(json.dumps(shown, indent=2) if args.json else format_runs(shown))
        return 0

    echo = print
    if args.action == "check":
        r = check(root=REPO_ROOT, remote=args.remote, echo=echo)
    else:
        cmd = shlex.split(args.test_cmd) if args.test_cmd else None
        r = apply(root=REPO_ROOT, remote=args.remote, test_cmd=cmd, test_timeout=args.test_timeout,
                  echo=echo, stream_tests=args.verbose)
    print(r.message)
    if r.status in ("available", "updated", "rolled_back") and r.changed_files:
        shown = r.changed_files[:10]
        print("Files: " + ", ".join(shown) + (f" (+{len(r.changed_files) - 10} more)" if len(r.changed_files) > 10 else ""))
    if r.status == "available" and r.deps_changed:
        print("Note: requirements changed in this update.")
    if r.status == "rolled_back":
        if r.failed_tests:
            print("Failed: " + ", ".join(r.failed_tests[:10]))
        if r.test_output_tail and not args.verbose:
            print("--- last test output ---\n" + r.test_output_tail)
    if r.run_id:
        print(f"Run {r.run_id} ({r.duration_s:.1f}s). Details: python -m core.updater log --run {r.run_id}")
    return 0 if r.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
