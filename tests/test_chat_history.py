import json

from core.chat_history import ChatSessionStore, load_history, save_history


def test_chat_history_round_trips_tool_turns(tmp_path):
    path = tmp_path / "history.json"
    messages = [
        {"role": "user", "content": "What is two plus two?"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1"}]},
        {"role": "tool", "content": "4", "tool_call_id": "call_1"},
        {"role": "assistant", "content": "It is 4."},
    ]

    save_history(messages, path)

    assert load_history(path) == messages
    assert not list(tmp_path.glob("*.tmp"))


def test_chat_history_rejects_invalid_messages_and_corrupt_files(tmp_path):
    path = tmp_path / "history.json"
    path.write_text(json.dumps({
        "version": 1,
        "messages": [
            {"role": "user", "content": "ok"},
            {"role": "system", "content": "not persisted"},
            "invalid",
        ],
    }), encoding="utf-8")
    assert load_history(path) == [{"role": "user", "content": "ok"}]

    path.write_text("{broken", encoding="utf-8")
    assert load_history(path) == []
    backups = list(tmp_path.glob("history.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{broken"


def test_chat_history_preserves_unsupported_store_version(tmp_path):
    path = tmp_path / "history.json"
    original = json.dumps({"version": 3, "messages": [{"role": "user", "content": "future"}]})
    path.write_text(original, encoding="utf-8")

    store = ChatSessionStore(path)

    backups = list(tmp_path.glob("history.json.corrupt-*"))
    assert store.active_messages == []
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == original


def test_chat_history_is_bounded(tmp_path):
    path = tmp_path / "history.json"
    messages = [{"role": "user", "content": str(i)} for i in range(250)]

    save_history(messages, path)

    restored = load_history(path)
    assert len(restored) == 200
    assert restored[0]["content"] == "50"
    assert restored[-1]["content"] == "249"

    save_history([{"role": "user", "content": "x" * (129 * 1024)}], path)
    assert load_history(path) == []


def test_named_chats_switch_and_persist_independently(tmp_path):
    path = tmp_path / "history.json"
    store = ChatSessionStore(path)
    first_id = store.active_id
    store.save_current([{"role": "user", "content": "first transcript"}])
    first_title = store.active_title

    second = store.create("Research")
    store.save_current([{"role": "user", "content": "second transcript"}])
    assert second["title"] == "Research"
    assert store.switch(first_title)["id"] == first_id
    assert store.active_messages == [{"role": "user", "content": "first transcript"}]

    restored = ChatSessionStore(path)
    assert restored.active_title == first_title
    assert restored.switch("Research")["messages"] == [
        {"role": "user", "content": "second transcript"}
    ]


def test_named_chat_titles_are_unique_and_rename_delete_are_safe(tmp_path):
    store = ChatSessionStore(tmp_path / "history.json")
    first = store.create("Notes")
    second = store.create("Notes")
    assert first["title"] == "Notes"
    assert second["title"] == "Notes (2)"
    assert store.delete("New chat")
    assert store.rename_active("Updated notes")
    assert store.active_title == "Updated notes"
    assert store.delete("Notes")
    assert not store.delete("Updated notes")


def test_chat_store_migrates_existing_single_history(tmp_path):
    path = tmp_path / "history.json"
    old_messages = [{"role": "user", "content": "old conversation"}]
    path.write_text(json.dumps({"version": 1, "messages": old_messages}), encoding="utf-8")

    store = ChatSessionStore(path)

    assert store.active_title == "Previous chat"
    assert store.active_messages == old_messages


def test_new_chat_auto_titles_from_first_user_message(tmp_path):
    store = ChatSessionStore(tmp_path / "history.json")
    store.create()
    store.save_current([{"role": "user", "content": "  Explain   local chat sessions  "}])

    assert store.active_title == "Explain local chat sessions"
