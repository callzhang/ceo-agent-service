"""Deployment reads only the exact current and immediate source declarations."""
import sqlite3
import pytest
from app import current_publication_sources as sources
from app.current_action_schema_release import prepare_current_action_schema_release
from tests.test_current_action_schema_release import source_database, ROOT


@pytest.mark.parametrize('variant',('actual_no_memory_fk','fresh_memory_fk'))
def test_publication_readers_prepare_on_previous_and_current(tmp_path,variant):
    path=tmp_path/'paired.sqlite3';source_database(path,variant)
    release=prepare_current_action_schema_release(ROOT,path)
    for target in (False,True):
        if target:release.publish()
        with sqlite3.connect(path) as db:
            selects=[*sources.publication_run_sources(db),*sources.publication_task_sources(db),
                *sources.publication_meeting_sources(db),sources.publication_scheduled_source(db),
                sources.publication_dispatcher_source(db),sources.publication_candidate_source(db),
                *sources.publication_receipt_sources(db).values(),*sources.publication_delivery_sources(db).values(),
                sources.publication_email_action_source(db)]
            for select in selects:db.execute('select * from ('+select+') limit 1').fetchall()
            if target: assert sources.current_email_actions_available(db)


def test_store_first_has_no_email_runtime(tmp_path):
    from app.store import AutoReplyStore
    path=tmp_path/'store.sqlite3';AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        assert not sources.current_email_actions_available(db)


def test_publication_rejects_mixed_email_version(tmp_path):
    from app.email_store import EmailStore
    path=tmp_path/'mixed.sqlite3';EmailStore(path)
    with sqlite3.connect(path) as db:
        db.execute('update email_schema_migrations set version=54')
        with pytest.raises(ValueError,match='publication'):
            sources.publication_task_sources(db)
