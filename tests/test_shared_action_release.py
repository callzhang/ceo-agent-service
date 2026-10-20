"""Archived Store19.5/Email53 forward, new facts, exact inverse and cold reopen."""
from contextlib import closing
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile

import pytest

from app import action_storage as actions
from app.email_classifier_contracts import EmailAction, EmailClassificationStatus
from app.email_store import EmailStore
from app.shared_action_definitions import email_projection
from app.shared_action_schema_release import prepare_shared_action_schema_release
from app.store import AutoReplyStore
from app.store_schema_release import prepare_database_schema_release
from tests.test_email_store import _classification, _task2_account_values

ROOT = Path(__file__).resolve().parents[1]
SOURCE_HEAD = '17c29efdab08f339acb5f9e34911aa76ede1764d'


def _old_process(root, code, *args):
    result = subprocess.run([sys.executable, '-c', code, *map(str, args)], cwd=root,
        env={**os.environ, 'PYTHONPATH': str(root)}, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout) if result.stdout.strip() else None


@pytest.fixture(scope='module')
def old_source(tmp_path_factory):
    root = tmp_path_factory.mktemp('actual-source19-5')
    archive = subprocess.check_output(['git', 'archive', SOURCE_HEAD, 'app', 'tests', 'scripts'], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(root, filter='data')
    database = root / 'source.sqlite3'
    _old_process(root, '''
import json,sys
from pathlib import Path
from app.store import AutoReplyStore
from app.email_store import EmailStore
from app.email_classifier_contracts import EmailClassification
from tests.test_reviewed_candidate_store import _reviewed
p=Path(sys.argv[1]); s=AutoReplyStore(p); e=EmailStore(p)
e.create_account(json.loads(sys.argv[3]))
c=EmailClassification.model_validate_json(sys.argv[2])
e.persist_scan_result(c, model_text='source fixture features')
a=e.direct_action_ids_for_plan(c.action_plan.action_plan_id)[0]
e.append_action_attempt(action_id=a, attempt_number=1, status='failed', provider_operation='archive',
 provider_target='message',provider_result_id='',error='source failure',started_at='source-start',finished_at='source-finish')
_,candidate,review,_=_reviewed(s,options=False)
x=s.claim_candidate_execution(candidate['id'],review['id'],'source-owner',60)
s.begin_candidate_action(x['id'],'source-owner',0,'source-key')
s.record_candidate_external_action(x['id'],'source-owner',0,'source-key','send',{'user_id':'x'},{'receipt':'source'})
s.finish_candidate_execution(x['id'],'source-owner','done',{'done':True})
with e._connect() as db:
 db.execute("update sqlite_sequence set seq=700 where name='actions:email_action_attempts'")
''', database, _classification(status=EmailClassificationStatus.PROCESSED, classification_id=11,
        actions=(EmailAction.ARCHIVE,), action_parameters={}).model_dump_json(),
        json.dumps(_task2_account_values('dingtalk-account', enabled=False)))
    return root, database


@pytest.fixture
def source_path(old_source, tmp_path):
    original, source = old_source
    path = tmp_path / 'copy.sqlite3'
    with closing(sqlite3.connect(source)) as first, closing(sqlite3.connect(path)) as second:
        first.backup(second)
    return original, path


def _facts(path, *, shared):
    from app.action_email_definitions import email_action_projection
    projection = email_projection if shared else email_action_projection
    with closing(sqlite3.connect(path)) as db:
        def scalar(query):
            return [tuple(row) for row in db.execute(query)]
        direct = projection('email_actions', include_rowid=True)
        names = ('rowid', *actions.EMAIL_FIELDS['email_actions'])
        values = ','.join(names) + ',' + ','.join('typeof(' + name + ')' for name in names)
        return dict(
            direct=scalar('select ' + values + ' from (' + direct + ') order by rowid'),
            attempts=scalar(projection('email_action_attempts') + ' order by id'),
            plans=scalar(projection('email_action_plans', include_rowid=True) + ' order by rowid'),
            reviewed=scalar(actions.REVIEW_CANDIDATE_SQL + ' order by id'),
            reviewed_attempts=scalar(actions.CANDIDATE_ACTION_ATTEMPT_SQL + ' order by id'),
            executions=scalar(actions.CANDIDATE_EXECUTION_SQL + ' order by id'),
            ids=scalar('select id,legacy_key,record_type from actions order by id'),
            waters=scalar('select rowid,name,seq from sqlite_sequence order by rowid'),
            fks=sorted(scalar('pragma foreign_key_check')),
            email_markers=scalar('select * from email_schema_migrations order by version'),
        )


def test_staging_step_updates_seek_physical_id_in_both_directions(source_path):
    from app.shared_action_schema import migrate_shared_actions, restore_shared_actions

    plans = []

    class ObservedConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.startswith('update temp._shared_action_rows set payload_json='):
                plans.extend(row[3] for row in super().execute('explain query plan ' + sql, parameters))
            return super().execute(sql, parameters)

    _, path = source_path
    with closing(sqlite3.connect(path, factory=ObservedConnection)) as db:
        db.execute('pragma foreign_keys=off')
        db.execute('begin immediate')
        metadata = migrate_shared_actions(db)
        assert db.execute("select name from sqlite_temp_master where tbl_name='_shared_action_rows'").fetchall() == []
        restore_shared_actions(db, metadata)
        assert db.execute("select name from sqlite_temp_master where tbl_name='_shared_action_rows'").fetchall() == []
        db.rollback()
    # The archived source has one direct step, transformed once each way.
    assert len(plans) == 2
    assert all(plan.startswith('SEARCH temp._shared_action_rows USING INDEX ') and '(id=?)' in plan
               for plan in plans), plans


def test_archived_source_new_attempt_plan_and_claim_inverse_cold_and_reforward(source_path, monkeypatch):
    original, path = source_path
    before = _facts(path, shared=False)
    with sqlite3.connect(path) as db:
        source_schema = actions._objects(db, 'actions')
        from app.result_schema_release import _markers
        markers = _markers(db)
    release = prepare_database_schema_release(ROOT, path)
    assert type(release).__name__ == 'SharedActionSchemaRelease'
    release.publish()
    assert _facts(path, shared=True) == before
    email = EmailStore(path)
    store = AutoReplyStore(path)
    action_id = before['direct'][0][1]
    # A newly appended attempt retains the original independent AUTO water.
    attempt = email.append_action_attempt(action_id=action_id, attempt_number=2, status='done',
        provider_operation='archive', provider_target='message', provider_result_id='new-receipt',
        error='', started_at='new-start', finished_at='new-finish')
    assert attempt['id'] == 701
    # New plan versions and current in-flight claims must inverse from current data.
    new = _classification(status=EmailClassificationStatus.PROCESSED, classification_id=12,
        message_id='new-message', actions=(EmailAction.MARK_READ,), action_parameters={})
    email.persist_scan_result(new, model_text='new fixture features')
    new_id = email.direct_action_ids_for_plan(new.action_plan.action_plan_id)[0]
    claim = email.claim_direct_action(action_id=new_id, claimed_at='2026-10-19T10:00:00+00:00')
    assert claim is not None
    # A new reviewed plan has real Consumer/Audit runs and keeps its distinct
    # human selection, execution identity and mutable attempt slot on inverse.
    from tests.test_reviewed_candidate_store import _reviewed
    enqueue = store.enqueue_reply_task
    def enqueue_new(**values):
        return enqueue(**(values | {'conversation_id': 'new-reviewed', 'trigger_message_id': 'new-reviewed'}))
    monkeypatch.setattr(store, 'enqueue_reply_task', enqueue_new)
    _, candidate, review, _ = _reviewed(store)
    selection = store.select_candidate_option(candidate['id'], review['id'], 'execute')
    execution = store.claim_candidate_execution(candidate['id'], review['id'], 'new-owner', 60)
    assert execution['selection_id'] == selection['id']
    new_attempt = store.begin_candidate_action(execution['id'], 'new-owner', 0, 'new-reviewed-key')
    store.record_candidate_action_outcome(execution['id'], 'new-owner', 0,
        'confirmed_no_effect', {'readback': 'absent'})
    same = store.begin_candidate_action(execution['id'], 'new-owner', 0, 'new-reviewed-key')
    assert (same['id'], same['created_at']) == (new_attempt['id'], new_attempt['created_at'])
    current = _facts(path, shared=True)
    release.verify_loaded()
    release.rollback()
    assert _facts(path, shared=False) == current
    with sqlite3.connect(path) as db:
        assert actions._objects(db, 'actions') == source_schema
        assert _markers(db) == markers
    _old_process(original, '''
import sys
from pathlib import Path
from app.store import AutoReplyStore
from app.email_store import EmailStore
AutoReplyStore(Path(sys.argv[1])); EmailStore(Path(sys.argv[1]))
''', path)
    # Old cold open is an actual executable source oracle, not a target parser.
    release = prepare_database_schema_release(ROOT, path)
    release.publish()
    release.finalize()
    assert _facts(path, shared=True) == current
    assert prepare_database_schema_release(ROOT, path) is None


@pytest.mark.parametrize('mutation', ['mirror', 'ordering', 'count', 'processing_terminal', 'processing_operation'])
def test_source_exception_refuses_before_any_write(source_path, mutation):
    original, path = source_path
    with sqlite3.connect(path) as db:
        layout = actions.action_payloads('fresh_memory_fk').layouts['email_actions']
        if mutation == 'mirror':
            db.execute("update actions set payload_json=jsonb_set(payload_json,?,'different') where record_type='email_actions'", (layout.path('provider_operation'),))
        elif mutation == 'ordering':
            attempt = actions.action_payloads('fresh_memory_fk').layouts['email_action_attempts']
            db.execute("update actions set payload_json=jsonb_set(payload_json,?,2) where record_type='email_action_attempts'", (attempt.path('attempt_number'),))
        elif mutation == 'count':
            db.execute("delete from actions where record_type='email_action_attempts'")
        elif mutation == 'processing_operation':
            fields = {'status': 'processing', 'finished_at': '', 'provider_result_id': '', 'error': ''}
            parts = layout.assignments_sql('payload_json', {field: 'json_quote(' + repr(value) + ')' for field, value in fields.items()})
            db.execute("update actions set payload_json=" + parts + " where record_type='email_actions'")
        else:
            db.execute("update actions set payload_json=jsonb_set(payload_json,?,'processing') where record_type='email_actions'", (layout.path('status'),))
    if mutation == 'processing_operation':
        # The source cold validator permits this old in-flight operation/target.
        # They cannot be discarded or mistaken for the prior attempt's result.
        _old_process(original, '''
import sys
from pathlib import Path
from app.store import AutoReplyStore
from app.email_store import EmailStore
AutoReplyStore(Path(sys.argv[1])); EmailStore(Path(sys.argv[1]))
''', path)
    before = path.read_bytes()
    with pytest.raises(ValueError, match='direct source'):
        prepare_shared_action_schema_release(ROOT, path)
    assert path.read_bytes() == before


def test_wrong_pair_constructor_refuses_without_writes(source_path):
    _, path = source_path
    before = path.read_bytes()
    for constructor in (AutoReplyStore, EmailStore):
        with pytest.raises(sqlite3.IntegrityError, match='unsupported or mixed'):
            constructor(path)
        assert path.read_bytes() == before


def test_all_publication_sources_and_failed_cold_load_restore_original_clock(source_path, monkeypatch):
    from app import store_schema_release
    from app.result_schema_release import _markers
    from app.state_storage import update_state_rows
    from tests.test_shared_action_lifecycle import _read_publication

    original, path = source_path
    with sqlite3.connect(path) as db:
        db.execute('begin immediate')
        update_state_rows(db, 'service_state', {'updated_at': b'original-source-clock'},
            where_sql='key=?', parameters=('store_schema_version',))
        markers = _markers(db)
    before = _facts(path, shared=False)
    assert _read_publication(path) >= 0
    release = prepare_database_schema_release(ROOT, path)
    def fail(root, database):
        assert _read_publication(database) >= 0
        raise RuntimeError('injected target cold-load failure')
    monkeypatch.setattr(store_schema_release, '_migrate_store', fail)
    with pytest.raises(RuntimeError, match='injected target cold-load'):
        release.publish()
    release.rollback()
    release.rollback()
    assert _facts(path, shared=False) == before
    with sqlite3.connect(path) as db:
        assert _markers(db) == markers
    _old_process(original, '''
import sys
from pathlib import Path
from app.store import AutoReplyStore
from app.email_store import EmailStore
AutoReplyStore(Path(sys.argv[1])); EmailStore(Path(sys.argv[1]))
''', path)


@pytest.mark.parametrize('operation,trash', [
    ('_direct_cycle', False),
    ('_append_reapply_and_retired_poll', False),
    ('_explicit_retry', True),
    ('_attempt_sequence_conflicts', False),
    ('_feedback_and_plan_conflicts', False),
])
def test_complete_direct_dao_scenarios_match_archived_19_5(old_source, tmp_path, operation, trash):
    from tests import test_email_action_plan_direct_dao as scenarios
    original, _ = old_source
    source, target = tmp_path / 'oracle.sqlite3', tmp_path / 'target.sqlite3'
    classification = _classification(status=EmailClassificationStatus.PROCESSED,
        classification_id=11, actions=((EmailAction.TRASH,) if trash else
                                      (EmailAction.ARCHIVE, EmailAction.MARK_READ)),
        action_parameters={})
    pending = _classification(status=EmailClassificationStatus.PENDING_FEEDBACK,
        classification_id=12, message_id='pending')
    _old_process(original, '''
import json,sys
from pathlib import Path
from app.store import AutoReplyStore
from app.email_store import EmailStore
from app.email_classifier_contracts import EmailClassification
p=Path(sys.argv[1]); AutoReplyStore(p); e=EmailStore(p)
e.create_account(json.loads(sys.argv[4]))
for value in sys.argv[2:4]:
 e.persist_scan_result(EmailClassification.model_validate_json(value),model_text='fixture features')
with e._connect() as db:
 db.execute("insert into sqlite_sequence(name,seq) values('actions:email_action_attempts',700)")
''', source, classification.model_dump_json(), pending.model_dump_json(),
        json.dumps(_task2_account_values('dingtalk-account', enabled=False)))
    with closing(sqlite3.connect(source)) as first, closing(sqlite3.connect(target)) as second:
        first.backup(second)
    release = prepare_database_schema_release(ROOT, target)
    release.publish()
    email = EmailStore(target)
    email._now = lambda: '2026-10-14T10:00:00+00:00'
    scenario = getattr(scenarios, operation)
    pair = (email, classification, (), source, original)
    expected = scenarios._genuine_old_result(pair, scenario)
    actual = scenarios._normalized(scenario(email, classification))
    assert actual == expected
    current = _facts(target, shared=True)
    assert current == _facts(source, shared=False)
    release.rollback()
    assert _facts(target, shared=False) == current
    _old_process(original, '''
import sys
from pathlib import Path
from app.store import AutoReplyStore
from app.email_store import EmailStore
AutoReplyStore(Path(sys.argv[1])); EmailStore(Path(sys.argv[1]))
''', target)
