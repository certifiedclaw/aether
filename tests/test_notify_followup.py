"""Roadmap #19 / #20: ntfy follow-through for approvals and the audit trail
left behind by an approval request.

No network: core.notify.send_ntfy is always mocked. Delays are tiny so the
whole file runs in well under a second.
"""
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

import core.notify as notify

FAST = (0.01, 0.01, 0.01)


def _far_deadline():
    return time.time() + 30


# ── keep_notifying ─────────────────────────────────────────────────────────

def test_no_extra_pushes_when_first_push_succeeded_and_no_reminder():
    with patch.object(notify, "send_ntfy") as send:
        extra = notify.keep_notifying("m", threading.Event(), _far_deadline(),
                                      first_sent=True, retry_delays=FAST, remind_after=None)
    assert extra == 0
    send.assert_not_called()


def test_failed_first_push_is_retried_until_it_succeeds():
    with patch.object(notify, "send_ntfy", side_effect=[False, True]) as send:
        extra = notify.keep_notifying("delete X?", threading.Event(), _far_deadline(),
                                      first_sent=False, title="T", click_url="http://dash",
                                      retry_delays=FAST, remind_after=None)
    assert extra == 2
    assert send.call_count == 2
    kwargs = send.call_args.kwargs
    assert kwargs == {"message": "delete X?", "title": "T", "click_url": "http://dash"}


def test_gives_up_after_the_retry_budget_and_does_not_storm():
    with patch.object(notify, "send_ntfy", return_value=False) as send:
        extra = notify.keep_notifying("m", threading.Event(), _far_deadline(),
                                      first_sent=False, retry_delays=FAST, remind_after=None)
    assert extra == len(FAST) == send.call_count


def test_retries_stop_as_soon_as_the_request_is_resolved():
    stop = threading.Event()
    stop.set()  # already resolved
    with patch.object(notify, "send_ntfy") as send:
        extra = notify.keep_notifying("m", stop, _far_deadline(),
                                      first_sent=False, retry_delays=(5.0,), remind_after=None)
    assert extra == 0
    send.assert_not_called()


def test_no_retry_after_the_deadline():
    with patch.object(notify, "send_ntfy") as send:
        extra = notify.keep_notifying("m", threading.Event(), time.time() - 1,
                                      first_sent=False, retry_delays=FAST, remind_after=None)
    assert extra == 0
    send.assert_not_called()


def test_one_reminder_if_still_unanswered():
    with patch.object(notify, "send_ntfy") as send:
        extra = notify.keep_notifying("run shell?", threading.Event(), _far_deadline(),
                                      first_sent=True, retry_delays=FAST, remind_after=0.05)
    assert extra == 1
    send.assert_called_once()
    assert send.call_args.kwargs["message"] == "Still waiting: run shell?"


def test_no_reminder_if_resolved_while_waiting():
    stop = threading.Event()
    threading.Timer(0.05, stop.set).start()
    with patch.object(notify, "send_ntfy") as send:
        t0 = time.time()
        extra = notify.keep_notifying("m", stop, _far_deadline(), first_sent=True,
                                      retry_delays=FAST, remind_after=10.0)
    assert extra == 0
    send.assert_not_called()
    assert time.time() - t0 < 2, "must wake as soon as the request is resolved, not sleep out the reminder"


def test_no_reminder_that_would_land_after_the_deadline():
    with patch.object(notify, "send_ntfy") as send:
        extra = notify.keep_notifying("m", threading.Event(), time.time() + 0.2,
                                      first_sent=True, retry_delays=FAST, remind_after=5.0)
    assert extra == 0
    send.assert_not_called()


def test_reminder_goes_out_after_a_successful_retry_too():
    with patch.object(notify, "send_ntfy", side_effect=[True, True]) as send:
        extra = notify.keep_notifying("m", threading.Event(), _far_deadline(),
                                      first_sent=False, retry_delays=FAST, remind_after=0.02)
    assert extra == 2
    assert send.call_args_list[1].kwargs["message"].startswith("Still waiting:")


def test_keep_notifying_never_raises():
    with patch.object(notify, "send_ntfy", side_effect=RuntimeError("boom")):
        assert notify.keep_notifying("m", threading.Event(), _far_deadline(),
                                     first_sent=False, retry_delays=FAST) == 1


@pytest.mark.parametrize("cfg,expected", [
    ({}, notify.DEFAULT_REMIND_AFTER_S),
    ({"ntfy_remind_after_s": 90}, 90.0),
    ({"ntfy_remind_after_s": "30"}, 30.0),
    ({"ntfy_remind_after_s": 0}, None),
    ({"ntfy_remind_after_s": -5}, None),
    ({"ntfy_remind_after_s": "soon"}, notify.DEFAULT_REMIND_AFTER_S),
    ({"ntfy_remind_after_s": None}, notify.DEFAULT_REMIND_AFTER_S),
])
def test_remind_after_seconds_config(cfg, expected):
    with patch.object(notify, "load_api_keys", return_value=cfg):
        assert notify.remind_after_seconds() == expected


# ── confirm_blocking integration ───────────────────────────────────────────

def _start_confirm(message, timeout):
    from core.headless_ui import HeadlessUI
    ui = HeadlessUI("face.png")
    box = {}
    t = threading.Thread(target=lambda: box.__setitem__("v", ui.confirm_blocking(message, timeout=timeout)),
                         daemon=True)
    t.start()
    return t, box


def _pending_id(message, wait=2.0):
    from core.pending_approvals import get_approval_store
    end = time.time() + wait
    while time.time() < end:
        for a in get_approval_store().list_pending():
            if a["message"] == message:
                return a["id"]
        time.sleep(0.01)
    raise AssertionError("approval never registered")


def test_confirm_blocking_hands_failed_push_to_the_follow_up_thread():
    from core.pending_approvals import get_approval_store
    follow = MagicMock()
    with patch("core.notify.send_ntfy", return_value=False), \
         patch("core.notify.keep_notifying", follow), \
         patch("core.notify.remind_after_seconds", return_value=42.0):
        t, box = _start_confirm("ci: failed push", timeout=5)
        approval_id = _pending_id("ci: failed push")
        get_approval_store().resolve(approval_id, True)
        t.join(5)
    assert box["v"] is True
    deadline_wait = time.time() + 2
    while not follow.called and time.time() < deadline_wait:
        time.sleep(0.01)
    args, kwargs = follow.call_args
    assert args[0] == "ci: failed push"
    assert isinstance(args[1], threading.Event)       # the approval's own event
    assert kwargs["first_sent"] is False
    assert kwargs["remind_after"] == 42.0
    assert args[1].is_set(), "event must be set once confirm_blocking returns so the thread can exit"


def test_follow_up_thread_stops_when_the_approval_times_out():
    """Nobody answers: confirm_blocking denies, and the follow-up helper's stop
    event is set so its thread doesn't linger waiting out a long reminder."""
    seen = {}

    def _fake_keep(message, stop, deadline, **kw):
        seen["woke_after_timeout"] = stop.wait(5)

    with patch("core.notify.send_ntfy", return_value=True), \
         patch("core.notify.keep_notifying", _fake_keep):
        t, box = _start_confirm("ci: timeout", timeout=0.2)
        t.join(5)
        end = time.time() + 2
        while "woke_after_timeout" not in seen and time.time() < end:
            time.sleep(0.01)
    assert box["v"] is False
    assert seen.get("woke_after_timeout") is True


# ── audit trail for requested approvals (#19) ──────────────────────────────

def test_requesting_an_approval_leaves_a_pending_audit_entry():
    from core import audit_log
    from core.pending_approvals import get_approval_store
    with patch("core.notify.send_ntfy", return_value=True), \
         patch("core.notify.keep_notifying"):
        t, _ = _start_confirm("ci: audit trail", timeout=5)
        approval_id = _pending_id("ci: audit trail")
        # The request is on record *before* anyone answers, i.e. it would
        # survive a process restart that interrupts the wait. (Poll: the
        # approval becomes visible in the store a moment before the audit
        # line is flushed.)
        entries = []
        end = time.time() + 2
        while not entries and time.time() < end:
            entries = audit_log.read(tool="approval")
            time.sleep(0.01)
        get_approval_store().resolve(approval_id, False)
        t.join(5)
    assert len(entries) == 1
    e = entries[0]
    assert (e["tool"], e["action"], e["decision"], e["tier"]) == ("approval", "requested", "pending", "high")
    assert e["params"]["approval_id"] == approval_id
    assert e["note"] == "ci: audit trail"
    ok, _msg = audit_log.verify()
    assert ok


def test_audit_failure_does_not_break_the_approval_flow():
    from core.pending_approvals import get_approval_store
    with patch("core.notify.send_ntfy", return_value=True), \
         patch("core.notify.keep_notifying"), \
         patch("core.audit_log.record", side_effect=RuntimeError("disk full")):
        t, box = _start_confirm("ci: audit broken", timeout=5)
        get_approval_store().resolve(_pending_id("ci: audit broken"), True)
        t.join(5)
    assert box["v"] is True
