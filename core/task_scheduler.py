"""
task_scheduler.py — Aether v3 Task Scheduler (stub / minimal implementation)

Provides a simple cron-style scheduler that the agent can register periodic
tasks with. Extend with APScheduler or a custom QThread for full functionality.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)


class ScheduledTask:
    def __init__(self, name: str, fn: Callable, interval_sec: float):
        self.name         = name
        self.fn           = fn
        self.interval_sec = interval_sec
        self.last_run: float = 0.0
        self.deferred_since: float | None = None   # set while the resource governor holds it back


class TaskScheduler:
    """Simple background thread that fires registered tasks at their intervals."""

    def __init__(self, agent=None, config=None):
        self.agent  = agent
        self.config = config
        self._tasks: list[ScheduledTask] = []
        self._tasks_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # Public API

    def register(self, name: str, fn: Callable, interval_sec: float = 3600.0):
        """Register a task under `name`. Calling this again with a name
        that's already registered REPLACES it (updates fn/interval, resets
        last_run) instead of adding a second, independently-firing
        duplicate. This matters because register_task (below) is exposed as
        an agent tool -- an LLM re-calling it with the same name (retry,
        re-confirming a standing reminder, etc.) must not silently stack up
        N copies of the same recurring action.
        """
        with self._tasks_lock:
            existing = next((t for t in self._tasks if t.name == name), None)
            if existing:
                existing.fn           = fn
                existing.interval_sec = interval_sec
                existing.last_run     = 0.0
                existing.deferred_since = None
                log.info("Scheduled task updated: %s (every %.0fs)", name, interval_sec)
            else:
                self._tasks.append(ScheduledTask(name, fn, interval_sec))
                log.info("Scheduled task registered: %s (every %.0fs)", name, interval_sec)

    def as_tools(self) -> dict[str, Callable]:
        """Expose register_task as an agent tool so the LLM can schedule work."""
        def register_task(name: str, prompt: str, interval_minutes: float = 60):
            """Register a recurring prompt with the agent."""
            def _run():
                if self.agent:
                    try:
                        self.agent.run(prompt)
                    except Exception as exc:
                        log.warning("Scheduled task '%s' failed: %s", name, exc)
            self.register(name, _run, interval_sec=interval_minutes * 60)
            return f"✅ Scheduled '{name}' every {interval_minutes} minutes."

        return {"register_task": register_task}

    # Lifecycle

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="TaskScheduler")
        self._thread.start()
        log.info("TaskScheduler started (%d tasks)", len(self._tasks))

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        log.info("TaskScheduler stopped")

    # Internal

    @staticmethod
    def _governor_allows(task, now: float) -> bool:
        try:
            from core.resource_governor import get_governor
            if task.deferred_since is None:
                task.deferred_since = now
            if get_governor().allow_background(now - task.deferred_since):
                return True
            log.info("Task '%s' deferred: host is busy", task.name)
            return False
        except Exception:  # noqa: BLE001 - the governor must never stop a task from running
            return True

    def _loop(self):
        while not self._stop_event.is_set():
            now = time.monotonic()
            with self._tasks_lock:
                tasks_snapshot = list(self._tasks)
            for task in tasks_snapshot:
                if now - task.last_run >= task.interval_sec:
                    # Yield to a busy host (Palworld, a game...); stays due and runs
                    # once the load drops, or after governor_max_defer_s.
                    if not self._governor_allows(task, now):
                        continue
                    task.deferred_since = None
                    task.last_run = now
                    try:
                        task.fn()
                    except Exception as exc:
                        log.warning("Task '%s' raised: %s", task.name, exc)
            self._stop_event.wait(timeout=10)   # tick every 10 s
