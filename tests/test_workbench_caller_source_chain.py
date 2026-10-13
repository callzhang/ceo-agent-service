"""A historical caller fixture crosses each verified Store schema boundary."""

import sqlite3

from app.action_storage import ACTION_PROJECTIONS, action_schema_is_current
from app.run_storage import insert_run
from app.store import AutoReplyStore
from app.task_storage import insert_task
from tests.test_action_receipt_source import insert_source, source_values
from tests.workbench_caller_fixtures import empty_source13


def test_source13_fixture_keeps_original_actions_receipts_and_watermark(tmp_path):
    path = tmp_path / "historical.db"
    with empty_source13(path) as db:
        insert_task(db, "reply_task_inputs", dict(
            id=2, parent_id=1, channel="dingtalk", conversation_id="chat",
            conversation_title="title", single_chat=0, trigger_message_id="message",
            trigger_create_time="time", trigger_sender="source", trigger_text="raw input",
            business_object_key="business",
        ))
        insert_task(db, "reply_tasks", dict(
            id=1, current_input_id=2, conversation_id="chat", conversation_title="title",
            single_chat=0, trigger_message_id="message", trigger_create_time="time",
            trigger_sender="source",
        ))
        insert_run(db, "agent_runs", dict(
            id=1, task_id=1, execution_generation="generation", role="consumer",
        ))
        original_candidate = (
            7, 1, "generation", 1, 0, 1, None, "digest", " not JSON, retained ",
            "", "", "source time",
        )
        db.execute("insert into review_candidates values(?,?,?,?,?,?,?,?,?,?,?,?)", original_candidate)
        insert_source(db, "external_action_results", source_values(
            "external_action_results", token="001",
        ))
        insert_source(db, "agent_effect_intents", {
            **source_values("agent_effect_intents", token="intent"), "id": 9,
        })
        insert_source(db, "agent_execution_receipts", {
            **source_values("agent_execution_receipts", token="receipt"), "id": 11,
        })
        insert_source(db, "agent_execution_receipts", {
            **source_values("agent_execution_receipts", token="deleted"), "id": 50,
        })
        db.execute("delete from agent_execution_receipts where id=50")
        original_receipts = {
            name: db.execute(f"select * from {name}").fetchall()
            for name in ("external_action_results", "agent_effect_intents", "agent_execution_receipts")
        }

    with sqlite3.connect(path) as db:
        assert db.execute("select value from service_state where key='store_schema_version'").fetchone() == ("2026-10-13.2",)
        assert action_schema_is_current(db, include_receipts=True)
        assert db.execute(ACTION_PROJECTIONS["review_candidates"]).fetchone() == original_candidate
        assert {name: db.execute(ACTION_PROJECTIONS[name]).fetchall() for name in original_receipts} == original_receipts
        assert db.execute("select seq from sqlite_sequence where name='actions:agent_execution_receipts'").fetchone() == (50,)
        original_physical = db.execute("select id,legacy_key,payload_json from actions where record_type='review_candidates'").fetchone()

    AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute("select value from service_state where key='store_schema_version'").fetchone() == ("2026-10-13.3",)
        assert action_schema_is_current(db, include_postfix=True)
        assert db.execute(ACTION_PROJECTIONS["review_candidates"]).fetchone() == original_candidate
        assert db.execute("select id,legacy_key,payload_json from actions where record_type='review_candidates'").fetchone() == original_physical
        for name, expected in original_receipts.items():
            assert db.execute(ACTION_PROJECTIONS[name]).fetchall() == expected
        assert db.execute("select seq from sqlite_sequence where name='actions:agent_execution_receipts'").fetchone() == (50,)
