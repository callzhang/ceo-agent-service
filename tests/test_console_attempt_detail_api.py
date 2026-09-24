"""Evidence the Attempt detail DTO must carry for a run to be reviewable."""

import json
from pathlib import Path

import pytest

from app.audit_web import handle_rerun_attempt_post
from app.store import AgentRole, AutoReplyStore
from app.web_api.attempts import (
    _consumer_result_payload as build_consumer_result_payload,
    _linked_consumer_run,
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
            "final_result_json": json.dumps(partial_result),
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
    "account_id": "mailbox",
    "action_identity": TRIGGER,
    "action_type": "unsubscribe",
    "category": "junk",
    "classification_id": 42,
    "action_plan_id": "email-action-plan:plan-digest",
    "stable_message_identity": "mailbox:message-id:<msg@host>",
    "action_parameters": {"candidate_source": "body_html_https"},
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
            "outcome": "executed",
            "summary": "The reviewed update was published.",
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


def test_recorded_calls_survive_an_attempt_whose_transcript_is_gone(
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
    assert [use["tool"] for use in item["tool_uses"]] == ["unsubscribe_email"]


def test_email_attempt_carries_its_message_and_unsubscribe_receipt(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = _seed_email_attempt(store, with_agent_runs=False)

    _, item = build_attempt_detail(store, attempt_id, email_store=_FakeEmailStore())

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

    _, item = build_attempt_detail(store, attempt_id, email_store=_FakeEmailStore())

    assert item is not None
    assert item["email"] is None


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


def test_attempt_detail_labels_a_manual_rerun_as_not_audit_feedback(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _consumer_result_task(store)
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
                "update agent_runs set final_result_json=? where id=?",
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
            "update agent_runs set final_result_json=? where id=?",
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
        db.execute("update agent_runs set final_result_json='' where id=?", (consumer.id,))
    attempt_id = _finalize_consumer_result_attempt(store, task, consumer)

    _, item = build_attempt_detail(store, attempt_id)

    assert item is not None
    assert item["consumer_result"] == {
        "confidence": "—",
        "information_completeness": "—",
        "rule_coverage": "—",
        "risk": "—",
        "error_reason": "Consumer 未保存最终结果",
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
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _consumer_result_task(store)
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
