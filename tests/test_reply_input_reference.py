import sqlite3

import pytest

from app.store import AutoReplyStore


def values(text="first"):
    return dict(
        conversation_id="chat",
        conversation_title="Chat",
        single_chat=False,
        trigger_message_id="message",
        trigger_create_time="2026-10-11",
        trigger_sender="sender",
        trigger_text=text,
        trigger_message_json='{"schema":"sample","message_id":"message"}',
    )


def test_parent_has_real_input_foreign_keys_and_no_payload_cache(tmp_path):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    task = store.ensure_reply_task(**values()).id
    with sqlite3.connect(store.path) as db:
        columns = {r[1] for r in db.execute("pragma table_info(reply_tasks)")}
        assert (
            not {
                "trigger_text",
                "trigger_message_json",
                "input_compacted",
                "input_provenance_json",
            }
            & columns
        )
        keys = {
            (r[3], r[2], r[4])
            for r in db.execute("pragma foreign_key_list(reply_tasks)")
        }
        assert ("current_input_id", "reply_task_inputs", "id") in keys
        assert ("claimed_input_id", "reply_task_inputs", "id") in keys
        assert db.execute(
            "select current_input_id from reply_tasks where id=?", (task,)
        ).fetchone()[0]


def test_claim_keeps_exact_input_when_current_context_changes(tmp_path):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    task_id = store.ensure_reply_task(**values()).id
    claimed = store.claim_reply_task(task_id)
    updated = values("second")
    updated["trigger_message_id"] = "message2"
    updated["business_object_key"] = store.get_reply_task(task_id).business_object_key
    store.enqueue_reply_task(**updated)
    assert store.get_reply_task(task_id).trigger_text == "second"
    execution = store.get_reply_task_for_execution(
        task_id, claimed.execution_generation
    )
    assert execution.trigger_text == "first"
    assert execution.trigger_message_id == "message"
    with sqlite3.connect(store.path) as db:
        current, original = db.execute(
            "select current_input_id, claimed_input_id from reply_tasks where id=?",
            (task_id,),
        ).fetchone()
        assert current != original


def test_terminal_input_persists_no_compact_json_but_projects_old_shape(tmp_path):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    task_id = store.ensure_reply_task(**values()).id
    task = store.claim_reply_task(task_id)
    store.complete_reply_task(
        task_id, expected_execution_generation=task.execution_generation
    )
    result = store.get_reply_task(task_id)
    assert result.input_compacted
    assert '"input_compacted":true' in result.trigger_message_json
    with sqlite3.connect(store.path) as db:
        assert db.execute(
            "select trigger_text, trigger_message_json, input_compacted from reply_task_inputs"
        ).fetchone() == ("", "", 1)
    assert (
        store.list_reply_task_inputs(task_id)[0]["trigger_message_json"]
        == result.trigger_message_json
    )


def test_refresh_appends_without_changing_old_revision(tmp_path):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    task_id = store.ensure_reply_task(**values()).id
    old = store.list_reply_task_inputs(task_id)[0]
    assert (
        store.update_reply_task_trigger(
            task_id, trigger_text="refresh", trigger_message_json="{}"
        )
        == 1
    )
    inputs = store.list_reply_task_inputs(task_id)
    assert inputs[0] == old
    assert inputs[-1]["trigger_text"] == "refresh"
    assert store.get_reply_task(task_id).trigger_text == "refresh"


def test_manual_rerun_appends_exact_new_input(tmp_path):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    task_id = store.ensure_reply_task(**values()).id
    store.enqueue_manual_rerun_reply_task(**values("rerun"), attempt_id=0)
    assert store.list_reply_task_inputs(task_id)[-1]["trigger_text"] == "rerun"
    assert store.get_reply_task(task_id).trigger_text == "rerun"


def test_legacy_current_mismatch_and_missing_input_preserve_old_rows_and_watermarks(
    tmp_path,
):
    import app.store as store_module
    from tests.reply_schema_fixtures import migrate_legacy_reply_parent, restore_legacy_reply_parent

    path = tmp_path / "legacy.sqlite3"
    store = AutoReplyStore(path)
    first = store.ensure_reply_task(**values()).id
    missing_values = values("missing body")
    missing_values["trigger_message_id"] = "missing"
    missing = store.ensure_reply_task(**missing_values).id
    old = store.list_reply_task_inputs(first)[0]
    restore_legacy_reply_parent(store)
    with sqlite3.connect(path) as db:
        db.execute(
            "update reply_tasks set trigger_text='actual current', input_version=17, claimed_input_version=6, status='needs_human' where id=?",
            (first,),
        )
        db.execute("delete from reply_task_inputs where reply_task_id=?", (missing,))
    migrated = migrate_legacy_reply_parent(store)
    assert migrated.get_reply_task(first).trigger_text == "actual current"
    assert migrated.get_reply_task(first).input_version == 17
    assert migrated.get_reply_task(first).claimed_input_version == 6
    assert migrated.get_reply_task(first).claimed_input_id is None
    assert migrated.list_reply_task_inputs(first)[0] == old
    assert migrated.get_reply_task(missing).trigger_text == "missing body"
    ids = [row["id"] for row in migrated.list_reply_task_inputs(first)]
    assert len(ids) == 2
    store_module._INITIALIZED_STORE_PATHS.discard(path.resolve())
    assert [
        row["id"] for row in AutoReplyStore(path).list_reply_task_inputs(first)
    ] == ids


def test_legacy_migration_selects_exact_old_row_instead_of_latest_id(tmp_path):
    from tests.reply_schema_fixtures import migrate_legacy_reply_parent, restore_legacy_reply_parent

    store = AutoReplyStore(tmp_path / "legacy.sqlite3")
    task = store.ensure_reply_task(**values()).id
    original = store.list_reply_task_inputs(task)[0]["id"]
    restore_legacy_reply_parent(store)
    with sqlite3.connect(store.path) as db:
        db.execute(
            "insert into reply_task_inputs (reply_task_id,channel,conversation_id,conversation_title,single_chat,trigger_message_id,trigger_create_time,trigger_sender,trigger_text,trigger_message_json,business_object_key,input_revision_key) select reply_task_id,channel,conversation_id,conversation_title,single_chat,trigger_message_id,trigger_create_time,trigger_sender,'not current',trigger_message_json,business_object_key,'later revision' from reply_task_inputs where id=?",
            (original,),
        )
        db.execute(
            "update reply_tasks set input_version=29, claimed_input_version=8 where id=?",
            (task,),
        )
    migrated = migrate_legacy_reply_parent(store)
    assert migrated.get_reply_task(task).current_input_id == original
    assert len(migrated.list_reply_task_inputs(task)) == 2
    assert migrated.get_reply_task(task).input_version == 29


def test_legacy_live_claim_blocks_atomically(tmp_path):
    import pytest
    from tests.reply_schema_fixtures import migrate_legacy_reply_parent, restore_legacy_reply_parent

    store = AutoReplyStore(tmp_path / "legacy.sqlite3")
    task = store.ensure_reply_task(**values()).id
    restore_legacy_reply_parent(store)
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update reply_tasks set status='processing',claimed_input_version=1 where id=?",
            (task,),
        )
    with pytest.raises(RuntimeError, match="quiet"):
        migrate_legacy_reply_parent(store)
    with sqlite3.connect(store.path) as db:
        assert "current_input_id" not in {
            row[1] for row in db.execute("pragma table_info(reply_tasks)")
        }
        assert (
            db.execute(
                "select value from service_state where key='store_schema_version'"
            ).fetchone()[0]
            == "2026-10-11.2"
        )
        assert db.execute("select count(*) from reply_task_inputs").fetchone()[0] == 1


def test_inverse_restores_exact_ddl_and_latest_current_without_erasing_claimed_revision(
    tmp_path,
):
    from tests.reply_schema_fixtures import migrate_legacy_reply_parent, restore_legacy_reply_parent

    store = AutoReplyStore(tmp_path / "inverse.sqlite3")
    task = store.ensure_reply_task(**values()).id
    ddl, indexes = restore_legacy_reply_parent(store)
    migrated = migrate_legacy_reply_parent(store)
    claimed = migrated.claim_reply_task(task)
    updated = values("new current")
    updated.update(
        trigger_message_id="second-message",
        business_object_key=claimed.business_object_key,
    )
    migrated.enqueue_reply_task(**updated)
    before = migrated.list_reply_task_inputs(task)
    current = migrated.get_reply_task(task)
    with sqlite3.connect(store.path) as db:
        db.row_factory = sqlite3.Row
        original_seq = db.execute(
            "select seq from sqlite_sequence where name='reply_tasks'"
        ).fetchone()[0]
        db.execute("pragma foreign_keys=OFF")
        db.execute("begin immediate")
        AutoReplyStore.restore_reply_task_input_schema(
            db, original_tasks_sql=ddl, original_indexes=indexes
        )
        assert db.execute(
            "select sql from sqlite_master where name='reply_tasks'"
        ).fetchone()[0] == ddl.rstrip(";")
        row = db.execute("select * from reply_tasks where id=?", (task,)).fetchone()
        assert row["trigger_text"] == "new current"
        assert row["status"] == "processing"
        assert row["input_version"] == current.input_version
        assert row["claimed_input_version"] == claimed.claimed_input_version
        assert (
            db.execute(
                "select seq from sqlite_sequence where name='reply_tasks'"
            ).fetchone()[0]
            == original_seq
        )
        assert [
            dict(row)
            for row in db.execute("select * from reply_task_inputs order by id")
        ] == before
        assert db.execute("pragma foreign_key_check").fetchall() == []


def test_retired_projection_round_trip_retains_provenance_without_a_second_json(
    tmp_path,
):
    from tests.reply_schema_fixtures import migrate_legacy_reply_parent, restore_legacy_reply_parent

    store = AutoReplyStore(tmp_path / "retired.sqlite3")
    task = store.ensure_reply_task(**values()).id
    claimed = store.claim_reply_task(task)
    store.complete_reply_task(
        task, expected_execution_generation=claimed.execution_generation
    )
    projected = store.get_reply_task(task)
    original_inputs = store.list_reply_task_inputs(task)
    ddl, indexes = restore_legacy_reply_parent(store)
    migrated = migrate_legacy_reply_parent(store)
    assert (
        migrated.get_reply_task(task).trigger_message_json
        == projected.trigger_message_json
    )
    assert (
        migrated.get_reply_task(task).input_provenance_json
        == projected.input_provenance_json
    )
    assert migrated.list_reply_task_inputs(task) == original_inputs
    with sqlite3.connect(store.path) as db:
        assert db.execute(
            "select trigger_text, trigger_message_json from reply_task_inputs"
        ).fetchone() == ("", "")
        db.row_factory = sqlite3.Row
        db.execute("pragma foreign_keys=OFF")
        db.execute("begin immediate")
        AutoReplyStore.restore_reply_task_input_schema(
            db, original_tasks_sql=ddl, original_indexes=indexes
        )
        assert (
            db.execute("select trigger_message_json from reply_task_inputs").fetchone()[
                0
            ]
            == projected.trigger_message_json
        )
        assert (
            db.execute("select trigger_message_json from reply_tasks").fetchone()[0]
            == projected.trigger_message_json
        )


def test_legacy_retired_json_must_be_derivable_before_drop(tmp_path):
    import pytest
    from tests.reply_schema_fixtures import migrate_legacy_reply_parent, restore_legacy_reply_parent

    store = AutoReplyStore(tmp_path / "invalid-retired.sqlite3")
    task = store.ensure_reply_task(**values()).id
    claimed = store.claim_reply_task(task)
    store.complete_reply_task(
        task, expected_execution_generation=claimed.execution_generation
    )
    restore_legacy_reply_parent(store)
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update reply_task_inputs set trigger_message_json='{}' where reply_task_id=?",
            (task,),
        )
    with pytest.raises(RuntimeError, match="not derivable"):
        migrate_legacy_reply_parent(store)
    with sqlite3.connect(store.path) as db:
        assert "trigger_message_json" in {
            row[1] for row in db.execute("pragma table_info(reply_tasks)")
        }
        assert (
            db.execute(
                "select value from service_state where key='store_schema_version'"
            ).fetchone()[0]
            == "2026-10-11.2"
        )


def test_inverse_preserves_autoincrement_sequence_after_all_tasks_removed(tmp_path):
    from tests.reply_schema_fixtures import migrate_legacy_reply_parent, restore_legacy_reply_parent

    store = AutoReplyStore(tmp_path / "empty.sqlite3")
    task = store.ensure_reply_task(**values()).id
    ddl, indexes = restore_legacy_reply_parent(store)
    with sqlite3.connect(store.path) as db:
        db.execute("delete from business_object_tasks")
        db.execute("delete from reply_task_inputs")
        db.execute("delete from reply_tasks where id=?", (task,))
        db.execute("update sqlite_sequence set seq=77 where name='reply_tasks'")
    migrate_legacy_reply_parent(store)
    with sqlite3.connect(store.path) as db:
        db.row_factory = sqlite3.Row
        db.execute("pragma foreign_keys=OFF")
        db.execute("begin immediate")
        AutoReplyStore.restore_reply_task_input_schema(
            db, original_tasks_sql=ddl, original_indexes=indexes
        )
        assert (
            db.execute(
                "select seq from sqlite_sequence where name='reply_tasks'"
            ).fetchone()[0]
            == 77
        )


def test_frozen_skill_reader_uses_claimed_material_after_current_input_changes(
    tmp_path,
):
    import json
    from app.agent_cli import build_role_server
    from app.agent_cron.commands import (
        ServiceCommandConsumerContext,
        ServiceCommandSkillMaterial,
    )

    def trigger(content):
        context = ServiceCommandConsumerContext(
            scheduled_task_id=11,
            scheduled_task_run_id=29,
            prompt="Read exact Skill",
            skill_names=("ceo-calendar-invite",),
            skill_protocol="Read selected Skill",
            skill_materials=(
                ServiceCommandSkillMaterial(
                    name="ceo-calendar-invite", content=content
                ),
            ),
        )
        return json.dumps({"raw_payload": {"scheduled_consumer": context.to_payload()}})

    store = AutoReplyStore(tmp_path / "claimed-skill.sqlite3")
    first = values()
    first["trigger_message_json"] = trigger("original frozen material")
    task = store.ensure_reply_task(**first)
    claimed = store.claim_reply_task(task.id)
    second = values("new trigger")
    second.update(
        trigger_message_id="later",
        business_object_key=task.business_object_key,
        trigger_message_json=trigger("later frozen material"),
    )
    store.ensure_reply_task(**second)
    for role in ("consumer", "audit"):
        read = (
            build_role_server(
                role,
                task_id=task.id,
                db_path=store.path,
                execution_generation=claimed.execution_generation,
            )
            ._tool_manager.get_tool("read_task_skill")
            .fn
        )
        assert read("ceo-calendar-invite")["content"] == "original frozen material"


@pytest.mark.parametrize("changed_field", ["trigger_text", "trigger_message_json"])
def test_single_chat_same_message_body_or_json_refresh_rotates_generation_and_returns_change(
    tmp_path, changed_field
):
    store = AutoReplyStore(tmp_path / "single-refresh.sqlite3")
    original = values()
    original["single_chat"] = True
    task = store.ensure_reply_task(**original)
    before = store.list_reply_task_inputs(task.id)
    update = {
        field: original[field]
        for field in (
            "conversation_id",
            "trigger_message_id",
            "trigger_create_time",
            "trigger_sender",
            "trigger_text",
            "trigger_message_json",
        )
    }
    update[changed_field] = (
        "changed body" if changed_field == "trigger_text" else '{"schema":"revised"}'
    )
    result = store.replace_pending_single_chat_reply_task_trigger(**update)
    assert result == 1
    current = store.get_reply_task(task.id)
    assert current.execution_generation != task.execution_generation
    assert getattr(current, changed_field) == update[changed_field]
    assert store.list_reply_task_inputs(task.id)[:-1] == before
    assert current.current_input_id == store.list_reply_task_inputs(task.id)[-1]["id"]


def test_deferral_reclaim_same_generation_captures_latest_current_snapshot(tmp_path):
    store = AutoReplyStore(tmp_path / "reclaim.sqlite3")
    task = store.ensure_reply_task(**values())
    first = store.claim_reply_task(task.id)
    second = values("latest")
    second.update(
        trigger_message_id="later", business_object_key=task.business_object_key
    )
    store.ensure_reply_task(**second)
    store.defer_reply_task(
        task.id, "capacity", expected_execution_generation=first.execution_generation
    )
    assert (
        store.get_reply_task_for_execution(
            task.id, first.execution_generation
        ).trigger_text
        == "first"
    )
    reclaimed = store.claim_reply_task(task.id)
    assert reclaimed.execution_generation == first.execution_generation
    assert reclaimed.claimed_input_id == reclaimed.current_input_id
    assert reclaimed.claimed_input_id != first.claimed_input_id
    assert reclaimed.claimed_input_version == reclaimed.input_version
    assert (
        store.get_reply_task_for_execution(
            task.id, reclaimed.execution_generation
        ).trigger_text
        == "latest"
    )


@pytest.mark.parametrize("operation", ["dispatch", "reconcile"])
def test_reviewed_dingtalk_reply_handler_uses_claimed_message_after_new_input(
    tmp_path, operation
):
    from types import SimpleNamespace
    from app.agent_contracts import ProposedAction
    from app.dingtalk_models import DingTalkMessage
    from app.service_message_sender import agent_message_delivery_key
    from app.system_action_handlers import DingTalkMessageHandler

    store = AutoReplyStore(tmp_path / "reviewed-reply.sqlite3")
    first = values()
    first["trigger_message_json"] = DingTalkMessage(
        open_conversation_id="chat",
        open_message_id="message",
        conversation_title="Chat",
        single_chat=False,
        sender_name="sender",
        create_time="2026-10-11",
        content="first",
    ).model_dump_json()
    task = store.ensure_reply_task(**first)
    claimed = store.claim_reply_task(task.id)
    key = agent_message_delivery_key(
        business_object_key=claimed.business_object_key,
        action_identity="reply-original",
        execution_generation=claimed.execution_generation,
        proposal_revision=0,
    )
    prepared = store.prepare_outbound_postfix(
        "dingtalk", key, "Reviewed reply", "first", feedback_base_url=""
    )
    second = values("latest")
    second.update(
        trigger_message_id="later", business_object_key=task.business_object_key
    )
    second["trigger_message_json"] = DingTalkMessage(
        open_conversation_id="chat",
        open_message_id="later",
        conversation_title="Chat",
        single_chat=False,
        sender_name="sender",
        create_time="2026-10-11",
        content="latest",
    ).model_dump_json()
    store.ensure_reply_task(**second)
    observed = []

    class Sender:
        def send_dingtalk_reply_to_trigger_prepared(
            self, prepared, *, conversation, trigger
        ):
            observed.append(trigger.open_message_id)
            return SimpleNamespace(provider_result={"receipt": "local-test"})

    class Dws:
        def verify_message_send_result(self, result):
            return {"state": "sent"}

        def reconcile_reply_to_trigger(self, conversation, trigger, body):
            observed.append(trigger.open_message_id)
            return {"receipt": "local-test"}

    action = ProposedAction(
        description="Reply",
        action_identity="reply-original",
        capability="dingtalk-chat",
        operation="reply_to_message",
        target={"conversation_id": "chat", "message_id": "message"},
        payload={"content": prepared.final_body},
    )
    candidate = dict(
        task_id=task.id,
        proposal_revision=0,
        execution_generation=claimed.execution_generation,
    )
    result = getattr(DingTalkMessageHandler(store, Dws(), sender=Sender()), operation)(
        action, action_key="key", candidate=candidate
    )
    assert result.status == "verified"
    assert observed == ["message"]


def test_wechat_execution_finalizer_records_claimed_source_while_current_differs(
    tmp_path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "wechat-claimed.sqlite3")
    first = values()
    first.update(channel="wechat", single_chat=True)
    task = store.ensure_reply_task(**first)
    claimed = store.claim_reply_task(task.id)
    second = values("latest current")
    second.update(
        channel="wechat",
        single_chat=True,
        trigger_message_id="later",
        business_object_key=task.business_object_key,
    )
    store.ensure_reply_task(**second)
    observed = []
    original_prepare = store._prepare_outbound_postfix_in_transaction

    def prepare(db, **kwargs):
        observed.append(kwargs["original_text"])
        return original_prepare(db, **kwargs)

    monkeypatch.setattr(store, "_prepare_outbound_postfix_in_transaction", prepare)
    attempt = store.finalize_wechat_reply_task(
        task_id=task.id,
        expected_execution_generation=claimed.execution_generation,
        action="send_reply",
        sensitivity_kind="normal",
        codex_reason="reviewed",
        draft_reply_text="Reviewed reply",
        audit_summary="approved",
        send_status="done",
        account_id="account",
        target_type="direct",
        target_id="target",
        conversation_id="chat",
        reply_text="Reviewed reply",
    )
    persisted = store.get_reply_attempt(attempt)
    assert persisted.trigger_message_id == "message"
    assert persisted.trigger_text == "first"
    assert observed == ["first"]
    assert store.get_reply_task(task.id).trigger_text == "latest current"
