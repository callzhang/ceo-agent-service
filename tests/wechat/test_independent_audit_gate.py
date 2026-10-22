"""A WeChat sender cannot act on a Consumer decision without independent review."""

import json
from types import SimpleNamespace

import pytest

from app.action_storage import task_delivery_set_parts
from app.agent_runtime_router import RoutedCodexExecutionError
from app.dingtalk_models import CodexAction, CodexDecision
from app.store import AgentRole, AutoReplyStore
from app.wechat import service
from app.wechat.audit import (
    WechatAuditDecision, WechatDeliveryAuditor, wechat_audit_candidate,
    wechat_audit_digest,
)


def _delivery_and_parent(tmp_path):
    store = AutoReplyStore(tmp_path / "wechat-audit.sqlite3")
    store.enqueue_reply_task(
        channel="wechat", conversation_id="chat-1", conversation_title="Alex",
        single_chat=True, trigger_message_id="message-1",
        trigger_create_time="2026-10-21T09:00:00+08:00",
        trigger_sender="Alex", trigger_text="今天能回复吗？",
    )
    task = store.claim_reply_tasks(1, channel="wechat")[0]
    parent = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.CONSUMER,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
        operation_id="", owner="consumer",
    ).run
    store.complete_agent_run(parent.id, CodexDecision(
        action=CodexAction.SEND_REPLY, reply_text="可以，今天回复。",
        reason="direct question", audit_summary="Consumer self-report",
    ).model_dump(mode="json"), owner="consumer")
    store.finalize_wechat_reply_task(
        task_id=task.id,
        expected_execution_generation=task.execution_generation,
        action="send_reply", sensitivity_kind="normal",
        codex_reason="direct question", draft_reply_text="可以，今天回复。",
        audit_summary="Consumer self-report", send_status="pending",
        account_id="account-1", target_type="direct", target_id="chat-1",
        conversation_id="chat-1", reply_text="可以，今天回复。",
    )
    delivery = store.get_wechat_delivery_for_task(task.id)
    assert delivery is not None
    assert delivery.status == "ready_to_send"
    return store, task, delivery, parent


def test_unreviewed_consumer_delivery_cannot_be_claimed(tmp_path):
    store, task, delivery, _parent = _delivery_and_parent(tmp_path)
    assert store.claim_wechat_delivery(
        delivery.id, expected_execution_generation=task.execution_generation,
    ) is None
    assert store.get_wechat_delivery_by_id(delivery.id).status == "ready_to_send"


def test_service_never_sends_unreviewed_delivery_even_with_sender_stub(tmp_path):
    store, _task, delivery, _parent = _delivery_and_parent(tmp_path)

    class Sender:
        def send(self, *_args):
            raise AssertionError("unreviewed delivery reached sender")

    sender = Sender()
    assert service.process_ready_wechat_deliveries(
        store, sender, mode="auto", sender_enabled=True,
    ) == 0
    with pytest.raises(ValueError, match="awaits independent audit"):
        service.approve_wechat_delivery(store, sender, delivery.id)
    assert store.get_wechat_delivery_by_id(delivery.id).status == "ready_to_send"


def test_independent_approval_binds_exact_prepared_delivery(tmp_path, monkeypatch):
    store, task, delivery, parent = _delivery_and_parent(tmp_path)
    digest = wechat_audit_digest(wechat_audit_candidate(task, delivery))
    decision = WechatAuditDecision(
        outcome="approve", candidate_digest=digest, summary="Current and correctly addressed",
    )
    audit = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.AUDIT,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=parent.id,
        operation_id=f"wechat-delivery:{delivery.id}", owner="audit",
    ).run
    store.complete_agent_run(audit.id, decision.model_dump(mode="json"), owner="audit")
    original_get_agent_run = store.get_agent_run

    def native_result(run_id, **kwargs):
        run = original_get_agent_run(run_id, **kwargs)
        if run_id == audit.id:
            return run.model_copy(update={"final_result_json": decision.model_dump_json()})
        return run

    monkeypatch.setattr(store, "get_agent_run", native_result)
    store.record_wechat_delivery_review(delivery.id, audit.id, decision)
    assert store.wechat_delivery_review_approved(delivery.id)
    claimed = store.claim_wechat_delivery(
        delivery.id, expected_execution_generation=task.execution_generation,
    )
    assert claimed is not None
    assert claimed.reply_text == delivery.reply_text


def test_independent_rejection_prevents_delivery(tmp_path, monkeypatch):
    store, task, delivery, parent = _delivery_and_parent(tmp_path)
    decision = WechatAuditDecision(
        outcome="reject",
        candidate_digest=wechat_audit_digest(wechat_audit_candidate(task, delivery)),
        summary="Outdated promise",
    )
    audit = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.AUDIT,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=parent.id,
        operation_id=f"wechat-delivery:{delivery.id}", owner="audit",
    ).run
    store.complete_agent_run(audit.id, decision.model_dump(mode="json"), owner="audit")
    original_get_agent_run = store.get_agent_run
    monkeypatch.setattr(
        store, "get_agent_run",
        lambda run_id, **kwargs: original_get_agent_run(run_id, **kwargs).model_copy(
            update={"final_result_json": decision.model_dump_json()}
        ),
    )
    store.record_wechat_delivery_review(delivery.id, audit.id, decision)
    assert store.get_wechat_delivery_by_id(delivery.id).status == "failed"
    assert store.requeue_unperformed_wechat_deliveries(retry_delay_seconds=0) == 0
    assert store.claim_wechat_delivery(
        delivery.id, expected_execution_generation=task.execution_generation,
    ) is None


def test_model_only_audit_reviews_exact_prepared_reply_before_send(
    tmp_path, monkeypatch,
):
    store, task, delivery, _parent = _delivery_and_parent(tmp_path)
    decision = WechatAuditDecision(
        outcome="approve",
        candidate_digest=wechat_audit_digest(wechat_audit_candidate(task, delivery)),
        summary="The answer matches the frozen request and recipient",
    )

    class Reader:
        def read_messages(self, account, **kwargs):
            assert account == "account"
            assert kwargs["conversation_id"] == delivery.conversation_id
            return []

    class Runtime:
        def __init__(self):
            self.calls = []

        def execute(self, **kwargs):
            self.calls.append(kwargs)
            assert kwargs["command_factory"].model_only
            assert "service-owned postfix" in kwargs["command_factory"].developer_instructions
            assert kwargs["workload_kind"] == "agent_run"
            candidate = json.loads(kwargs["prompt"])["candidate"]
            assert candidate["reply_text"] == delivery.reply_text
            assert candidate["target_id"] == delivery.target_id
            return SimpleNamespace(value=decision.model_dump_json(), transcript_end=0)

    import app.native_trajectory as native_trajectory
    monkeypatch.setattr(
        native_trajectory, "read_native_result_json_from_attempt",
        lambda _db, _row: decision.model_dump_json(),
    )
    runtime = Runtime()
    assert WechatDeliveryAuditor(store, runtime, Reader(), "account").review(delivery)
    assert len(runtime.calls) == 1
    assert store.wechat_delivery_review_approved(delivery.id)
    assert store.claim_wechat_delivery(
        delivery.id, expected_execution_generation=task.execution_generation,
    ) is not None


@pytest.mark.parametrize(
    ("field", "changed"),
    [("reply_text", "不同的正文"), ("target_id", "different-chat")],
)
def test_review_cannot_authorize_changed_body_or_target(
    tmp_path, monkeypatch, field, changed,
):
    store, task, delivery, parent = _delivery_and_parent(tmp_path)
    decision = WechatAuditDecision(
        outcome="approve",
        candidate_digest=wechat_audit_digest(wechat_audit_candidate(task, delivery)),
        summary="Approved original candidate",
    )
    audit = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.AUDIT,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=parent.id,
        operation_id=f"wechat-delivery:{delivery.id}", owner="audit",
    ).run
    store.complete_agent_run(audit.id, decision.model_dump(mode="json"), owner="audit")
    original_get_agent_run = store.get_agent_run
    monkeypatch.setattr(
        store, "get_agent_run",
        lambda run_id, **kwargs: original_get_agent_run(run_id, **kwargs).model_copy(
            update={"final_result_json": decision.model_dump_json()}
        ),
    )
    store.record_wechat_delivery_review(delivery.id, audit.id, decision)
    assignments, args = task_delivery_set_parts("wechat_deliveries", {field: changed})
    with store._connect() as db:
        db.execute(
            f"update actions set {assignments} "
            "where record_type='wechat_deliveries' and cast(legacy_key as integer)=?",
            (*args, delivery.id),
        )
    assert not store.wechat_delivery_review_approved(delivery.id)
    assert store.claim_wechat_delivery(
        delivery.id, expected_execution_generation=task.execution_generation,
    ) is None


def test_429_wait_reclaims_same_audit_run_and_resumes(tmp_path, monkeypatch):
    store, task, delivery, _parent = _delivery_and_parent(tmp_path)
    decision = WechatAuditDecision(
        outcome="approve",
        candidate_digest=wechat_audit_digest(wechat_audit_candidate(task, delivery)),
        summary="Correct recipient and current answer",
    )

    class Reader:
        def read_messages(self, *_args, **_kwargs):
            return []

    class Runtime:
        calls = []

        def execute(self, **kwargs):
            self.calls.append(kwargs["workload_key"])
            if len(self.calls) == 1:
                raise RoutedCodexExecutionError(
                    "runtime_execution_failed", failure_code="provider_capacity",
                    retryable_external_dependency=True,
                )
            return SimpleNamespace(value=decision.model_dump_json(), transcript_end=0)

    import app.native_trajectory as native_trajectory
    monkeypatch.setattr(
        native_trajectory, "read_native_result_json_from_attempt",
        lambda _row, _attempt: decision.model_dump_json(),
    )
    runtime = Runtime()
    auditor = WechatDeliveryAuditor(store, runtime, Reader(), "account")
    assert not auditor.review(delivery)
    runs = store.list_agent_runs_for_task_generation(
        task.id, task.execution_generation, load_events=False,
    )
    audit = next(run for run in runs if run.role is AgentRole.AUDIT)
    assert audit.status == "running"
    assert store.recover_orphaned_agent_runs_for_terminal_reply_tasks() == 0
    assert store.get_agent_run(audit.id, load_events=False).status == "running"
    with store._connect() as db:
        db.execute(
            "update runs set lease_expires_at='2000-01-01 00:00:00' "
            "where record_type='agent_runs' and id=?",
            (audit.id,),
        )
    assert auditor.review(delivery)
    assert runtime.calls == [str(audit.id), str(audit.id)]
    assert store.wechat_delivery_review_approved(delivery.id)
