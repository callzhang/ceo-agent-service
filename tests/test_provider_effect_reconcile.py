"""Reconciling a delivery a proposal turn performed but never recorded.

A Consumer turn may only propose, yet one with plain shell access can reach a
provider and be accepted.  The message is then real and the ledger is empty, so
the next attempt sends it again.  These tests pin the two halves of the repair:
the run's evidence has to name exactly one delivery or the reconciliation is
refused, and the ledger has to accept the run that actually produced the
receipt while still refusing a run belonging to some other task.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.business_identity import external_action_key
from app.provider_effect_reconcile import (
    UnreconcilableProviderEffect,
    plan_provider_effect_reconciliation,
)
from app.store import AgentRole, AutoReplyStore

TARGET_ID = "DVPPfhk5M348jhueJ0ZVY4QOArx0bp4jn"
REPLY_TEXT = "刘紫煜，结算比例与付款金额不一致，请说明计算逻辑。"
SEND_COMMAND = (
    f'/bin/zsh -lc "dws chat message send --open-dingtalk-id \\"{TARGET_ID}\\" '
    '--content \\"刘紫煜，结算比例与付款金额不一致\\""'
)
SEND_OUTPUT = json.dumps(
    {
        "ok": True,
        "outcome": "pending",
        "data": {"result": {"openTaskId": "nexgdfcCmkv"}, "success": True},
    },
    ensure_ascii=False,
)


def _command_event(command: str, output: str, *, exit_code: int = 0) -> dict:
    return {
        "item": {
            "type": "command_execution",
            "command": command,
            "exit_code": exit_code,
            "aggregated_output": output,
        }
    }


def _final_result(*actions: dict) -> str:
    return json.dumps(
        {
            "outcome": "proposed",
            "proposal": {
                "objective": "审批付款申请",
                "actions": list(actions),
                "authored_judgment": "数据不一致，需申请人说明",
                "sourced_facts": [],
            },
        },
        ensure_ascii=False,
    )


def _send_action(
    *,
    action_identity: str = "clarify_payment_calculation",
    target: dict | None = None,
    content: str = REPLY_TEXT,
) -> dict:
    return {
        "action_identity": action_identity,
        "capability": "dingtalk-chat",
        "operation": "message.send",
        "payload": {"content": content},
        "target": {"open_dingtalk_id": TARGET_ID} if target is None else target,
    }


def _plan(events: list[dict], final_result_json: str):
    return plan_provider_effect_reconciliation(
        agent_run_id=14017,
        reply_task_id=383537,
        business_object_key="message:dingtalk:cid-1:msg-1",
        final_result_json=final_result_json,
        tool_events=events,
    )


def test_plan_names_the_single_delivery_the_run_performed() -> None:
    plan = _plan(
        [
            _command_event("/bin/zsh -lc 'dws chat message send --help'", "usage"),
            _command_event(SEND_COMMAND, SEND_OUTPUT),
        ],
        _final_result(_send_action()),
    )

    assert plan.action_identity == "clarify_payment_calculation"
    assert plan.operation == "message.send"
    assert plan.target_identifiers == {"open_dingtalk_id": TARGET_ID}
    assert plan.reply_text == REPLY_TEXT
    assert plan.receipts == ("nexgdfcCmkv",)
    assert plan.provider_result["reconciled_from_agent_run_id"] == 14017
    assert plan.provider_result["receipt"]["data"]["result"]["openTaskId"] == "nexgdfcCmkv"


def test_reconciled_key_is_the_key_the_normal_delivery_path_computes() -> None:
    """A reconciled row must occupy the slot a later attempt looks in.

    If the reconciled key differed by so much as a target field name, the
    idempotency check would miss it and the message would go out twice, which
    is the whole harm this reconciliation exists to prevent.
    """
    plan = _plan([_command_event(SEND_COMMAND, SEND_OUTPUT)], _final_result(_send_action()))

    assert plan.external_action_key == external_action_key(
        business_object_key="message:dingtalk:cid-1:msg-1",
        action_identity="clarify_payment_calculation",
        operation="message.send",
        target_identifiers={"open_dingtalk_id": TARGET_ID},
    )


def test_plan_refuses_a_run_that_reached_no_provider() -> None:
    with pytest.raises(UnreconcilableProviderEffect, match="no provider receipt"):
        _plan(
            [_command_event("/bin/zsh -lc 'dws oa approval detail'", "{}")],
            _final_result(_send_action()),
        )


def test_plan_refuses_when_two_commands_returned_different_receipts() -> None:
    second = json.dumps({"data": {"result": {"openTaskId": "other-task"}}})
    with pytest.raises(UnreconcilableProviderEffect, match="distinct provider results"):
        _plan(
            [
                _command_event(SEND_COMMAND, SEND_OUTPUT),
                _command_event(SEND_COMMAND.replace(TARGET_ID, "OTHER"), second),
            ],
            _final_result(_send_action()),
        )


def test_plan_refuses_when_no_proposed_action_matches_the_command() -> None:
    other = _send_action(target={"open_dingtalk_id": "SOMEONE-ELSE"})
    with pytest.raises(UnreconcilableProviderEffect, match="cannot be attributed"):
        _plan([_command_event(SEND_COMMAND, SEND_OUTPUT)], _final_result(other))


def test_plan_ignores_a_command_that_failed() -> None:
    with pytest.raises(UnreconcilableProviderEffect, match="no provider receipt"):
        _plan(
            [_command_event(SEND_COMMAND, SEND_OUTPUT, exit_code=1)],
            _final_result(_send_action()),
        )


def _failed_task_with_completed_consumer(store: AutoReplyStore):
    store.enqueue_reply_task(
        conversation_id="cid-reconcile",
        conversation_title="付款审批",
        single_chat=True,
        trigger_message_id="msg-reconcile",
        trigger_create_time="2026-09-10 12:00:00",
        trigger_sender="刘紫煜",
        trigger_text="请审批",
        execution_generation="gen-1",
    )
    task = store.claim_reply_tasks(limit=1)[0]
    consumer = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="worker-1",
    ).run
    store.complete_agent_run(consumer.id, {"outcome": "proposed"}, owner="worker-1")
    store.fail_reply_task(
        task.id,
        "audit_rejected_duplicate",
        expected_execution_generation=task.execution_generation,
    )
    return task, consumer


def _record(store: AutoReplyStore, task, run, *, key: str = "reconciled-key"):
    return store.record_completed_agent_message_delivery(
        agent_run_id=run.id,
        external_action_key=key,
        business_object_key=task.business_object_key,
        action_identity="clarify_payment_calculation",
        operation="message.send",
        target_identifiers={"open_dingtalk_id": TARGET_ID},
        conversation_id=task.conversation_id,
        trigger_message_id=task.trigger_message_id,
        reply_text=REPLY_TEXT,
        provider_result={"reconciled_from_agent_run_id": run.id},
    )


def test_ledger_records_the_completed_consumer_run_that_produced_the_receipt(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task, consumer = _failed_task_with_completed_consumer(store)

    sent = _record(store, task, consumer)

    assert sent.external_action_key == "reconciled-key"
    with store._connect() as db:
        assert db.execute(
            "select first_agent_run_id from external_action_results "
            "where external_action_key=?",
            ("reconciled-key",),
        ).fetchone()[0] == consumer.id


def test_ledger_still_rejects_a_run_belonging_to_another_business_object(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    _, consumer = _failed_task_with_completed_consumer(store)

    with pytest.raises(ValueError, match="run identity mismatch"):
        store.record_completed_agent_message_delivery(
            agent_run_id=consumer.id,
            external_action_key="reconciled-key",
            business_object_key="message:dingtalk:someone-else:msg-9",
            action_identity="clarify_payment_calculation",
            operation="message.send",
            target_identifiers={"open_dingtalk_id": TARGET_ID},
            conversation_id="cid-reconcile",
            trigger_message_id="msg-reconcile",
            reply_text=REPLY_TEXT,
            provider_result={},
        )


def test_failed_task_closes_once_its_delivery_is_in_the_ledger(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task, consumer = _failed_task_with_completed_consumer(store)
    _record(store, task, consumer)

    assert store.resolve_reconciled_failed_reply_task(
        task.id, external_action_key="reconciled-key"
    )

    reloaded = store.get_reply_task(task.id)
    assert reloaded is not None
    assert reloaded.status == "done"
    assert reloaded.error == ""


def test_failed_task_stays_failed_when_no_delivery_was_recorded(tmp_path: Path) -> None:
    """The close is only ever a consequence of the ledger, never a shortcut."""
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task, _ = _failed_task_with_completed_consumer(store)

    with pytest.raises(ValueError, match="not recorded"):
        store.resolve_reconciled_failed_reply_task(
            task.id, external_action_key="reconciled-key"
        )

    reloaded = store.get_reply_task(task.id)
    assert reloaded is not None
    assert reloaded.status == "failed"
