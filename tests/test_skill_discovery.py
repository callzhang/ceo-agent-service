import httpx
import pytest

from app.skill_repositories import RepositorySkill, SkillRepositoryError


class Reader:
    def __init__(self, rows=(), failure=None):
        self.rows, self.failure, self.urls = rows, failure, []
    def list_skills(self, url, **kwargs):
        self.urls.append(url)
        if self.failure:
            raise self.failure
        return self.rows


def test_public_name_hydrates_description_author_and_skill_time():
    from app.skill_discovery import SkillDiscoveryService
    row = RepositorySkill('https://github.com/example/repo', 'a' * 40, 'skills/example', 'example', 'Actual description', 'Actual Author', 'author', '2026-01-01T00:00:00Z')
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={'skills': [{'name': 'example', 'source': 'example/repo', 'id': 'example/repo/example'}]})))
    result = SkillDiscoveryService(reader=Reader((row,)), client=client).search('example')
    assert result.items == (row,)
    assert not result.errors


def test_repository_query_never_calls_index():
    from app.skill_discovery import SkillDiscoveryService
    def no_request(req):
        pytest.fail('private URL was sent to public index')
    reader = Reader(failure=SkillRepositoryError('service account cannot access'))
    service = SkillDiscoveryService(reader=reader, client=httpx.Client(transport=httpx.MockTransport(no_request)))
    with pytest.raises(SkillRepositoryError):
        service.search('git@private.example:team/skills.git')


def test_provider_failure_is_not_empty_success():
    from app.skill_discovery import SkillDiscoveryService
    service = SkillDiscoveryService(reader=Reader(), client=httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(503))))
    with pytest.raises(SkillRepositoryError):
        service.search('example')


def test_limit_and_repository_deduplication():
    from app.skill_discovery import SkillDiscoveryService
    rows = tuple(RepositorySkill('https://github.com/example/repo', 'a' * 40, f'skills/s{i}', f's{i}') for i in range(12))
    reader = Reader(rows)
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={'skills': [{'name': f's{i}', 'source': 'example/repo', 'id': f'example/repo/s{i}'} for i in range(12)]})))
    result = SkillDiscoveryService(reader=reader, client=client).search('example')
    assert [r.name for r in result.items] == [f's{i}' for i in range(10)]
    assert reader.urls == ['https://github.com/example/repo']


def test_unavailable_match_preserves_usable_siblings():
    from app.skill_discovery import SkillDiscoveryService
    rows = (RepositorySkill('https://github.com/example/repo', 'a' * 40, 'skills/a', 'a'), RepositorySkill('https://github.com/example/repo', 'a' * 40, 'skills/b', 'b', available=False, unavailable_reason='bad metadata'))
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={'skills': [{'name': n, 'source': 'example/repo', 'id': n} for n in ['a', 'b']]})))
    result = SkillDiscoveryService(reader=Reader(rows), client=client).search('example')
    assert result.items[0].available
    assert not result.items[1].available
    assert result.items[1].unavailable_reason == 'bad metadata'


def test_late_metadata_timeout_is_explicit():
    from app.skill_discovery import SkillDiscoveryService
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={'skills': [{'name': 'example', 'source': 'example/repo', 'id': 'example'}]})))
    result = SkillDiscoveryService(reader=Reader(failure=TimeoutError('timeout')), client=client).search('example')
    assert not result.items[0].available
    assert result.errors[0].message == 'Repository metadata timed out'


@pytest.mark.parametrize('query', ['./private-skills', '../private-skills', '/Users/example/private-skills', '~/private-skills', 'skills/private-example'])
def test_local_paths_are_rejected_without_public_lookup(query):
    from app.skill_discovery import SkillDiscoveryService
    def no_request(req):
        pytest.fail('local path was sent to public index')
    service = SkillDiscoveryService(reader=Reader(), client=httpx.Client(transport=httpx.MockTransport(no_request)))
    with pytest.raises(ValueError):
        service.search(query)


def test_non_git_ssh_user_is_not_a_public_keyword():
    from app.skill_discovery import SkillDiscoveryService
    def no_request(req):
        pytest.fail('private SSH source was sent to public index')
    service = SkillDiscoveryService(reader=Reader(failure=SkillRepositoryError('service account access missing')), client=httpx.Client(transport=httpx.MockTransport(no_request)))
    with pytest.raises(SkillRepositoryError):
        service.search('alice@private.example:team/skills.git')


def test_incomplete_ssh_source_is_rejected_without_public_lookup():
    from app.skill_discovery import SkillDiscoveryService
    def no_request(req):
        pytest.fail('private source fragment reached the public index')
    service = SkillDiscoveryService(reader=Reader(), client=httpx.Client(transport=httpx.MockTransport(no_request)))
    with pytest.raises(ValueError):
        service.search('git@private.example')
