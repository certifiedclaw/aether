import os
import queue
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


def test_action_launcher_builds_form_and_dispatches_values(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PyQt6.QtWidgets import QApplication, QDialogButtonBox, QLineEdit
        app = QApplication.instance() or QApplication([])
    except Exception:
        pytest.skip("no usable Qt platform plugin in this environment")

    import ui

    window = ui.MainWindow("")
    dispatched = []
    window.on_action_command = lambda name, args: dispatched.append((name, args))

    def run_form(dialog):
        dialog.findChild(QLineEdit).setText("Montreal")
        dialog.findChild(QDialogButtonBox).button(
            QDialogButtonBox.StandardButton.Ok
        ).click()
        return 0

    monkeypatch.setattr(ui.QDialog, "exec", run_form)
    window._open_action_form({
        "name": "weather_report",
        "description": "Get local weather.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "City name"}},
            "required": ["city"],
        },
    })

    assert dispatched == [("weather_report", {"city": "Montreal"})]
    assert any(
        button.text() == "BROWSE ALL ACTIONS"
        for button in window.findChildren(ui.QPushButton)
    )
    assert [window._right_tabs.tabText(index) for index in range(2)] == [
        "Activity", "Actions",
    ]
    assert all(
        button.text() == button.text().encode("ascii", "replace").decode()
        for button in window._quick_action_buttons.values()
    )
    window.show()
    app.processEvents()
    assert window._right_tabs.tabText(0).endswith("Activity")
    assert window._right_tabs.tabText(1).endswith("Actions")
    assert window._log.height() >= 180
    research_form = Mock()
    research_tool = {
        "function": {
            "name": "langchain",
            "description": "Multi-step research",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "task": {"type": "string"},
                },
                "required": ["action"],
            },
        }
    }
    window.on_action_catalog = lambda: [research_tool]
    window._open_action_form = research_form
    window._quick_action_buttons["research"].click()
    research_form.assert_called_once()
    selected_research = research_form.call_args.args[0]
    assert selected_research["name"] == "langchain"
    assert selected_research["preset_args"] == {"action": "research"}
    assert selected_research["parameters"]["required"] == ["task"]

    window.on_action_catalog = lambda: [
        {"function": {"name": "web_search", "parameters": {}}}
    ]
    window._open_named_action("research")
    fallback = research_form.call_args.args[0]
    assert fallback["name"] == "web_search"
    assert "single web search" in fallback["description"]
    assert fallback["parameters"]["required"] == ["query"]

    quick_commands = []
    window.on_action_catalog = lambda: [
        {"function": {
            "name": "langchain",
            "description": "Research online using tools.",
            "parameters": {"type": "object", "properties": {}},
        }},
        {"function": {
            "name": "file_controller",
            "description": "Manage files and folders.",
            "parameters": {"type": "object", "properties": {}},
        }},
    ]
    window.on_text_command = quick_commands.append

    def run_catalog(dialog):
        category = dialog.findChild(ui.QComboBox)
        category.setCurrentText("Research & web")
        buttons = {
            button.accessibleName(): button
            for button in dialog.findChildren(ui.QPushButton)
        }
        assert not buttons["Multi-step research (web and Wikipedia)"].isHidden()
        assert buttons["Manage files and folders"].isHidden()
        category.setCurrentText("All categories")
        next(
            button for button in dialog.findChildren(ui.QPushButton)
            if button.accessibleName() == "Command /doctor"
        ).click()
        return 0

    monkeypatch.setattr(ui.QDialog, "exec", run_catalog)
    window._open_action_catalog()
    assert quick_commands == ["/doctor"]
    assert app is not None
    window.close()


def test_direct_action_uses_worker_queue_without_an_llm_turn():
    from main import AetherLocal

    aether = object.__new__(AetherLocal)
    aether._text_queue = queue.Queue()
    aether._on_action_command("weather_report", {"city": "Montreal"})
    assert aether._text_queue.get_nowait() == (
        "action", "weather_report", {"city": "Montreal"}
    )

    logs = []
    aether._conversation_lock = threading.RLock()
    aether.ui = SimpleNamespace(write_log=logs.append)
    aether._execute_tool = Mock(return_value="Sunny, 20°C.")
    aether._run_action_from_ui("weather_report", {"city": "Montreal"})

    aether._execute_tool.assert_called_once_with(
        "weather_report", {"city": "Montreal"}
    )
    assert logs == [
        "SYS: Running action from Actions: weather_report",
        "Aether: Sunny, 20°C.",
    ]


def test_chat_input_is_discoverable_and_does_not_drop_unsent_text(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
    except Exception:
        pytest.skip("no usable Qt platform plugin in this environment")

    import ui

    window = ui.MainWindow("")
    window.show()
    app.processEvents()

    assert window._chat_status_lbl.text() == "Ready to help"
    window._apply_state("THINKING")
    assert window._chat_status_lbl.text() == "Thinking…"
    window._apply_state("PROCESSING")
    assert window._chat_status_lbl.text() == "Working on it…"
    window._apply_state("ERROR")
    assert "check Activity" in window._chat_status_lbl.text()
    window._apply_state("LISTENING")

    assert "Ctrl+L" in window._input.toolTip() or any(
        "Ctrl+L" in label.text() for label in window.findChildren(ui.QLabel)
    )
    assert "(/help for commands)" in window._input.placeholderText()
    assert "Ctrl+Enter" in window._input.toolTip()
    assert not window._send_btn.isEnabled()
    assert window._prompt_suggestions.isVisible()

    examples = {
        button.accessibleName(): button
        for button in window._prompt_suggestions.findChildren(ui.QPushButton)
    }
    get_started = examples["Use example: What can you help me with?"]
    get_started.click()
    assert window._input.toPlainText() == "What can you help me with?"
    assert window._prompt_suggestions.isHidden()
    window._input.clear()
    assert window._prompt_suggestions.isVisible()

    window._input.setPlainText("   ")
    assert not window._send_btn.isEnabled()
    window._input.setPlainText("hello\nAether")
    assert window._send_btn.isEnabled()
    window._use_prompt("This must not replace the draft")
    assert window._input.toPlainText() == "hello\nAether"

    window._input.setFocus()
    cursor = window._input.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    window._input.setTextCursor(cursor)
    QTest.keyClick(window._input, Qt.Key.Key_Return)
    assert window._input.toPlainText() == "hello\nAether\n"
    QTest.keyClick(
        window._input, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier
    )
    assert window._input.toPlainText() == "hello\nAether\n"

    sent = []

    class ImmediateThread:
        def __init__(self, target, args, daemon):
            self.target = target
            self.args = args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(ui.threading, "Thread", ImmediateThread)
    window.on_text_command = sent.append
    QTest.keyClick(
        window._input, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier
    )
    assert sent == ["hello\nAether"]
    assert window._input.toPlainText() == ""
    assert not window._send_btn.isEnabled()

    window._focus_input()
    assert window.focusWidget() is window._input
    window.close()
