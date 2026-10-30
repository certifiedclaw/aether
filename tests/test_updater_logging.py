"""Roadmap #48 follow-up: the updater's logging. Real git repos, real subprocesses."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from core import updater as up

PASS = [sys.executable, "-c", "pass"]


def git(cwd, *args):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    assert r.returncode == 0, f"git {args}: {r.stderr}"
    return r.stdout.strip()


@pytest.fixture(autouse=True)
def _identity(monkeypatch):
    for k, v in (("NAME", "Test"), ("EMAIL", "t@example.com")):
        monkeypatch.setenv(f"GIT_AUTHOR_{k}", v)
        monkeypatch.setenv(f"GIT_COMMITTER_{k}", v)


@pytest.fixture
def repos(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    dev = tmp_path / "dev"
    subprocess.run(["git", "clone", "-q", str(remote), str(dev)], check=True, capture_output=True)
    git(dev, "checkout", "-q", "-b", "main")
    (dev / "app.py").write_text("V = 1\n")
    git(dev, "add", "-A")
    git(dev, "commit", "-q", "-m", "v1")
    git(dev, "push", "-q", "origin", "main")
    user = tmp_path / "user"
    subprocess.run(["git", "clone", "-q", str(remote), str(user)], check=True, capture_output=True)
    data = tmp_path / "data"
    return type("R", (), {"dev": dev, "user": user, "log": data / "update_log.jsonl",
                          "runs": data / "update_runs"})


def publish(dev, content="V = 2\n", path="app.py"):
    (dev / path).write_text(content)
    git(dev, "add", "-A")
    git(dev, "commit", "-q", "-m", "next")
    git(dev, "push", "-q", "origin", "main")


def events(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines()]


def names(path):
    return [e["event"] for e in events(path)]


def py(code):
    return [sys.executable, "-c", code]


# ---------------- what gets logged ----------------

def test_check_is_logged_too(repos):
    publish(repos.dev)
    r = up.check(repos.user, log_path=repos.log)
    assert r.run_id and Path(r.log_file).exists()
    row = json.loads(repos.log.read_text().splitlines()[-1])
    assert row["action"] == "check" and row["status"] == "available" and row["behind"] == 1
    assert names(r.log_file)[0] == "started" and names(r.log_file)[-1] == "finished"


def test_apply_event_timeline_and_timings(repos):
    publish(repos.dev)
    r = up.apply(repos.user, test_cmd=PASS, log_path=repos.log)
    got = names(r.log_file)
    for step in ("started", "repo_state", "fetch_start", "compared", "plan", "merge_start",
                 "merge_done", "tests_start", "tests_done", "finished"):
        assert step in got
    assert got.index("merge_done") < got.index("tests_start") < got.index("tests_done") < got.index("finished")
    row = json.loads(repos.log.read_text().splitlines()[-1])
    assert {"fetch_ms", "merge_ms", "tests_ms"} <= set(row["timings"])
    assert row["v"] == 2 and row["duration_ms"] >= 0


def test_started_event_records_environment(repos):
    r = up.check(repos.user, log_path=repos.log)
    started = events(r.log_file)[0]
    assert started["python"] == sys.version.split()[0] and started["platform"]
    assert started["git"].startswith("git version") and started["remote_url"]


def test_failure_details_are_parsed_and_logged(repos):
    publish(repos.dev)
    code = ("print('collected 3 items')\n"
            "print('FAILED tests/a.py::test_one - AssertionError')\n"
            "print('ERROR tests/b.py::test_two - ImportError')\n"
            "print('=========== 1 failed, 1 error, 40 passed in 2.50s ===========')\n"
            "raise SystemExit(1)")
    r = up.apply(repos.user, test_cmd=py(code), log_path=repos.log)
    assert r.status == "rolled_back"
    assert r.failed_tests == ["tests/a.py::test_one", "tests/b.py::test_two"]
    assert r.test_summary == "1 failed, 1 error, 40 passed in 2.50s"
    row = json.loads(repos.log.read_text().splitlines()[-1])
    assert row["failed_tests"] == r.failed_tests and row["test_summary"] == r.test_summary
    done = [e for e in events(r.log_file) if e["event"] == "tests_done"][0]
    assert done["passed"] is False and done["exit_code"] == 1


def test_full_test_output_is_saved_not_just_a_tail(repos):
    publish(repos.dev)
    r = up.apply(repos.user, test_cmd=py("[print(f'line {i}') for i in range(500)]; raise SystemExit(1)"),
                 log_path=repos.log)
    saved = Path(r.test_log).read_text().splitlines()
    assert len(saved) == 500 and saved[0] == "line 0" and saved[-1] == "line 499"
    assert len(r.test_output_tail.splitlines()) == 15


def test_test_log_is_capped(repos, monkeypatch):
    monkeypatch.setattr(up, "TEST_LOG_MAX_BYTES", 2000)
    publish(repos.dev)
    r = up.apply(repos.user, test_cmd=py("[print('x' * 50) for i in range(500)]"), log_path=repos.log)
    text = Path(r.test_log).read_text()
    assert "output truncated" in text and len(text) < 3000


def test_stderr_is_captured_with_stdout(repos):
    publish(repos.dev)
    r = up.apply(repos.user, test_cmd=py("import sys; print('to-out'); print('to-err', file=sys.stderr); sys.exit(1)"),
                 log_path=repos.log)
    saved = Path(r.test_log).read_text()
    assert "to-out" in saved and "to-err" in saved


# ---------------- crash safety ----------------

def test_events_are_on_disk_while_tests_are_still_running(repos):
    """A killed process must still leave a record: nothing may wait for finish()."""
    publish(repos.dev)
    # The probe's own source is logged inside the events file (as test_cmd), so build the
    # words at run time instead of writing them literally, or it would match itself.
    probe = (f"import glob, sys\n"
             f"files = glob.glob({str(repos.runs)!r} + '/*.jsonl')\n"
             f"text = open(files[0]).read() if files else ''\n"
             f"ev = lambda *w: '\"event\": \"' + ''.join(w) + '\"'\n"
             f"ok = ev('merge', '_done') in text and ev('tests', '_start') in text and ev('fin', 'ished') not in text\n"
             f"sys.exit(0 if ok else 7)")
    assert up.apply(repos.user, test_cmd=py(probe), log_path=repos.log).status == "updated"


def test_interrupted_run_is_detected_once(repos):
    repos.runs.mkdir(parents=True)
    stale = repos.runs / "20250101-000000-dead.jsonl"
    stale.write_text(json.dumps({"event": "started", "action": "apply", "head": "abc12345"}) + "\n"
                     + json.dumps({"event": "merge_done"}) + "\n")
    old = time.time() - 10_000
    os.utime(stale, (old, old))
    seen = []
    r = up.check(repos.user, log_path=repos.log, echo=seen.append)
    assert any("20250101-000000-dead" in s and "never finished" in s for s in seen)
    assert events(stale)[-1]["status"] == "interrupted"
    rows = [json.loads(x) for x in repos.log.read_text().splitlines()]
    assert any(x["run_id"] == "20250101-000000-dead" and x["status"] == "interrupted" for x in rows)
    assert "previous_run_interrupted" in names(r.log_file)
    seen2 = []
    up.check(repos.user, log_path=repos.log, echo=seen2.append)
    assert not any("never finished" in s for s in seen2)   # reported only once


def test_recent_unfinished_run_is_not_called_interrupted(repos):
    repos.runs.mkdir(parents=True)
    live = repos.runs / "29990101-000000-live.jsonl"     # mtime = now: could still be running
    live.write_text(json.dumps({"event": "started"}) + "\n")
    seen = []
    up.check(repos.user, log_path=repos.log, echo=seen.append)
    assert not any("never finished" in s for s in seen)
    assert events(live)[-1]["event"] == "started"


def test_torn_last_line_does_not_break_reading(repos):
    r = up.check(repos.user, log_path=repos.log)
    with open(r.log_file, "a") as f:
        f.write('{"event": "half-written')
    row = up.find_run("latest", up.load_index(repos.log))
    assert "Timeline:" in up.format_run(row, repos.log)


# ---------------- Ctrl-C and crashes still roll back ----------------

def test_ctrl_c_during_tests_rolls_back(repos, monkeypatch):
    publish(repos.dev)
    before = git(repos.user, "rev-parse", "HEAD")

    def interrupt(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(up, "_run_tests", interrupt)
    try:
        r = up.apply(repos.user, test_cmd=PASS, log_path=repos.log)
    except KeyboardInterrupt:   # without this, a regression aborts the whole pytest session
        pytest.fail("Ctrl-C escaped the updater, leaving the unvalidated update in place")
    assert r.status == "rolled_back" and "Ctrl-C" in r.message
    assert git(repos.user, "rev-parse", "HEAD") == before
    assert "tests_aborted" in names(r.log_file) and "rollback_verified" in names(r.log_file)


def test_unexpected_error_after_merge_rolls_back(repos, monkeypatch):
    publish(repos.dev)
    before = git(repos.user, "rev-parse", "HEAD")
    monkeypatch.setattr(up, "_run_tests", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bug")))
    r = up.apply(repos.user, test_cmd=PASS, log_path=repos.log)
    assert r.status == "rolled_back" and "bug" in r.message
    assert git(repos.user, "rev-parse", "HEAD") == before


def test_crash_before_merge_is_logged_and_reported(repos, monkeypatch):
    monkeypatch.setattr(up, "_inspect", lambda *a, **k: (_ for _ in ()).throw(ValueError("kaboom")))
    r = up.apply(repos.user, test_cmd=PASS, log_path=repos.log)
    assert r.status == "error" and "kaboom" in r.message
    crashed = [e for e in events(r.log_file) if e["event"] == "crashed"][0]
    assert "kaboom" in crashed["error"] and "Traceback" in crashed["traceback"]
    assert json.loads(repos.log.read_text().splitlines()[-1])["status"] == "error"


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")
def test_timeout_kills_the_whole_test_process_tree(repos, tmp_path):
    publish(repos.dev)
    pidfile = tmp_path / "child.pid"
    code = (f"import subprocess, sys, time\n"
            f"c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            f"open({str(pidfile)!r}, 'w').write(str(c.pid))\n"
            f"time.sleep(60)")
    r = up.apply(repos.user, test_cmd=py(code), test_timeout=2, log_path=repos.log)
    assert r.status == "rolled_back" and "timed out" in r.message
    pid = int(pidfile.read_text())
    time.sleep(0.3)
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except FileNotFoundError:
        state = "gone"
    # 'Z' = killed but not yet reaped by its (orphan) parent; still-running would be R or S
    assert state in ("Z", "gone"), f"child survived the timeout in state {state}"


# ---------------- secrets ----------------

def test_credentials_in_remote_url_never_reach_any_log(repos):
    git(repos.user, "remote", "set-url", "origin", "https://alice:hunter2-token@127.0.0.1:9/nope.git")
    r = up.apply(repos.user, test_cmd=PASS, log_path=repos.log)
    assert r.status == "error"
    blob = repos.log.read_text() + "".join(p.read_text() for p in repos.runs.glob("*")) + r.message
    assert "hunter2-token" not in blob and "alice:" not in blob
    assert "***@" in events(r.log_file)[0]["remote_url"]
    assert any(e["event"] == "git_failed" for e in events(r.log_file))


def test_redact_helper():
    assert up._redact("fatal: https://u:p@github.com/x/y.git failed") == "fatal: https://***@github.com/x/y.git failed"
    assert up._redact("git@github.com:x/y.git") == "git@github.com:x/y.git"   # ssh form has no secret
    assert up._redact("") == "" and up._redact(None) == ""


# ---------------- live progress ----------------

def test_progress_lines_are_echoed_with_elapsed_time(repos):
    publish(repos.dev)
    seen = []
    up.apply(repos.user, test_cmd=PASS, log_path=repos.log, echo=seen.append)
    joined = "\n".join(seen)
    assert "Fetching origin/main" in joined and "Fast-forwarding" in joined and "Tests passed" in joined
    assert all(s.startswith("[") and "s]" in s for s in seen)


def test_test_output_streams_only_when_asked(repos):
    publish(repos.dev)
    quiet, loud = [], []
    up.apply(repos.user, test_cmd=py("print('marker-' + 'line')"), log_path=repos.log, echo=quiet.append)
    assert not any("marker-line" in s for s in quiet)
    git(repos.user, "reset", "-q", "--hard", "HEAD~1")
    up.apply(repos.user, test_cmd=py("print('marker-' + 'line')"), log_path=repos.log, echo=loud.append,
             stream_tests=True)
    assert any("| marker-line" in s for s in loud)


def test_heartbeat_while_tests_run_quietly(repos, monkeypatch):
    monkeypatch.setattr(up, "HEARTBEAT_S", 0.3)
    publish(repos.dev)
    seen = []
    up.apply(repos.user, test_cmd=py("import time; time.sleep(1.2)"), log_path=repos.log, echo=seen.append)
    assert any("still running" in s for s in seen)


# ---------------- retention ----------------

def test_old_runs_are_pruned(repos, monkeypatch):
    monkeypatch.setattr(up, "KEEP_RUNS", 3)
    repos.runs.mkdir(parents=True)
    for i in range(6):
        (repos.runs / f"2025010{i}-000000-aaaa.jsonl").write_text(json.dumps({"event": "finished"}) + "\n")
        (repos.runs / f"2025010{i}-000000-aaaa.tests.log").write_text("x")
    r = up.check(repos.user, log_path=repos.log)
    left = sorted({p.name.split(".")[0] for p in repos.runs.iterdir()})
    assert len(left) == 3 and r.run_id in left
    assert "20250100-000000-aaaa" not in left and not list(repos.runs.glob("20250100*"))


def test_index_rotates_keeping_one_previous_generation(repos, monkeypatch):
    monkeypatch.setattr(up, "INDEX_MAX_BYTES", 200)
    ids = [up.check(repos.user, log_path=repos.log).run_id for _ in range(3)]
    assert Path(str(repos.log) + ".1").exists()
    # bounded on purpose: the current file plus one backup, so the oldest run ages out
    assert [r["run_id"] for r in up.load_index(repos.log)] == ids[1:]
    assert repos.log.stat().st_size < 2000


def test_unwritable_log_dir_does_not_break_anything(repos, tmp_path):
    publish(repos.dev)
    blocker = tmp_path / "blocker"
    blocker.write_text("file, so mkdir under it fails")
    r = up.apply(repos.user, test_cmd=PASS, log_path=blocker / "sub" / "log.jsonl")
    assert r.status == "updated" and r.log_file == "" and r.test_log == ""


# ---------------- viewing ----------------

def test_format_runs_and_find_run(repos):
    assert "No update runs" in up.format_runs([])
    publish(repos.dev)
    a = up.check(repos.user, log_path=repos.log)
    b = up.apply(repos.user, test_cmd=PASS, log_path=repos.log)
    rows = up.load_index(repos.log)
    table = up.format_runs(rows)
    assert a.run_id in table and b.run_id in table and "updated" in table and "available" in table
    assert up.find_run("latest", rows)["run_id"] == b.run_id
    assert up.find_run(a.run_id, rows)["run_id"] == a.run_id
    assert up.find_run("nope", rows) is None and up.find_run("latest", []) is None
    common = os.path.commonprefix([a.run_id, b.run_id]) or a.run_id[:4]
    assert len(up.run_matches(common, rows)) == 2
    assert up.find_run(common, rows) is None          # ambiguous: refuse to guess


def test_format_run_shows_timeline_failures_and_test_output(repos):
    publish(repos.dev)
    r = up.apply(repos.user, test_cmd=py("print('FAILED tests/z.py::test_z - boom'); raise SystemExit(1)"),
                 log_path=repos.log)
    text = up.format_run(up.find_run(r.run_id, up.load_index(repos.log)), repos.log)
    assert "rolled_back" in text and "Timeline:" in text and "rollback_verified" in text
    assert "tests/z.py::test_z" in text and "Test output:" in text and "| FAILED tests/z.py::test_z - boom" in text
    assert "timings:" in text


def test_format_run_without_event_file(repos):
    r = up.check(repos.user, log_path=repos.log)
    Path(r.log_file).unlink()
    assert "no step-by-step file" in up.format_run(up.find_run("latest", up.load_index(repos.log)), repos.log)


def test_cli_log_commands(repos, monkeypatch, capsys):
    monkeypatch.setattr(up, "REPO_ROOT", repos.user)
    monkeypatch.setattr(up, "default_log_path", lambda: repos.log)
    publish(repos.dev)
    assert up.main(["apply", "--test-cmd", f"{sys.executable} -c pass"]) == 0
    out = capsys.readouterr().out
    assert "Fast-forwarding" in out and "Details: python -m core.updater log --run" in out
    assert up.main(["log"]) == 0 and "updated" in capsys.readouterr().out
    assert up.main(["log", "--run", "latest"]) == 0 and "Timeline:" in capsys.readouterr().out
    assert up.main(["log", "--run", "latest", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "updated"
    assert up.main(["log", "-n", "1", "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 1
    assert up.main(["log", "--run", "zzzz"]) == 1
    assert "No run matching" in capsys.readouterr().out
    up.main(["check"])
    capsys.readouterr()
    assert up.main(["log", "--run", "2026"]) == 1
    assert "ambiguous" in capsys.readouterr().out


def test_cli_failure_prints_failed_tests(repos, monkeypatch, capsys):
    monkeypatch.setattr(up, "REPO_ROOT", repos.user)
    monkeypatch.setattr(up, "default_log_path", lambda: repos.log)
    publish(repos.dev)
    cmd = f"{sys.executable} -c \"print('FAILED tests/q.py::test_q - x'); raise SystemExit(2)\""
    assert up.main(["apply", "--test-cmd", cmd]) == 1
    out = capsys.readouterr().out
    assert "Failed: tests/q.py::test_q" in out and "rolled back" in out
