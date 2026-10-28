"""Frozen Action and Task fixtures for historical schema release tests."""

import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from app.business_source_reader import SourceVersionBinding
from app.email_canonical_storage import _action_objects
from app.provenance_storage import create_provenance_schema
from app.task_source_documents import source_document_key


@pytest.fixture(params=("actual_no_memory_fk", "fresh_memory_fk"))
def db(request):
    with sqlite3.connect(":memory:") as connection:
        config_objects = json.loads((Path(__file__).parent / "fixtures/store26_configs.json").read_text())
        CONFIG_TABLE_DDL = next(item[3] for item in config_objects if item[0] == "table")
        CONFIG_INDEX_DDL = tuple(item[3] for item in config_objects if item[0] == "index")
        from app.task20_definitions import TASK20_TABLE_DDL, TASK20_INDEX_DDL
        from app.runs11_current_schema import current_run11_objects
        from app.business_definitions import _definitions
        from app.email_canonical_definitions import EMAILS_TABLE_SQL, EMAILS_INDEX_SQL
        from app.schedules_definitions import SCHEDULE_DDL

        connection.execute(SCHEDULE_DDL)
        for table, indexes in ((CONFIG_TABLE_DDL, CONFIG_INDEX_DDL),
                               (TASK20_TABLE_DDL, TASK20_INDEX_DDL),
                               (EMAILS_TABLE_SQL, EMAILS_INDEX_SQL)):
            connection.execute(table)
            for sql in indexes:
                connection.execute(sql)
        for sql in current_run11_objects().values():
            connection.execute(sql)
        business_table, business_indexes, _ = _definitions(
            include_candidates=True, include_business_actions=True,
        )
        connection.execute(business_table)
        for sql in business_indexes:
            connection.execute(sql)
        create_provenance_schema(connection)
        objects = _action_objects(
            request.param, target=True, inline_legacy_owner=True,
            physical_provenance=True, compact_payload=True,
        )
        for category in ("table", "index", "trigger"):
            for obj in objects:
                if obj["type"] == category:
                    connection.execute(obj["sql"])
        connection.execute("begin")
        yield connection


@pytest.fixture
def binding():
    source = dict(
        source_type="ai_minutes",
        source_ref="source-1#todos-sha256=" + "a" * 64,
        source_time="clock",
        conversation_id="chat",
        author_user_id="author",
        author_name="Author",
        author_kind="human",
    )
    body = "source α"
    return SourceVersionBinding(
        **source,
        identity_key=source_document_key(**source, evidence_text=body),
        body_sha256=hashlib.sha256(body.encode()).hexdigest(),
        body_utf8_bytes=len(body.encode()),
        provider_locator_json=(
            '{"kind":"ai_minutes","minutes_id":"source-1",'
            '"components":["meeting","todos"]}'
        ),
    )


def seed_historical_task(db):
    from app import action_storage as actions
    from app.run_storage import insert_run
    from app.storage_payload import TASK_RECORD_TYPES
    from app.task20_definitions import TASK20_DEFAULTS, TASK20_LAYOUTS
    from app.task8_storage import register_task8_functions

    register_task8_functions(db)
    kind = "reply_tasks"
    values = dict(conversation_id="chat", conversation_title="title", single_chat=0,
                  trigger_message_id="message", trigger_create_time="time", trigger_sender="author")
    for field in TASK20_LAYOUTS[kind].fields:
        if field not in values:
            expression = TASK20_DEFAULTS[kind].get(field, "null")
            values[field] = db.execute("select " + expression).fetchone()[0]
    payload = TASK20_LAYOUTS[kind].pack(
        values, scalar_codec=actions._encoded_native_sqlite_value,
    )
    db.execute(
        "insert into tasks(_kind,legacy_key,payload_json) values(?,?,jsonb(?))",
        (TASK_RECORD_TYPES.code(kind), "1", payload),
    )
    insert_run(db, "agent_runs", dict(
        id=1, task_id=1, execution_generation="generation", role="consumer",
    ))


def insert_historical_reply(db, values):
    from app import action_storage as actions

    kind = "reply_attempts"
    now = db.execute("select current_timestamp").fetchone()[0]
    layout = actions.action_payloads(
        actions._reply_variant_for(db), physical_provenance=True,
    ).layouts[kind]
    data = dict.fromkeys(layout.fields)
    data.update({field: value for field, value in (
        actions._ATTEMPT_DEFAULTS | {"created_at": now, "updated_at": now} | values
    ).items() if field in data})
    payload = layout.pack(data, scalar_codec=actions._encoded_native_sqlite_value)
    db.execute(
        "insert into actions(_kind,legacy_key,payload_json) values(?,?,jsonb(?))",
        (actions.ACTION_RECORD_TYPES.code(kind), "1", payload),
    )
