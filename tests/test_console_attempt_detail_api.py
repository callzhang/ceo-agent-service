"""Evidence the Attempt detail DTO must carry for a run to be reviewable."""

import json
from pathlib import Path

import pytest

from app.store import AutoReplyStore
from app.web_api.attempts import build_attempt_detail


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


def test_attempt_detail_carries_each_role_calls_and_transcript(
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
    assert [use["tool"] for use in sessions["consumer"]["tool_uses"]] == ["read_thread"]
    audit_call = sessions["audit"]["tool_uses"][0]
    assert audit_call["tool"] == "unsubscribe_email"
    assert "skipped_no_reliable_entry" in audit_call["output"]


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
