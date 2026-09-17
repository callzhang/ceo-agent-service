import os
from pathlib import Path

import pytest

from app.business_skills import (
    BUNDLED_BUSINESS_SKILL_NAMES,
    BusinessSkillInstallConflict,
    BusinessSkillInstallRollbackError,
    BusinessSkillInstallTargetError,
    BusinessSkillValidationError,
    codex_skill_config_override,
    default_skill_catalog,
    expand_skill_dependencies,
    install_bundled_business_skills,
    installed_runtime_skill_paths,
    installed_runtime_skills,
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
    "ceo-sales-weekly-report",
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
    automatic_action = text.split("## Automatic Email Action", 1)[1].split(
        "## Authorization And Outcome", 1
    )[0]
    assert "Inspect every linked material" not in automatic_action


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


def _skill_file(root: Path, relative: str, name: str, description: str) -> Path:
    path = root / relative / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\nBody\n",
        encoding="utf-8",
    )
    return path


def test_runtime_scan_finds_nested_skills_and_skips_unparsable(tmp_path: Path):
    root = tmp_path / "skills"
    _skill_file(root, "top-level", "top-level", "A top level skill.")
    _skill_file(root, "bundle/nested", "nested", "A nested skill.")
    broken = root / "broken" / "SKILL.md"
    broken.parent.mkdir(parents=True)
    broken.write_text("no frontmatter here\n", encoding="utf-8")

    paths = installed_runtime_skill_paths(root)
    entries = installed_runtime_skills(root)

    # The exclusion list must see every file, including the unparsable one.
    assert len(paths) == 3
    # The catalog only lists what it can describe.
    assert {item.name for item in entries} == {"top-level", "nested"}
    assert {item.description for item in entries} == {
        "A top level skill.",
        "A nested skill.",
    }


def test_runtime_scan_filters_to_the_requested_names(tmp_path: Path):
    root = tmp_path / "skills"
    _skill_file(root, "wanted", "wanted", "Keep me.")
    _skill_file(root, "other", "other", "Not this run.")

    entries = installed_runtime_skills(root, names={"wanted"})

    assert [item.name for item in entries] == ["wanted"]


def test_codex_override_enables_the_allow_set_and_disables_everything_else(
    tmp_path: Path,
):
    root = tmp_path / "skills"
    wanted = _skill_file(root, "wanted", "wanted", "Keep me.")
    unwanted = _skill_file(root, "unwanted", "unwanted", "Disable me.")
    nested = _skill_file(root, "bundle/deep", "deep", "Disable me too.")

    override = codex_skill_config_override(
        {"wanted"}, target_root=root, codex_home=tmp_path / "no-codex"
    )

    assert override.startswith("skills.config=[")
    assert f'{{path="{wanted}",enabled=true}}' in override
    assert f'{{path="{unwanted}",enabled=false}}' in override
    assert f'{{path="{nested}",enabled=false}}' in override


def test_codex_override_is_empty_when_there_are_no_skills(tmp_path: Path):
    assert (
        codex_skill_config_override(
            {"anything"}, target_root=tmp_path / "none", codex_home=tmp_path / "none"
        )
        == ""
    )


def test_codex_override_forces_on_a_skill_the_user_config_disabled(tmp_path: Path):
    """Production: ~/.codex/config.toml disabled dingtalk-oa-approval, and the
    override merges with it, so the OA task's only Skill was never visible."""
    root = tmp_path / "skills"
    oa = _skill_file(root, "dingtalk-oa-approval", "dingtalk-oa-approval", "OA.")

    override = codex_skill_config_override(
        {"dingtalk-oa-approval"}, target_root=root, codex_home=tmp_path / "codex"
    )

    assert f'{{path="{oa}",enabled=true}}' in override


def test_codex_override_excludes_skills_in_every_codex_root(tmp_path: Path):
    """Production: sora and imagegen live in ~/.codex/skills and were never trimmed."""
    root = tmp_path / "agents"
    codex_home = tmp_path / "codex"
    _skill_file(root, "task", "task", "The task.")
    native = _skill_file(codex_home / "skills", "sora", "sora", "Video.")
    plugin = _skill_file(
        codex_home / "plugins" / "cache" / "vendor" / "1.0" / "skills",
        "figma-use",
        "figma-use",
        "Design.",
    )

    override = codex_skill_config_override(
        {"task"}, target_root=root, codex_home=codex_home
    )

    assert f'{{path="{native}",enabled=false}}' in override
    assert f'{{path="{plugin}",enabled=false}}' in override


def _skill_with_body(root: Path, name: str, body: str) -> None:
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {name}.\n---\n\n{body}\n", encoding="utf-8"
    )


def test_dependencies_are_followed_to_the_end_not_cut_at_a_depth(tmp_path: Path):
    root = tmp_path / "skills"
    _skill_with_body(root, "task", "Delegate reading to reader.")
    _skill_with_body(root, "reader", "Scanned pages go through ocr.")
    _skill_with_body(root, "ocr", "Convert output with pdf.")
    _skill_with_body(root, "pdf", "Leaf.")
    _skill_with_body(root, "unrelated", "Nobody points here.")

    closed = set(expand_skill_dependencies(["task"], target_root=root))

    assert closed == {"task", "reader", "ocr", "pdf"}


def test_dependency_cycles_terminate(tmp_path: Path):
    root = tmp_path / "skills"
    _skill_with_body(root, "alpha", "See beta.")
    _skill_with_body(root, "beta", "See alpha.")

    assert set(expand_skill_dependencies(["alpha"], target_root=root)) == {"alpha", "beta"}


def test_dws_command_resolves_to_its_dingtalk_skill_only_when_installed(tmp_path: Path):
    root = tmp_path / "skills"
    _skill_with_body(
        root, "task", "Run `dws doc read` then `dws nonexistent list`."
    )
    _skill_with_body(root, "dingtalk-doc", "Leaf.")

    closed = set(expand_skill_dependencies(["task"], target_root=root))

    assert "dingtalk-doc" in closed
    assert "dingtalk-nonexistent" not in closed


def test_a_skill_name_inside_a_longer_name_is_not_a_reference(tmp_path: Path):
    root = tmp_path / "skills"
    _skill_with_body(root, "task", "Use dingtalk-docs-export for this.")
    _skill_with_body(root, "dingtalk-doc", "Leaf.")

    assert set(expand_skill_dependencies(["task"], target_root=root)) == {"task"}


def test_exclusion_override_keeps_the_whole_dependency_closure(tmp_path: Path):
    root = tmp_path / "skills"
    _skill_with_body(root, "task", "Read attachments with ocr.")
    _skill_with_body(root, "ocr", "Leaf.")
    _skill_with_body(root, "sora", "Leaf.")

    override = codex_skill_config_override(
        ["task"], target_root=root, codex_home=tmp_path / "codex"
    )

    assert '/sora/SKILL.md",enabled=false}' in override
    assert '/ocr/SKILL.md",enabled=true}' in override
    assert '/task/SKILL.md",enabled=true}' in override


def _plugin_skill(cache: Path, version: str, skill: str) -> Path:
    return _skill_file(
        cache / "memory-connector-local" / "memory-connector" / version / "skills",
        skill,
        skill,
        f"{skill} durable memory.",
    )


def test_memory_skills_stay_on_even_when_the_task_does_not_declare_them(tmp_path: Path):
    """Consumer and Audit are told to recall durable context; trimming a task to
    its own Skills must not take memory away."""
    root = tmp_path / "agents"
    codex_home = tmp_path / "codex"
    _skill_file(root, "dingtalk-oa-approval", "dingtalk-oa-approval", "OA.")
    recall = _plugin_skill(codex_home / "plugins" / "cache", "0.4.0", "recall")
    other = _skill_file(codex_home / "skills", "sora", "sora", "Video.")

    override = codex_skill_config_override(
        {"dingtalk-oa-approval"}, target_root=root, codex_home=codex_home
    )

    assert f'{{path="{recall}",enabled=true}}' in override
    assert f'{{path="{other}",enabled=false}}' in override


def test_default_catalog_offers_only_the_newest_memory_plugin_version(tmp_path: Path):
    cache = tmp_path / "claude-cache"
    _plugin_skill(cache, "0.2.0", "recall")
    newest = _plugin_skill(cache, "0.10.0", "recall")
    _plugin_skill(cache, "0.10.0", "remember")

    catalog = default_skill_catalog(cache)

    assert [entry.name for entry in catalog] == [
        "memory-connector:recall",
        "memory-connector:remember",
    ]
    assert catalog[0].skill_path == newest.resolve()
