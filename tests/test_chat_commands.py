import sys
import threading
import types

from core.chat_commands import ChatCommandsMixin
from core.chat_history import ChatSessionStore


def _agent(tmp_path, monkeypatch):
    main_stub = types.ModuleType("main")
    for name in (
        "get_recent_crashes", "backup_memory", "memory_stats", "prune_memory",
        "export_memory", "import_memory", "run_self_test_async",
    ):
        setattr(main_stub, name, lambda *args, **kwargs: None)
    monkeypatch.setitem(sys.modules, "main", main_stub)

    class UI:
        muted = True

        def __init__(self):
            self.logs = []
            self.clears = 0

        def clear_log(self):
            self.clears += 1

        def write_log(self, text):
            self.logs.append(text)

    class Agent(ChatCommandsMixin):
        def __init__(self):
            self._chat_sessions = ChatSessionStore(tmp_path / "chats.json")
            self._active_chat_id = self._chat_sessions.active_id
            self._conversation = []
            self._conversation_lock = threading.RLock()
            self._semantic_mem = None
            self.ui = UI()

        def _refresh_chat_list(self):
            pass

        def _render_chat_history(self):
            self.ui.clear_log()
            for message in self._conversation:
                if message.get("role") in ("user", "assistant"):
                    label = "You" if message["role"] == "user" else "Aether"
                    self.ui.write_log(f"{label}: {message['content']}")

        def _execute_tool(self, *args):
            pass

        def speak(self, *args):
            pass

    return Agent()


def test_new_and_switch_chat_preserves_independent_context(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    assert agent._handle_chat_command("/newchat Project notes")
    agent._conversation.extend([
        {"role": "user", "content": "remember this"},
        {"role": "assistant", "content": "I will."},
    ])
    first_title = agent._chat_sessions.active_title

    assert agent._handle_chat_command("/newchat Travel plans")
    assert agent._conversation == []
    assert agent._handle_chat_command(f"/switchchat {first_title}")
    assert agent._conversation[-1]["content"] == "I will."
    assert "You: remember this" in agent.ui.logs
    assert "Aether: I will." in agent.ui.logs


def test_rename_delete_and_chat_list_commands(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    agent._handle_chat_command("/newchat Work")
    assert agent._handle_chat_command("/renamechat Personal workspace")
    assert agent._chat_sessions.active_title == "Personal workspace"
    assert agent._handle_chat_command("/newchat Temporary")
    assert agent._handle_chat_command("/chats")
    assert "Personal workspace" in agent.ui.logs[-1]
    assert agent._handle_chat_command("/deletechat Temporary")
    assert agent._chat_sessions.active_title == "Personal workspace"


def test_searchchats_finds_user_and_assistant_text_across_sessions(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    agent._handle_chat_command("/newchat Work")
    agent._conversation.extend([
        {"role": "user", "content": "Where is the deployment guide?"},
        {"role": "assistant", "content": "The deployment guide is in the docs folder."},
    ])
    agent._chat_sessions.save_current(agent._conversation)
    agent._handle_chat_command("/newchat Travel")
    agent._conversation.extend([
        {"role": "user", "content": "Find a hotel near the station."},
    ])
    agent._chat_sessions.save_current(agent._conversation)

    assert agent._handle_chat_command("/searchchats DEPLOYMENT GUIDE")
    result = agent.ui.logs[-1]
    assert "Work" in result
    assert "You: Where is the deployment guide?" in result
    assert "Aether: The deployment guide is in the docs folder." in result
    assert "Travel" not in result


def test_searchchats_requires_query_and_reports_no_matches(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)

    assert agent._handle_chat_command("/searchchats")
    assert "Usage: /searchchats <text>" in agent.ui.logs[-1]
    assert agent._handle_chat_command("/searchchats absent phrase")
    assert "No saved chat messages matched" in agent.ui.logs[-1]
