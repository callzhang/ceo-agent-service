from app.runtime_status import TaskStatus, TraceEvent, is_schedulable


def test_task_status_is_small_and_trace_carries_process_detail():
    assert [status.value for status in TaskStatus] == [
        "pending",
        "running",
        "done",
        "failed",
        "needs_human",
    ]
    assert TraceEvent.AUDIT_FEEDBACK.value == "audit_feedback"
    assert TraceEvent.AGENT_REVISION.value == "agent_revision"
    assert is_schedulable("pending")
    assert is_schedulable("running")
    assert not is_schedulable("done")
