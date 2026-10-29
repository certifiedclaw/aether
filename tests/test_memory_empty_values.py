"""Roadmap #32: empty/None values wrapped in {"value": ...} must not be stored."""
from memory import memory_manager as mm


def test_wrapped_none_and_empty_values_are_skipped():
    target = {}
    changed = mm._recursive_update(target, {
        "identity": {
            "name": {"value": None},
            "city": {"value": "   "},
            "role": {"value": "developer"},
        }
    })
    assert changed
    assert list(target["identity"]) == ["role"]
    assert target["identity"]["role"]["value"] == "developer"


def test_only_empty_values_changes_nothing():
    target = {}
    assert not mm._recursive_update(target, {"identity": {"name": {"value": None}}}) or \
        target.get("identity") in ({}, None)
