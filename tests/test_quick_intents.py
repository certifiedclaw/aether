"""Quick-intent shortcuts: the 15 live prompts plus false-positive guards."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from core.quick_intents import match_all


def names(text):
    plan = match_all(text)
    return None if plan is None else [(n, a) for n, a, _ in plan]


@pytest.mark.parametrize("text,expect", [
    ("Turn the volume down a little.", [("computer_settings", {"action": "volume_down"})]),
    ("Turn the volume up a little.", [("computer_settings", {"action": "volume_up"})]),
    ("turn up the volume", [("computer_settings", {"action": "volume_up"})]),
    ("Can you please lower the volume?", [("computer_settings", {"action": "volume_down"})]),
    ("louder", [("computer_settings", {"action": "volume_up"})]),
    ("mute", [("computer_settings", {"action": "mute"})]),
    ("unmute the sound", [("computer_settings", {"action": "unmute"})]),
    ("set the volume to 35%", [("computer_settings", {"action": "volume_set", "value": 35})]),
    ("set volume to 250", [("computer_settings", {"action": "volume_set", "value": 100})]),
    ("What's the weather in London right now?", [("weather_report", {"city": "London", "time": "today"})]),
    ("weather in New York tomorrow", [("weather_report", {"city": "New York", "time": "tomorrow"})]),
    ("List the files on my desktop.", [("file_controller", {"action": "list", "path": "desktop"})]),
    ("show me the files in my downloads folder", [("file_controller", {"action": "list", "path": "downloads"})]),
    ("Search the web for the latest stable version of Python.",
     [("web_search", {"query": "the latest stable version of Python"})]),
    ("Open an application called Zorblax Quantum Editor.", [("open_app", {"app_name": "Zorblax Quantum Editor"})]),
    ("open Spotify", [("open_app", {"app_name": "Spotify"})]),
    ("Turn the volume down a little, then tell me the weather in London.",
     [("computer_settings", {"action": "volume_down"}), ("weather_report", {"city": "London", "time": "today"})]),
])
def test_matches(text, expect):
    assert names(text) == expect


@pytest.mark.parametrize("text", [
    "Hello.", "Hello, how are you today?", "What is 240 times 17?",
    "Explain in two sentences how a hash table works.",
    "Remember that my favorite color is teal.", "What is my favorite color?",
    "Compare SQLite and PostgreSQL for a small desktop app in three bullet points.",
    "open a new tab", "open the file report.pdf", "open google.com", "open it",
    "search my files for taxes", "search my computer for budget",
    "How does the volume of a sphere scale with radius?",
    "I wish the weather in London were nicer, and also write a poem",
    "turn the volume down, then write me a poem",      # one clause needs the LLM -> all to the LLM
    "What was the volume down at the gym?",
    "",
])
def test_no_false_positives(text):
    assert match_all(text) is None


def test_overlong_or_many_clauses_not_matched():
    assert match_all("mute then louder then mute then louder") is None
    assert match_all("mute " + "x" * 200) is None


def test_volume_reply_passes_errors_through():
    plan = match_all("turn the volume down")
    friendly = plan[0][2]
    assert friendly("Done: volume_down.") == "Volume down."
    assert "pyautogui" in friendly("pyautogui is not installed. Run: pip install pyautogui")
    assert friendly("Unknown action: 'x'.") == "Unknown action: 'x'."


# ---- computer_settings alias normalization (LLM path safety net) ----------
def test_volume_aliases():
    from actions.computer_settings import _normalize_volume_action as n
    assert n("set_volume", "40", "") == ("volume_set", 40)
    assert n("set_volume", "50%", "") == ("volume_set", 50)
    assert n("set_volume", None, "turn the volume down a little") == ("volume_down", None)
    assert n("volume_control", "up", "") == ("volume_up", None)
    assert n("decrease_volume", None, "") == ("volume_down", None)
    assert n("set_volume", None, "") is None           # no number, no direction: don't guess
    assert n("web_search", None, "") is None
    assert n("volume_up", None, "") is None            # real action untouched


def test_unknown_action_message_lists_valid_actions(monkeypatch):
    from actions import computer_settings as cs
    monkeypatch.setattr(cs, "_PYAUTOGUI", True)
    out = cs.computer_settings({"action": "web_search"})
    assert "volume_up" in out and "Unknown action" in out
    called = {}
    monkeypatch.setitem(cs.ACTION_MAP, "volume_down", lambda: called.setdefault("d", 1))
    assert cs.computer_settings({"action": "set_volume", "description": "turn the volume down"}) == "Done: volume_down."
    assert called == {"d": 1}


# ---- hook on the real ConversationCoreMixin --------------------------------
def _fake_agent():
    import threading
    import types
    from core.conversation_core import ConversationCoreMixin

    class A(ConversationCoreMixin):
        def __init__(self):
            self.ui = types.SimpleNamespace(muted=False, write_log=lambda m: self.logs.append(m),
                                            set_state=lambda s: None)
            self.logs, self.spoken, self.calls = [], [], []
            self._conversation, self._conversation_lock = [], threading.RLock()
            self._obs = types.SimpleNamespace(count=lambda *a, **k: None)
        def speak(self, t): self.spoken.append(t)
        def _execute_tool(self, name, args):
            self.calls.append((name, args))
            return "Done: volume_down." if name == "computer_settings" else "It is 15 degrees Celsius in London, sir."
    return A()


def test_agent_handles_compound_request_without_llm():
    a = _fake_agent()
    assert a._try_quick_intent("Turn the volume down a little, then tell me the weather in London.") is True
    assert [c[0] for c in a.calls] == ["computer_settings", "weather_report"]
    assert a.spoken == ["Volume down. It is 15 degrees Celsius in London, sir."]
    assert a._conversation[-1]["role"] == "assistant"


def test_agent_leaves_other_requests_to_llm():
    a = _fake_agent()
    assert a._try_quick_intent("Explain in two sentences how a hash table works.") is False
    assert a.calls == [] and a.spoken == []


def test_purge_db_removes_only_bad_rows(tmp_path):
    import sqlite3
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import fix_local_state as f
    db = tmp_path / "memory.db"
    con = sqlite3.connect(db)
    con.execute("create table memories (id integer primary key, content text)")
    con.executemany("insert into memories(content) values (?)",
                    [("name is Fatih",), ("lives in Istanbul",), ("likes teal",)])
    con.commit(); con.close()
    assert f.purge_db(db, apply=False) == 2            # dry run reports, deletes nothing
    assert sqlite3.connect(db).execute("select count(*) from memories").fetchone()[0] == 3
    assert f.purge_db(db, apply=True) == 2
    assert [r[0] for r in sqlite3.connect(db).execute("select content from memories")] == ["likes teal"]
    assert (tmp_path / "memory.db.bak").exists()


# ---- memory hygiene ---------------------------------------------------------
@pytest.mark.parametrize("user,reply,ok", [
    ("Explain a hash table", "I'm ready. How can I assist you?", False),
    ("What is 240 times 17?", "What would you like to do next?", False),
    ("Open Zorblax", "I'm here. How can I assist you?", False),
    ("turn volume up", "Unknown action: 'set_volume'.", False),
    ("list files", '<tool_call>\n{"name": "x"}', False),
    ("[SYSTEM_ALERT] GPU load is at 100%.", "Noted.", False),
    ("x", "", False),
    ("What is my favorite color?", "Your favorite color is teal. Is there anything else I can help with?", True),
    ("What is 240 times 17?", "240 times 17 is 4,080.", True),
])
def test_storable_exchange(user, reply, ok):
    from core.memory_hygiene import is_storable_exchange
    assert is_storable_exchange(user, reply) is ok


def test_stored_row_poison_detection():
    from core.memory_hygiene import stored_row_is_poisoned as bad
    assert bad("User: Explain hash tables\nAssistant: I'm ready. How can I assist you?")
    assert bad('User: List files\nAssistant: <tool_call>\n{"name": "x"}')
    assert not bad("User: What is my favorite color?\nAssistant: Your favorite color is teal.")
    assert not bad("just a plain note")


def test_purge_db_also_removes_failed_turns(tmp_path):
    import sqlite3
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import fix_local_state as f
    db = tmp_path / "memory.db"
    con = sqlite3.connect(db)
    con.execute("create table memories (id integer primary key, content text)")
    con.executemany("insert into memories(content) values (?)", [
        ("User: Explain hash tables\nAssistant: I'm ready. How can I assist you?",),
        ("User: What is 2+2?\nAssistant: 4.",),
    ])
    con.commit(); con.close()
    assert f.purge_db(db, apply=True) == 1
    assert [r[0] for r in sqlite3.connect(db).execute("select content from memories")] == ["User: What is 2+2?\nAssistant: 4."]


# ---- spoken file-list summary ------------------------------------------------
def test_list_reply_is_short():
    plan = match_all("list the files on my desktop")
    friendly = plan[0][2]
    listing = "Contents of Desktop/ (69 items):\n" + "\n".join(f"\U0001F4C4 file{i}.lnk (1.2 KB)" for i in range(69))
    out = friendly(listing)
    assert out.startswith("Your desktop has 69 items: file0.lnk, file1.lnk") and "and 64 more" in out
    assert len(out) < 200
