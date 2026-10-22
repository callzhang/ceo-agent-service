"""Native writes between declaration preparation and conversion are current facts."""
import importlib.util
import os
from pathlib import Path

import pytest

from app.task_storage import insert_task
from tests.test_tasks12_native_source import insert,values
from tests.test_task20_schema_release import db as db, snapshot

if os.environ.get('TASK20_ORIGINAL_SOURCE')=='1':
    from app import task20_schema_release as release
else:
    spec=importlib.util.spec_from_file_location('task20_current_water_candidate',Path(__file__).with_name('2026-10-16-task20-current-water-source-candidate.py'))
    release=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)


@pytest.mark.parametrize('kind',release.SOURCE_COUNTERS)
def test_omitted_native_auto_after_prepare_preserves_current_stream_and_facts(db,kind):
    insert(db,'feedback_processing_batches',batch_id='seed')
    db.commit()
    db.execute('begin')
    metadata=release.capture_task20_source(db)
    assert metadata['sequence_present'][kind] is False
    if kind=='tasks':
        insert_task(db,'work_summary_inputs',{'source_type':'local_file','source_ref':'after-prepare','payload_json':'{}'})
    else:
        insert(db,kind,supplied=values(kind))
    expected=snapshot(db)
    assert expected['sequence'][kind]==1
    release.migrate_task20_schema(db,metadata)
    release.restore_task20_schema(db,metadata)
    assert snapshot(db)==expected
    # Reuse the original declaration metadata, including original absence bit.
    release.migrate_task20_schema(db,metadata)
    release.restore_task20_schema(db,metadata)
    assert snapshot(db)==expected


@pytest.mark.parametrize('kind',release.TASK12_AUTOINCREMENT_KINDS)
def test_native_explicit_negative_new_family_can_create_zero_counter_after_prepare(db,kind):
    insert(db,'feedback_processing_batches',batch_id='seed')
    db.commit()
    db.execute('begin')
    metadata=release.capture_task20_source(db)
    assert metadata['sequence_present'][kind] is False
    insert(db,kind,supplied=values(kind),id=-7)
    expected=snapshot(db)
    assert expected['sequence'][kind]==0
    release.migrate_task20_schema(db,metadata)
    release.restore_task20_schema(db,metadata)
    assert snapshot(db)==expected
    release.migrate_task20_schema(db,metadata)
    release.restore_task20_schema(db,metadata)
    assert snapshot(db)==expected
