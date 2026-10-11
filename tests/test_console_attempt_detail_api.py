"""Evidence the Attempt detail DTO must carry for a run to be reviewable."""

import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from app.audit_web import handle_rerun_attempt_post
from app.store import AgentRole, AutoReplyStore
from app.web_api.attempts import (
    _consumer_result_payload as build_consumer_result_payload,
    _linked_consumer_run,
    _runtime_payload,
    build_attempt_detail,
)


CONVERSATION = "email-thread:thread-digest"
TRIGGER = "email-action:action-digest"

CONSUMER_EVENTS = [
    {
        "tool": "read_thread",
        "call_id": "call-consumer",
        "args": {"conversation_id": CONVERSATION},
        "output": "3 条邮件",
    }
]
AUDIT_EVENTS = [
    {
        "tool": "unsubscribe_email",
        "call_id": "call-audit",
        "args": {"task_id": 1},
        "output": '{"outcome": "skipped_no_reliable_entry"}',
    }
]


@pytest.mark.parametrize("run_status", ["completed", "failed", "running"])
def test_runtime_payload_preserves_business_status_separately(
    tmp_path: Path, run_status: str
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _consumer_result_task(store)
    run = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="audit-result-status",
        owner="audit-status-test",
    ).run
    runtime = store.claim_agent_runtime_attempt(
        run.id, "codex_oauth", "codex_cli", "local_oauth", "test-model"
    )
    store.complete_agent_runtime_attempt(runtime.id, "", "", 0, 0)
    if run_status == "failed":
        run = store.fail_agent_run(
            run.id, {"code": "provider_risk_rejected"}, owner="audit-status-test"
        )
    elif run_status == "completed":
        run = store.complete_agent_run(
            run.id, {"outcome": "returned"}, owner="audit-status-test"
        )
    before = store.list_agent_runtime_attempts(run.id)

    payload = _runtime_payload([run], store)

    assert payload[0]["status"] == "completed"
    assert payload[0]["run_status"] == run_status
    assert store.get_agent_run(run.id) == run
    assert store.list_agent_runtime_attempts(run.id) == before


def test_claude_native_range_is_readable_without_a_codex_viewer_link(
    tmp_path: Path, monkeypatch,
) -> None:
    from app import native_trajectory

    native = tmp_path / "claude.jsonl"
    native.write_text('{"type":"assistant","message":{"content":[]}}\n')
    monkeypatch.setattr(native_trajectory, "claude_session_path", lambda *_: native)
    run = SimpleNamespace(
        id=1, role=AgentRole.CONSUMER, execution_generation="initial",
        proposal_revision=0, turn_attempt=0, status="completed",
    )
    attempt = SimpleNamespace(
        runtime_kind="claude_cli", session_id="claude-session",
        transcript_start=0, transcript_end=1, attempt_number=1,
        route_name="claude", credential_mode="local_oauth", model="test",
        status="completed", failure_code="", failover_permitted=False,
        first_effect_started_at="", created_at="", finished_at="",
    )
    store = SimpleNamespace(
        list_agent_runtime_attempts_for_runs=lambda _ids: {1: [attempt]}
    )

    [entry] = _runtime_payload([run], store)
    assert entry["session_available"] is True
    assert entry["session_url"] == ""
    assert entry["native_reason"] == ""


def test_runtime_payload_uses_batch_runtime_attempt_lookup(tmp_path: Path, monkeypatch):
    store = AutoReplyStore(tmp_path / "runtime-payload-batch.sqlite3")
    task = _consumer_result_task(store)
    run = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="runtime-batch",
        owner="audit-runtime-batch",
    ).run
    runtime = store.claim_agent_runtime_attempt(
        run.id, "codex_oauth", "codex_cli", "local_oauth", "test-model"
    )
    batch_calls = []
    original_batch = store.list_agent_runtime_attempts_for_runs

    def record_batch(run_ids):
        batch_calls.append(run_ids)
        return original_batch(run_ids)

    monkeypatch.setattr(store, "list_agent_runtime_attempts_for_runs", record_batch)
    monkeypatch.setattr(
        store,
        "list_agent_runtime_attempts",
        lambda _run_id: pytest.fail("per-run runtime attempt query was used"),
    )

    payload = _runtime_payload([run], store)

    assert [item["attempt_number"] for item in payload] == [runtime.attempt_number]
    assert batch_calls == [[run.id]]


def test_linked_consumer_run_falls_back_to_latest_consumer_sibling():
    audit = type("Run", (), {"id": 3, "role": "audit", "parent_agent_run_id": None})()
    old = type("Run", (), {"id": 1, "role": "consumer", "turn_attempt": 0, "proposal_revision": 0})()
    latest = type("Run", (), {"id": 2, "role": "consumer", "turn_attempt": 1, "proposal_revision": 0})()
    assert _linked_consumer_run(audit, [old, latest, audit]) is latest


def test_consumer_result_prefers_current_generation_when_attempt_has_no_run_id():
    old_failed = type(
        "Run",
        (),
        {
            "id": 99,
            "role": "consumer",
            "status": "failed",
            "turn_attempt": 4,
            "proposal_revision": 0,
            "final_result_json": "",
                "adopted_result_json": "",
            "structured_error_json": '{"code":"service_restart_before_effect"}',
        },
    )()
    partial_result = _consumer_result_payload(confidence=0.97, risk="low")
    partial_result.pop("information_completeness")
    partial_result.pop("rule_coverage")
    current = type(
        "Run",
        (),
        {
            "id": 100,
            "role": "consumer",
            "status": "completed",
            "turn_attempt": 0,
            "proposal_revision": 0,
                "adopted_result_json": json.dumps(partial_result),
            "structured_error_json": "",
        },
    )()

    result = build_consumer_result_payload(None, [old_failed, current], [current], None)

    assert result["confidence"] == "97%"
    assert result["risk"] == "low"
    assert result["error_reason"] == ""


def test_attempt_detail_loads_task_runs_when_attempt_has_no_run_id(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _consumer_result_task(store)
    consumer = _complete_consumer_run(store, task, owner="task-run-fallback")
    attempt_id = _finalize_consumer_result_attempt(store, task, consumer)
    with store._immediate_write_transaction() as db:
        db.execute("update reply_attempts set agent_run_id=null where id=?", (attempt_id,))
    _, item = build_attempt_detail(store, attempt_id)
    assert item is not None
    assert item["consumer_result"]["confidence"] == "82%"


TRIGGER_PAYLOAD = {
    "schema": "email_agent_action.v1",
    "lifecycle_version": "email_unsubscribe_audited_v2",
    "account_id": "mailbox",
    "action_identity": TRIGGER,
    "action_type": "unsubscribe",
    "category": "junk",
    "classification_id": 42,
    "action_plan_id": "email-action-plan:plan-digest",
    "action_plan_version": 1,
    "stable_message_identity": "mailbox:message-id:<msg@host>",
    "thread_identity": "thread-digest",
    "action_parameters": {
        "candidate_source": "body_html_https",
        "instruction": "Private execution detail that must not remain in completed input",
    },
}


def _consumer_result_payload(
    *,
    confidence: float = 0.82,
    information_completeness: float = 0.75,
    rule_coverage: float = 1.0,
    risk: str = "medium",
) -> dict[str, object]:
    return {
        "outcome": "proposal",
        "summary": "Publish the reviewed update.",
        "proposal": {
            "objective": "Publish the reviewed update.",
            "actions": [
                {
                    "description": "Publish the update.",
                    "action_identity": "publish-reviewed-update",
                    "capability": "dingtalk-chat",
                    "operation": "send_to_group",
                    "target": {"conversation_id": "cid-consumer-api-result"},
                    "payload": {"text": "Reviewed update"},
                }
            ],
            "sourced_facts": [],
            "authored_judgment": "The update is ready to publish.",
        },
        "decision_options": [],
        "error": {
            "code": "",
            "retryable": False,
            "authorization_required": False,
        },
        "risk": risk,
        "confidence": confidence,
        "rule_coverage": rule_coverage,
        "information_completeness": information_completeness,
    }


def _consumer_result_task(store: AutoReplyStore):
    assert store.enqueue_reply_task(
        conversation_id="cid-consumer-api-result",
        conversation_title="Consumer API result",
        single_chat=False,
        trigger_message_id="msg-consumer-api-result",
        trigger_create_time="2026-09-14 09:00:00",
        trigger_sender="Avery",
        trigger_text="Publish the reviewed update.",
        trigger_message_json=json.dumps(
            {
                "open_conversation_id": "cid-consumer-api-result",
                "open_message_id": "msg-consumer-api-result",
                "conversation_title": "Consumer API result",
                "single_chat": False,
                "sender_name": "Avery",
                "create_time": "2026-09-14 09:00:00",
                "content": "Publish the reviewed update.",
            }
        ),
        execution_generation="consumer-api-result-generation",
    )
    task = store.claim_reply_task(1)
    assert task is not None
    return task


def _complete_consumer_run(
    store: AutoReplyStore,
    task,
    *,
    owner: str,
    payload: dict[str, object] | None = None,
    proposal_revision: int = 0,
    parent_agent_run_id: int | None = None,
):
    run = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=proposal_revision,
        turn_attempt=0,
        parent_agent_run_id=parent_agent_run_id,
        operation_id="",
        owner=owner,
    ).run
    return store.complete_agent_run(
        run.id, payload or _consumer_result_payload(), owner=owner
    )


def _complete_audit_run(store: AutoReplyStore, task, consumer, *, owner: str):
    candidate = store.adopted_candidate_for_consumer_run(consumer.id)
    assert candidate is not None
    run = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=consumer.id,
        operation_id=f"audit-{owner}",
        owner=owner,
    ).run
    return store.complete_agent_run(
        run.id,
        {
            "outcome": "approve",
            "summary": "The reviewed update was published.",
            "proposal_revision": 0,
            "candidate_digest": candidate["candidate_digest"],
            "feedback": None,
            "error": {"code": "", "retryable": False, "authorization_required": False},
            "risk": "low",
            "confidence": 1.0,
            "rule_coverage": 1.0,
            "information_completeness": 1.0,
        },
        owner=owner,
    )


def _finalize_consumer_result_attempt(store: AutoReplyStore, task, terminal_run) -> int:
    return store.finalize_orchestrated_reply_task(
        task_id=task.id,
        expected_execution_generation=task.execution_generation,
        run_id=terminal_run.id,
        task_status="done",
        task_error="",
        available_at="",
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        codex_reason="The reviewed update was published.",
        codex_session_id="",
        codex_transcript_start_line=0,
        codex_transcript_end_line=0,
        audit_tool_events_json="[]",
        audit_summary="The reviewed update was published.",
        send_status="completed",
        send_error="",
        channel="dingtalk",
    )


def _failed_provider_risk_attempt(
    store: AutoReplyStore, *, error: dict[str, object]
):
    task = _consumer_result_task(store)
    consumer = _complete_consumer_run(store, task, owner="risk-guard-consumer")
    audit = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=consumer.id,
        operation_id="audit-risk-guard",
        owner="risk-guard-audit",
    ).run
    audit = store.fail_agent_run(audit.id, error, owner="risk-guard-audit")
    attempt_id = store.finalize_orchestrated_reply_task(
        task_id=task.id,
        expected_execution_generation=task.execution_generation,
        run_id=audit.id,
        task_status="failed",
        task_error="provider_risk_rejected",
        available_at="",
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        codex_reason="The runtime refused this outbound action.",
        codex_session_id="",
        codex_transcript_start_line=0,
        codex_transcript_end_line=0,
        audit_tool_events_json="[]",
        audit_summary="The runtime refused this outbound action.",
        send_status="failed",
        send_error="agent_reported_failure",
        channel="dingtalk",
    )
    return task, audit, attempt_id


@pytest.mark.parametrize(
    "error",
    [
        {"code": "provider_risk_rejected"},
        {"code": "agent_reported_failure", "source_code": "provider_risk_rejected"},
    ],
)
def test_provider_risk_rejection_disables_history_rerun_and_explains_why(
    tmp_path: Path, error: dict[str, object]
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    _task, _run, attempt_id = _failed_provider_risk_attempt(store, error=error)

    status, item = build_attempt_detail(store, attempt_id)

    assert status == 200
    assert item is not None
    assert item["actions"]["can_rerun"] is False
    assert "此入口不能重放历史候选" in item["actions"]["rerun_block_reason"]
    assert "此入口不能重放历史候选" in item["status"]["message"]
    assert "提交新候选并重新审核" in item["failure_reason"]


def test_provider_risk_rejection_is_blocked_at_the_rerun_handler(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task, _run, attempt_id = _failed_provider_risk_attempt(
        store,
        error={"code": "agent_reported_failure", "source_code": "provider_risk_rejected"},
    )

    status, _headers, body = handle_rerun_attempt_post(store, attempt_id)

    unchanged_task = store.get_reply_task(task.id)
    assert status == 409
    assert "此入口不能重放历史候选" in body
    assert unchanged_task is not None
    assert unchanged_task.status == "failed"
    assert unchanged_task.manual_rerun_attempt_id == 0


class _FakeEmailStore:
    """The two email reads the Attempt DTO needs, with real row shapes."""

    def get_classification(self, classification_id: int) -> dict[str, object] | None:
        if classification_id != 42:
            return None
        return {
            "subject": "有 4 种新图像风格等你尝试",
            "sender": "noreply@email.openai.com",
            "folder": "已删除邮件",
            "received_at": "Sat, 12 Sep 2026 06:18:39 +0000",
            "rfc_message_id": "<msg@host>",
        }

    def get_email_unsubscribe_receipt(self, action_identity: str) -> dict[str, object] | None:
        if action_identity != TRIGGER:
            return None
        return {
            "outcome": "skipped_no_reliable_entry",
            "evidence": "page-not-operable",
            "result_text": "host='r.openai.com' control_count=0 modelled_controls=0",
            "receipt_id": "unsubscribe-receipt:5fd912d9:no_reliable_entry",
            "entry_reference": "unsubscribe-entry:870a914f",
            "entry_url": "https://r.openai.com/asm/unsubscribe?token=private-token",
            "started_at": "2026-09-12T06:21:29+00:00",
            "completed_at": "2026-09-12T06:21:29+00:00",
        }

    def list_email_unsubscribe_steps(self, action_identity: str) -> list[dict[str, object]]:
        if action_identity != TRIGGER:
            return []
        return [
            {
                "sequence": 1,
                "operation": "open_entry",
                "state": "skipped_no_reliable_entry",
            }
        ]


def _seed_email_attempt(store: AutoReplyStore, *, with_agent_runs: bool) -> int:
    store.enqueue_reply_task(
        conversation_id=CONVERSATION,
        conversation_title="Email unsubscribe",
        single_chat=True,
        trigger_message_id=TRIGGER,
        trigger_create_time="2026-09-12 06:18:39",
        trigger_sender="noreply@email.openai.com",
        trigger_text="Immutable ActionPlan authorizes unsubscribe.",
        execution_generation="initial",
        channel="email",
        trigger_message_json=json.dumps(TRIGGER_PAYLOAD),
    )
    attempt_id = store.record_reply_attempt(
        conversation_id=CONVERSATION,
        conversation_title="Email unsubscribe",
        trigger_message_id=TRIGGER,
        trigger_sender="noreply@email.openai.com",
        trigger_text="Immutable ActionPlan authorizes unsubscribe.",
        action="agent_run",
        sensitivity_kind="general",
        send_status="skipped",
        channel="email",
        codex_session_id="session-audit",
        codex_transcript_start_line=0,
        codex_transcript_end_line=59,
        audit_tool_events_json=json.dumps(AUDIT_EVENTS),
    )
    if not with_agent_runs:
        return attempt_id
    with store._connect() as db:
        task_id = db.execute(
            "select id from reply_tasks where conversation_id=? and trigger_message_id=?",
            (CONVERSATION, TRIGGER),
        ).fetchone()[0]
        consumer = db.execute(
            """insert into agent_runs (
                reply_task_id, execution_generation, role, status,
                codex_session_id, transcript_start_line, transcript_end_line
            ) values (?, 'initial', 'consumer', 'completed', 'session-consumer', 0, 42)""",
            (task_id,),
        ).lastrowid
        audit = db.execute(
            """insert into agent_runs (
                reply_task_id, execution_generation, role, status,
                parent_agent_run_id, codex_session_id,
                transcript_start_line, transcript_end_line
            ) values (?, 'initial', 'audit', 'completed', ?, 'session-audit', 0, 59)""",
            (task_id, consumer),
        ).lastrowid
        db.execute(
            "update reply_attempts set agent_run_id=? where id=?", (audit, attempt_id)
        )
    return attempt_id


@pytest.fixture
def readable_transcripts(monkeypatch):
    """Serve both roles' transcript slices without a Codex session on disk."""
    slices = {
        ("session-consumer", 0, 42): CONSUMER_EVENTS,
        ("session-audit", 0, 59): AUDIT_EVENTS,
    }
    monkeypatch.setattr(
        "app.codex_history.find_codex_session_path",
        lambda session_id, **_kwargs: Path(f"/sessions/{session_id}.jsonl"),
    )
    monkeypatch.setattr(
        "app.audit_web.extract_codex_audit_events_from_session",
        lambda session_id, start_line=0, end_line=None: slices.get(
            (session_id, start_line, end_line or 0), []
        ),
    )


def test_email_attempt_is_not_presented_as_a_dingtalk_conversation(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = _seed_email_attempt(store, with_agent_runs=False)

    status, item = build_attempt_detail(store, attempt_id)

    assert status == 200
    assert item is not None
    # An email thread identity cannot open a DingTalk conversation, so the page
    # must not offer one.
    assert item["conversation"]["label"] == "邮件"
    assert item["actions"]["dingtalk_url"] == ""


def test_oa_attempt_detail_opens_the_original_approval_link(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    approval_url = (
        "https://aflow.dingtalk.com/dingtalk/mobile/homepage.htm?"
        "corpid=ding-example&procInstId=proc-1&taskId=&dinghash=approval#approval"
    )
    stored_url = "https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId="
    trigger_text = f"审批通知\n[{approval_url}]({approval_url})"
    trigger_json = json.dumps(
        {"content": trigger_text, "raw_payload": {"source": "dingtalk_notification"}},
        ensure_ascii=False,
    )
    store.enqueue_reply_task(
        conversation_id="cid-oa-link",
        conversation_title="OA审批",
        single_chat=False,
        trigger_message_id="msg-oa-link",
        trigger_create_time="2026-09-28 09:00:00",
        trigger_sender="OA审批",
        trigger_text=trigger_text,
        trigger_message_json=trigger_json,
        oa_url=stored_url,
        channel="dingtalk",
    )
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-oa-link",
        conversation_title="OA审批",
        trigger_message_id="msg-oa-link",
        trigger_sender="OA审批",
        trigger_text=trigger_text,
        action="oa_approval",
        sensitivity_kind="general",
        send_status="completed",
        channel="dingtalk",
        oa_process_instance_id="proc-1",
        oa_url=stored_url,
    )

    status, item = build_attempt_detail(store, attempt_id)

    assert status == 200
    assert item is not None
    assert item["oa"]["url"] == approval_url
    assert item["actions"]["dingtalk_url"] == (
        "/open-dingtalk-oa-popup?url=" + quote(approval_url, safe="")
    )


def test_attempt_detail_reaches_every_role_transcript(
    tmp_path: Path, readable_transcripts
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = _seed_email_attempt(store, with_agent_runs=True)

    _, item = build_attempt_detail(store, attempt_id)

    assert item is not None
    sessions = {session["role"]: session for session in item["agent_sessions"]}
    assert set(sessions) == {"consumer", "audit"}
    # The Consumer transcript exists on disk; linking only the Attempt's own
    # session id made it unreachable from the console.
    assert sessions["consumer"]["url"] == "/codex/session-consumer"
    assert sessions["audit"]["url"] == "/codex/session-audit"
    # The calls live in the Agent record at those URLs, not copied here.
    assert "tool_uses" not in sessions["consumer"]
    assert item["tool_uses"] == []


def test_missing_transcript_does_not_resurrect_copied_attempt_calls(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = _seed_email_attempt(store, with_agent_runs=True)
    monkeypatch.setattr(
        "app.codex_history.find_codex_session_path",
        lambda _session_id, **_kwargs: None,
    )

    _, item = build_attempt_detail(store, attempt_id)

    assert item is not None
    assert item["agent_sessions"] == []
    assert item["tool_uses"] == []


def test_email_attempt_carries_its_message_and_unsubscribe_receipt(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = _seed_email_attempt(store, with_agent_runs=False)

    _, item = build_attempt_detail(store, attempt_id, email_store_factory=_FakeEmailStore)

    assert item is not None
    email = item["email"]
    assert email["classification_url"] == "/email?tab=list&selected=42"
    assert email["subject"] == "有 4 种新图像风格等你尝试"
    assert email["folder"] == "已删除邮件"
    assert email["action_type"] == "unsubscribe"
    assert email["candidate_source"] == "body_html_https"
    # Without the receipt, the page cannot say what the authorized action did.
    assert email["unsubscribe"]["outcome"] == "skipped_no_reliable_entry"
    assert email["unsubscribe"]["evidence"] == "page-not-operable"
    assert "r.openai.com" in email["unsubscribe"]["result_text"]
    # The private entry is execution input, not receipt evidence.  It must
    # never cross the Attempt API boundary, where clicking it would bypass the
    # recorded skipped outcome and start a separate external operation.
    assert "entry_url" not in email["unsubscribe"]
    assert email["unsubscribe"]["steps"] == [
        {"sequence": 1, "operation": "open_entry", "state": "skipped_no_reliable_entry"}
    ]


def test_completed_email_attempt_keeps_adopted_metadata_after_input_compaction(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = _seed_email_attempt(store, with_agent_runs=False)
    _, before = build_attempt_detail(
        store, attempt_id, email_store_factory=_FakeEmailStore,
    )
    assert before is not None
    original_email = before["email"]
    assert original_email["category"] == "junk"
    assert original_email["candidate_source"] == "body_html_https"

    task = store.get_reply_task_for_message(CONVERSATION, TRIGGER, channel="email")
    assert task is not None
    claimed = store.claim_reply_task(task.id)
    assert claimed is not None
    store.complete_reply_task(
        task.id, expected_execution_generation=claimed.execution_generation,
    )
    compacted = store.get_reply_task(task.id)
    assert compacted is not None and compacted.input_compacted
    assert compacted.trigger_text == ""
    projection = json.loads(compacted.trigger_message_json)
    assert projection["category"] == "junk"
    assert projection["action_parameters"] == {
        "candidate_source": "body_html_https"
    }

    _, after = build_attempt_detail(
        store, attempt_id, email_store_factory=_FakeEmailStore,
    )
    assert after is not None
    assert after["email"] == original_email


def test_non_email_attempt_has_no_email_context(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        send_status="sent",
    )

    _, item = build_attempt_detail(store, attempt_id, email_store_factory=_FakeEmailStore)

    assert item is not None
    assert item["email"] is None


@pytest.mark.parametrize("channel", ["dingtalk", "wechat", None])
def test_history_detail_does_not_initialize_unrelated_email_store(
    tmp_path: Path, monkeypatch, channel
):
    from tests.test_console_web_api import _client

    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = 999999
    if channel is not None:
        attempt_id = store.record_reply_attempt(
            channel=channel,
            conversation_id="context-test",
            conversation_title="Context test",
            trigger_message_id="message-test",
            trigger_sender="Sender",
            trigger_text="Status request",
            action="send_reply",
            sensitivity_kind="general",
            send_status="failed",
        )

    def unrelated_email_store(_path):
        raise AssertionError("non-email history must not scan email durable state")

    with _client(tmp_path) as client:
        monkeypatch.setattr("app.audit_web.EmailStore", unrelated_email_store)
        response = client.get(f"/api/console/history/{attempt_id}")

    assert response.status_code == (404 if channel is None else 200)
    if channel is not None:
        assert response.json()["item"]["email"] is None


def test_email_history_detail_reuses_initialized_store_and_reads_fresh_context(
    tmp_path: Path, monkeypatch
):
    from tests.test_console_web_api import _client

    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = _seed_email_attempt(store, with_agent_runs=False)
    initializations = []
    reads = []

    class FreshEmailStore(_FakeEmailStore):
        def get_classification(self, classification_id):
            reads.append(classification_id)
            result = super().get_classification(classification_id)
            return {**result, "subject": f"Revision {len(reads)}"}

    def email_store_factory(path, **kwargs):
        initializations.append((path, kwargs))
        return FreshEmailStore()

    monkeypatch.setattr("app.audit_web.EmailStore", email_store_factory)
    with _client(tmp_path) as client:
        assert len(initializations) == 1
        first = client.get(f"/api/console/history/{attempt_id}")
        second = client.get(f"/api/console/history/{attempt_id}")

    assert first.status_code == second.status_code == 200
    assert len(initializations) == 1
    assert initializations[0][1]["validate_rows"] is False
    assert len(reads) == 2
    assert first.json()["item"]["email"]["subject"] == "Revision 1"
    assert second.json()["item"]["email"]["subject"] == "Revision 2"
    assert second.json()["item"]["email"]["unsubscribe"]["evidence"] == "page-not-operable"


def test_email_store_validation_failure_does_not_block_unrelated_web_routes(
    tmp_path: Path, monkeypatch
):
    from app.email_store import EmailPersistenceCorruption
    from tests.test_console_web_api import _client

    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = _seed_email_attempt(store, with_agent_runs=False)

    def invalid_email_store(_path, **_kwargs):
        raise EmailPersistenceCorruption("test-invalid-email-state")

    monkeypatch.setattr("app.audit_web.EmailStore", invalid_email_store)
    with _client(tmp_path) as client:
        assert client.get("/healthz").status_code == 200
        with pytest.raises(EmailPersistenceCorruption, match="test-invalid-email-state"):
            client.get(f"/api/console/history/{attempt_id}")


def test_codex_session_roles_resolve_both_transcripts_of_one_attempt(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = _seed_email_attempt(store, with_agent_runs=True)

    consumer = store.list_codex_session_attempt_roles("session-consumer")
    audit = store.list_codex_session_attempt_roles("session-audit")

    # The Attempt row stores only the last role's session, so matching on it
    # left the Consumer transcript with no business record at all.
    assert consumer == [{"id": attempt_id, "status": "skipped", "role": "consumer"}]
    assert audit == [{"id": attempt_id, "status": "skipped", "role": "audit"}]


def test_attempt_detail_api_projects_latest_effective_run_result_read_only(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _consumer_result_task(store)
    consumer = _complete_consumer_run(store, task, owner="consumer-api")
    audit = _complete_audit_run(store, task, consumer, owner="audit-api")
    _complete_consumer_run(
        store,
        task,
        owner="later-consumer-api",
        proposal_revision=1,
        parent_agent_run_id=audit.id,
        payload=_consumer_result_payload(
            confidence=0.10,
            information_completeness=0.20,
            rule_coverage=0.30,
            risk="high",
        ),
    )
    attempt_id = _finalize_consumer_result_attempt(store, task, audit)
    before = {
        "attempt": store.get_reply_attempt(attempt_id),
        "task": store.get_reply_task(task.id),
        "consumer": store.get_agent_run(consumer.id),
        "audit": store.get_agent_run(audit.id),
        "runs": store.list_agent_runs_for_task_generation(
            task.id, task.execution_generation
        ),
    }

    status, item = build_attempt_detail(store, attempt_id)

    assert status == 200
    assert item is not None
    assert store.get_reply_attempt(attempt_id) == before["attempt"]
    assert store.get_reply_task(task.id) == before["task"]
    assert store.get_agent_run(consumer.id) == before["consumer"]
    assert store.get_agent_run(audit.id) == before["audit"]
    assert store.list_agent_runs_for_task_generation(
        task.id, task.execution_generation
    ) == before["runs"]
    assert item["consumer_result"] == {
        "confidence": "10%",
        "information_completeness": "20%",
        "rule_coverage": "30%",
        "risk": "high",
        "error_reason": "",
        "current_run": None,
        "from_run_id": audit.id + 1,
    }


def test_attempt_detail_api_projects_direct_terminal_consumer_result(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _consumer_result_task(store)
    consumer = _complete_consumer_run(store, task, owner="direct-consumer-api")
    attempt_id = _finalize_consumer_result_attempt(store, task, consumer)

    status, item = build_attempt_detail(store, attempt_id)

    assert status == 200
    assert item is not None
    assert item["consumer_result"] == {
        "confidence": "82%",
        "information_completeness": "75%",
        "rule_coverage": "100%",
        "risk": "medium",
        "error_reason": "",
        "current_run": None,
        "from_run_id": consumer.id,
    }


def test_attempt_detail_names_a_recorded_delivery_when_the_task_is_done(
    tmp_path: Path,
):
    """A queue-level `done` must not hide a chat reply the ledger proves sent."""
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _consumer_result_task(store)
    consumer = _complete_consumer_run(store, task, owner="delivery-status-api")
    attempt_id = _finalize_consumer_result_attempt(store, task, consumer)
    store.record_sent_reply(
        task.conversation_id,
        task.trigger_message_id,
        "The reviewed update was sent.",
        send_result_json='{"openTaskId":"provider-task-1"}',
    )

    status, item = build_attempt_detail(store, attempt_id)

    assert status == 200
    assert item is not None
    assert item["status"]["raw"] == "done"
    assert item["status"]["message"] == "已向 Consumer API result 发送回复，并已记录投递回执。"


def test_attempt_detail_labels_a_manual_rerun_as_not_audit_feedback(tmp_path: Path, monkeypatch):
    from app.dws_client import DingTalkMessage, DwsClient
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _consumer_result_task(store)
    monkeypatch.setattr(DwsClient, "read_message_by_id",
                        lambda *_: DingTalkMessage.model_validate_json(task.trigger_message_json))
    consumer = _complete_consumer_run(store, task, owner="manual-rerun-api")
    attempt_id = _finalize_consumer_result_attempt(store, task, consumer)

    status, _, _ = handle_rerun_attempt_post(store, attempt_id)
    assert status == 303

    _, item = build_attempt_detail(store, attempt_id)

    assert item is not None
    assert {"label": "当前批次", "value": "人工重新处理：不是审核反馈后的复审。"} in item["metadata"]


@pytest.mark.parametrize(
    ("stored_result", "failure", "expected_error"),
    [
        ({"outcome": "proposal", "raw_marker": "raw-malformed-marker"}, None, "Consumer 结果不符合当前契约"),
        (None, {"code": "consumer_source_failed", "detail": "Source read failed"}, "Source read failed"),
    ],
)
def test_attempt_detail_api_marks_unavailable_consumer_result_per_field(
    tmp_path: Path,
    stored_result: dict[str, object] | None,
    failure: dict[str, str] | None,
    expected_error: str,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _consumer_result_task(store)
    if failure is None:
        consumer = _complete_consumer_run(store, task, owner="malformed-api")
        with store._immediate_write_transaction() as db:
            db.execute(
                    "update review_candidates set candidate_json=? where consumer_run_id=?",
                (json.dumps(stored_result), consumer.id),
            )
    else:
        claimed = store.claim_agent_run(
            task.id,
            task.execution_generation,
            role=AgentRole.CONSUMER,
            proposal_revision=0,
            turn_attempt=0,
            parent_agent_run_id=None,
            operation_id="",
            owner="failed-api",
        ).run
        consumer = store.fail_agent_run(claimed.id, failure, owner="failed-api")
    attempt_id = _finalize_consumer_result_attempt(store, task, consumer)

    status, item = build_attempt_detail(store, attempt_id)

    assert status == 200
    assert item is not None
    result = item["consumer_result"]
    assert result["confidence"] == "—"
    assert result["information_completeness"] == "—"
    assert result["rule_coverage"] == "—"
    assert result["risk"] == "—"
    assert result["error_reason"] == expected_error
    assert "raw-malformed-marker" not in json.dumps(result)


def test_attempt_detail_api_preserves_valid_metrics_from_partial_consumer_result(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _consumer_result_task(store)
    consumer = _complete_consumer_run(store, task, owner="partial-api")
    partial = _consumer_result_payload(confidence=0.97, risk="low")
    partial.pop("information_completeness")
    partial.pop("rule_coverage")
    with store._immediate_write_transaction() as db:
        db.execute(
                "update review_candidates set candidate_json=? where consumer_run_id=?",
            (json.dumps(partial), consumer.id),
        )
    attempt_id = _finalize_consumer_result_attempt(store, task, consumer)

    status, item = build_attempt_detail(store, attempt_id)

    assert status == 200
    assert item is not None
    assert item["consumer_result"] == {
        "confidence": "97%",
        "information_completeness": "100%",
        "rule_coverage": "100%",
        "risk": "low",
        "error_reason": "",
        "current_run": None,
        "from_run_id": consumer.id,
    }


def test_attempt_detail_api_marks_completed_consumer_without_result_unavailable(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _consumer_result_task(store)
    consumer = _complete_consumer_run(store, task, owner="missing-result-api")
    with store._immediate_write_transaction() as db:
            db.execute("update review_candidates set candidate_json='' where consumer_run_id=?", (consumer.id,))
    attempt_id = _finalize_consumer_result_attempt(store, task, consumer)

    _, item = build_attempt_detail(store, attempt_id)

    assert item is not None
    assert item["consumer_result"] == {
        "confidence": "—",
        "information_completeness": "—",
        "rule_coverage": "—",
        "risk": "—",
            "error_reason": "Consumer 已采用业务结果不可用",
        "current_run": None,
    }


def test_attempt_detail_api_recovers_consumer_when_audit_parent_link_is_missing(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _consumer_result_task(store)
    consumer = _complete_consumer_run(store, task, owner="missing-consumer-api")
    audit = _complete_audit_run(store, task, consumer, owner="missing-audit-api")
    attempt_id = _finalize_consumer_result_attempt(store, task, audit)
    with store._immediate_write_transaction() as db:
        db.execute("update agent_runs set parent_agent_run_id=null where id=?", (audit.id,))

    _, item = build_attempt_detail(store, attempt_id)

    assert item is not None
    assert item["consumer_result"]["confidence"] == "100%"
    assert item["consumer_result"]["from_run_id"] == audit.id
    assert item["consumer_result"]["error_reason"] == ""


def test_attempt_detail_api_recovers_consumer_when_audit_parent_is_invalid(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _consumer_result_task(store)
    consumer = _complete_consumer_run(store, task, owner="wrong-role-consumer-api")
    audit = _complete_audit_run(store, task, consumer, owner="wrong-role-audit-api")
    attempt_id = _finalize_consumer_result_attempt(store, task, audit)
    with store._immediate_write_transaction() as db:
        db.execute("update agent_runs set parent_agent_run_id=? where id=?", (audit.id, audit.id))

    _, item = build_attempt_detail(store, attempt_id)

    assert item is not None
    assert item["consumer_result"]["confidence"] == "100%"
    assert item["consumer_result"]["from_run_id"] == audit.id
    assert item["consumer_result"]["error_reason"] == ""


def test_attempt_detail_api_keeps_old_metrics_while_current_generation_is_pending_then_running(
    tmp_path: Path,
    monkeypatch,
):
    from app.dws_client import DingTalkMessage, DwsClient
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _consumer_result_task(store)
    monkeypatch.setattr(DwsClient, "read_message_by_id",
                        lambda *_: DingTalkMessage.model_validate_json(task.trigger_message_json))
    consumer = _complete_consumer_run(store, task, owner="completed-api")
    attempt_id = _finalize_consumer_result_attempt(store, task, consumer)

    status, _, _ = handle_rerun_attempt_post(store, attempt_id)
    assert status == 303
    _, pending = build_attempt_detail(store, attempt_id)

    assert pending is not None
    assert pending["consumer_result"] == {
        "confidence": "82%",
        "information_completeness": "75%",
        "rule_coverage": "100%",
        "risk": "medium",
        "error_reason": "",
        "current_run": {"id": None, "status": "pending"},
        "from_run_id": consumer.id,
    }

    current_task = store.claim_reply_task(task.id)
    assert current_task is not None
    running = store.claim_agent_run(
        current_task.id,
        current_task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="running-api",
    ).run

    _, running_detail = build_attempt_detail(store, attempt_id)

    assert running_detail is not None
    assert running_detail["consumer_result"] == {
        "confidence": "82%",
        "information_completeness": "75%",
        "rule_coverage": "100%",
        "risk": "medium",
        "error_reason": "",
        "current_run": {"id": running.id, "status": "running"},
        "from_run_id": consumer.id,
    }


@pytest.mark.parametrize('error,refused', [
    ({'code': 'provider_risk_rejected', 'retryable': False}, True),
    ({'code': 'agent_reported_failure', 'source_code': 'provider_risk_rejected'}, True),
    ({'code': 'source_read_failed', 'detail': 'provider_risk_rejected'}, False),
    ({'code': 'source_read_failed', 'nested': {'code': 'provider_risk_rejected'}}, False),
    (['provider_risk_rejected'], False),
    ('malformed-json', False),
])
def test_failed_attempt_rerun_presentation_uses_structured_historical_refusal(
    tmp_path: Path, error, refused: bool,
):
    from app.audit_web import render_attempt_detail
    store = AutoReplyStore(tmp_path / 'worker.sqlite3')
    task = _consumer_result_task(store)
    run = store.claim_agent_run(task.id, task.execution_generation, role=AgentRole.AUDIT,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
        operation_id='refusal-display', owner='test').run
    # A later failed generation must still describe the original refusal.
    raw = error if error == 'malformed-json' else json.dumps(error)
    with store._connect() as db:
        db.execute("update agent_runs set status='failed', execution_generation='old-generation', "
                   "structured_error_json=? where id=?", (raw, run.id))
        db.execute("update reply_tasks set status='failed', error='agent_reported_failure' where id=?", (task.id,))
    attempt_id = store.record_reply_attempt(
        conversation_id=task.conversation_id, conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id, trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text, action='send_reply', sensitivity_kind='normal',
        send_status='failed', channel='dingtalk',
    )
    with store._connect() as db:
        db.execute('update reply_attempts set agent_run_id=? where id=?', (run.id, attempt_id))
    before = store.get_agent_run(run.id)
    _, item = build_attempt_detail(store, attempt_id)
    assert item['actions']['can_rerun'] is (not refused)
    assert item['actions']['rerun_url'] == f'/api/console/history/{attempt_id}/rerun'
    expected_label = '重新评估候选' if refused else '重新处理'
    assert item['actions']['rerun_label'] == expected_label
    assert item['actions']['rerun_confirmation'] == (
        '确认重新评估候选？不会重放历史被拒执行。' if refused else '确认重新处理这条 Attempt？'
    )
    if refused:
        assert '此入口不能重放历史候选' in item['status']['message']
        assert item['actions']['rerun_block_reason'] == item['status']['message']
    else:
        assert item['status']['message'] == '这次处理没有完成，可重新处理当前事项。'
    status, html = render_attempt_detail(store, attempt_id)
    assert status == 200
    assert (f'>{expected_label}</button>' in html) is (not refused)
    assert ('此入口不能重放历史候选' in html) is refused
    assert ('历史候选不可重放' in html) is refused
    assert store.get_agent_run(run.id) == before
    assert store.get_reply_task(task.id).status == 'failed'
    with store._connect() as db:
        assert db.execute('select count(*) from candidate_executions').fetchone()[0] == 0


def test_failed_attempt_does_not_inherit_another_business_objects_refusal(tmp_path: Path):
    store = AutoReplyStore(tmp_path / 'worker.sqlite3')
    task = _consumer_result_task(store)
    run = store.claim_agent_run(task.id, task.execution_generation, role=AgentRole.AUDIT,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
        operation_id='other-object', owner='test').run
    store.fail_agent_run(run.id, {'code': 'provider_risk_rejected'}, owner='test')
    # Create an unrelated failed Attempt with no risk-bearing business object.
    other = store.record_reply_attempt(conversation_id='other', conversation_title='Other',
        trigger_message_id='other-message', trigger_sender='Avery', trigger_text='provider_risk_rejected',
        action='send_reply', sensitivity_kind='normal', send_status='failed', channel='dingtalk')
    _, item = build_attempt_detail(store, other)
    assert item['actions']['rerun_label'] == '重新处理'
    assert item['actions']['can_rerun'] is True
    assert '不重放历史被拒执行' not in item['status']['message']
