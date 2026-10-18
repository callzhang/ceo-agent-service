import hashlib
import tomllib

import pytest

from app.agent_cron.commands import ServiceCommandSkillMaterial
from app.agent_runtime_config import load_runtime_config
from app.agent_runtime_router import CodexCommandFactory
from app.codex_runtime_adapter import CodexRuntimeAdapter
from app.business_skills import codex_skill_config_override


def test_frozen_skill_material_exposes_only_a_verified_full_file_digest():
    skill_file = "---\nname: ceo-meeting-work\ndescription: Test.\n---\nFull body."
    digest = hashlib.sha256(skill_file.encode("utf-8")).hexdigest()
    material = ServiceCommandSkillMaterial(
        name="ceo-meeting-work",
        content=f"## Managed Skill: ceo-meeting-work\nrevision_id: 3\nsha256: {digest}\n\n{skill_file}",
    )

    verified = getattr(material, "verified_body_sha256", lambda: None)()

    assert verified == digest
    damaged = ServiceCommandSkillMaterial(
        name="ceo-meeting-work",
        content=material.content.replace("Full body.", "Changed body."),
    )
    assert getattr(damaged, "verified_body_sha256", lambda: None)() is None


def test_skill_catalog_disables_only_the_exact_frozen_copy(tmp_path):
    project_root = tmp_path / "project"
    codex_home = tmp_path / "home"
    user_root = codex_home / "skills"
    frozen_file = project_root / "ceo-meeting-work" / "SKILL.md"
    changed_file = user_root / "ceo-meeting-work" / "SKILL.md"
    frozen_content = "---\nname: ceo-meeting-work\ndescription: Frozen.\n---\nBody A."
    changed_content = "---\nname: ceo-meeting-work\ndescription: Changed.\n---\nBody B."
    frozen_file.parent.mkdir(parents=True)
    changed_file.parent.mkdir(parents=True)
    frozen_file.write_text(frozen_content, encoding="utf-8")
    changed_file.write_text(changed_content, encoding="utf-8")
    digest = hashlib.sha256(frozen_content.encode("utf-8")).hexdigest()

    override = codex_skill_config_override(
        ("ceo-meeting-work",),
        inlined_hashes={"ceo-meeting-work": digest},
        target_root=project_root,
        codex_home=codex_home,
    )

    # The native config must disable only the exact project file and keep the
    # changed same-name user file available.
    config_arg = tomllib.loads(override)
    assert config_arg["skills"]["config"] == [
        {"path": str(changed_file.resolve()), "enabled": True},
        {"path": str(frozen_file.resolve()), "enabled": False},
    ]


def test_command_factory_transports_frozen_skill_digest_to_native_cli(
    tmp_path, monkeypatch
):
    root = tmp_path / "skills"
    skill_file = root / "ceo-meeting-work" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    content = "---\nname: ceo-meeting-work\ndescription: Meeting.\n---\nFrozen body."
    skill_file.write_text(content, encoding="utf-8")
    monkeypatch.setattr(
        "app.business_skills.codex_skill_roots",
        lambda **kwargs: (root, tmp_path / "empty-skills", tmp_path / "empty-plugins"),
    )
    monkeypatch.setattr(
        "app.codex_runtime_adapter.service_mcp_config_options",
        lambda **kwargs: [],
    )
    config = load_runtime_config({"CEO_AGENT_RUNTIME_ROUTES": "codex_oauth"})
    adapter = CodexRuntimeAdapter(tmp_path, config)
    factory = CodexCommandFactory.standard(
        developer_instructions="Meeting instructions.",
        inlined_skill_hashes=(
            ("ceo-meeting-work", hashlib.sha256(content.encode()).hexdigest()),
        ),
    )

    command, _ = factory.build(
        adapter=adapter,
        route=config.routes[0],
        prompt="Frozen content remains in the service prompt.",
        session_id=None,
    )

    override = next(value for value in command if value.startswith("skills.config="))
    assert tomllib.loads(override)["skills"]["config"] == [
        {"path": str(skill_file.resolve()), "enabled": False}
    ]


@pytest.mark.parametrize("session_id", [None, "same-task-session"])
def test_selected_and_inlined_skills_compile_to_one_native_override(tmp_path, monkeypatch, session_id):
    root = tmp_path / "skills"
    for name in ("ceo-work-tracking", "other"):
        path = root / name / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text(f"---\nname: {name}\ndescription: Test source.\n---\nFull body.")
    monkeypatch.setattr("app.business_skills.codex_skill_roots", lambda **kwargs: (root, tmp_path / "empty1", tmp_path / "empty2"))
    monkeypatch.setattr(
        "app.codex_runtime_adapter.service_mcp_config_options",
        lambda **kwargs: [],
    )
    config = load_runtime_config({"CEO_AGENT_RUNTIME_ROUTES": "codex_oauth"})
    adapter = CodexRuntimeAdapter(tmp_path, config)
    options = dict(developer_instructions="Exact Task instructions.", skill_names=("ceo-work-tracking",))
    baseline, _ = CodexCommandFactory.standard(**options).build(
        adapter=adapter, route=config.routes[0], prompt="Exact source.", session_id=session_id,
    )
    candidate, _ = CodexCommandFactory.standard(**options, inlined_skill_names=("ceo-work-tracking",)).build(
        adapter=adapter, route=config.routes[0], prompt="Exact source.", session_id=session_id,
    )
    overrides = [value for value in candidate if value.startswith("skills.config=")]
    assert len(overrides) == 1
    entries = tomllib.loads(overrides[0])["skills"]["config"]
    baseline_override = next(value for value in baseline if value.startswith("skills.config="))
    assert entries[:-1] == tomllib.loads(baseline_override)["skills"]["config"]
    assert entries[-1] == {"name": "ceo-work-tracking", "enabled": False}
    normalized = [baseline_override if value.startswith("skills.config=") else value for value in candidate]
    assert normalized == baseline
