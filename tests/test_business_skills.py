import os
from pathlib import Path

import pytest

from app.business_skills import (
    BUNDLED_BUSINESS_SKILL_NAMES,
    BusinessSkillInstallConflict,
    BusinessSkillInstallRollbackError,
    BusinessSkillInstallTargetError,
    BusinessSkillValidationError,
    install_bundled_business_skills,
    installed_business_skill_catalog,
    load_bundled_business_skills,
    render_business_skill_protocol,
)


EXPECTED_NAMES = (
    "ceo-message-triage",
    "ceo-calendar-invite",
    "ceo-document-review",
    "ceo-meeting-work",
    "ceo-mail-review",
    "ceo-personnel-communication",
    "ceo-work-tracking",
)


def test_bundled_business_skill_inventory_is_exact_and_valid():
    assert BUNDLED_BUSINESS_SKILL_NAMES == EXPECTED_NAMES

    skills = load_bundled_business_skills()

    assert tuple(skill.name for skill in skills) == EXPECTED_NAMES
    assert all(skill.description.strip() for skill in skills)
    assert all(skill.managed_by == "ceo-agent-service" for skill in skills)


def test_ceo_mail_review_uses_immutable_email_authorization_and_metadata_only():
    skill = next(
        item for item in load_bundled_business_skills() if item.name == "ceo-mail-review"
    )
    text = skill.content

    assert "immutable ActionPlan" in text
    assert "auto_reply" in text
    assert "unsubscribe" in text
    assert "attachment metadata only" in text
    assert "image_paths=()" in text
    assert "sent state" in text
    assert "unsubscribe state" in text
    assert "Do not open or inspect attachment content" in text
    assert "Do not open or inspect linked content" in text
    assert "Do not invent attachment facts" in text
    assert "Inspect every linked material" not in text


def test_installed_business_skill_catalog_and_protocol_are_explicit(
    tmp_path: Path,
):
    target_root = tmp_path / ".agents" / "skills"
    install_bundled_business_skills(target_root)

    catalog = installed_business_skill_catalog(target_root)
    protocol = render_business_skill_protocol(catalog)

    assert tuple(item.name for item in catalog) == EXPECTED_NAMES
    assert all(item.skill_path.is_absolute() for item in catalog)
    assert all(str(item.skill_path) in protocol for item in catalog)
    assert "PROTOCOL PRECONDITION" in protocol
    assert "at least one CEO business Skill" in protocol
    assert "Do not return an outcome before completing this read" in protocol


def test_business_skill_loader_rejects_mismatched_frontmatter_name(
    monkeypatch,
    tmp_path: Path,
):
    _write_bundle(tmp_path, EXPECTED_NAMES[0], name="different-name")
    monkeypatch.setattr(
        "app.business_skills.BUNDLED_BUSINESS_SKILL_NAMES",
        (EXPECTED_NAMES[0],),
    )
    monkeypatch.setattr(
        "app.business_skills.bundled_business_skills_root",
        lambda: tmp_path,
    )

    with pytest.raises(BusinessSkillValidationError, match="matching name"):
        load_bundled_business_skills()


@pytest.mark.parametrize(
    ("description", "managed_by", "expected_message"),
    [
        ("", "ceo-agent-service", "nonempty description"),
        ("A useful description.", "someone-else", "managed marker"),
    ],
)
def test_business_skill_loader_rejects_invalid_required_metadata(
    monkeypatch,
    tmp_path: Path,
    description: str,
    managed_by: str,
    expected_message: str,
):
    _write_bundle(
        tmp_path,
        EXPECTED_NAMES[0],
        description=description,
        managed_by=managed_by,
    )
    monkeypatch.setattr(
        "app.business_skills.BUNDLED_BUSINESS_SKILL_NAMES",
        (EXPECTED_NAMES[0],),
    )
    monkeypatch.setattr(
        "app.business_skills.bundled_business_skills_root",
        lambda: tmp_path,
    )

    with pytest.raises(BusinessSkillValidationError, match=expected_message):
        load_bundled_business_skills()


def test_skill_install_writes_all_bundled_skills(tmp_path: Path):
    target_root = tmp_path / ".agents" / "skills"

    installed = install_bundled_business_skills(target_root)

    assert tuple(item.name for item in installed) == EXPECTED_NAMES
    for item in installed:
        assert item.install_path == target_root / item.name
        content = (item.install_path / "SKILL.md").read_text(encoding="utf-8")
        assert f"name: {item.name}" in content
        assert "managed_by: ceo-agent-service" in content


def test_skill_install_rejects_codex_skills_root_before_creating_files(
    monkeypatch,
    tmp_path: Path,
):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    target_root = home / ".codex" / "skills"

    with pytest.raises(BusinessSkillInstallTargetError, match=r"\.codex/skills"):
        install_bundled_business_skills(target_root)

    assert not target_root.exists()


def test_skill_install_rejects_target_below_codex_skills_root(
    monkeypatch,
    tmp_path: Path,
):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    target_root = home / ".codex" / "skills" / "nested-root"

    with pytest.raises(BusinessSkillInstallTargetError, match=r"\.codex/skills"):
        install_bundled_business_skills(target_root)

    assert not target_root.exists()


def test_skill_install_rejects_symlink_alias_to_codex_skills_without_writes(
    monkeypatch,
    tmp_path: Path,
):
    home = tmp_path / "home"
    forbidden_root = home / ".codex" / "skills"
    forbidden_root.mkdir(parents=True)
    sentinel = forbidden_root / "user-owned.txt"
    sentinel.write_text("keep\n", encoding="utf-8")
    alias = tmp_path / "skills-alias"
    alias.symlink_to(forbidden_root, target_is_directory=True)
    monkeypatch.setenv("HOME", str(home))

    with pytest.raises(BusinessSkillInstallTargetError, match=r"\.codex/skills"):
        install_bundled_business_skills(alias)

    assert sentinel.read_text(encoding="utf-8") == "keep\n"
    assert list(forbidden_root.iterdir()) == [sentinel]


def test_skill_install_rejects_symlinked_skill_directory_without_external_writes(
    tmp_path: Path,
):
    target_root = tmp_path / ".agents" / "skills"
    target_root.mkdir(parents=True)
    external = tmp_path / "external-skill"
    external.mkdir()
    external_skill = external / "SKILL.md"
    external_content = (
        "---\nmetadata:\n  managed_by: ceo-agent-service\n---\nexternal\n"
    )
    external_skill.write_text(external_content, encoding="utf-8")
    (target_root / EXPECTED_NAMES[0]).symlink_to(external, target_is_directory=True)

    with pytest.raises(BusinessSkillInstallTargetError, match="symlink"):
        install_bundled_business_skills(target_root)

    assert external_skill.read_text(encoding="utf-8") == external_content
    assert list(target_root.iterdir()) == [target_root / EXPECTED_NAMES[0]]


def test_skill_install_upgrades_service_managed_skill_deterministically(
    tmp_path: Path,
):
    target_root = tmp_path / ".agents" / "skills"
    target = target_root / EXPECTED_NAMES[0] / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text(
        "---\nmetadata:\n  managed_by: ceo-agent-service\n---\nold\n",
        encoding="utf-8",
    )

    install_bundled_business_skills(target_root)
    first_content = target.read_bytes()
    install_bundled_business_skills(target_root)

    assert target.read_bytes() == first_content
    assert first_content == (
        Path("skills") / EXPECTED_NAMES[0] / "SKILL.md"
    ).read_bytes()


def test_skill_install_replaces_complete_managed_directory(tmp_path: Path):
    target_root = tmp_path / ".agents" / "skills"
    target_dir = target_root / EXPECTED_NAMES[0]
    target_dir.mkdir(parents=True)
    (target_dir / "SKILL.md").write_text(
        "---\nmetadata:\n  managed_by: ceo-agent-service\n---\nold\n",
        encoding="utf-8",
    )
    obsolete = target_dir / "obsolete.txt"
    obsolete.write_text("remove me\n", encoding="utf-8")

    install_bundled_business_skills(target_root)

    assert not obsolete.exists()
    assert list(target_dir.iterdir()) == [target_dir / "SKILL.md"]


def test_skill_install_rolls_back_every_directory_after_mid_swap_failure(
    monkeypatch,
    tmp_path: Path,
):
    target_root = tmp_path / ".agents" / "skills"
    original_content: dict[str, str] = {}
    for name in EXPECTED_NAMES:
        target_dir = target_root / name
        target_dir.mkdir(parents=True)
        content = (
            "---\nmetadata:\n  managed_by: ceo-agent-service\n---\n"
            f"original {name}\n"
        )
        (target_dir / "SKILL.md").write_text(content, encoding="utf-8")
        original_content[name] = content

    real_replace = os.replace
    failed = False

    def fail_third_skill_swap(source, destination):
        nonlocal failed
        destination_path = Path(destination)
        third_target = target_root / EXPECTED_NAMES[2]
        is_third_target = destination_path in {
            third_target,
            third_target / "SKILL.md",
        }
        if is_third_target and not failed:
            failed = True
            raise OSError("injected mid-swap failure")
        return real_replace(source, destination)

    monkeypatch.setattr("app.business_skills.os.replace", fail_third_skill_swap)

    with pytest.raises(OSError, match="injected mid-swap failure"):
        install_bundled_business_skills(target_root)

    for name, content in original_content.items():
        assert (target_root / name / "SKILL.md").read_text(
            encoding="utf-8"
        ) == content
    assert not list(target_root.parent.glob(".ceo-business-skills-*"))


def test_skill_install_preserves_recovery_directory_when_restore_fails(
    monkeypatch,
    tmp_path: Path,
):
    target_root = tmp_path / ".agents" / "skills"
    original_content: dict[str, str] = {}
    for name in EXPECTED_NAMES:
        target_dir = target_root / name
        target_dir.mkdir(parents=True)
        content = (
            "---\nmetadata:\n  managed_by: ceo-agent-service\n---\n"
            f"original {name}\n"
        )
        (target_dir / "SKILL.md").write_text(content, encoding="utf-8")
        original_content[name] = content

    real_replace = os.replace
    live_swap_failed = False
    restore_failed = False

    def fail_live_swap_and_restore(source, destination):
        nonlocal live_swap_failed, restore_failed
        source_path = Path(source)
        destination_path = Path(destination)
        third_target = target_root / EXPECTED_NAMES[2]
        if (
            destination_path == third_target
            and source_path.parent.name == "staged"
            and not live_swap_failed
        ):
            live_swap_failed = True
            raise OSError("injected live swap failure")
        if (
            destination_path == third_target
            and source_path.parent.name == "backups"
            and not restore_failed
        ):
            restore_failed = True
            raise OSError("injected restore failure")
        return real_replace(source, destination)

    monkeypatch.setattr(
        "app.business_skills.os.replace",
        fail_live_swap_and_restore,
    )

    with pytest.raises(
        BusinessSkillInstallRollbackError,
        match="rollback",
    ) as exc_info:
        install_bundled_business_skills(target_root)

    recovery_path = exc_info.value.recovery_path
    assert str(recovery_path) in str(exc_info.value)
    assert recovery_path.is_dir()
    assert (recovery_path / "backups" / EXPECTED_NAMES[2] / "SKILL.md").read_text(
        encoding="utf-8"
    ) == original_content[EXPECTED_NAMES[2]]
    assert (recovery_path / "staged" / EXPECTED_NAMES[2] / "SKILL.md").is_file()
    for name in EXPECTED_NAMES[:2] + EXPECTED_NAMES[3:]:
        assert (target_root / name / "SKILL.md").read_text(
            encoding="utf-8"
        ) == original_content[name]


def test_skill_install_refuses_user_owned_conflict_without_any_writes(
    tmp_path: Path,
):
    target_root = tmp_path / ".agents" / "skills"
    managed_target = target_root / EXPECTED_NAMES[0] / "SKILL.md"
    managed_target.parent.mkdir(parents=True)
    managed_content = (
        "---\nmetadata:\n  managed_by: ceo-agent-service\n---\nkeep until preflight passes\n"
    )
    managed_target.write_text(managed_content, encoding="utf-8")
    conflict = target_root / EXPECTED_NAMES[-1] / "SKILL.md"
    conflict.parent.mkdir(parents=True)
    conflict.write_text("user-owned content\n", encoding="utf-8")

    with pytest.raises(BusinessSkillInstallConflict, match=EXPECTED_NAMES[-1]):
        install_bundled_business_skills(target_root)

    assert managed_target.read_text(encoding="utf-8") == managed_content
    assert conflict.read_text(encoding="utf-8") == "user-owned content\n"


def _write_bundle(
    root: Path,
    directory_name: str,
    *,
    name: str | None = None,
    description: str = "A useful description.",
    managed_by: str = "ceo-agent-service",
) -> None:
    skill_dir = root / directory_name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            (
                "---",
                f"name: {name or directory_name}",
                f"description: {description}",
                "metadata:",
                f"  managed_by: {managed_by}",
                "  version: 1",
                "---",
                "",
            )
        ),
        encoding="utf-8",
    )
