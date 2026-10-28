"""Install one exact repository package into existing managed runtime state."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile

from app.business_skills import _validate_install_target, _validate_swap_destination
from app.managed_skills import validate_managed_skill_name
from app.skill_repositories import SkillRepositoryReader


@dataclass(frozen=True)
class SkillInstallResult:
    name: str
    skill_id: int
    revision_id: int
    sha256: str
    package_sha256: str
    source: str
    install_status: str
    activation_status: str


class SkillInstallationService:
    def __init__(self, *, store, reader=None, skills_root=None):
        self.store = store
        self.reader = reader or SkillRepositoryReader()
        self.skills_root = Path(skills_root or Path.home() / '.agents/skills').expanduser()

    def install(self, repo_url: str, commit: str, skill_path: str) -> SkillInstallResult:
        package = self.reader.read_package(repo_url, commit, skill_path)
        candidate = package.candidate
        name = validate_managed_skill_name(candidate.name)
        source = json.dumps({'kind': 'git-skill', 'version': 1, 'repo_url': candidate.repo_url,
                             'commit': candidate.commit, 'skill_path': candidate.skill_path,
                             'package_sha256': package.package_sha256}, separators=(',', ':'))
        root = self.skills_root
        _validate_install_target(root)
        root.mkdir(parents=True, exist_ok=True)
        resolved_root = root.resolve(strict=True)
        target = root / name
        with self.store.managed_skill_baseline_initialization_lock():
            existing = self.store.get_managed_skill_by_name(name)
            if existing is not None:
                revisions = self.store.list_managed_skill_revisions(existing.id)
                revision = next((r for r in revisions if r.source == source), None)
                if revision is None or target.is_symlink() or not target.is_dir():
                    raise ValueError('Skill name or package conflict; existing content was preserved')
                actual_files = {str(p.relative_to(target)) for p in target.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}
                if actual_files != {f.path for f in package.files} or any((target / f.path).is_symlink() or (target / f.path).read_bytes() != f.content or bool((target / f.path).stat().st_mode & 0o111) != f.executable for f in package.files):
                    raise ValueError('Installed Skill content conflict; local edits were preserved')
                active = self.store.get_active_runtime_skill_config()
                loaded = active and any(b.skill_id == existing.id and b.revision_id == revision.id and b.enabled for b in self.store.list_runtime_skill_bindings(active.id))
                return SkillInstallResult(name, existing.id, revision.id, revision.sha256, package.package_sha256, source, 'already_installed', 'active' if loaded else 'pending_restart')
            if target.exists() or target.is_symlink():
                raise ValueError('Installed Skill name conflict; existing files were preserved')
            with tempfile.TemporaryDirectory(prefix='.skill-install-', dir=root.parent) as temporary:
                staged = Path(temporary) / name
                staged.mkdir()
                for item in package.files:
                    relative = Path(item.path)
                    if relative.is_absolute() or '..' in relative.parts:
                        raise ValueError('Invalid package path')
                    path = staged / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(item.content)
                    path.chmod(0o755 if item.executable else 0o644)
                current = self.store.get_latest_runtime_skill_config()
                published = False
                def publish():
                    nonlocal published
                    _validate_swap_destination(root, resolved_root, target)
                    if target.exists():
                        raise ValueError('Installed Skill name conflict')
                    os.rename(staged, target)
                    published = True
                try:
                    revision, _config = self.store.install_repository_skill(
                        name, name, (staged / 'SKILL.md').read_bytes().decode('utf-8'),
                        source=source, expected_parent_id=current.id if current else None,
                        publish_package=publish,
                    )
                except BaseException:
                    if published:
                        os.rename(target, staged)
                    raise
            return SkillInstallResult(name, revision.skill_id, revision.id, revision.sha256, package.package_sha256, source, 'installed', 'pending_restart')
