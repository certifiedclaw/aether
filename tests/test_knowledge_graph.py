import sqlite3

import pytest

from memory import knowledge_graph as kg


def test_identical_relation_is_idempotent(tmp_path):
    db = tmp_path / "memory.db"
    first = kg.add_fact("Maya", "is Ian's sister", "Ian", db_path=db)
    repeat = kg.add_fact(" maya ", "IS IAN'S SISTER", " ian ", db_path=db)

    assert first["status"] == "added"
    assert repeat["status"] == "duplicate"
    assert repeat["id"] == first["id"]
    assert len(kg.list_facts(db_path=db)) == 1


def test_conflicting_relation_is_preserved_until_explicit_resolution(tmp_path):
    db = tmp_path / "memory.db"
    old = kg.add_fact("Ian", "lives in", "Boston", db_path=db)
    new = kg.add_fact("Ian", "lives in", "Atlanta", db_path=db)

    assert new["status"] == "conflict"
    assert [(r["object"], r["status"]) for r in new["facts"]] == [
        ("Boston", "contested"), ("Atlanta", "contested"),
    ]
    prompt = kg.format_for_prompt(db_path=db)
    assert "Boston" in prompt and "Atlanta" in prompt
    assert "Do not assume either is current" in prompt

    assert kg.resolve_fact(new["id"], db_path=db)
    visible = kg.list_facts(include_history=False, db_path=db)
    assert [(r["object"], r["status"]) for r in visible] == [("Atlanta", "active")]
    history = kg.list_facts(db_path=db)
    assert {(r["object"], r["status"]) for r in history} == {
        ("Boston", "superseded"), ("Atlanta", "active"),
    }
    assert not kg.resolve_fact(old["id"], db_path=db)


def test_deleting_one_conflicting_claim_promotes_the_remaining_claim(tmp_path):
    db = tmp_path / "memory.db"
    first = kg.add_fact("Aether", "runs on", "Windows", db_path=db)
    kg.add_fact("Aether", "runs on", "Linux", db_path=db)

    assert kg.delete_fact(first["id"], db_path=db)
    remaining = kg.list_facts(include_history=False, db_path=db)
    assert [(r["object"], r["status"]) for r in remaining] == [("Linux", "active")]


@pytest.mark.parametrize(
    ("subject", "predicate", "object"),
    [("", "likes", "tea"), ("Ian", "", "tea"), ("Ian", "likes", "  "),
     ("x" * 121, "likes", "tea"), ("Ian", "x" * 81, "tea"),
     ("Ian", "likes", "x" * 381)],
)
def test_rejects_empty_or_oversized_relation_fields(tmp_path, subject, predicate, object):
    with pytest.raises(ValueError):
        kg.add_fact(subject, predicate, object, db_path=tmp_path / "memory.db")


def test_prompt_includes_graph_even_without_legacy_facts(tmp_path, monkeypatch):
    monkeypatch.setenv("AETHER_DATA_DIR", str(tmp_path))
    from memory import memory_manager
    monkeypatch.setattr(memory_manager, "MEMORY_PATH", tmp_path / "long_term.json")
    kg.add_fact("Maya", "is Ian's sister", "Ian")

    result = memory_manager.format_memory_for_prompt({})
    assert "[Structured memory" in result
    assert "Maya — is Ian's sister → Ian" in result


def test_graph_schema_shares_the_existing_memory_database(tmp_path):
    db = tmp_path / "memory.db"
    kg.add_fact("Aether", "supports", "local storage", db_path=db)
    with sqlite3.connect(db) as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert "knowledge_facts" in tables


def test_relation_tool_is_routed_for_relationship_statements():
    from core.tool_router import select_tool_names
    from core.tool_declarations import TOOL_DECLARATIONS

    assert "save_relation" in select_tool_names("Maya is my sister")
    declaration = next(tool for tool in TOOL_DECLARATIONS if tool["name"] == "save_relation")
    assert declaration["parameters"]["required"] == ["subject", "predicate", "object"]


def test_save_relation_tool_surfaces_conflicts(tmp_path, monkeypatch):
    monkeypatch.setenv("AETHER_DATA_DIR", str(tmp_path))
    from core.tool_dispatch import ToolDispatchMixin
    save = ToolDispatchMixin._tool_save_relation
    args = {"subject": "Ian", "predicate": "lives in", "object": "Boston"}

    assert save(None, args) == "__SILENT__"
    conflict = save(None, {**args, "object": "Atlanta"})
    assert "preserved without choosing one" in conflict
    assert "g:" in conflict


def test_memory_export_and_import_preserve_current_and_contested_relations(tmp_path, monkeypatch):
    from core import memory_tools

    monkeypatch.setattr(memory_tools, "_LONG_TERM_JSON", tmp_path / "long_term.json")
    source_db = tmp_path / "source.db"
    with sqlite3.connect(source_db) as conn:
        conn.execute("""CREATE TABLE memories (
            id INTEGER PRIMARY KEY, role TEXT, content TEXT, timestamp REAL, embedding BLOB
        )""")
    kg.add_fact("Ian", "works at", "Northwind", db_path=source_db)
    kg.add_fact("Ian", "lives in", "Boston", db_path=source_db)
    kg.add_fact("Ian", "lives in", "Atlanta", db_path=source_db)
    monkeypatch.setattr(memory_tools, "_MEMORY_DB", source_db)
    export_path = tmp_path / "export.json"
    assert "3 structured relations" in memory_tools.export_memory(str(export_path))

    destination_db = tmp_path / "restored.db"
    monkeypatch.setattr(memory_tools, "_MEMORY_DB", destination_db)
    assert "3" in memory_tools.import_memory(str(export_path))
    restored = kg.list_facts(include_history=False, db_path=destination_db)
    assert {(fact["predicate"], fact["status"]) for fact in restored} == {
        ("works at", "active"), ("lives in", "contested"),
    }
