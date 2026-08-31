import hashlib
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.business_skills import (
    BusinessSkillInstallRollbackError,
    BusinessSkillValidationError,
    sync_bundled_skill,
)
from app.skill_files import (
    SkillFileConflict,
    SkillFileService,
    SkillFileSyncError,
    SkillFileValidationError,
)


def _write_skill(root: Path, name: str, *, content: str | None = None) -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "SKILL.md"
    path.write_text(
        content
        or f"---\nname: {name}\ndescription: Description\nmetadata:\n  managed_by: ceo-agent-service\n---\nBody\n",
        encoding="utf-8",
    )
    return path


def test_list_skills_discovers_only_real_skill_directories(tmp_path: Path):
    root = tmp_path / "skills"
    _write_skill(root, "valid")
    (root / "missing").mkdir(parents=True)
    (root / "file").write_text("x", encoding="utf-8")
    (root / "link").symlink_to(root / "valid", target_is_directory=True)

    skills = SkillFileService(root).list_skills()

    assert tuple(item.name for item in skills) == ("valid",)
    assert skills[0].path == root / "valid" / "SKILL.md"


@pytest.mark.parametrize(
    "content, message",
    [
        ("---\nname: other\ndescription: x\nmetadata:\n  managed_by: ceo-agent-service\n---\n", "name"),
        ("---\nname: valid\ndescription: x\n---\n", "managed marker"),
    ],
)
def test_skill_frontmatter_is_validated(tmp_path: Path, content: str, message: str):
    root = tmp_path / "skills"
    _write_skill(root, "valid", content=content)

    with pytest.raises(SkillFileValidationError, match=message):
        SkillFileService(root).get_skill("valid")


def test_get_skill_returns_exact_utf8_sha_and_rejects_traversal(tmp_path: Path):
    root = tmp_path / "skills"
    path = _write_skill(root, "valid")
    document = SkillFileService(root).get_skill("valid")

    assert document.content == path.read_text(encoding="utf-8")
    assert document.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(SkillFileValidationError):
        SkillFileService(root).get_skill("../valid")
    with pytest.raises(SkillFileValidationError):
        SkillFileService(root).get_skill("bad\x00name")


def test_crlf_bytes_are_hashed_and_restored_without_normalization(tmp_path: Path, monkeypatch):
    source_root = tmp_path / "skills"
    runtime_root = tmp_path / "runtime"
    path = _write_skill(source_root, "valid")
    original = path.read_bytes().replace(b"\n", b"\r\n")
    path.write_bytes(original)
    service = SkillFileService(source_root, runtime_skills_root=runtime_root)
    document = service.get_skill("valid")
    assert document.raw_bytes == original
    assert document.sha256 == hashlib.sha256(original).hexdigest()
    monkeypatch.setattr(service, "_sync_runtime", lambda *_args: (_ for _ in ()).throw(OSError("sync failed")))
    with pytest.raises(SkillFileSyncError):
        service.save_skill("valid", document.content.replace("Body", "Changed"), document.sha256)
    assert path.read_bytes() == original


def test_save_skill_uses_expected_sha_and_syncs_runtime_copy(tmp_path: Path):
    source_root = tmp_path / "skills"
    runtime_root = tmp_path / "runtime"
    path = _write_skill(source_root, "valid")
    service = SkillFileService(source_root, runtime_skills_root=runtime_root)
    expected = service.get_skill("valid").sha256
    updated = service.save_skill("valid", path.read_text(encoding="utf-8") + "Updated\n", expected)

    assert updated.content.endswith("Updated\n")
    assert (runtime_root / "valid" / "SKILL.md").read_text(encoding="utf-8") == updated.content

    with pytest.raises(SkillFileConflict):
        service.save_skill("valid", "different", expected)


def test_save_skill_sync_failure_restores_source_and_runtime(tmp_path: Path, monkeypatch):
    source_root = tmp_path / "skills"
    runtime_root = tmp_path / "runtime"
    source = _write_skill(source_root, "valid")
    service = SkillFileService(source_root, runtime_skills_root=runtime_root)
    original = source.read_bytes()
    monkeypatch.setattr(service, "_sync_runtime", lambda *_args: (_ for _ in ()).throw(OSError("sync failed")))

    with pytest.raises(SkillFileSyncError, match="sync failed"):
        service.save_skill("valid", "---\nname: valid\ndescription: x\nmetadata:\n  managed_by: ceo-agent-service\n---\nnew\n", hashlib.sha256(original).hexdigest())

    assert source.read_bytes() == original


def test_concurrent_save_with_same_sha_allows_only_one_writer(tmp_path: Path):
    source_root = tmp_path / "skills"
    runtime_root = tmp_path / "runtime"
    _write_skill(source_root, "valid")
    service = SkillFileService(source_root, runtime_skills_root=runtime_root)
    expected = service.get_skill("valid").sha256
    contents = [service.get_skill("valid").content + suffix for suffix in ("one\n", "two\n")]

    def save(content: str):
        try:
            return service.save_skill("valid", content, expected)
        except SkillFileConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, contents))
    assert sum(result != "conflict" for result in results) == 1


def test_sync_rollback_failure_preserves_recovery_data(tmp_path: Path, monkeypatch):
    source_root = tmp_path / "skills"
    runtime_root = tmp_path / "runtime"
    _write_skill(source_root, "valid")
    source = source_root / "valid" / "SKILL.md"
    sync_bundled_skill("valid", source_path=source, target_root=runtime_root)
    real_replace = os.replace

    def fail_install_and_restore(source_path, destination):
        source_path = Path(source_path)
        destination = Path(destination)
        if destination == runtime_root / "valid" and source_path.name == "staged":
            raise OSError("install failed")
        if destination == runtime_root / "valid" and source_path.parent.name == "backup":
            raise OSError("restore failed")
        return real_replace(source_path, destination)

    monkeypatch.setattr("app.business_skills.os.replace", fail_install_and_restore)

    with pytest.raises(BusinessSkillInstallRollbackError) as exc_info:
        sync_bundled_skill("valid", source_path=source, target_root=runtime_root)
    assert exc_info.value.recovery_path.is_dir()
    assert (exc_info.value.recovery_path / "backup" / "valid" / "SKILL.md").is_file()


def test_sync_staging_failure_cleans_transaction_directory(tmp_path: Path, monkeypatch):
    source_root = tmp_path / "skills"
    runtime_root = tmp_path / "runtime"
    source = _write_skill(source_root, "valid")

    def fail_write(_self, _data):
        raise OSError("staging failed")

    monkeypatch.setattr(Path, "write_bytes", fail_write)
    with pytest.raises(OSError, match="staging failed"):
        sync_bundled_skill("valid", source_path=source, target_root=runtime_root)
    assert not list(tmp_path.glob(".ceo-business-skill-*"))


def test_sync_rejects_parent_name_without_touching_runtime(tmp_path: Path):
    source_root = tmp_path / "skills"
    runtime_root = tmp_path / "runtime"
    source = _write_skill(source_root, "valid")
    with pytest.raises(BusinessSkillValidationError):
        sync_bundled_skill("..", source_path=source, target_root=runtime_root)
    assert not runtime_root.exists()
