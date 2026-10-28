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
from app.shared_action_definitions import email_projection
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


CANDIDATE_ACTION_ATTEMPT_SQL = """select r.id,a.id as action_owner_id,r.execution_id,r.action_index,
       a.action_external_action_key as external_action_key,r.status,
       coalesce(r.outcome_json,p.provider_result_json,'{}') as result_json,
       r.agent_run_id,r.provider_receipt_id,r.target,
       r.created_at,r.updated_at
from action_receipts r join actions a on a.id=r.action_id
left join action_receipts p on p.id=r.provider_receipt_id and p.action_id=r.action_id
where a.record_type='action' and a.action_external_action_key is not null
  and r.execution_id is not null and r.action_index is not null
  and r.id=(select max(latest.id) from action_receipts latest
            where latest.action_id=r.action_id and latest.execution_id=r.execution_id
              and latest.action_index=r.action_index)"""

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
            reviewed_attempts=scalar(CANDIDATE_ACTION_ATTEMPT_SQL + ' order by id'),
            executions=scalar(actions.CANDIDATE_EXECUTION_SQL + ' order by id'),
            ids=scalar('select id,legacy_key,record_type from actions order by id'),
            waters=scalar('select rowid,name,seq from sqlite_sequence order by rowid'),
            fks=sorted(scalar('pragma foreign_key_check')),
            email_markers=scalar('select * from email_schema_migrations order by version'),
        )

