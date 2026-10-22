"""Exercise complete unselected current constructors in an owned code/DB copy."""
import argparse
import hashlib
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).parent
PYTHON='/Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python'
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--actual',action='store_true')
parser.add_argument('--tasks-writes',action='store_true')
parser.add_argument('--receipt',type=Path)
parser.add_argument('--focused',action='store_true')
args=parser.parse_args()
ns=runpy.run_path(str(HERE/'2026-10-16-task20-paired-closure-proposal-build.py'))
owned=Path(tempfile.mkdtemp(prefix='task20-coupled-preflight-',dir='/tmp')).resolve()
try:
    for name in ('app','tests','data/config','docs/superpowers/specs'):
        shutil.copytree(ROOT/name,owned/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for name in ns['PATCHES']+('2026-10-16-task20-paired-closure-proposal.patch',):
        subprocess.run(['git','apply',str(HERE/name)],cwd=owned,check=True)
    shutil.copy2(HERE/'2026-10-16-task20-current-water-source-candidate.py',owned/'app/task20_schema_release.py')
    for config in ('pyproject.toml','pytest.ini'):
        if (ROOT/config).exists():
            shutil.copy2(ROOT/config,owned/config)
    environment={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','CEO_WORKER_DB':str(owned/'fresh.sqlite3'),
                 'CEO_ENV_FILE':str(owned/'absent.env'),'CEO_SERVICE_ROOT':str(owned/'absent-service'),
                 'PYTHONPATH':str(owned)}
    code='''
from pathlib import Path
import sqlite3,json,sys
from app.store import AutoReplyStore
from app.email_store import EmailStore
from app.workbench.store import WorkbenchStore
from app.task20_current_schema import task20_schema_is_current,task20_feedback_child_is_current
from app.task_schema_release import publication_task_sources,publication_meeting_sources
p=Path(sys.argv[1])
s=AutoReplyStore(p)
e=EmailStore(p)
w=WorkbenchStore(p)
with sqlite3.connect(p) as db:
    assert task20_schema_is_current(db)
    assert task20_feedback_child_is_current(db)
    for sql in (*publication_task_sources(db),*publication_meeting_sources(db)):
        db.execute('select * from ('+sql+') limit 1').fetchall()
    assert db.execute('pragma quick_check').fetchall()==[('ok',)]
    assert db.execute('pragma foreign_key_check').fetchall()==[]
    tables=sorted(r[1] for r in db.execute('pragma main.table_list') if r[2]=='table' and not r[1].startswith('sqlite_'))
print(json.dumps({'fresh_cold_three_constructors':True,'ordinary_tables':len(tables),'tables':tables}))
'''
    subprocess.run([PYTHON,'-B','-c',code,str(owned/'fresh.sqlite3')],cwd=owned,env=environment,check=True)
    subprocess.run([PYTHON,'-B','-c',code,str(owned/'fresh.sqlite3')],cwd=owned,env=environment,check=True)
    if args.focused:
        subprocess.run(['git','apply',str(HERE/'2026-10-16-task20-feedback-e2e-fixture-proposal.patch')],cwd=owned,check=True)
        subprocess.run(['git','apply',str(HERE/'2026-10-16-task20-workbench-private-fixture-proposal.patch')],cwd=owned,check=True)
        subprocess.run([PYTHON,'-B','-m','pytest','-q','-p','no:cacheprovider',
                        '--basetemp',str(owned/'pytest'),
                        'tests/test_task_business_resolution.py',
                        'tests/test_feedback_processing_e2e.py',
                        'tests/test_workbench_store.py::test_public_turn_excludes_private_resume_context',
                        'tests/test_workbench_store.py::test_create_turn_persists_initial_status_event_and_lists_nested_resources',
                        'tests/test_workbench_store.py::test_concurrent_turn_creation_allocates_unique_monotonic_task_sequences'],
                       cwd=owned,env=environment,check=True)
    if args.actual:
        source=Path('/private/tmp/ceo-business-combined-actual-u6i3ldg0/source.sqlite3')
        with source.open('rb') as stream:
            source_hash=hashlib.file_digest(stream,'sha256').hexdigest()
        assert source_hash=='970a909a9cfcb4fe604d2111d5572560d4744978df20fb317c4e433059566320'
        target=owned/'scripts/validate_state_runtime_copy.py'
        target.parent.mkdir()
        if args.tasks_writes:
            text=(HERE/'2026-10-16-task20-paired-actual-copy-candidate.py').read_text()
        else:
            text=(ROOT/'scripts/validate_state_runtime_copy.py').read_text()
            original='assert len(before["ordinary_tables"]) - len(target["ordinary_tables"]) == 49'
            assert text.count(original)==1
            text=text.replace(original,original.replace('49','61'))
            original='candidate = run(["git", "rev-parse", "HEAD"], root=root)'
            assert text.count(original)==1
            text=text.replace(original,'candidate = os.environ["TASK20_PROPOSAL_BASE"]')
        target.write_text(text)
        environment['TASK20_PROPOSAL_BASE']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
        receipt_args=['--receipt',str(args.receipt.resolve())] if args.receipt else []
        subprocess.run([PYTHON,'-B',str(target),
                        '--source',str(source),
                        '--production-root','/Users/derek/Services/ceo-agent-service',*receipt_args],
                       cwd=owned,env=environment,check=True)
        with source.open('rb') as stream:
            assert hashlib.file_digest(stream,'sha256').hexdigest()==source_hash
finally:
    shutil.rmtree(owned)
    print('OWNED_TEMP_REMOVED',not owned.exists(),flush=True)
