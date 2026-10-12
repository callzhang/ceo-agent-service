"""Observe filesystem-backed Skill configuration work in a role invocation."""


def track_role_skill_build(tmp_path, monkeypatch):
    from app import business_skills, codex_runtime_adapter, consumer_agent
    from app.wechat import codex_safety

    root = tmp_path / "role-skills"
    entry_path = root / "ceo-document-review" / "SKILL.md"
    other_path = root / "unrelated-skill" / "SKILL.md"
    for path, name in ((entry_path, "ceo-document-review"), (other_path, "unrelated-skill")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\nname: {name}\ndescription: Synthetic test Skill.\n---\n\nSynthetic body.")
    home = tmp_path / "role-codex-home"
    original = business_skills.codex_skill_config_override
    expected = original((), target_root=root, codex_home=home)
    calls = []

    def observed(names, **kwargs):
        selection = tuple(names)
        calls.append(selection)
        return original(selection, target_root=root, codex_home=home)

    monkeypatch.setattr(codex_runtime_adapter, "codex_skill_config_override", observed)
    monkeypatch.setattr(codex_safety, "codex_skill_config_override", observed)
    entry = business_skills.BusinessSkillCatalogEntry(
        "ceo-document-review", entry_path, "Synthetic selected document review.",
    )
    monkeypatch.setattr(consumer_agent, "default_task_skill_catalog", lambda _names=(): (entry,))
    return calls, expected


def assert_single_role_skill_build(calls, expected, executor):
    assert calls == [()], "A role invocation must not build and discard a task-specific native Skill config"
    final_configs = [
        command[index + 1]
        for command in executor.commands
        for index, value in enumerate(command[:-1])
        if value == "-c" and command[index + 1].startswith("skills.config=")
    ]
    assert final_configs == [expected]
    assert "ceo-document-review" in executor.prompts[0]
    assert "agent_cli.read_skill(path=" in executor.prompts[0]
