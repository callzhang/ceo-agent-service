from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.skill_discovery import SkillSearchResult
from app.skill_repositories import RepositorySkill, SkillRepositoryError
from app.skill_installation import SkillInstallResult


def client_for(*, search=None, install=None):
    from app.web_api.skill_installation import register_skill_installation_routes
    class Discovery:
        def search(self, query):
            return search(query)
    class Installer:
        def install(self, **kwargs):
            return install(**kwargs)
    app = FastAPI()
    register_skill_installation_routes(app, discovery_factory=Discovery, installation_factory=Installer)
    return TestClient(app)


def test_search_returns_metadata_and_install_preserves_exact_identity():
    row = RepositorySkill('https://github.com/example/skills', 'a' * 40, 'skills/example', 'example', 'Example', 'Owner', 'maintainer', '2026-01-01T00:00:00Z')
    def install(**kwargs):
        assert kwargs == {'repo_url': row.repo_url, 'commit': row.commit, 'skill_path': row.skill_path}
        return SkillInstallResult('example', 1, 2, 'b' * 64, 'c' * 64, 'source', 'installed', 'pending_restart')
    client = client_for(search=lambda query: SkillSearchResult((row,)), install=install)
    response = client.post('/api/console/settings/skill-search', json={'query': 'example'})
    assert response.status_code == 200
    assert response.json()['items'][0]['author'] == 'Owner'
    installed = client.post('/api/console/settings/skill-installs', json={'repo_url': row.repo_url, 'commit': row.commit, 'skill_path': row.skill_path})
    assert installed.status_code == 201
    assert installed.json()['activation_status'] == 'pending_restart'


@pytest.mark.parametrize('error,status,code', [(ValueError('bad source'), 422, 'validation_error'), (ValueError('name conflict'), 409, 'conflict'), (SkillRepositoryError('unavailable'), 502, 'upstream_error'), (TimeoutError('timed out'), 504, 'timeout')])
def test_search_errors_are_not_empty_success(error, status, code):
    def fail(query):
        raise error
    response = client_for(search=fail).post('/api/console/settings/skill-search', json={'query': 'example'})
    assert response.status_code == status
    assert response.json()['code'] == code
    assert 'items' not in response.json()


def test_invalid_install_request_is_rejected_before_install():
    def unexpected(**kwargs):
        pytest.fail('invalid request reached installer')
    response = client_for(install=unexpected).post('/api/console/settings/skill-installs', json={'repo_url': 12})
    assert response.status_code == 422
