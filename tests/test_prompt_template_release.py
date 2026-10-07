import hashlib
import importlib
import json
from pathlib import Path
import plistlib
import stat
import sys

import pytest

from test_repository_updater_publication import _repo, _State
from app.repository_updater import RepositoryUpdater, UpgradeFailed


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _fixture(tmp_path):
    root = tmp_path / 'checkout'
    root.mkdir()
    targets = {}
    entries = []
    old, new = {}, {}
    for name in ('developer_prompt', 'user_prompt'):
        source = root / f'app/defaults/{name}.md'
        source.parent.mkdir(parents=True, exist_ok=True)
        old[name], new[name] = f'old {name}\n'.encode(), f'new {name}\n'.encode()
        source.write_bytes(new[name])
        target = tmp_path / f'configured/{name}.md'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(old[name])
        target.chmod(0o640)
        targets[name] = target
        entries.append(dict(source=f'app/defaults/{name}.md', target=name,
                            old_sha256=_digest(old[name]), new_sha256=_digest(new[name])))
    manifest = root / 'ci/prompt-template-release.json'
    manifest.parent.mkdir()
    manifest.write_text(json.dumps(dict(version=1, files=entries)))
    return root, targets, manifest, old, new


def _prepare(tmp_path, fixture):
    root, targets, manifest, _, _ = fixture
    module = importlib.import_module('app.prompt_template_release')
    return module.prepare_prompt_templates(
        root=root, database_path=tmp_path / 'service.sqlite3', operation_id='offline-prompts',
        developer_prompt_path=targets['developer_prompt'], user_prompt_path=targets['user_prompt'],
        manifest_path=manifest,
    )


def test_publish_retains_verified_exact_originals_modes_and_receipt(tmp_path):
    fixture = _fixture(tmp_path)
    root, targets, _, old, new = fixture
    profile = targets['user_prompt'].with_name('work_profile.md')
    profile.write_text('unchanged personality')
    publication = _prepare(tmp_path, fixture)
    publication.publish()
    publication.verify_loaded()
    publication.finalize()
    receipt_dir = tmp_path / 'release-receipts/offline-prompts'
    receipt = json.loads((receipt_dir / 'receipt.json').read_text())
    assert receipt['status'] == 'verified'
    for index, name in enumerate(targets):
        assert targets[name].read_bytes() == new[name]
        assert stat.S_IMODE(targets[name].stat().st_mode) == 0o640
        assert (receipt_dir / f'old-{index}').read_bytes() == old[name]
        assert receipt['files'][index]['target'] == str(targets[name])
        assert receipt['files'][index]['old_sha256'] == _digest(old[name])
        assert (root / f'app/defaults/{name}.md').read_bytes() == new[name]
    assert profile.read_text() == 'unchanged personality'
    publication.rollback()
    assert all(targets[name].read_bytes() == old[name] for name in targets)
    assert json.loads((receipt_dir / 'receipt.json').read_text())['status'] == 'rolled_back'


@pytest.mark.parametrize('name', ['developer_prompt', 'user_prompt'])
def test_custom_template_refuses_whole_release_without_changing_any_file(tmp_path, name):
    fixture = _fixture(tmp_path)
    _, targets, _, old, _ = fixture
    targets[name].write_bytes(b'customized operator text\n')
    before = {key: path.read_bytes() for key, path in targets.items()}
    with pytest.raises(ValueError, match='migration needed.*' + name):
        _prepare(tmp_path, fixture)
    assert {key: path.read_bytes() for key, path in targets.items()} == before
    assert not (tmp_path / 'release-receipts').exists()


def test_already_new_template_is_not_replaced_and_rollback_preserves_it(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path)
    _, targets, _, old, new = fixture
    targets['developer_prompt'].write_bytes(new['developer_prompt'])
    publication = _prepare(tmp_path, fixture)
    module = importlib.import_module('app.prompt_template_release')
    replace = module._atomic_replace
    writes = []
    def record(path, data, mode):
        writes.append(path)
        replace(path, data, mode)
    monkeypatch.setattr(module, '_atomic_replace', record)
    publication.publish()
    assert targets['developer_prompt'] not in writes
    publication.rollback()
    assert targets['developer_prompt'].read_bytes() == new['developer_prompt']
    assert targets['user_prompt'].read_bytes() == old['user_prompt']


def test_partial_publish_can_restore_file_even_if_replace_wrote_then_raised(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path)
    _, targets, _, old, _ = fixture
    publication = _prepare(tmp_path, fixture)
    module = importlib.import_module('app.prompt_template_release')
    replace = module._atomic_replace
    def fail_after_replace(path, data, mode):
        replace(path, data, mode)
        if path == targets['user_prompt']:
            raise OSError('write failed after rename')
    monkeypatch.setattr(module, '_atomic_replace', fail_after_replace)
    with pytest.raises(OSError):
        publication.publish()
    monkeypatch.setattr(module, '_atomic_replace', replace)
    publication.rollback()
    assert all(targets[name].read_bytes() == old[name] for name in targets)


def test_loaded_readback_rejects_modified_file(tmp_path):
    fixture = _fixture(tmp_path)
    publication = _prepare(tmp_path, fixture)
    publication.publish()
    fixture[1]['user_prompt'].write_text('changed after restart')
    with pytest.raises(ValueError, match='released file changed.*user_prompt'):
        publication.verify_loaded()
    publication.rollback()


@pytest.mark.parametrize('failure', [
    'new_sha', 'old_sha', 'source_sha_mismatch', 'extra', 'duplicate',
    'target', 'missing', 'source_target', 'same_targets', 'linked_target',
])
def test_invalid_manifest_or_file_refuses_before_publication(tmp_path, failure):
    fixture = _fixture(tmp_path)
    root, targets, manifest, _, _ = fixture
    data = json.loads(manifest.read_text())
    if failure in ('new_sha', 'old_sha'):
        data['files'][0][failure + '256'] = 'invalid'
    elif failure == 'source_sha_mismatch':
        data['files'][0]['new_sha256'] = _digest(b'different reviewed source')
    elif failure == 'extra':
        data['files'].append(dict(source='app/defaults/work_profile.md'))
    elif failure == 'duplicate':
        data['files'][1] = data['files'][0]
    elif failure == 'target':
        data['files'][0]['target'] = 'work_profile'
    elif failure == 'missing':
        targets['user_prompt'].unlink()
    elif failure == 'source_target':
        targets['developer_prompt'] = root / 'app/defaults/developer_prompt.md'
    elif failure == 'same_targets':
        targets['user_prompt'] = targets['developer_prompt']
    elif failure == 'linked_target':
        targets['user_prompt'].unlink()
        targets['user_prompt'].symlink_to(targets['developer_prompt'])
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        _prepare(tmp_path, fixture)
    assert not (tmp_path / 'release-receipts').exists()


def test_backup_readback_failure_prevents_any_template_write(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path)
    publication = _prepare(tmp_path, fixture)
    read_bytes = Path.read_bytes
    def corrupt_read(path):
        if path.name == 'old-0':
            return b'corrupt backup'
        return read_bytes(path)
    monkeypatch.setattr(Path, 'read_bytes', corrupt_read)
    with pytest.raises(ValueError, match='backup SHA mismatch'):
        publication.publish()
    assert all(path.read_bytes() == fixture[3][name] for name, path in fixture[1].items())


def test_health_failure_restores_templates_before_old_code_restarts(tmp_path):
    local, operation = _repo(tmp_path)
    fixture = _fixture(tmp_path)
    publication = _prepare(tmp_path, fixture)
    _, targets, _, old, new = fixture
    starts = []
    updater = RepositoryUpdater(
        local, _State(), database_path=tmp_path / 'absent.db',
        publication=lambda: publication,
        restart=lambda: starts.append({name: path.read_bytes() for name, path in targets.items()}),
        health=lambda: len(starts) > 1,
    )
    with pytest.raises(UpgradeFailed):
        updater.execute(operation)
    assert starts == [new, old]
    assert (local / 'version').read_text() == 'old\n'


@pytest.mark.parametrize('requested', [False, True])
def test_deploy_cli_passes_explicit_prompt_publication(monkeypatch, requested):
    import app.deploy as deploy_module
    calls = []
    monkeypatch.setattr(deploy_module, 'deploy', lambda *args, **kwargs: calls.append(kwargs) or 'offline')
    monkeypatch.setattr(sys, 'argv', ['app.deploy'] + (['--publish-prompt-templates'] if requested else []))
    assert deploy_module.main() == 0
    assert calls[0]['publish_prompt_templates'] is requested


@pytest.mark.parametrize('flags, message', [
    (['--restart', '--publish-prompt-templates'], '--restart cannot publish prompt templates'),
    (['--publish-consumer-system-contracts', '--publish-prompt-templates'], 'require separate deployments'),
])
def test_deploy_rejects_incompatible_publication_options(monkeypatch, capsys, flags, message):
    import app.deploy as deploy_module
    monkeypatch.setattr(sys, 'argv', ['app.deploy', *flags])
    with pytest.raises(SystemExit) as exc:
        deploy_module.main()
    assert exc.value.code == 2
    assert message in capsys.readouterr().err


def test_deploy_prompt_path_uses_production_environment_and_launchd_override(tmp_path, monkeypatch):
    import app.deploy as deploy_module
    root = tmp_path / 'production'
    root.mkdir()
    (root / '.env').write_text('CEO_DEVELOPER_PROMPT_TEMPLATE_PATH=relative/developer.md\nCEO_USER_PROMPT_TEMPLATE_PATH=/outside/user.md\n')
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('CEO_DEVELOPER_PROMPT_TEMPLATE_PATH', '/wrong/dev-checkout.md')
    monkeypatch.setenv('CEO_USER_PROMPT_TEMPLATE_PATH', '/wrong/dev-checkout-user.md')
    assert deploy_module._production_prompt_template_paths(root) == (root / 'relative/developer.md', Path('/outside/user.md'))
    plist = tmp_path / 'Library/LaunchAgents/com.ceo-agent-service.main.plist'
    plist.parent.mkdir(parents=True)
    plist.write_bytes(plistlib.dumps({'EnvironmentVariables': {'CEO_DEVELOPER_PROMPT_TEMPLATE_PATH': '~/installed/developer.md'}}))
    assert deploy_module._production_prompt_template_paths(root) == (Path('~/installed/developer.md').expanduser(), Path('/outside/user.md'))


def test_deploy_explicit_prompt_publication_runs_even_when_code_already_current(tmp_path, monkeypatch):
    import app.deploy as deploy_module
    local, operation = _repo(tmp_path)
    # Fast-forward this offline fixture without invoking a service.
    import subprocess
    subprocess.run(['git', 'merge', '--ff-only', 'origin/main'], cwd=local, check=True, capture_output=True)
    events = []
    class Receipt:
        def publish(self): events.append('publish')
        def verify_loaded(self): events.append('loaded')
        def rollback(self): events.append('rollback')
        def finalize(self): events.append('finalize')
    monkeypatch.setattr(deploy_module, 'prepare_prompt_templates', lambda **kwargs: Receipt())
    monkeypatch.setattr(deploy_module, 'ExistingSchemaUpgradeStateStore', lambda db: _State())
    monkeypatch.setattr(deploy_module, '_default_stop', lambda: events.append('stop'))
    monkeypatch.setattr(deploy_module, '_default_start', lambda: events.append('start'))
    monkeypatch.setattr(deploy_module, 'wait_until_quiet', lambda db: events.append('quiet'))
    monkeypatch.setattr(deploy_module, 'build_frontend', lambda *args: None)
    monkeypatch.setattr(deploy_module, 'verify_imports', lambda *args: None)
    monkeypatch.setattr(deploy_module, 'wait_for_health', lambda: True)
    deploy_module.deploy(local, tmp_path / 'absent.db', publish_prompt_templates=True)
    assert events == ['quiet', 'stop', 'publish', 'start', 'loaded', 'finalize']


def test_partial_publication_failure_rolls_back_before_old_restart(tmp_path, monkeypatch):
    local, operation = _repo(tmp_path)
    fixture = _fixture(tmp_path)
    publication = _prepare(tmp_path, fixture)
    _, targets, _, old, _ = fixture
    module = importlib.import_module('app.prompt_template_release')
    replace = module._atomic_replace
    failed = False
    def fail_once_after_second_write(path, data, mode):
        nonlocal failed
        replace(path, data, mode)
        if path == targets['user_prompt'] and not failed:
            failed = True
            raise OSError('partial publication')
    monkeypatch.setattr(module, '_atomic_replace', fail_once_after_second_write)
    starts = []
    updater = RepositoryUpdater(
        local, _State(), database_path=tmp_path / 'absent.db',
        publication=lambda: publication,
        restart=lambda: starts.append({name: path.read_bytes() for name, path in targets.items()}),
        health=lambda: True,
    )
    with pytest.raises(UpgradeFailed):
        updater.execute(operation)
    assert starts == [old]
    assert (local / 'version').read_text() == 'old\n'
    assert json.loads((publication.receipt_dir / 'receipt.json').read_text())['status'] == 'rolled_back'


def test_deploy_default_prompt_paths_belong_to_production_checkout(tmp_path, monkeypatch):
    import app.deploy as deploy_module
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    root = tmp_path / 'production'
    assert deploy_module._production_prompt_template_paths(root) == (
        root / 'data/prompts/developer_prompt.md', root / 'data/prompts/user_prompt.md',
    )


def test_checked_release_manifest_pins_baseline_and_current_two_defaults():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / 'ci/prompt-template-release.json').read_text())
    assert manifest['version'] == 1
    expected = {
        'developer_prompt': (
            'b2bb8ed69298c7fa57bf95c54fa6693fa76d265e90d07cb666ab05e35cfe2623',
            '3ccd6261291b6610aa95a4764d4fd81b7c232011f8ca7dbc52b18639703fb457',
        ),
        'user_prompt': (
            '5106c30c0ea2cbf133ec537a600d3250b699659c786892eaa46434cdb6eff398',
            '164feb55f0f14f01253f5394eae21e692a64d089e60e868872ec39a2579a1866',
        ),
    }
    assert len(manifest['files']) == 2
    assert {entry['target'] for entry in manifest['files']} == set(expected)
    for entry in manifest['files']:
        old_sha, new_sha = expected[entry['target']]
        assert entry['source'] == f"app/defaults/{entry['target']}.md"
        assert entry['old_sha256'] == old_sha
        assert entry['new_sha256'] == new_sha == _digest((root / entry['source']).read_bytes())


def test_tracked_common_developer_template_matches_approved_release_default():
    root = Path(__file__).resolve().parents[1]
    tracked = root / 'data/prompts/developer_prompt.md'
    source = root / 'app/defaults/developer_prompt.md'
    assert tracked.read_bytes() == source.read_bytes()
