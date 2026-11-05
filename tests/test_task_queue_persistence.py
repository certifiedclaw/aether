import json

from agent.task_queue import TaskQueue, TaskStatus


def test_queued_tasks_survive_restart_and_run_only_after_queue_start(tmp_path):
    path = tmp_path / "tasks.json"
    first = TaskQueue(persist_path=path)
    task_id = first.submit("Prepare a report")
    assert json.loads(path.read_text(encoding="utf-8"))["tasks"][0]["status"] == "pending"

    restarted = TaskQueue(persist_path=path)
    assert restarted.pending_count() == 1
    assert restarted.get_status(task_id)["goal"] == "Prepare a report"
    assert restarted.get_status(task_id)["status"] == "pending"
    assert not restarted._running


def test_running_tasks_become_interrupted_and_require_explicit_resume(tmp_path):
    path = tmp_path / "tasks.json"
    first = TaskQueue(persist_path=path)
    task_id = first.submit("Organize the downloads folder")
    with first._lock:
        first._tasks[task_id].status = TaskStatus.RUNNING
        first._persist_locked()

    restarted = TaskQueue(persist_path=path)
    status = restarted.get_status(task_id)
    assert status["status"] == "interrupted"
    assert "resume explicitly" in status["error"]
    assert restarted.pending_count() == 0

    assert restarted.resume(task_id)
    status = restarted.get_status(task_id)
    assert status["status"] == "pending"
    assert status["goal"] == "Organize the downloads folder"
    assert restarted.pending_count() == 1
    assert json.loads(path.read_text(encoding="utf-8"))["tasks"][0]["status"] == "pending"


def test_completed_and_cancelled_tasks_are_not_requeued_after_restart(tmp_path):
    path = tmp_path / "tasks.json"
    first = TaskQueue(persist_path=path)
    completed = first.submit("Completed work")
    cancelled = first.submit("Cancelled work")
    with first._lock:
        first._tasks[completed].status = TaskStatus.COMPLETED
        first._tasks[completed].result = "Done"
        first._tasks[cancelled].status = TaskStatus.CANCELLED
        first._persist_locked()

    restarted = TaskQueue(persist_path=path)
    assert restarted.pending_count() == 0
    assert restarted.get_status(completed)["result"] == "Done"
    assert restarted.get_status(cancelled)["status"] == "cancelled"
    assert not restarted.resume(completed)
    assert not restarted.resume(cancelled)


def test_corrupt_task_store_does_not_prevent_queue_start(tmp_path):
    path = tmp_path / "tasks.json"
    path.write_text("{", encoding="utf-8")

    queue = TaskQueue(persist_path=path)
    assert queue.get_all_statuses() == []
    assert queue.submit("New task")
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1


def test_task_is_not_queued_when_its_initial_state_cannot_be_persisted(tmp_path, monkeypatch):
    queue = TaskQueue(persist_path=tmp_path / "tasks.json")

    def fail_write():
        raise OSError("disk unavailable")

    monkeypatch.setattr(queue, "_persist_locked", fail_write)
    try:
        queue.submit("Must be durable before starting")
    except OSError as exc:
        assert "disk unavailable" in str(exc)
    else:
        raise AssertionError("submission should fail when durable state cannot be written")
    assert queue.pending_count() == 0
    assert queue.get_all_statuses() == []


def test_agent_task_tool_reports_interrupted_tasks_and_resumes_explicitly(monkeypatch, tmp_path):
    from agent import task_queue
    from core.tool_dispatch import ToolDispatchMixin

    queue = TaskQueue(persist_path=tmp_path / "tasks.json")
    task_id = queue.submit("Review the project")
    with queue._lock:
        queue._tasks[task_id].status = TaskStatus.INTERRUPTED
        queue._tasks[task_id].error = "App stopped while this task was running."
        queue._persist_locked()
    monkeypatch.setattr(task_queue, "get_queue", lambda: queue)
    dispatcher = ToolDispatchMixin.__new__(ToolDispatchMixin)
    dispatcher.speak = None

    listing = dispatcher._tool_agent_task({"action": "list"})
    assert task_id in listing
    assert "explicitly resume" in listing

    message = dispatcher._tool_agent_task({"action": "resume", "task_id": task_id})
    assert "Completed steps may repeat" in message
    assert "Ask the user to confirm" in message
    assert queue.get_status(task_id)["status"] == "interrupted"

    message = dispatcher._tool_agent_task({"action": "confirm_resume", "task_id": task_id})
    assert "may run again" in message
    assert queue.get_status(task_id)["status"] == "pending"


def test_task_actions_are_routed_and_resume_is_not_quick():
    from core import tier_router, tool_router
    from core.tier_router import Tier

    assert "agent_task" in tool_router.select_tool_names("Resume task abc123")
    assert "agent_task" in tool_router.select_tool_names("yes")
    assert tier_router.classify("yes", follows_turn=True).tier == Tier.EVERYDAY
    assert tier_router.classify("Resume task abc123").tier != Tier.QUICK
    assert "agent_task" in tool_router.select_tool_names("List my tasks")
