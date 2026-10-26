"""Frozen pre-parent-only Signal fixture for historical Action release tests."""

import hashlib
import sqlite3

import pytest

from app.business_source_reader import SourceVersionBinding
from app.email_canonical_storage import _action_objects
from app.provenance_storage import create_provenance_schema
from app.task_source_documents import source_document_key


@pytest.fixture(params=("actual_no_memory_fk", "fresh_memory_fk"))
def db(request):
    with sqlite3.connect(":memory:") as connection:
        from app.configs_definitions import CONFIG_TABLE_DDL
        from app.configs_definitions import CONFIG_INDEX_DDL
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
