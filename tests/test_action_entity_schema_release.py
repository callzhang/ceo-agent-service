from __future__ import annotations

from contextlib import closing
import json
import sqlite3

import pytest

from app import action_entity_schema_release as release


SOURCE_VERSION = "2026-10-19.12"
TARGET_VERSION = "2026-10-21.1"
SOURCE_MARKER_TIME = "  original marker time  "


def test_current_release_ddl_matches_target_manifest():
    from pathlib import Path
    from tools.action_entity_contract import build_current_release_target_ddl

    ddl = Path(__file__).resolve().parents[1] / "docs/action-entity-current-target.sql"
    rendered = ddl.read_text()
    assert rendered == build_current_release_target_ddl()
    assert "foreign key(email_action_id) references actions(id)" in rendered
    assert "trg_actions_email_retry_parent_insert" in rendered


def _source_database(path):
    """Build a synthetic Store19.12/Email53 with its pre-Action declaration."""
    from app.current_action_schema_release import source_objects
    from app.state_storage import insert_state
    from app.task8_storage import register_task8_functions

    with closing(sqlite3.connect(path)) as db:
        register_task8_functions(db)
        db.execute("pragma foreign_keys=off")
        for kind, name, table, sql in sorted(
                source_objects("fresh_memory_fk"), key=lambda item: item[0] != "table"):
            db.execute(sql)
        db.execute("begin")
        insert_state(db, "service_state", {"key": "store_schema_version", "value": SOURCE_VERSION,
                                            "updated_at": SOURCE_MARKER_TIME})
        db.execute("insert into email_schema_migrations(version,applied_at) values(53,' email marker ')")
        db.commit()


def _prepare(path):
    contract = release.prepare_action_entity_release(path)
    assert contract["source_version"] == SOURCE_VERSION
    return contract


def _snapshot(path):
    with closing(sqlite3.connect(path)) as db:
        tables = {row[0] for row in db.execute(
            "select name from sqlite_schema where type='table'")}
        def rows(query, table):
            return db.execute(query).fetchall() if table in tables else []
        return {
            "marker": tuple(db.execute("select value,updated_at from (select * from state where record_type='service_state') where key='store_schema_version'").fetchone()),
            "actions": db.execute("select id,_kind,legacy_key,record_type,payload_json from actions order by id").fetchall(),
            "receipts": rows("select * from action_receipts order by id", "action_receipts"),
            "observers": rows("select * from action_observers order by action_id,agent_run_id", "action_observers"),
            "history_refs": db.execute("select legacy_key,payload_json from actions where record_type='email_historical_operations' order by legacy_key").fetchall(),
        }


def test_forward_preserves_business_facts_and_receipts(tmp_path):
    path = tmp_path / "actions.sqlite3"
    _source_database(path)
    contract = _prepare(path)
    release.publish_action_entity_release(path, contract)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("select value from (select * from state where record_type='service_state') where key='store_schema_version'").fetchone() == (TARGET_VERSION,)
        assert db.execute("select max(version) from email_schema_migrations").fetchone() == (53,)


def test_populated_external_result_is_transferred_to_common_action(tmp_path, monkeypatch):
    path = tmp_path / "populated-result.sqlite3"
    _source_database(path)
    with closing(sqlite3.connect(path)) as db:
        from app.action_storage import insert_action
        from app import action_storage
        from app.execution_owner_storage import create_plan_execution
        from app.run_storage import insert_run
        from app.task8_storage import register_task8_functions, task8_insert_parts
        import re
        from app.current_action_schema_release import source_objects

        register_task8_functions(db)
        actions_sql = next(obj[3] for obj in source_objects("fresh_memory_fk")
                           if obj[0] == "table" and obj[1] == "actions")
        source_codes = {name: int(code) for code, name in re.findall(
            r"when (\d+) then '([^']+)'", actions_sql)}

        class SourceRecordTypes:
            def code(self, name):
                return source_codes[name]

        monkeypatch.setattr(action_storage, "ACTION_RECORD_TYPES", SourceRecordTypes())

        task_parts = task8_insert_parts("reply_tasks", {
            "channel": "dingtalk", "conversation_id": "conversation-5",
            "conversation_title": "source", "single_chat": 0,
            "trigger_message_id": "message-5", "trigger_create_time": "source",
            "trigger_sender": "sender-5", "status": "done",
            "business_object_key": "reply-task-23",
        })
        task_id = db.execute(
            f"insert into tasks({task_parts.columns}) values({task_parts.values}) returning id",
            task_parts.parameters,
        ).fetchone()[0]
        insert_run(db, "agent_runs", {
            "id": 901, "task_id": task_id,
            "execution_generation": "generation-17", "role": "consumer",
        })

        insert_action(db, "external_action_results", {
            "external_action_key": "provider-effect-17",
            "business_object_key": "reply-task-23",
            "action_identity": "send_notice",
            "operation": "send_group_message",
            "target_identifiers_json": '{"conversation_id":"conversation-5"}',
            "provider_result_json": '{"messageId":"provider-17"}',
            "result_digest": "sha256:provider-17",
            "first_agent_run_id": 901,
            "created_at": "2026-10-21T10:00:00Z",
        })
        insert_action(db, "review_candidates", {
            "id": 201, "task_id": task_id, "execution_generation": "generation-17",
            "consumer_run_id": 901, "stage_index": 0, "proposal_revision": 0,
            "candidate_digest": "candidate-digest",
            "candidate_json": '{"proposal":{"actions":[{"action_identity":"send_notice",'
                              '"operation":"send_group_message",'
                              '"target":{"conversation_id":"conversation-5"}}]}}',
            "created_at": "candidate-clock",
        }, execution_owner=True)
        insert_action(db, "candidate_reviews", {
            "id": 202, "candidate_id": 201, "audit_run_id": 901,
            "decision": "approve", "candidate_digest": "candidate-digest",
            "result_json": "{}", "created_at": "review-clock",
        })
        execution_id = create_plan_execution(db, candidate_id=201, review_id=202, selection_id=None,
                                             status="running", lease_owner="test", lease_expires_at="")
        insert_action(db, "candidate_action_attempts", {
            "id": 203, "execution_id": execution_id, "action_index": 0,
            "external_action_key": "provider-effect-17", "status": "verified",
            "result_json": None, "created_at": "attempt-clock", "updated_at": "attempt-updated",
        }, execution_owner=True)
        db.commit()

    contract = _prepare(path)
    release.publish_action_entity_release(path, contract)
    with closing(sqlite3.connect(path)) as db:
        action = db.execute(
            "select id,action_external_action_key,action_business_object_key,"
            "action_action_identity from actions where record_type='action'"
        ).fetchone()
        assert action is not None
        action_id = action[0]
        assert action[1:] == (
            "provider-effect-17", "reply-task-23", "send_notice",
        )
        assert db.execute(
            "select operation,target,provider_result_json,result_digest from action_receipts where action_id=?",
            (action_id,),
        ).fetchone() == (
            "send_group_message", '{"conversation_id":"conversation-5"}',
            '{"messageId":"provider-17"}', "sha256:provider-17",
        )
        maps = db.execute(
            "select source_type,action_id,receipt_id from action_entity_identity_map order by source_type"
        ).fetchall()
        canonical_receipt = db.execute(
            "select id from action_receipts where action_id=? and execution_id is null", (action_id,),
        ).fetchone()[0]
        attempt_receipt = db.execute(
            "select id from action_receipts where action_id=? and execution_id is not null", (action_id,),
        ).fetchone()[0]
        assert maps == [
            ("candidate_action_attempts", action_id, attempt_receipt),
            ("external_action_results", action_id, canonical_receipt),
        ]
        assert db.execute("select action_id,execution_id,action_index,status,outcome_json,provider_receipt_id "
                          "from action_receipts where action_id=? and execution_id is not null",
                          (action_id,)).fetchall() == [
            (action_id, execution_id, 0, "verified", None,
             db.execute("select id from action_receipts where action_id=? and execution_id is null",
                        (action_id,)).fetchone()[0])]
        assert db.execute("select action_id,receipt_id from action_entity_identity_map "
                          "where source_type='candidate_action_attempts'").fetchone()[0] == action_id


def test_populated_prepared_sent_and_observer_facts_keep_distinct_receipts(tmp_path, monkeypatch):
    path = tmp_path / "prepared-sent.sqlite3"
    _source_database(path)
    with closing(sqlite3.connect(path)) as db:
        from app.action_storage import insert_action
        from app import action_storage
        from app.execution_owner_storage import create_plan_execution
        from app.run_storage import insert_run
        from app.task8_storage import register_task8_functions, task8_insert_parts
        from app.current_action_schema_release import source_objects
        from app.service_message_sender import agent_message_delivery_key
        from app.action_entity_transfer import capture_source_facts
        import re

        register_task8_functions(db)
        actions_sql = next(obj[3] for obj in source_objects("fresh_memory_fk")
                           if obj[0] == "table" and obj[1] == "actions")
        source_codes = {name: int(code) for code, name in re.findall(
            r"when (\d+) then '([^']+)'", actions_sql)}

        class SourceRecordTypes:
            def code(self, name):
                return source_codes[name]

        monkeypatch.setattr(action_storage, "ACTION_RECORD_TYPES", SourceRecordTypes())
        task_parts = task8_insert_parts("reply_tasks", {
            "channel": "dingtalk", "conversation_id": "conversation-sent",
            "conversation_title": "source", "single_chat": 0,
            "trigger_message_id": "trigger-sent", "trigger_create_time": "source",
            "trigger_sender": "sender", "status": "done",
            "business_object_key": "business-sent",
        })
        task_id = db.execute(
            f"insert into tasks({task_parts.columns}) values({task_parts.values}) returning id",
            task_parts.parameters,
        ).fetchone()[0]
        insert_run(db, "agent_runs", {
            "id": 911, "task_id": task_id, "execution_generation": "generation-sent",
            "role": "consumer",
        })
        insert_action(db, "external_action_results", {
            "external_action_key": "canonical-sent-effect",
            "business_object_key": "business-sent", "action_identity": "notify",
            "operation": "send_message", "target_identifiers_json": '{"channel":"group"}',
            "provider_result_json": '{"messageId":"canonical-body"}', "result_digest": "canonical-digest",
            "first_agent_run_id": 911, "created_at": "canonical-clock",
        })
        insert_action(db, "external_action_results", {
            "external_action_key": "local-equal-effect",
            "business_object_key": "business-sent", "action_identity": "notify_again",
            "operation": "send_message", "target_identifiers_json": '{"channel":"group"}',
            "provider_result_json": '{"messageId":"canonical-body"}', "result_digest": "local-digest",
            "first_agent_run_id": 911, "created_at": "local-canonical-clock",
        })
        candidate_delivery_key = agent_message_delivery_key(
            business_object_key="business-sent", action_identity="notify",
            execution_generation="generation-sent", proposal_revision=0,
        )
        insert_action(db, "review_candidates", {
            "id": 211, "task_id": task_id, "execution_generation": "generation-sent",
            "consumer_run_id": 911, "stage_index": 0, "proposal_revision": 0,
            "candidate_digest": "candidate-sent-digest",
            "candidate_json": '{"proposal":{"actions":[{"action_identity":"notify",'
                              '"operation":"send_message","target":{"channel":"group"}}]}}',
            "created_at": "candidate-sent-clock",
        }, execution_owner=True)
        insert_action(db, "candidate_reviews", {
            "id": 212, "candidate_id": 211, "audit_run_id": 911,
            "decision": "approve", "candidate_digest": "candidate-sent-digest",
            "result_json": "{}", "created_at": "review-sent-clock",
        })
        execution_id = create_plan_execution(db, candidate_id=211, review_id=212,
                                             selection_id=None, status="running",
                                             lease_owner="test", lease_expires_at="")
        insert_action(db, "candidate_action_attempts", {
            "id": 213, "execution_id": execution_id, "action_index": 0,
            "external_action_key": "canonical-sent-effect", "status": "verified",
            "result_json": None, "created_at": "candidate-attempt-clock",
            "updated_at": "candidate-attempt-updated",
        }, execution_owner=True)
        # Only an actual execution bound to its matching approved review may
        # supply candidate identity to a prepared delivery owner.
        unselected_delivery_key = agent_message_delivery_key(
            business_object_key="business-sent", action_identity="unselected-plan:unused",
            execution_generation="generation-unselected", proposal_revision=0,
        )
        unapproved_delivery_key = agent_message_delivery_key(
            business_object_key="business-sent", action_identity="unapproved-plan",
            execution_generation="generation-unapproved", proposal_revision=0,
        )
        approved_prepared_delivery_key = agent_message_delivery_key(
            business_object_key="business-sent", action_identity="approved-prepared-only:chosen",
            execution_generation="generation-approved-prepared", proposal_revision=0,
        )
        for candidate_id, decision, generation, candidate_json in (
            (214, "approve", "generation-unselected",
             '{"proposal":{"actions":[]},"decision_options":[{"key":"unused",'
             '"plan":{"actions":[{"action_identity":"unselected-plan",'
             '"operation":"send_message","target":{"channel":"group"}}]}}]}'),
            (215, "return", "generation-unapproved",
             '{"proposal":{"actions":[{"action_identity":"unapproved-plan",'
             '"operation":"send_message","target":{"channel":"group"}}]}}'),
            (216, "approve", "generation-approved-prepared",
             '{"proposal":{"actions":[]},"decision_options":[{"key":"chosen",'
             '"plan":{"actions":[{"action_identity":"approved-prepared-only",'
             '"operation":"send_message","target":{"channel":"group"}}]}}]}'),
        ):
            digest = f"candidate-{candidate_id}-digest"
            run_id = 910 + candidate_id
            insert_run(db, "agent_runs", {
                "id": run_id, "task_id": task_id,
                "execution_generation": generation, "role": "consumer",
            })
            insert_action(db, "review_candidates", {
                "id": candidate_id, "task_id": task_id,
                "execution_generation": generation, "consumer_run_id": run_id,
                "stage_index": 0, "proposal_revision": 0, "candidate_digest": digest,
                "candidate_json": candidate_json, "created_at": f"candidate-{candidate_id}-clock",
            }, execution_owner=True)
            review_id = candidate_id + 100
            insert_action(db, "candidate_reviews", {
                "id": review_id, "candidate_id": candidate_id, "audit_run_id": run_id,
                "decision": decision, "candidate_digest": digest,
                "result_json": "{}", "created_at": f"review-{candidate_id}-clock",
            })
            if candidate_id == 216:
                selected = insert_action(db, "candidate_selections", {
                    "candidate_id": candidate_id, "review_id": review_id,
                    "option_key": "chosen",
                }, selection_references=True)
                create_plan_execution(db, candidate_id=candidate_id, review_id=review_id,
                                      selection_id=selected.lastrowid, status="running", lease_owner="test",
                                      lease_expires_at="")
        insert_action(db, "outbound_postfixes", {
            "channel": "dingtalk", "delivery_key": "prepared-key",
            "final_body": "prepared-body", "feedback_token": "feedback",
            "postfix_version": "postfix-v1", "created_at": "prepared-clock",
        })
        insert_action(db, "outbound_postfixes", {
            "channel": "dingtalk", "delivery_key": candidate_delivery_key,
            "final_body": "prepared-canonical-body", "feedback_token": "feedback-canonical",
            "postfix_version": "postfix-v2", "created_at": "prepared-canonical-clock",
        })
        insert_action(db, "outbound_postfixes", {
            "channel": "dingtalk", "delivery_key": "dispatch-only-key",
            "final_body": "dispatch-only-body", "feedback_token": "dispatch-feedback",
            "postfix_version": "postfix-v3", "created_at": "dispatch-only-prepared-clock",
        })
        for delivery_key, label in (
            (unselected_delivery_key, "unselected"),
            (unapproved_delivery_key, "unapproved"),
            (approved_prepared_delivery_key, "approved-prepared-only"),
        ):
            insert_action(db, "outbound_postfixes", {
                "channel": "dingtalk", "delivery_key": delivery_key,
                "final_body": f"{label}-body", "feedback_token": f"{label}-feedback",
                "postfix_version": f"{label}-version", "created_at": f"{label}-clock",
            })
        db.execute("update actions set postfix_receipt_provider_result_json=?,postfix_receipt_created_at=?,"
                   "postfix_dispatch_created_at=? where record_type='outbound_postfixes' and delivery_key=?",
                   ('{"accepted":true}', "provider-clock", "dispatch-clock", "prepared-key"))
        db.execute("update actions set postfix_receipt_provider_result_json=?,postfix_receipt_created_at=?,"
                   "postfix_dispatch_created_at=? where record_type='outbound_postfixes' and delivery_key=?",
                   ('{"accepted":true,"canonical":true}', "canonical-provider-clock",
                    "canonical-dispatch-clock", candidate_delivery_key))
        db.execute("update actions set postfix_dispatch_created_at=? where record_type='outbound_postfixes' "
                   "and delivery_key='dispatch-only-key'", ("dispatch-only-clock",))
        sent_ids = []
        for source_id, external_key, local_result in (
            (301, "", '{"local":"empty-key"}'),
            (302, "orphan-effect", '{"local":"orphan"}'),
            (303, "canonical-sent-effect", None),
            (304, "local-equal-effect", '{"messageId":"local-body"}'),
        ):
            sent_ids.append(insert_action(db, "sent_replies", {
                "id": source_id, "conversation_id": f"conversation-{source_id}",
                "trigger_message_id": f"trigger-{source_id}",
                "reply_text": "prepared-canonical-body" if source_id in (303, 304) else f"sent-body-{source_id}",
                "sent_at": f"sent-clock-{source_id}", "send_result_json": local_result,
                "channel": "dingtalk", "agent_run_id": 911,
                "external_action_key": external_key,
            }).lastrowid)
        insert_action(db, "sent_reply_observers", {
            "sent_reply_id": sent_ids[2], "agent_run_id": 911,
            "reply_task_id": task_id, "created_at": "observer-clock",
        })
        db.commit()

    with closing(sqlite3.connect(path)) as db:
        source_sent = {row["id"]: row for row in capture_source_facts(db)["sent_replies"]}
        assert source_sent[303]["send_result_json"] is None
        assert source_sent[304]["send_result_json"] == '{"messageId":"local-body"}'

    contract = _prepare(path)
    release.publish_action_entity_release(path, contract)
    with closing(sqlite3.connect(path)) as db:
        prepared = db.execute("select id,action_prepared_body,action_prepared_receipt_id from actions "
                              "where record_type='action' and action_delivery_key='prepared-key'").fetchone()
        assert prepared[1] == "prepared-body"
        prepared_receipt = db.execute("select provider_result_json,observed_at,dispatched_at from action_receipts where id=?",
                                      (prepared[2],)).fetchone()
        assert prepared_receipt == ('{"accepted":true}', "provider-clock", "dispatch-clock")
        dispatch_only = db.execute("select id,action_prepared_receipt_id from actions where record_type='action' "
                                   "and action_delivery_key='dispatch-only-key'").fetchone()
        assert dispatch_only[1] is None
        assert db.execute("select dispatched_at,provider_result_json from action_receipts where action_id=?",
                          (dispatch_only[0],)).fetchone() == ("dispatch-only-clock", None)
        prepared_only = db.execute(
            "select id,action_business_object_key,action_action_identity,action_operation "
            "from actions where record_type='action' and action_delivery_key=?",
            (approved_prepared_delivery_key,),
        ).fetchone()
        assert prepared_only[1:] == ("business-sent", "approved-prepared-only:chosen", None)
        for delivery_key in (unselected_delivery_key, unapproved_delivery_key):
            unproven = db.execute(
                "select action_business_object_key,action_action_identity,action_operation "
                "from actions where record_type='action' and action_delivery_key=?",
                (delivery_key,),
            ).fetchone()
            assert unproven == (None, None, None)
        canonical_id = db.execute("select action_id from action_entity_identity_map "
                                   "where source_type='external_action_results'").fetchone()[0]
        sent_maps = {json.loads(source_identity)[1]: (action_id, receipt_id)
                     for source_identity, action_id, receipt_id in db.execute(
                         "select source_identity,action_id,receipt_id from action_entity_identity_map "
                         "where source_type='sent_replies'")}
        assert len(sent_maps) == 4
        assert sent_maps[301][0] != sent_maps[302][0]
        assert sent_maps[303][0] == canonical_id
        assert sent_maps[304][0] != canonical_id
        canonical_receipt_id = db.execute("select id from action_receipts where action_id=? and operation='send_message'",
                                          (canonical_id,)).fetchone()[0]
        assert db.execute("select provider_result_json,provider_receipt_id,observed_at from action_receipts "
                          "where id=?", (sent_maps[303][1],)).fetchone() == (
            None, canonical_receipt_id, "sent-clock-303")
        assert db.execute("select provider_result_json,provider_receipt_id,observed_at from action_receipts "
                          "where id=?", (sent_maps[304][1],)).fetchone() == (
            '{"messageId":"local-body"}', None, "sent-clock-304")
        canonical_prepared = db.execute("select action_prepared_body,action_prepared_receipt_id "
                                        "from actions where id=?", (canonical_id,)).fetchone()
        assert canonical_prepared[0] == "prepared-canonical-body"
        assert db.execute("select provider_result_json,observed_at,dispatched_at from action_receipts "
                          "where id=?", (canonical_prepared[1],)).fetchone() == (
            '{"accepted":true,"canonical":true}', "canonical-provider-clock",
            "canonical-dispatch-clock")
        expected_aliases = {
            "candidate_action_attempts", "external_action_results", "outbound_postfixes",
            "outbound_postfix_receipts", "native_reply_dispatches", "sent_replies",
            "sent_reply_observers",
        }
        aliases = db.execute("select source_type,action_id from action_entity_identity_map").fetchall()
        canonical_aliases = {source_type for source_type, owner in aliases if owner == canonical_id}
        assert expected_aliases <= canonical_aliases
        assert db.execute("select agent_run_id,task_id,created_at from action_observers").fetchall() == [
            (911, task_id, "observer-clock")]


def test_direct_email_translation_preserves_attempt_identity_and_rewrites_history_ids(tmp_path, monkeypatch):
    path = tmp_path / "email-translation.sqlite3"
    _source_database(path)
    with closing(sqlite3.connect(path)) as db:
        from app import action_storage
        from app.action_storage import insert_action, _action_payload
        from app.action_email_history_storage import insert_email_history
        from app.current_action_schema import current_action_payloads
        from app.current_action_schema_release import source_objects
        from app.action_entity_transfer import capture_source_facts
        import re

        source_sql = next(sql for kind, name, _table, sql in source_objects("fresh_memory_fk")
                          if kind == "table" and name == "actions")
        source_codes = {name: int(code) for code, name in re.findall(
            r"when (\d+) then '([^']+)'", source_sql)}

        class SourceRecordTypes:
            def code(self, name):
                return source_codes[name]

        monkeypatch.setattr(action_storage, "ACTION_RECORD_TYPES", SourceRecordTypes())
        db.execute("""insert into emails(
            classification_id,message_id,account_id,stable_message_identity,uidvalidity,uid,
            class_model_text,class_confidence,class_margin,class_probabilities_json,
            class_model_id,class_config_version,class_status,class_classification_source,
            class_agent_result_json,class_legacy_processed_without_plan,class_confirmed_at,
            class_created_at,class_updated_at,message_folder,message_rfc_message_id,
            message_thread_identity,message_sender,message_recipients_json,message_subject,
            message_normalized_text,message_preview,message_attachment_metadata_json,
            message_received_at,message_created_at,message_updated_at
        ) values(1,1,'account-1','stable-1',1,1,'model text',0.5,0.1,'{}',
            'model-1','config-v1','processed','model','{}',0,'confirmed','created','updated',
            'INBOX','<message-1>','thread-1','sender-1','[]','subject','body','preview','[]',
            'received','created','updated')""")
        for index, plan_id in enumerate(("plan-1", "plan-2"), 1):
            plan = {
                "action_plan_id": plan_id, "action_plan_version": index,
                "classification_id": 1, "account_id": "account-1", "category": "work",
                "classification_source": "model", "confidence": 0.5, "model_id": "model-1",
                "config_version": "config-v1", "actions_json": "[]",
                "action_parameters_json": "{}", "legacy_serialization_pre_v16": 0,
                "created_at": f"plan-created-{index}",
            }
            db.execute(
                "insert into actions(_kind,legacy_key,payload_json) values(?,?,?)",
                (source_codes["email_action_plans"], str(index), _action_payload(
                    db, "email_action_plans", plan,
                    layout=current_action_payloads().layouts["email_action_plans"])),
            )
        direct_actions = (
            ("old-direct-action", "plan-1", "archive", "first-result"),
            ("second-direct-action", "plan-2", "label", "second-result"),
        )
        for position, (action_id, plan_id, action_type, result_id) in enumerate(direct_actions, 1):
            direct = {
                "action_id": action_id, "action_plan_id": plan_id,
                "classification_id": 1, "account_id": "account-1", "action_type": action_type,
                    "parameters_json": "{}", "config_version": "config-v1", "status": "pending",
                    "started_at": "", "next_attempt_at": "",
                    "created_at": f"created-{position}", "updated_at": f"updated-{position}",
            }
            db.execute(
                "insert into actions(_kind,legacy_key,payload_json) values(?,?,?)",
                (source_codes["email_actions"], str(22 + position), _action_payload(
                    db, "email_actions", direct,
                    layout=current_action_payloads().layouts["email_actions"])),
            )
            insert_action(db, "email_action_attempts", {
                "id": 6 + position, "action_id": action_id, "attempt_number": position,
                "status": "done", "provider_operation": action_type.upper(),
                "provider_target": f"mailbox:message-{position}", "provider_result_id": result_id,
                "error": "", "started_at": f"attempt-start-{position}",
                "finished_at": f"attempt-finish-{position}",
            })
        insert_email_history(db, "email_historical_operations", {
            "operation_id": "operation-1", "account_id": "account-1",
            "stable_message_identity": "message-identity", "model_id": "model-1",
            "classification_id": 1, "action_plan_id": "plan-1",
            "action_ids_json": '["old-direct-action","second-direct-action"]',
            "predicted_category": "work", "threshold": 0.4, "probability": 0.8,
            "important": 0, "status": "processing", "action_outcome": "",
            "created_at": "history-created", "updated_at": "history-updated",
        })
        db.commit()

    with closing(sqlite3.connect(path)) as db:
        source_facts = capture_source_facts(db)
        assert len(source_facts["email_actions"]) == 2
        assert len(source_facts["email_action_attempts"]) == 2
        assert source_facts["email_historical_operations"][0]["action_ids_json"] == (
            '["old-direct-action","second-direct-action"]')

    contract = _prepare(path)
    release.publish_action_entity_release(path, contract)
    with closing(sqlite3.connect(path)) as db:
        maps = {
            identity: action_id for identity, action_id in db.execute(
                "select source_identity,action_id from action_entity_identity_map "
                "where source_type='email_actions'")
        }
        action_ids = [maps[json.dumps(["text", source_id], separators=(",", ":"))]
                      for source_id in ("old-direct-action", "second-direct-action")]
        assert db.execute("select action_id,attempt_number,status,provider_result_id from action_receipts "
                          "where action_id in (?,?) and attempt_number is not null order by id",
                          action_ids).fetchall() == [
            (action_ids[0], 1, "done", "first-result"),
            (action_ids[1], 2, "done", "second-result"),
        ]
        history = db.execute(
            "select email_operation_action_ids_json from actions "
            "where record_type='email_historical_operations'"
        ).fetchone()
        assert history == (json.dumps([str(value) for value in action_ids], separators=(",", ":")),)


def test_direct_retry_parent_retargets_only_the_covered_action_reference(tmp_path):
    path = tmp_path / "email-retry-parent.sqlite3"
    _source_database(path)
    with closing(sqlite3.connect(path)) as db:
        from app.action_storage import _action_payload, _encoded_native_sqlite_value
        from app.current_action_schema import current_action_payloads
        from app.action_email_definitions import EMAIL_COMPOSITE_KEYS
        from app.action_email_definitions import EMAIL_ACTION_PROJECTIONS
        import re
        from app.current_action_schema_release import source_objects

        source_sql = next(
            sql for kind, name, _table, sql in source_objects("fresh_memory_fk")
            if kind == "table" and name == "actions"
        )
        source_codes = {name: int(code) for code, name in re.findall(
            r"when (\d+) then '([^']+)'", source_sql
        )}

        direct = {
            "action_id": "legacy-direct-action-key", "action_plan_id": "plan-1",
            "classification_id": 1, "account_id": "account-1", "action_type": "archive",
            "parameters_json": "{}", "config_version": "config-v1", "status": "pending",
            "started_at": "", "next_attempt_at": "",
            "created_at": "direct-created", "updated_at": "direct-updated",
        }
        # The frozen source FK points both the action and its plan at a real
        # email classification.  Seed the smallest valid parent row so this
        # fixture tests only the retry reference being migrated.
        db.execute("""insert into emails(
            classification_id,message_id,account_id,stable_message_identity,uidvalidity,uid,
            class_model_text,class_confidence,class_margin,class_probabilities_json,
            class_model_id,class_config_version,class_status,class_classification_source,
            class_agent_result_json,class_legacy_processed_without_plan,class_confirmed_at,
            class_created_at,class_updated_at,message_folder,message_rfc_message_id,
            message_thread_identity,message_sender,message_recipients_json,message_subject,
            message_normalized_text,message_preview,message_attachment_metadata_json,
            message_received_at,message_created_at,message_updated_at
        ) values(1,1,'account-1','stable-1',1,1,'model text',0.5,0.1,'{}',
            'model-1','config-v1','processed','model','{}',0,'confirmed','created','updated',
            'INBOX','<message-1>','thread-1','sender-1','[]','subject','body','preview','[]',
            'received','created','updated')""")
        plan = {
            "action_plan_id": "plan-1", "action_plan_version": 1,
            "classification_id": 1, "account_id": "account-1", "category": "work",
            "classification_source": "model", "confidence": 0.5, "model_id": "model-1",
            "config_version": "config-v1", "actions_json": "[]",
            "action_parameters_json": "{}", "legacy_serialization_pre_v16": 0,
            "created_at": "plan-created",
        }
        db.execute(
            "insert into actions(_kind,legacy_key,payload_json) values(?,?,?)",
            (source_codes["email_action_plans"], "1", _action_payload(
                db, "email_action_plans", plan,
                layout=current_action_payloads().layouts["email_action_plans"])),
        )
        db.execute(
            "insert into actions(_kind,legacy_key,payload_json) values(?,?,?)",
            (source_codes["email_actions"], "23", _action_payload(
                db, "email_actions", direct, layout=current_action_payloads().layouts["email_actions"])),
        )
        retry = {
            "action_id": direct["action_id"], "attempt_number": 2,
            "source_readback_digest": "a" * 64,
            "account_configuration_snapshot": '[null,null,null,null,null,null,null,null,null]',
            "requested_at": "retry-requested",
        }
        retry_key = json.dumps([
            _encoded_native_sqlite_value(retry[field])
            for field in EMAIL_COMPOSITE_KEYS["email_direct_retry_requests"]
        ], separators=(",", ":"))
        db.execute(
            "insert into actions(_kind,legacy_key,payload_json) values(?,?,?)",
            (source_codes["email_direct_retry_requests"], retry_key,
             _action_payload(db, "email_direct_retry_requests",
                             {key: value for key, value in retry.items()
                              if key not in EMAIL_COMPOSITE_KEYS["email_direct_retry_requests"]},
                             layout=current_action_payloads().layouts["email_direct_retry_requests"])),
        )
        projection = EMAIL_ACTION_PROJECTIONS["email_direct_retry_requests"]
        before_cursor = db.execute(projection)
        before = [dict(zip((column[0] for column in before_cursor.description), row, strict=True))
                  for row in before_cursor.fetchall()]
        assert len(before) == 1 and before[0]["action_id"] == direct["action_id"]
        db.commit()
    contract = _prepare(path)
    release.publish_action_entity_release(path, contract)
    with closing(sqlite3.connect(path)) as db:
        from app.action_entity_transfer import _identity
        from app.action_storage import insert_action
        from app.action_email_definitions import EMAIL_ACTION_PROJECTIONS

        cursor = db.execute(EMAIL_ACTION_PROJECTIONS["email_direct_retry_requests"])
        after = [dict(zip((column[0] for column in cursor.description), row, strict=True))
                 for row in cursor.fetchall()]
        mappings = db.execute(
            "select source_type,source_identity,action_id from action_entity_identity_map order by source_type,source_identity"
        ).fetchall()
        owner = next((row[2] for row in mappings
                      if row[0] == "email_actions" and row[1] == _identity("legacy-direct-action-key")), None)
        assert owner is not None, mappings
        owner_id = owner
        assert len(after) == 1 and after[0]["action_id"] == str(owner_id)
        assert {key: value for key, value in after[0].items() if key != "action_id"} == {
            key: value for key, value in before[0].items() if key != "action_id"
        }
        assert db.execute(
            "select email_operation_parent_type,email_action_id from actions "
            "where record_type='email_direct_retry_requests'"
        ).fetchone() == ("action", str(owner_id))
        assert db.execute("pragma foreign_key_check").fetchall() == list(contract["source_fk_findings"])

        # The normal current storage writer uses the physical common Action ID.
        current = retry | {"action_id": str(owner_id), "attempt_number": 3,
                           "requested_at": "current-retry-requested"}
        insert_action(db, "email_direct_retry_requests", current)
        assert db.execute(
            "select count(*) from actions where record_type='email_direct_retry_requests' "
            "and email_action_id=? and email_operation_parent_type='action'",
            (str(owner_id),),
        ).fetchone() == (2,)


def test_conflicting_identity_aborts_without_mutation(tmp_path, monkeypatch):
    path = tmp_path / "conflict.sqlite3"
    _source_database(path)
    with closing(sqlite3.connect(path)) as db:
        from app.action_storage import insert_action
        from app import action_storage
        from app.execution_owner_storage import create_plan_execution
        from app.run_storage import insert_run
        from app.task8_storage import register_task8_functions, task8_insert_parts
        import re
        from app.current_action_schema_release import source_objects

        register_task8_functions(db)
        actions_sql = next(obj[3] for obj in source_objects("fresh_memory_fk")
                           if obj[0] == "table" and obj[1] == "actions")
        source_codes = {name: int(code) for code, name in re.findall(
            r"when (\d+) then '([^']+)'", actions_sql)}
        class SourceRecordTypes:
            def code(self, name):
                return source_codes[name]

        monkeypatch.setattr(action_storage, "ACTION_RECORD_TYPES", SourceRecordTypes())
        task_parts = task8_insert_parts("reply_tasks", {
            "channel": "dingtalk", "conversation_id": "conflict-conversation",
            "conversation_title": "source", "single_chat": 0,
            "trigger_message_id": "conflict-message", "trigger_create_time": "source",
            "trigger_sender": "sender", "status": "done",
            "business_object_key": "business-conflict",
        })
        task_id = db.execute(
            f"insert into tasks({task_parts.columns}) values({task_parts.values}) returning id",
            task_parts.parameters,
        ).fetchone()[0]
        insert_run(db, "agent_runs", {
            "id": 902, "task_id": task_id,
            "execution_generation": "generation-conflict", "role": "consumer",
        })
        insert_action(db, "external_action_results", {
            "external_action_key": "conflicting-effect",
            "business_object_key": "business-conflict", "action_identity": "notify",
            "operation": "send_message", "target_identifiers_json": '{"user_id":"expected"}',
            "provider_result_json": '{"sent":true}', "result_digest": "digest",
            "first_agent_run_id": 902, "created_at": "source-time",
        })
        insert_action(db, "review_candidates", {
            "id": 71, "task_id": task_id, "execution_generation": "generation-conflict",
            "consumer_run_id": 902, "stage_index": 0, "proposal_revision": 0,
            "candidate_digest": "candidate-digest",
            "candidate_json": '{"proposal":{"actions":[{"action_identity":"notify",'
                              '"operation":"send_message","target":{"user_id":"different"}}]}}',
            "created_at": "candidate-time",
        }, execution_owner=True)
        insert_action(db, "candidate_reviews", {
            "id": 72, "candidate_id": 71, "audit_run_id": 902,
            "decision": "approve", "candidate_digest": "candidate-digest",
            "result_json": "{}", "created_at": "review-time",
        })
        create_plan_execution(db, candidate_id=71, review_id=72, selection_id=None,
                              status="running", lease_owner="test", lease_expires_at="")
        insert_action(db, "candidate_action_attempts", {
            "id": 91, "execution_id": 1, "action_index": 0,
            "external_action_key": "conflicting-effect", "status": "verified",
            "result_json": None, "created_at": "attempt-time", "updated_at": "attempt-time",
        }, execution_owner=True)
        db.commit()
    contract = _prepare(path)
    before = _snapshot(path)
    with pytest.raises(ValueError, match="conflict|identity"):
        release.publish_action_entity_release(path, contract)
    assert _snapshot(path) == before


def test_new_action_and_recall_survive_inverse(tmp_path):
    path = tmp_path / "current-facts.sqlite3"
    _source_database(path)
    contract = _prepare(path)
    release.publish_action_entity_release(path, contract)
    with closing(sqlite3.connect(path)) as db:
        from app.action_entity_storage import create_action, append_action_receipt, add_action_observer

        action = create_action(db, values={"operation": "send_reply", "status": "done",
                                           "prepared_body": "native task output"})
        receipt = append_action_receipt(db, action["id"], values={
            "operation": "send_reply", "status": "done", "provider_result_json": '{"id":"provider-1"}',
            "provider_result_id": "provider-1", "observed_at": "provider receipt clock",
        })
        add_action_observer(db, action["id"], agent_run_id=901, task_id=701, created_at="observer clock")
        db.execute("update actions set action_data=jsonb_set(action_data,'$.recall_status',json('" + '"recalled"' + "')) where id=?",
                   (action["id"],))
        db.commit()
    release.invert_action_entity_release(path, contract)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("select action_receipt_id from actions where id=?", (action["id"],)).fetchone() == (receipt,)
        assert db.execute("select recall_status from actions where id=?", (action["id"],)).fetchone() == ("recalled",)
        assert db.execute("select count(*) from action_observers where action_id=?", (action["id"],)).fetchone() == (1,)


def test_inverse_does_not_restore_source_snapshot(tmp_path):
    path = tmp_path / "current-data.sqlite3"
    _source_database(path)
    contract = _prepare(path)
    release.publish_action_entity_release(path, contract)
    release.invert_action_entity_release(path, contract)
    assert _snapshot(path)["marker"] == (SOURCE_VERSION, SOURCE_MARKER_TIME)


def test_reforward_has_no_duplicate_effect(tmp_path):
    path = tmp_path / "reforward.sqlite3"
    _source_database(path)
    contract = _prepare(path)
    release.publish_action_entity_release(path, contract)
    release.invert_action_entity_release(path, contract)
    second = _prepare(path)
    release.publish_action_entity_release(path, second)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("select count(*) from actions where record_type='action'").fetchone() == (0,)


def test_unsupported_source_rejected_before_write(tmp_path):
    path = tmp_path / "unsupported.sqlite3"
    _source_database(path)
    with closing(sqlite3.connect(path)) as db:
        from app.state_storage import update_state_rows
        db.execute("begin")
        update_state_rows(db, "service_state", {"value": "unknown"}, where_sql="key='store_schema_version'")
        db.commit()
    before = _snapshot(path)
    with pytest.raises(ValueError, match="source|schema|version"):
        release.prepare_action_entity_release(path)
    assert _snapshot(path) == before
