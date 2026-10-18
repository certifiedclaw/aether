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
        button.text() == "Browse all actions"
        for button in window.findChildren(ui.QPushButton)
    )
    assert [window._right_tabs.tabText(index) for index in range(2)] == [
        "Chat", "Actions",
    ]
    assert all(
        button.text() == button.text().encode("ascii", "replace").decode()
        for button in window._quick_action_buttons.values()
    )
    window.show()
    app.processEvents()
    assert window._right_tabs.tabText(0) == "Chat"
    assert window._right_tabs.tabText(1) == "Actions"
    assert window._log.height() >= 200
    assert window.width() >= min(
        ui._MIN_W, app.primaryScreen().availableGeometry().width()
    )
    assert window._left_panel.maximumWidth() <= 220
    monkeypatch.setattr(
        ui._metrics,
        "snapshot",
        lambda: {
            "cpu": 42.0,
            "mem": 66.0,
            "mem_used": 8 * 1024**3,
            "mem_total": 16 * 1024**3,
            "net": 0.5,
            "gpu": -1.0,
            "tmp": -1.0,
        },
    )
    window._update_metrics()
    assert window._bar_cpu._text == "42%"
    assert window._bar_mem._text == "66% / 16GB"
    assert "8.0 gigabytes used of 16.0 gigabytes total" in (
        window._bar_mem.accessibleDescription()
    )
    assert window._bar_net._text == "512 KiB/s"
    if window.width() < ui._SIDEBAR_BREAKPOINT:
        assert window._left_panel.isHidden()
        assert window._system_panel_btn.isVisible()
        window._system_panel_btn.click()
        assert not window._left_panel.isHidden()
        window._system_panel_btn.click()
        assert window._left_panel.isHidden()
    assert window._stats_bar.isHidden()
    assert window._stats_bar._ctx_label.text() == "Unavailable"
    window._stats_toggle.click()
    assert window._stats_bar.isVisible()
    window._stats_bar.update_stats({
        "model": "qwen2.5:7b",
        "prompt_tokens": 1200,
        "output_tokens": 300,
        "elapsed": 2.5,
        "tokens_per_sec": 120,
        "context_window": 8192,
        "route": "remote",
    })
    assert window._stats_bar._value_labels["input"].text() == "1,200 tokens"
    assert window._stats_bar._value_labels["speed"].text() == "120.0 tok/s"
    assert window._stats_bar._ctx_label.text() == "18% of 8,192 tokens"
    window._stats_toggle.click()
    assert window._stats_bar.isHidden()
    window._stats_bar.update_stats({"model": "qwen2.5:7b", "route": "remote"})
    assert window.orb.route == "remote"
    assert "GPU PC" in window.orb.accessibleDescription()
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

    window.resize(760, 620)
    app.processEvents()

    def top_y(widget):
        return widget.mapTo(window, widget.rect().topLeft()).y()

    assert top_y(window._stats_bar) >= top_y(window._log) + window._log.height()
    assert window._stats_bar.isHidden()
    assert top_y(window._input) >= top_y(window._log) + window._log.height()
    assert window._prompt_suggestions.isHidden()
    window._stats_toggle.click()
    app.processEvents()
    assert top_y(window._input) >= (
        top_y(window._stats_bar) + window._stats_bar.height()
    )
    window._stats_toggle.click()

    window.resize(800, 780)
    app.processEvents()
    assert window._log.height() >= 200
    assert not window._prompt_suggestions.isHidden()

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


def test_published_message_stats_include_model_route(monkeypatch):
    from core import conversation_core, perf_stats

    published = []
    monkeypatch.setattr(perf_stats, "route_for", lambda _kwargs: "cloud")
    monkeypatch.setattr(perf_stats, "record", lambda *args, **kwargs: None)
    monkeypatch.setattr(conversation_core, "get_context_window", lambda _model: 8192)
    core = object.__new__(conversation_core.ConversationCoreMixin)
    core.ui = SimpleNamespace(update_message_stats=published.append)

    core._publish_message_stats(
        {"model": "qwen2.5:7b", "prompt_tokens": 12, "output_tokens": 4},
        elapsed=0.25,
        stream_kwargs={"mode": "cloud"},
    )

    assert published[0]["route"] == "cloud"
    assert published[0]["model"] == "qwen2.5:7b"


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
    screen = app.primaryScreen().availableGeometry()
    window.resize(
        min(ui._DEFAULT_W, screen.width()),
        min(ui._DEFAULT_H, screen.height()),
    )
    window.show()
    app.processEvents()

    assert window._chat_status_lbl.text() == "Ready to help"
    window._apply_state("THINKING")
    assert window._chat_status_lbl.text() == "Thinking…"
    window._apply_state("PROCESSING")
    assert window._chat_status_lbl.text() == "Working on it…"
    window._apply_state("ERROR")
    assert "conversation log" in window._chat_status_lbl.text()
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
