"""Build complete coordinator integration in an owned source copy."""
from pathlib import Path
import difflib
import hashlib
import json
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).parent
PREFIX = '2026-10-16-confirmation-release-proposal'


def replace(text, old, new, count=1):
    assert text.count(old) == count, (old, text.count(old))
    return text.replace(old, new)


with tempfile.TemporaryDirectory(prefix='confirmation-release-', dir='/tmp') as tmp:
    owned = Path(tmp)
    shutil.copytree(ROOT / 'app', owned / 'app', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    subprocess.run(['git', 'apply', str(HERE / '2026-10-16-confirmation-store-schema-proposal.patch')], cwd=owned, check=True)
    before = {name: (ROOT / name).read_text() for name in ('app/action_email_schema_release.py', 'app/task20_schema_release.py')}
    source = before['app/action_email_schema_release.py']
    source = replace(source, 'from app.task20_schema_release import (', 'from app.confirmation_schema_release import (\n    capture_confirmation_source, retire_confirmation_schema,\n    restore_confirmation_schema, verify_confirmation_retired,\n)\nfrom app.task20_schema_release import (')
    source = replace(source, '        "tasks": source["tasks"],', '        "tasks": source["tasks"],\n        "confirmation": source["confirmation"],')
    source = replace(source, '        current["tasks"] = capture_task20_source(db)', '        current["confirmation"] = capture_confirmation_source(db)\n        current["tasks"] = capture_task20_source(db)')
    source = replace(source, '    def _verify_target(self, db):\n', '    def _verify_target(self, db):\n        verify_confirmation_retired(db)\n')
    source = replace(source, '                migrate_task20_schema(db, self.source_schema["tasks"])', '                migrate_task20_schema(db, self.source_schema["tasks"])\n                retire_confirmation_schema(db, self.source_schema["confirmation"])')
    source = replace(source, '                restore_task20_schema(db, self.source_schema["tasks"])', '                restore_confirmation_schema(db, self.source_schema["confirmation"])\n                restore_task20_schema(db, self.source_schema["tasks"])')
    source = replace(source, '        source["tasks"] = capture_task20_source(db)', '        source["confirmation"] = capture_confirmation_source(db)\n        source["tasks"] = capture_task20_source(db)')
    (owned / 'app/action_email_schema_release.py').write_text(source)
    # Derive the exact new Tasks declaration from the proposed current factory.
    driver = '''import sqlite3\nfrom app.task20_definitions import create_task20_schema\nfrom app.task20_schema_release import _declaration\nfrom app.action_reply_storage import _fingerprint\nwith sqlite3.connect(':memory:') as db:\n create_task20_schema(db)\n print(_fingerprint(_declaration(db,'tasks')))\n'''
    env = __import__('os').environ | {'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(owned)}
    result = subprocess.run(['/Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python', '-B', '-c', driver], cwd=owned, env=env, text=True, capture_output=True, check=True)
    target_hash = result.stdout.strip()
    source = replace(before['app/task20_schema_release.py'], "TARGET_TASKS_HASH = '7606f44ab526f4216537199be9f4ca4be6f4ae1eaf0ff67f6adb432710b18e2e'", 'TARGET_TASKS_HASH = ' + repr(target_hash))
    (owned / 'app/task20_schema_release.py').write_text(source)
    canonical = (HERE / '2026-10-16-confirmation-release-canonical.py').read_text()
    (owned / 'app/confirmation_schema_release.py').write_text(canonical)
    before['app/confirmation_schema_release.py'] = ''
    patch = ''.join(''.join(difflib.unified_diff(old.splitlines(True), (owned / name).read_text().splitlines(True), fromfile='/dev/null' if not old else 'a/' + name, tofile='b/' + name)) for name, old in before.items())
    (HERE / (PREFIX + '.patch')).write_text(patch)
    subprocess.run(['/Users/derek/.cache/uv/archive-v0/1vqK0HG0A7DbkHHlOjztN/bin/ruff', 'check', '--no-cache', *(str(owned / name) for name in before)], check=True)
    for name in before:
        compile((owned / name).read_text(), name, 'exec')
    (HERE / (PREFIX + '-analysis.json')).write_text(json.dumps({'patch_sha256': hashlib.sha256(patch.encode()).hexdigest(), 'target_tasks_hash': target_hash, 'files': {n: {'before': hashlib.sha256(s.encode()).hexdigest(), 'after': hashlib.sha256((owned/n).read_bytes()).hexdigest()} for n,s in before.items()}, 'scope': 'Unselected pure retirement plus paired coordinator; original source factories untouched; data-copy regression pending.'}, indent=2) + '\n')
