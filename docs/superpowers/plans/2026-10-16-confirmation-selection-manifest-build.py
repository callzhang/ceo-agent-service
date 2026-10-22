"""Freeze the exact complete candidate without editing installed source."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).parent
SUFFIXES = ('store-schema-proposal','executor-proposal','surface-proposal','release-proposal',
            'executor-test-proposal','executor-existing-tests-proposal','surface-proposal-tests',
            'api-fixtures-proposal','surface-store-tests-proposal','current-schema-tests-proposal',
            'copy-checker-proposal','behavior-docs-proposal')
NATIVE = ('app/task_storage.py','app/workbench_storage.py','app/agent_cli.py',
          'app/workbench/service_runtime.py','app/native_cli_metadata.py')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


with tempfile.TemporaryDirectory(prefix='confirmation-manifest-',dir='/tmp') as directory:
    owned=Path(directory)
    for name in ('app','tests','docs','frontend','scripts'):
        shutil.copytree(ROOT/name,owned/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc','node_modules','dist'))
    patches=[]
    files=set()
    for suffix in SUFFIXES:
        path=HERE/('2026-10-16-confirmation-'+suffix+'.patch')
        files.update(line[6:] for line in path.read_text().splitlines() if line.startswith(('+++ b/','--- a/')))
        patches.append({'path':str(path.relative_to(ROOT)),'sha256':digest(path)})
        subprocess.run(['git','apply','--whitespace=error-all',str(path)],cwd=owned,check=True)
    path=HERE/'2026-10-16-confirmation-executor-task20-fixture-proposal.patch'
    subprocess.run(['git','apply','--include=tests/test_workbench_native_events.py',str(path)],cwd=owned,check=True)
    patches.append({'path':str(path.relative_to(ROOT)),'sha256':digest(path),'include':'tests/test_workbench_native_events.py'})
    files.add('tests/test_workbench_native_events.py')
    canonical=HERE/'2026-10-16-confirmation-release-tests-canonical.py'
    test='tests/test_confirmation_schema_release.py'
    shutil.copyfile(canonical,owned/test)
    files.add(test)
    for name in NATIVE:
        assert (ROOT/name).is_file(), name
        assert digest(ROOT/name)==digest(owned/name)
    for name in sorted(files):
        if name.endswith('.py') and (owned/name).exists():
            compile((owned/name).read_text(),name,'exec')
    files=sorted(files)
    manifest={'scope':'Exact mechanical selection only; no main/push/production/deploy.',
              'patches':patches,'copies':[{'from':str(canonical.relative_to(ROOT)),'to':test,'sha256':digest(canonical)}],
              'files':{name:{'before':digest(ROOT/name),'after':digest(owned/name)} for name in files},
              'unchanged_native':{name:digest(ROOT/name) for name in NATIVE},
              'known_test_boundary':'Full retained Store module47pass3preexisting unsupported hybrid source13 fixture failures, unchanged assertions; API52pass; canonical migrated315pass; independent quality201+301 backend and135frontend/build pass; actualcopy receipt separate.'}
    (HERE/'2026-10-16-confirmation-selection-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({'files':len(files),'patches':len(patches),'compile':'passed'}))
