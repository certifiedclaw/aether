import json

from core.chat_history import load_history, save_history


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
