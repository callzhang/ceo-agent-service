from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import tempfile
import threading
import yaml

from app.business_skills import (
    MANAGED_BY,
    BusinessSkillValidationError,
    _parse_frontmatter,
    _required_scalar,
    sync_bundled_skill,
)

_LOCKS_GUARD = threading.Lock()
_SKILL_LOCKS: dict[Path, threading.Lock] = {}


class SkillFileError(RuntimeError):
    """Base error for project Skill file operations."""


class SkillFileValidationError(SkillFileError):
    """A Skill name, path, or frontmatter is invalid."""


class SkillFileOwnershipError(SkillFileValidationError):
    """A service-managed Skill was presented as an operation Skill."""


class SkillFileConflict(SkillFileError):
    """The source changed since the caller last read it."""


class SkillFileSyncError(SkillFileError):
    """The source write or runtime synchronization failed."""

    def __init__(self, stage: str, cause: BaseException):
        self.stage = stage
        self.cause = cause
        super().__init__(f"Skill {stage} failed: {cause}")


@dataclass(frozen=True)
class ProjectSkill:
    name: str
    path: Path


@dataclass(frozen=True)
class SkillDocument:
    name: str
    description: str
    managed_by: str | None
    path: Path
    content: str
    sha256: str
    raw_bytes: bytes = b""


class SkillFileService:
    def __init__(
        self,
        project_skills_root: Path | str | None = None,
        *,
        runtime_skills_root: Path | str | None = None,
    ) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        self.project_skills_root = Path(project_skills_root or repository_root / "skills").expanduser().resolve()
        self.runtime_skills_root = (
            Path.home() / ".agents" / "skills"
            if runtime_skills_root is None
            else Path(runtime_skills_root).expanduser()
        )

    def list_skills(self) -> tuple[ProjectSkill, ...]:
        if not self.project_skills_root.is_dir() or self.project_skills_root.is_symlink():
            return ()
        result: list[ProjectSkill] = []
        for directory in sorted(self.project_skills_root.iterdir(), key=lambda item: item.name):
            if not directory.is_dir() or directory.is_symlink():
                continue
            path = directory / "SKILL.md"
            if path.is_file() and not path.is_symlink():
                result.append(ProjectSkill(directory.name, path))
        return tuple(result)

    def get_skill(self, name: str) -> SkillDocument:
        path, raw_bytes, content = self._read_skill_file(name)
        try:
            frontmatter = _parse_frontmatter(content, path)
        except BusinessSkillValidationError as exc:
            raise SkillFileValidationError(str(exc)) from exc
        if _required_scalar(frontmatter, "name", path) != name:
            raise SkillFileValidationError(f"Skill name does not match directory: {path}")
        description = _required_scalar(frontmatter, "description", path)
        metadata = frontmatter.get("metadata")
        if not isinstance(metadata, dict) or metadata.get("managed_by") != MANAGED_BY:
            raise SkillFileValidationError(f"Skill missing managed marker {MANAGED_BY!r}: {path}")
        return SkillDocument(
            name=name,
            description=description,
            managed_by=MANAGED_BY,
            path=path,
            content=content,
            sha256=hashlib.sha256(raw_bytes).hexdigest(),
            raw_bytes=raw_bytes,
        )

    def get_operation_skill(self, name: str) -> SkillDocument:
        """Read one installed operation Skill without requiring service ownership."""
        path, raw_bytes, content = self._read_skill_file(name)
        frontmatter = _parse_standard_skill_frontmatter(content, path)
        declared_name = frontmatter.get("name")
        description = frontmatter.get("description")
        metadata = frontmatter.get("metadata")
        if not isinstance(declared_name, str) or not declared_name.strip():
            raise SkillFileValidationError(f"Skill must have nonempty name: {path}")
        if declared_name.strip() != name:
            raise SkillFileValidationError(f"Skill name does not match directory: {path}")
        if not isinstance(description, str) or not description.strip():
            raise SkillFileValidationError(f"Skill must have nonempty description: {path}")
        if not isinstance(metadata, dict):
            raise SkillFileValidationError(f"Skill metadata must be a mapping: {path}")
        managed_by = metadata.get("managed_by")
        if managed_by == MANAGED_BY:
            raise SkillFileOwnershipError(
                f"service-managed Skill cannot be used as an operation Skill: {path}"
            )
        return SkillDocument(
            name=name,
            description=description.strip(),
            managed_by=managed_by if isinstance(managed_by, str) else None,
            path=path,
            content=content,
            sha256=hashlib.sha256(raw_bytes).hexdigest(),
            raw_bytes=raw_bytes,
        )

    def _read_skill_file(self, name: str) -> tuple[Path, bytes, str]:
        path = self._resolve_skill_path(name)
        try:
            raw_bytes = path.read_bytes()
            content = raw_bytes.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise SkillFileValidationError(f"unable to read Skill: {path}: {exc}") from exc
        return path, raw_bytes, content

    def save_skill(self, name: str, content: str, expected_sha256: str) -> SkillDocument:
        if not isinstance(content, str):
            raise SkillFileValidationError("Skill content must be text")
        path = self._resolve_skill_path(name)
        with _skill_lock(path):
            current = self.get_skill(name)
            if current.sha256 != expected_sha256:
                raise SkillFileConflict(
                    f"Skill {name!r} changed; expected {expected_sha256}, current {current.sha256}"
                )
            candidate = self._validate_content(name, current.path, content)
            old_bytes = current.raw_bytes
            try:
                self._atomic_write(current.path, candidate.encode("utf-8"))
            except BaseException as exc:
                raise SkillFileSyncError("source", exc) from exc
            try:
                self._sync_runtime(name, current.path)
            except BaseException as sync_error:
                try:
                    self._atomic_write(current.path, old_bytes)
                except BaseException as restore_error:
                    raise SkillFileSyncError("sync-and-restore", restore_error) from sync_error
                raise SkillFileSyncError("runtime-sync", sync_error) from sync_error
            return self.get_skill(name)

    def _resolve_skill_path(self, name: str) -> Path:
        _validate_skill_name(name)
        path = self.project_skills_root / name / "SKILL.md"
        try:
            resolved = path.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise SkillFileValidationError(f"unable to resolve Skill path: {path}") from exc
        if resolved.parent.parent != self.project_skills_root or path.parent.is_symlink() or path.is_symlink():
            raise SkillFileValidationError(f"Skill path escaped project skills root: {path}")
        if not path.is_file():
            raise SkillFileValidationError(f"unknown Skill: {name}")
        return path

    def _validate_content(self, name: str, path: Path, content: str) -> str:
        try:
            frontmatter = _parse_frontmatter(content, path)
        except BusinessSkillValidationError as exc:
            raise SkillFileValidationError(str(exc)) from exc
        if _required_scalar(frontmatter, "name", path) != name:
            raise SkillFileValidationError(f"Skill name does not match directory: {path}")
        _required_scalar(frontmatter, "description", path)
        metadata = frontmatter.get("metadata")
        if not isinstance(metadata, dict) or metadata.get("managed_by") != MANAGED_BY:
            raise SkillFileValidationError(f"Skill missing managed marker {MANAGED_BY!r}: {path}")
        return content

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

    def _sync_runtime(self, name: str, source_path: Path) -> Path:
        return sync_bundled_skill(name, source_path=source_path, target_root=self.runtime_skills_root)


def _skill_lock(path: Path) -> threading.Lock:
    with _LOCKS_GUARD:
        return _SKILL_LOCKS.setdefault(path, threading.Lock())


def _validate_skill_name(name: str) -> None:
    if (
        not isinstance(name, str)
        or not name
        or name in {".", ".."}
        or Path(name).name != name
        or any(ord(char) < 32 or ord(char) == 127 for char in name)
    ):
        raise SkillFileValidationError(f"invalid Skill name: {name!r}")


def _parse_standard_skill_frontmatter(
    content: str, source_path: Path
) -> dict[str, object]:
    lines = content.splitlines()
    if not lines or lines[0] != "---":
        raise SkillFileValidationError(
            f"Skill must start with YAML frontmatter: {source_path}"
        )
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise SkillFileValidationError(
            f"Skill frontmatter is not closed: {source_path}"
        ) from exc
    try:
        parsed = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as exc:
        raise SkillFileValidationError(
            f"invalid Skill YAML frontmatter: {source_path}: {exc}"
        ) from exc
    if not isinstance(parsed, dict) or any(not isinstance(key, str) for key in parsed):
        raise SkillFileValidationError(
            f"Skill frontmatter must be a string-keyed mapping: {source_path}"
        )
    return parsed
