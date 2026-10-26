"""Frozen pre-edit declarations and the opt-in Business Actions contract."""

from pathlib import Path

import pytest

from app.business_storage import insert_business
from app.business_candidate_storage import insert_business_candidate
from app.action_business_definitions import BUSINESS_ACTION_AUTO
from app.action_feedback_storage import _insert_feedback_row, _materialize
from app.action_feedback_definitions import FEEDBACK_FIELDS


SOURCE_PATH = (Path(__file__).parents[1] / "docs/superpowers/specs"
               / "2026-10-17-business-actions-current25-source.json")


def _insert_text_boundary_business_action(db, kind, values, *, source_rowid=None,
                                          ignore=False,
                                          on_conflict_key_do_nothing=False):
    """Test DML for the historical TEXT Actions intermediate only.

    Final compact Actions writer behavior is covered by the native parity
    tests; this fixture changes only the physical row representation here.
    """
    from app import action_business_storage as storage
    from app import action_storage as actions
    from app.action_business_definitions import BUSINESS_ACTION_AUTO, BUSINESS_ACTION_KEYS, business_action_column

    storage._validate_fields(kind, values)
    if on_conflict_key_do_nothing and (kind not in BUSINESS_ACTION_KEYS or ignore):
        raise ValueError("unsupported Business action conflict clause")
    actions._email_lock(db)
    supplied = values.get("id") if kind in BUSINESS_ACTION_AUTO else source_rowid
    identity = (storage._next_source_rowid(db, kind) if actions._source_bound_null(supplied)
                else actions._native_integer_primary_key(supplied))
    complete = storage._complete(db, kind, values)
    if kind in BUSINESS_ACTION_AUTO:
        complete.pop("id", None)
    created_at_sql = "json_set(?, '$.created_at', current_timestamp)" if "created_at" not in values else "?"
    conflict = (" on conflict(" + ",".join(business_action_column(kind, field)
                 for field in BUSINESS_ACTION_KEYS[kind]) + f") where record_type='{kind}' do nothing"
                if on_conflict_key_do_nothing else "")
    cursor = db.execute(
        "insert " + ("or ignore " if ignore else "")
        + "into main.actions(record_type,legacy_key,payload_json) values(?,?," + created_at_sql + ")"
        + conflict,
        (kind, str(identity), actions._native_payload(complete)),
    )
    from app.action_business_storage import BusinessActionWriteResult
    return BusinessActionWriteResult(identity if cursor.rowcount else None, cursor.rowcount)


def _update_text_boundary_business_action(db, kind, source_rowid, values, *, ignore=False):
    """Update the historical TEXT Actions intermediate using native payloads."""
    from app import action_business_storage as storage
    from app import action_storage as actions
    from app.action_business_definitions import BUSINESS_ACTION_AUTO, BUSINESS_ACTION_FIELDS, business_action_projection
    from app.action_task_delivery_storage import _quote

    storage._validate_fields(kind, values)
    if not values:
        raise ValueError("empty Business action update")
    actions._email_lock(db)
    projection = business_action_projection(kind, include_rowid=True, public_id_index=True)
    fields = BUSINESS_ACTION_FIELDS[kind]
    row = db.execute("select " + ",".join(_quote(field) for field in fields)
                     + " from (" + projection + ") where _source_rowid=?",
                     (source_rowid,)).fetchone()
    if row is None:
        return 0
    current = dict(zip(fields, row, strict=True))
    current.update(values)
    new_id = (actions._native_integer_primary_key(current.pop("id"), update=True)
              if kind in BUSINESS_ACTION_AUTO else source_rowid)
    return db.execute(
        "update " + ("or ignore " if ignore else "")
        + "main.actions set legacy_key=?,payload_json=? where record_type=? and legacy_key=?",
        (str(new_id), actions._native_payload(current), kind, str(source_rowid)),
    ).rowcount


@pytest.fixture(autouse=True)
def _historical_text_actions_writers(monkeypatch):
    """Keep current25 migration-boundary tests on their actual TEXT DDL."""
    from app import action_business_storage as storage

    monkeypatch.setattr(storage, "insert_business_action", _insert_text_boundary_business_action)
    monkeypatch.setattr(storage, "update_business_action", _update_text_boundary_business_action)


def _seed_source_feedback(db, values):
    """Seed the frozen object source, independent of current runtime writers."""
    fields = _materialize(db, "feedback_events", values)
    _insert_feedback_row(db, "feedback_events", (1, *(fields[name] for name in FEEDBACK_FIELDS["feedback_events"])), "actual")


def _seed_all_ten(db):
    _seed_source_feedback(db, {
        "key": "feedback:1", "feedback_token": "feedback-token:1", "rating": "positive",
    })
    db.execute("insert into business_source_documents(id,identity_key,body) values(1,'document:1','Source body')")
    db.execute("insert into business_task_signals(id,source_type,source_ref,source_document_id,"
               "dedupe_key,context_json) values(10,'meeting','meeting:10',1,'signal:10','{\"owner\":1}')")
    insert_business(db, "anchor", {"id": 1, "anchor_type": "project", "anchor_ref": "anchor:1", "title": "Anchor"})
    insert_business(db, "project", {"id": 2, "canonical_anchor_id": 1, "title": "Project", "registry_source": "test"})
    insert_business(db, "task", {"id": 3, "title": "Task", "stage": "candidate"})
    insert_business(db, "work_cluster", {"id": 4, "title": "Cluster"})
    insert_business(db, "task", {"id": 6, "title": "Second task", "stage": "candidate"})
    insert_business(db, "work_project", {"id": 7, "title": "Work project"})
    insert_business_candidate(db, "attention_item", {
        "id": 5, "stable_key": "attention:5", "category": "watch", "title": "Attention",
        "why_attention": "why", "current_state": "open", "ceo_action": "act",
        "anchor_id": 1, "evidence_signal_id": 10,
    })
    db.execute("insert into business_attention_events(id,attention_item_id,event_type,signal_id,"
               "before_json,after_json,reason) values(7,5,'opened',10,'{}','{}','opened')")
    db.execute("insert into business_attention_proposal_tasks(attention_item_id,task_id) values(5,3)")
    db.execute("insert into business_attention_tasks(attention_item_id,task_id) values(5,3)")
    db.execute("insert into business_project_evidence(project_id,signal_id) values(2,10)")
    db.execute("insert into business_task_anchor_links(id,task_id,anchor_id,evidence_signal_id) values(8,3,1,10)")
    db.execute("insert into business_task_date_evidence(id,task_id,date_type,raw_phrase,source_signal_id,actor_kind) "
               "values(9,3,'next_check_at','next Friday',10,'human')")
    db.execute("insert into business_task_evidence(task_id,signal_id,evidence_role) values(3,10,'discovery')")
    db.execute("insert into business_task_relations(from_task_id,to_task_id,relation_type,supporting_signal_id) "
               "values(3,6,'depends_on',10)")
    db.execute("insert into business_work_cluster_tasks(cluster_id,task_id) values(4,3)")
    db.execute("insert into business_legacy_links(signal_id,work_project_id) values(10,7)")
    for kind in BUSINESS_ACTION_AUTO:
        db.execute("update sqlite_sequence set seq=90 where name=?", (kind,))
