from concurrent.futures import ThreadPoolExecutor
import json
import hashlib
import sqlite3

from tests.email_schema_fixtures import restore_action_plan_snapshot_column
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import inspect
from threading import Event
from urllib.parse import quote

import pytest

from app.agent_context import PriorReceipt
from app.agent_cron.commands import ServiceCommandConsumerContext, ServiceCommandSkillMaterial
from app.agent_contracts import ProposedAction
from app.email_classifier_contracts import (
    EmailAction,
    EmailAttachmentMetadata,
    EmailCategory,
    EmailClassification,
    EmailClassificationStatus,
    build_versioned_email_action_plan,
)
from app.email_store import EmailStore
from app.email_canonical_storage import email_classification_projection, email_message_projection
from app.email_imap_readonly import parse_rfc822_message
from app.email_task_adapter import (
    EmailAgentTaskAdapter,
    EmailAgentTaskConflict,
    EmailAgentTaskInput,
    EmailAgentTaskMetadataError,
    EmailThreadMessage,
    _assert_safe_email_metadata,
    accepted_email_unsubscribe_effect,
    email_action_identity,
    email_conversation_id,
    EmailClassificationTaskAdapter,
    EmailClassificationTaskInput,
)
from app.email_task_producer import EmailActionTaskProducer
from app.email_task_producer import EmailClassificationTaskProducer
from app.email_unsubscribe import (
    EmailUnsubscribeEffect,
    UnsubscribeAuthenticationEvidence,
    UnsubscribeOperation,
)
from app.store import AutoReplyStore
from app.skill_features import FeatureRegistry


def _native_email_store(path: Path) -> EmailStore:
    """Bind the real EmailStore connection code to a target-only test database."""
    from app.task_run_entity_assembly import task_run_entity_assembly_objects

    if not path.exists():
        db = sqlite3.connect(path)
        db.execute("pragma foreign_keys=on")
        objects = task_run_entity_assembly_objects()
        for sql in objects.values():
            if sql.lstrip().lower().startswith("create table"):
                db.execute(sql)
        for sql in objects.values():
            if not sql.lstrip().lower().startswith("create table"):
                db.execute(sql)
        db.close()
    store = EmailStore.__new__(EmailStore)
    store.path = path
    return store


def test_email_classification_adapter_uses_native_task_and_typed_result(tmp_path):
    store = _native_email_store(tmp_path / "native-email.sqlite3")
    adapter = EmailClassificationTaskAdapter(store)
    queued = adapter.ensure_task(_classification_input(uid=101))
    assert queued.task_id.isdecimal()
    assert queued.status == "pending"
    claimed = adapter.claim_next(owner="worker-1")
    assert claimed is not None
    assert claimed.task_id == queued.task_id
    assert (claimed.status, claimed.owner, claimed.attempt_count, claimed.generation) == (
        "running", "worker-1", 1, 1,
    )
    adapter.complete(claimed, {"decision_status": "processed"})
    done = adapter.get_task(claimed.task_id)
    assert done is not None and done.status == "done"
    assert json.loads(done.result_json) == {"decision_status": "processed"}
    assert json.loads(done.input_json)["input_compacted"] is True
    with store._connect() as db:
        assert db.execute("select count(*) from email_classification_inputs").fetchone()[0] == 1
        assert db.execute("select count(*) from task_results where result_kind='email_classification'").fetchone()[0] == 1
        task = db.execute("select kind,parameters_json from tasks where id=?", (int(done.task_id),)).fetchone()
        assert task["kind"] == "email_classification"
        assert json.loads(db.execute("select json(parameters_json) from tasks where id=?",
                                     (int(done.task_id),)).fetchone()[0]) == {}


def test_email_classification_console_queue_reads_native_owner(tmp_path):
    from app.audit_web import _queue_latest_error, _queue_status_counts

    store = _native_email_store(tmp_path / "native-email.sqlite3")
    adapter = EmailClassificationTaskAdapter(store)
    task = adapter.ensure_task(_classification_input(uid=119))
    with store._connect() as db:
        db.execute(
            "update tasks set status='failed',error='native classifier failure' where id=?",
            (int(task.task_id),),
        )
        counts = _queue_status_counts(db, "email_agent_classification_tasks", "status")
        latest_error = _queue_latest_error(
            db, "email_agent_classification_tasks", "status", "error",
        )
    assert counts == {"failed": 1}
    assert latest_error == "native classifier failure"


def test_source_migrated_email_tasks_keep_status_input_result_and_lifecycle_facts(tmp_path):
    from app.task12_storage import insert_task12
    from app.run_adoption_schema import capture_run_adoptions, migrate_run_adoptions
    from app.run_entity_schema import capture_run_entities, migrate_run_entities
    from app.task_entity_schema import capture_task_entities, migrate_task_entities
    from tests.test_run_entity_schema import _run_handoff

    source_path = tmp_path / "source-email.sqlite3"
    EmailStore(source_path)
    source = sqlite3.connect(source_path)
    source.row_factory = sqlite3.Row
    from app.task8_storage import register_task8_functions
    register_task8_functions(source)
    source.execute("pragma foreign_keys=off")
    source.execute("begin immediate")
    source_inputs = {}
    for index, status in enumerate(("pending", "running", "done"), start=1):
        task_input = _classification_input(uid=500 + index)
        source_inputs[index] = task_input
        insert_task12(source, "email_agent_classification_tasks", {
            "task_id": f"classification-native-migration-{index}",
            "channel": "email", "stable_message_identity": task_input.stable_message_identity,
            "status": status, "attempt_count": index,
            "owner": "classifier-worker" if status == "running" else "",
            "generation": index,
            "lease_expires_at": "2099-01-01T00:00:00Z" if status == "running" else "",
            "available_at": "", "error": "",
            "input_json": json.dumps(task_input.payload(), ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")),
            "result_json": '{"decision_status":"processed"}' if status == "done" else "null",
            "created_at": f"2026-10-23 00:00:0{index}",
            "updated_at": f"2026-10-23 00:01:0{index}",
        })
    adoption_source = capture_run_adoptions(source)
    task_source_metadata = capture_task_entities(source)
    run_source_metadata = capture_run_entities(source)
    task_metadata = migrate_task_entities(source, task_source_metadata)
    run_handoff = _run_handoff(source, run_source_metadata)
    run_metadata = migrate_run_entities(
        source, run_source_metadata, task_metadata=task_metadata, run_handoff=run_handoff,
    )
    migrate_run_adoptions(
        source, adoption_source, task_metadata=task_metadata, run_metadata=run_metadata,
    )
    source.commit()
    source.execute("pragma foreign_keys=on")
    assert source.execute("pragma foreign_keys").fetchone()[0] == 1
    assert tuple(source.execute("pragma foreign_key_check")) == ()
    database = tmp_path / "source-migrated-email.sqlite3"
    with sqlite3.connect(database) as target:
        source.backup(target)
    source.close()

    store = _native_email_store(database)
    adapter = EmailClassificationTaskAdapter(store)
    with store._connect() as db:
        ids = {
            identity: str(task_id)
            for task_id, identity in db.execute(
                "select task_id,stable_message_identity from email_classification_inputs "
                "where stable_message_identity like 'account-primary:%'"
            )
        }
        result_rows = db.execute(
            "select task_id,result_kind,result_json from task_results "
            "where result_kind='email_classification'"
        ).fetchall()
        assert len(result_rows) == 3
        assert all(key.isdecimal() for key in ids.values())
        assert any(json.loads(row[2]) == {"decision_status": "processed"} for row in result_rows)
        assert tuple(db.execute(
            "select created_at,updated_at from tasks where id=?",
            (int(ids[source_inputs[3].stable_message_identity]),),
        ).fetchone()) == ("2026-10-23 00:00:03", "2026-10-23 00:01:03")
        assert db.execute(
            "select count(*) from email_classification_inputs where stable_message_identity "
            "like 'account-primary:%'"
        ).fetchone()[0] == 3

    tasks = {status: adapter.get_task(ids[source_inputs[index].stable_message_identity])
             for index, status in enumerate(("pending", "running", "done"), start=1)}
    assert all(task is not None for task in tasks.values())
    assert {status: task.status for status, task in tasks.items()} == {
        "pending": "pending", "running": "running", "done": "done",
    }
    running = tasks["running"]
    assert (running.owner, running.attempt_count, running.generation, running.lease_expires_at) == (
        "classifier-worker", 2, 2, "2099-01-01T00:00:00Z",
    )
    assert tasks["done"].result_json == '{"decision_status":"processed"}'
    producer = EmailClassificationTaskProducer(store)
    assert producer.adapter.ensure_task(source_inputs[3]).task_id == tasks["done"].task_id
    pending_claim = adapter.claim_next(owner="migrated-pending-worker")
    assert pending_claim is not None
    assert pending_claim.task_id == tasks["pending"].task_id
    adapter.complete(pending_claim, {"decision_status": "processed"})
    adapter.complete(tasks["running"], {"decision_status": "processed"})
    assert adapter.get_task(tasks["pending"].task_id).status == "done"
    assert adapter.get_task(tasks["running"].task_id).status == "done"
    assert adapter.get_task(tasks["done"].task_id).result_json == '{"decision_status":"processed"}'


def test_email_classification_expired_claim_uses_native_cas_and_canonical_runtime_key(tmp_path):
    from datetime import timedelta

    now = [datetime(2026, 10, 23, tzinfo=timezone.utc)]
    store = _native_email_store(tmp_path / "native-email.sqlite3")
    adapter = EmailClassificationTaskAdapter(
        store, now=lambda: now[0], lease_seconds=1, retry_base_seconds=0,
    )
    queued = adapter.ensure_task(_classification_input(uid=102))
    first = adapter.claim_next(owner="worker-a")
    assert first is not None
    with store._connect() as db:
        assert AutoReplyStore._validate_runtime_operation_workload(
            "email_classification", first.task_id
        ) == ("email_classification", first.task_id)
        assert AutoReplyStore._runtime_operation_parent_exists(
            db, "email_classification", first.task_id
        )
        with pytest.raises(ValueError, match="persisted task"):
            AutoReplyStore._validate_runtime_operation_workload(
                "email_classification", "email-classification:" + "f" * 64
            )
        assert not AutoReplyStore._runtime_operation_parent_exists(
            db, "email_classification", "999999"
        )
    now[0] += timedelta(seconds=2)
    second = adapter.claim_next(owner="worker-b")
    assert second is not None
    assert (second.task_id, second.status, second.owner, second.attempt_count, second.generation) == (
        queued.task_id, "running", "worker-b", 2, 2,
    )
    with pytest.raises(ValueError, match="lease changed"):
        adapter.complete(first, {"stale": True})
    adapter.complete(second, {"current": True})
    with store._connect() as db:
        assert not AutoReplyStore._runtime_operation_parent_exists(
            db, "email_classification", second.task_id
        )


def test_concurrent_native_classification_enqueue_has_one_stable_owner(tmp_path):
    path = tmp_path / "native-email.sqlite3"
    first = EmailClassificationTaskAdapter(_native_email_store(path))
    second = EmailClassificationTaskAdapter(_native_email_store(path))
    task_input = _classification_input(uid=103)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda adapter: adapter.ensure_task(task_input), (first, second)))
    assert results[0].task_id == results[1].task_id
    with first.email_store._connect() as db:
        assert db.execute(
            "select count(*) from tasks where kind='email_classification'"
        ).fetchone()[0] == 1
        assert db.execute("select count(*) from email_classification_inputs").fetchone()[0] == 1


def _store(tmp_path: Path) -> AutoReplyStore:
    return AutoReplyStore(tmp_path / "email-agent.sqlite3")


def _plan(
    actions: tuple[EmailAction, ...],
    *,
    version: int = 1,
    classification_id: int = 41,
    account_id: str = "account-primary",
    instruction: str = "Reply in Chinese and acknowledge receipt.",
    classification_source: str = "model",
    category: EmailCategory | None = None,
):
    category = category or (
        EmailCategory.JUNK if EmailAction.UNSUBSCRIBE in actions else EmailCategory.WORK
    )
    parameters = {}
    for action in actions:
        if action is EmailAction.AUTO_REPLY:
            parameters[action] = {"instruction": instruction}
        elif action is EmailAction.LABEL:
            parameters[action] = {"labels": ["Work"]}
        else:
            parameters[action] = {}
    return build_versioned_email_action_plan(
        action_plan_version=version,
        classification_id=classification_id,
        account_id=account_id,
        category=category,
        classification_source=classification_source,
        confidence=0.98,
        model_id="email-model:2026-08-30:sha256:test",
        config_version="email-config:v7",
        actions=actions,
        action_parameters=parameters,
        created_at=datetime(2026, 8, 30, 8, 0, tzinfo=timezone.utc),
    )


def _task_input(
    *,
    account_id: str = "account-primary",
) -> EmailAgentTaskInput:
    stable_identity = f"{account_id}:message-id:<mail-41@example.com>"
    body_text = (
        "请确认合同。退订入口 "
        "https://example.com/unsubscribe?token=private-token "
        "以及 password=do-not-persist /Users/derek/private/attachment.bin"
    )
    return EmailAgentTaskInput(
        stable_message_identity=stable_identity,
        thread_identity="thread-customer-41",
        subject="Re: 合同确认",
        trigger=EmailThreadMessage(
            message_id=stable_identity,
            sender="customer@example.com",
            text=body_text,
            create_time="2026-08-30T08:00:00+00:00",
        ),
        thread_messages=(
            EmailThreadMessage(
                message_id=f"{account_id}:message-id:<mail-40@example.com>",
                sender="derek@stardust.ai",
                text="上一封邮件的纯文本回复。",
                create_time="2026-08-29T08:00:00+00:00",
            ),
        ),
        attachments=(
            EmailAttachmentMetadata(
                filename="contract.pdf",
                mime_type="application/pdf",
                size_bytes=1234,
                inline=False,
            ),
        ),
        prior_receipts=(
            PriorReceipt(
                receipt_id="sent-state-1",
                operation="sent_state_readback",
                summary="No equivalent reply exists.",
                completed=True,
            ),
        ),
        body_text=body_text,
    )


def _email_store(tmp_path: Path) -> EmailStore:
    return EmailStore(tmp_path / "email-agent.sqlite3")


def _downgrade_email_database_to_v25(database: Path) -> None:
    with sqlite3.connect(database) as db:
        restore_action_plan_snapshot_column(db)
        db.execute("pragma foreign_keys=off")
        for table in (
            "email_historical_classification_history",
            "email_historical_candidates",
            "email_historical_operations",
            "email_historical_traversals",
        ):
            db.execute(f"drop table {table}")
        db.execute("delete from email_schema_migrations where version > 25")
        db.execute(
            "insert or ignore into email_schema_migrations(version, applied_at) "
            "values (25, '2026-09-08T00:00:00+00:00')"
        )


def _classification_input(*, uid: int) -> EmailClassificationTaskInput:
    return EmailClassificationTaskInput.from_message(
        {
            "accountId": "account-primary",
            "folder": "INBOX",
            "uidValidity": 42,
            "uid": uid,
            "messageId": f"<mail-{uid}@example.com>",
            "providerUnread": True,
        },
        allowed_category_keys=("work", "junk"),
        category_descriptions={
            "work": {"core": "Business."},
            "junk": {"core": "Unwanted."},
        },
        folder_targets={"work": "Work"},
        config_version="config-v1",
        unsubscribe_candidates=(),
    )


def _percent_encode(value: str, rounds: int) -> str:
    encoded = value
    for _ in range(rounds):
        encoded = quote(encoded, safe="")
    return encoded


def test_classification_task_adapter_persists_one_stable_task_across_reopen(
    tmp_path: Path,
):
    store = _native_email_store(tmp_path / "native-email.sqlite3")
    task_input = EmailClassificationTaskInput.from_message(
        {
            "accountId": "account-primary",
            "folder": "INBOX",
            "uidValidity": 42,
            "uid": 41,
            "messageId": "<mail-41@example.com>",
            "threadId": "thread-41",
            "providerUnread": True,
            "from": {"email": "customer@example.com"},
            "subject": "Project decision",
            "textBody": "Please decide the launch scope.",
            "attachments": [
                {
                    "filename": "scope.pdf",
                    "mime_type": "application/pdf",
                    "size_bytes": 12,
                    "inline": False,
                }
            ],
        },
        allowed_category_keys=("work", "junk"),
        category_descriptions={
            "work": {"core": "Business."},
            "junk": {"core": "Unwanted."},
        },
        folder_targets={"work": "Work"},
        config_version="config-v1",
        unsubscribe_candidates=(),
    )

    first = EmailClassificationTaskAdapter(store).ensure_task(task_input)
    reopened = EmailClassificationTaskAdapter(
        _native_email_store(tmp_path / "native-email.sqlite3")
    ).ensure_task(
        task_input
    )

    assert first.task_id == reopened.task_id
    assert first.status == reopened.status == "pending"
    assert EmailClassificationTaskAdapter(store).has_stable_record(
        task_input.stable_message_identity
    )
    assert EmailClassificationTaskAdapter(store).stable_provider_uids(
        account_id="account-primary", folder="INBOX", uidvalidity=42
    ) == frozenset({41})
    assert "scope.pdf" in first.input_json
    assert "attachment content" not in first.input_json


def test_duplicate_email_scan_keeps_first_trigger_context_without_conflict(
    tmp_path: Path,
) -> None:
    store = _native_email_store(tmp_path / "native-email.sqlite3")
    first_context = ServiceCommandConsumerContext(
        scheduled_task_id=10,
        scheduled_task_run_id=101,
        prompt="使用 $ceo-email-classifier 分类真实新邮件。",
        skill_names=("ceo-email-classifier",),
        skill_protocol="## Managed Skill: ceo-email-classifier\nFIRST REVISION",
    )
    later_context = replace(first_context, scheduled_task_run_id=102)
    message = {
        "accountId": "account-primary",
        "folder": "INBOX",
        "uidValidity": 42,
        "uid": 141,
        "messageId": "<mail-141@example.com>",
        "providerUnread": True,
    }

    def task_input(context: ServiceCommandConsumerContext):
        return EmailClassificationTaskInput.from_message(
            message,
            allowed_category_keys=("work", "junk"),
            category_descriptions={
                "work": {"core": "Business."},
                "junk": {"core": "Unwanted."},
            },
            folder_targets={"work": "Work"},
            config_version="config-v1",
            unsubscribe_candidates=(),
            scheduled_consumer=context.to_payload(),
        )

    adapter = EmailClassificationTaskAdapter(store)
    first = adapter.ensure_task(task_input(first_context))
    duplicate = adapter.ensure_task(task_input(later_context))

    assert duplicate.task_id == first.task_id
    persisted = json.loads(duplicate.input_json)["scheduled_consumer"]
    assert persisted["scheduled_task_run_id"] == 101
    assert persisted["skill_names"] == ["ceo-email-classifier"]


def test_completed_classification_compacts_input_and_keeps_immutable_identity(tmp_path: Path):
    store = _native_email_store(tmp_path / "native-email.sqlite3")
    adapter = EmailClassificationTaskAdapter(store)
    context = ServiceCommandConsumerContext(
        scheduled_task_id=10, scheduled_task_run_id=101,
        prompt="classify " + "prompt body " * 100,
        skill_names=("ceo-email-classifier",),
        skill_protocol="frozen protocol " * 100,
        skill_materials=(ServiceCommandSkillMaterial(
            name="ceo-email-classifier",
            content="## Managed Skill: ceo-email-classifier\nrevision_id: 71\nsha256: "
                    + "b" * 64 + "\n\nfrozen Skill body " * 100,
        ),),
    )
    original = replace(
        _classification_input(uid=191),
        message={"text": "private message body " * 500},
        unsubscribe_candidates=({
            "index": 0, "source": "header_https", "digest": "a" * 64,
            "reference": "unsubscribe-entry:" + "a" * 64,
        },),
        scheduled_consumer=context.to_payload(),
    )
    pending = adapter.ensure_task(original)
    claimed = adapter.claim_next(owner="worker")
    assert claimed is not None and claimed.input_json == pending.input_json
    result = {"classification_id": 191, "decision_status": "processed"}
    adapter.complete(claimed, result)
    done = adapter.get_task(pending.task_id)
    assert done is not None and done.status == "done"
    assert json.loads(done.result_json) == result
    compact = json.loads(done.input_json)
    assert compact["input_compacted"] is True
    assert compact["input_bytes"] == len(pending.input_json.encode("utf-8"))
    assert compact["input_sha256"] == hashlib.sha256(
        pending.input_json.encode("utf-8")
    ).hexdigest()
    assert compact["provider_locator"] == json.loads(pending.input_json)["provider_locator"]
    assert compact["unsubscribe_candidates"] == [original.unsubscribe_candidates[0]]
    assert compact["scheduled_consumer_reference"]["scheduled_task_run_id"] == 101
    assert compact["scheduled_consumer_reference"]["skill_materials"][0]["name"] == "ceo-email-classifier"
    assert compact["scheduled_consumer_reference"]["skill_materials"][0]["content_sha256"] == hashlib.sha256(
        context.skill_materials[0].content.encode("utf-8")
    ).hexdigest()
    assert "private message body" not in done.input_json
    assert "frozen Skill body" not in done.input_json
    assert adapter.stable_provider_uids(
        account_id="account-primary", folder="INBOX", uidvalidity=42
    ) == frozenset({191})
    assert adapter.ensure_task(original).task_id == pending.task_id
    assert adapter.ensure_task(replace(
        original, scheduled_consumer=replace(context, scheduled_task_run_id=102).to_payload()
    )).task_id == pending.task_id
    with pytest.raises(ValueError, match="stable classification task input changed"):
        adapter.ensure_task(replace(original, message={"text": "different"}))
    # The assembled target remains deliberately unactivated by EmailStore's
    # shared constructor; the native owners above are the acceptance surface.


def test_compacted_classifier_input_never_copies_custom_skill_preamble():
    body = "private custom Skill body " * 600
    context = ServiceCommandConsumerContext(
        scheduled_task_id=10, scheduled_task_run_id=101,
        prompt="classify", skill_names=("ceo-email-classifier",),
        skill_protocol="custom protocol", skill_protocol_source="explicit_custom",
        skill_materials=(ServiceCommandSkillMaterial(
            name="ceo-email-classifier",
            content="## Managed Skill: ceo-email-classifier\n" + body + "\n\nmore body",
        ),),
    )
    task_input = replace(_classification_input(uid=193),
                         scheduled_consumer=context.to_payload())
    original = json.dumps(task_input.payload(), ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"))
    compact = EmailClassificationTaskAdapter.compact_done_input_json(original)
    material = json.loads(compact)["scheduled_consumer_reference"]["skill_materials"][0]
    assert material == {
        "name": "ceo-email-classifier",
        "content_sha256": hashlib.sha256(context.skill_materials[0].content.encode("utf-8")).hexdigest(),
    }
    assert body not in compact
    assert "more body" not in compact


def test_classification_failed_and_retry_inputs_remain_exact(tmp_path: Path):
    adapter = EmailClassificationTaskAdapter(
        _native_email_store(tmp_path / "native-email.sqlite3"), retry_base_seconds=0
    )
    task = adapter.ensure_task(replace(
        _classification_input(uid=192), message={"text": "retry body " * 200}
    ))
    first = adapter.claim_next(owner="worker")
    assert first is not None
    adapter.fail(first, error="temporary", retryable=True)
    pending = adapter.get_task(task.task_id)
    assert pending is not None and pending.input_json == task.input_json
    second = adapter.claim_next(owner="worker")
    assert second is not None and second.input_json == task.input_json
    adapter.fail(second, error="terminal", retryable=False)
    failed = adapter.get_task(task.task_id)
    assert failed is not None and failed.status == "failed"
    assert failed.input_json == task.input_json
    assert adapter.retry_failed_tasks([task.task_id]) == 1
    assert adapter.get_task(task.task_id).input_json == task.input_json


def test_assembled_classifier_schema_is_email_owned_not_adapter(tmp_path: Path):
    store = _native_email_store(tmp_path / "email-agent.sqlite3")
    with store._connect() as db:
        columns = {
            row["name"]
            for row in db.execute("pragma table_xinfo(tasks)")
        }
        input_columns = {row[1] for row in db.execute("pragma table_info(email_classification_inputs)")}
        result_columns = {row[1] for row in db.execute("pragma table_info(task_results)")}
    assert {"id", "kind", "status", "available_at", "attempts", "generation",
            "lease_owner", "lease_expires_at", "parameters_json"} <= columns
    assert {"task_id", "channel", "stable_message_identity", "input_json"} <= input_columns
    assert {"task_id", "result_kind", "result_json"} <= result_columns
    assert (
        "create table"
        not in inspect.getsource(EmailClassificationTaskAdapter).casefold()
    )


def test_email_native_classifier_schema_keeps_input_and_result_out_of_task_parameters(tmp_path: Path):
    store = _native_email_store(tmp_path / "email-agent.sqlite3")
    task = EmailClassificationTaskAdapter(store).ensure_task(_classification_input(uid=141))
    with store._connect() as db:
        native = db.execute("select parameters_json from tasks where id=?", (int(task.task_id),)).fetchone()
        assert json.loads(db.execute("select json(parameters_json) from tasks where id=?",
                                     (int(task.task_id),)).fetchone()[0]) == {}
        assert db.execute(
            "select count(*) from email_classification_inputs where task_id=?", (int(task.task_id),)
        ).fetchone()[0] == 1
        assert db.execute(
            "select count(*) from task_results where task_id=? and result_kind='email_classification'",
            (int(task.task_id),),
        ).fetchone()[0] == 0
        assert native is not None


@pytest.mark.parametrize("state", ["empty", "done", "delayed", "live"])
def test_ineligible_classification_poll_does_not_acquire_writer_lock(
    tmp_path: Path, monkeypatch, state: str
):
    store = _native_email_store(tmp_path / "email-agent.sqlite3")
    adapter = EmailClassificationTaskAdapter(store)
    if state != "empty":
        task = adapter.ensure_task(_classification_input(uid=142))
        with store._connect() as db:
            db.execute(
                "update tasks set status=?, available_at=?, lease_expires_at=? where id=?",
                ("pending" if state == "delayed" else "running" if state == "live" else "done",
                 "2099-01-01 00:00:00" if state == "delayed" else "",
                 "2099-01-01 00:00:00" if state == "live" else "", int(task.task_id)),
            )
    original_open = store._open_connection

    def short_wait_connection(**kwargs):
        db = original_open(**kwargs)
        db.execute("pragma busy_timeout=20")
        return db

    monkeypatch.setattr(store, "_open_connection", short_wait_connection)
    with sqlite3.connect(store.path) as writer:
        writer.execute("begin immediate")
        try:
            assert adapter.claim_next(owner="idle-poller") is None
        finally:
            writer.rollback()


def test_classification_claim_rechecks_after_an_intervening_owner(tmp_path: Path, monkeypatch):
    store = _native_email_store(tmp_path / "email-agent.sqlite3")
    adapter = EmailClassificationTaskAdapter(store)
    task = adapter.ensure_task(_classification_input(uid=142))
    competitor = EmailClassificationTaskAdapter(_native_email_store(store.path))
    original_open = store._open_connection
    claims = []

    def competing_connection(**kwargs):
        db = original_open(**kwargs)

        def before_begin(statement):
            if statement.lower() == "begin immediate":
                db.set_trace_callback(None)
                claims.append(competitor.claim_next(owner="other-worker"))

        db.set_trace_callback(before_begin)
        return db

    monkeypatch.setattr(store, "_open_connection", competing_connection)
    assert adapter.claim_next(owner="first-worker") is None
    assert len(claims) == 1 and claims[0] is not None
    persisted = adapter.get_task(task.task_id)
    assert persisted.owner == "other-worker"
    assert persisted.generation == 1


def test_classification_task_claim_is_single_owner_and_recoverable(tmp_path: Path):
    adapter = EmailClassificationTaskAdapter(
        _native_email_store(tmp_path / "email-agent.sqlite3"), retry_base_seconds=0
    )
    task_input = EmailClassificationTaskInput.from_message(
        {
            "accountId": "account-primary",
            "folder": "INBOX",
            "uidValidity": 42,
            "uid": 42,
            "messageId": "<mail-42@example.com>",
            "providerUnread": True,
        },
        allowed_category_keys=("work", "junk"),
        category_descriptions={
            "work": {"core": "Business."},
            "junk": {"core": "Unwanted."},
        },
        folder_targets={"work": "Work"},
        config_version="config-v1",
        unsubscribe_candidates=(),
    )
    adapter.ensure_task(task_input)

    claimed = adapter.claim_next(owner="worker-1")

    assert claimed is not None and claimed.status == "running"
    assert adapter.claim_next(owner="worker-2") is None
    adapter.fail(claimed, error="provider unavailable", retryable=True)
    assert adapter.claim_next(owner="worker-2") is not None


def test_live_classifier_lease_cannot_be_stolen_or_completed_by_stale_claim(
    tmp_path: Path,
):
    now = [datetime(2026, 9, 8, tzinfo=timezone.utc)]
    adapter = EmailClassificationTaskAdapter(
        _native_email_store(tmp_path / "email-agent.sqlite3"), now=lambda: now[0], lease_seconds=60
    )
    adapter.ensure_task(_classification_input(uid=142))
    first = adapter.claim_next(owner="worker-1")
    assert first is not None
    assert adapter.recover_running_tasks() == 0
    assert adapter.claim_next(owner="worker-2") is None

    now[0] = datetime(2026, 9, 8, 0, 2, tzinfo=timezone.utc)
    assert adapter.recover_running_tasks() == 1
    second = adapter.claim_next(owner="worker-2")
    assert second is not None and second.generation == first.generation + 1
    with pytest.raises(ValueError, match="lease changed"):
        adapter.complete(first, {"decision_status": "processed"})


def test_fast_restart_reclaims_claim_when_lease_expires_during_normal_polling(
    tmp_path: Path,
):
    now = [datetime(2026, 9, 8, tzinfo=timezone.utc)]
    store = _native_email_store(tmp_path / "email-agent.sqlite3")
    original = EmailClassificationTaskAdapter(
        store, now=lambda: now[0], lease_seconds=60
    )
    original.ensure_task(_classification_input(uid=144))
    first = original.claim_next(owner="worker-before-restart")
    assert first is not None

    now[0] = datetime(2026, 9, 8, 0, 0, 30, tzinfo=timezone.utc)
    restarted = EmailClassificationTaskAdapter(
        _native_email_store(tmp_path / "email-agent.sqlite3"), now=lambda: now[0], lease_seconds=60
    )
    assert restarted.claim_next(owner="worker-after-restart") is None

    now[0] = datetime(2026, 9, 8, 0, 1, 1, tzinfo=timezone.utc)
    reclaimed = restarted.claim_next(owner="worker-after-restart")

    assert reclaimed is not None
    assert reclaimed.owner == "worker-after-restart"
    assert reclaimed.generation == first.generation + 1
    with pytest.raises(ValueError, match="lease changed"):
        original.complete(first, {"decision_status": "processed"})


def test_expired_classifier_claim_returns_to_retry_queue_without_terminal_failure(
    tmp_path: Path,
):
    now = [datetime(2026, 9, 8, tzinfo=timezone.utc)]
    adapter = EmailClassificationTaskAdapter(
        _native_email_store(tmp_path / "email-agent.sqlite3"),
        now=lambda: now[0],
        lease_seconds=60,
        retry_base_seconds=10,
        retry_max_delay_seconds=15,
    )
    task = adapter.ensure_task(_classification_input(uid=145))
    claimed = adapter.claim_next(owner="worker-before-crash")
    assert claimed is not None

    now[0] = datetime(2026, 9, 8, 0, 1, 1, tzinfo=timezone.utc)
    assert adapter.recover_running_tasks() == 1
    waiting = adapter.get_task(task.task_id)
    assert waiting is not None
    assert waiting.status == "pending"
    assert waiting.error == "classification task lease expired"
    assert waiting.available_at == "2026-09-08T00:01:01.000000+00:00"

    now[0] = datetime(2026, 9, 8, 0, 1, 12, tzinfo=timezone.utc)
    replacement = adapter.claim_next(owner="worker-after-crash")
    assert replacement is not None
    assert replacement.attempt_count == 2
    with pytest.raises(ValueError, match="lease changed"):
        adapter.complete(claimed, {"decision_status": "processed"})


def test_default_classifier_lease_outlasts_maximum_agent_turn(tmp_path: Path):
    adapter = EmailClassificationTaskAdapter(_native_email_store(tmp_path / "email-agent.sqlite3"))

    assert adapter.lease_seconds > 900


def test_classifier_retry_uses_shared_capped_backoff_until_success(tmp_path: Path):
    now = [datetime(2026, 9, 8, tzinfo=timezone.utc)]
    adapter = EmailClassificationTaskAdapter(
        _native_email_store(tmp_path / "email-agent.sqlite3"),
        now=lambda: now[0],
        retry_base_seconds=10,
        retry_max_delay_seconds=15,
    )
    adapter.ensure_task(_classification_input(uid=143))
    first = adapter.claim_next(owner="worker")
    assert first is not None
    adapter.fail(first, error="ConnectionError:offline", retryable=True)
    assert adapter.claim_next(owner="worker") is None
    now[0] = datetime(2026, 9, 8, 0, 0, 11, tzinfo=timezone.utc)
    second = adapter.claim_next(owner="worker")
    assert second is not None
    adapter.fail(second, error="ConnectionError:offline", retryable=True)
    waiting = adapter.get_task(second.task_id)
    assert waiting is not None
    assert waiting.status == "pending"
    assert waiting.available_at == "2026-09-08T00:00:26.000000+00:00"

    now[0] = datetime(2026, 9, 8, 0, 0, 27, tzinfo=timezone.utc)
    third = adapter.claim_next(owner="worker")
    assert third is not None
    adapter.complete(third, {"decision_status": "processed"})
    assert adapter.get_task(third.task_id).status == "done"


def test_classifier_retryable_failure_stops_after_four_attempts(tmp_path: Path):
    adapter = EmailClassificationTaskAdapter(_native_email_store(tmp_path / "email-agent.sqlite3"), retry_base_seconds=0)
    original = adapter.ensure_task(_classification_input(uid=245))
    for attempt in range(1, 5):
        claimed = adapter.claim_next(owner="worker")
        assert claimed is not None
        assert claimed.attempt_count == attempt
        adapter.fail(claimed, error="LookupError:email source UID is unavailable", retryable=True)
    exhausted = adapter.get_task(original.task_id)
    assert exhausted.status == "failed"
    assert exhausted.available_at == ""
    assert exhausted.error == "LookupError:email source UID is unavailable"
    assert exhausted.input_json == original.input_json
    assert adapter.claim_next(owner="worker") is None
    assert adapter.retry_failed_tasks((original.task_id,)) == 1
    assert adapter.claim_next(owner="worker").attempt_count == 1


def test_classifier_repeated_expired_claims_stop_after_four_attempts(tmp_path: Path):
    now = [datetime(2026, 9, 8, tzinfo=timezone.utc)]
    adapter = EmailClassificationTaskAdapter(
        _native_email_store(tmp_path / "email-agent.sqlite3"), now=lambda: now[0], lease_seconds=1,
    )
    original = adapter.ensure_task(_classification_input(uid=246))
    for attempt in range(1, 5):
        claimed = adapter.claim_next(owner="crashed-worker")
        assert claimed is not None
        assert claimed.attempt_count == attempt
        now[0] = now[0].replace(second=attempt * 2)
        adapter.recover_running_tasks()
    exhausted = adapter.get_task(original.task_id)
    assert exhausted.status == "failed"
    assert exhausted.attempt_count == 4
    assert exhausted.error == "classification task lease expired"
    assert adapter.claim_next(owner="replacement") is None


def test_classifier_closes_legacy_exhausted_pending_before_claiming(tmp_path: Path):
    store = _native_email_store(tmp_path / "email-agent.sqlite3")
    adapter = EmailClassificationTaskAdapter(store)
    original = adapter.ensure_task(_classification_input(uid=247))
    with store._connect() as db:
        db.execute(
            "update tasks set attempts=58, available_at=?, error=? where id=?",
            ("2099-01-01T00:00:00+00:00", "LookupError:email source UID is unavailable", int(original.task_id)),
        )
    assert adapter.claim_next(owner="worker") is None
    exhausted = adapter.get_task(original.task_id)
    assert exhausted.status == "failed"
    assert exhausted.attempt_count == 58
    assert exhausted.error == "LookupError:email source UID is unavailable"
    assert exhausted.input_json == original.input_json
    fresh = adapter.ensure_task(_classification_input(uid=248))
    assert adapter.claim_next(owner="worker").task_id == fresh.task_id


def test_explicit_classifier_repair_reopens_only_selected_failed_tasks(
    tmp_path: Path,
):
    adapter = EmailClassificationTaskAdapter(_native_email_store(tmp_path / "email-agent.sqlite3"))
    selected = adapter.ensure_task(_classification_input(uid=146))
    claimed = adapter.claim_next(owner="worker")
    assert claimed is not None and claimed.task_id == selected.task_id
    adapter.fail(
        claimed,
        error="ValueError:invalid classification input",
        retryable=False,
    )
    untouched = adapter.ensure_task(_classification_input(uid=147))
    claimed = adapter.claim_next(owner="worker")
    assert claimed is not None and claimed.task_id == untouched.task_id
    adapter.fail(
        claimed,
        error="ValueError:invalid classification input",
        retryable=False,
    )

    assert adapter.retry_failed_tasks((selected.task_id,)) == 1

    reopened = adapter.get_task(selected.task_id)
    still_failed = adapter.get_task(untouched.task_id)
    assert reopened is not None
    assert reopened.status == "pending"
    assert reopened.attempt_count == 0
    assert reopened.error == ""
    assert still_failed is not None and still_failed.status == "failed"


def test_classification_task_bootstrap_recovers_crash_interrupted_claim(tmp_path: Path):
    store = _native_email_store(tmp_path / "email-agent.sqlite3")
    now = [datetime(2026, 9, 8, tzinfo=timezone.utc)]
    adapter = EmailClassificationTaskAdapter(
        store, now=lambda: now[0], lease_seconds=60
    )
    task_input = EmailClassificationTaskInput.from_message(
        {
            "accountId": "account-primary",
            "folder": "INBOX",
            "uidValidity": 42,
            "uid": 43,
            "messageId": "<mail-43@example.com>",
            "providerUnread": True,
        },
        allowed_category_keys=("work", "junk"),
        category_descriptions={
            "work": {"core": "Business."},
            "junk": {"core": "Unwanted."},
        },
        folder_targets={"work": "Work"},
        config_version="config-v1",
        unsubscribe_candidates=(),
    )
    adapter.ensure_task(task_input)
    assert adapter.claim_next(owner="crashed-worker") is not None

    now[0] = datetime(2026, 9, 8, 0, 2, tzinfo=timezone.utc)
    reopened = EmailClassificationTaskAdapter(
        _native_email_store(tmp_path / "email-agent.sqlite3"), now=lambda: now[0], lease_seconds=60
    )
    assert reopened.recover_running_tasks() == 1
    reclaimed = reopened.claim_next(owner="replacement-worker")

    assert reclaimed is not None
    assert reclaimed.owner == "replacement-worker"


def _persist_authorization(
    store: EmailStore,
    plan,
    task_input: EmailAgentTaskInput,
) -> None:
    initial_plan = plan
    if plan.action_plan_version > 1:
        initial_plan = build_versioned_email_action_plan(
            action_plan_version=1,
            classification_id=plan.classification_id,
            account_id=plan.account_id,
            category=plan.category,
            classification_source=plan.classification_source,
            confidence=plan.confidence,
            model_id=plan.model_id,
            config_version=plan.config_version,
            actions=plan.actions,
            action_parameters=plan.action_parameters,
            created_at=plan.created_at,
        )
    store.upsert_classification(
        EmailClassification.model_validate(
            {
                "classification_id": plan.classification_id,
                "stable_message_identity": task_input.stable_message_identity,
                "provider_locator": {
                    "account_id": plan.account_id,
                    "folder": "INBOX",
                    "uidvalidity": 42,
                    "uid": plan.classification_id,
                    "rfc_message_id": "<mail-41@example.com>",
                    "thread_id": task_input.thread_identity,
                },
                "category": plan.category,
                "confidence": plan.confidence,
                "margin": 0.42,
                "probabilities": {plan.category: plan.confidence},
                "model_id": plan.model_id,
                "config_version": plan.config_version,
                "status": EmailClassificationStatus.PROCESSED,
                "classification_source": plan.classification_source,
                "action_plan": initial_plan,
            }
        ),
        sender=task_input.trigger.sender,
        subject=task_input.subject,
        model_text="__subject__contract confirmation",
        received_at=task_input.trigger.create_time,
    )
    if plan.action_plan_version > 1:
        store.append_action_plan_version(
            plan.classification_id,
            plan,
            confirmed_category=plan.category,
        )


def _authorized_adapter(
    tmp_path: Path,
    plan,
    task_input: EmailAgentTaskInput,
    *,
    task_store: AutoReplyStore | None = None,
    email_store: EmailStore | None = None,
) -> EmailAgentTaskAdapter:
    durable_email_store = email_store or _email_store(tmp_path)
    _persist_authorization(durable_email_store, plan, task_input)
    return EmailAgentTaskAdapter(
        task_store or _store(tmp_path),
        durable_email_store,
    )


def test_only_unsubscribe_creates_task_from_mixed_legacy_action_plan(tmp_path: Path):
    store = _store(tmp_path)
    plan = _plan(
        (
            EmailAction.LABEL,
            EmailAction.MARK_READ,
            EmailAction.AUTO_REPLY,
            EmailAction.UNSUBSCRIBE,
        )
    )
    task_input = _task_input()
    adapter = _authorized_adapter(
        tmp_path,
        plan,
        task_input,
        task_store=store,
    )

    first = adapter.ensure_action_plan_tasks(plan, task_input)
    replay = adapter.ensure_action_plan_tasks(plan, task_input)

    assert [route.action_type for route in first] == [EmailAction.UNSUBSCRIBE]
    assert [route.task.id for route in replay] == [route.task.id for route in first]
    assert store.count_reply_tasks(channel="email") == 1
    assert {route.task.channel for route in first} == {"email"}
    assert {route.task.status for route in first} == {"pending"}
    assert store.count_sent_replies() == 0
    assert all(
        store.list_agent_runs_for_task_generation(
            route.task.id,
            route.task.execution_generation,
        )
        == []
        for route in first
    )
    assert {route.task.conversation_id for route in first} == {
        email_conversation_id(plan.account_id, task_input.thread_identity)
    }
    for route in first:
        assert route.task.trigger_message_id == email_action_identity(
            account_id=plan.account_id,
            stable_message_identity=task_input.stable_message_identity,
            action_type=route.action_type,
            action_plan_version=plan.action_plan_version,
        )


def test_task_producer_builds_email_task_from_persisted_message_context(
    tmp_path: Path,
):
    plan = _plan((EmailAction.UNSUBSCRIBE,))
    task_input = _task_input()
    email_store = _email_store(tmp_path)
    _persist_authorization(email_store, plan, task_input)
    task_store = _store(tmp_path)
    producer = EmailActionTaskProducer(task_store, email_store)

    routes = producer.produce(
        plan,
        {
            "accountId": plan.account_id,
            "folder": "INBOX",
            "uidValidity": 42,
            "uid": 41,
            "messageId": "<mail-41@example.com>",
            "stableMessageIdentity": task_input.stable_message_identity,
            "threadId": task_input.thread_identity,
            "from": {"email": task_input.trigger.sender},
            "subject": task_input.subject,
            "textBody": task_input.trigger.text,
            "markdownBody": task_input.trigger.text,
        },
    )

    assert len(routes) == 1
    assert routes[0].action_type is EmailAction.UNSUBSCRIBE
    assert routes[0].task.channel == "email"
    assert task_store.count_reply_tasks(channel="email") == 1


def test_task_producer_projects_html_only_unsubscribe_as_opaque_reference(
    tmp_path: Path,
):
    plan = _plan((EmailAction.UNSUBSCRIBE,))
    task_input = _task_input()
    email_store = _email_store(tmp_path)
    _persist_authorization(email_store, plan, task_input)
    private_url = "https://news.example.com/unsubscribe?token=html-only-private"
    message = parse_rfc822_message(
        (
            b"From: customer@example.com\r\n"
            b"To: derek@example.com\r\n"
            b"Subject: Newsletter\r\n"
            b"Date: Sun, 30 Aug 2026 08:00:00 +0000\r\n"
            b"Message-ID: <mail-41@example.com>\r\n"
            b"Content-Type: text/html; charset=utf-8\r\n\r\n"
            + (
                '<p>Newsletter</p><a href="' + private_url + '">Unsubscribe</a>'
            ).encode()
        ),
        account_id=plan.account_id,
        folder="INBOX",
        uidvalidity=42,
        uid=41,
    )

    [route] = EmailActionTaskProducer(
        _store(tmp_path),
        email_store,
    ).produce(plan, message)
    payload = json.loads(route.task.trigger_message_json)

    assert payload["unsubscribe_entries"] == [
        {
            "index": 0,
            "digest": payload["unsubscribe_entries"][0]["digest"],
            "reference": payload["unsubscribe_entries"][0]["reference"],
            "source": "body_html_https",
        }
    ]
    assert payload["unsubscribe_entries"][0]["reference"].startswith(
        "unsubscribe-entry:"
    )
    assert private_url not in route.task.trigger_message_json
    assert "html-only-private" not in route.task.trigger_message_json
    assert private_url not in repr(route)
    assert private_url.encode() not in (tmp_path / "email-agent.sqlite3").read_bytes()


def test_task_producer_policy_is_exact_deterministic_https_candidate_origin_set(
    tmp_path: Path,
) -> None:
    plan = _plan((EmailAction.UNSUBSCRIBE,))
    task_input = _task_input()
    email_store = _email_store(tmp_path)
    _persist_authorization(email_store, plan, task_input)
    first = "https://news.example.com/unsubscribe?token=first-private"
    second = "https://preferences.example.net/opt-out?token=second-private"
    message = parse_rfc822_message(
        (
            b"From: customer@example.com\r\n"
            b"To: derek@example.com\r\n"
            b"Subject: Newsletter\r\n"
            b"Date: Sun, 30 Aug 2026 08:00:00 +0000\r\n"
            b"Message-ID: <mail-41@example.com>\r\n"
            + f"List-Unsubscribe: <{first}>, <mailto:list@example.com>\r\n".encode()
            + b"Content-Type: text/html; charset=utf-8\r\n\r\n"
            + f'<a href="{second}">Unsubscribe</a>'.encode()
        ),
        account_id=plan.account_id,
        folder="INBOX",
        uidvalidity=42,
        uid=41,
    )

    [route] = EmailActionTaskProducer(
        _store(tmp_path),
        email_store,
    ).produce(plan, message)
    payload = json.loads(route.task.trigger_message_json)

    assert len(payload["unsubscribe_entries"]) == 2
    encoded = route.task.trigger_message_json
    for forbidden in (first, second, "first-private", "second-private", "mailto:"):
        assert forbidden not in encoded


def test_task_producer_does_not_persist_auto_reply_under_current_policy(
    tmp_path: Path,
):
    plan = _plan((EmailAction.AUTO_REPLY,))
    task_input = _task_input()
    email_store = _email_store(tmp_path)
    _persist_authorization(email_store, plan, task_input)
    producer = EmailActionTaskProducer(_store(tmp_path), email_store)

    routes = producer.produce(
        plan,
        {
            "accountId": plan.account_id,
            "folder": "INBOX",
            "uidValidity": 42,
            "uid": 41,
            "messageId": "<mail-41@example.com>",
            "stableMessageIdentity": task_input.stable_message_identity,
            "threadId": task_input.thread_identity,
            "from": {"email": task_input.trigger.sender},
            "subject": task_input.subject,
            "textBody": task_input.trigger.text,
            "markdownBody": task_input.trigger.text,
        },
    )

    assert routes == ()
    assert _store(tmp_path).count_reply_tasks(channel="email") == 0


def test_disabled_mail_review_does_not_create_email_tasks(tmp_path: Path):
    store = _store(tmp_path)
    plan = _plan((EmailAction.AUTO_REPLY,))
    task_input = _task_input()
    email_store = _email_store(tmp_path)
    _persist_authorization(email_store, plan, task_input)
    registry = FeatureRegistry(state_path=tmp_path / "skill-state.json")
    registry.set_enabled("mail_review", False)

    adapter = EmailAgentTaskAdapter(store, email_store, feature_registry=registry)
    assert adapter.ensure_action_plan_tasks(plan, task_input) == ()
    assert store.count_reply_tasks(channel="email") == 0


def test_disabling_mail_review_does_not_change_existing_email_task(tmp_path: Path):
    store = _store(tmp_path)
    plan = _plan((EmailAction.UNSUBSCRIBE,))
    task_input = _task_input()
    email_store = _email_store(tmp_path)
    _persist_authorization(email_store, plan, task_input)
    registry = FeatureRegistry(state_path=tmp_path / "skill-state.json")
    enabled_adapter = EmailAgentTaskAdapter(
        store, email_store, feature_registry=registry
    )
    [existing] = enabled_adapter.ensure_action_plan_tasks(plan, task_input)
    registry.set_enabled("mail_review", False)

    [replayed] = enabled_adapter.ensure_action_plan_tasks(plan, task_input)
    assert replayed.task.id == existing.task.id
    assert replayed.task.status == existing.task.status == "pending"


def test_classification_with_zero_agent_actions_creates_no_reply_task(tmp_path: Path):
    store = _store(tmp_path)
    plan = _plan(
        (EmailAction.LABEL, EmailAction.ARCHIVE),
        classification_source="user",
    )

    routes = EmailAgentTaskAdapter(
        store, _email_store(tmp_path)
    ).ensure_action_plan_tasks(
        plan,
        _task_input(),
    )

    assert routes == ()
    assert store.count_reply_tasks(channel="email") == 0


def test_adapter_requires_one_database_for_atomic_email_authorization(
    tmp_path: Path,
):
    task_store = AutoReplyStore(tmp_path / "tasks.sqlite3")
    email_store = EmailStore(tmp_path / "email.sqlite3")

    with pytest.raises(ValueError, match="share one database"):
        EmailAgentTaskAdapter(task_store, email_store)


def test_action_plan_version_and_account_are_part_of_task_identity(tmp_path: Path):
    store = _store(tmp_path)
    email_store = _email_store(tmp_path)
    first_plan = _plan((EmailAction.UNSUBSCRIBE,), version=1)
    second_plan = _plan((EmailAction.UNSUBSCRIBE,), version=2)
    other_account_plan = _plan(
        (EmailAction.UNSUBSCRIBE,),
        version=1,
        classification_id=42,
        account_id="account-secondary",
    )

    adapter = _authorized_adapter(
        tmp_path,
        first_plan,
        _task_input(),
        task_store=store,
        email_store=email_store,
    )
    first = adapter.ensure_action_plan_tasks(first_plan, _task_input())[0]
    _persist_authorization(email_store, second_plan, _task_input())
    second = adapter.ensure_action_plan_tasks(second_plan, _task_input())[0]
    _persist_authorization(
        email_store,
        other_account_plan,
        _task_input(account_id="account-secondary"),
    )
    other = adapter.ensure_action_plan_tasks(
        other_account_plan,
        _task_input(account_id="account-secondary"),
    )[0]

    assert len({first.task.id, second.task.id, other.task.id}) == 3
    assert (
        len(
            {
                first.task.trigger_message_id,
                second.task.trigger_message_id,
                other.task.trigger_message_id,
            }
        )
        == 3
    )


def test_persisted_payload_is_traceable_without_message_secrets_or_attachments(
    tmp_path: Path,
):
    plan = _plan((EmailAction.AUTO_REPLY, EmailAction.UNSUBSCRIBE))
    task_input = _task_input()
    route = _authorized_adapter(tmp_path, plan, task_input).ensure_action_plan_tasks(
        plan, task_input
    )[0]

    payload = json.loads(route.task.trigger_message_json)
    encoded = route.task.trigger_message_json

    assert payload["schema"] == "email_agent_action.v1"
    assert payload["account_id"] == "account-primary"
    assert payload["action_plan_version"] == 1
    assert payload["action_type"] == "unsubscribe"
    assert payload["action_identity"] == route.task.trigger_message_id
    assert payload["model_id"] == "email-model:2026-08-30:sha256:test"
    assert payload["action_parameters"] == {}
    for forbidden in (
        "private-token",
        "do-not-persist",
        "/Users/derek/private",
        "attachment.bin",
        "contract.pdf",
        "application/pdf",
    ):
        assert forbidden not in encoded


def test_unsubscribe_task_projects_only_redacted_real_entry_references(
    tmp_path: Path,
) -> None:
    plan = _plan((EmailAction.UNSUBSCRIBE,))
    private_url = "https://news.example.com/unsubscribe?token=private-token"
    task_input = replace(
        _task_input(),
        list_unsubscribe=f"<{private_url}>",
        list_unsubscribe_post="List-Unsubscribe=One-Click",
        body_text="",
        unsubscribe_authentication=UnsubscribeAuthenticationEvidence(
            dkim_covers_list_unsubscribe=True,
            dkim_covers_list_unsubscribe_post=True,
            evidence_reference="dkim-evidence:mail-41",
        ),
    )

    route = _authorized_adapter(tmp_path, plan, task_input).ensure_action_plan_tasks(
        plan, task_input
    )[0]
    payload = json.loads(route.task.trigger_message_json)
    entries = payload["unsubscribe_entries"]

    assert entries == [
        {
            "index": 0,
            "digest": entries[0]["digest"],
            "reference": entries[0]["reference"],
            "source": "header_one_click_https",
        }
    ]
    assert entries[0]["reference"].startswith("unsubscribe-entry:")
    assert payload["unsubscribe_authentication"] == {
        "evidence_reference": "dkim-evidence:mail-41",
        "one_click_verified": True,
    }
    assert private_url not in route.task.trigger_message_json
    assert private_url not in repr(task_input)

    accepted = ProposedAction.model_validate(
        {
            "action_identity": payload["action_identity"],
            "description": "Unsubscribe the current sender",
            "capability": "email_browser",
            "operation": "unsubscribe",
            "target": {
                "action_identity": payload["action_identity"],
                "account_id": payload["account_id"],
                "stable_message_identity": payload["stable_message_identity"],
                "thread_identity": payload["thread_identity"],
                "entry_reference": entries[0]["reference"],
            },
            "payload": {
                "operations": [
                    {
                        "operation_reference": "step-1",
                        "kind": "post_one_click",
                        "target_reference": entries[0]["reference"],
                    }
                ]
            },
        }
    )

    effect = accepted_email_unsubscribe_effect(route.task, accepted)
    assert effect.entry_reference == entries[0]["reference"]


def test_accepted_unsubscribe_rejects_opening_mailto_entry(tmp_path: Path) -> None:
    plan = _plan((EmailAction.UNSUBSCRIBE,))
    task_input = replace(
        _task_input(),
        list_unsubscribe="<https://example.com/unsubscribe?token=private-token>",
        body_text="",
    )
    route = _authorized_adapter(tmp_path, plan, task_input).ensure_action_plan_tasks(
        plan, task_input
    )[0]
    payload = json.loads(route.task.trigger_message_json)
    [entry] = payload["unsubscribe_entries"]
    entry.update({"source": "header_mailto", "priority": 20})
    persisted_task = route.task.model_copy(
        update={"trigger_message_json": json.dumps(payload, sort_keys=True)}
    )
    accepted = ProposedAction.model_validate(
        {
            "action_identity": payload["action_identity"],
            "description": "Open the projected unsubscribe entry",
            "capability": "email_browser",
            "operation": "unsubscribe",
            "target": {
                "action_identity": payload["action_identity"],
                "account_id": payload["account_id"],
                "stable_message_identity": payload["stable_message_identity"],
                "thread_identity": payload["thread_identity"],
                "entry_reference": entry["reference"],
            },
            "payload": {
                "operations": [
                    {
                        "operation_reference": "step-mailto",
                        "kind": "open_entry",
                        "target_reference": entry["reference"],
                    }
                ]
            },
        }
    )

    effect = accepted_email_unsubscribe_effect(route.task, accepted)
    assert effect.operations[0].kind.value == "open_entry"

    with pytest.raises(ValueError, match="accepted unsubscribe proposal is invalid"):
        accepted_email_unsubscribe_effect(persisted_task, accepted)


def test_non_junk_unsubscribe_is_rejected_before_task_creation(
    tmp_path: Path,
) -> None:
    plan = _plan(
        (EmailAction.UNSUBSCRIBE,),
        category=EmailCategory.NOTIFICATION,
    )
    private_url = "https://news.example.com/unsubscribe?token=private-token"
    task_input = replace(
        _task_input(),
        list_unsubscribe=f"<{private_url}>",
        list_unsubscribe_post="List-Unsubscribe=One-Click",
        body_text="",
        unsubscribe_authentication=UnsubscribeAuthenticationEvidence(
            dkim_covers_list_unsubscribe=True,
            dkim_covers_list_unsubscribe_post=True,
            evidence_reference="dkim-evidence:mail-41",
        ),
    )

    adapter = _authorized_adapter(tmp_path, plan, task_input)
    with pytest.raises(
        EmailAgentTaskMetadataError,
        match="only junk may create an unsubscribe task",
    ):
        adapter.ensure_action_plan_tasks(plan, task_input)
    assert adapter.store.count_reply_tasks(channel="email") == 0


@pytest.mark.parametrize(
    "unsafe_instruction",
    (
        "Use password=do-not-store",
        "Read /Users/derek/private/reply.txt before replying",
        "Open https://example.com/unsubscribe?token=private-token",
    ),
)
def test_disabled_auto_reply_metadata_is_ignored_before_task_persistence(
    tmp_path: Path,
    unsafe_instruction: str,
):
    store = _store(tmp_path)
    plan = _plan((EmailAction.AUTO_REPLY,), instruction=unsafe_instruction)
    task_input = _task_input()

    routes = _authorized_adapter(
        tmp_path,
        plan,
        task_input,
        task_store=store,
    ).ensure_action_plan_tasks(plan, task_input)

    assert routes == ()
    assert store.count_reply_tasks(channel="email") == 0


def test_local_path_in_trace_identity_is_rejected_before_persistence(tmp_path: Path):
    store = _store(tmp_path)
    plan = _plan((EmailAction.UNSUBSCRIBE,))
    task_input = replace(
        _task_input(),
        thread_identity="/Users/derek/private/provider-thread.json",
    )

    with pytest.raises(EmailAgentTaskMetadataError):
        _authorized_adapter(
            tmp_path,
            plan,
            task_input,
            task_store=store,
        ).ensure_action_plan_tasks(plan, task_input)

    assert store.count_reply_tasks(channel="email") == 0


def test_unsafe_prior_receipt_is_rejected_before_context_or_task_persistence(
    tmp_path: Path,
):
    store = _store(tmp_path)
    unsafe_receipt = PriorReceipt(
        receipt_id="receipt-unsafe",
        operation="sent_state_readback",
        summary="authorization_token=do-not-persist",
        completed=False,
    )
    plan = _plan((EmailAction.UNSUBSCRIBE,))
    task_input = replace(_task_input(), prior_receipts=(unsafe_receipt,))

    with pytest.raises(EmailAgentTaskMetadataError):
        _authorized_adapter(
            tmp_path,
            plan,
            task_input,
            task_store=store,
        ).ensure_action_plan_tasks(plan, task_input)

    assert store.count_reply_tasks(channel="email") == 0


def test_email_context_contains_text_metadata_receipts_and_no_image_inputs(
    tmp_path: Path,
):
    plan = _plan((EmailAction.UNSUBSCRIBE,))
    task_input = _task_input()
    adapter = _authorized_adapter(tmp_path, plan, task_input)
    route = adapter.ensure_action_plan_tasks(plan, task_input)[0]
    context = adapter.load_existing_task_context(route.task, task_input)
    rendered = context.render_business_context(current_time="2026-08-30T09:00:00+00:00")

    assert context.channel == "email"
    assert context.image_paths == ()
    assert context.image_sha256s == ()
    assert [message.text for message in context.messages] == [
        "上一封邮件的纯文本回复。",
        _task_input().trigger.text,
    ]
    assert len(context.materials) == 1
    assert context.materials[0].kind == "attachment_metadata"
    assert context.materials[0].read_commands == ()
    assert "contract.pdf" in context.materials[0].reference
    assert "application/pdf" in context.materials[0].reference
    assert "Safe prior execution receipts" in rendered
    assert "sent_state_readback" in rendered
    assert "attachment.bin" in rendered  # email text is allowed as text evidence
    assert "Actual Codex image inputs" not in rendered
    assert "read attachment" not in rendered.casefold()
    assert "Task-bound proposal contract" in rendered
    action = context.required_proposal_action
    assert action["capability"] == "email_browser"
    assert action["operation"] == "unsubscribe"
    assert set(action["payload"]) == {"operations"}
    assert set(action["target"]) == {
        "action_identity",
        "account_id",
        "stable_message_identity",
        "thread_identity",
        "entry_reference",
    }
    assert "classification_id" not in json.dumps(action)


def test_refreshed_email_context_contains_one_opaque_continuation_receipt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plan = _plan(
        (EmailAction.UNSUBSCRIBE,),
        classification_source="user",
        category=EmailCategory.JUNK,
    )
    task_input = _task_input()
    email_store = _email_store(tmp_path)
    adapter = _authorized_adapter(
        tmp_path,
        plan,
        task_input,
        email_store=email_store,
    )
    first = adapter.ensure_action_plan_tasks(plan, task_input)[0]
    payload = json.loads(first.task.trigger_message_json)
    entry_reference = payload["unsubscribe_entries"][0]["reference"]
    operation_reference = "unsubscribe-operation:" + "a" * 64
    control_reference = "unsubscribe-control:" + "b" * 64
    operations = [
        {
            "operation_reference": operation_reference,
            "kind": "open_entry",
            "target_reference": entry_reference,
        }
    ]
    effect_digest = EmailUnsubscribeEffect(
        action_identity=payload["action_identity"],
        action_plan_id=payload["action_plan_id"],
        action_plan_version=payload["action_plan_version"],
        classification_id=payload["classification_id"],
        account_id=payload["account_id"],
        stable_message_identity=payload["stable_message_identity"],
        thread_identity=payload["thread_identity"],
        entry_reference=entry_reference,
        operations=(UnsubscribeOperation.from_mapping(operations[0]),),
    ).effect_digest
    claim = {
        "action_identity": payload["action_identity"],
        "effect_digest": effect_digest,
        "action_plan_id": payload["action_plan_id"],
        "action_plan_version": payload["action_plan_version"],
        "classification_id": payload["classification_id"],
        "account_id": payload["account_id"],
        "stable_message_identity": payload["stable_message_identity"],
        "thread_identity": payload["thread_identity"],
        "entry_reference": entry_reference,
        "operations": operations,
        "status": "awaiting_audit",
        "audit_agent_run_id": 101,
    }
    continuation = {
        "action_identity": payload["action_identity"],
        "effect_digest": effect_digest,
        "previous_effect_digest": "",
        "operations": operations,
        "controls": [
            {
                "reference": control_reference,
                "kind": "button",
                "intent": "confirm",
            }
        ],
        "observation_reference": "unsubscribe-state:" + "d" * 64,
    }
    monkeypatch.setattr(
        email_store,
        "get_email_unsubscribe_claim",
        lambda _identity: claim,
    )
    monkeypatch.setattr(
        email_store,
        "get_email_unsubscribe_continuation",
        lambda _identity: continuation,
    )

    refreshed = adapter.load_existing_task_context(
        adapter.ensure_action_plan_tasks(plan, task_input)[0].task, task_input
    )

    continuation_receipts = [
        receipt
        for receipt in refreshed.prior_receipts
        if receipt.operation == "unsubscribe_continuation"
    ]
    assert len(continuation_receipts) == 1
    receipt_payload = json.loads(continuation_receipts[0].summary)
    assert receipt_payload == {
        "accepted_operations": operations,
        "controls": continuation["controls"],
        "instruction": (
            "Audit accepted the durable prefix; propose exactly one next "
            "operation from the listed opaque controls."
        ),
        "previous_effect_digest": effect_digest,
        "requires_human": False,
    }
    assert "private-token" not in continuation_receipts[0].summary
    assert "https://" not in continuation_receipts[0].summary
    assert "/Users/" not in continuation_receipts[0].summary
    # The continuation turn is told the action to propose, exactly as the
    # initial turn is. Left as prose the Consumer had to invent the payload
    # shape, the operation kind and the append-only prefix, and the strict
    # validators rejected the guess -- that is the whole of
    # unsubscribe_operation_rejected:ValueError.
    prescribed = refreshed.required_proposal_action
    assert prescribed["capability"] == "email_browser"
    assert prescribed["operation"] == "unsubscribe"
    assert prescribed["payload"]["operations"] == [
        *operations,
        {
            "operation_reference": "unsubscribe-operation:click_confirmation",
            "kind": "click_confirmation",
            "target_reference": control_reference,
        },
    ]


def test_a_continuation_offering_a_choice_is_not_prescribed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A control admitting several kinds is a judgement, not a transcription.

    Prescribing one of them would make the decision for the model instead of
    removing a shape it cannot guess, so those turns keep the prose receipt.
    """
    from app.email_task_adapter import (
        _CONTINUATION_CONTROL_KINDS,
    )

    ambiguous = [
        kind for kind, kinds in _CONTINUATION_CONTROL_KINDS.items() if len(kinds) > 1
    ]
    assert ambiguous, "expected at least one control kind with a choice"


def test_refreshed_email_context_rejects_private_continuation_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plan = _plan(
        (EmailAction.UNSUBSCRIBE,),
        classification_source="user",
        category=EmailCategory.JUNK,
    )
    task_input = _task_input()
    email_store = _email_store(tmp_path)
    adapter = _authorized_adapter(
        tmp_path,
        plan,
        task_input,
        email_store=email_store,
    )
    first = adapter.ensure_action_plan_tasks(plan, task_input)[0]
    payload = json.loads(first.task.trigger_message_json)
    claim = {
        "action_identity": payload["action_identity"],
        "effect_digest": "c" * 64,
        "action_plan_id": payload["action_plan_id"],
        "action_plan_version": payload["action_plan_version"],
        "classification_id": payload["classification_id"],
        "account_id": payload["account_id"],
        "stable_message_identity": payload["stable_message_identity"],
        "thread_identity": payload["thread_identity"],
        "entry_reference": payload["unsubscribe_entries"][0]["reference"],
        "operations": [],
        "status": "awaiting_audit",
        "audit_agent_run_id": 101,
    }
    continuation = {
        "action_identity": payload["action_identity"],
        "effect_digest": "c" * 64,
        "previous_effect_digest": "",
        "operations": [],
        "controls": [
            {
                "reference": "https://example.com/unsubscribe?token=private",
                "kind": "button",
                "intent": "confirm",
            }
        ],
        "observation_reference": "unsubscribe-state:" + "d" * 64,
    }
    monkeypatch.setattr(
        email_store,
        "get_email_unsubscribe_claim",
        lambda _identity: claim,
    )
    monkeypatch.setattr(
        email_store,
        "get_email_unsubscribe_continuation",
        lambda _identity: continuation,
    )

    with pytest.raises(EmailAgentTaskMetadataError):
        adapter.load_existing_task_context(first.task, task_input)


def test_terminal_unsubscribe_claim_without_continuation_adds_no_receipt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plan = _plan(
        (EmailAction.UNSUBSCRIBE,),
        classification_source="user",
        category=EmailCategory.JUNK,
    )
    task_input = _task_input()
    email_store = _email_store(tmp_path)
    adapter = _authorized_adapter(
        tmp_path,
        plan,
        task_input,
        email_store=email_store,
    )
    adapter.ensure_action_plan_tasks(plan, task_input)
    monkeypatch.setattr(
        email_store,
        "get_email_unsubscribe_claim",
        lambda _identity: {"status": "done"},
    )
    monkeypatch.setattr(
        email_store,
        "get_email_unsubscribe_continuation",
        lambda _identity: None,
    )

    refreshed = adapter.load_existing_task_context(
        adapter.ensure_action_plan_tasks(plan, task_input)[0].task, task_input
    )

    assert [
        receipt
        for receipt in refreshed.prior_receipts
        if receipt.operation == "unsubscribe_continuation"
    ] == []


def test_task_creation_rejects_wrong_persisted_message_and_historical_plan(
    tmp_path: Path,
):
    task_store = _store(tmp_path)
    email_store = _email_store(tmp_path)
    current_plan = _plan((EmailAction.UNSUBSCRIBE,), version=2)
    persisted_input = _task_input()
    _persist_authorization(email_store, current_plan, persisted_input)
    adapter = EmailAgentTaskAdapter(task_store, email_store)

    wrong_message = replace(
        persisted_input,
        stable_message_identity="account-primary:message-id:<wrong@example.com>",
        trigger=replace(
            persisted_input.trigger,
            message_id="account-primary:message-id:<wrong@example.com>",
        ),
    )
    historical_plan = _plan((EmailAction.UNSUBSCRIBE,), version=1)

    with pytest.raises(EmailAgentTaskConflict):
        adapter.ensure_action_plan_tasks(current_plan, wrong_message)
    with pytest.raises(EmailAgentTaskConflict):
        adapter.ensure_action_plan_tasks(historical_plan, persisted_input)

    assert task_store.count_reply_tasks(channel="email") == 0


def test_current_plan_switch_cannot_interleave_after_authorization_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    database = tmp_path / "email-agent.sqlite3"
    task_store = _store(tmp_path)
    email_store = _email_store(tmp_path)
    task_input = _task_input()
    first_plan = _plan((EmailAction.UNSUBSCRIBE,), version=1)
    second_plan = _plan((EmailAction.UNSUBSCRIBE,), version=2)
    _persist_authorization(email_store, first_plan, task_input)
    adapter = EmailAgentTaskAdapter(task_store, email_store)
    authorization_checked = Event()
    allow_insert = Event()

    def observed_authorization_read(db, classification_id: int):
        row = db.execute(
                f"""
            select
                classifications.id as classification_id,
                classifications.account_id as account_id,
                classifications.stable_message_identity
                    as stable_message_identity,
                messages.thread_identity as thread_identity,
                classifications.current_action_plan_id
                    as current_action_plan_id
                from ({email_classification_projection()}) as classifications
                join ({email_message_projection()}) as messages
              on messages.account_id=classifications.account_id
             and messages.stable_message_identity=
                 classifications.stable_message_identity
            where classifications.id=?
              and classifications.status='processed'
              and classifications.current_action_plan_id is not null
            """,
            (classification_id,),
        ).fetchone()
        authorization_checked.set()
        if not allow_insert.wait(timeout=2):
            raise RuntimeError("test did not release email task insertion")
        return row

    monkeypatch.setattr(
        task_store,
        "_get_current_email_task_authorization",
        observed_authorization_read,
        raising=False,
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            adapter.ensure_action_plan_tasks,
            first_plan,
            task_input,
        )
        try:
            assert authorization_checked.wait(timeout=1)
            competing_email_store = _native_email_store(database)

            def zero_timeout_open_connection(*, foreign_keys: bool = True) -> sqlite3.Connection:
                connection = sqlite3.connect(database, timeout=0)
                connection.execute("pragma busy_timeout = 0")
                connection.execute("pragma foreign_keys = " + ("on" if foreign_keys else "off"))
                connection.row_factory = sqlite3.Row
                return connection

            monkeypatch.setattr(
                competing_email_store,
                "_open_connection",
                zero_timeout_open_connection,
            )
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                competing_email_store.append_action_plan_version(
                    first_plan.classification_id,
                    second_plan,
                    confirmed_category=second_plan.category,
                )
        finally:
            allow_insert.set()
        route = future.result(timeout=2)[0]

    email_store.append_action_plan_version(
        first_plan.classification_id,
        second_plan,
        confirmed_category=second_plan.category,
    )

    assert json.loads(route.task.trigger_message_json)["action_plan_id"] == (
        first_plan.action_plan_id
    )
    assert (
        email_store.get_classification(first_plan.classification_id)[
            "current_action_plan_id"
        ]
        == second_plan.action_plan_id
    )


def test_disabled_auto_reply_payload_cannot_block_unsubscribe_persistence(
    tmp_path: Path,
):
    store = _store(tmp_path)
    plan = _plan(
        (EmailAction.UNSUBSCRIBE, EmailAction.AUTO_REPLY),
        instruction="Read ~/private/reply.txt before replying",
    )
    task_input = _task_input()

    routes = _authorized_adapter(
        tmp_path,
        plan,
        task_input,
        task_store=store,
    ).ensure_action_plan_tasks(plan, task_input)

    assert [route.action_type for route in routes] == [EmailAction.UNSUBSCRIBE]
    assert store.count_reply_tasks(channel="email") == 1


def test_agent_action_identity_conflict_rolls_back_the_whole_plan(tmp_path: Path):
    store = _store(tmp_path)
    plan = _plan((EmailAction.AUTO_REPLY, EmailAction.UNSUBSCRIBE))
    task_input = _task_input()
    conversation_id = email_conversation_id(
        plan.account_id,
        task_input.thread_identity,
    )
    unsubscribe_identity = email_action_identity(
        account_id=plan.account_id,
        stable_message_identity=task_input.stable_message_identity,
        action_type=EmailAction.UNSUBSCRIBE,
        action_plan_version=plan.action_plan_version,
    )
    auto_reply_identity = email_action_identity(
        account_id=plan.account_id,
        stable_message_identity=task_input.stable_message_identity,
        action_type=EmailAction.AUTO_REPLY,
        action_plan_version=plan.action_plan_version,
    )
    store.ensure_reply_task(
        channel="email",
        conversation_id=conversation_id,
        conversation_title="Conflicting historical input",
        single_chat=False,
        trigger_message_id=unsubscribe_identity,
        trigger_create_time=task_input.trigger.create_time,
        trigger_sender=task_input.trigger.sender,
        trigger_text="Conflicting historical input",
        trigger_message_json=json.dumps({"action_identity": "different"}),
    )
    adapter = _authorized_adapter(
        tmp_path,
        plan,
        task_input,
        task_store=store,
    )

    with pytest.raises(EmailAgentTaskConflict):
        adapter.ensure_action_plan_tasks(plan, task_input)

    assert store.count_reply_tasks(channel="email") == 1
    with sqlite3.connect(tmp_path / "email-agent.sqlite3") as db:
        assert (
            db.execute(
                "select count(*) from task_inputs where channel='email' and trigger_message_id=?",
                (auto_reply_identity,),
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize(
    "unsafe_metadata",
    (
        {"outer": [{"path": "~/private/reply.txt"}]},
        {"outer": {"path": "~someone/private/reply.txt"}},
        {"outer": [{"uri": "FiLe:///Users/derek/private/reply.txt"}]},
        {"outer": [{"uri": "file%3A///Users/derek/private/reply.txt"}]},
    ),
)
def test_nested_home_relative_paths_and_file_uris_are_rejected(
    unsafe_metadata: object,
):
    with pytest.raises(EmailAgentTaskMetadataError):
        _assert_safe_email_metadata(unsafe_metadata)


@pytest.mark.parametrize(
    "unsafe_value",
    (
        _percent_encode("file:///Users/derek/private/reply.txt", 3),
        _percent_encode("~/private/reply.txt", 3),
        _percent_encode("https://example.com/unsubscribe/confirm", 3),
        _percent_encode(
            "https://example.com/resource?token=do-not-persist",
            3,
        ),
    ),
)
def test_metadata_canonicalization_rejects_triple_encoded_unsafe_values(
    unsafe_value: str,
):
    with pytest.raises(EmailAgentTaskMetadataError) as error:
        _assert_safe_email_metadata({"outer": [{"value": unsafe_value}]})

    assert unsafe_value not in str(error.value)


def test_metadata_canonicalization_rejects_excessive_encoding_depth():
    unsafe_value = _percent_encode("file:///private/reply.txt", 10)

    with pytest.raises(EmailAgentTaskMetadataError) as error:
        _assert_safe_email_metadata(unsafe_value)

    assert unsafe_value not in str(error.value)


def test_metadata_canonicalization_rejects_oversized_text():
    oversized = "a" * 70_000

    with pytest.raises(EmailAgentTaskMetadataError):
        _assert_safe_email_metadata(oversized)


def test_safe_public_https_url_is_allowed_in_action_metadata(tmp_path: Path):
    store = _store(tmp_path)
    plan = _plan(
        (EmailAction.AUTO_REPLY,),
        instruction="Reference https://docs.example.com/help/getting-started?lang=zh.",
    )
    task_input = _task_input()

    routes = _authorized_adapter(
        tmp_path,
        plan,
        task_input,
        task_store=store,
    ).ensure_action_plan_tasks(
        plan,
        task_input,
    )

    assert routes == ()
    assert store.count_reply_tasks(channel="email") == 0


@pytest.mark.parametrize(
    "sensitive_url",
    (
        "https://example.com/resource?token=do-not-persist",
        "https://example.com/resource?X-Amz-Signature=do-not-persist",
        "https://example.com/resource?X-Amz-SignedHeaders=host",
        "https://example.com/resource#access_token=do-not-persist",
        "https://example.com/unsubscribe/confirm",
        "https://unsubscribe.example.com/confirm",
        "https://example.com/preferences?action=opt-out",
    ),
)
def test_disabled_auto_reply_sensitive_url_is_not_persisted(
    tmp_path: Path,
    sensitive_url: str,
):
    plan = _plan(
        (EmailAction.AUTO_REPLY,),
        instruction=f"Reference {sensitive_url}",
    )
    task_input = _task_input()
    store = _store(tmp_path)
    routes = _authorized_adapter(
        tmp_path,
        plan,
        task_input,
        task_store=store,
    ).ensure_action_plan_tasks(plan, task_input)

    assert routes == ()
    assert store.count_reply_tasks(channel="email") == 0


def test_thread_identity_is_normalized_once_for_identity_payload_and_context(
    tmp_path: Path,
):
    store = _store(tmp_path)
    plan = _plan((EmailAction.UNSUBSCRIBE,))
    spaced = replace(_task_input(), thread_identity="  thread-customer-41  ")
    adapter = _authorized_adapter(
        tmp_path,
        plan,
        spaced,
        task_store=store,
    )

    first = adapter.ensure_action_plan_tasks(plan, spaced)[0]
    replay = adapter.ensure_action_plan_tasks(plan, _task_input())[0]

    assert first.task.id == replay.task.id
    assert json.loads(first.task.trigger_message_json)["thread_identity"] == (
        "thread-customer-41"
    )
    assert adapter.load_existing_task_context(first.task, spaced).conversation_id == (
        adapter.load_existing_task_context(replay.task, _task_input()).conversation_id
    )


def test_enqueue_does_not_build_unused_execution_context(tmp_path, monkeypatch):
    plan = _plan((EmailAction.UNSUBSCRIBE,))
    task_input = _task_input()
    adapter = _authorized_adapter(tmp_path, plan, task_input)

    def unexpected(**kwargs):
        raise AssertionError("enqueue should not build execution context")

    monkeypatch.setattr(adapter, "_build_context", unexpected)
    [route] = adapter.ensure_action_plan_tasks(plan, task_input)
    assert route.task.channel == "email"
    assert not hasattr(route, "context")
