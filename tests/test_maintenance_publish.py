import subprocess

from tools.maintenance import publish


def _git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_publish_leaves_unrelated_staged_changes_out_of_commit(tmp_path, monkeypatch):
    repo = tmp_path
    _git(repo, "init")
    _git(repo, "config", "user.name", "Aether Test")
    _git(repo, "config", "user.email", "aether-test@example.invalid")

    report = repo / "PROJECT_STATE.md"
    report.write_text("old report\n", encoding="utf-8")
    _git(repo, "add", "PROJECT_STATE.md")
    _git(repo, "commit", "-m", "initial report")

    (repo / "private.txt").write_text("unrelated staged content\n", encoding="utf-8")
    _git(repo, "add", "private.txt")
    monkeypatch.setattr(publish, "REPO_ROOT", repo)

    success, message = publish.commit_and_push(push=False)

    assert success, message
    assert "No changes to maintenance reports" in message
    assert _git(repo, "diff", "--cached", "--name-only") == "private.txt"

    report.write_text("refreshed report\n", encoding="utf-8")
    success, message = publish.commit_and_push(push=False)

    assert success, message
    assert _git(repo, "show", "--pretty=format:", "--name-only", "HEAD") == "PROJECT_STATE.md"
    assert _git(repo, "diff", "--cached", "--name-only") == "private.txt"
