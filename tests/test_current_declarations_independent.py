from app import action_storage, business_definitions, current_action_schema
from app import current_schema_manifest, task_run_entity_assembly


def test_current_declarations_do_not_call_historical_builders(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('historical builder used by current declarations')

    monkeypatch.setattr(action_storage, '_build_action_payloads', forbidden)
    monkeypatch.setattr(action_storage, '_action_definitions', forbidden)
    monkeypatch.setattr(business_definitions, '_definitions', forbidden)
    current_action_schema.current_action_payloads.cache_clear()
    current_schema_manifest.current_objects.cache_clear()
    assert task_run_entity_assembly._action_objects()
    assert current_action_schema.current_action_payloads().layouts
    assert current_schema_manifest.current_objects(email_ledger=False)
    assert action_storage._action_layout('errors').fields
    import sqlite3
    from app.action_error_definitions import error_projection
    from app.current_database_schema import create_current_database_schema
    with sqlite3.connect(":memory:") as db:
        create_current_database_schema(db)
        action_storage.insert_error_action(db, {"kind": "fixture", "detail": "evidence", "created_at": "clock"})
        row = db.execute(error_projection()).fetchone()
        assert row[3:6] == ("fixture", "evidence", "clock")




def test_both_current_schema_roles_preserve_exact_catalogs():
    import hashlib
    import json
    from app.current_schema_manifest import current_objects
    from app.feedback_owner_schema_release import feedback_target_objects
    from app.model_retirement_schema_source import store26_objects

    for email_ledger, count, digest in (
        (False, 463, '147d3ac4553e6e54ff7183f582f1fae80e3f6e1b3653028a31a3415f6ce00d11'),
        (True, 464, 'b912b9a1beac8e601ec644010936968d5ca3f5fe930c2453458916763ddbe88b'),
    ):
        objects = current_objects(email_ledger=email_ledger)
        assert len(objects) == count
        encoded = json.dumps(objects).encode()
        assert hashlib.sha256(encoded).hexdigest() == digest
        # Store28.4 only adds the WeChat voice transcript table to the frozen Store28.3 catalog.
        assert tuple(row for row in objects if row[1] != 'wechat_voice_transcripts') == tuple(
            row for row in feedback_target_objects()
            if email_ledger or row[1] != 'email_schema_migrations')

    for email_ledger, count, digest in (
        (False, 475, '335bb6fe9c5c04a1c6f10849e8c7d4e8e1bee6e037ae070aea28126a506f8575'),
        (True, 476, '105891b5280133fa10cf3fbfc66c93570cfcdb7928c7f3be8edbf47810686754'),
    ):
        source = store26_objects(email_ledger=email_ledger)
        assert len(source) == count
        assert hashlib.sha256(json.dumps(source).encode()).hexdigest() == digest


def test_current_field_and_payload_metadata_preserves_frozen_layouts():
    import hashlib
    import json
    from app.business_legacy_fields import CURRENT_BUSINESS_FIELDS, CURRENT_SYNC_FIELDS

    fields = {
        "business": (CURRENT_BUSINESS_FIELDS, "9aea33ecc91ad7cec46b0db5c037e94bb06c9437d90c86608ce01fa8cad7cfba"),
        "sync": (CURRENT_SYNC_FIELDS, "6717b85c10acf98a539839ec981191cc9e1105e17b9b6be9f6d7612f5b5e1ff3"),
        "payload": ({kind: layout.fields for kind, layout in current_action_schema.current_action_payloads().layouts.items()},
                    "289b6f3d07d4927f8feaf6aca6d0ff2d9cdd8366325f096c987bbe8e8fdc2f4f"),
    }
    for value, digest in fields.values():
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
        assert hashlib.sha256(encoded).hexdigest() == digest


def test_shared_email_writer_uses_frozen_layouts_without_historical_builder(monkeypatch):
    import hashlib
    import json
    import sqlite3
    from app.current_database_schema import create_current_database_schema
    from app.shared_action_definitions import SOURCE_TO_TARGET, SPECIALIZED_SOURCE_TO_TARGET, email_projection
    from app import shared_action_lifecycle as lifecycle

    def forbidden(*args, **kwargs):
        raise AssertionError('historical builder used by current Email writer')

    monkeypatch.setattr(action_storage, '_build_action_payloads', forbidden)
    kinds = {'email_action_plans', *lifecycle.STEP_KINDS.values(), *lifecycle.RESULT_KINDS.values(),
             *SOURCE_TO_TARGET, *SPECIALIZED_SOURCE_TO_TARGET}
    layouts = {kind: lifecycle._layout(kind).fields for kind in sorted(kinds)}
    assert hashlib.sha256(json.dumps(layouts, sort_keys=True).encode()).hexdigest() == 'd6705e134b334b88f68e76f4ef2a86b4f09265710898c423a8038ca7e77be8e0'
    values = {
        'action_plan_id': 'plan', 'action_plan_version': 1, 'classification_id': 11,
        'account_id': 'account', 'category': 'work', 'classification_source': 'model',
        'confidence': 0.5, 'model_id': 'model', 'config_version': 'config',
        'actions_json': '["archive"]', 'action_parameters_json': '{}',
        'authorization_snapshot_json': '[]', 'legacy_serialization_pre_v16': 0,
        'created_at': '2026-10-12',
    }
    with sqlite3.connect(':memory:') as db:
        create_current_database_schema(db)
        lifecycle.persist_email_plan(db, values)
        row = db.execute('select action_plan_id, classification_id, actions_json from ('
                         + email_projection('email_action_plans') + ')').fetchone()
        assert row == ('plan', 11, '["archive"]')
        assert lifecycle.persist_email_plan(db, values).rowcount == 0


def test_cold_store_import_create_and_open_do_not_run_historical_builders(tmp_path):
    import json
    import subprocess
    import sys

    program = '''
import json, sys
from pathlib import Path
hits = []
original_read_bytes = Path.read_bytes
original_read_text = Path.read_text
def checked_read_bytes(path):
    if path.name == "store26_1_email54_catalog.json":
        raise AssertionError("historical source catalog loaded by current Store")
    return original_read_bytes(path)
def checked_read_text(path, *args, **kwargs):
    if path.name == "store26_1_email54_catalog.json":
        raise AssertionError("historical source catalog loaded by current Store")
    return original_read_text(path, *args, **kwargs)
Path.read_bytes = checked_read_bytes
Path.read_text = checked_read_text
def profile(frame, event, arg):
    if event == "call":
        path = frame.f_code.co_filename.replace("\\\\", "/")
        name = frame.f_code.co_name
        if ((path.endswith("/app/action_storage.py") and name in
             {"_action_definitions", "_build_action_payloads", "action_payloads"}) or
            (path.endswith("/app/business_definitions.py") and name == "_definitions") or
            (path.endswith("/app/model_retirement_schema_source.py") and name == "store26_objects")):
            hits.append((Path(path).name, name))
sys.setprofile(profile)
from app.store import AutoReplyStore
AutoReplyStore(Path(sys.argv[1]))
AutoReplyStore(Path(sys.argv[1]))
sys.setprofile(None)
print(json.dumps(hits))
'''
    # The second fresh process exercises an existing database without the
    # per-process same-path initialization cache hiding its cold-open path.
    for _ in range(2):
        result = subprocess.run([sys.executable, '-c', program, str(tmp_path / 'store.sqlite3')],
                                check=True, capture_output=True, text=True, timeout=30)
        assert json.loads(result.stdout.splitlines()[-1]) == []


def test_current_assembly_cache_is_isolated_by_role_and_caller(monkeypatch):
    from app import task_run_entity_assembly as assembly

    assembly._cached_assembly_objects.cache_clear()
    original = assembly._unique_declared_objects
    calls = []

    def count(families):
        calls.append(1)
        return original(families)

    monkeypatch.setattr(assembly, '_unique_declared_objects', count)
    bare = assembly.task_run_entity_assembly_objects()
    paired = assembly.task_run_entity_assembly_objects(email_ledger=True)
    original_bare, original_paired = dict(bare), dict(paired)
    bare['actions'] = 'mutated'
    paired.pop('email_schema_migrations')
    bare_again = assembly.task_run_entity_assembly_objects()
    paired_again = assembly.task_run_entity_assembly_objects(email_ledger=True)
    assert bare_again == original_bare
    assert paired_again == original_paired
    assert 'email_schema_migrations' not in bare_again
    assert 'email_schema_migrations' in paired_again
    assert len(bare_again) == 463 and len(paired_again) == 464
    assert len(calls) == 2
