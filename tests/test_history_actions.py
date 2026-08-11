from app.agent_contracts import DecisionOption
from app.history_actions import reply_history_attention
from app.store import ReplyAttempt, ReplyTask


def _attempt(**changes) -> ReplyAttempt:
    values = {
        "id": 1,
        "conversation_id": "cid-1",
        "conversation_title": "HR",
        "trigger_message_id": "msg-1",
        "trigger_sender": "Mina",
        "trigger_text": "Please review this.",
        "action": "agent_run",
        "sensitivity_kind": "general",
        "codex_reason": "",
        "draft_reply_text": "",
        "final_reply_text": "",
        "permission_action": "",
        "permission_reason": "",
        "send_status": "failed",
        "send_error": "",
        "retry_count": 0,
        "created_at": "2026-08-11 05:00:00",
        "updated_at": "2026-08-11 05:00:00",
    }
    values.update(changes)
    return ReplyAttempt.model_validate(values)


def _task(**changes) -> ReplyTask:
    values = {
        "id": 1,
        "conversation_id": "cid-1",
        "conversation_title": "HR",
        "single_chat": False,
        "trigger_message_id": "msg-1",
        "trigger_create_time": "2026-08-11 05:00:00",
        "trigger_sender": "Mina",
        "trigger_text": "Please review this.",
        "status": "failed",
        "attempts": 3,
        "created_at": "2026-08-11 05:00:00",
        "updated_at": "2026-08-11 05:00:00",
    }
    values.update(changes)
    return ReplyTask.model_validate(values)


def test_pending_retry_uses_persisted_backoff_without_human_actions():
    attempt = _attempt(codex_reason="Codex provider unavailable")
    task = _task(
        status="pending",
        attempts=2,
        available_at="2026-08-11 05:14:00",
    )

    state = reply_history_attention(
        attempt,
        task=task,
        decision_options=(),
        side_effect_state="none",
    )

    assert state is not None
    assert state.kind == "automatic_recovery"
    assert state.reason == "Codex provider unavailable"
    assert state.retry_attempt == 2
    assert state.retry_limit == 3
    assert state.retry_at == "2026-08-11 05:14:00"
    assert [action.key for action in state.actions] == ["details"]


def test_exhausted_failure_requires_manager_and_offers_safe_choices():
    attempt = _attempt(audit_summary="Current task did not complete")
    task = _task(status="failed", attempts=3, available_at="")

    state = reply_history_attention(
        attempt,
        task=task,
        decision_options=(),
        side_effect_state="none",
    )

    assert state is not None
    assert state.kind == "needs_manager"
    assert state.reason == "Current task did not complete"
    assert [action.key for action in state.actions] == [
        "retry",
        "defer",
        "manual",
        "details",
    ]


def test_agent_supplied_choices_are_preserved_for_general_needs_human():
    attempt = _attempt(
        send_status="needs_human",
        audit_summary="Two valid plans change external state",
    )
    options = (
        DecisionOption(
            key="A",
            label="Use plan A",
            instruction="Use plan A",
            consequence="Publishes plan A",
        ),
        DecisionOption(
            key="B",
            label="Use plan B",
            instruction="Use plan B",
            consequence="Publishes plan B",
        ),
    )

    state = reply_history_attention(
        attempt,
        task=None,
        decision_options=options,
        side_effect_state="none",
    )

    assert state is not None
    assert state.kind == "needs_manager"
    assert state.actions[0].label == "Use plan A"
    assert state.actions[0].instruction == "Use plan A"


def test_unknown_external_effect_never_offers_replay():
    attempt = _attempt(audit_summary="Execution receipt is unknown")

    state = reply_history_attention(
        attempt,
        task=None,
        decision_options=(),
        side_effect_state="unknown",
    )

    assert state is not None
    assert state.external_effect == "执行结果未知，不能安全重放"
    assert [action.key for action in state.actions] == ["manual", "details"]


def test_confirmed_external_effect_only_keeps_agent_choices_and_read_only_actions():
    attempt = _attempt(
        send_status="needs_human",
        audit_summary="External action completed; choose the follow-up.",
    )
    options = (
        DecisionOption(
            key="A",
            label="Confirm complete",
            instruction="Confirm the completed action.",
            consequence="No external action is repeated.",
        ),
        DecisionOption(
            key="B",
            label="Escalate manually",
            instruction="Escalate for manual review without replay.",
            consequence="No automatic replay occurs.",
        ),
    )

    state = reply_history_attention(
        attempt,
        task=None,
        decision_options=options,
        side_effect_state="confirmed",
    )

    assert state is not None
    assert state.external_effect == "已确认产生外部动作"
    assert [action.key for action in state.actions] == ["A", "B", "manual", "details"]
