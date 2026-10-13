"""Exact previous/current sources used by existing publication readers."""
import pytest

from app.action_delivery_schema_release import publication_delivery_sources
from app.action_delivery_storage import migrate_action_delivery_schema
from app.action_postfix_schema_release import publication_postfix_sources
from app.action_receipt_schema_release import publication_receipt_sources
from app.action_schema_release import publication_candidate_source
from app.scheduled_config_storage import publication_scheduled_source
from app.task_schema_release import publication_meeting_sources, publication_task_sources
from tests.test_action_delivery_migration import delivery_source
from tests.test_action_receipt_storage import target_db  # noqa: F401
from tests.test_sent_delivery_source import SOURCE


@pytest.mark.parametrize('variant', tuple(SOURCE))
def test_publication_readers_select_exact_source_and_target(target_db, variant):  # noqa: F811
    db = delivery_source(target_db, variant)
    db.execute('create table service_state(key text primary key,value text,updated_at text)')
    db.execute("insert into service_state values('store_schema_version','2026-10-13.3','original')")
    db.commit()
    original = {kind:[tuple(row) for row in db.execute(sql)]
                for kind,sql in publication_delivery_sources(db).items()}
    for target in (False, True):
        if target:
            db.execute('begin immediate')
            migrate_action_delivery_schema(db)
            db.execute("update service_state set value='2026-10-13.4'")
            db.commit()
        assert {kind:[tuple(row) for row in db.execute(sql)]
                for kind,sql in publication_delivery_sources(db).items()} == original
        # Each existing reader keeps the same logical source while selecting
        # the exact declaration associated with its marker.
        db.execute(publication_candidate_source(db)).fetchall()
        for sql in publication_receipt_sources(db).values():
            db.execute(sql).fetchall()
        for sql in publication_postfix_sources(db).values():
            db.execute(sql).fetchall()
        assert len(publication_task_sources(db)) == 2
        assert len(publication_meeting_sources(db)) == 2
        assert 'runs' in publication_scheduled_source(db)
    db.execute("update service_state set value='2026-10-13.3'")
    for reader in (publication_delivery_sources, publication_candidate_source,
                   publication_receipt_sources, publication_postfix_sources,
                   publication_task_sources, publication_meeting_sources,
                   publication_scheduled_source):
        with pytest.raises(ValueError):
            reader(db)
