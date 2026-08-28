import json

from app.agent_contracts import DecisionOption
from app.history import HistoryItem
from app.history_actions import (
    meeting_history_attention,
    reply_history_attention,
    task_history_attention,
)
from app.meeting_alignment_models import MeetingAlignmentJob, MeetingAlignmentRun
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


def test_internal_retry_code_is_rendered_as_readable_failure_reason():
    attempt = _attempt(audit_summary="consumer_retry_exhausted")

    state = reply_history_attention(
        attempt,
        task=_task(status="failed", attempts=3),
        decision_options=(),
    )

    assert state is not None
    assert state.reason == (
        "Agent 生成回复连续重试后仍未得到可验证结果，已达到本轮重试上限。"
    )
    assert "consumer_retry_exhausted" not in state.reason


def test_live_okr_retry_summary_keeps_source_reason_readable():
    attempt = _attempt(
        audit_summary=(
            "live_okr_and_supporting_evidence_unavailable; consumer retry attempts exhausted"
        )
    )

    state = reply_history_attention(
        attempt,
        task=_task(status="failed", attempts=3),
        decision_options=(),
    )

    assert state is not None
    assert "实时 OKR" in state.reason


def test_historical_image_error_remains_recoverable():
    attempt = _attempt(send_error="image_dependency_unavailable")

    state = reply_history_attention(
        attempt,
        task=_task(status="pending", attempts=1, available_at="2026-08-11 05:02:00"),
        decision_options=(),
    )

    assert state is not None
    assert state.kind == "automatic_recovery"
    assert state.reason == "image_dependency_unavailable"


def test_historical_oa_conflict_remains_recoverable():
    attempt = _attempt(
        action="oa_approval",
        oa_process_instance_id="proc-1",
        send_error="oa_live_evidence_conflict",
        audit_summary=(
            "DWS 与审计读取结果存在冲突，需要 Derek 选择同意或拒绝。"
        ),
    )
    task = _task(
        status="pending",
        attempts=1,
        available_at="2026-08-11 05:02:00",
    )

    state = reply_history_attention(
        attempt,
        task=task,
        decision_options=(),
    )

    assert state is not None
    assert state.kind == "automatic_recovery"
    assert state.reason == (
        "DWS 与审计读取结果存在冲突，需要 Derek 选择同意或拒绝。"
    )


def test_manual_oa_rerun_replaces_old_human_choice_with_automatic_recovery():
    attempt = _attempt(
        id=7546,
        action="oa_approval",
        oa_process_instance_id="proc-1",
        send_status="needs_human",
        send_error="live_evidence_conflict",
    )
    task = _task(
        status="pending",
        attempts=1,
        manual_rerun_attempt_id=7546,
        available_at="2026-08-11 05:02:00",
    )

    state = reply_history_attention(
        attempt,
        task=task,
        decision_options=(
            DecisionOption(
                key="approve",
                label="同意",
                instruction="同意",
                consequence="审批继续",
            ),
            DecisionOption(
                key="reject",
                label="拒绝",
                instruction="拒绝",
                consequence="审批结束",
            ),
        ),
    )

    assert state is not None
    assert state.kind == "automatic_recovery"
    assert "重新读取当前审批" in state.reason
    assert [action.key for action in state.actions] == ["details"]


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
    )

    assert state is not None
    assert state.kind == "needs_manager"
    assert state.actions[0].label == "Use plan A"
    assert state.actions[0].instruction == "Use plan A"


def test_failed_external_effect_uses_current_result_without_replay_choice():
    attempt = _attempt(audit_summary="Execution did not complete")

    state = reply_history_attention(
        attempt,
        task=None,
        decision_options=(),
    )

    assert state is not None
    assert state.external_effect == "外部动作是否完成由当前结果和业务系统状态决定"
    assert [action.key for action in state.actions] == ["retry", "defer", "manual", "details"]


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
    )

    assert state is not None
    assert state.external_effect == "外部动作是否完成由当前结果和业务系统状态决定"
    assert [action.key for action in state.actions] == [
        "A", "B", "defer", "manual", "details"
    ]


def test_retrying_meeting_uses_persisted_retry_plan():
    job = MeetingAlignmentJob.model_construct(
        status="retry",
        attempts=2,
        available_at="2026-08-11T05:14:00+00:00",
        error="Meeting provider unavailable",
    )
    run = MeetingAlignmentRun.model_construct(
        audit_summary="Meeting provider unavailable",
        error="Meeting provider unavailable",
    )

    state = meeting_history_attention(run, job)

    assert state is not None
    assert state.kind == "automatic_recovery"
    assert state.retry_attempt == 2
    assert state.retry_limit == 3
    assert [action.key for action in state.actions] == ["details"]


def test_failed_follow_up_requires_manager_without_synthetic_retry():
    item = HistoryItem(
        kind="task",
        object_type="task",
        source_id=3,
        source_title="Hiring",
        source_actor="Follow-up",
        input_label="跟进",
        input_text="Please provide the update.",
        output_label="结果",
        output_text="Follow-up delivery failed",
        action="follow_up_failed",
        status="failed",
        project_id=1,
        follow_up_id=3,
        created_at="2026-08-11 05:00:00",
    )

    state = task_history_attention(item)

    assert state is not None
    assert state.kind == "needs_manager"
    assert state.reason == "Follow-up delivery failed"
    assert [action.key for action in state.actions] == ["manual", "details"]


def test_failed_follow_up_with_confirmed_non_delivery_offers_safe_resolution():
    item = HistoryItem(
        kind="task",
        object_type="task",
        source_id=3,
        source_title="Hiring",
        source_actor="Follow-up",
        input_label="跟进",
        input_text="Please provide the update.",
        output_label="结果",
        output_text=json.dumps(
            {
                "reason": "direct_message_target_rejected",
                "delivery_state": "not_sent",
                "error": "The recipient is inactive; no message was delivered.",
                "external_side_effect": "none",
            }
        ),
        action="follow_up_failed",
        status="failed",
        project_id=1,
        follow_up_id=3,
        created_at="2026-08-11 05:00:00",
    )

    state = task_history_attention(item)

    assert state is not None
    assert state.reason == "The recipient is inactive; no message was delivered."
    assert state.external_effect == "已确认未发送跟进消息"
    assert [action.key for action in state.actions] == [
        "repair_follow_up",
        "cancel_follow_up",
        "details",
    ]
