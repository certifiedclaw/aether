"""Roadmap #48: core/updater -- fast-forward update with rollback.

Uses real git: a bare "remote", a "dev" clone that publishes commits, and a
"user" clone that the updater runs against. Nothing about git is mocked.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from core import updater as up

PASS = [sys.executable, "-c", "pass"]
FAIL = [sys.executable, "-c", "import sys; print('boom: 1 failed'); sys.exit(1)"]


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
    (dev / "app.py").write_text("VERSION = 1\n")
    (dev / "requirements.txt").write_text("requests\n")
    git(dev, "add", "-A")
    git(dev, "commit", "-q", "-m", "v1")
    git(dev, "push", "-q", "origin", "main")
    user = tmp_path / "user"
    subprocess.run(["git", "clone", "-q", str(remote), str(user)], check=True, capture_output=True)
    return type("R", (), {"dev": dev, "user": user, "log": tmp_path / "update_log.jsonl"})


def publish(dev, content="VERSION = 2\n", path="app.py", msg="v2"):
    (dev / path).write_text(content)
    git(dev, "add", "-A")
    git(dev, "commit", "-q", "-m", msg)
    git(dev, "push", "-q", "origin", "main")


# ---------------- check ----------------

def test_up_to_date(repos):
    r = up.check(repos.user)
    assert r.status == "up_to_date" and r.ok


def test_check_reports_available_and_changes_nothing(repos):
    head = git(repos.user, "rev-parse", "HEAD")
    publish(repos.dev)
    r = up.check(repos.user)
    assert r.status == "available" and r.behind == 1 and r.changed_files == ["app.py"]
    assert git(repos.user, "rev-parse", "HEAD") == head
    assert (repos.user / "app.py").read_text() == "VERSION = 1\n"


def test_check_flags_dependency_changes(repos):
    publish(repos.dev, "requests\nnumpy\n", "requirements.txt", "deps")
    assert up.check(repos.user).deps_changed is True


# ---------------- apply: success ----------------

def test_apply_updates_when_tests_pass_and_logs(repos):
    publish(repos.dev)
    before = git(repos.user, "rev-parse", "HEAD")
    r = up.apply(repos.user, test_cmd=PASS, log_path=repos.log)
    assert r.status == "updated" and r.before == before
    assert git(repos.user, "rev-parse", "HEAD") == git(repos.dev, "rev-parse", "HEAD")
    assert (repos.user / "app.py").read_text() == "VERSION = 2\n"
    row = json.loads(repos.log.read_text().splitlines()[-1])
    assert row["status"] == "updated" and row["ts"]


def test_apply_notes_dependency_change_without_installing(repos):
    publish(repos.dev, "requests\nnumpy\n", "requirements.txt", "deps")
    r = up.apply(repos.user, test_cmd=PASS, log_path=repos.log)
    assert r.status == "updated" and r.deps_changed
    assert "pip install -r requirements.txt" in r.message


def test_apply_when_already_current_does_not_run_tests(repos, tmp_path):
    marker = tmp_path / "ran"
    cmd = [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"]
    assert up.apply(repos.user, test_cmd=cmd, log_path=repos.log).status == "up_to_date"
    assert not marker.exists()


# ---------------- apply: rollback ----------------

def test_failing_tests_roll_back_exactly(repos):
    publish(repos.dev)
    before = git(repos.user, "rev-parse", "HEAD")
    r = up.apply(repos.user, test_cmd=FAIL, log_path=repos.log)
    assert r.status == "rolled_back" and not r.ok
    assert git(repos.user, "rev-parse", "HEAD") == before
    assert (repos.user / "app.py").read_text() == "VERSION = 1\n"
    assert git(repos.user, "status", "--porcelain") == ""
    assert "boom: 1 failed" in r.test_output_tail
    assert json.loads(repos.log.read_text().splitlines()[-1])["status"] == "rolled_back"


def test_test_timeout_rolls_back(repos):
    publish(repos.dev)
    before = git(repos.user, "rev-parse", "HEAD")
    slow = [sys.executable, "-c", "import time; time.sleep(30)"]
    r = up.apply(repos.user, test_cmd=slow, test_timeout=1, log_path=repos.log)
    assert r.status == "rolled_back" and "timed out" in r.message
    assert git(repos.user, "rev-parse", "HEAD") == before


def test_unstartable_test_command_rolls_back(repos):
    publish(repos.dev)
    before = git(repos.user, "rev-parse", "HEAD")
    r = up.apply(repos.user, test_cmd=["/no/such/binary"], log_path=repos.log)
    assert r.status == "rolled_back" and "could not start" in r.message
    assert git(repos.user, "rev-parse", "HEAD") == before


def test_rollback_keeps_untracked_and_ignored_files(repos):
    (repos.user / ".gitignore").write_text("secret.json\n")
    git(repos.user, "add", "-A")
    git(repos.user, "commit", "-q", "-m", "ignore")
    git(repos.user, "push", "-q", "origin", "main")
    git(repos.dev, "pull", "-q", "origin", "main")
    publish(repos.dev)
    (repos.user / "secret.json").write_text("{\"key\": 1}")
    (repos.user / "notes.txt").write_text("mine")
    assert up.apply(repos.user, test_cmd=FAIL, log_path=repos.log).status == "rolled_back"
    assert (repos.user / "secret.json").read_text() == "{\"key\": 1}"
    assert (repos.user / "notes.txt").read_text() == "mine"


# ---------------- refusals ----------------

def test_dirty_tracked_file_is_refused_and_untouched(repos, tmp_path):
    publish(repos.dev)
    (repos.user / "app.py").write_text("MY LOCAL EDIT\n")
    marker = tmp_path / "ran"
    cmd = [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"]
    r = up.apply(repos.user, test_cmd=cmd, log_path=repos.log)
    assert r.status == "refused" and "uncommitted" in r.message
    assert (repos.user / "app.py").read_text() == "MY LOCAL EDIT\n"
    assert not marker.exists()


def test_untracked_files_do_not_block_update(repos):
    publish(repos.dev)
    (repos.user / "scratch.txt").write_text("x")
    assert up.apply(repos.user, test_cmd=PASS, log_path=repos.log).status == "updated"
    assert (repos.user / "scratch.txt").exists()


def test_local_commits_are_refused(repos):
    (repos.user / "mine.txt").write_text("m")
    git(repos.user, "add", "-A")
    git(repos.user, "commit", "-q", "-m", "local")
    local = git(repos.user, "rev-parse", "HEAD")
    r = up.apply(repos.user, test_cmd=PASS, log_path=repos.log)
    assert r.status == "refused" and "local commit" in r.message
    assert git(repos.user, "rev-parse", "HEAD") == local


def test_diverged_branch_is_refused(repos):
    (repos.user / "mine.txt").write_text("m")
    git(repos.user, "add", "-A")
    git(repos.user, "commit", "-q", "-m", "local")
    local = git(repos.user, "rev-parse", "HEAD")
    publish(repos.dev)
    r = up.apply(repos.user, test_cmd=PASS, log_path=repos.log)
    assert r.status == "refused" and "diverged" in r.message
    assert git(repos.user, "rev-parse", "HEAD") == local


def test_detached_head_is_refused(repos):
    git(repos.user, "checkout", "-q", "--detach")
    publish(repos.dev)
    assert up.apply(repos.user, test_cmd=PASS, log_path=repos.log).status == "refused"


def test_not_a_git_checkout(tmp_path):
    r = up.apply(tmp_path, test_cmd=PASS, log_path=tmp_path / "log.jsonl")
    assert r.status == "error" and "not a git checkout" in r.message


def test_unreachable_remote_is_an_error_not_a_crash(repos):
    git(repos.user, "remote", "set-url", "origin", str(repos.user.parent / "gone.git"))
    r = up.apply(repos.user, test_cmd=PASS, log_path=repos.log)
    assert r.status == "error" and "fetch" in r.message


def test_log_failure_never_breaks_an_update(repos, tmp_path):
    publish(repos.dev)
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, so mkdir under it fails")
    r = up.apply(repos.user, test_cmd=PASS, log_path=blocker / "sub" / "log.jsonl")
    assert r.status == "updated"


# ---------------- CLI ----------------

def test_cli_exit_codes(repos, monkeypatch, capsys):
    monkeypatch.setattr(up, "REPO_ROOT", repos.user)
    monkeypatch.setattr(up, "default_log_path", lambda: repos.log)
    publish(repos.dev)
    assert up.main(["check"]) == 0
    assert "1 new commit" in capsys.readouterr().out
    cmd = f"{sys.executable} -c \"import sys; sys.exit(3)\""
    assert up.main(["apply", "--test-cmd", cmd]) == 1
    out = capsys.readouterr().out
    assert "rolled back" in out
    assert up.main(["apply", "--test-cmd", f"{sys.executable} -c pass"]) == 0


def test_default_test_command_is_the_full_suite():
    assert up.DEFAULT_TEST_CMD[1:4] == ["-m", "pytest", "tests"]
