"""tools/apply_roadmap_v12.py must add items through the roadmap module, idempotently."""
import json
from pathlib import Path

import pytest

from tools import apply_roadmap_v12 as ar
from tools.maintenance import roadmap

ROOT = Path(__file__).resolve().parent.parent


BASELINE = {"next_id": 4, "items": [
    {"id": i, "title": f"existing item {i}", "priority": "low", "status": "backlog",
     "notes": "", "created": "2026-10-01", "updated": "2026-10-01"} for i in (1, 2, 3)]}
BASELINE["items"][2]["status"] = "done"


@pytest.fixture
def scratch(monkeypatch, tmp_path):
    """A fixed synthetic roadmap in a temp dir: independent of your real roadmap
    (so these tests pass whether or not you have already run the script)."""
    data = tmp_path / "roadmap.json"
    data.write_text(json.dumps(BASELINE), encoding="utf-8")
    monkeypatch.setattr(roadmap, "ROADMAP_JSON", data)
    monkeypatch.setattr(roadmap, "REPO_ROOT", tmp_path)
    return data


def test_adds_every_planned_item_once_with_sequential_ids(scratch):
    before = json.loads(scratch.read_text())
    added, skipped = ar.apply()
    after = json.loads(scratch.read_text())
    assert len(added) == len(ar.PLANNED) and skipped == []
    assert added == list(range(before["next_id"], before["next_id"] + len(ar.PLANNED)))
    assert len(after["items"]) == len(before["items"]) + len(ar.PLANNED)
    new = after["items"][len(before["items"]):]
    assert all(i["status"] == "backlog" for i in new)           # nothing is marked done
    assert {i["priority"] for i in new} <= {"low", "medium", "high"}
    assert all(i["notes"] for i in new)
    assert after["items"][:len(before["items"])] == before["items"]   # existing items untouched


def test_running_twice_adds_nothing_the_second_time(scratch):
    ar.apply()
    snapshot = scratch.read_text()
    added, skipped = ar.apply()
    assert added == [] and len(skipped) == len(ar.PLANNED)
    assert scratch.read_text() == snapshot


def test_skips_items_already_added_by_hand_even_with_different_punctuation(scratch):
    d = json.loads(scratch.read_text())
    d["items"].append(roadmap._new_item(d, ar.PLANNED[1][1].upper() + "!!", "low", "me"))
    scratch.write_text(json.dumps(d))
    added, skipped = ar.apply()
    assert len(skipped) == 1 and ar.PLANNED[1][1] in skipped
    assert len(added) == len(ar.PLANNED) - 1


def test_never_marks_anything_done_and_renders_the_markdown(scratch, tmp_path):
    ar.apply()
    roadmap.cmd_render(__import__("argparse").Namespace())
    md = (tmp_path / "ROADMAP.md").read_text(encoding="utf-8")
    items = json.loads(scratch.read_text())["items"]
    assert [i["status"] for i in items[:3]] == ["backlog", "backlog", "done"]   # untouched
    assert all(i["status"] == "backlog" for i in items[3:])                     # nothing marked done
    assert "Egress log" in md and "get_secret" in md


def test_planned_list_is_well_formed():
    titles = [t for _, t, _ in ar.PLANNED]
    assert len({ar._norm(t) for t in titles}) == len(titles)     # no duplicates in the list itself
    assert all(p in roadmap.PRIORITIES for p, _, _ in ar.PLANNED)


def test_an_item_added_earlier_under_the_old_wording_is_not_duplicated(scratch):
    new_title = "Central get_secret(): env > OS keyring > config file"
    old_title = ar.RENAMED[new_title]
    d = json.loads(scratch.read_text())
    d["items"].append(roadmap._new_item(d, old_title, "medium", "added by the earlier script"))
    scratch.write_text(json.dumps(d))
    added, skipped = ar.apply()
    assert new_title in skipped and len(added) == len(ar.PLANNED) - 1
    titles = [i["title"] for i in json.loads(scratch.read_text())["items"]]
    assert titles.count(old_title) == 1 and new_title not in titles
