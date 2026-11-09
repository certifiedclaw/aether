"""Roadmap #57 / #32: memory panel lists, edits and deletes facts and past exchanges."""
import json
import sqlite3
import time

import pytest

from core import memory_panel as mp
from memory import memory_manager as mm


@pytest.fixture(autouse=True)
def _isolated_rules(tmp_path, monkeypatch):
    from core import behavior_rules
    monkeypatch.setattr(behavior_rules, "_path", lambda: tmp_path / "behavior_rules.json")


@pytest.fixture
def stores(tmp_path, monkeypatch):
    """Point both stores at temp files and run with no live HybridMemory."""
    facts = tmp_path / "long_term.json"
    monkeypatch.setattr(mm, "MEMORY_PATH", facts)
    db = tmp_path / "memory.db"
    con = sqlite3.connect(str(db))
    con.execute("""CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT,
                   role TEXT NOT NULL DEFAULT 'exchange', content TEXT NOT NULL,
                   timestamp REAL NOT NULL, embedding BLOB)""")
    now = time.time()
    con.executemany("INSERT INTO memories (role, content, timestamp) VALUES (?,?,?)",
                    [("exchange", "User: favourite colour?\nAether: teal", now),
                     ("exchange", "User: what is 2+2\nAether: 4", now)])
    con.commit()
    con.close()
    monkeypatch.setattr(mp, "_db_path", lambda: db)
    monkeypatch.setattr(mp, "_live", None)
    facts.write_text(json.dumps({
        "identity": {"name": {"value": "Ian", "updated": "2026-10-01"},
                     "broken": {"updated": "2026-10-02"},          # the #32 symptom: no value
                     "legacy": "plain string"},
        "preferences": {"tone": {"value": "concise"}},
    }), encoding="utf-8")
    return facts, db


def test_list_all_shows_facts_exchanges_and_flags_problem_entries(stores):
    out = mp.list_all()
    ids = {f["id"]: f for f in out["facts"]}
    assert ids["f:identity/name"]["value"] == "Ian"
    assert ids["f:identity/legacy"]["value"] == "plain string"
    assert ids["f:identity/broken"]["problem"]
    assert out["problems"] == 1
    assert len(out["exchanges"]) == 2
    assert out["exchanges"][0]["id"].startswith("e:")


def test_search_filters_both_stores(stores):
    out = mp.list_all("teal")
    assert out["facts"] == []
    assert len(out["exchanges"]) == 1
    assert [f["id"] for f in mp.list_all("concise")["facts"]] == ["f:preferences/tone"]


def test_edit_fact_and_repair_broken_entry(stores):
    ok, _ = mp.edit("f:identity/broken", "Berlin")
    assert ok
    data = json.loads(stores[0].read_text(encoding="utf-8"))
    assert data["identity"]["broken"]["value"] == "Berlin"
    assert mp.list_all()["problems"] == 0


def test_edit_rejects_empty_unknown_and_bad_ids(stores):
    assert not mp.edit("f:identity/name", "   ")[0]
    assert not mp.edit("f:identity/nope", "x")[0]
    assert not mp.edit("f:bogus/name", "x")[0]
    assert not mp.edit("e:99999", "x")[0]
    assert not mp.edit("e:notanumber", "x")[0]
    assert not mp.edit("zzz", "x")[0]


def test_delete_fact_including_broken_one(stores):
    assert mp.delete("f:identity/broken")[0]
    assert mp.delete("f:identity/name")[0]
    data = json.loads(stores[0].read_text(encoding="utf-8"))
    assert "broken" not in data["identity"] and "name" not in data["identity"]
    assert "legacy" in data["identity"]
    assert not mp.delete("f:identity/name")[0]          # already gone


def test_edit_and_delete_exchange_without_live_instance(stores):
    rid = mp.list_all()["exchanges"][0]["id"]
    assert mp.edit(rid, "User: corrected")[0]
    con = sqlite3.connect(str(stores[1]))
    assert con.execute("SELECT content FROM memories WHERE id=?", (int(rid[2:]),)).fetchone()[0] \
        == "User: corrected"
    con.close()
    assert mp.delete(rid)[0]
    assert len(mp.list_all()["exchanges"]) == 1
    assert not mp.delete(rid)[0]


def test_live_instance_is_used_for_exchange_edits(stores):
    calls = []

    class Live:
        def list_exchanges(self, q, limit):
            return [{"id": 7, "role": "exchange", "content": "hi", "ts": time.time()}]

        def update_content(self, rid, text):
            calls.append(("update", rid, text))
            return True

        def delete_ids(self, ids):
            calls.append(("delete", list(ids)))
            return 1

    mp.set_live(Live())
    try:
        assert mp.list_all()["exchanges"][0]["id"] == "e:7"
        assert mp.edit("e:7", "new")[0]
        assert mp.delete("e:7")[0]
    finally:
        mp.set_live(None)
    assert calls == [("update", 7, "new"), ("delete", [7])]


def test_add_fact_validates_category_and_key(stores):
    assert mp.add_fact("identity", "city", "Atlanta")[0]
    assert mp.list_all("atlanta")["facts"][0]["id"] == "f:identity/city"
    assert not mp.add_fact("nope", "k", "v")[0]
    assert not mp.add_fact("identity", "", "v")[0]
    assert not mp.add_fact("identity", "k" * 81, "v")[0]
    assert not mp.add_fact("identity", "k", "  ")[0]


def test_structured_relations_are_listed_and_resolved_explicitly(stores):
    assert mp.add_relation("Ian", "lives in", "Boston")[0]
    ok, message = mp.add_relation("Ian", "lives in", "Atlanta")
    assert ok and "Conflict saved" in message
    claims = mp.list_all()["relations"]
    assert [r["status"] for r in claims] == ["contested", "contested"]
    selected = claims[0]["id"]

    assert mp.resolve_relation(selected)[0]
    claims = mp.list_all()["relations"]
    assert [(r["object"], r["status"]) for r in claims] == [
        ("Atlanta", "active"), ("Boston", "superseded"),
    ]
    assert mp.delete("g:not-a-number")[0] is False
    assert mp.delete(selected)[0]
    assert len(mp.list_all()["relations"]) == 1


def test_relation_command_lists_conflicts_for_manual_choice(stores):
    mp.add_relation("Ian", "lives in", "Boston")
    result = mp.add_relation("Ian", "lives in", "Atlanta")[1]
    candidate_id = result.split("g:", 1)[1].split(" ", 1)[0]
    rendered = mp.handle_command("/memory")
    assert "conflict - choose explicitly" in rendered
    assert "g:" in rendered
    assert "Kept g:" in mp.handle_command(f"/memory resolve g:{candidate_id}")


def test_chat_command_roundtrip(stores):
    assert "f:identity/name" in mp.handle_command("/memory")
    assert "Updated" in mp.handle_command("/memory edit f:identity/name Ian K")
    assert "Forgot" in mp.handle_command("/memory forget f:identity/name")
    assert "Saved" in mp.handle_command("/memory add notes todo buy milk")
    assert "Usage" in mp.handle_command("/memory add notes onlykey")
    assert "no such" in mp.handle_command("/memory forget f:identity/name")


def test_missing_database_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(mm, "MEMORY_PATH", tmp_path / "none.json")
    monkeypatch.setattr(mp, "_db_path", lambda: tmp_path / "absent.db")
    monkeypatch.setattr(mp, "_live", None)
    out = mp.list_all()
    assert out["facts"] == [] and out["exchanges"] == []


# ── HybridMemory support methods (no models loaded) ─────────────────────
@pytest.fixture
def hybrid(tmp_path):
    from memory.hybrid_memory import BM25Index, HybridMemory
    h = HybridMemory.__new__(HybridMemory)          # skip __init__: no model preload threads
    h._db_path = tmp_path / "h.db"
    h._conn = h._setup_db()
    h._bm25 = BM25Index()
    h._doc_ids, h._doc_texts, h._doc_embeddings = [], [], []
    h._encode_one = lambda text: None               # encoder "not ready": stored with NULL embedding
    now = time.time()
    for text in ("alpha 100% sure", "beta under_score", "gamma plain"):
        h._conn.execute("INSERT INTO memories (role, content, timestamp) VALUES ('exchange', ?, ?)",
                        (text, now))
    h._conn.commit()
    h._rebuild_bm25()
    return h


def test_hybrid_list_exchanges_newest_first_and_literal_wildcards(hybrid):
    assert [r["content"] for r in hybrid.list_exchanges()][0] == "gamma plain"
    assert [r["content"] for r in hybrid.list_exchanges("100%")] == ["alpha 100% sure"]
    assert [r["content"] for r in hybrid.list_exchanges("r_s")] == ["beta under_score"]
    assert hybrid.list_exchanges("%") and len(hybrid.list_exchanges("%")) == 1   # literal %, not "match all"


def test_hybrid_update_and_delete_keep_index_in_step(hybrid):
    rid = hybrid.list_exchanges("beta")[0]["id"]
    assert hybrid.update_content(rid, "beta rewritten")
    assert "beta rewritten" in hybrid._doc_texts
    assert not hybrid.update_content(99999, "x")
    assert hybrid.delete_ids([rid]) == 1
    assert rid not in hybrid._doc_ids and len(hybrid._doc_texts) == 2
    assert hybrid.delete_ids([]) == 0
    ids = [r["id"] for r in hybrid.list_exchanges()]
    assert hybrid.delete_ids(ids) == 2
    assert hybrid._doc_texts == []                   # empty store drops the stale index


def test_panel_search_escapes_wildcards_without_live_instance(stores):
    assert mp.list_all("2+2")["exchanges"]
    assert mp.list_all("%")["exchanges"] == []       # literal percent sign: nothing contains one


# ── dashboard routes ────────────────────────────────────────────────────
def _client(stores, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from dashboard import server as ds
    monkeypatch.setenv("AETHER_DASHBOARD_HOST", "127.0.0.1")
    monkeypatch.setattr(ds.DashboardServer, "_ssl_enabled", staticmethod(lambda: False))
    d = ds.DashboardServer()
    d._add_token("tok" * 8, "test-key")
    return TestClient(d.app), {"Authorization": "Bearer " + "tok" * 8}


def test_dashboard_memory_api_requires_the_token(stores, monkeypatch):
    c, _ = _client(stores, monkeypatch)
    assert c.get("/api/memory").status_code == 401
    assert c.post("/api/memory", json={"op": "delete", "id": "f:identity/name"}).status_code == 401
    assert c.get("/memory").status_code == 200                      # the page itself is static
    assert "name" in json.loads(stores[0].read_text(encoding="utf-8"))["identity"]   # untouched


def test_dashboard_memory_api_list_edit_delete_add(stores, monkeypatch):
    c, h = _client(stores, monkeypatch)
    listing = c.get("/api/memory", headers=h).json()
    assert listing["problems"] == 1 and len(listing["exchanges"]) == 2
    assert c.get("/api/memory?q=concise", headers=h).json()["facts"][0]["id"] == "f:preferences/tone"
    r = c.post("/api/memory", headers=h, json={"op": "edit", "id": "f:identity/name", "text": "Ian K"})
    assert r.status_code == 200 and r.json()["ok"]
    r = c.post("/api/memory", headers=h, json={"op": "add_relation", "subject": "Ian",
                                               "predicate": "lives in", "object": "Boston"})
    assert r.status_code == 200 and r.json()["ok"]
    relations = c.get("/api/memory", headers=h).json()["relations"]
    assert relations[0]["value"] == "Ian — lives in — Boston"
    r = c.post("/api/memory", headers=h, json={"op": "add_relation", "subject": "Ian",
                                               "predicate": "lives in", "object": "Atlanta"})
    assert r.status_code == 200 and r.json()["ok"] and "Conflict saved" in r.json()["message"]
    candidate = c.get("/api/memory", headers=h).json()["relations"][0]["id"]
    r = c.post("/api/memory", headers=h, json={"op": "resolve_relation", "id": candidate})
    assert r.status_code == 200 and r.json()["ok"]
    r = c.post("/api/memory", headers=h, json={"op": "delete", "id": "f:identity/broken"})
    assert r.status_code == 200
    r = c.post("/api/memory", headers=h, json={"op": "add", "category": "notes", "key": "k", "value": "v"})
    assert r.status_code == 200
    data = json.loads(stores[0].read_text(encoding="utf-8"))
    assert data["identity"]["name"]["value"] == "Ian K" and "broken" not in data["identity"]
    assert data["notes"]["k"]["value"] == "v"


def test_dashboard_memory_api_errors(stores, monkeypatch):
    c, h = _client(stores, monkeypatch)
    assert c.post("/api/memory", headers=h, json={"op": "nuke"}).status_code == 400
    assert c.post("/api/memory", headers=h, content=b"not json").status_code == 400
    r = c.post("/api/memory", headers=h, json={"op": "delete", "id": "f:identity/ghost"})
    assert r.status_code == 400 and not r.json()["ok"]
