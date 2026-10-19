"""Build a complete unselected paired-closure patch after ten exact proposals."""
from pathlib import Path
import difflib
import hashlib
import json
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).parent
PREFIX = '2026-10-16-task20-paired-closure-proposal'
PATCHES = (
    '2026-10-16-task8-store-caller-proposal.patch',
    '2026-10-16-task8-workbench-caller-proposal.patch',
    '2026-10-16-task12-email-native-caller-proposal.patch',
    '2026-10-16-task12-store-misc-caller-proposal.patch',
    '2026-10-16-task12-feedback-caller-proposal.patch',
    '2026-10-16-task12-workbench-children-caller-proposal.patch',
    '2026-10-16-task20-external-writers-caller-proposal.patch',
    '2026-10-16-task20-console-readers-caller-proposal.patch',
    '2026-10-16-task20-remaining-readers-caller-proposal.patch',
    '2026-10-16-task20-store-constructor-proposal.patch',
)
FILES = ('app/email_store.py', 'app/email_current_schema.py',
         'app/task_schema_release.py', 'app/action_email_schema_release.py')
owned = Path(tempfile.mkdtemp(prefix='task20-paired-proposal-', dir='/tmp')).resolve()


def replace(text, old, new, count=1):
    assert text.count(old) == count, (old[:140], text.count(old), count)
    return text.replace(old, new)


try:
    shutil.copytree(ROOT/'app', owned/'app', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in PATCHES:
        subprocess.run(['git', 'apply', str(HERE/name)], cwd=owned, check=True)
    before = {name: (owned/name).read_text() for name in FILES}
    s = before['app/email_store.py']
    s = replace(s, 'from app.state_definitions import STATE_FIELDS, state_projection',
                'from app.task20_definitions import TASK20_NEW_KINDS\n'
                'from app.task20_current_schema import task20_schema_is_current\n'
                'from app.task8_storage import register_task8_functions\n'
                'from app.state_definitions import STATE_FIELDS, state_projection')
    s = replace(s, '        db.row_factory = sqlite3.Row\n        return db\n',
                '        db.row_factory = sqlite3.Row\n        register_task8_functions(db)\n        return db\n')
    s = replace(s, '        try:\n            if not state_schema_is_current(db):',
                '        try:\n            if not task20_schema_is_current(db):\n'
                '                raise EmailPersistenceCorruption("required Tasks20 declaration is missing or malformed")\n'
                '            if not state_schema_is_current(db):')
    s = replace(s, 'retired_config_tables = {*CONFIG_FIELDS, *STATE_FIELDS, "email_training_snapshot_observations"}',
                'retired_config_tables = {*CONFIG_FIELDS, *STATE_FIELDS, *TASK20_NEW_KINDS, "email_training_snapshot_observations"}')
    (owned/'app/email_store.py').write_text(s)

    s = before['app/email_current_schema.py']
    old = '    from app.state_definitions import STATE_FIELDS\n'
    # Only the runtime factory imports this name locally; source factories stay exact.
    start = s.index('def create_current_email_runtime_schema(')
    s = s[:start] + replace(s[start:], old, old+'    from app.task20_definitions import TASK20_NEW_KINDS\n')
    s = replace(s, '    retired = {*CONFIG_FIELDS, *STATE_FIELDS, "email_training_snapshot_observations"}',
                '    retired = {*CONFIG_FIELDS, *STATE_FIELDS, *TASK20_NEW_KINDS, "email_training_snapshot_observations"}')
    s = replace(s, '    retired_indexes = {\n',
                '    retired_indexes = {\n        "idx_email_agent_classification_tasks_status",\n')
    (owned/'app/email_current_schema.py').write_text(s)

    s = before['app/task_schema_release.py']
    s = replace(s, 'from app.action_storage import action_schema_is_current, reply_schema_is_current\n',
                'from app.action_storage import action_schema_is_current, reply_schema_is_current\n'
                'from app.task20_definitions import task20_projection\n'
                'from app.task20_current_schema import task20_schema_is_current\n')
    s = replace(s, 'if (version == "2026-10-14.1" and reply_schema_is_current(db, include_email=True, include_skill_config_receipts=True)',
                'if (version == "2026-10-14.1" and reply_schema_is_current(db, include_email=True, include_skill_config_receipts=True)\n'
                '            and task20_schema_is_current(db)', count=2)
    s = replace(s, '''        return REPLY_TASK_SQL, "select status from tasks where record_type='work_summary_inputs'"
    raise ValueError("unknown or mixed task publication source schema")''',
                '''        return task20_projection("reply_tasks"), "select status from (" + task20_projection("work_summary_inputs") + ")"
    raise ValueError("unknown or mixed task publication source schema")''')
    s = replace(s, '''        return MEETING_JOB_SQL, MEETING_RUN_SQL
    raise ValueError("unknown or mixed meeting publication source schema")''',
                '''        return task20_projection("meeting_alignment_jobs"), MEETING_RUN_SQL
    raise ValueError("unknown or mixed meeting publication source schema")''')
    (owned/'app/task_schema_release.py').write_text(s)

    s = before['app/action_email_schema_release.py']
    s = replace(s, 'from app.state_storage import update_state_rows\n',
                'from app.state_storage import update_state_rows\n'
                'from app.task20_schema_release import (\n'
                '    capture_task20_source, migrate_task20_schema, restore_task20_schema,\n'
                ')\n'
                'from app.task20_current_schema import (\n'
                '    task20_schema_is_current, task20_feedback_child_is_current,\n'
                ')\n')
    s = replace(s, '        "state": source["state"],\n',
                '        "state": source["state"],\n        "tasks": source["tasks"],\n')
    s = replace(s, '        current["state"] = capture_state_source(db)\n'
                  '        if _inverse_metadata(current) != _inverse_metadata(self.source_schema):',
                '        current["state"] = capture_state_source(db)\n'
                '        current["tasks"] = capture_task20_source(db)\n'
                '        current_declarations = _inverse_metadata(current)\n'
                '        source_declarations = _inverse_metadata(self.source_schema)\n'
                '        # Counter presence is mutable state; retain it for inverse transport,\n'
                '        # but compare only the Tasks declarations after native current writes.\n'
                '        for declarations in (current_declarations, source_declarations):\n'
                '            declarations["tasks"] = {\n'
                '                key: declarations["tasks"][key] for key in ("variant", "tables")\n'
                '            }\n'
                '        if current_declarations != source_declarations:')
    s = replace(s, '    def _verify_target(self, db):\n',
                '    def _verify_target(self, db):\n'
                '        if not task20_schema_is_current(db) or not task20_feedback_child_is_current(db):\n'
                '            raise ValueError("paired Tasks20 target declaration mismatch")\n')
    s = replace(s, '                migrate_state_schema(db, self.source_schema["state"])\n',
                '                migrate_state_schema(db, self.source_schema["state"])\n'
                '                migrate_task20_schema(db, self.source_schema["tasks"])\n')
    s = replace(s, '                restore_state_schema(db, self.source_schema["state"])\n',
                '                restore_task20_schema(db, self.source_schema["tasks"])\n'
                '                restore_state_schema(db, self.source_schema["state"])\n')
    s = replace(s, '        source["state"] = capture_state_source(db)\n',
                '        source["state"] = capture_state_source(db)\n'
                '        source["tasks"] = capture_task20_source(db)\n')
    (owned/'app/action_email_schema_release.py').write_text(s)
    after = {name: (owned/name).read_text() for name in FILES}
    patch = ''.join(''.join(difflib.unified_diff(before[name].splitlines(True), after[name].splitlines(True),
                     fromfile='a/'+name, tofile='b/'+name)) for name in FILES)
    (HERE/(PREFIX+'.patch')).write_text(patch)
    subprocess.run(['git', 'apply', '--reverse', str(HERE/(PREFIX+'.patch'))], cwd=owned, check=True)
    assert all((owned/name).read_text() == before[name] for name in FILES)
    subprocess.run(['git', 'apply', str(HERE/(PREFIX+'.patch'))], cwd=owned, check=True)
    assert all((owned/name).read_text() == after[name] for name in FILES)
    for name in FILES:
        compile(after[name], name, 'exec')
    subprocess.run(['/Users/derek/.cache/uv/archive-v0/1vqK0HG0A7DbkHHlOjztN/bin/ruff',
                    'check', '--no-cache', *FILES], cwd=owned, check=True)
    evidence = {
        'scope': 'Complete unselected four-file candidate after nine callers and Store constructor; no DB or runtime selection',
        'prerequisite_patch_hashes': {name: hashlib.sha256((HERE/name).read_bytes()).hexdigest() for name in PATCHES},
        'files': {name: {'before': hashlib.sha256(before[name].encode()).hexdigest(),
                         'after': hashlib.sha256(after[name].encode()).hexdigest()} for name in FILES},
        'patch_sha256': hashlib.sha256(patch.encode()).hexdigest(),
        'compile_ruff_apply_reverse_reapply': 'pass',
    }
    (HERE/(PREFIX+'-analysis.json')).write_text(json.dumps(evidence, indent=2)+'\n')
    print(json.dumps(evidence, indent=2))
finally:
    shutil.rmtree(owned)
    print('OWNED_TEMP_REMOVED', not owned.exists())
