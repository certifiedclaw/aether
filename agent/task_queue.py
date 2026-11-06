import json
import os
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Any
import logging

from core.data_home import resolve_data_path

logger = logging.getLogger("aether")


class TaskStatus(Enum):
    PENDING    = "pending"
    RUNNING    = "running"
    COMPLETED  = "completed"
    FAILED     = "failed"
    CANCELLED  = "cancelled"
    INTERRUPTED = "interrupted"


class TaskPriority(Enum):
    LOW    = 3
    NORMAL = 2
    HIGH   = 1   


@dataclass(order=True)
class Task:
    priority:    int                       
    created_at:  float = field(compare=False)
    task_id:     str   = field(compare=True)
    goal:        str   = field(compare=False)
    status:      TaskStatus = field(compare=False, default=TaskStatus.PENDING)
    result:      Any        = field(compare=False, default=None)
    error:       str        = field(compare=False, default="")
    speak:       Any        = field(compare=False, default=None)   
    on_complete: Any        = field(compare=False, default=None)  
    cancel_flag: threading.Event = field(compare=False, default_factory=threading.Event)


class TaskQueue:
    _MAX_TASK_HISTORY = 200  # cap on finished (terminal-status) tasks retained in self._tasks

    def __init__(self, max_concurrent: int = 1, persist_path: Path | None = None):
        self._queue:        list[Task]       = []
        self._lock:         threading.Lock   = threading.Lock()
        self._condition:    threading.Condition = threading.Condition(self._lock)
        self._tasks:        dict[str, Task]  = {} 
        self._running:      bool             = False
        self._worker_thread: threading.Thread | None = None
        self._max_concurrent = max_concurrent
        self._active_count   = 0
        self._executor       = None  
        self._persist_path = Path(persist_path) if persist_path is not None else \
            resolve_data_path(None, "task_queue.json")
        self._load_persisted()

    @staticmethod
    def _finished(status: TaskStatus) -> bool:
        return status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)

    def _load_persisted(self) -> None:
        """Restore queued work, but never replay work that may have side effects."""
        try:
            payload = json.loads(self._persist_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, UnicodeError, json.JSONDecodeError) as e:
            logger.warning("[TaskQueue] Could not load persisted tasks from %s: %s",
                           self._persist_path, e)
            return
        if not isinstance(payload, dict) or payload.get("version") != 1 \
                or not isinstance(payload.get("tasks"), list):
            logger.warning("[TaskQueue] Ignoring invalid persisted task data in %s",
                           self._persist_path)
            return

        for item in payload["tasks"][-self._MAX_TASK_HISTORY:]:
            if not isinstance(item, dict):
                continue
            task_id, goal = item.get("task_id"), item.get("goal")
            if (not isinstance(task_id, str) or not task_id or task_id in self._tasks
                    or not isinstance(goal, str) or not goal or len(goal) > 4000):
                continue
            try:
                status = TaskStatus(item.get("status"))
                priority = int(item.get("priority", TaskPriority.NORMAL.value))
                created_at = float(item.get("created_at", 0))
            except (TypeError, ValueError):
                continue
            if status == TaskStatus.RUNNING:
                status = TaskStatus.INTERRUPTED
                error = "App stopped while this task was running; resume explicitly after reviewing its goal."
            else:
                error = str(item.get("error", ""))[:1000]
            task = Task(
                priority=priority,
                created_at=created_at,
                task_id=task_id,
                goal=goal[:4000],
                status=status,
                result=item.get("result"),
                error=error,
            )
            self._tasks[task_id] = task
            if status == TaskStatus.PENDING:
                self._queue.append(task)
        self._queue.sort(key=lambda t: (t.priority, t.created_at))
        if any(task.status == TaskStatus.INTERRUPTED for task in self._tasks.values()):
            try:
                self._persist_locked()
            except OSError as e:
                logger.error("[TaskQueue] Could not record interrupted tasks: %s", e)

    def _persist_locked(self) -> None:
        """Atomically persist task state. Caller must hold self._lock."""
        payload = {
            "version": 1,
            "tasks": [
                {"task_id": task.task_id, "goal": task.goal,
                 "priority": task.priority, "created_at": task.created_at,
                 "status": task.status.value,
                 "result": str(task.result)[:4000] if task.result is not None else None,
                 "error": task.error}
                for task in sorted(self._tasks.values(), key=lambda item: item.created_at)
            ],
        }
        encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self._persist_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=self._persist_path.parent, prefix="task_queue_",
                suffix=".tmp", delete=False
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(encoded)
                temporary.flush()
                os.fsync(temporary.fileno())
            temporary_path.replace(self._persist_path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _get_executor(self):
        if self._executor is None:
            from agent.executor import AgentExecutor
            self._executor = AgentExecutor()
        return self._executor

    def start(self) -> None:
        if self._running:
            return
        self._running      = True
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name="AgentTaskQueue"
        )
        self._worker_thread.start()
        logger.info("[TaskQueue] ✅ Started")

    def stop(self) -> None:
        self._running = False
        with self._condition:
            self._condition.notify_all()
        logger.info("[TaskQueue] 🔴 Stopped")

    def submit(
        self,
        goal:        str,
        priority:    TaskPriority = TaskPriority.NORMAL,
        speak:       Callable | None = None,
        on_complete: Callable | None = None,
    ) -> str:

        goal = str(goal).strip()
        if not goal:
            raise ValueError("A task goal is required.")
        if len(goal) > 4000:
            raise ValueError("Task goals are limited to 4000 characters so they can be safely resumed.")
        with self._condition:
            task_id = str(uuid.uuid4())[:8]
            while task_id in self._tasks:
                task_id = str(uuid.uuid4())[:8]
            task = Task(
                priority=priority.value,
                created_at=time.time(),
                task_id=task_id,
                goal=goal,
                speak=speak,
                on_complete=on_complete,
            )
            self._queue.append(task)
            self._queue.sort(key=lambda t: (t.priority, t.created_at))
            self._tasks[task_id] = task
            self._prune_task_history()
            try:
                self._persist_locked()
            except OSError:
                self._queue.remove(task)
                self._tasks.pop(task_id, None)
                raise
            self._condition.notify()

        logger.info(f"[TaskQueue] 📥 Task queued: [{task_id}] {goal[:60]}")
        return task_id

    def cancel(self, task_id: str) -> bool:

        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                return False

            task.cancel_flag.set()
            task.status = TaskStatus.CANCELLED
            # Evict from the pending queue immediately. _next_task() only
            # ever returns PENDING tasks, so a task cancelled before it
            # started running would otherwise sit in self._queue forever --
            # silently skipped on every worker-loop pass but never removed.
            try:
                self._queue.remove(task)
            except ValueError:
                pass  # already picked up by the worker (now running) — fine
            self._prune_task_history()
            self._persist_locked()
            logger.info(f"[TaskQueue] 🚫 Task cancelled: [{task_id}]")
            return True

    def resume(self, task_id: str, speak: Callable | None = None,
               on_complete: Callable | None = None) -> bool:
        """Explicitly requeue an interrupted task using its original goal."""
        with self._condition:
            task = self._tasks.get(task_id)
            if task is None or task.status != TaskStatus.INTERRUPTED:
                return False
            task.status = TaskStatus.PENDING
            task.result = None
            task.error = ""
            task.cancel_flag = threading.Event()
            task.speak = speak
            task.on_complete = on_complete
            self._queue.append(task)
            self._queue.sort(key=lambda t: (t.priority, t.created_at))
            try:
                self._persist_locked()
            except OSError:
                self._queue.remove(task)
                task.status = TaskStatus.INTERRUPTED
                task.error = "Could not persist resumed task; it was not started."
                task.speak = None
                task.on_complete = None
                raise
            self._condition.notify()
            logger.info("[TaskQueue] Explicitly resumed interrupted task [%s]", task_id)
            return True

    def get_status(self, task_id: str) -> dict | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            return {
                "task_id": task.task_id,
                "goal":    task.goal,
                "status":  task.status.value,
                "result":  task.result,
                "error":   task.error,
                "created_at": task.created_at,
            }

    def get_all_statuses(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "task_id": t.task_id,
                    "goal":    t.goal[:50],
                    "status":  t.status.value,
                    "error":   t.error,
                }
                for t in self._tasks.values()
            ]

    def pending_count(self) -> int:
        with self._lock:
            return sum(1 for t in self._queue if t.status == TaskStatus.PENDING)

    def _prune_task_history(self) -> None:
        """Cap self._tasks so it doesn't grow forever over a long-running
        session. Only evicts finished tasks (COMPLETED/FAILED/CANCELLED),
        oldest first by created_at; PENDING/RUNNING tasks are never touched.
        Caller must already hold self._lock.
        """
        if len(self._tasks) <= self._MAX_TASK_HISTORY:
            return
        finished = sorted(
            (t for t in self._tasks.values()
             if self._finished(t.status)),
            key=lambda t: t.created_at,
        )
        overflow = len(self._tasks) - self._MAX_TASK_HISTORY
        for t in finished[:overflow]:
            self._tasks.pop(t.task_id, None)

    def _worker_loop(self) -> None:
        while self._running:
            task = None

            with self._condition:
                while self._running and not self._next_task():
                    self._condition.wait(timeout=1.0)
                task = self._next_task()
                if task:
                    task.status = TaskStatus.RUNNING
                    self._active_count += 1
                    try:
                        self._queue.remove(task)
                    except ValueError:
                        pass
                    try:
                        self._persist_locked()
                    except OSError as e:
                        task.status = TaskStatus.FAILED
                        task.error = f"Could not persist running state; task was not started: {e}"
                        self._active_count -= 1
                        self._prune_task_history()
                        task = None

            if task:
                threading.Thread(
                    target=self._run_task,
                    args=(task,),
                    daemon=True,
                    name=f"AgentTask-{task.task_id}"
                ).start()

    def _next_task(self) -> Task | None:
        if self._active_count >= self._max_concurrent:
            return None
        for task in self._queue:
            if task.status == TaskStatus.PENDING and not task.cancel_flag.is_set():
                return task
        return None

    def _run_task(self, task: Task) -> None:
        logger.info(f"[TaskQueue] ▶️ Running: [{task.task_id}] {task.goal[:60]}")
        try:
            # Its own approval scope: dialogs say this is a background task, a later user
            # message neither ends its approvals nor can it spend theirs (core/risk.py).
            try:
                from core import risk as _risk
                _risk.begin_turn(f"[background task] {task.goal}", background=True)
            except Exception:  # noqa: BLE001 - never block a task on the risk layer
                pass
            executor = self._get_executor()
            result   = executor.execute(
                goal        = task.goal,
                speak       = task.speak,
                cancel_flag = task.cancel_flag,
            )

            with self._lock:
                _st = getattr(result, "status", "succeeded")
                if task.cancel_flag.is_set() or _st == "cancelled":
                    task.status = TaskStatus.CANCELLED
                elif _st == "failed":
                    task.status = TaskStatus.FAILED
                    task.error  = str(result)
                else:
                    task.status = TaskStatus.COMPLETED
                    task.result = result
                self._active_count -= 1
                self._prune_task_history()
                try:
                    self._persist_locked()
                except OSError as e:
                    logger.error("[TaskQueue] Could not persist completed task [%s]: %s",
                                 task.task_id, e)

            if task.on_complete and not task.cancel_flag.is_set():
                try:
                    task.on_complete(task.task_id, result)
                except Exception as e:
                    logger.warning(f"[TaskQueue] ⚠️ on_complete callback error: {e}")

            logger.info(f"[TaskQueue] {task.status.value}: [{task.task_id}]")

        except Exception as e:
            with self._lock:
                task.status = TaskStatus.FAILED
                task.error  = str(e)
                self._active_count -= 1
                self._prune_task_history()
                try:
                    self._persist_locked()
                except OSError as persist_error:
                    logger.error("[TaskQueue] Could not persist failed task [%s]: %s",
                                 task.task_id, persist_error)
            logger.warning(f"[TaskQueue] ❌ Failed: [{task.task_id}] {e}")

        with self._condition:
            self._condition.notify()

_queue: TaskQueue | None = None
_queue_lock = threading.Lock()


def get_queue() -> TaskQueue:
    global _queue
    with _queue_lock:
        if _queue is None:
            _queue = TaskQueue()
            _queue.start()
        return _queue