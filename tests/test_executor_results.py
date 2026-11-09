"""AgentExecutor results and how TaskQueue turns them into task status."""
import threading
import time


from agent import executor as ex
from agent import task_queue as tq


class _FakeExec:
    def __init__(self, result):
        self.result = result

    def execute(self, goal, speak=None, cancel_flag=None):
        return self.result


def _run(result):
    q = tq.TaskQueue(max_concurrent=1, persist_path=None)
    q._executor = _FakeExec(result)
    done = threading.Event()
    tid = q.submit("g", on_complete=lambda *_: done.set()) if "on_complete" in q.submit.__code__.co_varnames else q.submit("g")
    q.start() if hasattr(q, "start") else None
    deadline = time.time() + 5
    while time.time() < deadline:
        t = q.get(tid) if hasattr(q, "get") else q._tasks[tid]
        if t.status in (tq.TaskStatus.COMPLETED, tq.TaskStatus.FAILED, tq.TaskStatus.CANCELLED):
            break
        time.sleep(0.05)
    q.stop()
    return t


def test_failed_outcome_is_marked_failed():
    t = _run(ex.ExecutionResult.failed("Task failed after 2 replan attempts, sir."))
    assert t.status == tq.TaskStatus.FAILED
    assert "replan attempts" in t.error


def test_cancelled_outcome_is_marked_cancelled():
    t = _run(ex.ExecutionResult.cancelled("Task cancelled."))
    assert t.status == tq.TaskStatus.CANCELLED


def test_succeeded_outcome_completes_with_its_text():
    t = _run(ex.ExecutionResult.succeeded("All done."))
    assert t.status == tq.TaskStatus.COMPLETED and t.result == "All done."


def test_executor_reports_failed_when_no_plan(monkeypatch):
    monkeypatch.setattr(ex, "create_plan", lambda goal: {"steps": []})
    assert ex.AgentExecutor().execute("anything").status == "failed"


def test_executor_abort_decision_is_failed_not_completed(monkeypatch):
    monkeypatch.setattr(ex, "create_plan", lambda goal: {"steps": [
        {"step": 1, "tool": "x", "description": "d", "parameters": {}}]})
    monkeypatch.setattr(ex, "_call_tool", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(ex, "analyze_error", lambda *a, **k: {
        "decision": ex.ErrorDecision.ABORT, "reason": "nope"})
    out = ex.AgentExecutor().execute("g")
    assert out.status == "failed" and "aborted" in out.text


def test_executor_declined_generated_code_is_cancelled(monkeypatch):
    monkeypatch.setattr(ex, "create_plan", lambda goal: {"steps": [
        {"step": 1, "tool": "generated_code", "description": "d", "parameters": {}}]})
    monkeypatch.setattr(ex, "_call_tool", lambda *a, **k: (_ for _ in ()).throw(
        ex.GeneratedCodeNotRun("Cancelled: not approved.")))
    out = ex.AgentExecutor().execute("g")
    assert out.status == "cancelled"


def test_task_fails_closed_when_background_scope_cannot_start(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("risk layer down")
    monkeypatch.setattr(tq.risk, "begin_turn", boom)
    t = _run(ex.ExecutionResult.succeeded("must not run"))
    assert t.status == tq.TaskStatus.FAILED and "risk layer down" in t.error
