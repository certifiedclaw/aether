"""Tool dispatch must not depend on the Windows console encoding."""

from core.tool_dispatch import ToolDispatchMixin


class _UI:
    def set_state(self, _state):
        pass


class _Dispatcher(ToolDispatchMixin):
    _TOOL_DISPATCH = {}

    def __init__(self):
        self.ui = _UI()

    def _tool_save_memory(self, _args):
        return "saved"


def test_dispatch_does_not_print_unicode_tool_arguments(monkeypatch):
    def unsupported_console_print(*_args, **_kwargs):
        raise UnicodeEncodeError("cp1252", "🐢", 0, 1, "test console encoding")

    monkeypatch.setattr("builtins.print", unsupported_console_print)

    result = _Dispatcher()._execute_tool("save_memory", {"value": "turtles 🐢"})

    assert result == "saved"
