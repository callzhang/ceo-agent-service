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
from app.business_storage import insert_business
from app.business_candidate_storage import insert_business_candidate
from app.action_storage import action_payloads, _encoded_native_sqlite_value
from app.storage_payload import ACTION_RECORD_TYPES
from app.provenance_definitions import SOURCE_FIELDS
from app.provenance_storage import (
    create_provenance_schema, insert_signal_version, signal_binding,
    signals_for_source,
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


def test_inline_second_and_third_owner_preserve_binding_and_public_ids(db, binding):
    first = insert_signal_version(db, binding, dedupe_key="a", conversation_title="first")
    assert db.execute("select count(*) from provenance").fetchone()[0] == 0
    assert set(_payload(db, first)) == set(SOURCE_FIELDS) | {
        "conversation_title", "context_json", "dedupe_key", "created_at",
    }
    assert signals_for_source(db, source_type=binding.source_type,
                              source_ref=binding.source_ref,
                              source_time=binding.source_time) == (first,)
    second = insert_signal_version(db, replace(binding, provider_locator_json=None),
                                   dedupe_key="b", conversation_title="second")
    third = insert_signal_version(db, replace(binding, provider_locator_json='{"kind":"ai_minutes","minutes_id":"source-1","components":["meeting","todos","meeting_summary"]}'),
                                  dedupe_key="c", context_json='{"independent":true}')
    assert (first, second, third) == (1, 2, 3)
    assert db.execute("select count(*) from provenance").fetchone()[0] == 1
    assert signal_binding(db, first) == signal_binding(db, second) == signal_binding(db, third) == binding
    for public_id in (first, second, third):
        assert set(_payload(db, public_id)) == {
            "provenance_id", "conversation_title", "context_json", "dedupe_key", "created_at",
        }
    assert _payload(db, first)["conversation_title"] == "first"
    assert _payload(db, second)["conversation_title"] == "second"
    assert _payload(db, third)["context_json"] == '{"independent":true}'
    assert set(signals_for_source(db, source_type=binding.source_type,
                                  source_ref=binding.source_ref,
                                  source_time=binding.source_time)) == {1, 2, 3}
    assert db.execute("pragma foreign_key_check").fetchall() == []


def test_different_complete_version_stays_inline_and_duplicate_dedupe_fails(db, binding):
    first = insert_signal_version(db, binding, dedupe_key="a")
    changed = replace(binding, source_time="time-2", identity_key=source_document_key(
        source_type=binding.source_type, source_ref=binding.source_ref, source_time="time-2",
        conversation_id=binding.conversation_id, author_user_id=binding.author_user_id,
        author_name=binding.author_name, author_kind=binding.author_kind,
        evidence_text="exact body α",
    ))
    second = insert_signal_version(db, changed, dedupe_key="b")
    assert (first, second) == (1, 2)
    assert db.execute("select count(*) from provenance").fetchone()[0] == 0
    with pytest.raises(sqlite3.IntegrityError):
        insert_signal_version(db, binding, dedupe_key="a")
    assert db.in_transaction
    assert _payload(db, first)["identity_key"] == binding.identity_key
    assert db.execute("select count(*) from provenance").fetchone()[0] == 0


def test_same_ref_changed_body_time_or_author_are_separate_versions(db, binding):
    def version(body, **changes):
        values = {name: getattr(binding, name) for name in (
            "source_type", "source_ref", "source_time", "conversation_id",
            "author_user_id", "author_name", "author_kind",
        )} | changes
        return replace(binding, **changes,
                       identity_key=source_document_key(**values, evidence_text=body),
                       body_sha256=hashlib.sha256(body.encode()).hexdigest(),
                       body_utf8_bytes=len(body.encode()))

    versions = (
        binding, version("changed body"), version("exact body α", source_time="later"),
        version("exact body α", author_user_id="different"),
    )
    ids = [insert_signal_version(db, item, dedupe_key=f"version:{index}")
           for index, item in enumerate(versions)]
    assert ids == [1, 2, 3, 4]
    assert db.execute("select count(*) from provenance").fetchone()[0] == 0
    assert [signal_binding(db, public_id) for public_id in ids] == list(versions)


def test_failed_second_insert_rolls_back_promotion_inside_caller_transaction(db, binding):
    first = insert_signal_version(db, binding, dedupe_key="a")
    with pytest.raises(sqlite3.IntegrityError):
        insert_signal_version(db, binding, dedupe_key="a")
    db.commit()
    assert _payload(db, first)["identity_key"] == binding.identity_key
    assert db.execute("select count(*) from provenance").fetchone()[0] == 0


def test_promotion_guard_covers_every_writable_outer_column(db, binding):
    first = insert_signal_version(db, binding, dedupe_key="a")
    insert_signal_version(db, binding, dedupe_key="b")
    columns = [row[1] for row in db.execute("pragma table_xinfo(actions)")
               if row[6] == 0 and row[1] != "payload_json"]
    assert {"id", "_kind", "legacy_key", "memory_attempt_ref"} <= set(columns)
    for column in columns:
        current = db.execute(f"select {column} from actions where business_signal_public_id=?",
                             (first,)).fetchone()[0]
        replacement = 99999 if current != 99999 else 99998
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(f"update actions set {column}=? where business_signal_public_id=?",
                       (replacement, first))
    assert signal_binding(db, first) == binding
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("update actions set payload_json=jsonb_set(payload_json," + repr(_LAYOUTS.path("business_task_signals", "conversation_title")) + ",'changed') "
                   "where business_signal_public_id=?", (first,))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("delete from actions where business_signal_public_id=?", (first,))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("update provenance set body_utf8_bytes=body_utf8_bytes+1")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("delete from provenance")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("insert or replace into provenance(id,source_type,source_ref,source_time,conversation_id,"
                   "author_user_id,author_name,author_kind,identity_key,body_sha256,body_utf8_bytes) "
                   "select id,source_type,source_ref,source_time,conversation_id,author_user_id,"
                   "author_name,author_kind,identity_key,body_sha256,body_utf8_bytes from provenance")


def test_full_payload_rejects_missing_extra_wrong_type_and_bad_locator(db, binding):
    baseline = {name: getattr(binding, name) for name in SOURCE_FIELDS} | {
        "conversation_title": "", "context_json": "{}", "dedupe_key": "direct",
        "created_at": "created",
    }
    bad = [
        baseline | {"unexpected": "field"},
        {name: value for name, value in baseline.items() if name != "identity_key"},
        baseline | {"body_utf8_bytes": "thirteen"},
        baseline | {"identity_key": "A" * 64},
        baseline | {"native_provenance_json": "{}"},
        baseline | {"provider_locator_json": '{"kind":"ai_minutes"}'},
        baseline | {"context_json": "[]"},
    ]
    for index, payload in enumerate(bad):
        try:
            db.execute("insert into actions(_kind,legacy_key,payload_json) values(?,?,jsonb(?))",
                       (ACTION_RECORD_TYPES.code("business_task_signals"), "1", _signal_payload(payload)))
        except sqlite3.IntegrityError:
            pass
        else:
            pytest.fail(f"invalid Signal payload {index} was accepted")
    assert db.execute("select count(*) from actions where record_type='business_task_signals'").fetchone()[0] == 0


def test_same_key_with_changed_source_fact_is_rejected(db, binding):
    insert_signal_version(db, binding, dedupe_key="a")
    with pytest.raises(ValueError, match="collides"):
        insert_signal_version(db, replace(binding, source_ref="another"), dedupe_key="b")
    assert db.execute("select count(*) from provenance").fetchone()[0] == 0


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


def test_inline_rejects_missing_wrong_type_and_malformed_coordinates(db, binding):
    base = {name: getattr(binding, name) for name in SOURCE_FIELDS} | {
        "conversation_title": "", "context_json": "{}", "dedupe_key": "direct",
        "created_at": "created",
    }
    bad = [
        base | {"body_utf8_bytes": 1.5},
        base | {"provider_locator_json": '{"kind":"ai_minutes","minutes_id":"source-1"}'},
        base | {"provider_locator_json": '{"kind":"ai_minutes","minutes_id":"source-1","components":null}'},
        base | {"provider_locator_json": '{"kind":"ai_minutes","minutes_id":"wrong","components":["meeting","todos"]}'},
        base | {"native_provenance_json": '{"runtime_kind":"codex_cli"}', "provider_locator_json": None},
        base | {"native_provenance_json": json.dumps(_valid_native() | {"leaf_path": [True]}),
                "provider_locator_json": None},
    ]
    for index, payload in enumerate(bad):
        with pytest.raises(sqlite3.IntegrityError, match="CHECK|invalid"):
            db.execute("insert into actions(_kind,legacy_key,payload_json) values(?,?,jsonb(?))",
                       (ACTION_RECORD_TYPES.code("business_task_signals"), "1", _signal_payload(payload)))
    assert db.execute("select count(*) from actions where record_type='business_task_signals'").fetchone()[0] == 0


def test_weekly_empty_url_is_a_valid_existing_source_coordinate(db, binding):
    source_type = "project_weekly_report"
    source_ref = "dingtalk-doc:node-exact#sha256=" + "a" * 64
    weekly = replace(
        binding,
        source_type=source_type, source_ref=source_ref,
        identity_key=source_document_key(
            source_type=source_type, source_ref=source_ref,
            source_time=binding.source_time, conversation_id=binding.conversation_id,
            author_user_id=binding.author_user_id, author_name=binding.author_name,
            author_kind=binding.author_kind, evidence_text="exact body α",
        ),
        provider_locator_json='{"kind":"weekly_report","node_id":"node-exact","url":"","historyVersion":0}',
    )
    public_id = insert_signal_version(db, weekly, dedupe_key="weekly")
    assert signal_binding(db, public_id) == weekly
    columns = ",".join(SOURCE_FIELDS)
    slots = ",".join("?" for _ in SOURCE_FIELDS)
    with sqlite3.connect(":memory:") as parent_db:
        create_provenance_schema(parent_db)
        parent_db.execute(f"insert into provenance({columns}) values({slots})",
                          tuple(getattr(weekly, name) for name in SOURCE_FIELDS))
        assert parent_db.execute("select count(*) from provenance").fetchone()[0] == 1


def test_all_existing_signal_public_id_links_survive_promotion(db, binding):
    first = insert_signal_version(db, binding, dedupe_key="linked")
    insert_business(db, "anchor", {"id": 1, "anchor_type": "project", "anchor_ref": "anchor:1", "title": "Anchor"})
    insert_business(db, "project", {"id": 2, "canonical_anchor_id": 1,
                                    "title": "Project", "registry_source": "test"})
    insert_business(db, "task", {"id": 3, "title": "Task", "stage": "candidate"})
    insert_business(db, "task", {"id": 4, "title": "Task 2", "stage": "candidate"})
    insert_business_candidate(db, "attention_item", {
        "id": 5, "stable_key": "attention:5", "category": "watch", "title": "Attention",
        "why_attention": "why", "current_state": "open", "ceo_action": "act",
        "anchor_id": 1, "evidence_signal_id": first,
    })
    writes = (
        ("business_attention_events", {"attention_item_id": 5, "event_type": "opened",
                                       "signal_id": first, "before_json": "{}", "after_json": "{}", "reason": "opened"}),
        ("business_project_evidence", {"project_id": 2, "signal_id": first}),
        ("business_task_anchor_links", {"task_id": 3, "anchor_id": 1, "evidence_signal_id": first}),
        ("business_task_date_evidence", {"task_id": 3, "date_type": "next_check_at",
                                         "raw_phrase": "Friday", "source_signal_id": first,
                                         "actor_kind": "human"}),
        ("business_task_evidence", {"task_id": 3, "signal_id": first,
                                    "evidence_role": "discovery"}),
        ("business_task_relations", {"from_task_id": 3, "to_task_id": 4,
                                      "relation_type": "depends_on", "supporting_signal_id": first}),
    )
    from app.action_business_definitions import BUSINESS_ACTION_DEFAULTS
    for kind, values in writes:
        layout = _LAYOUTS.layouts[kind]
        seeded = {field: (values[field] if field in values else db.execute(
            "select " + BUSINESS_ACTION_DEFAULTS[kind].get(field, "null")).fetchone()[0])
                  for field in layout.fields}
        assert db.execute(
            "insert into actions(_kind,legacy_key,payload_json) values(?,?,jsonb(?))",
            (ACTION_RECORD_TYPES.code(kind), "1", _LAYOUTS.pack(kind, seeded)),
        ).rowcount == 1
    before = db.execute("select record_type,payload_json from actions where record_type in ("
                        + ",".join("?" for _ in writes) + ") order by id",
                        tuple(kind for kind, _ in writes)).fetchall()
    second = insert_signal_version(db, binding, dedupe_key="linked-second")
    after = db.execute("select record_type,payload_json from actions where record_type in ("
                       + ",".join("?" for _ in writes) + ") order by id",
                       tuple(kind for kind, _ in writes)).fetchall()
    assert second != first and before == after
    assert db.execute("pragma foreign_key_check").fetchall() == []


def test_existing_unowned_parent_is_rejected_without_inserting_inline(db, binding):
    columns = ",".join(SOURCE_FIELDS)
    slots = ",".join("?" for _ in SOURCE_FIELDS)
    db.execute(f"insert into provenance({columns}) values({slots})",
               tuple(getattr(binding, name) for name in SOURCE_FIELDS))
    with pytest.raises(sqlite3.IntegrityError, match="fewer than two owners"):
        insert_signal_version(db, binding, dedupe_key="malformed-parent")
    assert db.execute("select count(*) from provenance").fetchone()[0] == 1
    assert db.execute("select count(*) from actions where record_type='business_task_signals'").fetchone()[0] == 0


def test_existing_native_receipt_coordinate_fields_are_supported(db, binding):
    native = _valid_native() | {
        "path": "/obsolete/not-used.jsonl", "line_number_one_based": 1,
        "raw_line_bytes": 123,
    }
    observed = replace(binding, provider_locator_json=None,
                       native_provenance_json=json.dumps(native, sort_keys=True))
    first = insert_signal_version(db, observed, dedupe_key="native")
    assert signal_binding(db, first) == observed
    with sqlite3.connect(":memory:") as parent_db:
        create_provenance_schema(parent_db)
        columns = ",".join(SOURCE_FIELDS)
        slots = ",".join("?" for _ in SOURCE_FIELDS)
        parent_db.execute(f"insert into provenance({columns}) values({slots})",
                          tuple(getattr(observed, name) for name in SOURCE_FIELDS))


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


def test_native_reader_empty_dictionary_key_is_valid_inline_coordinate(db, monkeypatch, binding):
    directory, body, locator = _empty_key_native_locator(monkeypatch)
    try:
        assert native_trajectory.read_native_source_body(locator) == body
        observed = replace(binding, provider_locator_json=None,
                           native_provenance_json=json.dumps(locator, sort_keys=True))
        public_id = insert_signal_version(db, observed, dedupe_key="empty-native-key")
        assert signal_binding(db, public_id) == observed
    finally:
        directory.cleanup()
