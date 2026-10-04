from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

from pydantic import ValidationError
import pytest

from app import store as store_module
from app import task_semantic_models as models
from app.store import AutoReplyStore
from app.task_source_documents import source_document_key


NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
STAMP = NOW.isoformat(timespec="seconds")

# Complete persisted rows pin the storage boundary. Mutation services are later
# tasks; these tests use no live service or provider.
ROWS = {
    "business_task_signals": (
        "BusinessTaskSignal",
        {
            "id": 1,
            "source_document_id": 1,
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
    ("business_task_signals", "evidence_text"),
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
    if table == "business_task_signals":
        values = dict(values)
        identity_key = source_document_key(**{
            field: values[field] for field in (
                "source_type", "source_ref", "source_time", "conversation_id",
                "author_user_id", "author_name", "author_kind", "evidence_text",
            )
        })
        body = values.pop("evidence_text")
        insert_row(db, "business_source_documents", {
            "id": values["source_document_id"], "identity_key": identity_key,
            "body": body, "created_at": values["created_at"],
        })
    db.execute(
        f"insert into {table} ({', '.join(values)}) values ({', '.join('?' for _ in values)})",
        tuple(values.values()),
    )


def read_record_row(db, table):
    if table == "business_task_signals":
        return db.execute(
            "select signal.*, document.body as evidence_text from business_task_signals signal "
            "join business_source_documents document on document.id=signal.source_document_id "
            "order by signal.id limit 1"
        ).fetchone()
    return db.execute(f"select * from {table} limit 1").fetchone()


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


@pytest.mark.parametrize("confirmed", [True, False])
def test_project_attention_evidence_migration_preserves_original_history_once(tmp_path, confirmed):
    path = tmp_path / "project-proof-migration.sqlite3"
    original = AutoReplyStore(path)
    with original.business_task_transaction() as db:
        seed_references(db)
        insert_row(db, "business_task_evidence", ROWS["business_task_evidence"][1])
        insert_row(db, "business_task_anchor_links", dict(
            ROWS["business_task_anchor_links"][1], status="confirmed" if confirmed else "proposed"
        ))
        insert_row(db, "business_attention_events", ROWS["business_attention_events"][1])
        before = {
            table: [dict(row) for row in db.execute(f"select * from {table} order by rowid")]
            for table in (
                "business_tasks", "business_task_signals", "business_task_evidence",
                "business_task_anchor_links", "business_attention_items", "business_attention_events"
            )
        }
        db.execute("delete from service_state where key='business_project_attention_evidence_migrated'")
        db.execute("update service_state set value='2026-10-04.3' where key=?", (store_module.STORE_SCHEMA_VERSION_KEY,))
    store_module._INITIALIZED_STORE_PATHS.discard(path.resolve())
    upgraded = AutoReplyStore(path)
    assert [(proof.project_id, proof.signal_id) for proof in upgraded.list_business_project_evidence(1)] == (
        [(1, 1)] if confirmed else []
    )
    with upgraded.business_task_transaction() as db:
        after = {table: [dict(row) for row in db.execute(f"select * from {table} order by rowid")] for table in before}
        assert before == after
        assert db.execute("pragma foreign_key_check").fetchall() == []
        assert db.execute("select value from service_state where key='business_project_attention_evidence_migrated'").fetchone()[0] == "1"
    # The structural migration is not a read-time or later-initialization healer.
    later_signal = upgraded.create_business_task_signal(
        source_type="message", source_ref="later:task-only", evidence_text="任务自己的后续证据。", dedupe_key="later:task-only"
    )
    upgraded.link_business_task_evidence(task_id=1, signal_id=later_signal, evidence_role="discovery")
    upgraded.set_service_state(store_module.STORE_SCHEMA_VERSION_KEY, "2026-10-04.3")
    store_module._INITIALIZED_STORE_PATHS.discard(path.resolve())
    reopened = AutoReplyStore(path)
    assert [proof.signal_id for proof in reopened.list_business_project_evidence(1)] == ([1] if confirmed else [])


@pytest.mark.parametrize("case", ["memory_provenance", "session_provenance", "inactive_link", "resolved_card"])
def test_project_attention_migration_does_not_invent_original_relationships(tmp_path, case):
    path = tmp_path / f"migration-exclusion-{case}.sqlite3"
    original = AutoReplyStore(path)
    with original.business_task_transaction() as db:
        seed_references(db)
        signal_id = original.create_business_task_signal_in_transaction(
            source_type=case if case.endswith("provenance") else "message", source_ref=f"m:{case}",
            evidence_text="历史项目风险。", dedupe_key=case, _db=db,
        )
        original.link_business_task_evidence_in_transaction(task_id=1, signal_id=signal_id, evidence_role="discovery", _db=db)
        insert_row(db, "business_task_anchor_links", dict(ROWS["business_task_anchor_links"][1], active=case != "inactive_link"))
        if case == "resolved_card":
            db.execute("update business_attention_items set status='resolved', resolution_signal_id=?, resolved_at=? where id=1", (signal_id, STAMP))
        db.execute("delete from service_state where key='business_project_attention_evidence_migrated'")
        db.execute("update service_state set value='2026-10-04.3' where key=?", (store_module.STORE_SCHEMA_VERSION_KEY,))
    store_module._INITIALIZED_STORE_PATHS.discard(path.resolve())
    upgraded = AutoReplyStore(path)
    assert upgraded.list_business_project_evidence(1) == ()
    assert upgraded.get_business_task_signal(signal_id).source_type == (case if case.endswith("provenance") else "message")


@pytest.mark.parametrize("fail_insert", [False, True])
def test_project_attention_migration_isolates_projects_deduplicates_and_rolls_back(tmp_path, fail_insert):
    from app.task_business_resolution import BusinessResolutionService

    path = tmp_path / "two-project-migration.sqlite3"
    original = AutoReplyStore(path)
    resolver = BusinessResolutionService(original)
    projects = []
    signals = []
    for number in (1, 2):
        anchor_id = resolver.register_anchor(anchor_type="project", anchor_ref=f"project:{number}", title=f"项目{number}")
        project_id = resolver.register_official_project(anchor_id=anchor_id, registry_source=f"meeting:{number}")
        task_id = original.create_business_task(title=f"交付{number}", stage="candidate")
        signal_id = original.create_business_task_signal(
            source_type="message", source_ref=f"m:{number}", evidence_text=f"项目{number}的交付存在风险。", dedupe_key=f"m:{number}"
        )
        resolver.confirm_anchor_match(task_id=task_id, anchor_id=anchor_id, evidence_signal_id=signal_id, reason="真实项目关系")
        original.link_business_task_evidence(task_id=task_id, signal_id=signal_id, evidence_role="assignment")
        with original.business_task_transaction() as db:
            card_id = original.create_business_attention_item_in_transaction(
                stable_key=f"project:{anchor_id}", category="watch", title=f"项目{number}风险", business_area="交付",
                why_attention="交付风险", current_state="待明确", ceo_action="观察", anchor_id=anchor_id,
                evidence_signal_id=signal_id, assessment_json="{}", now=STAMP, _db=db,
            )
            original.replace_business_attention_tasks_in_transaction(attention_item_id=card_id, task_ids=(task_id,), _db=db)
            original.replace_business_attention_proposal_tasks_in_transaction(attention_item_id=card_id, task_ids=(task_id,), _db=db)
        projects.append(project_id)
        signals.append(signal_id)
    preserved = original.create_business_task_signal(source_type="message", source_ref="m:project-only", evidence_text="项目一的独立资料。", dedupe_key="m:project-only")
    tables = (
        "business_tasks", "business_task_signals", "business_source_documents", "business_task_evidence",
        "business_task_events", "business_task_anchor_links", "business_projects", "business_attention_items",
        "business_attention_tasks", "business_attention_proposal_tasks", "business_attention_events"
    )
    with original.business_task_transaction() as db:
        db.execute("insert into business_project_evidence(project_id, signal_id, created_at) values (?, ?, ?)", (projects[0], preserved, STAMP))
        before = {table: [tuple(row) for row in db.execute(f"select * from {table} order by rowid")] for table in tables}
        if fail_insert:
            db.execute(f"create trigger fixture_fail_project_proof before insert on business_project_evidence when new.project_id={projects[1]} begin select raise(abort, 'fixture migration failure'); end")
        db.execute("delete from service_state where key='business_project_attention_evidence_migrated'")
        db.execute("update service_state set value='2026-10-04.3' where key=?", (store_module.STORE_SCHEMA_VERSION_KEY,))
    store_module._INITIALIZED_STORE_PATHS.discard(path.resolve())
    if fail_insert:
        with pytest.raises(sqlite3.IntegrityError, match="fixture migration failure"):
            AutoReplyStore(path)
    else:
        AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        assert before == {table: db.execute(f"select * from {table} order by rowid").fetchall() for table in tables}
        proofs = db.execute("select project_id, signal_id, created_at from business_project_evidence order by project_id, signal_id").fetchall()
        assert (projects[0], preserved, STAMP) in proofs
        assert {(row[0], row[1]) for row in proofs} == (
            {(projects[0], preserved)} if fail_insert else {(projects[0], preserved), *zip(projects, signals)}
        )
        marker = db.execute("select value from service_state where key='business_project_attention_evidence_migrated'").fetchone()
        assert marker is None if fail_insert else marker == ("1",)
        assert db.execute("pragma foreign_key_check").fetchall() == []


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
        assert model_for(table).model_validate(dict(row)) == expected


@pytest.mark.parametrize("table", ROWS)
def test_schema_manifest_and_complete_record_columns(store, table):
    expected = set(ROWS[table][1])
    if table == "business_task_signals":
        expected.remove("evidence_text")
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
        row = read_record_row(db, table)
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
    values.pop("source_document_id")
    values["evidence_text"] = " \t\u2003王明，周五前提交报价。\n\u00a0"
    signal_id = store.create_business_task_signal(**values, now=NOW)
    signal = store.get_business_task_signal(signal_id)
    assert signal is not None
    assert signal.model_dump(mode="json") == dict(
        values, id=signal_id, source_document_id=signal.source_document_id, created_at=STAMP
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
        "update business_task_signals set source_document_id=999 where id=1",
        "update business_task_signals set source_ref='another source' where id=1",
        "delete from business_task_signals where id=1",
        "insert or replace into business_task_signals (id, source_type, source_ref, source_document_id, dedupe_key) values (1, 'email', 'changed', 1, 'message:42:v1')",
    ],
)
def test_signal_observations_are_immutable(store, statement):
    with store._connect() as db:
        insert_row(db, "business_task_signals", ROWS["business_task_signals"][1])
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute(statement)


@pytest.mark.parametrize(
    "statement",
    [
        "update business_task_date_evidence set raw_phrase='下周' where id=1",
        "delete from business_task_date_evidence where id=1",
        "insert or replace into business_task_date_evidence (id, task_id, date_type, value_at, raw_phrase, source_signal_id, actor_kind) values (1, 1, 'estimated_deadline_at', '', '下周', 1, 'human')",
    ],
)
def test_date_evidence_is_immutable(store, statement):
    with store._connect() as db:
        seed_references(db)
        insert_row(db, "business_task_date_evidence", ROWS["business_task_date_evidence"][1])
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute(statement)


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
        "business_attention_proposal_tasks",
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
        "idx_business_signal_document",
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
        "idx_business_attention_proposal_tasks_task",
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
        db.execute("drop table business_source_documents")
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


def test_previous_semantic_schema_migrates_without_classifying_legacy_deadline(tmp_path):
    path = tmp_path / "previous-semantic.sqlite3"
    original = AutoReplyStore(path)
    task_id = original.create_business_task(
        title="旧报价", stage="candidate", deadline_at="2026-09-25T18:00:00+08:00"
    )
    signal_id = original.create_business_task_signal(
        source_type="message", source_ref="old:1", evidence_text="旧报价待确认",
        dedupe_key="old:1", author_user_id="wangming", author_kind="human",
    )
    original.link_business_task_evidence(
        task_id=task_id, signal_id=signal_id, evidence_role="discovery"
    )
    with original.business_task_transaction() as db:
        original_event_id = original.append_business_task_event(
            task_id=task_id, event_type="created", signal_id=signal_id,
            before_json="{}", after_json='{"stage":"candidate"}', reason="旧语义任务",
            _db=db,
        )
    original_signal = original.get_business_task_signal(signal_id).model_dump(
        mode="json", exclude={"source_document_id"}
    )
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        install_legacy_signals(db, [original_signal])
        for trigger in (
            "trg_business_task_date_evidence_immutable_update",
            "trg_business_task_date_evidence_immutable_delete",
            "trg_business_task_date_evidence_immutable_replace",
        ):
            db.execute(f"drop trigger {trigger}")
        db.execute("drop table business_task_date_evidence")
        db.execute("alter table business_task_signals drop column author_kind")
        event_sql = db.execute(
            "select sql from sqlite_master where type='table' and name='business_task_events'"
        ).fetchone()["sql"]
        old_event_sql = event_sql.replace("'details_changed', 'fields_changed', ", "")
        assert old_event_sql != event_sql
        db.execute("drop table business_task_events")
        db.execute(old_event_sql)
        db.execute(
            """insert into business_task_events (
                id, task_id, event_type, signal_id, before_json, after_json, reason
            ) values (?, ?, 'created', ?, '{}', '{"stage":"candidate"}', '旧语义任务')""",
            (original_event_id, task_id, signal_id),
        )
        db.execute(
            "create index idx_business_task_events_task "
            "on business_task_events(task_id, created_at, id)"
        )
        db.execute(
            "update service_state set value='2026-09-22.1' where key=?",
            (store_module.STORE_SCHEMA_VERSION_KEY,),
        )
    store_module._INITIALIZED_STORE_PATHS.discard(path.resolve())

    migrated = AutoReplyStore(path)
    assert migrated._schema_is_current() is True
    assert migrated.get_business_task(task_id).deadline_at == "2026-09-25T18:00:00+08:00"
    assert migrated.list_business_task_date_evidence(task_id) == ()
    assert migrated.get_business_task_signal(signal_id).author_kind.value == "unknown"
    assert [event.id for event in migrated.list_business_task_events(task_id)] == [original_event_id]
    with migrated._connect() as db:
        assert db.execute("pragma foreign_key_check").fetchall() == []
        migrated_event_sql = db.execute(
            "select sql from sqlite_master where type='table' and name='business_task_events'"
        ).fetchone()["sql"]
        assert "details_changed" in migrated_event_sql
        assert "fields_changed" in migrated_event_sql
        assert "'skipped'" in db.execute(
            "select sql from sqlite_master where type='table' and name='task_todo_sync_outbox'"
        ).fetchone()["sql"]
    fresh = AutoReplyStore(tmp_path / "fresh-semantic.sqlite3")
    for table in (
        "business_task_signals", "business_task_date_evidence", "business_task_events"
    ):
        with migrated._connect() as old_db, fresh._connect() as new_db:
            assert [tuple(row) for row in old_db.execute(f"pragma table_info({table})")] == [
                tuple(row) for row in new_db.execute(f"pragma table_info({table})")
            ]
            assert [tuple(row) for row in old_db.execute(f"pragma foreign_key_list({table})")] == [
                tuple(row) for row in new_db.execute(f"pragma foreign_key_list({table})")
            ]
    with migrated.business_task_transaction() as db:
        migrated.create_business_task_date_evidence_in_transaction(
            task_id=task_id, source_signal_id=signal_id,
            date_type="estimated_deadline_at", value_at="", raw_phrase="大概下周",
            actor_kind="unknown", _db=db,
        )
    assert migrated.list_business_task_date_evidence(task_id)[0].raw_phrase == "大概下周"
    assert len(AutoReplyStore(path).list_business_task_date_evidence(task_id)) == 1


def test_event_constraint_migration_rolls_back_when_copy_fails(tmp_path):
    path = tmp_path / "event-migration-failure.sqlite3"
    AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        event_sql = db.execute(
            "select sql from sqlite_master where type='table' and name='business_task_events'"
        ).fetchone()[0]
        old_event_sql = event_sql.replace("'details_changed', 'fields_changed', ", "")
        db.execute("drop table business_task_events")
        db.execute(old_event_sql)
        db.execute(
            """insert into business_task_events
               (id, task_id, event_type, signal_id, before_json, after_json, reason)
               values (7, 999, 'created', null, '{}', '{}', 'legacy orphan')"""
        )
        db.execute("update service_state set value='2026-09-22.1' where key=?",
                   (store_module.STORE_SCHEMA_VERSION_KEY,))
    store_module._INITIALIZED_STORE_PATHS.discard(path.resolve())

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        AutoReplyStore(path)

    with sqlite3.connect(path) as db:
        names = {row[0] for row in db.execute(
            "select name from sqlite_master where type='table'"
        )}
        assert "business_task_events" in names
        assert "business_task_events_before_date_evidence" not in names
        assert db.execute("select id from business_task_events").fetchall() == [(7,)]
        sql = db.execute(
            "select sql from sqlite_master where type='table' and name='business_task_events'"
        ).fetchone()[0]
        assert "details_changed" not in sql


def test_event_constraint_migration_recovers_stranded_rename(tmp_path):
    path = tmp_path / "stranded-event-migration.sqlite3"
    original = AutoReplyStore(path)
    task_id = original.create_business_task(title="报价", stage="candidate")
    with original.business_task_transaction() as db:
        original.append_business_task_event(
            task_id=task_id, event_type="created", signal_id=None,
            before_json="{}", after_json="{}", reason="source event", _db=db,
        )
    with sqlite3.connect(path) as db:
        event_sql = db.execute(
            "select sql from sqlite_master where type='table' and name='business_task_events'"
        ).fetchone()[0]
        db.execute("alter table business_task_events rename to business_task_events_before_date_evidence")
        db.execute(event_sql)
        db.execute("update service_state set value='2026-09-22.1' where key=?",
                   (store_module.STORE_SCHEMA_VERSION_KEY,))
    store_module._INITIALIZED_STORE_PATHS.discard(path.resolve())

    recovered = AutoReplyStore(path)
    assert [event.reason for event in recovered.list_business_task_events(task_id)] == [
        "source event"
    ]
    with recovered._connect() as db:
        assert db.execute(
            "select name from sqlite_master where type='table' "
            "and name='business_task_events_before_date_evidence'"
        ).fetchone() is None


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
    for field in ("id", "created_at", "source_document_id"):
        values.pop(field, None)
    return values


def test_distinct_task_signals_share_one_exact_source_body(store):
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
    assert first.source_document_id == second.source_document_id
    assert first.dedupe_key != second.dedupe_key
    assert second.conversation_title == "另一任务的标题"
    assert second.context_json == '{ "task_context": "另一项任务" }'
    assert first.evidence_text == second.evidence_text == signal_input()["evidence_text"]
    assert store.list_business_task_signals() == (first, second)
    with store.business_task_transaction() as db:
        assert store.get_business_task_signal_by_dedupe_key(dedupe_key=second.dedupe_key, _db=db) == second
        assert store.get_business_task_signal_for_task_source_ref_in_transaction(
            task_id=second_task, source_ref=second.source_ref, _db=db,
        ) == second
        assert db.execute("select count(*) from business_source_documents").fetchone()[0] == 1
        assert db.execute("select body from business_source_documents").fetchone()[0] == first.evidence_text
        assert "evidence_text" not in {r["name"] for r in db.execute("pragma table_info(business_task_signals)")}
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
    assert cited.evidence_text == observed.evidence_text
    assert cited.source_document_id != observed.source_document_id
    assert cited.source_type == source_type
    with store._connect() as db:
        assert db.execute("select count(*) from business_source_documents").fetchone()[0] == 2


# Frozen pre-sharing SQLite representation, not a mock of the migration.
LEGACY_SIGNAL_DDL = """
create table business_task_signals (
    id integer primary key autoincrement,
    source_type text not null check(trim(source_type) <> ''),
    source_ref text not null check(trim(source_ref) <> ''),
    source_time text not null default '',
    conversation_id text not null default '',
    conversation_title text not null default '',
    author_user_id text not null default '',
    author_name text not null default '',
    evidence_text text not null check(trim(evidence_text) <> ''),
    context_json text not null default '{}'
        check(json_valid(context_json) and json_type(context_json) = 'object'),
    dedupe_key text not null unique check(trim(dedupe_key) <> ''),
    created_at text not null default current_timestamp,
    author_kind text not null default 'unknown'
        check(author_kind in ('human', 'system', 'agent', 'unknown'))
);
create index idx_business_task_signals_source
    on business_task_signals(source_type, source_ref, source_time, id);
create trigger trg_business_task_signals_immutable_update
before update on business_task_signals begin
    select raise(abort, 'business task signals are immutable');
end;
create trigger trg_business_task_signals_immutable_delete
before delete on business_task_signals begin
    select raise(abort, 'business task signals are immutable');
end;
create trigger trg_business_task_signals_immutable_replace
before insert on business_task_signals
when exists (select 1 from business_task_signals where id=new.id or dedupe_key=new.dedupe_key)
begin
    select raise(abort, 'business task signals are immutable: UNIQUE id or dedupe_key');
end;
"""


def install_legacy_signals(db, signals):
    # Fixture-only downgrade, with foreign keys disabled on this independent
    # temporary-database connection so the existing children keep their names.
    db.execute("drop table business_task_signals")
    db.execute("drop table if exists business_source_documents")
    db.executescript(LEGACY_SIGNAL_DDL)
    for values in signals:
        db.execute(
            f"insert into business_task_signals ({', '.join(values)}) values ({', '.join('?' for _ in values)})",
            tuple(values.values()),
        )


def legacy_source_store(path, *, include_author_kind=True):
    AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        first = dict(signal_input(evidence_text=" \t\u2003王明，周五前提交报价。\n\u00a0"), id=1, created_at=STAMP)
        legacy_signals = [
            first,
            dict(first, id=7, dedupe_key="message:42:second-task", context_json='{ "independent": true }'),
            dict(first, id=11, dedupe_key="message:42:another-author", author_user_id="another-human"),
        ]
        install_legacy_signals(db, legacy_signals)
        # Exercise real children of the immutable parent, with non-contiguous
        # signal IDs and an AUTOINCREMENT high-water mark above the last row.
        db.execute("update sqlite_sequence set seq=83 where name='business_task_signals'")
        db.execute("insert into work_projects (id, title) values (1, 'Legacy project')")
        for table in ROWS:
            if table != "business_task_signals":
                insert_row(db, table, ROWS[table][1])
                if table == "business_tasks":
                    insert_row(db, table, dict(ROWS[table][1], id=2, title="安排演示"))
        insert_row(db, "business_task_evidence", {
            "task_id": 2, "signal_id": 7, "evidence_role": "discovery", "created_at": STAMP,
        })
        insert_row(db, "business_task_evidence", {
            "task_id": 1, "signal_id": 11, "evidence_role": "acceptance", "created_at": STAMP,
        })
        insert_row(db, "business_task_events", dict(
            ROWS["business_task_events"][1], id=9, task_id=2, signal_id=7,
            before_json='{ "text": "原来正文\\n" }', after_json='{ "signal_id": 7 }',
        ))
        db.execute("insert into work_summary_inputs (id, source_type, source_ref, payload_json) values (5, 'message', 'message:42', ?)", ('{ "body": "原始输入" }',))
        db.execute("insert into task_agent_runs (id, summary_input_id, decision_json, projection_json, created_at, finished_at, updated_at) values (8, 5, ?, ?, ?, ?, ?)", ('{ "historical": "原始判断", "signal_id": 7 }', '{ "signal_id": 11 }', STAMP, STAMP, STAMP))
        db.execute("update service_state set value='2026-09-25.1' where key=?", (store_module.STORE_SCHEMA_VERSION_KEY,))
        if not include_author_kind:
            db.execute("alter table business_task_signals drop column author_kind")
            for signal in legacy_signals:
                signal.pop("author_kind")
        assert db.execute("pragma foreign_key_check").fetchall() == []
    store_module._INITIALIZED_STORE_PATHS.discard(path.resolve())
    return legacy_signals


def source_history_snapshot(db):
    tables = [table for table in ROWS if table != "business_task_signals"]
    tables += ["work_summary_inputs", "task_agent_runs"]
    return {table: [dict(row) for row in db.execute(f"select * from {table} order by rowid")] for table in tables}


@pytest.mark.parametrize("include_author_kind", [True, False])
def test_legacy_signal_bodies_migrate_without_changing_evidence_identity_or_history(tmp_path, include_author_kind):
    path = tmp_path / "legacy-source.sqlite3"
    original_signals = legacy_source_store(path, include_author_kind=include_author_kind)
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        original_history = source_history_snapshot(db)
    migrated = AutoReplyStore(path)
    signals = migrated.list_business_task_signals()
    added_fields = {"source_document_id"}
    if not include_author_kind:
        added_fields.add("author_kind")
        assert all(s.author_kind is models.BusinessActorKind.UNKNOWN for s in signals)
    assert [s.model_dump(mode="json", exclude=added_fields) for s in signals] == original_signals
    assert signals[0].source_document_id == signals[1].source_document_id
    assert signals[0].source_document_id != signals[2].source_document_id
    with migrated._connect() as db:
        assert source_history_snapshot(db) == original_history
        assert db.execute("select count(*) from business_source_documents").fetchone()[0] == 2
        assert db.execute("select seq from sqlite_sequence where name='business_task_signals'").fetchone()[0] == 83
        assert db.execute("pragma foreign_key_check").fetchall() == []
        assert "evidence_text" not in {r["name"] for r in db.execute("pragma table_info(business_task_signals)")}
        assert db.execute("select value from service_state where key=?", (store_module.STORE_SCHEMA_VERSION_KEY,)).fetchone()[0] == store_module.STORE_SCHEMA_VERSION
    assert migrated._schema_is_current()
    store_module._INITIALIZED_STORE_PATHS.discard(path.resolve())
    reopened = AutoReplyStore(path)
    assert reopened.list_business_task_signals() == signals
    with reopened._connect() as db:
        assert source_history_snapshot(db) == original_history
        assert db.execute("select count(*) from business_source_documents").fetchone()[0] == 2
    repeated_source = signal_input(**(original_signals[0] | {
        "dedupe_key": "after-migration", "author_kind": "human" if include_author_kind else "unknown",
    }))
    new_id = reopened.create_business_task_signal(**repeated_source)
    assert new_id == 84
    assert reopened.get_business_task_signal(new_id).source_document_id == signals[0].source_document_id
    with reopened._connect() as db:
        assert db.execute("select count(*) from business_source_documents").fetchone()[0] == 2


def test_source_body_migration_rolls_back_on_foreign_key_failure(tmp_path):
    path = tmp_path / "legacy-source-failure.sqlite3"
    original_signals = legacy_source_store(path)
    with sqlite3.connect(path) as db:
        db.execute("insert into business_task_evidence (task_id, signal_id, evidence_role) values (2, 999, 'discovery')")
    with pytest.raises(sqlite3.IntegrityError, match="foreign keys"):
        AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        assert [dict(r) for r in db.execute("select * from business_task_signals order by id")] == original_signals
        assert db.execute("select name from sqlite_master where name in ('business_source_documents', 'business_task_signals_source_migration')").fetchall() == []
        assert db.execute("select seq from sqlite_sequence where name='business_task_signals'").fetchone()[0] == 83
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("update business_task_signals set evidence_text='rewritten' where id=1")
        assert db.execute("select value from service_state where key=?", (store_module.STORE_SCHEMA_VERSION_KEY,)).fetchone()[0] == "2026-09-25.1"


@pytest.mark.parametrize("statement", [
    "update business_source_documents set body='rewritten' where id=1",
    "update business_source_documents set identity_key='changed' where id=1",
    "delete from business_source_documents where id=1",
    "insert or replace into business_source_documents (id, identity_key, body) values (1, 'replacement', 'rewritten')",
    "insert or replace into business_source_documents (identity_key, body) select identity_key, 'rewritten' from business_source_documents where id=1",
])
def test_shared_source_documents_are_immutable(store, statement):
    signal_id = store.create_business_task_signal(**signal_input())
    original = store.get_business_task_signal(signal_id)
    with store._connect() as db:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute(statement)
    assert store.get_business_task_signal(signal_id) == original


@pytest.mark.parametrize("upgrade", [False, True])
def test_source_document_schema_manifest_and_foreign_key_on_fresh_and_upgraded_store(tmp_path, upgrade):
    path = tmp_path / "source-schema.sqlite3"
    if upgrade:
        legacy_source_store(path)
    store = AutoReplyStore(path)
    assert "business_source_documents" in store_module.STORE_SCHEMA_REQUIRED_TABLES
    assert set(store_module.STORE_SCHEMA_REQUIRED_COLUMNS["business_source_documents"]) == {
        "id", "identity_key", "body", "created_at",
    }
    assert "source_document_id" in store_module.STORE_SCHEMA_REQUIRED_COLUMNS["business_task_signals"]
    assert "evidence_text" in store_module.STORE_SCHEMA_REMOVED_COLUMNS["business_task_signals"]
    triggers = {f"trg_business_{table}_immutable_{operation}" for table in ("source_documents", "task_signals") for operation in ("update", "delete", "replace")}
    assert triggers <= set(store_module.STORE_SCHEMA_REQUIRED_TRIGGERS)
    with store._connect() as db:
        assert triggers <= {row[0] for row in db.execute("select name from sqlite_master where type='trigger'")}
        assert [tuple(row) for row in db.execute("pragma foreign_key_list(business_task_signals)")] == [
            (0, 0, "business_source_documents", "source_document_id", "id", "NO ACTION", "NO ACTION", "NONE"),
        ]
        assert [row["name"] for row in db.execute("pragma index_info(idx_business_signal_document)")] == ["source_document_id"]
        for document_id, error in [(None, "NOT NULL"), (999, "FOREIGN KEY")]:
            with pytest.raises(sqlite3.IntegrityError, match=error):
                db.execute(
                    "insert into business_task_signals (source_type, source_ref, source_document_id, dedupe_key) values ('message', 'new-source', ?, 'new-source')",
                    (document_id,),
                )
        if upgrade:
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                db.execute("update business_task_signals set source_document_id=2 where id=1")
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                db.execute("delete from business_source_documents where id=1")
        assert db.execute("pragma foreign_key_check").fetchall() == []
    assert store._schema_is_current()


@pytest.mark.parametrize("change", [
    {"source_ref": "another-ref"}, {"source_type": "meeting"},
    {"source_time": "2026-09-23T12:00:00+00:00"}, {"conversation_id": "chat:8"},
    {"author_user_id": "another-human"}, {"author_name": "另一个人"},
    {"author_kind": "system"}, {"evidence_text": "另一版本正文"},
    {"evidence_text": signal_input()["evidence_text"].strip()},
])
def test_persisted_source_version_changes_never_share_a_document(store, change):
    first_id = store.create_business_task_signal(**signal_input())
    second_id = store.create_business_task_signal(**signal_input(dedupe_key="second-signal", **change))
    assert store.get_business_task_signal(first_id).source_document_id != store.get_business_task_signal(second_id).source_document_id
    with store._connect() as db:
        assert db.execute("select count(*) from business_source_documents").fetchone()[0] == 2


def test_duplicate_signal_failure_does_not_leave_an_unreferenced_document(store):
    signal_id = store.create_business_task_signal(**signal_input())
    original = store.get_business_task_signal(signal_id)
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        store.create_business_task_signal(**signal_input(evidence_text="same Signal key, different body"))
    assert store.list_business_task_signals() == (original,)
    with store._connect() as db:
        assert db.execute("select count(*) from business_source_documents").fetchone()[0] == 1


def test_empty_legacy_signal_table_keeps_its_autoincrement_high_water_mark(tmp_path):
    path = tmp_path / "empty-legacy-source.sqlite3"
    AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        install_legacy_signals(db, [])
        db.execute("insert into sqlite_sequence (name, seq) values ('business_task_signals', 83)")
        db.execute("update service_state set value='2026-09-25.1' where key=?", (store_module.STORE_SCHEMA_VERSION_KEY,))
    store_module._INITIALIZED_STORE_PATHS.discard(path.resolve())
    migrated = AutoReplyStore(path)
    assert migrated.list_business_task_signals() == ()
    assert migrated.create_business_task_signal(**signal_input()) == 84
