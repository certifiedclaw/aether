"""tools/apply_handoff_v12.py records the session once, through the handoff module."""
import json
import re

import pytest

from tools import apply_handoff_v12 as ah
from tools.maintenance import handoff


@pytest.fixture
def scratch(monkeypatch, tmp_path):
    f = tmp_path / "handoff.json"
    f.write_text(json.dumps({"next_id": 3, "entries": [
        {"id": 1, "timestamp": "t", "by": "x", "summary": "old one", "files": [], "next_steps": ""},
        {"id": 2, "timestamp": "t", "by": "x", "summary": "old two", "files": [], "next_steps": ""}]}))
    monkeypatch.setattr(handoff, "HANDOFF_JSON", f)
    monkeypatch.setattr(handoff, "REPO_ROOT", tmp_path)
    return f


def test_adds_each_entry_with_sequential_ids_and_keeps_old_ones(scratch):
    assert ah.apply() == len(ah.ENTRIES) == 3
    d = json.loads(scratch.read_text())
    assert [e["id"] for e in d["entries"]] == [1, 2, 3, 4, 5] and d["next_id"] == 6
    assert d["entries"][2]["summary"].startswith(ah.MARKER)
    assert d["entries"][3]["summary"].startswith(ah.MARKER_TASK)
    assert d["entries"][4]["summary"].startswith(ah.MARKER_KEYS)
    new = d["entries"][2]
    assert new["by"] == "Claude (claude.ai chat)"
    assert "core/risk.py" in new["files"] and "tests/test_risk.py" not in new["files"]  # dir-style entry
    assert d["entries"][:2][0]["summary"] == "old one"


def test_running_twice_adds_nothing(scratch):
    assert ah.apply() == 3
    snapshot = scratch.read_text()
    assert ah.apply() == 0
    assert scratch.read_text() == snapshot


def test_a_later_entry_is_still_added_if_the_first_was_already_recorded(scratch):
    """Someone who ran the earlier script must still get the newer entry."""
    handoff.cmd_add(__import__("argparse").Namespace(
        by="x", summary=ah.SUMMARY, files="", next=""))
    assert ah.apply() == 2
    sums = [e["summary"] for e in json.loads(scratch.read_text())["entries"]]
    for m in (ah.MARKER, ah.MARKER_TASK, ah.MARKER_KEYS):
        assert sum(x.startswith(m) for x in sums) == 1


def test_entries_state_what_was_not_verified_and_carry_no_dates():
    for s in (ah.SUMMARY, ah.SUMMARY_TASK, ah.SUMMARY_KEYS):
        assert "NOT VERIFIED" in s and "VERIFIED:" in s
    assert "Windows" in ah.SUMMARY
    assert "done 23" in ah.NEXT and "git status" in ah.NEXT
    for marker in (ah.MARKER, ah.MARKER_TASK, ah.MARKER_KEYS):
        assert not re.search(r"\b(20\d\d|Oct|Nov|Dec)\b", marker)    # never stamp a guessed date


def test_renders_handoff_markdown(scratch, tmp_path):
    ah.apply()
    handoff.cmd_render(__import__("argparse").Namespace())
    md = (tmp_path / "HANDOFF.md").read_text(encoding="utf-8")
    assert "v12 session" in md and "v12c session" in md and "v12d session" in md
