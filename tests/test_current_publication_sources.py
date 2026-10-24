"""Deployment reads only the exact current and immediate source declarations."""
import sqlite3
import pytest
from app import current_publication_sources as sources
from tests.test_task_run_entity_schema_release import _source_database


def test_publication_readers_select_the_explicit_predecessor_and_target(tmp_path):
    path=tmp_path/'paired.sqlite3'
    _source_database(path)
    from app.task_run_entity_schema_release import prepare_task_run_entity_release
    from app.repository_updater import in_flight_work
    from app.action_entity_storage import create_action
    from app.task8_storage import register_task8_functions, task8_insert_parts
    release=prepare_task_run_entity_release(path)
    assert release is not None
    with sqlite3.connect(path) as db:
        register_task8_functions(db)
        create_action(db, values={
            "operation": "archive", "parameters_json": "{}", "status": "processing",
            "action_plan_id": "source-plan", "classification_id": 1,
            "account_id": "source-account", "created_at": "now", "updated_at": "now",
        })
        meeting = task8_insert_parts("meeting_alignment_jobs", {
            "meeting_id": "source-meeting", "status": "ready_to_send",
            "locked_at": "claimed", "updated_at": "now",
        })
        db.execute(f"insert into tasks({meeting.columns}) values({meeting.values})", meeting.parameters)
    assert in_flight_work(path) == 2
    with sqlite3.connect(path) as db:
        predecessor_selects=[*sources.publication_run_sources(db),*sources.publication_task_sources(db),
            *sources.publication_deployment_task_sources(db),
            *sources.publication_meeting_sources(db),sources.publication_scheduled_source(db),
            sources.publication_dispatcher_source(db),sources.publication_candidate_source(db),
            *sources.publication_receipt_sources(db).values(),
            *sources.publication_delivery_sources(db).values(),
            sources.publication_email_action_source(db)]
        for select in predecessor_selects:
            db.execute('select * from ('+select+') limit 1').fetchall()
    release.publish()
    assert in_flight_work(path) == 2
    with sqlite3.connect(path) as db:
        target_selects=[*sources.publication_run_sources(db),*sources.publication_task_sources(db),
            *sources.publication_deployment_task_sources(db),
            *sources.publication_meeting_sources(db),sources.publication_scheduled_source(db),
            sources.publication_dispatcher_source(db),sources.publication_candidate_source(db),
            *sources.publication_receipt_sources(db).values(),*sources.publication_delivery_sources(db).values(),
            sources.publication_email_action_source(db)]
        for select in target_selects:
            db.execute('select * from ('+select+') limit 1').fetchall()
        assert sources.current_email_actions_available(db)
    release.rollback()


def test_store_first_has_no_email_runtime(tmp_path):
    from app.store import AutoReplyStore
    path=tmp_path/'store.sqlite3'
    AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        assert not sources.current_email_actions_available(db)


def test_publication_rejects_mixed_email_version(tmp_path):
    from app.email_store import EmailStore
    path=tmp_path/'mixed.sqlite3'
    EmailStore(path)
    with sqlite3.connect(path) as db:
        db.execute('update email_schema_migrations set version=54')
        with pytest.raises(ValueError,match='publication'):
            sources.publication_task_sources(db)
