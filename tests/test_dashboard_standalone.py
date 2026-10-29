"""Roadmap #21: the dashboard can be built and queried without AetherLocal."""
import queue
import sys
import threading

import pytest

pytest.importorskip("fastapi")


def test_module_imports_without_main_or_ui():
    for mod in ("main", "ui"):
        sys.modules.pop(mod, None)
    import dashboard.__main__ as entry
    assert callable(entry.main)
    assert "main" not in sys.modules and "ui" not in sys.modules


def test_drain_empties_command_queue():
    import dashboard.__main__ as entry

    class Fake:
        _command_queue = queue.Queue()

    d = Fake()
    d._command_queue.put("hello")
    stop = threading.Event()
    t = threading.Thread(target=entry._drain, args=(d, stop), daemon=True)
    t.start()
    for _ in range(40):
        if d._command_queue.empty():
            break
        stop.wait(0.05)
    stop.set()
    t.join(2)
    assert d._command_queue.empty()
