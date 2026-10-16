"""Confined schema and ordinary runtime/API proof; no installed source or production write."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile
import time
import sys

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).parent
PYTHON = '/Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python'
PATCHES = ('store-schema-proposal', 'executor-proposal', 'surface-proposal', 'release-proposal', 'executor-test-proposal', 'executor-existing-tests-proposal', 'surface-proposal-tests','api-fixtures-proposal','surface-store-tests-proposal','current-schema-tests-proposal','copy-checker-proposal')
started = time.monotonic()
with tempfile.TemporaryDirectory(prefix='confirmation-coupled-', dir='/tmp') as directory:
    owned = Path(directory)
    for name in ('app','tests','docs','data/config','scripts','frontend'):
        shutil.copytree(ROOT / name, owned / name, ignore=shutil.ignore_patterns('__pycache__','*.pyc','node_modules','dist'))
    shutil.copyfile(ROOT / '.git', owned / '.git')
    for suffix in PATCHES:
        subprocess.run(['git','apply','--whitespace=error-all',str(HERE / ('2026-10-16-confirmation-' + suffix + '.patch'))], cwd=owned, check=True)
    subprocess.run(['git','apply','--include=tests/test_workbench_native_events.py',str(HERE / '2026-10-16-confirmation-executor-task20-fixture-proposal.patch')], cwd=owned, check=True)
    shutil.copyfile(HERE / '2026-10-16-confirmation-release-tests-canonical.py', owned / 'tests/test_confirmation_schema_release.py')
    checker = owned / 'scripts/validate_task20_runtime_copy.py'
    assert '== 62' in checker.read_text()
    env = os.environ | {'PYTHONDONTWRITEBYTECODE':'1', 'PYTHONPATH':str(owned)}
    cases = ['tests/test_task20_current_schema.py','tests/test_task20_schema_release.py','tests/test_task20_current_water_source.py','tests/test_confirmation_schema_release.py','tests/test_workbench_confirmation_retirement_api.py','tests/test_workbench_api.py','tests/test_workbench_executor.py','tests/test_workbench_confirmation_retirement.py','tests/test_workbench_runtime.py','tests/test_workbench_native_events.py']
    # Find the exact new six-test filename supplied by the executor proposal.
    cases = [name for name in cases if (owned/name).exists()]
    cases += [str(p.relative_to(owned)) for p in (owned/'tests').glob('*confirmation*retir*.py') if str(p.relative_to(owned)) not in cases]
    if '--migration-only' in sys.argv:
        cases = ['tests/test_confirmation_schema_release.py','tests/test_task20_current_schema.py','tests/test_task20_schema_release.py','tests/test_task20_current_water_source.py']
    result = subprocess.run([PYTHON,'-B','-m','pytest','-q','-p','no:cacheprovider','--basetemp',str(owned/'pytest-temp'),*cases], cwd=owned, env=env, text=True, capture_output=True)
    print(result.stdout, flush=True)
    print(result.stderr, flush=True)
    if result.returncode:
        raise SystemExit(result.returncode)
    pytest_stdout = result.stdout
    receipt = owned/'receipt.json'
    result = subprocess.run([PYTHON,'-B',str(checker),'--source','/private/tmp/ceo-business-combined-actual-u6i3ldg0/source.sqlite3','--production-root','/Users/derek/Services/ceo-agent-service','--receipt',str(receipt)],cwd=owned,env=env,text=True,capture_output=True)
    print(result.stdout, flush=True)
    print(result.stderr, flush=True)
    if result.returncode:
        raise SystemExit(result.returncode)
    data=json.loads(receipt.read_text())
    data['scope']='Unselected cumulative confirmation candidate in owned temporary code; no installed/production changes.'
    data['focused_pytest_stdout']=pytest_stdout[-1000:]
    data['copy_elapsed_seconds']=data['elapsed_seconds']
    data['elapsed_scope']='whole preflight including focused pytest and copied-database proof'
    data['elapsed_seconds']=round(time.monotonic()-started,3)
    (HERE/'2026-10-16-confirmation-coupled-preflight-result.json').write_text(json.dumps(data,indent=2)+'\n')
