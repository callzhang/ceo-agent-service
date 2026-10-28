import subprocess

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.business_skills import install_public_skill_baseline
from app.managed_skills import REPOSITORY_MANAGED_SKILL_NAMES, resolve_pending_runtime_skills
from app.skill_repositories import SkillRepositoryReader
from app.skill_discovery import SkillDiscoveryService
from app.skill_installation import SkillInstallationService
from app.store import AutoReplyStore
from app.web_api.registration import register_console_routes


def test_first_deploy_search_install_restart_keeps_exact_revision(tmp_path, monkeypatch):
    source, installed = tmp_path / 'public-release', tmp_path / 'installed'
    for name in REPOSITORY_MANAGED_SKILL_NAMES:
        folder = source / name
        folder.mkdir(parents=True)
        (folder / 'SKILL.md').write_text(f'---\nname: {name}\ndescription: Generic example\nmetadata:\n  managed_by: ceo-agent-service\n---\nExample')
    install_public_skill_baseline(source, installed)
    monkeypatch.setenv('CEO_SKILLS_ROOT', str(installed))
    store = AutoReplyStore(tmp_path / 'runtime.sqlite3')
    before = resolve_pending_runtime_skills(store, pid=101)
    repo = tmp_path / 'repository'
    repo.mkdir()
    def git(*args):
        return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()
    git('init', '-q')
    git('config', 'user.email', 'example@example.invalid')
    git('config', 'user.name', 'Example')
    for name in ['one', 'two']:
        folder = repo / 'skills' / name
        folder.mkdir(parents=True)
        (folder / 'SKILL.md').write_text(f'---\nname: {name}\ndescription: Capability {name}\n---\nExample')
        (folder / 'references').mkdir()
        (folder / 'references/guide.md').write_text('Support material')
    git('add', '.')
    git('commit', '-qm', 'Synthetic initial package')
    commit = git('rev-parse', 'HEAD')
    url = 'https://github.com/example/synthetic-private-repo'
    def run(args, **kwargs):
        return subprocess.run([str(repo) if a == url else a for a in args], **kwargs)
    reader = SkillRepositoryReader(run=run)
    app = FastAPI()
    register_console_routes(app, store_factory=lambda: store, status_payload_factory=lambda: {}, feedback_backlog_factory=lambda: {}, attention_rows_factory=lambda: [], skill_discovery_factory=lambda: SkillDiscoveryService(reader=reader), skill_installation_factory=lambda: SkillInstallationService(store=store, reader=reader, skills_root=installed))
    client = TestClient(app)
    found = client.post('/api/console/settings/skill-search', json={'query': url})
    assert found.status_code == 200
    assert {r['name'] for r in found.json()['items']} == {'one', 'two'}
    chosen = next(r for r in found.json()['items'] if r['name'] == 'one')
    (repo / 'skills/one/SKILL.md').write_text('Later source change')
    git('commit', '-am', 'Move default branch')
    result = client.post('/api/console/settings/skill-installs', json={k: chosen[k] for k in ['repo_url', 'commit', 'skill_path']})
    assert result.status_code == 201
    assert chosen['commit'] == commit
    assert (installed / 'one/references/guide.md').read_text() == 'Support material'
    assert not (installed / 'two').exists()
    assert 'Later source change' not in (installed / 'one/SKILL.md').read_text()
    listed = client.get('/api/console/settings/managed-skills').json()['items']
    assert 'one' in {r['name'] for r in listed}
    assert store.get_active_runtime_skill_config().id == before.config_id
    restarted = resolve_pending_runtime_skills(store, pid=202)
    assert any(r.id == result.json()['revision_id'] and r.sha256 == result.json()['sha256'] for r in restarted.revisions)
    receipts = store.list_runtime_skill_load_receipts(restarted.config_id)
    assert receipts[-1].pid == 202 and not receipts[-1].error
    assert set(r.id for r in before.revisions) <= set(r.id for r in restarted.revisions)
