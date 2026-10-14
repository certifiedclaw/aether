from core.pending_approvals import ApprovalStore


def test_expired_approval_cannot_be_approved_late():
    store = ApprovalStore()
    approval = store.create("confirm action", timeout=30)
    approval.created_at -= approval.timeout + 1

    assert store.resolve(approval.id, True) is False
    assert approval.result is False
    assert approval.resolved_at is not None
    assert approval.event.is_set()
    assert store.wait(approval.id, timeout=0) is False
    assert store.get(approval.id) is None


def test_expired_approval_is_not_listed_as_pending():
    store = ApprovalStore()
    approval = store.create("confirm action", timeout=30)
    approval.created_at -= approval.timeout + 1

    assert store.list_pending() == []
