import pytest

from app.store import AGENT_RUN_SQL, REPLY_TASK_SQL
from app.task8_storage import register_task8_functions
from tests.support.native_run_rows import insert_runtime_attempt_row
from tests.support.native_task_fields import set_native_task_fields
from app.state_definitions import state_projection

from app.deploy import deploy


@pytest.mark.parametrize('lease,expected_owners', [
    ('2999-01-01T00:00:00+00:00', (9999001,)),
    ('2000-01-01T00:00:00+00:00', ()),
])
def test_maintenance_reads_current_dispatcher_owners(tmp_path, monkeypatch, lease, expected_owners):
    import sqlite3
    from types import SimpleNamespace
    from app import deploy_maintenance
    from app.email_store import EmailStore
    from app.task_lifecycle_storage import acquire_claim
    from tests.test_reply_task_deferral import native_events, ready

    path, task_id, _ = ready(tmp_path)
    EmailStore(path)
    native_events(path, task_id, monkeypatch, {'type': 'AgentMessage', 'text': 'preparing'})
    with sqlite3.connect(path) as db:
        register_task8_functions(db)
        acquire_claim(db, adapter_name='reply', source_id=str(task_id), owner='maintenance',
                      owner_pid=9999001, lease_expires_at=lease, now='1999-01-01 00:00:00')
    monkeypatch.setattr(deploy_maintenance.subprocess, 'run', lambda *args, **kwargs:
                        SimpleNamespace(stdout='9999001 1\n9999002 9999001\n'))
    snapshot = deploy_maintenance.check_maintenance(path, (task_id,))
    assert snapshot.owners == expected_owners
    assert snapshot.processes == ((9999001, 9999002) if expected_owners else ())
    assert [binding[0] for binding in snapshot.task_bindings] == [task_id]


@pytest.mark.parametrize('missing', [False, True])
def test_native_tool_or_missing_source_blocks_maintenance(tmp_path, monkeypatch, missing):
    from app.deploy_maintenance import check_maintenance
    from app.email_store import EmailStore
    from app.repository_updater import UpgradePreconditionError
    from tests.test_reply_task_deferral import native_events, ready
    path, task_id, _ = ready(tmp_path)
    EmailStore(path)
    native_events(path, task_id, monkeypatch, {'type': 'UnknownTool'}, missing=missing)
    with pytest.raises(UpgradePreconditionError, match='native event source unavailable' if missing else 'tool or unknown event'):
        check_maintenance(path, (task_id,))


def test_unknown_native_event_without_item_is_not_proven_empty(tmp_path, monkeypatch):
    import json
    from app.deploy_maintenance import check_maintenance
    from app.email_store import EmailStore
    from app.reply_task_deferral import preview_deferral
    from app.repository_updater import UpgradePreconditionError
    from tests.test_reply_task_deferral import NOW, native_events, ready

    path, task_id, _ = ready(tmp_path)
    EmailStore(path)
    native_events(path, task_id, monkeypatch, {'type': 'AgentMessage'})
    native = tmp_path / 'native.jsonl'
    records = [json.loads(line) for line in native.read_text().splitlines()]
    records[1] = {'type': 'event_msg', 'payload': {'type': 'unknown_provider_effect'}}
    native.write_text('\n'.join(json.dumps(record) for record in records))
    with pytest.raises(UpgradePreconditionError, match='tool or unknown event'):
        check_maintenance(path, (task_id,))
    assert 'native_event_source_unavailable' in preview_deferral(path, task_id, now=NOW)['blockers']


def test_maintenance_deploy_stops_and_recovers_before_quiet(tmp_path, monkeypatch):
    from tests.test_repository_updater import fixture_repo, StateStore
    from app import deploy as module
    local, _ = fixture_repo(tmp_path)
    calls = []
    monkeypatch.setattr(module, 'ExistingSchemaUpgradeStateStore', lambda *a: StateStore())
    monkeypatch.setattr(module, 'check_maintenance', lambda *a: ())
    monkeypatch.setattr(module, 'stop_for_maintenance', lambda snapshot, stop: (stop(), snapshot)[1])
    monkeypatch.setattr(module, '_default_stop', lambda: calls.append('stop'))
    monkeypatch.setattr(module, '_default_start', lambda: calls.append('start'))
    monkeypatch.setattr(module, 'wait_for_health', lambda: True)
    monkeypatch.setattr(module, 'build_frontend', lambda *a: None)
    monkeypatch.setattr(module, 'verify_imports', lambda *a: None)
    monkeypatch.setattr(module, 'wait_until_quiet', lambda *a, **_kwargs: calls.append('quiet'))
    monkeypatch.setattr(module, 'in_flight_work', lambda *_args: 0)
    monkeypatch.setattr(module, 'prepare_maintenance', lambda *a: calls.append('recover'), raising=False)
    deploy(local, tmp_path/'missing.sqlite3', maintenance_tasks=(10,))
    assert calls == ['stop', 'recover', 'quiet', 'start']


def test_maintenance_precondition_failure_restarts_old_service(tmp_path, monkeypatch):
    from tests.test_repository_updater import fixture_repo, StateStore
    from app import deploy as module
    from app.repository_updater import UpgradePreconditionError
    local, _ = fixture_repo(tmp_path)
    calls = []
    monkeypatch.setattr(module, 'ExistingSchemaUpgradeStateStore', lambda *a: StateStore())
    monkeypatch.setattr(module, 'check_maintenance', lambda *a: ())
    monkeypatch.setattr(module, 'stop_for_maintenance', lambda snapshot, stop: (stop(), snapshot)[1])
    monkeypatch.setattr(module, '_default_stop', lambda: calls.append('stop'))
    monkeypatch.setattr(module, '_default_start', lambda: calls.append('start'))
    def fail(*a):
        raise UpgradePreconditionError('new effects')
    monkeypatch.setattr(module, 'prepare_maintenance', fail, raising=False)
    with pytest.raises(SystemExit, match='new effects'):
        deploy(local, tmp_path/'missing.sqlite3', maintenance_tasks=(10,))
    assert calls == ['stop', 'start']


def test_real_interrupted_recovery_preserves_identity_and_verified_backup(tmp_path, monkeypatch):
    import json
    import sqlite3
    from app.deploy_maintenance import check_maintenance, prepare_maintenance
    from app.database_backup import backup_is_complete
    from tests.test_reply_task_deferral import native_events, ready
    path, task_id, _ = ready(tmp_path)
    from app.email_store import EmailStore
    EmailStore(path)
    native_end = native_events(path, task_id, monkeypatch, {'type': 'AgentMessage', 'text': 'preparing'})
    with sqlite3.connect(path) as db:
        register_task8_functions(db)
        db.row_factory = sqlite3.Row
        set_native_task_fields(db, "reply_tasks", task_id, {"status": "processing"})
        db.execute("update runs set status='running',completed_at='' where task_id=?", (task_id,))
        run_id = db.execute(f'select id from ({AGENT_RUN_SQL}) where reply_task_id=?', (task_id,)).fetchone()[0]
        insert_runtime_attempt_row(
            db, {
                'parent_id': run_id,
                'workload_kind': 'agent_run',
                'workload_key': str(run_id),
                'attempt_number': 1,
                'route_name': 'test',
                'runtime_kind': 'codex_cli',
                'credential_mode': 'oauth',
                'model': 'test',
                'status': 'running',
                'session_id': 'native-test',
                'transcript_end': native_end,
            },
        )
        before = tuple(db.execute(f'select execution_generation,input_version,business_object_key,attempts from ({REPLY_TASK_SQL}) where id=?', (task_id,)).fetchone())
    prepare_maintenance(path, (task_id,), 'test-maintenance', check_maintenance(path, (task_id,)))
    with sqlite3.connect(path) as db:
        assert db.execute(f'select execution_generation,input_version,business_object_key,attempts from ({REPLY_TASK_SQL}) where id=?', (task_id,)).fetchone() == before
        assert db.execute(f'select status from ({REPLY_TASK_SQL}) where id=?', (task_id,)).fetchone()[0] == 'pending'
        assert db.execute(f'select status from ({AGENT_RUN_SQL}) where id=?', (run_id,)).fetchone()[0] == 'failed'
        assert db.execute('select failure_code from runtime_attempts where parent_id=?', (run_id,)).fetchone()[0] == 'service_restart_interrupted'
        receipt = json.loads(db.execute(f"select value from ({state_projection('service_state')}) where key='deployment_maintenance_receipt'").fetchone()[0])
    from pathlib import Path
    backup = Path(receipt['verified_backup'])
    assert backup_is_complete(backup)
    with sqlite3.connect(backup) as db:
        assert db.execute(f'select status from ({REPLY_TASK_SQL}) where id=?', (task_id,)).fetchone()[0] == 'processing'
        assert db.execute('pragma integrity_check').fetchone()[0] == 'ok'


@pytest.mark.parametrize('effect', ['receipt', 'unknown_tool', 'other_task'])
def test_post_stop_race_rejected_before_recovery_or_backup(tmp_path, monkeypatch, effect):
    import sqlite3
    from app.deploy_maintenance import check_maintenance, prepare_maintenance
    from app.repository_updater import UpgradePreconditionError
    from tests.test_reply_task_deferral import native_events, ready
    path, task_id, _ = ready(tmp_path)
    from app.email_store import EmailStore
    EmailStore(path)
    snapshot = check_maintenance(path, (task_id,))
    with sqlite3.connect(path) as db:
        if effect == 'receipt':
            from app.store import AutoReplyStore
            from tests.sent_fixtures import historical_sent_delivery
            run_id = db.execute('select id from runs where task_id=?', (task_id,)).fetchone()[0]
            historical_sent_delivery(
                AutoReplyStore(path), agent_run_id=run_id, external_action_key='deferral-sent',
                conversation_id='cid-deferral', trigger_message_id='mid-deferral',
                reply_text='sent', provider_result={'verification': {'state': 'sent'}},
            )
        elif effect == 'unknown_tool':
            native_events(path, task_id, monkeypatch, {'type': 'CommandExecution'})
        else:
            register_task8_functions(db)
            set_native_task_fields(db, "reply_tasks", task_id, {"status": "processing"})
    allowed = () if effect == 'other_task' else (task_id,)
    with pytest.raises(UpgradePreconditionError):
        prepare_maintenance(path, allowed, 'rejected', snapshot)
    assert not (tmp_path/'maintenance-backups').exists()


def test_publication_rollback_failure_keeps_service_stopped(tmp_path, monkeypatch):
    from tests.test_repository_updater import fixture_repo, StateStore
    from app import deploy as module
    from app.repository_updater import UpgradeFailed
    local, _ = fixture_repo(tmp_path)
    calls = []
    state = StateStore()
    monkeypatch.setattr(module, 'ExistingSchemaUpgradeStateStore', lambda *a: state)
    monkeypatch.setattr(module, 'check_maintenance', lambda *a: ())
    monkeypatch.setattr(module, 'stop_for_maintenance', lambda snapshot, stop: (stop(), snapshot)[1])
    monkeypatch.setattr(module, 'prepare_maintenance', lambda *a: None)
    monkeypatch.setattr(module, '_default_stop', lambda: calls.append('stop'))
    monkeypatch.setattr(module, '_default_start', lambda: calls.append('start'))
    monkeypatch.setattr(module, 'wait_for_health', lambda: True)
    monkeypatch.setattr(module, 'wait_until_quiet', lambda *a, **_kwargs: None)
    monkeypatch.setattr(module, 'in_flight_work', lambda *_args: 0)
    monkeypatch.setattr(module, 'build_frontend', lambda *a: None)
    monkeypatch.setattr(module, 'verify_imports', lambda *a: None)
    class Publication:
        def publish(self):
            pass
        def verify_loaded(self):
            raise RuntimeError('load failed')
        def rollback(self):
            raise RuntimeError('rollback failed')
    monkeypatch.setattr(module, 'prepare_consumer_system_contracts', lambda **a: Publication())
    with pytest.raises(UpgradeFailed):
        deploy(local, tmp_path/'missing.sqlite3', maintenance_tasks=(10,), publish_contracts=True)
    assert calls == ['stop', 'start', 'stop']
    import json
    assert json.loads(next(iter(state.values.values())))['status'] == 'needs_manual'


def test_freeze_discovers_late_child_before_stop_and_kills_only_owned_tree(monkeypatch):
    import signal
    from app import deploy_maintenance as module
    tables = iter([
        {100: (1, 90, 'S'), 110: (100, 110, 'S')},
        {100: (1, 90, 'T'), 110: (100, 110, 'T'), 111: (110, 110, 'S')},
        {100: (1, 90, 'T'), 110: (100, 110, 'T'), 111: (110, 110, 'T')},
        {110: (1, 110, 'T'), 111: (110, 110, 'T'), 999: (1, 999, 'S')},
        {999: (1, 999, 'S')},
    ])
    monkeypatch.setattr(module, '_process_table', lambda: next(tables))
    monkeypatch.setattr(module.os, 'getpgrp', lambda: 999)
    calls = []
    monkeypatch.setattr(module.os, 'kill', lambda pid, sig: calls.append((pid, sig)))
    result = module.stop_for_maintenance(module.MaintenanceSnapshot((100,110), ((1,'key','gen',1),), (100,)), lambda: calls.append('stop'))
    assert calls.index((111, signal.SIGSTOP)) < calls.index('stop')
    assert (110, signal.SIGKILL) in calls and (111, signal.SIGKILL) in calls
    assert not any(isinstance(call, tuple) and call[0] == 999 for call in calls)
    assert result.processes == ()


def test_freeze_error_stops_service_and_does_not_restart(monkeypatch):
    from app import deploy_maintenance as module
    monkeypatch.setattr(module, '_process_table', lambda: {100: (1, 90, 'S')})
    def fail(*a):
        raise PermissionError('cannot freeze')
    monkeypatch.setattr(module.os, 'kill', fail)
    calls = []
    with pytest.raises(PermissionError):
        module.stop_for_maintenance(module.MaintenanceSnapshot((100,), (), (100,)), lambda: calls.append('stop'))
    assert calls == ['stop']
