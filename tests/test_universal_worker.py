import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.leak_check import FORBIDDEN_MARKERS
from app.store import AutoReplyStore
from app.permission import PermissionAction, PermissionResult
from app.universal_context import UniversalContextMessage, UniversalTaskContext
from app.universal_executor import (
    UniversalActionExecution,
    UniversalActionExecutionState,
    build_universal_action_execution,
)
from app.universal_plan import (
    PlannedAction,
    PlannedActionKind,
    UniversalAudit,
    UniversalPlan,
)
from app.worker import DingTalkAutoReplyWorker, ReplyDeliveryError


class FakeDws:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def ding_self(self, text: str) -> None:
        self.calls.append(("ding_self", text))


class NativeReplyFakeDws(FakeDws):
    def resolve_message_sender(self, message) -> str:
        return message.sender_user_id or "resolved-user"

    def send_reply_to_trigger(
        self,
        conversation,
        trigger,
        text,
        **kwargs,
    ) -> dict:
        assert trigger.sender_open_dingtalk_id == "open-context-sender"
        assert trigger.sender_user_id == "user-context-sender"
        assert trigger.create_time == "2026-07-20 10:00:00"
        self.calls.append((conversation, trigger, text, kwargs))
        return {"success": True, "messageId": "sent-native-1"}

    def read_recent_messages(self, conversation):
        raise RuntimeError("visibility unavailable in offline test")


class FakeCodex:
    pass


def _execution(
    store: AutoReplyStore,
    *,
    kind: PlannedActionKind,
    target: dict | None = None,
    payload: dict | None = None,
    sensitivity_kind: str | None = None,
    personnel_subject_user_id: str | None = None,
    candidate_context_known: bool = False,
    candidate_department_ids: list[str] | None = None,
) -> UniversalActionExecution:
    inserted = store.enqueue_reply_task(
        conversation_id="cid-context",
        conversation_title="Context title",
        single_chat=False,
        trigger_message_id="msg-context",
        trigger_create_time="2026-07-20 10:00:00",
        trigger_sender="Context sender",
        trigger_text="Context trigger",
    )
    assert inserted is True
    task = store.claim_reply_tasks(limit=1)[0]
    context = UniversalTaskContext(
        task_id=task.id,
        conversation_id="cid-context",
        conversation_title="Context title",
        single_chat=False,
        trigger_message_id="msg-context",
        trigger_sender="Context sender",
        trigger_text="Context trigger",
        context_messages=(
            UniversalContextMessage(
                sender_name="Earlier sender",
                open_message_id="msg-earlier",
                content="Earlier context",
            ),
            UniversalContextMessage(
                sender_name="Context sender",
                open_message_id="msg-context",
                content="Context trigger",
                sender_open_dingtalk_id="open-context-sender",
                sender_user_id="user-context-sender",
                message_type="text",
                create_time="2026-07-20 10:00:00",
                mentioned_user_ids=("mentioned-user",),
                quoted_message_id="quoted-message",
                quoted_content="quoted-content",
                raw_payload_json='{"source":"reply-task"}',
            ),
        ),
        required_dependencies=("dws",),
        force_new_decision=False,
        dry_run=False,
    )
    action = PlannedAction(
        kind=kind,
        reason=f"Reason for {kind.value}",
        sensitivity_kind=(
            sensitivity_kind
            if sensitivity_kind is not None
            else (
                "general"
                if kind
                in {
                    PlannedActionKind.SEND_REPLY,
                    PlannedActionKind.ASK_CLARIFYING_QUESTION,
                }
                else None
            )
        ),
        personnel_subject_user_id=personnel_subject_user_id,
        candidate_context_known=candidate_context_known,
        candidate_department_ids=candidate_department_ids or [],
        target=target or {},
        payload=payload or {},
    )
    plan = UniversalPlan(
        task_kind="message_handling",
        reason="Handle immutable trigger",
        actions=[action],
        audit=UniversalAudit(
            summary="Universal action test",
            confidence=0.9,
        ),
    )
    plan_execution = store.create_universal_plan_execution(context, plan)
    return build_universal_action_execution(
        context,
        plan_execution,
        plan_execution.plan.actions[0],
        0,
    )


def _worker(store: AutoReplyStore) -> DingTalkAutoReplyWorker:
    return DingTalkAutoReplyWorker(
        store=store,
        dws=FakeDws(),
        codex=FakeCodex(),
    )


def test_universal_reply_native_delivery_receives_immutable_sender_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.SEND_REPLY,
        payload={"text": "Native reply"},
    )
    dws = NativeReplyFakeDws()
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())
    monkeypatch.setattr(worker, "_notify", lambda **kwargs: None)
    monkeypatch.setattr("app.worker.feedback_spike_vercel_base_url", lambda: "")

    assert worker.execute_universal_send_reply(execution) is True

    assert len(dws.calls) == 1
    sent = store.get_sent_reply("cid-context", "msg-context")
    assert sent is not None
    assert "Native reply" in sent.reply_text


@pytest.mark.parametrize(
    "kind",
    [
        PlannedActionKind.SEND_REPLY,
        PlannedActionKind.ASK_CLARIFYING_QUESTION,
    ],
)
def test_universal_reply_uses_immutable_context_and_completes_after_delivery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: PlannedActionKind,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=kind,
        target={
            "conversation_id": "cid-model-must-not-win",
            "trigger_message_id": "msg-model-must-not-win",
        },
        payload={"text": "Reply from plan"},
    )
    worker = _worker(store)
    captured: dict[str, object] = {}

    def fake_send_reply(
        conversation,
        trigger,
        new_messages,
        reply_text,
        reason,
        attempt_id,
        **kwargs,
    ) -> None:
        captured.update(
            conversation=conversation,
            trigger=trigger,
            new_messages=new_messages,
            reply_text=reply_text,
            reason=reason,
            attempt_id=attempt_id,
            kwargs=kwargs,
        )
        store.update_reply_attempt(
            attempt_id,
            final_reply_text=reply_text,
            send_status="sent",
        )
        store.record_sent_reply(
            conversation.open_conversation_id,
            trigger.open_message_id,
            reply_text,
            send_result_json='{"success":true}',
        )

    monkeypatch.setattr(worker, "_send_reply", fake_send_reply)

    assert worker.execute_universal_send_reply(execution) is True

    conversation = captured["conversation"]
    trigger = captured["trigger"]
    assert conversation.open_conversation_id == "cid-context"
    assert trigger.open_message_id == "msg-context"
    assert captured["reply_text"] == "Reply from plan"
    assert captured["reason"] == f"Reason for {kind.value}"
    assert captured["kwargs"] == {"raise_on_delivery_failure": True}
    assert [message.open_message_id for message in captured["new_messages"]] == [
        "msg-earlier",
        "msg-context",
    ]
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.SUCCEEDED
    )
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.action == kind.value
    assert attempt.send_status == "sent"
    event = json.loads(attempt.audit_tool_events_json)[0]
    assert event["execution_id"] == execution.execution_id
    assert event["execution_scope_id"] == execution.execution_scope_id


def test_universal_reply_succeeded_is_idempotent_without_send(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.SEND_REPLY,
        payload={"text": "Reply once"},
    )
    worker = _worker(store)
    sends = 0

    def fake_send_reply(conversation, trigger, reply_text, attempt_id, **kwargs) -> None:
        nonlocal sends
        sends += 1
        store.update_reply_attempt(attempt_id, send_status="sent")
        store.record_sent_reply(
            conversation.open_conversation_id,
            trigger.open_message_id,
            reply_text,
        )

    monkeypatch.setattr(worker, "_send_reply", fake_send_reply)

    assert worker.execute_universal_send_reply(execution) is True
    assert worker.execute_universal_send_reply(execution) is True
    assert sends == 1


def test_universal_reply_existing_delivery_completes_without_send(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.SEND_REPLY,
        payload={"text": "Must be deduplicated"},
    )
    store.record_sent_reply(
        "cid-context",
        "msg-context",
        "Already delivered",
    )
    worker = _worker(store)

    def fail_if_called(*args, **kwargs) -> None:
        raise AssertionError("duplicate delivery must not be attempted")

    monkeypatch.setattr(worker, "_send_reply", fail_if_called)

    assert worker.execute_universal_send_reply(execution) is True

    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "skipped"
    assert attempt.send_error == "duplicate_sent_reply_for_trigger"
    with store._connect() as db:
        row = db.execute(
            "select result_json from universal_action_executions where execution_id=?",
            (execution.execution_id,),
        ).fetchone()
    assert row is not None
    assert row["result_json"] == json.dumps(
        {
            "action_kind": "send_reply",
            "execution_id": execution.execution_id,
            "execution_scope_id": execution.execution_scope_id,
            "outcome": "duplicate_existing_delivery",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def test_universal_reply_unknown_fails_closed_without_send(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.SEND_REPLY,
        payload={"text": "Must not replay"},
    )
    assert (
        store.claim_universal_action_execution(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )
    store.mark_universal_action_execution_unknown(execution, "delivery uncertain")
    worker = _worker(store)

    def fail_if_called(*args, **kwargs) -> None:
        raise AssertionError("send must not be replayed")

    monkeypatch.setattr(worker, "_send_reply", fail_if_called)

    with pytest.raises(RuntimeError, match="outcome is unknown"):
        worker.execute_universal_send_reply(execution)


def test_universal_reply_exception_salvages_proven_delivery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.SEND_REPLY,
        payload={"text": "Delivered before exception"},
    )
    worker = _worker(store)

    def fake_send_reply(conversation, trigger, reply_text, attempt_id, **kwargs) -> None:
        store.record_sent_reply(
            conversation.open_conversation_id,
            trigger.open_message_id,
            reply_text,
        )
        raise ReplyDeliveryError("post-send verification failed")

    monkeypatch.setattr(worker, "_send_reply", fake_send_reply)

    assert worker.execute_universal_send_reply(execution) is True
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.SUCCEEDED
    )
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "sent"


def test_universal_reply_exception_without_delivery_marks_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.SEND_REPLY,
        payload={"text": "Uncertain reply"},
    )
    worker = _worker(store)

    def fake_send_reply(*args, **kwargs) -> None:
        raise ReplyDeliveryError("network disconnected")

    monkeypatch.setattr(worker, "_send_reply", fake_send_reply)

    with pytest.raises(ReplyDeliveryError, match="network disconnected"):
        worker.execute_universal_send_reply(execution)

    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.UNKNOWN
    )
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert "universal_action_outcome_unknown" in attempt.send_error


def test_universal_reply_permission_refusal_is_sent_and_audited(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.SEND_REPLY,
        sensitivity_kind="internal_personnel",
        personnel_subject_user_id="another-user",
        payload={"text": "Sensitive answer"},
    )
    worker = _worker(store)
    evaluated: dict[str, object] = {}

    def evaluate_permission(decision, trigger) -> PermissionResult:
        evaluated.update(decision=decision, trigger=trigger)
        return PermissionResult(
            action=PermissionAction.REPLY,
            reply_text="Safe refusal",
            reason="requester is unrelated",
        )

    worker.permission_gate = type(
        "Gate",
        (),
        {"evaluate": staticmethod(evaluate_permission)},
    )()
    sent_texts: list[str] = []

    def fake_send_reply(conversation, trigger, reply_text, attempt_id, **kwargs):
        sent_texts.append(reply_text)
        store.update_reply_attempt(attempt_id, send_status="sent")
        store.record_sent_reply(
            conversation.open_conversation_id,
            trigger.open_message_id,
            reply_text,
        )

    monkeypatch.setattr(worker, "_send_reply", fake_send_reply)

    assert worker.execute_universal_send_reply(execution) is True
    assert sent_texts == ["Safe refusal"]
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.sensitivity_kind == "internal_personnel"
    assert attempt.permission_action == "reply"
    assert attempt.permission_reason == "requester is unrelated"
    decision = evaluated["decision"]
    assert decision.sensitivity_kind.value == "internal_personnel"
    assert decision.personnel_subject_user_id == "another-user"


def test_universal_reply_permission_error_is_definite_retryable_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.SEND_REPLY,
        sensitivity_kind="external_candidate",
        candidate_context_known=True,
        candidate_department_ids=["dept-1"],
        payload={"text": "Sensitive answer"},
    )
    worker = _worker(store)
    worker.permission_gate = type(
        "Gate",
        (),
        {
            "evaluate": staticmethod(
                lambda decision, trigger: PermissionResult(
                    action=PermissionAction.ERROR,
                    reason="requester identity unavailable",
                )
            )
        },
    )()
    monkeypatch.setattr(
        worker,
        "_send_reply",
        lambda *args, **kwargs: pytest.fail("permission error must not send"),
    )

    with pytest.raises(RuntimeError, match="requester identity unavailable"):
        worker.execute_universal_send_reply(execution)

    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.permission_action == "error"
    assert attempt.send_status == "failed"


def test_universal_reply_recipient_preflight_failure_is_not_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.SEND_REPLY,
        payload={"text": "Reply"},
    )
    worker = _worker(store)
    monkeypatch.setattr(worker, "_explicit_reply_at_targets", lambda *args: [])
    monkeypatch.setattr(
        worker,
        "_default_reply_at_targets",
        lambda trigger: (_ for _ in ()).throw(RuntimeError("recipient lookup failed")),
    )
    monkeypatch.setattr(
        worker,
        "_send_reply",
        lambda *args, **kwargs: pytest.fail("recipient failure must not send"),
    )

    with pytest.raises(RuntimeError, match="recipient lookup failed"):
        worker.execute_universal_send_reply(execution)

    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )


def test_universal_reply_second_recipient_resolution_failure_is_not_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.SEND_REPLY,
        payload={"text": "Reply"},
    )
    worker = _worker(store)
    resolution_calls = 0

    monkeypatch.setattr(worker, "_explicit_reply_at_targets", lambda *args: [])

    def resolve_targets(trigger):
        nonlocal resolution_calls
        resolution_calls += 1
        if resolution_calls == 1:
            return []
        raise RuntimeError("late recipient lookup failed")

    monkeypatch.setattr(worker, "_default_reply_at_targets", resolve_targets)
    monkeypatch.setattr(worker, "_notify", lambda **kwargs: None)

    with pytest.raises(ReplyDeliveryError, match="late recipient lookup failed"):
        worker.execute_universal_send_reply(execution)

    assert resolution_calls == 2
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert attempt.send_error == "late recipient lookup failed"


def test_universal_reply_leak_check_block_is_definite_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.SEND_REPLY,
        payload={"text": f"Blocked reply {FORBIDDEN_MARKERS[0]}"},
    )
    worker = _worker(store)
    monkeypatch.setattr(worker, "_regenerate_reply_after_leak_check", lambda **kwargs: "")
    monkeypatch.setattr(worker, "_notify", lambda **kwargs: None)
    monkeypatch.setattr("app.worker.feedback_spike_vercel_base_url", lambda: "")

    with pytest.raises(ReplyDeliveryError, match="leak_check"):
        worker.execute_universal_send_reply(execution)

    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )


@pytest.mark.parametrize(
    ("kind", "send_status"),
    [
        (PlannedActionKind.NO_REPLY, "skipped"),
        (PlannedActionKind.HANDOFF_TO_HUMAN, "skipped"),
        (PlannedActionKind.BLOCKED, "blocked"),
        (PlannedActionKind.STOP_WITH_ERROR, "failed"),
    ],
)
def test_universal_terminal_actions_record_attempt_and_complete(
    tmp_path: Path,
    kind: PlannedActionKind,
    send_status: str,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(store, kind=kind)
    worker = _worker(store)

    assert worker.execute_universal_terminal_action(execution) is True

    if kind is PlannedActionKind.HANDOFF_TO_HUMAN:
        assert any(call[0] == "ding_self" for call in worker.dws.calls)
    else:
        assert worker.dws.calls == []
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.SUCCEEDED
    )
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.action == kind.value
    assert attempt.send_status == send_status
    assert attempt.send_error == f"{kind.value}: Reason for {kind.value}"
    event = json.loads(attempt.audit_tool_events_json)[0]
    assert event["execution_id"] == execution.execution_id
    if kind is PlannedActionKind.HANDOFF_TO_HUMAN:
        handoff_event = json.loads(attempt.audit_tool_events_json)[-1]
        assert handoff_event["tool"] == "universal_handoff"
        assert json.loads(handoff_event["output"])["notification_invoked"] is True
    assert store.has_seen("msg-context") is True


@pytest.mark.parametrize(
    "kind",
    [
        PlannedActionKind.NO_REPLY,
        PlannedActionKind.HANDOFF_TO_HUMAN,
        PlannedActionKind.BLOCKED,
        PlannedActionKind.STOP_WITH_ERROR,
    ],
)
def test_universal_terminal_side_effects_happen_before_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: PlannedActionKind,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(store, kind=kind)
    worker = _worker(store)
    enqueued: list[int] = []
    monkeypatch.setattr(
        worker,
        "_enqueue_conversation_work_item",
        lambda **kwargs: enqueued.append(kwargs["attempt_id"]),
    )
    original_complete = store.complete_universal_action_execution

    def assert_side_effects_before_complete(*args, **kwargs):
        assert store.has_seen("msg-context") is True
        assert enqueued
        if kind is PlannedActionKind.HANDOFF_TO_HUMAN:
            assert any(call[0] == "ding_self" for call in worker.dws.calls)
        return original_complete(*args, **kwargs)

    monkeypatch.setattr(
        store,
        "complete_universal_action_execution",
        assert_side_effects_before_complete,
    )

    assert worker.execute_universal_terminal_action(execution) is True


def test_universal_no_reply_local_queue_failure_is_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(store, kind=PlannedActionKind.NO_REPLY)
    worker = _worker(store)
    monkeypatch.setattr(
        worker,
        "_enqueue_conversation_work_item",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("queue unavailable")),
    )

    with pytest.raises(RuntimeError, match="queue unavailable"):
        worker.execute_universal_terminal_action(execution)

    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )
    assert store.has_seen("msg-context") is False
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert attempt.send_error == "queue unavailable"


def test_universal_handoff_local_failure_precedes_notification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(store, kind=PlannedActionKind.HANDOFF_TO_HUMAN)
    worker = _worker(store)
    monkeypatch.setattr(
        worker,
        "_enqueue_conversation_work_item",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("queue unavailable")),
    )
    monkeypatch.setattr(
        worker,
        "_execute_message_reactions",
        lambda **kwargs: pytest.fail("reaction must not start"),
    )
    monkeypatch.setattr(
        worker,
        "_notify_handoff",
        lambda **kwargs: pytest.fail("notification must not start"),
    )

    with pytest.raises(RuntimeError, match="queue unavailable"):
        worker.execute_universal_terminal_action(execution)

    assert worker.dws.calls == []
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert attempt.send_error == "queue unavailable"


def test_universal_handoff_failure_after_notification_is_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(store, kind=PlannedActionKind.HANDOFF_TO_HUMAN)
    worker = _worker(store)
    monkeypatch.setattr(worker, "_execute_message_reactions", lambda **kwargs: True)

    def notify_then_fail(**kwargs):
        worker.dws.ding_self("handoff started")
        raise RuntimeError("notification outcome uncertain")

    monkeypatch.setattr(worker, "_notify_handoff", notify_then_fail)

    with pytest.raises(RuntimeError, match="notification outcome uncertain"):
        worker.execute_universal_terminal_action(execution)

    assert store.has_seen("msg-context") is True
    assert worker.dws.calls == [("ding_self", "handoff started")]
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.UNKNOWN
    )
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert "universal_action_outcome_unknown" in attempt.send_error


def test_universal_terminal_retry_reuses_owned_attempt_and_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(store, kind=PlannedActionKind.NO_REPLY)
    worker = _worker(store)
    enqueue_work_item = worker._enqueue_conversation_work_item
    monkeypatch.setattr(
        worker,
        "_enqueue_conversation_work_item",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("queue unavailable")),
    )

    with pytest.raises(RuntimeError, match="queue unavailable"):
        worker.execute_universal_terminal_action(execution)

    first_attempt = store.get_latest_reply_attempt_for_trigger(
        "cid-context", "msg-context"
    )
    assert first_attempt is not None
    assert first_attempt.send_status == "failed"
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )

    monkeypatch.setattr(
        worker,
        "_enqueue_conversation_work_item",
        enqueue_work_item,
    )

    assert worker.execute_universal_terminal_action(execution) is True

    attempts = [
        attempt
        for attempt in store.list_reply_attempts()
        if attempt.universal_execution_id == execution.execution_id
    ]
    assert len(attempts) == 1
    assert attempts[0].id == first_attempt.id
    assert attempts[0].retry_count == 1
    assert attempts[0].send_status == "skipped"
    assert attempts[0].send_error == "no_reply: Reason for no_reply"
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.SUCCEEDED
    )


def test_universal_reply_pre_send_retry_reuses_owned_attempt_and_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.SEND_REPLY,
        payload={"text": "Reply after retry"},
    )
    worker = _worker(store)
    monkeypatch.setattr(worker, "_explicit_reply_at_targets", lambda *args: [])
    monkeypatch.setattr(
        worker,
        "_default_reply_at_targets",
        lambda trigger: (_ for _ in ()).throw(RuntimeError("recipient unavailable")),
    )

    with pytest.raises(ReplyDeliveryError, match="recipient unavailable"):
        worker.execute_universal_send_reply(execution)

    first_attempt = store.get_latest_reply_attempt_for_trigger(
        "cid-context", "msg-context"
    )
    assert first_attempt is not None
    assert first_attempt.send_status == "failed"
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )

    monkeypatch.setattr(worker, "_default_reply_at_targets", lambda trigger: [])

    def deliver(conversation, trigger, reply_text, attempt_id, **kwargs):
        store.update_reply_attempt(
            attempt_id,
            final_reply_text=reply_text,
            send_status="sent",
        )
        store.record_sent_reply(
            conversation.open_conversation_id,
            trigger.open_message_id,
            reply_text,
        )

    monkeypatch.setattr(worker, "_send_reply", deliver)

    assert worker.execute_universal_send_reply(execution) is True

    attempts = [
        attempt
        for attempt in store.list_reply_attempts()
        if attempt.universal_execution_id == execution.execution_id
    ]
    assert len(attempts) == 1
    assert attempts[0].id == first_attempt.id
    assert attempts[0].retry_count == 1
    assert attempts[0].send_status == "sent"
    assert attempts[0].send_error == ""
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.SUCCEEDED
    )


def test_concurrent_universal_attempt_retry_reuses_one_owned_row(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(store, kind=PlannedActionKind.NO_REPLY)
    worker = _worker(store)
    assert (
        store.claim_universal_action_execution(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )
    first_attempt_id = worker._record_universal_reply_attempt(
        execution,
        send_status="skipped",
        send_error="first failure",
    )
    store.mark_universal_action_execution_failed(execution, "first failure")
    assert (
        store.claim_universal_action_execution(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )

    def record_retry(_: int) -> int:
        return worker._record_universal_reply_attempt(
            execution,
            send_status="skipped",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        retry_attempt_ids = list(pool.map(record_retry, range(2)))

    assert retry_attempt_ids == [first_attempt_id, first_attempt_id]
    attempts = [
        attempt
        for attempt in store.list_reply_attempts()
        if attempt.universal_execution_id == execution.execution_id
    ]
    assert len(attempts) == 1
    assert attempts[0].universal_execution_scope_id == execution.execution_scope_id
    assert attempts[0].conversation_id == execution.context.conversation_id
    assert attempts[0].trigger_message_id == execution.context.trigger_message_id
    assert attempts[0].action == execution.action.kind.value


def test_universal_terminal_unknown_fails_closed_without_new_attempt(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(store, kind=PlannedActionKind.NO_REPLY)
    store.claim_universal_action_execution(execution)
    worker = _worker(store)

    with pytest.raises(RuntimeError, match="outcome is unknown"):
        worker.execute_universal_terminal_action(execution)

    assert store.list_reply_attempts() == []


def test_universal_terminal_preserves_unrelated_prior_attempt(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    prior_id = store.record_reply_attempt(
        conversation_id="cid-context",
        conversation_title="Original title",
        trigger_message_id="msg-context",
        trigger_sender="Original sender",
        trigger_text="Original trigger",
        action="send_reply",
        sensitivity_kind="general",
        codex_reason="Original failure",
        draft_reply_text="Original draft",
        audit_summary="Original audit",
        send_status="failed",
    )
    store.update_reply_attempt(prior_id, send_error="original_send_error")
    execution = _execution(store, kind=PlannedActionKind.NO_REPLY)

    assert _worker(store).execute_universal_terminal_action(execution) is True

    attempts = store.list_reply_attempts()
    assert len(attempts) == 2
    original = next(attempt for attempt in attempts if attempt.id == prior_id)
    universal = next(attempt for attempt in attempts if attempt.id != prior_id)
    assert original.action == "send_reply"
    assert original.codex_reason == "Original failure"
    assert original.draft_reply_text == "Original draft"
    assert original.audit_summary == "Original audit"
    assert original.send_status == "failed"
    assert original.send_error == "original_send_error"
    assert universal.action == "no_reply"
    assert universal.universal_execution_id == execution.execution_id
    assert universal.universal_execution_scope_id == execution.execution_scope_id


def test_legacy_trigger_attempt_does_not_overwrite_universal_owned_attempt(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(store, kind=PlannedActionKind.NO_REPLY)
    assert _worker(store).execute_universal_terminal_action(execution) is True
    universal = store.get_latest_reply_attempt_for_trigger(
        "cid-context", "msg-context"
    )
    assert universal is not None

    legacy_id = store.record_reply_attempt_for_trigger(
        conversation_id="cid-context",
        conversation_title="Legacy title",
        trigger_message_id="msg-context",
        trigger_sender="Legacy sender",
        trigger_text="Legacy trigger",
        action="send_reply",
        sensitivity_kind="general",
        codex_reason="Legacy retry",
        send_status="pending",
    )

    assert legacy_id != universal.id
    preserved = store.get_reply_attempt(universal.id)
    assert preserved is not None
    assert preserved.action == "no_reply"
    assert preserved.universal_execution_id == execution.execution_id
    assert preserved.send_status == "skipped"
