"""Keep a new research topic from inheriting an older tool-call topic."""
from contextlib import contextmanager

from core.conversation_core import ConversationCoreMixin


class _UI:
    def set_state(self, _state):
        pass

    def write_log(self, _message):
        pass


class _Observability:
    @contextmanager
    def span(self, *_args, **_kwargs):
        yield self

    def set(self, *_args, **_kwargs):
        pass

    def count(self, *_args, **_kwargs):
        pass


class _Conversation(ConversationCoreMixin):
    def __init__(self):
        self.ui = _UI()
        self._obs = _Observability()
        self._loop_guard = None
        self._conversation = []
        self.executed = []

    def _execute_tool(self, name, args):
        self.executed.append((name, args))
        return "research result"


def test_research_uses_latest_user_topic_even_if_model_tool_args_are_stale():
    conversation = _Conversation()
    messages = [
        {"role": "user", "content": "Research the riots in France."},
        {"role": "assistant", "content": "Here is an initial report about France."},
        {"role": "user", "content": "Do deep research on turtles."},
        {"role": "assistant", "content": "", "tool_calls": []},
    ]
    tool_calls = [{
        "id": "call-1",
        "function": {
            "name": "langchain",
            "arguments": {"action": "research", "task": "Deep research on France."},
        },
    }]

    conversation._execute_tool_calls(tool_calls, messages)

    name, args = conversation.executed[0]
    assert name == "langchain"
    assert args["task"] == "Do deep research on turtles."
    assert "France" in args["context"]  # retained only as prior-turn context
    assert "France" not in args["task"]


def test_non_research_tools_keep_their_arguments_unchanged():
    conversation = _Conversation()
    messages = [{"role": "user", "content": "Open Calculator."}]
    tool_calls = [{
        "id": "call-2",
        "function": {"name": "open_app", "arguments": {"app": "Calculator"}},
    }]

    conversation._execute_tool_calls(tool_calls, messages)

    assert conversation.executed == [("open_app", {"app": "Calculator"})]
