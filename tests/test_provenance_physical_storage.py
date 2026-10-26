"""The opt-in physical provenance component on the complete current Actions graph."""

from dataclasses import replace
import hashlib
import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from app.email_canonical_storage import _action_objects
from app.email_canonical_definitions import EMAILS_TABLE_SQL, EMAILS_INDEX_SQL, EMAILS_TRIGGER_SQL
from app.business_source_reader import SourceVersionBinding
from app.action_storage import action_payloads, _encoded_native_sqlite_value
from app.provenance_definitions import SOURCE_FIELDS
from app.provenance_storage import (
    create_provenance_schema,
)
from app.store import AutoReplyStore
from app import native_trajectory
from app.task_source_documents import source_document_key


DEFAULT_OBJECT_DIGESTS = {
    "actual_no_memory_fk": (222, "fd382a83539117330531dcd703f9580de02ab29571e7fd423bf14c1e411ff9f0"),
    "fresh_memory_fk": (224, "864459528f6bcae685df253cfe83a2d004c7d0af4f63f99d65c7eec759258975"),
}


@pytest.mark.parametrize("variant", ("actual_no_memory_fk", "fresh_memory_fk"))
def test_physical_actions_shape_is_available_without_changing_default(variant):
    default = _action_objects(variant, target=True, inline_legacy_owner=True)
    encoded = json.dumps(default, ensure_ascii=False, separators=(",", ":")).encode()
    assert (len(default), hashlib.sha256(encoded).hexdigest()) == DEFAULT_OBJECT_DIGESTS[variant]
    physical = _action_objects(
        variant, target=True, inline_legacy_owner=True, physical_provenance=True,
    )
    assert physical != default
    assert any(obj["name"] == "idx_actions_business_signal_public_id" for obj in physical)


@pytest.fixture(scope="module")
def current_schema_bytes():
    with TemporaryDirectory(prefix="provenance-physical-source-", dir="/tmp") as directory:
        path = Path(directory) / "current.sqlite3"
        AutoReplyStore(path)
        with sqlite3.connect(path) as db:
            db.execute("pragma journal_mode=delete")
            db.execute("pragma foreign_keys=off")
            db.execute("drop table actions")
            db.execute(EMAILS_TABLE_SQL)
            for sql in (*EMAILS_INDEX_SQL, *EMAILS_TRIGGER_SQL):
                db.execute(sql)
            create_provenance_schema(db)
            objects = _action_objects("fresh_memory_fk", target=True, inline_legacy_owner=True,
                                      physical_provenance=True, compact_payload=True)
            for kind in ("table", "index", "trigger"):
                for obj in objects:
                    if obj["type"] == kind:
                        db.execute(obj["sql"])
            db.commit()
            assert db.execute("pragma foreign_key_check").fetchall() == []
        return path.read_bytes()


@pytest.fixture
def db(current_schema_bytes):
    with TemporaryDirectory(prefix="provenance-physical-case-", dir="/tmp") as directory:
        path = Path(directory) / "case.sqlite3"
        path.write_bytes(current_schema_bytes)
        with sqlite3.connect(path) as connection:
            connection.execute("pragma foreign_keys=on")
            connection.execute("begin")
            yield connection
            connection.rollback()


@pytest.fixture
def binding():
    fields = dict(source_type="ai_minutes", source_ref="source-1#todos-sha256=" + "a" * 64, source_time="time-1",
                  conversation_id="conversation-1", author_user_id="author-1",
                  author_name="Author", author_kind="human")
    body = "exact body α"
    return SourceVersionBinding(
        **fields,
        identity_key=source_document_key(**fields, evidence_text=body),
        body_sha256=hashlib.sha256(body.encode()).hexdigest(),
        body_utf8_bytes=len(body.encode()),
        provider_locator_json='{"kind":"ai_minutes","minutes_id":"source-1","components":["meeting","todos"]}',
    )


_LAYOUTS = action_payloads("fresh_memory_fk", physical_provenance=True)


def _payload(db, public_id):
    encoded = db.execute(
        "select json(payload_json) from actions where business_signal_public_id=?", (public_id,),
    ).fetchone()[0]
    values = _LAYOUTS.layouts["business_task_signals"].unpack(encoded)
    absent = {"provenance_id"} if values["provenance_id"] is None else set(SOURCE_FIELDS)
    return {name: value for name, value in values.items() if name not in absent}


def _signal_payload(values):
    # Malformed-value cases exercise SQLite checks; shape cases remain malformed
    # arrays instead of being repaired by the fixture encoder.
    layout = _LAYOUTS.layouts["business_task_signals"]
    fields = list(layout.fields)
    if "identity_key" not in values:
        fields.remove("identity_key")
    encoded = [_encoded_native_sqlite_value(values.get(name)) for name in fields]
    if "unexpected" in values:
        encoded.append(values["unexpected"])
    return json.dumps(encoded, ensure_ascii=False, allow_nan=False)


def _valid_native():
    return dict(
        runtime_kind="codex_cli", session_uuid="session-1",
        literal_encoding_mode="json_string_literal_utf8",
        line_index_zero_based=0, substring_start_character_zero_based=0,
        substring_end_character_exclusive=4, decoded_utf8_bytes=4,
        raw_line_sha256="a" * 64, decoded_utf8_sha256="b" * 64,
        leaf_path=["messages", 0, "body"],
    )


def test_parent_rejects_missing_wrong_type_and_malformed_coordinates(binding):
    with sqlite3.connect(":memory:") as parent_db:
        create_provenance_schema(parent_db)
        base = {name: getattr(binding, name) for name in SOURCE_FIELDS}
        bad = [
            base | {"body_utf8_bytes": 1.5},
            base | {"provider_locator_json": '{"kind":"ai_minutes","minutes_id":"source-1"}'},
            base | {"provider_locator_json": '{"kind":"ai_minutes","minutes_id":"source-1","components":null}'},
            base | {"provider_locator_json": '{"kind":"ai_minutes","minutes_id":"wrong","components":["meeting","todos"]}'},
            base | {"native_provenance_json": '{"runtime_kind":"codex_cli"}', "provider_locator_json": None},
            base | {"native_provenance_json": json.dumps(_valid_native() | {"leaf_path": [True]}),
                    "provider_locator_json": None},
        ]
        columns = ",".join(SOURCE_FIELDS)
        slots = ",".join("?" for _ in SOURCE_FIELDS)
        for index, payload in enumerate(bad):
            with pytest.raises(sqlite3.IntegrityError, match="CHECK|invalid"), parent_db:
                parent_db.execute(f"insert into provenance({columns}) values({slots})",
                                  tuple(payload[name] for name in SOURCE_FIELDS))
        assert parent_db.execute("select count(*) from provenance").fetchone()[0] == 0


@pytest.mark.parametrize("formatter", ("paragraph_v1", "sentence_v1"))
def test_parent_requires_explicit_formatter_only_for_minutes_excerpt_locator(binding, formatter):
    with sqlite3.connect(":memory:") as parent_db:
        create_provenance_schema(parent_db)
        base = {name: getattr(binding, name) for name in SOURCE_FIELDS}
        columns = ",".join(SOURCE_FIELDS)
        slots = ",".join("?" for _ in SOURCE_FIELDS)
        index = 0
        def insert():
            nonlocal index
            index += 1
            base["identity_key"] = hashlib.sha256(f"locator-{formatter}-{index}".encode()).hexdigest()
            parent_db.execute(f"insert into provenance({columns}) values({slots})",
                              tuple(base[name] for name in SOURCE_FIELDS))

        locator = {
            "kind": "ai_minutes", "minutes_id": "source-1",
            "components": ["meeting", "todos", "transcript_excerpts"],
            "transcript_excerpt_format": formatter,
        }
        base["provider_locator_json"] = json.dumps(locator, separators=(",", ":"))
        insert()
        locator["transcript_excerpt_format"] = "unknown_v1"
        base["provider_locator_json"] = json.dumps(locator, separators=(",", ":"))
        with pytest.raises(sqlite3.IntegrityError, match="CHECK|invalid"):
            insert()
        locator["components"] = ["meeting", "todos"]
        locator["transcript_excerpt_format"] = formatter
        base["provider_locator_json"] = json.dumps(locator, separators=(",", ":"))
        with pytest.raises(sqlite3.IntegrityError, match="CHECK|invalid"):
            insert()


def _empty_key_native_locator(monkeypatch):
    body = "exact body α"
    literal = json.dumps(body, ensure_ascii=False)
    raw = json.dumps({"": literal}, ensure_ascii=False).encode() + b"\n"
    directory = TemporaryDirectory(prefix="provenance-empty-leaf-", dir="/tmp")
    path = Path(directory.name) / "session.jsonl"
    path.write_bytes(raw)
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda session_id: path)
    locator = _valid_native() | {
        "leaf_path": [""], "raw_line_sha256": hashlib.sha256(raw).hexdigest(),
        "decoded_utf8_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "decoded_utf8_bytes": len(body.encode()),
        "substring_end_character_exclusive": len(literal),
    }
    return directory, body, locator


def test_native_reader_empty_dictionary_key_is_valid_parent_coordinate(monkeypatch, binding):
    directory, body, locator = _empty_key_native_locator(monkeypatch)
    try:
        assert native_trajectory.read_native_source_body(locator) == body
        observed = replace(binding, provider_locator_json=None,
                           native_provenance_json=json.dumps(locator, sort_keys=True))
        with sqlite3.connect(":memory:") as parent_db:
            create_provenance_schema(parent_db)
            columns = ",".join(SOURCE_FIELDS)
            slots = ",".join("?" for _ in SOURCE_FIELDS)
            parent_db.execute(f"insert into provenance({columns}) values({slots})",
                              tuple(getattr(observed, name) for name in SOURCE_FIELDS))
    finally:
        directory.cleanup()

