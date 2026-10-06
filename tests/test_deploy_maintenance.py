import pytest

from app.deploy import deploy


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
    monkeypatch.setattr(module, 'wait_until_quiet', lambda *a: calls.append('quiet'))
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


def test_real_interrupted_recovery_preserves_identity_and_verified_backup(tmp_path):
    import json
    import sqlite3
    from app.deploy_maintenance import check_maintenance, prepare_maintenance
    from app.database_backup import backup_is_complete
    from tests.test_reply_task_deferral import ready
    path, task_id, _ = ready(tmp_path)
    from app.email_store import EmailStore
    EmailStore(path)
    with sqlite3.connect(path) as db:
        db.execute("update reply_tasks set status='processing' where id=?", (task_id,))
        db.execute("update agent_runs set status='running',completed_at='' where reply_task_id=?", (task_id,))
        run_id = db.execute('select id from agent_runs where reply_task_id=?', (task_id,)).fetchone()[0]
        db.execute("insert into agent_runtime_attempts(agent_run_id,workload_kind,workload_key,attempt_number,route_name,runtime_kind,credential_mode,model,status) values (?,'agent_run',?,1,'test','codex_cli','oauth','test','running')", (run_id,str(run_id)))
        before = db.execute('select execution_generation,input_version,business_object_key,attempts from reply_tasks where id=?', (task_id,)).fetchone()
    prepare_maintenance(path, (task_id,), 'test-maintenance', check_maintenance(path, (task_id,)))
    with sqlite3.connect(path) as db:
        assert db.execute('select execution_generation,input_version,business_object_key,attempts from reply_tasks where id=?', (task_id,)).fetchone() == before
        assert db.execute('select status from reply_tasks where id=?', (task_id,)).fetchone()[0] == 'pending'
        assert db.execute('select status from agent_runs where id=?', (run_id,)).fetchone()[0] == 'failed'
        assert db.execute('select failure_code from agent_runtime_attempts where agent_run_id=?', (run_id,)).fetchone()[0] == 'service_restart_interrupted'
        receipt = json.loads(db.execute("select value from service_state where key='deployment_maintenance_receipt'").fetchone()[0])
    from pathlib import Path
    backup = Path(receipt['verified_backup'])
    assert backup_is_complete(backup)
    with sqlite3.connect(backup) as db:
        assert db.execute('select status from reply_tasks where id=?', (task_id,)).fetchone()[0] == 'processing'
        assert db.execute('pragma integrity_check').fetchone()[0] == 'ok'


@pytest.mark.parametrize('effect', ['receipt', 'unknown_tool', 'other_task'])
def test_post_stop_race_rejected_before_recovery_or_backup(tmp_path, effect):
    import sqlite3
    from app.deploy_maintenance import check_maintenance, prepare_maintenance
    from app.repository_updater import UpgradePreconditionError
    from tests.test_reply_task_deferral import ready
    path, task_id, _ = ready(tmp_path)
    from app.email_store import EmailStore
    EmailStore(path)
    snapshot = check_maintenance(path, (task_id,))
    with sqlite3.connect(path) as db:
        if effect == 'receipt':
            db.execute("insert into sent_replies(conversation_id,trigger_message_id,reply_text) values ('cid-deferral','mid-deferral','sent')")
        elif effect == 'unknown_tool':
            run = db.execute('select id from agent_runs where reply_task_id=?', (task_id,)).fetchone()[0]
            db.execute("insert into agent_run_events(agent_run_id,sequence,event_json) values (?,999,?)", (run, '{"type":"item.completed","item":{"type":"command_execution"}}'))
        else:
            db.execute("update reply_tasks set status='processing' where id=?", (task_id,))
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
    monkeypatch.setattr(module, 'wait_until_quiet', lambda *a: None)
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
