import json
from pathlib import Path

import pytest

from app.store import AutoReplyStore
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


class FakeCodex:
    pass


def _execution(
    store: AutoReplyStore,
    *,
    kind: PlannedActionKind,
    target: dict | None = None,
    payload: dict | None = None,
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
            ),
        ),
        required_dependencies=("dws",),
        force_new_decision=False,
        dry_run=False,
    )
    action = PlannedAction(
        kind=kind,
        reason=f"Reason for {kind.value}",
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
