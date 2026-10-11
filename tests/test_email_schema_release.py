from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess

import pytest

from app.email_schema_release import EmailSchemaRelease, LOOKUP_INDEX, prepare_email_schema_release
from app.repository_updater import RepositoryUpdater, UpgradeFailed
from tests.test_repository_updater_publication import _State, _repo


INDEX_SQL = (f"CREATE INDEX {LOOKUP_INDEX} ON email_provider_observations"
             "(account_id,stable_message_identity,observed_at desc)")


def legacy_database(path):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("create table email_schema_migrations(version integer primary key,applied_at text)")
        db.execute("insert into email_schema_migrations values(47,'original')")
        db.execute("create table email_provider_observations(account_id text,"
                   "stable_message_identity text,observed_at text,primary key(account_id,stable_message_identity))")
        db.execute("insert into email_provider_observations values('account','original','time')")
        db.execute(INDEX_SQL)
        db.execute("create table external_action_results(external_action_key text primary key,result_digest text)")
    return path


def migrate(root, path):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(f"drop index {LOOKUP_INDEX}")
        db.execute("insert into email_schema_migrations values(48,'new')")


def release(root, path):
    with closing(sqlite3.connect(path)) as db:
        versions = tuple(db.execute("select version,applied_at from email_schema_migrations order by version"))
        sql = db.execute("select sql from sqlite_master where name=?", (LOOKUP_INDEX,)).fetchone()[0]
    return EmailSchemaRelease(root, path, versions, sql, migrate=migrate)


def metadata(path):
    with closing(sqlite3.connect(path)) as db:
        return (tuple(db.execute("select * from email_schema_migrations order by version")),
                db.execute("select sql from sqlite_master where name=?", (LOOKUP_INDEX,)).fetchone())


def test_inverse_preserves_replacement_receipt_and_exact_previous_schema(tmp_path):
    path = legacy_database(tmp_path / 'db.sqlite3')
    before = metadata(path)
    publication = release(tmp_path, path)
    publication.publish()
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("insert into external_action_results values('new-receipt','new-digest')")
    publication.rollback()
    assert metadata(path) == before
    publication.rollback()
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("select * from external_action_results").fetchall() == [('new-receipt', 'new-digest')]
        assert db.execute("pragma integrity_check").fetchone() == ('ok',)


def test_partial_inverse_failure_rolls_back_index_creation(tmp_path):
    path = legacy_database(tmp_path / 'db.sqlite3')
    publication = release(tmp_path, path)
    publication.publish()
    with closing(sqlite3.connect(path)) as db:
        db.execute("create trigger inverse_failure before delete on email_schema_migrations "
                   "begin select raise(abort,'inverse failed'); end")
    with pytest.raises(sqlite3.IntegrityError, match='inverse failed'):
        publication.rollback()
    assert metadata(path) == (((47, 'original'), (48, 'new')), None)


def test_unexpected_schema_does_not_erase_versions_or_receipts(tmp_path):
    path = legacy_database(tmp_path / 'db.sqlite3')
    publication = release(tmp_path, path)
    publication.publish()
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("insert into email_schema_migrations values(49,'unexpected')")
    before = metadata(path)
    with pytest.raises(ValueError, match='exact 47-to-48'):
        publication.rollback()
    assert metadata(path) == before


def test_prepare_handles_relative_paths_and_no_change(tmp_path, monkeypatch):
    root = tmp_path / 'target'
    (root / 'app').mkdir(parents=True)
    (root / 'app/email_store.py').write_text('EMAIL_SCHEMA_VERSION = 48\n')
    legacy_database(tmp_path / 'db.sqlite3')
    monkeypatch.chdir(tmp_path)
    publication = prepare_email_schema_release(Path('target'), Path('db.sqlite3'))
    assert publication.database_path == tmp_path / 'db.sqlite3'
    publication.migrate = migrate
    publication.publish()
    assert prepare_email_schema_release(root, tmp_path / 'db.sqlite3') is None


@pytest.mark.parametrize('version', [46, 49])
def test_prepare_requires_a_known_reversible_transition(tmp_path, version):
    (tmp_path / 'app').mkdir()
    (tmp_path / 'app/email_store.py').write_text('EMAIL_SCHEMA_VERSION = 48\n')
    path = legacy_database(tmp_path / 'db.sqlite3')
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('update email_schema_migrations set version=?', (version,))
    with pytest.raises(ValueError, match='verified inverse'):
        prepare_email_schema_release(tmp_path, path)


@pytest.mark.parametrize('failure', ['dependencies', 'verification', 'restart', 'health', 'loaded'])
def test_deploy_failure_inverses_schema_before_old_start_and_preserves_receipt(tmp_path, failure):
    root, operation = _repo(tmp_path)
    path = legacy_database(tmp_path / 'db.sqlite3')
    initial = metadata(path)
    events = []
    state = _State()

    def fail_at(stage):
        events.append(stage)
        if failure == stage:
            raise RuntimeError(stage + ' failure')

    def prepare():
        events.append('prepare_schema')
        publication = release(root, path)
        original_publish = publication.publish
        original_rollback = publication.rollback
        original_verify = publication.verify_loaded

        def publish():
            events.append('migrate')
            original_publish()

        def inverse():
            events.append('inverse')
            original_rollback()

        def verify():
            if events.count('start') and failure == 'loaded':
                raise RuntimeError('loaded failure')
            original_verify()

        publication.publish = publish
        publication.rollback = inverse
        publication.verify_loaded = verify
        return publication

    def start():
        events.append('start')
        with closing(sqlite3.connect(path)) as db, db:
            if events.count('start') == 1:
                assert db.execute('select max(version) from email_schema_migrations').fetchone()[0] == 48
                db.execute("insert into external_action_results values('new-receipt','new-digest')")
            else:
                assert metadata(path) == initial
        if events.count('start') == 1 and failure == 'restart':
            raise RuntimeError('partial bootstrap failure')

    health_calls = 0

    def health():
        nonlocal health_calls
        health_calls += 1
        return failure != 'health' or health_calls > 1

    updater = RepositoryUpdater(
        root, state, database_path=path,
        stop=lambda: events.append('stop'), restart=start, health=health,
        database_publication=prepare,
        dependency_sync=lambda: fail_at('dependencies'),
        verification=lambda: fail_at('verification'),
    )
    # On early failures, the first start is the original runtime, never v48.
    if failure in ('dependencies', 'verification'):
        def old_start():
            events.append('start')
            assert metadata(path) == initial
        updater.restart = old_start
    with pytest.raises(UpgradeFailed):
        updater.execute(operation)
    assert metadata(path) == initial
    assert (root / 'version').read_text() == 'old\n'
    assert events.index('prepare_schema') < events.index('dependencies')
    assert events.index('inverse') < len(events) - 1
    if failure in ('restart', 'health', 'loaded'):
        assert events.count('stop') == 2
        assert events.index('inverse') > events.index('stop', 1)
        with closing(sqlite3.connect(path)) as db:
            assert db.execute('select external_action_key from external_action_results').fetchall() == [('new-receipt',)]
    assert json.loads(state.values['repository_upgrade_operation:v1'])['status'] == 'rolled_back'


def test_inverse_failure_keeps_new_checkout_stopped_instead_of_old_start(tmp_path):
    root, operation = _repo(tmp_path)
    path = legacy_database(tmp_path / 'db.sqlite3')
    events = []
    state = _State()

    def prepare():
        publication = release(root, path)
        def inverse():
            raise RuntimeError('inverse failed')
        publication.rollback = inverse
        return publication

    updater = RepositoryUpdater(
        root, state, database_path=path, database_publication=prepare,
        stop=lambda: events.append('stop'), restart=lambda: events.append('start'),
        health=lambda: False,
    )
    with pytest.raises(UpgradeFailed):
        updater.execute(operation)
    assert events == ['stop', 'start', 'stop']
    assert (root / 'version').read_text() == 'new\n'
    assert metadata(path)[0][-1][0] == 48
    assert json.loads(state.values['repository_upgrade_operation:v1'])['status'] == 'needs_manual'


@pytest.mark.parametrize('absent', [True, False])
def test_failed_stop_accepts_only_native_absent_label_readback(monkeypatch, absent):
    import app.repository_updater as module
    calls = []
    def run(args, **kwargs):
        calls.append(args[1])
        message = 'Could not find service' if absent else 'Permission denied'
        return subprocess.CompletedProcess(args, 1, stdout='', stderr=message)
    monkeypatch.setattr(module.subprocess, 'run', run)
    if absent:
        module._default_stop()
    else:
        with pytest.raises(UpgradeFailed, match='stop failed'):
            module._default_stop()
    assert calls == ['bootout', 'print']


def test_partial_asset_failure_rolls_back_asset_then_schema_before_old_start(tmp_path):
    root, operation = _repo(tmp_path)
    path = legacy_database(tmp_path / 'db.sqlite3')
    initial = metadata(path)
    events = []

    def prepare():
        publication = release(root, path)
        inverse = publication.rollback
        def rollback():
            events.append('schema_inverse')
            inverse()
        publication.rollback = rollback
        return publication

    class Asset:
        def publish(self):
            events.append('asset_write')
            raise RuntimeError('partial asset failure')
        def rollback(self):
            events.append('asset_inverse')
        def verify_loaded(self):
            raise AssertionError('never reached')
        def finalize(self):
            raise AssertionError('never reached')

    def start():
        assert metadata(path) == initial
        events.append('old_start')

    updater = RepositoryUpdater(
        root, _State(), database_path=path,
        database_publication=prepare, publication=Asset,
        restart=start, health=lambda: True,
    )
    with pytest.raises(UpgradeFailed):
        updater.execute(operation)
    assert events == ['asset_write', 'asset_inverse', 'schema_inverse', 'old_start']
    assert metadata(path) == initial


def test_updater_module_entrypoint_uses_same_stopped_schema_release(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import sys
    import app.repository_updater as module
    import app.email_schema_release as schema_module
    captured = {}
    store = _State()
    monkeypatch.setattr(module, 'ExistingSchemaUpgradeStateStore', lambda path: store)
    monkeypatch.setattr(module, 'load_persisted_operation', lambda actual, opid: 'operation')
    monkeypatch.setattr(module, 'GitRepository', lambda root: object())

    class Updater:
        def __init__(self, root, actual_store, **kwargs):
            captured.update(kwargs)
            assert actual_store is store
        def execute(self, operation):
            assert operation == 'operation'
            return SimpleNamespace(status='succeeded')

    monkeypatch.setattr(module, 'RepositoryUpdater', Updater)
    monkeypatch.setattr(schema_module, 'prepare_email_schema_release', lambda root, path: (root, path))
    monkeypatch.setattr(sys, 'argv', ['updater', '--operation-id', 'op', '--repo', str(tmp_path), '--db', str(tmp_path/'db')])
    assert module.main() == 0
    assert captured['stop'] is module._default_stop
    assert captured['restart'] is module._default_start
    assert captured['database_publication']() == (tmp_path, tmp_path/'db')


def test_deploy_partial_bootstrap_can_stop_replacement(monkeypatch, tmp_path):
    import app.deploy as module
    root, operation = _repo(tmp_path)
    calls = []
    def start():
        calls.append('start')
        raise OSError('partial start')
    class Updater:
        def __init__(self, root, store, **kwargs):
            self.kwargs = kwargs
        def execute(self, operation, **kwargs):
            self.kwargs['stop']()
            with pytest.raises(OSError, match='partial start'):
                self.kwargs['restart']()
            self.kwargs['stop']()
            raise UpgradeFailed('offline bootstrap rollback')
    monkeypatch.setattr(module, 'RepositoryUpdater', Updater)
    monkeypatch.setattr(module, '_default_start', start)
    monkeypatch.setattr(module, '_default_stop', lambda: calls.append('stop'))
    with pytest.raises(UpgradeFailed, match='offline bootstrap rollback'):
        module.deploy(root, tmp_path/'absent.db')
    assert calls == ['stop', 'start', 'stop']


def test_cli_updater_uses_existing_schema_state_and_release(monkeypatch, tmp_path):
    import sys
    from types import SimpleNamespace
    import app.cli as cli
    import app.repository_updater as updater
    import app.email_schema_release as schema
    captured = {}
    store = _State()
    monkeypatch.setattr(cli, 'settings_from_args', lambda args: SimpleNamespace(
        db_path=tmp_path/'db', repository_upgrade_remote='origin', repository_upgrade_branch='main'))
    monkeypatch.setattr(cli, '_repo_root', lambda: tmp_path)
    monkeypatch.setattr(updater, 'ExistingSchemaUpgradeStateStore', lambda path: store)
    monkeypatch.setattr(updater, 'load_persisted_operation', lambda s, opid: 'operation')
    def forbid_store(*args, **kwargs):
        raise AssertionError('application store before stopped migration window')
    monkeypatch.setattr(cli, 'AutoReplyStore', forbid_store)
    class Updater:
        def __init__(self, root, s, **kwargs):
            captured.update(kwargs)
            assert s is store
        def execute(self, operation):
            return SimpleNamespace(status='succeeded')
    monkeypatch.setattr(updater, 'RepositoryUpdater', Updater)
    monkeypatch.setattr(schema, 'prepare_email_schema_release', lambda root, path: (root, path))
    monkeypatch.setattr(sys, 'argv', ['cli', 'repository-updater', '--operation-id', 'op'])
    cli.main()
    assert captured['stop'] is updater._default_stop
    assert captured['restart'] is updater._default_start
    assert captured['database_publication']() == (tmp_path, tmp_path/'db')


def action_plan_release(tmp_path):
    from tests.test_email_action_plan_reference import seed, rewind48
    path = tmp_path / 'plans.sqlite3'
    seed(path)
    rewind48(path)
    (tmp_path / 'app').mkdir()
    (tmp_path / 'app/email_store.py').write_text('EMAIL_SCHEMA_VERSION = 49\n')
    return path, prepare_email_schema_release(tmp_path, path)


def test_plan_inverse_preserves_new_classification_and_canonical_versions(tmp_path):
    from app.email_store import EmailStore
    from app.email_classifier_contracts import EmailClassificationStatus
    from tests.test_email_store import _classification, _persist_scan, _versioned_plan
    from tests.test_email_action_plan_reference import business_rows
    path, publication = action_plan_release(tmp_path)
    publication.migrate = lambda root, database: EmailStore(database)
    publication.publish()
    store = EmailStore(path)
    _persist_scan(store, _classification(status=EmailClassificationStatus.PROCESSED,
                                        classification_id=3, message_id='after-migration'))
    from app.email_classifier_contracts import EmailActionPlan
    original = EmailActionPlan.model_validate_json(json.dumps(store.get_classification(3)['action_plan']))
    newer = _versioned_plan(original, version=2, category=original.category,
                            actions=original.actions, action_parameters=original.action_parameters)
    store.append_action_plan_version(3, newer, confirmed_category=newer.category)
    before = business_rows(path)
    api = store.get_classification(3)
    publication.rollback()
    publication.rollback()
    assert business_rows(path) == before
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('select max(version) from email_schema_migrations').fetchone()[0] == 48
        snapshots = db.execute('select id, action_plan_json from email_classifications').fetchall()
        assert len(snapshots) == 3
        assert {identity: json.loads(snapshot) is None for identity, snapshot in snapshots} == {1: False, 2: True, 3: False}
        assert all(json.loads(snapshot)['classification_id'] == identity
                   for identity, snapshot in snapshots if snapshot != 'null')
        assert db.execute('pragma integrity_check').fetchone() == ('ok',)
    assert EmailStore(path).get_classification(3) == api


def test_plan_inverse_failure_is_atomic(tmp_path):
    from app.email_store import EmailStore
    path, publication = action_plan_release(tmp_path)
    publication.migrate = lambda root, database: EmailStore(database)
    publication.publish()
    with closing(sqlite3.connect(path)) as db:
        db.execute("create trigger plan_inverse_failure before delete on email_schema_migrations "
                   "begin select raise(abort,'plan inverse failed'); end")
    with pytest.raises(sqlite3.IntegrityError, match='plan inverse failed'):
        publication.rollback()
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('select max(version) from email_schema_migrations').fetchone()[0] == 49
        assert 'action_plan_json' not in {row[1] for row in db.execute('pragma table_info(email_classifications)')}


@pytest.mark.parametrize('full_legacy_snapshot', [True, False])
def test_plan_inverse_preserves_original_legacy_snapshot_byte_form(tmp_path, full_legacy_snapshot):
    from app.email_store import EmailStore
    path, _ = action_plan_release(tmp_path)
    with closing(sqlite3.connect(path)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute('update email_action_plans set legacy_serialization_pre_v16=1, authorization_snapshot_json=null')
        row = db.execute('select * from email_action_plans where classification_id=1').fetchone()
        plan = EmailStore._stored_action_plan(row)
        excluded = set() if full_legacy_snapshot else {'authorization_snapshot_format', 'action_authorizations'}
        snapshot = plan.model_dump_json(exclude=excluded)
        db.execute('update email_classifications set action_plan_json=? where id=1', (snapshot,))
        db.execute("update email_classifications set action_plan_json='' where id=2")
    publication = prepare_email_schema_release(tmp_path, path)
    publication.migrate = lambda root, database: EmailStore(database)
    publication.publish()
    publication.rollback()
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('select action_plan_json from email_classifications order by id').fetchall() == [(snapshot,), ('',)]
