"""Reference selections publish and invert current facts through the deploy protocol."""

import json
from pathlib import Path
import sqlite3

import pytest

from app.action_storage import _objects
from app.email_store import EmailStore
from app.model_action_schema import restore_selection_references
from app.state_definitions import state_projection
from app.state_storage import update_state_rows
from app.store import AutoReplyStore
from app.store_schema_release import prepare_database_schema_release
from tests.test_reviewed_candidate_store import _reviewed


ROOT = Path(__file__).resolve().parents[1]


def _markers(db):
    return (tuple(db.execute(f"select value,updated_at from ({state_projection('service_state')}) "
                             "where key='store_schema_version'").fetchone()),
            tuple(tuple(row) for row in db.execute("select version,applied_at from email_schema_migrations order by version")))


def _source(tmp_path):
    path = tmp_path / "selection-release.sqlite3"
    store = AutoReplyStore(path)
    EmailStore(path)
    task, candidate, review, body = _reviewed(store)
    selected = store.select_candidate_option(candidate["id"], review["id"], "execute")
    with sqlite3.connect(path, isolation_level=None) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        restore_selection_references(db, {"reply_variant": "fresh_memory_fk"})
        update_state_rows(db, "service_state", {"value": "2026-10-14.1", "updated_at": b"original-clock"},
                          where_sql="key='store_schema_version'")
        db.commit()
    return path, task, candidate, review, selected, body


def test_registered_release_keeps_email_marker_and_inverts_new_selection(tmp_path):
    path, task, candidate, review, selected, body = _source(tmp_path)
    with sqlite3.connect(path) as db:
        markers = _markers(db)
        source_objects = _objects(db, "actions")
    release = prepare_database_schema_release(ROOT, path)
    assert release is not None
    assert type(release).__name__ == "SelectionSchemaRelease"
    release.publish()
    release.verify_loaded()
    store = AutoReplyStore(path)
    EmailStore(path)
    assert json.loads(store.current_reviewed_candidate(task.id, task.execution_generation)["branch_json"]) == body["decision_options"][0]
    with store._connect() as db:
        assert "branch_json" not in {row[1] for row in db.execute("pragma table_xinfo(actions)")}
        assert _markers(db)[0][0] == "2026-10-19.1"
        assert _markers(db)[1] == markers[1]
    # A later candidate and its selection are current facts for the inverse.
    from app.store import AgentRole
    later = store.claim_agent_run(task.id, task.execution_generation, role=AgentRole.CONSUMER,
        proposal_revision=1, turn_attempt=0, parent_agent_run_id=review["audit_run_id"],
        operation_id="", owner="later").run
    new_body = json.loads(json.dumps(body))
    new_body["decision_options"][0]["plan"]["actions"][0]["target"] = {"user_id": "new-target"}
    store.complete_agent_run(later.id, new_body, owner="later")
    new_candidate = store.persist_review_candidate(task, later, new_body)
    audit = store.claim_agent_run(task.id, task.execution_generation, role=AgentRole.AUDIT,
        proposal_revision=1, turn_attempt=0, parent_agent_run_id=later.id,
        operation_id="later-audit", owner="audit").run
    verdict = {"outcome": "approve", "candidate_digest": new_candidate["candidate_digest"]}
    store.complete_agent_run(audit.id, verdict, owner="audit")
    new_review = store.record_candidate_review(new_candidate["id"], audit.id, verdict)
    new_selection = store.select_candidate_option(new_candidate["id"], new_review["id"], "execute")
    release.finalize()
    release.rollback()
    with sqlite3.connect(path) as db:
        assert _markers(db) == markers
        assert _objects(db, "actions") == source_objects
        assert json.loads(db.execute("select branch_json from actions where record_type='candidate_selections' and legacy_key=?", (str(selected["id"]),)).fetchone()[0]) == body["decision_options"][0]
        assert json.loads(db.execute("select branch_json from actions where record_type='candidate_selections' and legacy_key=?", (str(new_selection["id"]),)).fetchone()[0]) == new_body["decision_options"][0]
        assert db.execute("pragma integrity_check").fetchone() == ("ok",)
    release.rollback()


def test_current_target_factory_is_noop_and_rejects_mixed_pair(tmp_path):
    path = tmp_path / "current.sqlite3"
    AutoReplyStore(path)
    EmailStore(path)
    assert prepare_database_schema_release(ROOT, path) is None
    with sqlite3.connect(path) as db:
        db.execute("begin immediate")
        update_state_rows(db, "service_state", {"value": "2026-10-14.1"}, where_sql="key='store_schema_version'")
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        prepare_database_schema_release(ROOT, path)


@pytest.mark.parametrize("constructor", [AutoReplyStore, EmailStore])
def test_old_pair_requires_explicit_publication_without_constructor_writes(tmp_path, constructor):
    path, *_ = _source(tmp_path)
    before = path.read_bytes()
    with pytest.raises(sqlite3.IntegrityError, match="unsupported or mixed"):
        constructor(path)
    assert path.read_bytes() == before


def test_deploy_readers_support_both_exact_sides_and_target_cold_failure_rolls_back(tmp_path, monkeypatch):
    from app import store_schema_release
    from app.action_schema_release import publication_candidate_source
    from app.action_receipt_schema_release import publication_receipt_sources
    from app.action_postfix_schema_release import publication_postfix_sources
    from app.action_delivery_schema_release import publication_delivery_sources
    from app.email_publication_sources import publication_email_action_source
    from app.repository_updater import in_flight_work
    from app.scheduled_config_storage import publication_scheduled_source
    from app.task_schema_release import publication_task_sources, publication_meeting_sources

    path, *_ = _source(tmp_path)

    def read_all():
        with sqlite3.connect(path) as db:
            for sql in (publication_candidate_source(db), publication_email_action_source(db),
                        publication_scheduled_source(db), *publication_task_sources(db),
                        *publication_meeting_sources(db), *publication_delivery_sources(db).values(),
                        *publication_postfix_sources(db).values(), *publication_receipt_sources(db).values()):
                db.execute(sql).fetchall()
        assert in_flight_work(path) == 0

    read_all()
    with sqlite3.connect(path) as db:
        original = _markers(db)
    release = prepare_database_schema_release(ROOT, path)

    def failed_cold_open(root, database_path):
        read_all()
        raise RuntimeError("target cold startup failed")

    monkeypatch.setattr(store_schema_release, "_migrate_store", failed_cold_open)
    with pytest.raises(RuntimeError, match="cold startup"):
        release.publish()
    release.rollback()
    with sqlite3.connect(path) as db:
        assert _markers(db) == original
    read_all()
