from pathlib import Path

import pytest

from app.business_skills import (
    BUNDLED_BUSINESS_SKILL_NAMES,
    BusinessSkillInstallConflict,
    BusinessSkillValidationError,
    install_bundled_business_skills,
    load_bundled_business_skills,
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
