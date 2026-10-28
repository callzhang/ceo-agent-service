"""Repository-aware public Skill discovery; direct URLs never enter the index."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
import time
from urllib.parse import urlsplit

import httpx

from app.skill_repositories import RepositorySkill, SkillRepositoryError, SkillRepositoryReader, repository_url


@dataclass(frozen=True)
class SkillSearchError:
    source: str
    code: str
    message: str


@dataclass(frozen=True)
class SkillSearchResult:
    items: tuple[RepositorySkill, ...]
    errors: tuple[SkillSearchError, ...] = ()


class SkillDiscoveryService:
    def __init__(self, *, reader=None, client=None):
        self.reader = reader or SkillRepositoryReader()
        self.client = client

    def search(self, query: str) -> SkillSearchResult:
        if not isinstance(query, str) or not query.strip():
            raise ValueError('Enter a repository URL or public Skill name')
        query = query.strip()
        deadline = time.monotonic() + 120
        if urlsplit(query).scheme or query.startswith('git@'):
            return SkillSearchResult(self.reader.list_skills(repository_url(query), deadline=deadline))
        if self.client is None:
            with httpx.Client(timeout=30) as client:
                return self._public_search(query, deadline, client)
        return self._public_search(query, deadline, self.client)

    def _public_search(self, query, deadline, client):
        try:
            response = client.get('https://skills.sh/api/search', params={'q': query, 'limit': 10}, timeout=30)
            response.raise_for_status()
            hits = response.json()['skills'][:10]
            if not isinstance(hits, list):
                raise ValueError('Malformed public index response')
        except httpx.TimeoutException as exc:
            raise TimeoutError('Public Skill search timed out') from exc
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise SkillRepositoryError('Public Skill index is unavailable') from exc
        sources = {}
        for hit in hits:
            if not isinstance(hit, dict) or not isinstance(hit.get('name'), str) or not isinstance(hit.get('source'), str):
                raise SkillRepositoryError('Public Skill index returned invalid metadata')
            parts = hit['source'].split('/')
            if len(parts) != 2 or any(not p or any(c.isspace() for c in p) or p in ('.', '..') for p in parts):
                raise SkillRepositoryError('Public Skill index returned invalid repository')
            sources[hit['source']] = 'https://github.com/' + hit['source']
        executor = ThreadPoolExecutor(max_workers=2)
        futures = {source: executor.submit(self.reader.list_skills, url, deadline=deadline) for source, url in sources.items()}
        rows, errors = {}, []
        try:
            for source, future in futures.items():
                try:
                    rows[source] = future.result(timeout=max(0, deadline - time.monotonic()))
                except (SkillRepositoryError, ValueError, TimeoutError, FutureTimeout) as exc:
                    message = 'Repository metadata timed out' if isinstance(exc, (TimeoutError, FutureTimeout)) else str(exc)
                    errors.append(SkillSearchError(source, 'metadata_unavailable', message))
                    rows[source] = ()
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        results = []
        for hit in hits:
            matches = [row for row in rows[hit['source']] if row.name == hit['name']]
            if matches:
                results.extend(matches)
            else:
                results.append(RepositorySkill(sources[hit['source']], '', '', hit['name'], available=False, unavailable_reason='Skill metadata could not be read from its repository'))
        return SkillSearchResult(tuple(results), tuple(errors))
