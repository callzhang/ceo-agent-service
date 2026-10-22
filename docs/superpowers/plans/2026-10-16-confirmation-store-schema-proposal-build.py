"""Prepare the complete current Store/schema cancellation in an owned copy."""
import ast
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).parent
PREFIX='2026-10-16-confirmation-store-schema-proposal'
FILES=('app/store.py','app/workbench/store.py','app/workbench/__init__.py',
       'app/task20_current_schema.py','app/task20_definitions.py')


def replace(text,old,new,count=1):
    assert text.count(old)==count,(old[:120],text.count(old),count)
    return text.replace(old,new)


def remove_methods(text,names):
    lines=text.splitlines(True)
    found=[]
    for node in ast.walk(ast.parse(text)):
        if isinstance(node,ast.FunctionDef) and node.name in names:
            start=min([node.lineno,*[d.lineno for d in node.decorator_list]])
            found.append((start,node.end_lineno,node.name))
    assert {name for _,_,name in found}==set(names)
    for start,end,_ in sorted(found,reverse=True):
        del lines[start-1:end]
    return ''.join(lines)


with tempfile.TemporaryDirectory(prefix='confirmation-store-proposal-',dir='/tmp') as tmp:
    owned=Path(tmp)
    shutil.copytree(ROOT/'app',owned/'app',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    before={n:(owned/n).read_text() for n in FILES}
    s=before['app/store.py']
    s=remove_methods(s,{'_reconcile_legacy_workbench_confirmations'})
    s=replace(s,'            self._reconcile_legacy_workbench_confirmations(db)\n','')
    s=replace(s,'    "workbench_confirmations",\n','')
    s=replace(s,'    "idx_workbench_confirmations_turn_created_id",\n','')
    s=replace(s,'STORE_SCHEMA_REMOVED_TABLES = (\n','STORE_SCHEMA_REMOVED_TABLES = (\n    "workbench_confirmations",\n')
    (owned/'app/store.py').write_text(s)

    s=before['app/workbench/__init__.py']
    for word in ('ConfirmationStatus','WorkbenchConfirmation'):
        s=replace(s,'    '+word+',\n','')
        s=replace(s,'    "'+word+'",\n','')
    (owned/'app/workbench/__init__.py').write_text(s)

    s=before['app/task20_current_schema.py']
    s=replace(s,'    SOURCE_DECLARATIONS, RETIRED_WORKBENCH_TABLES, _child_ddl,\n','    RETIRED_WORKBENCH_TABLES,\n')
    s=replace(s,'*TASK20_NEW_KINDS)))','*TASK20_NEW_KINDS, "workbench_confirmations")))')
    s=replace(s,'RETAINED_WORKBENCH_CHILDREN = ("workbench_confirmations",)\n','')
    s=replace(s,'''                } else "workbench_turns" if table == "workbench_confirmations"
                else "reply_tasks"''','''                } else "reply_tasks"''')
    s=replace(s,'"""Retain confirmation/search auxiliaries without the two folded child tables."""',
                '"""Retain search auxiliaries; no folded children or legacy confirmations."""')
    start=s.index('    for table in RETAINED_WORKBENCH_CHILDREN:')
    end=s.index('    db.execute(SEARCH_TABLE_DDL',start)
    s=s[:start]+s[end:]
    s=replace(s,'"tasks","feedback_iteration_decision_items", *RETAINED_WORKBENCH_CHILDREN,','"tasks","feedback_iteration_decision_items",')
    (owned/'app/task20_current_schema.py').write_text(s)

    s=before['app/task20_definitions.py']
    s=replace(s,"('queued','running','waiting_confirmation','completed','stopped','failed')", "('queued','running','completed','stopped','failed')")
    s=replace(s,"('queued','running','waiting_confirmation')","('queued','running')")
    (owned/'app/task20_definitions.py').write_text(s)

    s=before['app/workbench/store.py']
    # This contiguous block contains only legacy confirmation CRUD/recovery.
    tree=ast.parse(s)
    names={n.name for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and
           1307<=n.lineno<=2095}
    names.update({'_resolve_pending_confirmations','_require_confirmation','_confirmation_from_row'})
    s=remove_methods(s,names)
    for name in ('ConfirmationStatus','WorkbenchConfirmation'):
        s=replace(s,'    '+name+',\n','')
    s=replace(s,'        TurnStatus.WAITING_CONFIRMATION,\n','')
    s=replace(s,'''    TurnStatus.WAITING_CONFIRMATION: {
        TurnStatus.QUEUED,
        TurnStatus.STOPPED,
        TurnStatus.FAILED,
    },
''','')
    s=replace(s,'WORKBENCH_RECOVERY_BATCH_LIMIT = 100\n','')
    start=s.index('WORKBENCH_CONFIRMATION_SQL = ')
    end=s.index('def _json_object_text(',start)
    s=s[:start]+s[end:]
    s=s.replace("'queued', 'running', 'waiting_confirmation'","'queued', 'running'")
    start=s.index('                confirmation = db.execute(',s.index('    def execution_run_id_for_executor('))
    end=s.index('            return str(turn["execution_run_id"] or "")',start)
    s=s[:start]+'                raise ValueError("executor does not own runtime run")\n'+s[end:]
    start=s.index('                  and not exists (',s.index('    def claim_next_turn('))
    end=s.index('                order by workbench_turns.created_at',start)
    s=s[:start]+s[end:]
    start=s.index('            self._resolve_pending_confirmations(',s.index('    def request_stop('))
    end=s.index('            self._transition_turn(',start)
    s=s[:start]+s[end:]
    s=replace(s,'        confirmation_after: tuple[str, str] | None = None,\n','')
    s=replace(s,'        list[WorkbenchConfirmation],\n','')
    start=s.index('                confirmation_cursor_sql = ',s.index('    def timeline_snapshot('))
    end=s.index('            else:\n',start)
    s=s[:start]+s[end:]
    s=replace(s,'event_rows, artifact_rows, confirmation_rows = [], [], []','event_rows, artifact_rows = [], []')
    for line in ('            confirmations_has_more = len(confirmation_rows) > 100\n',
                 '            confirmation_rows = confirmation_rows[:100]\n',
                 '                [self._confirmation_from_row(row) for row in confirmation_rows],\n',
                 '                    "confirmations_has_more": confirmations_has_more,\n',
                 '                    "confirmation_quiescence": confirmation_quiescence,\n'):
        s=replace(s,line,'')
    start=s.index('            runtime_quiesced_runs = ',s.index('    def timeline_snapshot('))
    end=s.index('            next_cursor = ',start)
    s=s[:start]+s[end:]
    start=s.index('            confirmation_counts = ',s.index('    def workbench_stats('))
    end=s.index('            event_counts = ',start)
    s=s[:start]+s[end:]
    s=replace(s,'            "confirmations": confirmation_counts,\n','')
    start=s.index('            if (current, target_status) in {',s.index('    def complete_turn('))
    end=s.index('            if target_status not in _TURN_TRANSITIONS[current]:',start)
    s=s[:start]+s[end:]
    start=s.index('                if current is TurnStatus.WAITING_CONFIRMATION:',s.index('    def complete_turn('))
    end=s.index('                self._append_control_event(',start)
    s=s[:start]+s[end:]
    assert 'confirmation' not in s.lower()
    (owned/'app/workbench/store.py').write_text(s)

    after={n:(owned/n).read_text() for n in FILES}
    for n in FILES:
        compile(after[n],n,'exec')
    patch=''.join(''.join(difflib.unified_diff(before[n].splitlines(True),after[n].splitlines(True),
                           fromfile='a/'+n,tofile='b/'+n)) for n in FILES)
    out=HERE/(PREFIX+'.patch')
    out.write_text(patch)
    subprocess.run(['git','apply','--reverse',str(out)],cwd=owned,check=True)
    assert all((owned/n).read_text()==before[n] for n in FILES)
    subprocess.run(['git','apply',str(out)],cwd=owned,check=True)
    assert all((owned/n).read_text()==after[n] for n in FILES)
    subprocess.run(['/Users/derek/.cache/uv/archive-v0/1vqK0HG0A7DbkHHlOjztN/bin/ruff','check','--no-cache',*FILES],cwd=owned,check=True)
    report={'scope':'Complete unselected Store/schema/facade proposal only; native source factories unchanged',
            'patch_sha256':hashlib.sha256(patch.encode()).hexdigest(),
            'files':{n:{'before':hashlib.sha256(before[n].encode()).hexdigest(),
                        'after':hashlib.sha256(after[n].encode()).hexdigest()} for n in FILES},
            'retired_workbench_methods':sorted(names),
            'compile_ruff_reverse_reapply':'pass',
            'unproved':'Combined model/executor/API/frontend and source-bound migration/cold/public/copy proofs required before application.'}
    (HERE/(PREFIX+'-analysis.json')).write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
print('OWNED_TEMP_REMOVED',not owned.exists())
