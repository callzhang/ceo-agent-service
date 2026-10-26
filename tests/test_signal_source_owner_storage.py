"""Current Signal rows reference one immutable provenance source owner."""

from dataclasses import replace
import hashlib
import json
import sqlite3

import pytest

from app import current_action_schema
from app.business_source_reader import SourceVersionBinding
from app.current_action_schema import current_action_payloads
from app.provenance_definitions import SIGNAL_FACT_FIELDS, SIGNAL_FIELDS, SOURCE_FIELDS
from app.provenance_storage import insert_signal_version, signal_binding, signals_for_source
from app.store import AutoReplyStore
from app.task_source_documents import source_document_key
from app.task_run_entity_assembly import _action_objects as assembled_action_objects


@pytest.fixture
def binding():
    source = {
        "source_type": "ai_minutes",
        "source_ref": "source-1#todos-sha256=" + "a" * 64,
        "source_time": "2026-10-25T12:00:00+00:00",
        "conversation_id": "chat",
        "author_user_id": "author",
        "author_name": "Author",
        "author_kind": "human",
    }
    body = "source alpha"
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


@pytest.fixture
def current_db(tmp_path):
    store = AutoReplyStore(tmp_path / "signal-source-owner.sqlite3")
    with store._connect() as db:
        db.execute("begin")
        yield db
        db.rollback()


def _payload(db, public_id):
    raw = db.execute(
        "select json(payload_json) from actions where business_signal_public_id=?",
        (public_id,),
    ).fetchone()[0]
    return current_action_payloads().layouts["business_task_signals"].unpack(raw)


def test_current_signal_layout_is_reference_plus_four_own_facts():
    assert SIGNAL_FIELDS == (*SOURCE_FIELDS, "provenance_id", *SIGNAL_FACT_FIELDS)
    assert current_action_payloads().layouts["business_task_signals"].fields == (
        "provenance_id",
        *SIGNAL_FACT_FIELDS,
    )


def test_first_signal_creates_one_parent_and_stores_only_reference(current_db, binding):
    signal_id = insert_signal_version(
        current_db,
        binding,
        dedupe_key="signal:first",
        conversation_title="First",
        created_at="2026-10-25T12:01:00+00:00",
    )

    assert current_db.execute("select count(*) from provenance").fetchone()[0] == 1
    assert _payload(current_db, signal_id) == {
        "provenance_id": 1,
        "conversation_title": "First",
        "context_json": "{}",
        "dedupe_key": "signal:first",
        "created_at": "2026-10-25T12:01:00+00:00",
    }
    assert signal_binding(current_db, signal_id) == binding


def test_second_signal_does_not_edit_first_and_reuses_same_binding(current_db, binding):
    first = insert_signal_version(current_db, binding, dedupe_key="signal:first")
    before = current_db.execute(
        "select payload_json from actions where business_signal_public_id=?", (first,)
    ).fetchone()[0]

    second = insert_signal_version(
        current_db,
        replace(binding, provider_locator_json=None),
        dedupe_key="signal:second",
    )

    after = current_db.execute(
        "select payload_json from actions where business_signal_public_id=?", (first,)
    ).fetchone()[0]
    assert after == before
    assert current_db.execute("select count(*) from provenance").fetchone()[0] == 1
    assert signal_binding(current_db, first) == binding
    assert signal_binding(current_db, second) == binding


def test_invalid_signal_facts_roll_back_new_parent_inside_caller_transaction(current_db, binding):
    with pytest.raises(sqlite3.IntegrityError):
        insert_signal_version(
            current_db,
            binding,
            dedupe_key="signal:invalid",
            context_json="not-json",
        )

    assert current_db.in_transaction
    assert current_db.execute("select count(*) from provenance").fetchone()[0] == 0
    assert current_db.execute(
        "select count(*) from actions where record_type='business_task_signals'"
    ).fetchone()[0] == 0


def test_conflicting_logical_source_facts_leave_existing_parent_and_signal_unchanged(
    current_db, binding,
):
    first = insert_signal_version(current_db, binding, dedupe_key="signal:first")
    parent_before = tuple(current_db.execute(
        "select * from provenance where identity_key=?", (binding.identity_key,)
    ).fetchone())
    signal_before = current_db.execute(
        "select payload_json from actions where business_signal_public_id=?", (first,)
    ).fetchone()[0]

    with pytest.raises(ValueError, match="different source facts"):
        insert_signal_version(
            current_db,
            replace(binding, source_time="2026-10-25T13:00:00+00:00"),
            dedupe_key="signal:conflict",
        )

    assert current_db.in_transaction
    assert tuple(current_db.execute(
        "select * from provenance where identity_key=?", (binding.identity_key,)
    ).fetchone()) == parent_before
    assert current_db.execute(
        "select payload_json from actions where business_signal_public_id=?", (first,)
    ).fetchone()[0] == signal_before
    assert current_db.execute(
        "select count(*) from actions where record_type='business_task_signals'"
    ).fetchone()[0] == 1


def test_signal_rows_are_immutable_and_source_lookup_uses_parent_indexes(current_db, binding):
    first = insert_signal_version(current_db, binding, dedupe_key="signal:first")
    second = insert_signal_version(current_db, binding, dedupe_key="signal:second")
    layout = current_action_payloads().layouts["business_task_signals"]

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        current_db.execute(
            "update actions set payload_json=jsonb_set(payload_json,?,?) "
            "where business_signal_public_id=?",
            (layout.path("conversation_title"), "changed", first),
        )

    assert set(signals_for_source(
        current_db,
        source_type=binding.source_type,
        source_ref=binding.source_ref,
        source_time=binding.source_time,
    )) == {first, second}
    plan = " ".join(str(row[3]) for row in current_db.execute(
        "explain query plan "
        "select a.business_signal_public_id from provenance p indexed by idx_provenance_source "
        "join actions a indexed by idx_actions_signal_shared "
        "on a.ba_task_signals_provenance_id=p.id "
        "where a.record_type='business_task_signals' "
        "and p.source_type=? and p.source_ref=? and p.source_time=?",
        (binding.source_type, binding.source_ref, binding.source_time),
    ))
    assert "idx_provenance_source" in plan
    assert "idx_actions_signal_shared" in plan
    assert "USE TEMP B-TREE" not in plan


def test_frozen_signal_source_actions_declaration_is_exact_25_2_digest():
    builder = getattr(current_action_schema, "signal_source_action_objects", None)
    assert builder is not None
    objects = builder()
    encoded = json.dumps(objects, separators=(",", ":")).encode()
    assert len(objects) == 215
    assert hashlib.sha256(encoded).hexdigest() == (
        "679e4bd4a5b9166a10754884a7dcab0b7b33b5c9137e01d270888b5cc2ff46a0"
    )


def test_current_target_manifest_has_exact_25_3_count_and_digest():
    from app.current_schema_manifest import current_objects

    objects = current_objects(email_ledger=True)
    encoded = json.dumps(objects, ensure_ascii=False, separators=(",", ":")).encode()
    assert len(objects) == 476
    assert hashlib.sha256(encoded).hexdigest() == (
        "234e17843afc171b4fa8cb42f3e1d959ef2ba415f43e295dc7db640ab3d02221"
    )
    assert "trg_actions_email_retry_immutable_insert" in {
        name for object_type, name, *_ in objects if object_type == "trigger"
    }


def test_current_actions_have_no_inline_signal_source_columns_or_promotion_trigger(current_db):
    columns = {row[1] for row in current_db.execute("pragma table_xinfo(actions)")}
    assert "ba_task_signals_provenance_id" in columns
    assert not {
        "ba_task_signals_" + field for field in SOURCE_FIELDS
    }.intersection(columns)
    objects = current_action_schema.current_action_objects()
    sql = "\n".join(item["sql"] for item in objects)
    assert "trg_actions_business_signals_native_leaf_insert" not in sql
    assert "idx_actions_signal_inline_key" not in sql
    assert "idx_actions_signal_inline_source" not in sql


def test_assembled_source_actions_keep_inline_shape_while_current_selects_references():
    source = assembled_action_objects(False)
    current = assembled_action_objects(True)
    assert "ba_task_signals_identity_key" in source["actions"]
    assert "idx_actions_signal_inline_key" in source
    assert "ba_task_signals_identity_key" not in current["actions"]
    assert "idx_actions_signal_inline_key" not in current
