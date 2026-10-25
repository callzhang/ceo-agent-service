"""Archived a76dc8072 source, current post-forward facts, inverse and old cold open."""
from contextlib import closing
import inspect
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import tarfile

import pytest

from app import action_storage as actions
from app.email_classifier_contracts import EmailAction, EmailClassificationStatus
from app.email_store import EmailStore
from app.shared_action_definitions import email_projection
from app.store_schema_release import prepare_database_schema_release
from tests.test_shared_action_release import _old_process
from tests.test_email_action_reply_dao import _reply_arguments, _receipt_arguments
from tests.test_email_action_unsubscribe_dao import _authorization
from tests.test_email_store import _classification, _task2_account_values

ROOT = Path(__file__).resolve().parents[1]
SOURCE_HEAD = 'a76dc8072e5bc19f46310c721375002ec16d031f'


@pytest.fixture(scope='module')
def old_source(tmp_path_factory):
    root = tmp_path_factory.mktemp('actual-source19-6')
    archive = subprocess.check_output(['git', 'archive', SOURCE_HEAD, 'app', 'tests', 'scripts'], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(root, filter='data')
    return root


def _seed(old, path, *, origin='unsubscribe', state='empty'):
    action = EmailAction.AUTO_REPLY if origin == 'reply' else EmailAction.UNSUBSCRIBE
    classification = _classification(status=EmailClassificationStatus.PROCESSED,
        classification_id=11, actions=(action,),
        action_parameters={EmailAction.AUTO_REPLY: {'instruction':'Acknowledge receipt.'}} if origin == 'reply' else {},
        thread_id='thread-unsubscribe-41')
    helpers = '\n'.join(inspect.getsource(f) for f in (_reply_arguments, _receipt_arguments, _authorization))
    _old_process(old, helpers + '''
import json,sys
from pathlib import Path
from app.store import AutoReplyStore
from app.email_store import EmailStore
from app.email_classifier_contracts import EmailClassification
from app.action_storage import update_email_action
p=Path(sys.argv[1]); AutoReplyStore(p); e=EmailStore(p)
e.create_account(json.loads(sys.argv[3]))
c=EmailClassification.model_validate_json(sys.argv[2]);e.persist_scan_result(c,model_text='fixture features')
e._now=lambda:'2026-10-14T10:00:00+00:00'
if sys.argv[4]=='reply' and sys.argv[5]=='retryable':
 a,o=_reply_arguments(e,c); e.claim_email_reply_dispatch(**a,owner=o)
 e.release_email_reply_dispatch_retryable(a['action_identity'],owner=o)
 with e._connect() as db:
  update_email_action(db,'email_reply_dispatch_claims',1,{},new_source_rowid=-7)
if sys.argv[4]=='unsubscribe' and sys.argv[5]=='claimed':
 a,o=_authorization(c);e.claim_email_unsubscribe_write(**a,owner=o)
 with e._connect() as db:
  update_email_action(db,'email_unsubscribe_claims',1,{},new_source_rowid=-7)
  db.execute("insert into sqlite_sequence(name,seq) values('actions:email_unsubscribe_steps',700)")
if sys.argv[4]=='unsubscribe' and sys.argv[5]=='task':
 a,o=_authorization(c)
 payload={k:v for k,v in a.items() if k not in ('operations','effect_digest','entry_reference')}
 payload.update(schema='email_agent_action.v1',lifecycle_version='email_unsubscribe_audited_v2',action_type='unsubscribe',
  unsubscribe_entries=[dict(index=0,source='header_https',digest='b'*64,reference=a['entry_reference'])],unsubscribe_authentication=None)
 from app.email_task_adapter import email_conversation_id
 t=AutoReplyStore(p)
 task=t.ensure_reply_task(channel='email',conversation_id=email_conversation_id(a['account_id'],a['thread_identity']),conversation_title='Email',single_chat=True,
  trigger_message_id=a['action_identity'],trigger_create_time='2026-10-14T09:00:00+00:00',trigger_sender='sender@example.com',
  trigger_text='unsubscribe',trigger_message_json=json.dumps(payload),execution_generation='generation-explicit-retry')
 task=t.claim_reply_task(task.id)
 t.fail_reply_task(task.id,'original-browser-failure',expected_execution_generation=task.execution_generation)
''', path, classification.model_dump_json(), json.dumps(_task2_account_values('dingtalk-account', enabled=True)), origin, state)
    return classification


def _facts(path):
    from app.action_email_storage import _columns, _quote
    with closing(sqlite3.connect(path)) as db:
        columns = tuple(name for name in _columns(db, 'actions') if name != '_kind')
        names = ','.join(map(_quote, columns)) + ',record_type'
        names += ',' + ','.join('typeof(' + _quote(name) + ')' for name in columns)
        def rows(sql):return [tuple(row) for row in db.execute(sql)]
        public = {}
        for kind, fields in actions.EMAIL_FIELDS.items():
            include_rowid = kind in actions.EMAIL_ROWID_TYPES
            columns = ('rowid', *fields) if include_rowid else fields
            selection = ','.join(columns) + ',' + ','.join('typeof(' + name + ')' for name in columns)
            public[kind] = rows('select ' + selection + ' from (' + email_projection(kind, include_rowid=include_rowid) + ') order by ' + ','.join(columns))
        return dict(actions=rows('select '+names+' from actions order by id'), public=public,
            emails=rows('select * from emails order by id'),
            waters=rows('select rowid,name,seq from sqlite_sequence order by rowid'),
            fks=sorted(rows('pragma foreign_key_check')),
            email_markers=rows('select * from email_schema_migrations order by version'))


def _cold(old, path):
    _old_process(old, '''
import sys
from pathlib import Path
from app.store import AutoReplyStore
from app.email_store import EmailStore
AutoReplyStore(Path(sys.argv[1]));EmailStore(Path(sys.argv[1]))
''', path)


def _unfinished_continuation_cycle(store, classification):
    a, owner = _authorization(classification)
    first = store.claim_email_unsubscribe_write(**a, owner=owner)
    continuation = store.persist_email_unsubscribe_continuation(**a, owner=owner,
        observation_reference='unsubscribe-state:'+'d'*64,
        controls=[dict(reference='unsubscribe-control:'+'c'*64, kind='button', intent='confirm')],
        final_step=dict(sequence=1, operation='open_entry', state='action_required', reference='observation'))
    assert store.get_email_unsubscribe_claim(a['action_identity'])['status'] == 'awaiting_audit'
    assert store.get_email_unsubscribe_receipt(a['action_identity']) is None
    return first, continuation, store.get_email_unsubscribe_state_snapshot(a['action_identity'])


@pytest.mark.parametrize('origin,state,scenario', [
    ('reply', 'empty', '_claim_owner_cycle'),
    ('reply', 'empty', '_receipt_cycle'),
    ('reply', 'retryable', '_retry_snapshot_cycle'),
    ('unsubscribe', 'claimed', '_continuation_terminal_cycle'),
    ('unsubscribe', 'claimed', '_release_recovery_cycle'),
    ('unsubscribe', 'empty', '_unfinished_continuation_cycle'),
    ('unsubscribe', 'task', '_cancellation_cycle'),
])
def test_current_dao_source_parity_inverse_old_cold_reforward(old_source, tmp_path, origin, state, scenario):
    from tests import test_email_action_reply_dao as reply
    from tests import test_email_action_unsubscribe_dao as unsub
    from tests.test_email_action_plan_direct_dao import _normalized
    source, target = tmp_path / 'source.sqlite3', tmp_path / 'target.sqlite3'
    classification = _seed(old_source, source, origin=origin, state=state)
    with closing(sqlite3.connect(source)) as first, closing(sqlite3.connect(target)) as second:
        first.backup(second)
    before = _facts(source)
    release = prepare_database_schema_release(ROOT, target)
    assert type(release).__name__ == 'SpecializedActionSchemaRelease'
    release.publish()
    assert _facts(target) == before
    email = EmailStore(target)
    email._now = lambda: '2026-10-14T10:00:00+00:00'
    module = reply if origin == 'reply' else unsub
    operation = _unfinished_continuation_cycle if scenario == '_unfinished_continuation_cycle' else getattr(module, scenario)
    expected = module._old_result((email, classification, None, source, old_source), operation)
    assert _normalized(operation(email, classification)) == expected
    current = _facts(target)
    assert current == _facts(source)
    release.verify_loaded()
    release.rollback()
    assert _facts(target) == current
    _cold(old_source, target)
    next_release = prepare_database_schema_release(ROOT, target)
    next_release.publish()
    next_release.finalize()
    assert _facts(target) == current
    assert prepare_database_schema_release(ROOT, target) is None


