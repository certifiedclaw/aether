"""Recovery of raw <tool_call> text, held-back streaming, plain monitor alerts, HF offline restore."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.conversation_core import _parse_tagged_tool_calls
from core.llm_client import _is_tool_tag_prefix


def test_single_block():
    t = '<tool_call>\n{"name": "volume_control", "arguments": {"level": 30}}\n</tool_call>'
    assert _parse_tagged_tool_calls(t) == [("computer_settings", {"action": "volume_set", "value": 30})]


def test_multiple_blocks_and_string_args():
    t = ('<tool_call>{"name":"a","arguments":"{\\"x\\": 1}"}</tool_call>\n'
         '<tool_call>{"name":"b","parameters":{}}</tool_call>')
    assert _parse_tagged_tool_calls(t) == [("a", {"x": 1}), ("b", {})]


def test_unclosed_final_tag():
    assert _parse_tagged_tool_calls('<tool_call>{"name":"a","arguments":{}}') == [("a", {})]


def test_prose_around_tags_ok_but_not_in_code_fence():
    t = 'Setting it now. <tool_call>{"name":"a","arguments":{}}</tool_call> Done.'
    assert _parse_tagged_tool_calls(t) == [("a", {})]
    fenced = 'Example:\n```\n<tool_call>{"name":"a","arguments":{}}</tool_call>\n```'
    assert _parse_tagged_tool_calls(fenced) is None


def test_bare_and_fenced_json():
    assert _parse_tagged_tool_calls('{"name": "a", "arguments": {"x": 1}}') == [("a", {"x": 1})]
    assert _parse_tagged_tool_calls('```json\n{"name": "a", "arguments": {}}\n```') == [("a", {})]
    assert _parse_tagged_tool_calls('{"foo": 1}') is None
    assert _parse_tagged_tool_calls('Here is {"name": "a", "arguments": {}} ok') is None


def test_json_data_with_name_field_is_not_recovered_as_tool_call():
    assert _parse_tagged_tool_calls('{"name": "Sam", "age": 30}') is None
    assert _parse_tagged_tool_calls('```json\n{"name": "Sam", "age": 30}\n```') is None


def test_rejects_bad_json():
    assert _parse_tagged_tool_calls("Sure, I set the volume.") is None
    assert _parse_tagged_tool_calls('<tool_call>{not json}</tool_call>') is None
    assert _parse_tagged_tool_calls('<tool_call>{"arguments":{}}</tool_call>') is None
    assert _parse_tagged_tool_calls('<tool_call>{"name":"a","arguments":[1]}</tool_call>') is None
    assert _parse_tagged_tool_calls('<tool_call>{"name":"a"}</tool_call><tool_call>oops</tool_call>') is None
    assert _parse_tagged_tool_calls("") is None


def test_volume_aliases_map_to_computer_settings():
    for name in ("set_volume", "volume_control", "Volume"):
        t = '<tool_call>{"name": "%s", "arguments": {"level": 30}}</tool_call>' % name
        assert _parse_tagged_tool_calls(t) == [("computer_settings", {"action": "volume_set", "value": 30})]
    t = '<tool_call>{"name": "volume_control", "arguments": {"action": "volume_up"}}</tool_call>'
    assert _parse_tagged_tool_calls(t) == [("computer_settings", {"action": "volume_up"})]


def test_stream_hold_back():
    assert _is_tool_tag_prefix("<tool_call>{")
    assert _is_tool_tag_prefix("<tool")
    assert _is_tool_tag_prefix("  <")
    assert not _is_tool_tag_prefix("Hello there.")
    assert not _is_tool_tag_prefix("")
    assert not _is_tool_tag_prefix("<b>bold</b> reply")


def test_streamed_speech_waits_until_tool_calls_are_ruled_out():
    import inspect
    from core.conversation_core import ConversationCoreMixin

    source = inspect.getsource(ConversationCoreMixin._process_message_inner)
    sentence_branch = source.split('if event["type"] == "sentence":', 1)[1].split(
        'elif event["type"] == "done":', 1
    )[0]
    final_reply_branch = source.split("if not final_tool_calls:", 1)[1]
    assert "self.speak(" not in sentence_branch
    assert "for sentence in _streamed:" in final_reply_branch


def test_hf_offline_restored():
    from core.hf_offline import allow_online
    os.environ["HF_HUB_OFFLINE"] = "1"
    with allow_online():
        assert "HF_HUB_OFFLINE" not in os.environ
    assert os.environ["HF_HUB_OFFLINE"] == "1"


def test_monitor_plain_alert_has_no_llm_instructions():
    import actions.system_monitor as sm
    sm._get_gpu_usage = lambda: 99.0
    m = sm.SystemMonitor()
    m._last_alert["gpu"] = -1e9  # cooldown is measured against monotonic time
    out = m.check(plain=True)
    assert out is not None and "SYSTEM_ALERT" not in out and "Warn the user" not in out
    assert "GPU load is at 99%" in out
