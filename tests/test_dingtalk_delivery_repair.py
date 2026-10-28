"""Historical receipt correction preserves evidence and never permits resend."""

import json
import hashlib
from types import SimpleNamespace

import pytest

from app.agent_contracts import ConsumerAgentResult
from app.agent_orchestrator import AgentOrchestrator
from app.attempt_projection import project_attempt_status
from app.dingtalk_delivery_repair import (
    apply_delivery_reverification,
    apply_native_chat_readback,
    collect_native_chat_readback,
    native_reply_text_matches,
)
from app.action_storage import (
    AGENT_EFFECT_INTENT_SQL, NATIVE_CANDIDATE_ACTION_ATTEMPT_SQL,
    NATIVE_EXTERNAL_ACTION_RESULT_SQL, insert_action, insert_error_action,
)
from app.current_action_schema import current_reply_action_projection
from app.store import NATIVE_REVIEW_CANDIDATE_SQL
from app.system_executor import ActionOutcome, SystemExecutor
from tests.test_agent_orchestrator import ScriptedAudit, ScriptedConsumer, candidate, task_and_context
from tests.test_dingtalk_delivery_verification import reply_receipt


def historical_receipt(tmp_path):
    store, task, context = task_and_context(tmp_path)
    data = candidate().model_dump(mode="json")
    action = data["proposal"]["actions"][0]
    action.update(capability="dingtalk-chat", operation="reply_to_message",
                  target={"conversation_id": "conversation", "message_id": "trigger"},
                  payload={"content": "Exact prepared advice"})
    consumer = ScriptedConsumer(store, ConsumerAgentResult.model_validate(data))
    audit = ScriptedAudit(store, "approve")
    old_result = {"provider_result": reply_receipt(), "verification": {
        "state": "sent", "open_task_id": "", "status_result": {},
    }}

    class Handler:
        sends = 0
        reads = 0
        confirmed = False

        def dispatch(self, action, **kwargs):
            self.sends += 1
            return ActionOutcome("verified", old_result)

        def reconcile(self, action, **kwargs):
            self.reads += 1
            if self.confirmed:
                return ActionOutcome("verified", {"provider_result": reply_receipt(), "verification": {
                    "state": "sent", "open_task_id": "send-task-1",
                    "status_result": {"success": True, "result": {"sendStatus": "SUCCESS"}},
                }})
            return ActionOutcome("uncertain", {"reason": "task_not_exists"})

    handler = Handler()
    driver = AgentOrchestrator(store=store, consumer=consumer, audit=audit,
        system_executor=SystemExecutor(store, {("dingtalk-chat", "reply_to_message"): handler}, owner="worker"))
    assert driver.process(task, context, refresh_context=lambda: context).status == "executed"
    store.complete_reply_task(task.id, expected_execution_generation=task.execution_generation)
    reviewed = store.current_reviewed_candidate(task.id, task.execution_generation)
    execution = store.get_candidate_execution(reviewed["id"])
    attempt = store.list_candidate_action_attempts(execution["id"])[0]
    receipt = store.get_candidate_external_action(attempt["external_action_key"])
    return store, task, context, driver, handler, receipt, execution


def proof(receipt, state):
    return {"key": receipt["external_action_key"], "original_provider_json": receipt["provider_result_json"],
        "queried_at": "2026-10-14T02:00:00Z", "verification": {
            "state": state, "open_task_id": "send-task-1",
            "status_result": {"success": True, "result": {"sendStatus": "SUCCESS"}} if state == "sent" else {},
        }}


def test_failed_current_task_does_not_inherit_historical_completion():
    assert project_attempt_status(SimpleNamespace(send_status="completed"),
        SimpleNamespace(status="failed", execution_generation="initial"), []) == "failed"


@pytest.mark.parametrize("state", ["ambiguous", "failed"])
def test_restart_recovery_cannot_complete_unverified_recorded_delivery(tmp_path, state):
    store, task, _context, _driver, _handler, receipt, _execution = historical_receipt(tmp_path)
    assert apply_delivery_reverification(store, proof(receipt, state))
    assert store.reconcile_failed_reply_tasks_with_recorded_deliveries() == 0
    assert store.get_reply_task(task.id).status == "failed"
    with pytest.raises(ValueError, match="not recorded"):
        store.resolve_reconciled_failed_reply_task(task.id, external_action_key=receipt["external_action_key"])
    with store._connect() as db:
        from app.task_entity_storage import update_task_cas
        # Reverification withdrew completion; restore the stale done projection
        # to exercise startup repair against the same native generation.
        assert update_task_cas(db, task_id=task.id, expected={"status": "failed"},
                               changes={"status": "done"}) is not None
        # Model a later failing current Run directly on its native physical owner.
        db.execute("update runs set status='failed',structured_error_json=? "
                   "where id=(select max(id) from runs where task_id=? and role in ('consumer','audit') "
                   "and execution_generation=?)",
                   ('{"code":"later_failure"}', task.id, task.execution_generation))
    assert store.reconcile_done_reply_tasks_with_failed_current_run() == 1
    assert store.get_reply_task(task.id).status == "failed"


def test_restart_recovery_keeps_confirmed_recorded_delivery(tmp_path):
    store, task, _context, _driver, _handler, receipt, _execution = historical_receipt(tmp_path)
    assert apply_delivery_reverification(store, proof(receipt, "sent"))
    with store._connect() as db:
        from app.task_entity_storage import update_task_cas
        assert update_task_cas(db, task_id=task.id, expected={"status": "done"},
                               changes={"status": "failed"}) is not None
    assert store.reconcile_failed_reply_tasks_with_recorded_deliveries() == 1
    assert store.get_reply_task(task.id).status == "done"


def _native_chat_proof(task, sent, *, mutation=""):
    row = {
        "conversationId": task.conversation_id,
        "messageId": "outgoing-message-1",
        "sender": "Principal",
        "senderId": "principal-open-id",
        "createTime": "2026-10-15 10:00:00",
        "text": sent.reply_text,
        "quotedMessage": {"messageId": task.trigger_message_id},
    }
    payload = {
        "contractVersion": "im.message-list.v1",
        "messages": [row],
        "count": 1,
        "complete": True,
        "partial": False,
        "hasMore": False,
        "failures": [],
        "failedCount": 0,
        "paginationKnown": True,
        "truncated": False,
        "truncatedByPageLimit": False,
        "truncatedByResultLimit": False,
        "pagesFetched": 1,
        "queryRange": {
            "startTime": "2026-10-15T09:55:00+08:00",
            "endTime": "2026-10-15T10:05:00+08:00",
            "order": "asc",
        },
    }
    if mutation == "partial":
        payload["complete"] = False
    elif mutation == "wrong_sender":
        row["senderId"] = "other-user"
    elif mutation == "wrong_text":
        row["text"] = "different body"
    elif mutation == "wrong_quote":
        row["quotedMessage"]["messageId"] = "different-trigger"
    elif mutation == "wrong_mention":
        row["text"] = f"@Someone {sent.reply_text}"
    elif mutation == "extra_prefix":
        row["text"] = f"@{task.trigger_sender} unrelated {sent.reply_text}"
    elif mutation == "wrong_range":
        payload["queryRange"]["startTime"] = "2026-10-15T00:55:00Z"
    return {
        "task_id": task.id,
        "payload": payload,
        "queried_at": "2026-10-15T02:00:00Z",
        "start": "2026-10-15T09:55:00+08:00",
        "end": "2026-10-15T10:05:00+08:00",
        "principal_user_id": "principal-user-id",
        "principal_open_dingtalk_id": "principal-open-id",
    }


def test_exact_complete_native_chat_readback_closes_without_resending(tmp_path):
    store, task, _context, _driver, handler, receipt, _execution = historical_receipt(tmp_path)
    assert apply_delivery_reverification(store, proof(receipt, "ambiguous"))
    sent = store.get_sent_reply(task.conversation_id, task.trigger_message_id)
    readback = _native_chat_proof(task, sent)

    assert apply_native_chat_readback(store, readback)
    assert store.get_reply_task(task.id).status == "done"
    assert handler.sends == 1
    result = json.loads(store.get_candidate_external_action(receipt["external_action_key"])["provider_result_json"])
    assert result["verification"]["source"] == "native_chat_readback"
    assert result["verification"]["message_id"] == "outgoing-message-1"
    assert result["verification_repair"]["native_chat_readback"]["quoted_trigger_id"] == task.trigger_message_id
    assert not apply_native_chat_readback(store, readback)


def _insert_effect_intent(db, receipt):
    from app import action_storage
    from app.storage_payload import ACTION_RECORD_TYPES

    values = {
        "authorization_id": "historical-authorization", "action_index": 0,
        "receipt_operation_id": "historical-operation", "capability": "dingtalk-chat",
        "operation": "reply_to_message", "operation_digest": "operation-digest",
        "arguments_digest": "arguments-digest",
        "target_identifiers_json": receipt["target_identifiers_json"],
        "state": "acknowledged", "result_digest": "result-digest", "exit_code": 0,
        "prepared_at": "historical-prepared", "dispatched_at": "historical-dispatched",
        "acknowledged_at": "historical-acknowledged", "updated_at": "historical-updated",
        "external_action_key": receipt["external_action_key"],
    }
    payload = action_storage._action_payload(db, "agent_effect_intents", values)
    db.execute(
        "insert into actions(_kind,legacy_key,agent_run_id,payload_json) values(?,?,?,?)",
        (ACTION_RECORD_TYPES.code("agent_effect_intents"), "1",
         receipt["first_agent_run_id"], payload),
    )


def _effect_intent_rows(db, run_id):
    return db.execute(
        "select id,_kind,legacy_key,agent_run_id,payload_json from actions "
        "where record_type='agent_effect_intents' and agent_run_id=? order by id",
        (run_id,),
    ).fetchall()


def _legacy_native_readback_case(tmp_path, monkeypatch, *, with_effect_intent=False):
    """Seed historical delivery evidence on current native Task/Run/Action owners."""
    from app.run_entity_storage import create_run
    from app.shared_action_lifecycle import record_sent_action
    from app.store import AutoReplyStore
    from app.task_entity_storage import update_task_cas

    path = tmp_path / "historical-current.sqlite3"
    store = AutoReplyStore(path)
    store.enqueue_reply_task(
        channel="dingtalk", conversation_id="conversation",
        conversation_title="Test", single_chat=True,
        trigger_message_id="trigger", trigger_create_time="2026-10-04 00:00:00",
        trigger_sender="Derek", trigger_text="Review",
        trigger_message_json="{}", execution_generation="historical-generation",
        business_object_key="historical-reply-task",
    )
    task = store.get_reply_task_for_message("conversation", "trigger")
    assert task is not None

    provider_json = json.dumps({
        "provider_result": reply_receipt(),
        "verification": {
            "state": "ambiguous", "open_task_id": "send-task-1",
            "status_result": {"success": True, "result": {"sendStatus": "SUCCESS"}},
        },
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    target_json = json.dumps({
        "conversation_id": "conversation", "message_id": "trigger",
    }, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(provider_json.encode()).hexdigest()
    with store._immediate_write_transaction() as db:
        assert update_task_cas(
            db, task_id=task.id, expected={"status": "pending"},
            changes={"status": "failed", "error": "external_action_uncertain"},
        ) is not None
        run = create_run(db, values={
            "task_id": task.id, "execution_generation": task.execution_generation,
            "role": "consumer", "status": "completed",
            "started_at": "historical-start", "completed_at": "historical-complete",
        })
        run_id = int(run["id"])
        attempt = insert_action(db, "reply_attempts", {
            "conversation_id": task.conversation_id,
            "conversation_title": task.conversation_title,
            "trigger_message_id": task.trigger_message_id,
            "trigger_sender": task.trigger_sender,
            "trigger_text": task.trigger_text,
            "action": "reply", "sensitivity_kind": "none",
            "send_status": "completed", "agent_run_id": run_id,
            "channel": task.channel, "draft_reply_text": "Exact prepared advice",
            "final_reply_text": "Exact prepared advice",
        })
        attempt_id = int(attempt.lastrowid)
        # This event predates candidate approval. It is retained historical
        # Action evidence and deliberately has no adopted candidate linkage.
        record_sent_action(
            db, operation="reply_to_message", target_json=target_json,
            provider_json=provider_json, result_digest=digest,
            external_action_key="historical-provider-effect",
            business_object_key="historical-reply-task",
            action_identity="historical-reply-action", channel="dingtalk",
            conversation_id="conversation", trigger_message_id="trigger",
            reply_text="Exact prepared advice", feedback_token="",
            agent_run_id=run_id, task_id=task.id,
            observed_at="historical-receipt-time",
        )
        if with_effect_intent:
            _insert_effect_intent(db, {
                "target_identifiers_json": target_json,
                "external_action_key": "historical-provider-effect",
                "first_agent_run_id": run_id,
            })

    receipt = None
    with store._connect() as db:
        receipt = db.execute(
            f"select * from ({NATIVE_EXTERNAL_ACTION_RESULT_SQL}) where external_action_key=?",
            ("historical-provider-effect",),
        ).fetchone()
        receipt = dict(receipt)
    sent = store.get_sent_reply(task.conversation_id, task.trigger_message_id)
    readback = _native_chat_proof(task, sent)
    return store, task, receipt, readback, attempt_id


def _legacy_delivery_facts(store, task, external_action_key):
    from app.action_storage import (
        SENT_REPLY_SQL,
    )

    with store._connect() as db:
        action = db.execute(
            "select id,action_external_action_key,action_business_object_key,action_action_identity "
            "from actions where record_type='action' and action_external_action_key=?",
            (external_action_key,),
        ).fetchall()
        result = db.execute(
            f"select external_action_key,business_object_key,action_identity,first_agent_run_id "
            f"from ({NATIVE_EXTERNAL_ACTION_RESULT_SQL}) where external_action_key=?",
            (external_action_key,),
        ).fetchall()
        sent = db.execute(
            f"select id,external_action_key,reply_text from ({SENT_REPLY_SQL}) "
            "where channel=? and conversation_id=? and trigger_message_id=?",
            (task.channel, task.conversation_id, task.trigger_message_id),
        ).fetchall()
        candidates = db.execute(
            f"select id from ({NATIVE_REVIEW_CANDIDATE_SQL}) where task_id=?",
            (task.id,),
        ).fetchall()
        action_attempts = db.execute(
            f"select id from ({NATIVE_CANDIDATE_ACTION_ATTEMPT_SQL}) where external_action_key=?",
            (external_action_key,),
        ).fetchall()
        run_id = result[0][3]
        attempts = db.execute(
            f"select id,send_status,permission_action,reviewed_at,audit_tool_events_json,audit_documents_json "
            f"from ({current_reply_action_projection('reply_attempts')}) "
            "where agent_run_id=? and channel=? and conversation_id=? and trigger_message_id=?",
            (run_id, task.channel, task.conversation_id, task.trigger_message_id),
        ).fetchall()
    return {
        "action": tuple(tuple(row) for row in action),
        "result": tuple(tuple(row) for row in result),
        "sent": tuple(tuple(row) for row in sent),
        "candidate_ids": tuple(tuple(row) for row in candidates),
        "candidate_action_attempt_ids": tuple(tuple(row) for row in action_attempts),
        "reply_attempts": tuple(tuple(row) for row in attempts),
    }


@pytest.mark.parametrize("with_effect_intent", [False, True])
def test_native_chat_readback_settles_legacy_completed_reply_without_inventing_audit(
    tmp_path, monkeypatch, with_effect_intent,
):
    store, task, receipt, readback, attempt_id = _legacy_native_readback_case(
        tmp_path, monkeypatch, with_effect_intent=with_effect_intent,
    )
    original_provider = json.loads(receipt["provider_result_json"])
    sent_before = store.get_sent_reply(task.conversation_id, task.trigger_message_id)
    assert sent_before.reply_text == "Exact prepared advice"
    delivery_facts_before = _legacy_delivery_facts(store, task, receipt["external_action_key"])
    assert len(delivery_facts_before["action"]) == 1
    assert len(delivery_facts_before["result"]) == 1
    assert len(delivery_facts_before["sent"]) == 1
    assert delivery_facts_before["candidate_ids"] == ()
    assert delivery_facts_before["candidate_action_attempt_ids"] == ()
    assert len(delivery_facts_before["reply_attempts"]) == 1
    assert delivery_facts_before["reply_attempts"][0][0] == attempt_id
    with store._connect() as db:
        original_run = db.execute(
            "select id,task_id as reply_task_id,execution_generation,role,status from runs where id=?",
            (receipt["first_agent_run_id"],),
        ).fetchone()
        before_intents = _effect_intent_rows(db, receipt["first_agent_run_id"])
    assert len(before_intents) == (1 if with_effect_intent else 0)
    assert apply_native_chat_readback(store, readback)
    assert store.get_reply_task(task.id).status == "done"
    result = json.loads(store.get_candidate_external_action(receipt["external_action_key"])["provider_result_json"])
    assert result["provider_result"] == original_provider["provider_result"]
    assert result["verification_repair"]["previous_verification"] == original_provider["verification"]
    assert result["verification_repair"]["source_digest"] == hashlib.sha256(
        receipt["provider_result_json"].encode()
    ).hexdigest()
    assert result["verification_repair"]["approval_reconstructed"] is False
    assert result["verification_repair"]["native_chat_readback"]["quoted_trigger_id"] == task.trigger_message_id
    with store._connect() as db:
        settled_receipt = db.execute(
            f"select * from ({NATIVE_EXTERNAL_ACTION_RESULT_SQL}) where external_action_key=?",
            (receipt["external_action_key"],),
        ).fetchone()
        settled_run = db.execute(
            "select id,task_id as reply_task_id,execution_generation,role,status from runs where id=?",
            (receipt["first_agent_run_id"],),
        ).fetchone()
        assert settled_receipt["first_agent_run_id"] == receipt["first_agent_run_id"]
        assert tuple(settled_run) == tuple(original_run)
        settled_sent = store.get_sent_reply(task.conversation_id, task.trigger_message_id)
        assert settled_sent.reply_text == sent_before.reply_text
        after_intents = _effect_intent_rows(db, receipt["first_agent_run_id"])
        assert [tuple(row) for row in after_intents] == [tuple(row) for row in before_intents]
    delivery_facts_after = _legacy_delivery_facts(store, task, receipt["external_action_key"])
    assert delivery_facts_after == delivery_facts_before
    assert result["verification"]["source"] == "native_chat_readback"
    assert result["verification_repair"]["approval_reconstructed"] is False
    assert result["verification_repair"]["settlement_basis"] == "verified_existing_legacy_reply_delivery"
    with store._connect() as db:
        assert db.execute(
            "select count(*) from actions where record_type='review_candidates' and legacy_key=?",
            (str(task.id),),
        ).fetchone()[0] == 0
    assert not apply_native_chat_readback(store, readback)


@pytest.mark.parametrize("mutation", [
    "missing_attempt", "duplicate_attempt", "incomplete_attempt", "permission",
    "reviewed", "audit_tools", "audit_documents", "receipt_target",
])
def test_legacy_native_readback_requires_unreviewed_exact_historical_facts(tmp_path, mutation, monkeypatch):
    store, task, receipt, readback, attempt_id = _legacy_native_readback_case(tmp_path, monkeypatch)
    with store._immediate_write_transaction() as db:
        if mutation == "missing_attempt":
            from app.action_storage import delete_action

            delete_action(db, "reply_attempts", attempt_id)
        elif mutation == "duplicate_attempt":
            insert_action(db, "reply_attempts", {
                "conversation_id": task.conversation_id,
                "conversation_title": task.conversation_title,
                "trigger_message_id": task.trigger_message_id,
                "trigger_sender": task.trigger_sender,
                "trigger_text": task.trigger_text,
                "action": "reply", "sensitivity_kind": "none",
                "send_status": "completed", "agent_run_id": receipt["first_agent_run_id"],
                "channel": task.channel,
            })
        elif mutation in {"incomplete_attempt", "permission", "audit_tools", "audit_documents"}:
            from app.action_storage import update_action

            changes = {
                "incomplete_attempt": {"send_status": "failed"},
                "permission": {"permission_action": "send"},
                "audit_tools": {"audit_tool_events_json": '[{"tool":"audit_review","result":"queued"}]'},
                "audit_documents": {"audit_documents_json": '[{"document_id":"reviewed"}]'},
            }[mutation]
            assert update_action(db, "reply_attempts", attempt_id, changes).rowcount == 1
        elif mutation == "reviewed":
            from app.action_storage import update_action

            assert update_action(db, "reply_attempts", attempt_id, {
                "reviewed_at": "reviewed-at", "reviewer_feedback": "reviewed",
            }).rowcount == 1
        elif mutation == "receipt_target":
            from app.action_entity_storage import update_sent_receipt

            action_id = db.execute(
                "select id from actions where record_type='action' and action_external_action_key=?",
                (receipt["external_action_key"],),
            ).fetchone()["id"]
            binding = db.execute(
                "select id,target from action_receipts where action_id=? and provider_result_json is null "
                "and provider_receipt_id is not null and status is null and attempt_number is null "
                "and execution_run_id is null and action_index is null "
                "and id is not (select action_delivery_receipt_id from actions where id=?) "
                "and id is not (select action_prepared_receipt_id from actions where id=?) "
                "order by id limit 1",
                (action_id, action_id, action_id),
            ).fetchone()
            assert update_sent_receipt(
                db, action_id, receipt_id=binding["id"],
                expected={"target": binding["target"]}, changes={"target": "{}"},
            ) == binding["id"]

    with store._connect() as db:
        before_rejection = db.execute(
            f"select * from ({NATIVE_EXTERNAL_ACTION_RESULT_SQL}) where external_action_key=?",
            (receipt["external_action_key"],),
        ).fetchone()
        expected_target = "{}" if mutation == "receipt_target" else receipt["target_identifiers_json"]
        assert before_rejection["target_identifiers_json"] == expected_target
        before_provider = before_rejection["provider_result_json"]
        before_run = before_rejection["first_agent_run_id"]
        before_target = before_rejection["target_identifiers_json"]
        before_sent = store.get_sent_reply(task.conversation_id, task.trigger_message_id)
        before_attempt = db.execute(
            f"select authorization_id,capability,operation,state from ({AGENT_EFFECT_INTENT_SQL}) "
            "where agent_run_id=? order by id",
            (receipt["first_agent_run_id"],),
        ).fetchall()
        before_reply_attempts = db.execute(
            f"select id,send_status,permission_action,reviewed_at,audit_tool_events_json,audit_documents_json "
            f"from ({current_reply_action_projection('reply_attempts')}) "
            "where agent_run_id=? and channel=? and conversation_id=? and trigger_message_id=? order by id",
            (receipt["first_agent_run_id"], task.channel, task.conversation_id,
             task.trigger_message_id),
        ).fetchall()
    with pytest.raises(ValueError, match="legacy reply lacks"):
        apply_native_chat_readback(store, readback)
    assert store.get_reply_task(task.id).status == "failed"
    with store._connect() as db:
        after_rejection = db.execute(
            f"select * from ({NATIVE_EXTERNAL_ACTION_RESULT_SQL}) where external_action_key=?",
            (receipt["external_action_key"],),
        ).fetchone()
        after_sent = store.get_sent_reply(task.conversation_id, task.trigger_message_id)
        assert after_rejection["provider_result_json"] == before_provider
        assert after_rejection["first_agent_run_id"] == before_run
        assert after_rejection["target_identifiers_json"] == before_target
        after_attempt = db.execute(
            f"select authorization_id,capability,operation,state from ({AGENT_EFFECT_INTENT_SQL}) "
            "where agent_run_id=? order by id",
            (receipt["first_agent_run_id"],),
        ).fetchall()
        assert [tuple(row) for row in after_attempt] == [tuple(row) for row in before_attempt]
        after_reply_attempts = db.execute(
            f"select id,send_status,permission_action,reviewed_at,audit_tool_events_json,audit_documents_json "
            f"from ({current_reply_action_projection('reply_attempts')}) "
            "where agent_run_id=? and channel=? and conversation_id=? and trigger_message_id=? order by id",
            (receipt["first_agent_run_id"], task.channel, task.conversation_id,
             task.trigger_message_id),
        ).fetchall()
        assert [tuple(row) for row in after_reply_attempts] == [
            tuple(row) for row in before_reply_attempts
        ]
        assert after_sent.reply_text == before_sent.reply_text
        assert after_sent.agent_run_id == before_sent.agent_run_id


def test_native_chat_readback_accepts_exact_native_quoted_sender_prefix(tmp_path):
    store, task, _context, _driver, handler, receipt, _execution = historical_receipt(tmp_path)
    assert apply_delivery_reverification(store, proof(receipt, "ambiguous"))
    sent = store.get_sent_reply(task.conversation_id, task.trigger_message_id)
    readback = _native_chat_proof(task, sent)
    readback["payload"]["messages"][0]["text"] = (
        f"@{task.trigger_sender}  {sent.reply_text}"
    )

    assert apply_native_chat_readback(store, readback)
    assert store.get_reply_task(task.id).status == "done"
    assert handler.sends == 1


def test_native_chat_readback_accepts_provider_utc_range_canonicalization(tmp_path):
    store, task, _context, _driver, _handler, receipt, _execution = historical_receipt(tmp_path)
    assert apply_delivery_reverification(store, proof(receipt, "ambiguous"))
    sent = store.get_sent_reply(task.conversation_id, task.trigger_message_id)
    readback = _native_chat_proof(task, sent)
    readback["payload"]["queryRange"] = {
        "startTime": "2026-10-15T01:55:00Z",
        "endTime": "2026-10-15T02:05:00Z",
        "order": "asc",
    }

    assert apply_native_chat_readback(store, readback)
    assert store.get_reply_task(task.id).status == "done"


def test_native_reply_rendering_normalizes_only_whitespace():
    assert native_reply_text_matches("**Status**  - One", "**Status**\n\n- One", "Derek")
    assert native_reply_text_matches(
        "@Derek  **Status**  - One", "**Status**\n\n- One", "Derek"
    )
    assert not native_reply_text_matches(
        "@Derek extra **Status** - One", "**Status**\n\n- One", "Derek"
    )
    assert not native_reply_text_matches(
        "@Someone **Status** - One", "**Status**\n\n- One", "Derek"
    )


def test_native_chat_readback_queries_exact_conversation_without_write_tools(tmp_path):
    store, task, _context, _driver, _handler, _receipt, _execution = historical_receipt(tmp_path)
    sent = store.get_sent_reply(task.conversation_id, task.trigger_message_id)

    class ReadOnlyDws:
        dws_bin = "dws"

        def __init__(self):
            self.command = None

        def run_json(self, command):
            self.command = command
            payload = _native_chat_proof(task, sent)["payload"]
            payload["queryRange"] = {
                "startTime": command[command.index("--start") + 1],
                "endTime": command[command.index("--end") + 1],
                "order": "asc",
            }
            return payload

        def get_current_user_id(self):
            return "principal-user-id"

        def get_user_profile(self, user_id):
            assert user_id == "principal-user-id"
            return SimpleNamespace(open_dingtalk_id="principal-open-id")

    dws = ReadOnlyDws()
    proof = collect_native_chat_readback(store, dws, task.id)
    assert "--conversation-id" in dws.command
    assert task.conversation_id in dws.command
    assert "--page-all" in dws.command
    assert "--no-reactions" in dws.command
    assert not any("send" in part or "comment" in part for part in dws.command)
    assert proof["principal_open_dingtalk_id"] == "principal-open-id"
    assert proof["start"].endswith("+00:00")
    assert proof["end"].endswith("+00:00")


@pytest.mark.parametrize("mutation", [
    "partial", "wrong_sender", "wrong_text", "wrong_quote", "wrong_mention", "extra_prefix",
    "wrong_range",
])
def test_native_chat_readback_refuses_incomplete_or_mismatched_proof(tmp_path, mutation):
    store, task, _context, _driver, _handler, receipt, _execution = historical_receipt(tmp_path)
    assert apply_delivery_reverification(store, proof(receipt, "ambiguous"))
    sent = store.get_sent_reply(task.conversation_id, task.trigger_message_id)

    with pytest.raises(ValueError):
        apply_native_chat_readback(store, _native_chat_proof(task, sent, mutation=mutation))
    assert store.get_reply_task(task.id).status == "failed"


@pytest.mark.parametrize("state", ["ambiguous", "failed"])
@pytest.mark.parametrize("source", ["terminal_attempt", "settlement"])
def test_old_completion_projections_cannot_override_current_delivery_error(tmp_path, state, source):
    store, task, _context, _driver, _handler, receipt, _execution = historical_receipt(tmp_path)
    store.record_reply_attempt(conversation_id=task.conversation_id, conversation_title="",
        trigger_message_id=task.trigger_message_id, trigger_sender="", trigger_text="",
        action="reply", sensitivity_kind="", send_status="completed", channel=task.channel)
    with store._connect() as db:
        insert_error_action(db, {
            "conversation_id": task.conversation_id, "message_id": task.trigger_message_id,
            "kind": "reply_task_already_settled", "detail": "",
            "resolved_at": "2026-10-13", "resolution": "old completion",
        })
    assert apply_delivery_reverification(store, proof(receipt, state))
    recovery = (store.reconcile_unresolved_reply_tasks_with_terminal_attempts
                if source == "terminal_attempt" else store.reconcile_unresolved_reply_tasks_with_settlement_evidence)
    assert recovery() == 0
    assert store.get_reply_task(task.id).status == "failed"


@pytest.mark.parametrize("state,expected", [("ambiguous", "投递结果仍未确认"), ("failed", "投递失败")])
def test_detail_current_delivery_error_overrides_old_sent_ledger_and_summary(tmp_path, state, expected):
    from app.web_api.attempts import build_attempt_detail

    store, task, _context, _driver, _handler, receipt, _execution = historical_receipt(tmp_path)
    assert store.get_sent_reply(task.conversation_id, task.trigger_message_id) is not None
    attempt_id = store.record_reply_attempt(conversation_id=task.conversation_id, conversation_title="",
        trigger_message_id=task.trigger_message_id, trigger_sender="", trigger_text="",
        action="reply", sensitivity_kind="", send_status="completed", channel=task.channel,
        audit_summary="All approved actions verified")
    assert apply_delivery_reverification(store, proof(receipt, state))
    code, item = build_attempt_detail(store, attempt_id)
    assert code == 200
    assert item["status"]["raw"] == "failed"
    assert expected in item["status"]["message"]
    assert expected in item["status"]["attention"]["reason"]
    assert item["status"]["requires_decision"] is False


@pytest.mark.parametrize("state", ["sent", "ambiguous"])
def test_reverification_keeps_original_provider_and_previous_verifier(tmp_path, state):
    store, task, _context, _driver, _handler, receipt, execution = historical_receipt(tmp_path)
    evidence = proof(receipt, state)
    assert apply_delivery_reverification(store, evidence)
    assert not apply_delivery_reverification(store, evidence)
    updated = json.loads(store.get_candidate_external_action(receipt["external_action_key"])["provider_result_json"])
    original = json.loads(receipt["provider_result_json"])
    assert updated["provider_result"] == original["provider_result"]
    assert updated["verification_repair"]["previous_verification"] == original["verification"]
    assert updated["verification"] == evidence["verification"]
    assert store.get_reply_task(task.id).status == ("done" if state == "sent" else "failed")
    assert store.get_candidate_execution(execution["id"])["status"] == ("done" if state == "sent" else "uncertain")
    assert all(run.status == "completed" for run in store.list_agent_runs_for_task_generation(task.id, task.execution_generation, load_events=False))


def test_executor_reconciles_uncertain_historical_receipt_without_dispatch(tmp_path):
    store, task, context, driver, handler, receipt, _execution = historical_receipt(tmp_path)
    apply_delivery_reverification(store, proof(receipt, "ambiguous"))
    # Orchestration sends uncertain messages to a receipt-feedback revision.
    # Exercise the persisted executor reconciliation boundary directly here.
    reviewed = store.current_reviewed_candidate(task.id, task.execution_generation)
    result = driver.system_executor.execute(
        task, reviewed["id"], reviewed["review_id"], context=context,
    )
    assert result.error.code == "external_action_uncertain"
    assert (handler.sends, handler.reads) == (1, 1)


def test_reverification_rejects_other_task_without_mutation(tmp_path):
    store, _task, _context, _driver, _handler, receipt, _execution = historical_receipt(tmp_path)
    evidence = proof(receipt, "sent")
    evidence["verification"]["open_task_id"] = "different-task"
    with pytest.raises(ValueError, match="identity mismatch"):
        apply_delivery_reverification(store, evidence)
    assert store.get_candidate_external_action(receipt["external_action_key"])["provider_result_json"] == receipt["provider_result_json"]


def test_executor_completes_corrected_uncertain_receipt_from_readback(tmp_path):
    store, task, context, driver, handler, receipt, _execution = historical_receipt(tmp_path)
    apply_delivery_reverification(store, proof(receipt, "ambiguous"))
    handler.confirmed = True
    # Orchestration sends uncertain messages to a receipt-feedback revision.
    # Exercise the persisted executor reconciliation boundary directly here.
    reviewed = store.current_reviewed_candidate(task.id, task.execution_generation)
    result = driver.system_executor.execute(
        task, reviewed["id"], reviewed["review_id"], context=context,
    )
    assert result.outcome == "executed"
    assert (handler.sends, handler.reads) == (1, 1)
    saved = json.loads(store.get_candidate_external_action(receipt["external_action_key"])["provider_result_json"])
    assert saved["verification"]["state"] == "sent"
    assert saved["verification_repair"]["previous_verification"]["state"] == "sent"


def test_actual_delivery_failure_is_not_reclassified_as_unknown(tmp_path):
    store, task, _context, _driver, _handler, receipt, execution = historical_receipt(tmp_path)
    apply_delivery_reverification(store, proof(receipt, "failed"))
    assert store.get_candidate_execution(execution["id"])["status"] == "failed"
    assert store.get_reply_task(task.id).error == "external_action_failed"
