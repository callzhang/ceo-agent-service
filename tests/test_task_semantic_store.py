from __future__ import annotations
from app.current_schema_manifest import current_objects

from datetime import datetime, timezone
import hashlib
import json
import sqlite3

from pydantic import ValidationError
import pytest

from app import action_storage
from app import task_semantic_models as models
from app.business_definitions import BUSINESS_PROJECTIONS
from app.business_storage import insert_business
from app.action_business_storage import insert_business_action
from app.business_candidate_definitions import candidate_projection
from app.business_candidate_storage import insert_business_candidate, update_business_candidate
from app.store import AutoReplyStore
from app.store import BUSINESS_ACTION_SQL
from app.action_storage import insert_sync_action
from app.action_followup_sync_definitions import SYNC_ACTION_PROJECTIONS
from app.action_business_definitions import business_action_projection
from app.action_business_definitions import BUSINESS_ACTION_PARENT_FIELDS, business_action_column
from app.business_candidate_definitions import CANDIDATE_REFERENCES, candidate_column
from app.action_followup_sync_definitions import SYNC_BUSINESS_PUBLIC_ID_FKS
from app.business_source_reader import SourceVersionBinding
from app.business_source_reader import PreparedSources
from app.provenance_storage import insert_signal_version
from app.run_storage import insert_run


NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
STAMP = NOW.isoformat(timespec="seconds")

# Complete persisted rows pin the storage boundary. Mutation services are later
# tasks; these tests use no live service or provider.
ROWS = {
    "business_task_signals": (
        "BusinessTaskSignal",
        {
            "id": 1,
            "source_type": "reply_attempt",
            "source_ref": "message:42",
            "source_time": STAMP,
            "conversation_id": "chat:7",
            "conversation_title": "客户报价",
            "author_user_id": "derek",
            "author_name": "Derek",
            "author_kind": "human",
            "evidence_text": "王明，周五前提交报价。\n",
            "context_json": '{"reply_to":"message:41"}',
            "dedupe_key": "message:42:v1",
            "created_at": STAMP,
        },
    ),
    "business_tasks": (
        "BusinessTask",
        {
            "id": 1,
            "title": "提交报价",
            "description": "美国客户报价第一版",
            "origin": "source",
            "suggestion_json": "{}",
            "stage": "formal",
            "status": "open",
            "formal_basis": "explicit_assignment",
            "commitment_status": "assigned_unaccepted",
            "owner_user_id": "wangming",
            "owner_name": "王明",
            "owner_evidence_json": '{"signal_id":1}',
            "deadline_at": "2026-09-25T12:00:00+00:00",
            "business_relevance": "unknown",
            "missing_evidence_json": '["acceptance"]',
            "merged_into_task_id": None,
            "created_at": STAMP,
            "updated_at": STAMP,
            "last_activity_at": STAMP,
        },
    ),
    "business_task_evidence": (
        "BusinessTaskEvidence",
        {
            "task_id": 1,
            "signal_id": 1,
            "evidence_role": "assignment",
            "created_at": STAMP,
        },
    ),
    "business_task_date_evidence": (
        "BusinessTaskDateEvidence",
        {
            "id": 1,
            "task_id": 1,
            "date_type": "requested_deadline_at",
            "value_at": "2026-09-25",
            "raw_phrase": "周五前",
            "source_signal_id": 1,
            "actor_kind": "human",
            "actor_user_id": "derek",
            "actor_name": "Derek",
            "created_at": STAMP,
        },
    ),
    "business_task_events": (
        "BusinessTaskEvent",
        {
            "id": 1,
            "task_id": 1,
            "event_type": "created",
            "signal_id": 1,
            "before_json": "{}",
            "after_json": '{"stage":"formal"}',
            "reason": "Explicit assignment",
            "created_at": STAMP,
        },
    ),
    "business_task_relations": (
        "BusinessTaskRelation",
        {
            "from_task_id": 1,
            "to_task_id": 2,
            "relation_type": "supports",
            "status": "proposed",
            "supporting_signal_id": 1,
            "reason": "Quote supports the demo",
            "created_at": STAMP,
        },
    ),
    "business_work_clusters": (
        "BusinessWorkCluster",
        {
            "id": 1,
            "title": "美国客户成交",
            "created_at": STAMP,
        },
    ),
    "business_work_cluster_tasks": (
        "BusinessWorkClusterTask",
        {
            "cluster_id": 1,
            "task_id": 1,
            "created_at": STAMP,
        },
    ),
    "business_anchors": (
        "BusinessAnchor",
        {
            "id": 1,
            "anchor_type": "project",
            "anchor_ref": "registry:us-launch",
            "title": "US launch",
            "active": True,
            "created_at": STAMP,
        },
    ),
    "business_task_anchor_links": (
        "BusinessTaskAnchorLink",
        {
            "id": 1,
            "task_id": 1,
            "anchor_id": 1,
            "status": "confirmed",
            "active": True,
            "evidence_signal_id": 1,
            "reason": "Registry match",
            "created_at": STAMP,
        },
    ),
    "business_projects": (
        "BusinessProject",
        {
            "id": 1,
            "canonical_anchor_id": 1,
            "anchor_type": "project",
            "title": "US launch",
            "registry_source": "portfolio-registry",
            "created_at": STAMP,
            "crm_customer_id": "",
            "crm_customer_name": "",
            "crm_customer_lookup_status": "not_requested",
            "crm_customer_candidates": [],
            "crm_customer_label": "",
            "crm_customer_evidence": None,
        },
    ),
    "business_project_candidates": (
        "BusinessProjectCandidate",
        {
            "id": 1,
            "cluster_id": 1,
            "title": "US expansion",
            "reason": "Ongoing goal",
            "status": "proposed",
            "confirmed_project_id": None,
            "confirmation_signal_id": None,
            "created_at": STAMP,
        },
    ),
    "business_attention_items": (
        "BusinessAttentionItem",
        {
            "id": 1,
            "stable_key": "quote:decision",
            "category": "decision",
            "status": "active",
            "title": "选择报价方案",
            "business_area": "销售",
            "why_attention": "关键客户等待报价",
            "current_state": "已有两版方案",
            "ceo_action": "选择方案",
            "anchor_id": 1,
            "evidence_signal_id": 1,
            "assessment_json": "{}",
            "resolution_signal_id": None,
            "resolved_at": "",
            "created_at": STAMP,
            "updated_at": STAMP,
        },
    ),
    "business_attention_tasks": (
        "BusinessAttentionTask",
        {
            "attention_item_id": 1,
            "task_id": 1,
            "created_at": STAMP,
        },
    ),
    "business_attention_proposal_tasks": (
        "BusinessAttentionProposalTask",
        {
            "attention_item_id": 1,
            "task_id": 1,
            "created_at": STAMP,
        },
    ),
    "business_attention_events": (
        "BusinessAttentionEvent",
        {
            "id": 1,
            "attention_item_id": 1,
            "event_type": "opened",
            "signal_id": 1,
            "before_json": "{}",
            "after_json": '{"category":"decision"}',
            "reason": "A decision is required",
            "created_at": STAMP,
        },
    ),
    "business_legacy_links": (
        "BusinessLegacyLink",
        {
            "id": 1,
            "signal_id": 1,
            "task_id": None,
            "cluster_id": None,
            "anchor_id": None,
            "project_id": None,
            "project_candidate_id": None,
            "attention_item_id": None,
            "work_project_id": 1,
            "work_todo_id": None,
            "work_update_id": None,
            "created_at": STAMP,
        },
    ),
}

# Python's str.strip whitespace includes C0 separators as well as Unicode
# spaces. Keep this fixture independent of the SQL predicate under test.
STRIP_WHITESPACE = (
    "\t\n\v\f\r\x1c\x1d\x1e\x1f \x85\xa0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)
NONBLANK_COLUMNS = [
    ("business_task_signals", "source_type"),
    ("business_task_signals", "source_ref"),
    ("business_task_signals", "dedupe_key"),
    ("business_tasks", "title"),
    ("business_task_date_evidence", "raw_phrase"),
    ("business_task_events", "reason"),
    ("business_work_clusters", "title"),
    ("business_anchors", "anchor_ref"),
    ("business_anchors", "title"),
    ("business_projects", "title"),
    ("business_projects", "registry_source"),
    ("business_project_candidates", "title"),
    ("business_project_candidates", "reason"),
    ("business_attention_items", "stable_key"),
    ("business_attention_items", "title"),
    ("business_attention_items", "why_attention"),
    ("business_attention_items", "current_state"),
    ("business_attention_items", "ceo_action"),
    ("business_attention_items", "resolved_at"),
    ("business_attention_events", "reason"),
]


@pytest.fixture
def store(tmp_path):
    return AutoReplyStore(tmp_path / "semantic.sqlite3")


def insert_row(db, table, values):
    values = dict(values)
    if table == "business_task_signals":
        from app.task_semantic_service import SourceSignal

        signal = SourceSignal(**{
            field: values[field] for field in (
                "source_type", "source_ref", "evidence_text", "dedupe_key",
                "source_time", "conversation_id", "conversation_title",
                "author_user_id", "author_name", "author_kind", "context_json",
            )
        })
        if not db.in_transaction:
            db.execute("begin")
        binding = SourceVersionBinding.from_signal(signal)
        signal_id = insert_signal_version(
            db, binding, conversation_title=signal.conversation_title,
            context_json=signal.context_json, dedupe_key=signal.dedupe_key,
            created_at=values["created_at"],
        )
        assert signal_id == values["id"]
        return
    elif table == "business_projects":
        values["crm_customer_candidates_json"] = json.dumps(
            values.pop("crm_customer_candidates"), ensure_ascii=False
        )
        evidence = values.pop("crm_customer_evidence")
        values["crm_customer_evidence_json"] = json.dumps(
            evidence or {}, ensure_ascii=False
        )
    kind = {
        "business_tasks": "task",
        "business_projects": "project",
        "business_anchors": "anchor",
        "business_project_context_revisions": "project_context_revision",
        "business_work_clusters": "work_cluster",
    }.get(table)
    if kind is not None:
        if not db.in_transaction:
            db.execute("begin")
        insert_business(db, kind, values)
        return
    candidate_kind = {
        "business_attention_items": "attention_item",
        "business_project_candidates": "project_candidate",
    }.get(table)
    if candidate_kind is not None:
        if not db.in_transaction:
            db.execute("begin")
        insert_business_candidate(db, candidate_kind, values)
        return
    if table in BUSINESS_ACTION_SQL:
        if not db.in_transaction:
            db.execute("begin")
        insert_business_action(db, table, values)
        return
    if table in SYNC_ACTION_PROJECTIONS:
        if not db.in_transaction:
            db.execute("begin")
        insert_sync_action(db, table, values)
        return
    db.execute(
        f"insert into {table} ({', '.join(values)}) values ({', '.join('?' for _ in values)})",
        tuple(values.values()),
    )


def _json_contains_scalar(value, expected):
    if isinstance(value, dict):
        return any(_json_contains_scalar(item, expected) for item in value.values())
    if isinstance(value, list):
        return any(_json_contains_scalar(item, expected) for item in value)
    return value == expected


def read_record_row(db, table):
    if table == "business_task_signals":
        return db.execute(
            f"select * from ({BUSINESS_ACTION_SQL[table]}) order by id limit 1"
        ).fetchone()
    kind = {
        "business_tasks": "task",
        "business_projects": "project",
        "business_anchors": "anchor",
        "business_project_context_revisions": "project_context_revision",
        "business_work_clusters": "work_cluster",
    }.get(table)
    candidate_kind = {
        "business_attention_items": "attention_item",
        "business_project_candidates": "project_candidate",
    }.get(table)
    relation = (
        f"({BUSINESS_PROJECTIONS[kind]})" if kind is not None
        else f"({candidate_projection(candidate_kind)})" if candidate_kind is not None
        else f"({BUSINESS_ACTION_SQL[table]})" if table in BUSINESS_ACTION_SQL
        else f"({SYNC_ACTION_PROJECTIONS[table]})" if table in SYNC_ACTION_PROJECTIONS
        else table
    )
    row = db.execute(f"select * from {relation} limit 1").fetchone()
    if table != "business_projects":
        return row
    values = dict(row)
    values["crm_customer_candidates"] = json.loads(
        values.pop("crm_customer_candidates_json")
    )
    evidence = json.loads(values.pop("crm_customer_evidence_json"))
    values["crm_customer_evidence"] = evidence or None
    return values


def seed_references(db):
    for table in (
        "business_task_signals",
        "business_tasks",
        "business_work_clusters",
        "business_anchors",
        "business_projects",
        "business_attention_items",
    ):
        insert_row(db, table, ROWS[table][1])
    insert_row(
        db, "business_tasks", dict(ROWS["business_tasks"][1], id=2, title="安排演示")
    )


def current_rows(db, table):
    kind = {
        "business_tasks": "task",
        "business_projects": "project",
        "business_anchors": "anchor",
        "business_project_context_revisions": "project_context_revision",
        "business_work_clusters": "work_cluster",
    }.get(table)
    candidate_kind = {
        "business_attention_items": "attention_item",
        "business_project_candidates": "project_candidate",
    }.get(table)
    if candidate_kind is not None or table in BUSINESS_ACTION_SQL:
        projection = (
            candidate_projection(candidate_kind, with_rowid=True)
            if candidate_kind is not None
            else business_action_projection(table, include_rowid=True)
        )
        columns = [column[0] for column in db.execute(
            f"select * from ({projection}) limit 0"
        ).description if column[0] != "_source_rowid"]
        return db.execute(
            f"select {', '.join(columns)} from ({projection}) order by _source_rowid"
        ).fetchall()
    if table in SYNC_ACTION_PROJECTIONS:
        return db.execute(
            f"select * from ({SYNC_ACTION_PROJECTIONS[table]}) order by id"
        ).fetchall()
    relation = f"({BUSINESS_PROJECTIONS[kind]})" if kind is not None else table
    ordering = "id" if kind is not None else "rowid"
    return db.execute(f"select * from {relation} order by {ordering}").fetchall()


def model_for(table):
    name = ROWS[table][0]
    assert hasattr(models, name), f"missing frozen record {name}"
    return getattr(models, name)


def nonblank_row(table, column, text):
    values = dict(ROWS[table][1], **{column: text})
    if column == "resolved_at":
        values.update(status="resolved", resolution_signal_id=1)
    return values


@pytest.mark.parametrize("table,column", NONBLANK_COLUMNS)
@pytest.mark.parametrize(
    "blank_text",
    [
        pytest.param("", id="empty"),
        pytest.param(" ", id="space"),
        pytest.param("\t", id="tab"),
        pytest.param("\n", id="newline"),
        pytest.param("\t\n", id="tab-newline"),
        pytest.param("\u00a0", id="nonbreaking-space"),
        pytest.param("\u2003", id="em-space"),
        pytest.param("\u3000", id="ideographic-space"),
        pytest.param(STRIP_WHITESPACE, id="all-python-whitespace"),
    ],
)
def test_nonblank_text_rejects_whitespace_in_pydantic_and_sqlite(
    store, table, column, blank_text
):
    assert not blank_text.strip()
    values = nonblank_row(table, column, blank_text)
    with pytest.raises(ValidationError):
        model_for(table).model_validate(values)
    # Use an independent connection: checks must work without a Python UDF,
    # and foreign-key failures must not mask a missing nonblank constraint.
    with sqlite3.connect(store.path) as db:
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            insert_row(db, table, values)


@pytest.mark.parametrize("table,column", NONBLANK_COLUMNS)
@pytest.mark.parametrize("content", ["original evidence", "\u200b\ufeff"])
def test_nonblank_text_preserves_surrounding_whitespace(store, table, column, content):
    text = f"{STRIP_WHITESPACE}{content}{STRIP_WHITESPACE}"
    values = nonblank_row(table, column, text)
    expected = model_for(table).model_validate(values)
    assert getattr(expected, column) == text
    with sqlite3.connect(store.path) as db:
        db.row_factory = sqlite3.Row
        insert_row(db, table, values)
        row = read_record_row(db, table)
        assert row[column] == text
        if table == "business_task_signals":
            record = store._business_task_signal_from_row(row)
            assert getattr(record, column) == text
            assert record.source == SourceVersionBinding.from_signal(expected)
            assert not hasattr(record, "evidence_text")
        else:
            assert model_for(table).model_validate(dict(row)) == expected


@pytest.mark.parametrize("body", ["", " ", "\t\u2003\u00a0"])
def test_store_rejects_blank_signal_body_before_persisting(store, body):
    with pytest.raises(ValidationError, match="text must not be blank"):
        store.create_business_task_signal(
            source_type="reply_attempt", source_ref="message:42", evidence_text=body,
            dedupe_key="blank-body",
        )
    with store._connect() as db:
        assert db.execute("select count(*) from actions where record_type='business_task_signals'").fetchone()[0] == 0
        assert db.execute("select count(*) from provenance").fetchone()[0] == 0


@pytest.mark.parametrize("table", ROWS)
def test_every_table_has_a_frozen_extra_forbid_record(table):
    cls = model_for(table)
    values = ROWS[table][1]
    record = cls.model_validate(values)
    assert record.model_dump(mode="json") == values
    with pytest.raises(ValidationError, match="frozen_instance"):
        setattr(record, next(iter(values)), 99)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        cls.model_validate(dict(values, unexpected="silent data loss"))


@pytest.mark.parametrize("table", (name for name in ROWS if name != "business_legacy_links"))
def test_sqlite_rows_round_trip_through_their_records(store, table):
    with store._connect() as db:
        seed_references(db)
        values = ROWS[table][1]
        if table not in {
            "business_task_signals",
            "business_tasks",
            "business_work_clusters",
            "business_anchors",
            "business_projects",
            "business_attention_items",
        }:
            insert_row(db, table, values)
        row = read_record_row(db, table)
        if table == "business_task_signals":
            input_signal = model_for(table).model_validate(values)
            expected_binding = SourceVersionBinding.from_signal(input_signal)
            record = store._business_task_signal_from_row(row)
        else:
            record = model_for(table).model_validate(dict(row))
    if table == "business_task_signals":
        assert record.id == input_signal.id
        assert record.source == expected_binding
        assert record.conversation_title == input_signal.conversation_title
        assert record.context_json == input_signal.context_json
        assert record.dedupe_key == input_signal.dedupe_key
        assert record.created_at == input_signal.created_at
        assert not hasattr(record, "evidence_text")
    else:
        assert record.model_dump(mode="json") == values


@pytest.mark.parametrize(
    ("name", "values"),
    [
        ("BusinessTaskStage", {"candidate", "formal"}),
        ("BusinessTaskStatus", {"open", "waiting", "done", "cancelled", "merged"}),
        (
            "CommitmentStatus",
            {
                "none",
                "assigned_unaccepted",
                "accepted",
                "disputed",
                "completed",
                "cancelled",
            },
        ),
        (
            "FormalTaskBasis",
            {
                "explicit_commitment",
                "explicit_assignment",
                "external_todo",
                "meeting_action_item",
            },
        ),
        ("BusinessRelevance", {"unknown", "not_relevant", "relevant"}),
        ("AttentionCategory", {"fyi", "watch", "decision", "push"}),
        (
            "BusinessEvidenceRole",
            {
                "discovery",
                "commitment",
                "assignment",
                "acceptance",
                "completion",
                "correction",
                "merge_identity",
                "relevance",
                "resolution",
            },
        ),
        (
            "BusinessRelationType",
            {"depends_on", "blocks", "supports", "supersedes", "related_to"},
        ),
    ],
)
def test_approved_enum_vocabulary(name, values):
    assert hasattr(models, name), f"missing approved enum {name}"
    assert {item.value for item in getattr(models, name)} == values


def test_signal_preserves_original_evidence_and_provenance(store):
    from app.business_source_reader import PreparedSources
    values = dict(ROWS["business_task_signals"][1])
    values.pop("id")
    values.pop("created_at")
    values["evidence_text"] = " \t\u2003王明，周五前提交报价。\n\u00a0"
    input_signal = models.BusinessTaskSignal(id=1, created_at=STAMP, **values)
    expected_binding = SourceVersionBinding.from_signal(input_signal)
    signal_id = store.create_business_task_signal(**values, now=NOW)
    signal = store.get_business_task_signal(signal_id)
    assert signal is not None
    assert "evidence_text" not in vars(signal)
    assert signal.id == signal_id and signal.created_at == STAMP
    assert signal.source == expected_binding
    assert signal.source.body_sha256 == hashlib.sha256(
        values["evidence_text"].encode("utf-8")
    ).hexdigest()
    assert signal.source.body_utf8_bytes == len(values["evidence_text"].encode("utf-8"))
    prepared = PreparedSources(store.business_source_reader())
    prepared.add_current(signal, values["evidence_text"])
    with store.business_task_transaction() as db:
        fact, original = prepared.require_body(store, signal_id, db=db)
    assert fact == signal
    assert original == values["evidence_text"]
    assert store.get_business_task_signal(signal.id) == signal
    assert store.get_business_task_signal(999) is None
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        store.create_business_task_signal(**values, now=NOW)
    changed = dict(values, evidence_text="王明：收到", dedupe_key="message:42:v2")
    changed_id = store.create_business_task_signal(**changed, now=NOW)
    assert changed_id != signal_id
    changed_fact = store.get_business_task_signal(changed_id)
    assert changed_fact.source.identity_key != signal.source.identity_key
    with store._connect() as db:
        assert not db.execute(
            "select 1 from sqlite_schema where name='business_source_documents'"
        ).fetchone()
        payloads = [json.loads(row[0]) for row in db.execute(
            "select json(payload_json) from actions where record_type='business_task_signals'"
        )]
        assert all(not _json_contains_scalar(payload, values["evidence_text"])
                   for payload in payloads)


def test_business_task_can_exist_without_project(tmp_path):
    from app.task_semantic_models import BusinessTaskStage, CommitmentStatus

    store = AutoReplyStore(tmp_path / "service.sqlite3")
    signal_id = store.create_business_task_signal(
        source_type="reply_attempt",
        source_ref="message:42",
        source_time="2026-09-22T10:00:00Z",
        evidence_text="王明，周五前提交美国客户报价第一版。",
        context_json="{}",
        dedupe_key="reply_attempt:message:42",
    )
    task_id = store.create_business_task(
        title="提交美国客户报价第一版",
        stage="formal",
        status="open",
        formal_basis="explicit_assignment",
        commitment_status="assigned_unaccepted",
        owner_name="王明",
    )
    store.link_business_task_evidence(
        task_id=task_id,
        signal_id=signal_id,
        evidence_role="assignment",
    )

    task = store.get_business_task(task_id)
    assert task is not None
    assert task.stage is BusinessTaskStage.FORMAL
    assert task.commitment_status is CommitmentStatus.ASSIGNED_UNACCEPTED
    assert store.list_business_task_project_links(task_id=task_id) == []


@pytest.mark.parametrize("operation", ["update", "delete", "replace"])
def test_signal_observations_are_immutable(store, operation):
    with store._connect() as db:
        values = dict(ROWS["business_task_signals"][1])
        insert_row(db, "business_task_signals", values)
        kind = "business_task_signals"
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            if operation == "update":
                path = action_storage._action_path(kind, "source_ref")
                db.execute(
                    "update actions set payload_json=jsonb_set(payload_json,?,json(?)) "
                    "where record_type=? and business_signal_public_id=1",
                    (path, json.dumps("another source"), kind),
                )
            elif operation == "delete":
                db.execute(
                    "delete from actions where record_type=? and business_signal_public_id=1",
                    (kind,),
                )
            else:
                input_signal = models.BusinessTaskSignal.model_validate(values)
                binding = SourceVersionBinding.from_signal(input_signal)
                source_fields = (
                    "source_type", "source_ref", "source_time", "conversation_id",
                    "author_user_id", "author_name", "author_kind", "identity_key",
                    "body_sha256", "body_utf8_bytes", "native_provenance_json",
                    "provider_locator_json",
                )
                payload = action_storage._action_payload(db, kind, {
                    **{field: getattr(binding, field) for field in source_fields},
                    "conversation_title": input_signal.conversation_title,
                    "context_json": input_signal.context_json,
                    "dedupe_key": input_signal.dedupe_key,
                    "created_at": input_signal.created_at,
                })
                db.execute(
                    "insert or replace into actions(_kind,legacy_key,payload_json) values(?,?,?)",
                    (action_storage.ACTION_RECORD_TYPES.code(kind), "1", payload),
                )


@pytest.mark.parametrize("operation", ["update", "delete", "replace"])
def test_shared_provenance_bindings_are_immutable(store, operation):
    first_id = store.create_business_task_signal(**signal_input())
    store.create_business_task_signal(**signal_input(dedupe_key="second-owner"))
    with store._connect() as db:
        before = tuple(db.execute("select * from provenance where id=1").fetchone())
        assert before
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            if operation == "update":
                db.execute(
                    "update provenance set body_sha256=? where id=1", ("a" * 64,)
                )
            elif operation == "delete":
                db.execute("delete from provenance where id=1")
            else:
                db.execute("insert or replace into provenance select * from provenance where id=1")
        assert tuple(db.execute("select * from provenance where id=1").fetchone()) == before
        assert db.execute(
            "select count(*) from actions where record_type='business_task_signals' "
            "and ba_task_signals_provenance_id=1"
        ).fetchone()[0] == 2
    assert store.get_business_task_signal(first_id) is not None


@pytest.mark.parametrize(
    "operation", ["update", "delete", "replace"],
)
def test_date_evidence_is_immutable(store, operation):
    with store._connect() as db:
        seed_references(db)
        values = dict(ROWS["business_task_date_evidence"][1])
        insert_row(db, "business_task_date_evidence", values)
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            kind = "business_task_date_evidence"
            identity = action_storage.ACTION_RECORD_TYPES.code(kind)
            if operation == "update":
                path = action_storage._action_path(kind, "raw_phrase")
                db.execute(
                    "update actions set payload_json=jsonb_set(payload_json,?,json(?)) "
                    "where record_type=? and legacy_key='1'",
                    (path, json.dumps("下周", ensure_ascii=False), kind),
                )
            elif operation == "delete":
                db.execute(
                    "delete from actions where record_type=? and legacy_key='1'", (kind,)
                )
            else:
                values.pop("id")
                db.execute(
                    "insert or replace into actions(_kind,legacy_key,payload_json) values(?,?,?)",
                    (identity, "1", action_storage._action_payload(db, kind, values)),
                )


@pytest.mark.parametrize(
    "date_type",
    ["requested_deadline_at", "external_deadline_at", "estimated_deadline_at"],
)
def test_unparseable_source_date_is_retained_without_fabricated_value(date_type):
    fact = models.BusinessTaskDateEvidence.model_validate(
        dict(
            ROWS["business_task_date_evidence"][1],
            date_type=date_type,
            value_at="",
            raw_phrase="尽快，日期未定",
        )
    )
    assert fact.value_at == ""
    assert fact.raw_phrase == "尽快，日期未定"


def test_committed_deadline_requires_parseable_value():
    with pytest.raises(ValidationError, match="date value"):
        models.BusinessTaskDateEvidence.model_validate(
            dict(
                ROWS["business_task_date_evidence"][1],
                date_type="committed_deadline_at", value_at="", raw_phrase="尽快",
            )
        )


def test_task_truth_round_trip_without_a_project(store):
    values = dict(ROWS["business_tasks"][1])
    for field in ("id", "created_at", "updated_at"):
        values.pop(field)
    task_id = store.create_business_task(**values, now=NOW)
    task = store.get_business_task(task_id)
    assert task is not None
    assert task.model_dump(mode="json") == ROWS["business_tasks"][1]
    assert store.get_business_task(task.id) == task
    assert store.list_business_task_project_links(task_id=task.id) == []
    assert store.get_business_task(999) is None


def test_creation_uses_declared_fields_and_defaults(store):
    task_id = store.create_business_task(
        title="Investigate pricing", stage="candidate", now=NOW
    )
    task = store.get_business_task(task_id)
    assert task is not None
    assert task.status.value == "open"
    assert task.commitment_status.value == "none"
    assert task.business_relevance.value == "unknown"
    assert task.missing_evidence_json == "[]"
    assert task.last_activity_at == STAMP
    with pytest.raises(TypeError, match="unexpected keyword"):
        store.create_business_task(
            title="bad", stage="candidate", injected_column="bad"
        )


def test_task_listing_combines_filters_and_paginates_stably(store):
    task_ids = []
    for stage, status, relevance in [
        ("candidate", "open", "unknown"),
        ("formal", "open", "relevant"),
        ("formal", "waiting", "relevant"),
        ("formal", "done", "not_relevant"),
    ]:
        task_ids.append(
            store.create_business_task(
                title=f"Task {len(task_ids)}",
                stage=stage,
                status=status,
                formal_basis="external_todo" if stage == "formal" else None,
                business_relevance=relevance,
                now=NOW,
            )
        )
    assert [t.id for t in store.list_business_tasks(limit=2, offset=1)] == task_ids[1:3]
    assert [
        t.id
        for t in store.list_business_tasks(
            stages=["formal"],
            statuses=["open", "waiting"],
            relevance=["relevant"],
        )
    ] == task_ids[1:3]
    assert store.list_business_tasks(stages=[]) == ()
    assert store.list_business_tasks(offset=99) == ()
    for kwargs in ({"limit": 0}, {"offset": -1}, {"statuses": ["completed"]}):
        with pytest.raises(ValueError):
            store.list_business_tasks(**kwargs)


def test_default_task_listing_uses_updated_id_index_without_sorting(store):
    first = store.create_business_task(title="First tied task", stage="candidate", now=NOW)
    earlier = store.create_business_task(
        title="Earlier task", stage="candidate", now=NOW.replace(day=21)
    )
    last = store.create_business_task(title="Last tied task", stage="candidate", now=NOW)
    with store.read_snapshot(), store._connect() as db:
        queries = []
        db.set_trace_callback(queries.append)
        tasks = store.list_business_tasks()
        db.set_trace_callback(None)
        assert [task.id for task in tasks] == [earlier, first, last]
        assert len(queries) == 1
        plan = [
            row["detail"]
            for row in db.execute(f"explain query plan {queries[0]}")
        ]
        assert not any("TEMP B-TREE" in detail for detail in plan), plan
        assert any(
            "USING INDEX idx_business_task_updated" in detail for detail in plan
        ), plan
        columns = [
            row["name"]
            for row in db.execute("pragma index_info(idx_business_task_updated)")
        ]
        assert columns[:2] == ["updated_at", "public_id"]


def test_evidence_is_a_typed_unique_task_signal_role_link(store):
    with store._connect() as db:
        seed_references(db)
    first = store.link_business_task_evidence(
        task_id=1, signal_id=1, evidence_role="assignment"
    )
    second = store.link_business_task_evidence(
        task_id=1, signal_id=1, evidence_role="acceptance"
    )
    assert first.evidence_role.value == "assignment"
    assert {
        row.evidence_role.value for row in store.list_business_task_evidence(1)
    } == {"assignment", "acceptance"}
    assert second.signal_id == first.signal_id
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        store.link_business_task_evidence(
            task_id=1, signal_id=1, evidence_role="assignment"
        )


INVALID_ROWS = [
    ("business_tasks", {"formal_basis": None}),
    ("business_tasks", {"stage": "candidate"}),
    ("business_tasks", {"status": "merged"}),
    ("business_tasks", {"merged_into_task_id": 2}),
    ("business_tasks", {"status": "merged", "merged_into_task_id": 1}),
    ("business_tasks", {"status": "completed"}),
    ("business_tasks", {"commitment_status": "declined"}),
    ("business_tasks", {"formal_basis": "approved_plan"}),
    ("business_tasks", {"business_relevance": "high"}),
    ("business_task_evidence", {"signal_id": None}),
    ("business_task_evidence", {"evidence_role": "progress"}),
    ("business_task_events", {"event_type": "display_label"}),
    ("business_task_events", {"before_json": "[]"}),
    ("business_task_relations", {"relation_type": "duplicates"}),
    ("business_task_relations", {"status": "active"}),
    ("business_task_relations", {"supporting_signal_id": None}),
    ("business_task_relations", {"to_task_id": 1}),
    ("business_anchors", {"anchor_type": "semantic_guess"}),
    ("business_anchors", {"active": 2}),
    ("business_task_anchor_links", {"status": "active"}),
    ("business_task_anchor_links", {"active": 2}),
    ("business_task_anchor_links", {"evidence_signal_id": None}),
    ("business_projects", {"anchor_type": "customer"}),
    ("business_projects", {"canonical_anchor_id": None}),
    ("business_project_candidates", {"cluster_id": None}),
    ("business_project_candidates", {"status": "confirmed"}),
    ("business_project_candidates", {"confirmed_project_id": 1}),
    ("business_project_candidates", {"confirmation_signal_id": 1}),
    ("business_attention_items", {"category": "intervene"}),
    ("business_attention_items", {"status": "read"}),
    ("business_attention_items", {"status": "resolved"}),
    ("business_attention_items", {"resolution_signal_id": 1, "resolved_at": STAMP}),
    ("business_attention_items", {"anchor_id": None}),
    ("business_attention_items", {"evidence_signal_id": None}),
    ("business_attention_items", {"why_attention": ""}),
    ("business_attention_events", {"event_type": "read"}),
    ("business_attention_events", {"signal_id": None}),
    ("business_attention_events", {"after_json": "not json"}),
    ("business_legacy_links", {"signal_id": None}),
    ("business_legacy_links", {"task_id": 1}),
    ("business_legacy_links", {"work_project_id": None}),
    ("business_legacy_links", {"work_todo_id": 1}),
]


@pytest.mark.parametrize(("table", "changes"), INVALID_ROWS)
def test_record_rejects_invalid_domain_states(table, changes):
    with pytest.raises(ValidationError):
        model_for(table).model_validate(dict(ROWS[table][1], **changes))


@pytest.mark.parametrize(("table", "changes"),
                         (item for item in INVALID_ROWS if item[0] != "business_legacy_links"))
def test_sqlite_rejects_invalid_domain_states(store, table, changes):
    # FKs are tested separately so a dangling reference cannot conceal a missing
    # CHECK constraint and make an invalid-state test pass for the wrong reason.
    with sqlite3.connect(store.path) as db:
        with pytest.raises(sqlite3.IntegrityError, match="CHECK|NOT NULL"):
            insert_row(db, table, dict(ROWS[table][1], **changes))


FOREIGN_KEYS = {
    "business_tasks": {"merged_into_task_id": "business_tasks"},
    "business_task_evidence": {
        "task_id": "business_tasks",
        "signal_id": "business_task_signals",
    },
    "business_task_events": {
        "task_id": "business_tasks",
        "signal_id": "business_task_signals",
    },
    "business_task_relations": {
        "from_task_id": "business_tasks",
        "to_task_id": "business_tasks",
        "supporting_signal_id": "business_task_signals",
    },
    "business_work_cluster_tasks": {
        "cluster_id": "business_work_clusters",
        "task_id": "business_tasks",
    },
    "business_task_anchor_links": {
        "task_id": "business_tasks",
        "anchor_id": "business_anchors",
        "evidence_signal_id": "business_task_signals",
    },
    "business_projects": {
        "canonical_anchor_id": "business_anchors",
        "anchor_type": "business_anchors",
    },
    "business_project_candidates": {
        "cluster_id": "business_work_clusters",
        "confirmed_project_id": "business_projects",
        "confirmation_signal_id": "business_task_signals",
    },
    "business_attention_items": {
        "anchor_id": "business_anchors",
        "evidence_signal_id": "business_task_signals",
        "resolution_signal_id": "business_task_signals",
    },
    "business_attention_tasks": {
        "attention_item_id": "business_attention_items",
        "task_id": "business_tasks",
    },
    "business_attention_proposal_tasks": {
        "attention_item_id": "business_attention_items",
        "task_id": "business_tasks",
    },
    "business_attention_events": {
        "attention_item_id": "business_attention_items",
        "signal_id": "business_task_signals",
    },
    "business_legacy_links": {
        "signal_id": "business_task_signals",
        "task_id": "business_tasks",
        "cluster_id": "business_work_clusters",
        "anchor_id": "business_anchors",
        "project_id": "business_projects",
        "project_candidate_id": "business_project_candidates",
        "attention_item_id": "business_attention_items",
        "work_project_id": "work_projects",
        "work_todo_id": "work_todos",
        "work_update_id": "work_updates",
    },
}


@pytest.mark.parametrize("table", (name for name in FOREIGN_KEYS if name != "business_legacy_links"))
def test_every_semantic_reference_has_a_foreign_key(store, table):
    with store._connect() as db:
        if table in {"business_tasks", "business_projects"}:
            actual = {
                (row["from"], row["table"], row["to"])
                for row in db.execute("pragma foreign_key_list(business)")
            }
            expected = (
                {("merged_task_ref", "business", "id"),
                 ("merged_task_ref_type", "business", "record_type"),
                 ("task_merged_into_task_id", "business", "public_id")}
                if table == "business_tasks" else
                {("canonical_anchor_ref", "business", "id"),
                 ("canonical_anchor_ref_type", "business", "record_type"),
                 ("project_canonical_anchor_id", "business", "public_id"),
                 ("project_anchor_type", "business", "anchor_anchor_type")}
            )
            assert expected <= actual
            return
        if table in BUSINESS_ACTION_SQL:
            expected = {
                business_action_column(table, field): BUSINESS_ACTION_PARENT_FIELDS[field]
                for field in FOREIGN_KEYS[table]
            }
            actual = {
                row["from"]: (row["table"], row["to"])
                for row in db.execute("pragma foreign_key_list(actions)")
                if row["from"] in expected
            }
            assert actual == expected
            return
        if table in SYNC_ACTION_PROJECTIONS:
            expected = {}
            for field, parent in FOREIGN_KEYS[table].items():
                target = SYNC_BUSINESS_PUBLIC_ID_FKS.get(field)
                if field == "signal_id":
                    target = ("actions", "business_signal_public_id")
                elif target is None:
                    target = (parent, "id")
                expected[f"sync_{field}"] = target
            actual = {
                row["from"]: (row["table"], row["to"])
                for row in db.execute("pragma foreign_key_list(actions)")
                if row["from"] in expected
            }
            assert actual == expected
            return
        candidate_kind = {
            "business_attention_items": "attention_item",
            "business_project_candidates": "project_candidate",
        }.get(table)
        if candidate_kind is not None:
            expected = {}
            for field, endpoint, parent in CANDIDATE_REFERENCES[candidate_kind]:
                target = (
                    ("actions", "business_signal_public_id")
                    if parent == "business_task_signals"
                    else ("business", endpoint)
                )
                expected[candidate_column(candidate_kind, field)] = target
            actual = {
                row["from"]: (row["table"], row["to"])
                for row in db.execute("pragma foreign_key_list(business)")
                if row["from"] in expected
            }
            assert actual == expected
            return
        actual = {
            row["from"]: row["table"]
            for row in db.execute(f"pragma foreign_key_list({table})")
        }
    expected = {
        column: "business" if parent in {
            "business_tasks", "business_projects", "business_anchors",
            "business_work_clusters",
        } else parent
        for column, parent in FOREIGN_KEYS[table].items()
    }
    assert actual == expected


@pytest.mark.parametrize(
    ("table", "column"),
    [
        (table, column)
        for table, columns in FOREIGN_KEYS.items() if table != "business_legacy_links"
        for column in columns
        if column != "anchor_type"
    ],
)
def test_foreign_keys_reject_dangling_references(store, table, column):
    values = dict(ROWS[table][1])
    if "id" in values:
        values["id"] = 3
    if column == "merged_into_task_id":
        values["status"] = "merged"
    if column in {"confirmed_project_id", "confirmation_signal_id"}:
        values.update(
            status="confirmed",
            confirmed_project_id=1,
            confirmation_signal_id=1,
        )
    if table == "business_attention_items":
        values["stable_key"] = "another-attention-item"
    if column == "resolution_signal_id":
        values.update(status="resolved", resolved_at=STAMP)
    values[column] = 999
    with store._connect() as db:
        seed_references(db)
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            insert_row(db, table, values)


def test_inline_work_todo_import_owner_has_typed_task_fk(store):
    with store._connect() as db:
        db.execute("pragma foreign_keys=on")
        db.execute("begin")
        insert_business(db, "task", {"id": 31, "title": "formal", "stage": "candidate"})
        insert_business(db, "work_project", {"id": 7, "title": "legacy project"})
        insert_business(db, "work_todo", {"id": 11, "project_id": 7, "title": "legacy todo"})
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            db.execute("update business set import_task_id=999 where record_type='work_todo' and public_id=11")
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            db.execute("update business set import_task_id=31 where record_type='work_project' and public_id=7")
        db.execute("update business set import_task_id=31 where record_type='work_todo' and public_id=11")
        assert db.execute("select import_task_id from business where record_type='work_todo' "
                          "and public_id=11").fetchone()[0] == 31


def test_official_project_requires_an_existing_project_anchor(store):
    with store._connect() as db:
        insert_row(
            db,
            "business_anchors",
            dict(ROWS["business_anchors"][1], anchor_type="customer"),
        )
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            insert_row(db, "business_projects", ROWS["business_projects"][1])


@pytest.mark.parametrize(
    "table",
    [
        "business_task_evidence",
        "business_task_relations",
        "business_work_cluster_tasks",
        "business_task_anchor_links",
        "business_attention_tasks",
        "business_attention_proposal_tasks",
    ],
)
def test_membership_and_provenance_links_are_unique(store, table):
    with store._connect() as db:
        seed_references(db)
        values = dict(ROWS[table][1])
        values.pop("id", None)
        insert_row(db, table, values)
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            insert_row(db, table, values)


def test_attention_resolution_retains_evidence_and_events(store):
    with store._connect() as db:
        seed_references(db)
        assert update_business_candidate(
            db, "attention_item", 1,
            {"status": "resolved", "resolution_signal_id": 1, "resolved_at": STAMP},
        ) == 1
        insert_row(
            db,
            "business_attention_events",
            dict(
                ROWS["business_attention_events"][1],
                event_type="resolved",
                after_json='{"status":"resolved"}',
            ),
        )
        item = models.BusinessAttentionItem.model_validate(
            dict(db.execute(
                f"select * from ({candidate_projection('attention_item')}) where id=1"
            ).fetchone())
        )
    assert item.status.value == "resolved"
    assert item.resolution_signal_id == 1


def test_list_indexes_are_present_and_in_the_required_manifest(store):
    expected = {
        "idx_provenance_source",
        "idx_actions_signal_shared",
        "idx_business_task_updated",
        "idx_business_task_stage",
        "idx_business_task_relevance",
        "idx_business_task_public_id",
        "idx_business_project_public_id",
        "idx_business_anchor_public_id",
        "idx_actions_business_task_evidence_lookup",
        "idx_actions_sync_business_task_events_0",
        "idx_actions_business_task_relations_lookup",
        "idx_actions_business_task_relations_reverse",
        "idx_actions_business_work_cluster_tasks_lookup",
        "idx_actions_business_task_anchor_links_lookup",
        "idx_business_project_candidate_cluster",
        "idx_business_attention_list",
        "idx_actions_business_attention_tasks_lookup",
        "idx_actions_business_attention_proposal_tasks_lookup",
        "idx_actions_business_attention_events_lookup",
    }
    with store._connect() as db:
        actual = {
            row[0]
            for row in db.execute("select name from sqlite_master where type='index'")
        }
        required = {obj[1] for obj in current_objects() if obj[0] == "index"}
        assert expected <= actual
        assert expected <= required

        db.execute("savepoint check_actions_index_manifest")
        try:
            db.execute("drop index idx_actions_business_task_evidence_lookup")
            assert not store._schema_manifest_is_current_in_connection(
                db, include_run_snapshots=False
            )
        finally:
            db.execute("rollback to check_actions_index_manifest")
            db.execute("release check_actions_index_manifest")
        assert store._schema_manifest_is_current_in_connection(
            db, include_run_snapshots=False
        )


@pytest.mark.parametrize(
    "field", ["context_json", "owner_evidence_json", "missing_evidence_json"]
)
def test_json_evidence_fields_reject_wrong_shapes(field):
    table = "business_task_signals" if field == "context_json" else "business_tasks"
    wrong = "{}" if field == "missing_evidence_json" else "[]"
    with pytest.raises(ValidationError):
        model_for(table).model_validate(dict(ROWS[table][1], **{field: wrong}))


def signal_input(**changes):
    values = dict(ROWS["business_task_signals"][1], **changes)
    for field in ("id", "created_at"):
        values.pop(field, None)
    return values


def test_distinct_task_signals_share_one_exact_source_body(store):
    from app.business_source_reader import PreparedSources
    first_id = store.create_business_task_signal(**signal_input(), now=NOW)
    with store.business_task_transaction() as db:
        second_id = store.create_business_task_signal_in_transaction(
            **signal_input(
                dedupe_key="message:42:another-task", conversation_title="另一任务的标题",
                context_json='{ "task_context": "另一项任务" }',
            ), now=NOW, _db=db,
        )
    first_task = store.create_business_task(title="提交报价", stage="candidate")
    second_task = store.create_business_task(title="安排演示", stage="candidate")
    store.link_business_task_evidence(task_id=first_task, signal_id=first_id, evidence_role="assignment")
    store.link_business_task_evidence(task_id=second_task, signal_id=second_id, evidence_role="discovery")
    first = store.get_business_task_signal(first_id)
    second = store.get_business_task_signal(second_id)
    assert first.id != second.id
    assert first.source == second.source
    assert first.source.identity_key == second.source.identity_key
    assert first.dedupe_key != second.dedupe_key
    assert second.conversation_title == "另一任务的标题"
    assert second.context_json == '{ "task_context": "另一项任务" }'
    body = signal_input()["evidence_text"]
    prepared = PreparedSources(store.business_source_reader())
    prepared.add_current(first, body)
    prepared.add_current(second, body)
    with store.business_task_transaction() as db:
        first_fact, first_body = prepared.require_body(store, first_id, db=db)
        second_fact, second_body = prepared.require_body(store, second_id, db=db)
        assert first_fact == first and second_fact == second
        assert first_body == second_body == body
    assert store.list_business_task_signals() == (first, second)
    with store.business_task_transaction() as db:
        assert store.get_business_task_signal_by_dedupe_key(dedupe_key=second.dedupe_key, _db=db) == second
        assert store.get_business_task_signal_for_task_source_ref_in_transaction(
            task_id=second_task, source_ref=second.source_ref, _db=db,
        ) == second
        assert db.execute("select count(*) from provenance").fetchone()[0] == 1
        assert db.execute(
            "select count(distinct ba_task_signals_provenance_id) from actions "
            "where record_type='business_task_signals'"
        ).fetchone()[0] == 1
        payloads = [json.loads(row[0]) for row in db.execute(
            "select json(payload_json) from actions where record_type='business_task_signals'"
        )]
        assert all(not _json_contains_scalar(payload, body) for payload in payloads)
        assert not db.execute(
            "select 1 from sqlite_schema where name='business_source_documents'"
        ).fetchone()
        assert db.execute("pragma foreign_key_check").fetchall() == []
    assert [(r.signal_id, r.evidence_role.value) for r in store.list_business_task_evidence(first_task)] == [(first_id, "assignment")]
    assert [(r.signal_id, r.evidence_role.value) for r in store.list_business_task_evidence(second_task)] == [(second_id, "discovery")]


@pytest.mark.parametrize("source_type", ["memory_provenance", "session_provenance"])
def test_cited_provenance_does_not_share_observed_source_body(store, source_type):
    cited_id = store.create_business_task_signal(
        **signal_input(source_type=source_type, dedupe_key=f"{source_type}:42"), now=NOW,
    )
    observed_id = store.create_business_task_signal(**signal_input(), now=NOW)
    cited = store.get_business_task_signal(cited_id)
    observed = store.get_business_task_signal(observed_id)
    assert cited.source_ref == observed.source_ref
    assert cited.source.body_sha256 == observed.source.body_sha256
    assert cited.source.body_utf8_bytes == observed.source.body_utf8_bytes
    assert "evidence_text" not in vars(cited) and "evidence_text" not in vars(observed)
    assert cited.source.identity_key != observed.source.identity_key
    assert cited.source_type == source_type
    with store._connect() as db:
        assert db.execute("select count(*) from provenance").fetchone()[0] == 2
        rows = db.execute(
            "select p.identity_key,a.ba_task_signals_provenance_id "
            "from actions a join provenance p on p.id=a.ba_task_signals_provenance_id "
            "where a.record_type='business_task_signals' order by a.id"
        ).fetchall()
        assert {row[0] for row in rows} == {
            cited.source.identity_key, observed.source.identity_key,
        }
        assert len({row[1] for row in rows}) == 2
        assert all(row[1] is not None for row in rows)


def insert_preexisting_orphan_meeting_run(db):
    reserved_parent_id = 4909
    assert db.execute("select max(id) from tasks").fetchone()[0] < reserved_parent_id
    assert db.execute(
        "select 1 from tasks where record_type='meeting_alignment_jobs' "
        "and cast(legacy_key as integer)=4908"
    ).fetchone() is None
    assert db.execute(
        "update sqlite_sequence set seq=? where name='tasks'",
        (reserved_parent_id,),
    ).rowcount == 1
    insert_run(db, "meeting_alignment_runs", {
        "id": 9, "legacy_key": 9, "task_id": reserved_parent_id,
        "job_id": 4908, "status": "failed", "created_at": STAMP,
    })


@pytest.mark.parametrize("change", [
    {"source_ref": "another-ref"}, {"source_type": "meeting"},
    {"source_time": "2026-09-23T12:00:00+00:00"}, {"conversation_id": "chat:8"},
    {"author_user_id": "another-human"}, {"author_name": "另一个人"},
    {"author_kind": "system"}, {"evidence_text": "另一版本正文"},
    {"evidence_text": signal_input()["evidence_text"].strip()},
])
def test_persisted_source_versions_keep_distinct_bindings(store, change):
    first_id = store.create_business_task_signal(**signal_input())
    second_id = store.create_business_task_signal(**signal_input(dedupe_key="second-signal", **change))
    first = store.get_business_task_signal(first_id)
    second = store.get_business_task_signal(second_id)
    assert first.source.identity_key != second.source.identity_key
    if "evidence_text" in change:
        assert first.source.body_sha256 != second.source.body_sha256
        assert first.source.body_utf8_bytes != second.source.body_utf8_bytes
    else:
        assert first.source.body_sha256 == second.source.body_sha256
        assert first.source.body_utf8_bytes == second.source.body_utf8_bytes
    with store._connect() as db:
        rows = db.execute(
            "select p.identity_key,a.ba_task_signals_provenance_id "
            "from actions a join provenance p on p.id=a.ba_task_signals_provenance_id "
            "where a.record_type='business_task_signals' order by a.id"
        ).fetchall()
        assert rows[0][0] != rows[1][0]
        assert rows[0][1] is not None and rows[1][1] is not None
        assert rows[0][1] != rows[1][1]
        assert db.execute("select count(*) from provenance").fetchone()[0] == 2


def test_duplicate_signal_failure_preserves_the_original_body_binding(store):
    signal_id = store.create_business_task_signal(**signal_input())
    original = store.get_business_task_signal(signal_id)
    original_body = signal_input()["evidence_text"]
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        store.create_business_task_signal(**signal_input(evidence_text="不同的重复正文"))
    assert store.list_business_task_signals() == (original,)
    prepared = PreparedSources(store.business_source_reader())
    prepared.add_current(original, original_body)
    with store.business_task_transaction() as db:
        fact, body = prepared.require_body(store, signal_id, db=db)
        assert fact == original
        assert body == original_body
    with store._connect() as db:
        assert db.execute("select count(*) from provenance").fetchone()[0] == 1
        assert db.execute(
            "select count(*) from actions where record_type='business_task_signals'"
        ).fetchone()[0] == 1
        fact = store.get_business_task_signal_in_transaction(signal_id=signal_id, _db=db)
        assert fact == original
