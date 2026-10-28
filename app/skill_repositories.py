"""Read portable Skill packages from exact Git revisions using native credentials."""
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, replace
import hashlib
import os
from pathlib import Path, PurePosixPath
import string
import subprocess
import tempfile
import time
from urllib.parse import urlsplit

from app.business_skills import BusinessSkillValidationError, _parse_frontmatter
from app.managed_skills import validate_managed_skill_content


class SkillRepositoryError(RuntimeError):
    """The native Git repository could not be read."""


@dataclass(frozen=True)
class RepositorySkill:
    repo_url: str
    commit: str
    skill_path: str
    name: str
    description: str = ''
    author: str = ''
    author_kind: str = 'unknown'
    updated_at: str | None = None
    available: bool = True
    unavailable_reason: str = ''


@dataclass(frozen=True)
class PackageFile:
    path: str
    content: bytes
    executable: bool = False


@dataclass(frozen=True)
class SkillPackage:
    candidate: RepositorySkill
    files: tuple[PackageFile, ...]
    package_sha256: str


def repository_url(value: str) -> str:
    """Normalize supported HTTPS/SSH repository identities, never public keywords."""
    if not isinstance(value, str) or not value.strip() or any(c.isspace() for c in value.strip()):
        raise ValueError('Enter an HTTPS or SSH Git repository URL')
    value = value.strip()
    if value.startswith('git@') and ':' in value:
        host, path = value.split(':', 1)
        if not host[4:] or not path or path.startswith('/'):
            raise ValueError('Invalid SSH Git repository URL')
    else:
        parsed = urlsplit(value)
        if parsed.scheme not in ('https', 'ssh') or not parsed.hostname or not parsed.path.strip('/'):
            raise ValueError('Enter an HTTPS or SSH Git repository URL')
        if parsed.password or (parsed.scheme == 'https' and parsed.username) or parsed.query or parsed.fragment:
            raise ValueError('Use native Git authentication; do not put credentials in the repository URL')
        path = parsed.path.strip('/')
    parts = PurePosixPath(path).parts
    if any(p in ('.', '..', 'tree', 'blob') for p in parts) or path.endswith(('.zip', '.tar', '.tgz', '.gz')):
        raise ValueError('Enter a repository URL, not a file, archive or tree URL')
    return value.rstrip('/')


def package_digest(files: tuple[PackageFile, ...]) -> str:
    digest = hashlib.sha256()
    for item in sorted(files, key=lambda f: f.path):
        name = item.path.encode('utf-8')
        digest.update(len(name).to_bytes(8, 'big') + name)
        digest.update(bytes([item.executable]))
        digest.update(len(item.content).to_bytes(8, 'big') + item.content)
    return digest.hexdigest()


class SkillRepositoryReader:
    def __init__(self, *, run=subprocess.run):
        self.run = run

    def _git(self, *args, cwd=None, deadline=None) -> bytes:
        remaining = 30 if deadline is None else min(30, deadline - time.monotonic())
        if remaining <= 0:
            raise TimeoutError('Repository search timed out')
        try:
            result = self.run(['git', *args], cwd=cwd, timeout=remaining,
                              capture_output=True, check=True,
                              env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError('Repository read timed out') from exc
        except (subprocess.CalledProcessError, OSError) as exc:
            raise SkillRepositoryError('Cannot read repository; check native Git access for the service account') from exc
        return result.stdout

    @contextmanager
    def _repository(self, repo_url, commit, deadline):
        url = repository_url(repo_url)
        if commit is not None and (len(commit) not in (40, 64) or any(c not in string.hexdigits for c in commit)):
            raise ValueError('Install requires the exact commit returned by search')
        with tempfile.TemporaryDirectory(prefix='skill-repository-') as temporary:
            root = Path(temporary) / 'repository.git'
            self._git('clone', '--bare', '--quiet', '--', url, str(root), deadline=deadline)
            resolved = self._git('rev-parse', '--verify', f'{commit or "HEAD"}^{{commit}}', cwd=root, deadline=deadline).decode().strip()
            tree = self._git('ls-tree', '-rz', resolved, cwd=root, deadline=deadline)
            entries = []
            for raw in tree.split(b'\0'):
                if not raw:
                    continue
                metadata, path = raw.split(b'\t', 1)
                mode, kind, oid = metadata.decode().split()
                name = path.decode('utf-8')
                if kind == 'blob' and mode in ('100644', '100755') and '__pycache__' not in PurePosixPath(name).parts and not name.endswith('.pyc'):
                    entries.append((name, oid, mode == '100755'))
            yield root, resolved, entries

    def _metadata(self, root, repo_url, commit, path, oid, deadline):
        name = PurePosixPath(path).name if path != '.' else 'root'
        try:
            content = self._git('cat-file', 'blob', oid, cwd=root, deadline=deadline).decode('utf-8')
            meta = _parse_frontmatter(content, Path(path) / 'SKILL.md')
            name = meta.get('name', name)
            validate_managed_skill_content(name, content, require_managed_marker=False)
        except (ValueError, UnicodeError, BusinessSkillValidationError) as exc:
            return RepositorySkill(repo_url, commit, path, str(name), available=False, unavailable_reason=str(exc))
        metadata = meta.get('metadata')
        author = (metadata.get('author') if isinstance(metadata, dict) else None) or meta.get('author')
        if isinstance(author, str) and author.strip():
            author, author_kind = author.strip(), 'author'
        else:
            urlpath = repo_url.split(':', 1)[1] if repo_url.startswith('git@') else urlsplit(repo_url).path
            parts = PurePosixPath(urlpath.strip('/')).parts
            author = '/'.join(parts[:-1])
            author_kind = 'maintainer' if author else 'unknown'
        stamp = self._git('log', '-1', '--format=%cI', commit, '--', path, cwd=root, deadline=deadline).decode().strip()
        return RepositorySkill(repo_url, commit, path, name, meta['description'], author, author_kind, stamp or None)

    def list_skills(self, repo_url: str, *, commit: str | None = None, deadline=None) -> tuple[RepositorySkill, ...]:
        repo_url = repository_url(repo_url)
        with self._repository(repo_url, commit, deadline) as (root, resolved, entries):
            rows = []
            for file, oid, _executable in entries:
                parts = PurePosixPath(file).parts
                if file == 'SKILL.md' or (len(parts) == 3 and parts[0] == 'skills' and parts[-1] == 'SKILL.md') or (len(parts) == 4 and parts[:2] == ('.agents', 'skills') and parts[-1] == 'SKILL.md'):
                    rows.append(self._metadata(root, repo_url, resolved, str(PurePosixPath(file).parent), oid, deadline))
            counts = Counter(row.name for row in rows if row.available)
            return tuple(replace(row, available=False, unavailable_reason='Duplicate declared Skill name in repository') if counts[row.name] > 1 else row for row in rows)

    def read_package(self, repo_url: str, commit: str, skill_path: str) -> SkillPackage:
        if not isinstance(skill_path, str) or not skill_path or PurePosixPath(skill_path).is_absolute() or '..' in PurePosixPath(skill_path).parts:
            raise ValueError('Invalid Skill directory')
        deadline = time.monotonic() + 120
        with self._repository(repo_url, commit, deadline) as (root, resolved, entries):
            prefix = '' if skill_path == '.' else skill_path.rstrip('/') + '/'
            matches = [(name, oid, execute) for name, oid, execute in entries if name.startswith(prefix)]
            skill = next(((oid, name) for name, oid, _ in matches if name == prefix + 'SKILL.md'), None)
            if skill is None:
                raise ValueError('Skill directory has no SKILL.md')
            candidate = self._metadata(root, repo_url, resolved, skill_path, skill[0], deadline)
            if not candidate.available:
                raise ValueError(candidate.unavailable_reason)
            files = tuple(PackageFile(name[len(prefix):], self._git('cat-file', 'blob', oid, cwd=root, deadline=deadline), execute) for name, oid, execute in matches)
            return SkillPackage(candidate, files, package_digest(files))
