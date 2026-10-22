"""Exercise complete prepared-declaration component on a disposable candidate."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).parent
PYTHON='/Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python'
owned=Path(tempfile.mkdtemp(prefix='task20-current-water-',dir='/tmp')).resolve()
try:
    shutil.copytree(ROOT/'app',owned/'app',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copytree(ROOT/'tests',owned/'tests',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copytree(ROOT/'docs/superpowers/specs',owned/'docs/superpowers/specs')
    target=owned/'docs/superpowers/plans'
    target.mkdir(parents=True)
    for name in ('2026-10-16-task20-current-water-source-candidate.py','2026-10-16-task20-current-water-source-regression.py'):
        shutil.copy2(HERE/name,target/name)
    shutil.copy2(HERE/'2026-10-16-task20-current-water-source-candidate.py',owned/'app/task20_schema_release.py')
    for config in ('pyproject.toml','pytest.ini'):
        if (ROOT/config).exists():
            shutil.copy2(ROOT/config,owned/config)
    result=subprocess.run([PYTHON,'-B','-m','pytest','-q','-p','no:cacheprovider',
                           'tests/test_task20_main_sequence.py','tests/test_task20_schema_release.py',
                           str(target/'2026-10-16-task20-current-water-source-regression.py')],cwd=owned,
                          env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','TASK20_ORIGINAL_SOURCE':'0'})
finally:
    shutil.rmtree(owned)
    print('OWNED_TEMP_REMOVED',not owned.exists(),flush=True)
raise SystemExit(result.returncode)
