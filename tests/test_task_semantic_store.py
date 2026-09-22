from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

from pydantic import ValidationError
import pytest

from app import store as store_module
from app import task_semantic_models as models
from app.store import AutoReplyStore


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
            "created_at": STAMP,
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
    ("business_task_signals", "evidence_text"),
    ("business_task_signals", "dedupe_key"),
    ("business_tasks", "title"),
    ("business_task_events", "reason"),
    ("business_work_clusters", "title"),
    ("business_anchors", "anchor_ref"),
    ("business_anchors", "title"),
    ("business_projects", "title"),
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
    db.execute(
        f"insert into {table} ({', '.join(values)}) values ({', '.join('?' for _ in values)})",
        tuple(values.values()),
    )


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
    db.execute("insert into work_projects (id, title) values (1, 'Legacy source')")


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
        row = db.execute(f"select * from {table}").fetchone()
        assert row[column] == text
        assert model_for(table).model_validate(dict(row)) == expected


@pytest.mark.parametrize("table", ROWS)
def test_schema_manifest_and_complete_record_columns(store, table):
    expected = set(ROWS[table][1])
    with store._connect() as db:
        actual = {row["name"] for row in db.execute(f"pragma table_info({table})")}
    assert actual == expected
    assert table in store_module.STORE_SCHEMA_REQUIRED_TABLES
    assert expected <= set(store_module.STORE_SCHEMA_REQUIRED_COLUMNS[table])


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


@pytest.mark.parametrize("table", ROWS)
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
        row = db.execute(f"select * from {table} limit 1").fetchone()
        record = model_for(table).model_validate(dict(row))
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
    values = dict(ROWS["business_task_signals"][1])
    values.pop("id")
    values.pop("created_at")
    values["evidence_text"] = " \t\u2003王明，周五前提交报价。\n\u00a0"
    signal_id = store.create_business_task_signal(**values, now=NOW)
    signal = store.get_business_task_signal(signal_id)
    assert signal is not None
    assert signal.model_dump(mode="json") == dict(
        values, id=signal_id, created_at=STAMP
    )
    assert store.get_business_task_signal(signal.id) == signal
    assert store.get_business_task_signal(999) is None
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        store.create_business_task_signal(**values, now=NOW)
    changed = dict(values, evidence_text="王明：收到", dedupe_key="message:42:v2")
    assert store.create_business_task_signal(**changed, now=NOW) != signal_id


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


@pytest.mark.parametrize(
    "statement",
    [
        "update business_task_signals set evidence_text='rewrite' where id=1",
        "update business_task_signals set source_ref='another source' where id=1",
        "delete from business_task_signals where id=1",
        "insert or replace into business_task_signals (id, source_type, source_ref, evidence_text, dedupe_key) values (1, 'email', 'changed', 'replacement', 'message:42:v1')",
    ],
)
def test_signal_observations_are_immutable(store, statement):
    with store._connect() as db:
        insert_row(db, "business_task_signals", ROWS["business_task_signals"][1])
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute(statement)


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
            "USING INDEX idx_business_tasks_updated_id" in detail for detail in plan
        ), plan
        columns = [
            row["name"]
            for row in db.execute("pragma index_info(idx_business_tasks_updated_id)")
        ]
        assert columns[:2] == ["updated_at", "id"]


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


@pytest.mark.parametrize(("table", "changes"), INVALID_ROWS)
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


@pytest.mark.parametrize("table", FOREIGN_KEYS)
def test_every_semantic_reference_has_a_foreign_key(store, table):
    with store._connect() as db:
        actual = {
            row["from"]: row["table"]
            for row in db.execute(f"pragma foreign_key_list({table})")
        }
    assert actual == FOREIGN_KEYS[table]


@pytest.mark.parametrize(
    ("table", "column"),
    [
        (table, column)
        for table, columns in FOREIGN_KEYS.items()
        for column in columns
        if column != "anchor_type"
    ],
)
def test_foreign_keys_reject_dangling_references(store, table, column):
    values = dict(ROWS[table][1])
    if "id" in values:
        values["id"] = 3
    if table == "business_legacy_links":
        endpoints = ("work_project_id", "work_todo_id", "work_update_id")
        if column not in endpoints:
            endpoints = (
                "signal_id",
                "task_id",
                "cluster_id",
                "anchor_id",
                "project_id",
                "project_candidate_id",
                "attention_item_id",
            )
        values.update({key: None for key in endpoints})
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
        "business_legacy_links",
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
        db.execute(
            "update business_attention_items set status='resolved', resolution_signal_id=1, resolved_at=? where id=1",
            (STAMP,),
        )
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
            dict(db.execute("select * from business_attention_items").fetchone())
        )
    assert item.status.value == "resolved"
    assert item.resolution_signal_id == 1


def test_list_indexes_are_present_and_in_the_required_manifest(store):
    expected = {
        "idx_business_task_signals_source",
        "idx_business_tasks_updated_id",
        "idx_business_tasks_list",
        "idx_business_tasks_relevance",
        "idx_business_task_evidence_task",
        "idx_business_task_events_task",
        "idx_business_task_relations_from",
        "idx_business_task_relations_to",
        "idx_business_work_cluster_tasks_task",
        "idx_business_task_anchor_links_task",
        "idx_business_project_candidates_cluster",
        "idx_business_attention_items_list",
        "idx_business_attention_tasks_task",
        "idx_business_attention_events_item",
        "idx_business_legacy_links_task",
    }
    with store._connect() as db:
        actual = {
            row[0]
            for row in db.execute("select name from sqlite_master where type='index'")
        }
    assert expected <= actual
    assert expected <= set(store_module.STORE_SCHEMA_REQUIRED_INDEXES)


def test_existing_pre_semantic_store_initializes_without_rewriting_legacy(tmp_path):
    path = tmp_path / "pre-semantic.sqlite3"
    AutoReplyStore(path)
    # Build a pre-semantic fixture with actual legacy data before initialization.
    # The DROP statements operate only on this test's new temporary database.
    with sqlite3.connect(path) as db:
        for table in reversed(ROWS):
            db.execute(f"drop table {table}")
        db.execute(
            "update service_state set value=? where key=?",
            ("2026-09-21.1", store_module.STORE_SCHEMA_VERSION_KEY),
        )
        db.execute(
            "insert into work_projects (id, title) values (7, 'Pre-existing legacy source')"
        )
    # Emulate a fresh process opening the older schema, as the store's existing
    # migration tests do after changing a fixture behind its per-process cache.
    store_module._INITIALIZED_STORE_PATHS.discard(path.resolve())
    store = AutoReplyStore(path)
    with store._connect() as db:
        seed_references(db)
    reopened = AutoReplyStore(path)
    assert reopened.get_business_task(1).description == "美国客户报价第一版"
    with reopened._connect() as db:
        assert (
            db.execute("select title from work_projects where id=1").fetchone()[0]
            == "Legacy source"
        )
        assert (
            db.execute("select title from work_projects where id=7").fetchone()[0]
            == "Pre-existing legacy source"
        )
        assert db.execute("pragma foreign_key_check").fetchall() == []


@pytest.mark.parametrize(
    "field", ["context_json", "owner_evidence_json", "missing_evidence_json"]
)
def test_json_evidence_fields_reject_wrong_shapes(field):
    table = "business_task_signals" if field == "context_json" else "business_tasks"
    wrong = "{}" if field == "missing_evidence_json" else "[]"
    with pytest.raises(ValidationError):
        model_for(table).model_validate(dict(ROWS[table][1], **{field: wrong}))
