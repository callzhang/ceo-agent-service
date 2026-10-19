"""Apply exact allocator qualification in disposable code; never select runtime."""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).parent
PYTHON='/Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',action='store_true')
    args=parser.parse_args()
    owned=Path(tempfile.mkdtemp(prefix='task20-main-sequence-',dir='/tmp')).resolve()
    try:
        shutil.copytree(ROOT/'app',owned/'app',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        shutil.copytree(ROOT/'tests',owned/'tests',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        shutil.copytree(ROOT/'docs/superpowers/specs',owned/'docs/superpowers/specs')
        target=owned/'docs/superpowers/plans'
        target.mkdir(parents=True)
        for name in ('2026-10-16-task20-main-sequence-regression.py','2026-10-16-task20-release-regression.py','2026-10-16-task20-release-candidate.py'):
            shutil.copy2(HERE/name,target/name)
        if not args.source:
            for src,dst in [('2026-10-16-task20-main-sequence-definitions-candidate.py','task20_definitions.py'),('2026-10-16-task20-main-sequence-storage-candidate.py','task12_storage.py')]:
                shutil.copy2(HERE/src,owned/'app'/dst)
                assert hashlib.sha256((HERE/src).read_bytes()).digest()==hashlib.sha256((owned/'app'/dst).read_bytes()).digest()
        for config in ('pyproject.toml','pytest.ini'):
            if (ROOT/config).exists():
                shutil.copy2(ROOT/config,owned/config)
        result=subprocess.run([PYTHON,'-B','-m','pytest','-q','-p','no:cacheprovider',
                    str(target/'2026-10-16-task20-main-sequence-regression.py'),
                    *([] if args.source else [str(target/'2026-10-16-task20-release-regression.py')])],cwd=owned,env=os.environ|{'PYTHONDONTWRITEBYTECODE':'1'})
        return result.returncode
    finally:
        shutil.rmtree(owned)
        print('OWNED_TEMP_REMOVED',not owned.exists(),flush=True)


if __name__=='__main__':
    raise SystemExit(main())
