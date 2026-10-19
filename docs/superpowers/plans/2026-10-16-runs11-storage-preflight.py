"""Run the unselected Runs11 declaration/writer native differential in isolation."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).parent
PYTHON='/Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python'
FILES={'app/runs11_definitions.py':'definition-canonical.py',
       'app/runs11_storage.py':'storage-canonical.py',
       'tests/test_runs11_definitions.py':'definition-tests.py',
       'tests/test_runs11_storage.py':'storage-tests.py'}
with tempfile.TemporaryDirectory(prefix='runs11-storage-',dir='/tmp') as directory:
    owned=Path(directory)
    shutil.copytree(ROOT/'app',owned/'app',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    (owned/'tests').mkdir()
    (owned/'tests/fixtures').mkdir()
    shutil.copyfile(HERE/'2026-10-16-runs11-native-source-oracle.json', owned/'tests/fixtures/runs11-native-source-oracle.json')
    for target,source in FILES.items():
        shutil.copyfile(HERE/('2026-10-16-runs11-'+source),owned/target)
    env=os.environ|{'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(owned)}
    result=subprocess.run([PYTHON,'-B','-m','pytest','-q','-p','no:cacheprovider','--basetemp',str(owned/'pytest'),
                           'tests/test_runs11_definitions.py','tests/test_runs11_storage.py'],cwd=owned,env=env,text=True,capture_output=True)
    print(result.stdout,flush=True)
    print(result.stderr,flush=True)
    if result.returncode:
        raise SystemExit(result.returncode)
    (HERE/'2026-10-16-runs11-storage-evidence.json').write_text(json.dumps({
        'scope':'Unselected declarations and new-source writer native actual/fresh differential only; no migration/callers/constructor/production acceptance.',
        'hashes':{target:hashlib.sha256((owned/target).read_bytes()).hexdigest() for target in FILES},
        'pytest':result.stdout.strip(),'status':'passed'},indent=2)+'\n')
