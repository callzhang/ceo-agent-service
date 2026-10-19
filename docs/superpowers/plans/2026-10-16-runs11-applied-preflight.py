"""Check installed unselected foundations; current constructors must stay54."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).parent
PYTHON = '/Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python'
FILES = {
    'app/runs11_definitions.py': 'definition-canonical.py',
    'app/runs11_storage.py': 'storage-canonical.py',
    'app/runs11_schema_release.py': 'release-canonical.py',
    'tests/test_runs11_definitions.py': 'definition-tests.py',
    'tests/test_runs11_storage.py': 'storage-tests.py',
    'tests/test_runs11_schema_release.py': 'release-tests.py',
    'tests/fixtures/runs11-native-source-oracle.json': 'native-source-oracle.json',
}
for target, source in FILES.items():
    assert (ROOT / target).read_bytes() == (HERE / ('2026-10-16-runs11-' + source)).read_bytes(), target
with tempfile.TemporaryDirectory(prefix='runs11-applied-', dir='/tmp') as directory:
    owned = Path(directory)
    env = os.environ | {'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(ROOT),
                        'CEO_WORKER_DB': str(owned / 'current.sqlite3'),
                        'CEO_ENV_FILE': str(owned / 'absent.env'),
                        'CEO_SERVICE_ROOT': str(owned / 'absent-service')}
    result = subprocess.run(
        [PYTHON, '-B', '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
         '--basetemp', str(owned / 'pytest'),
         *(path for path in FILES if path.startswith('tests/test_'))],
        cwd=ROOT, env=env, text=True, capture_output=True,
    )
    print(result.stdout, flush=True)
    print(result.stderr, flush=True)
    if result.returncode:
        raise SystemExit(result.returncode)
    cold = subprocess.run([PYTHON, '-B', '-c', '''
import json,os,sqlite3
from pathlib import Path
from contextlib import closing
from app.store import AutoReplyStore
from app.email_store import EmailStore
path=Path(os.environ['CEO_WORKER_DB'])
AutoReplyStore(path)
EmailStore(path)
with closing(sqlite3.connect(path)) as db:
    count=sum(row[0]=='main' and row[2]=='table' and not row[1].startswith('sqlite_') for row in db.execute('pragma main.table_list'))
    assert count==54
    assert db.execute('select max(version) from email_schema_migrations').fetchone()[0]==52
    assert not db.execute('pragma foreign_key_check').fetchall()
print(json.dumps({'current_store_email_tables':count,'email_ledger':52,'fresh_foreign_keys_clean':True}))
'''], cwd=ROOT, env=env, text=True, capture_output=True, check=True)
    print(cold.stdout, flush=True)
    (HERE / '2026-10-16-runs11-applied-evidence.json').write_text(json.dumps({
        'scope': 'Installed unselected Runs11 foundation only; current constructors remain54. No51 caller/coordinator/current-schema selection or production acceptance.',
        'hashes': {target: hashlib.sha256((ROOT / target).read_bytes()).hexdigest() for target in FILES},
        'pytest': result.stdout.strip(), 'cold_control': json.loads(cold.stdout), 'status': 'passed',
    }, indent=2) + '\n')
