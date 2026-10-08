import pytest

from app.prompt_composition import append_runtime_context, assemble_consumer_task, join_developer_sections


def test_named_prompt_sections_are_the_single_render_and_snapshot_source():
    from app.prompt_composition import (
        PromptSection,
        prompt_section_facts,
        render_prompt_sections,
    )

    sections = (
        PromptSection("role", "service_contract", "developer", "ROLE"),
        PromptSection("empty", "service_contract", "developer", ""),
        PromptSection("task", "current_task", "task", "TASK"),
    )

    assert render_prompt_sections(sections, placement="developer") == "ROLE"
    assert render_prompt_sections(sections, placement="task") == "TASK"
    assert prompt_section_facts(sections) == [
        {"name": "role", "source": "service_contract", "placement": "developer", "text": "ROLE"},
        {"name": "empty", "source": "service_contract", "placement": "developer", "text": ""},
        {"name": "task", "source": "current_task", "placement": "task", "text": "TASK"},
    ]


def test_developer_sections_are_named_and_keep_canonical_contracts():
    from app.agent_context import _AUDIT_AGENT_RULES, _CONSUMER_AGENT_RULES
    from app.consumer_agent import (
        audit_developer_instructions,
        audit_developer_sections,
        consumer_developer_instructions,
        consumer_developer_sections,
    )
    from app.prompt_composition import render_prompt_sections

    consumer_sections = consumer_developer_sections(runtime_context="", work_profile="PROFILE")
    audit_sections = audit_developer_sections("audit rules", runtime_context="", work_profile="PROFILE")

    assert consumer_developer_instructions(runtime_context="", work_profile="PROFILE") == render_prompt_sections(
        consumer_sections, placement="developer"
    )
    assert audit_developer_instructions("audit rules", runtime_context="", work_profile="PROFILE") == render_prompt_sections(
        audit_sections, placement="developer", omit_empty=False
    )
    assert {section.name for section in consumer_sections} >= {
        "角色合同", "共同工作原则", "系统动作目录",
        "输出契约", "角色边界", "决策证据",
        "应用结果合同", "工作人格",
    }
    assert {section.name for section in audit_sections} >= {
        "角色合同", "审核规则", "系统动作目录",
        "输出契约", "角色边界", "应用结果合同",
        "工作人格",
    }
    assert _CONSUMER_AGENT_RULES in render_prompt_sections(consumer_sections, placement="developer")
    assert _AUDIT_AGENT_RULES in render_prompt_sections(audit_sections, placement="developer", omit_empty=False)


def test_selected_task_skills_stay_in_task_and_do_not_inject_other_flows(
    tmp_path, monkeypatch
):
    import json
    import re

    from app import agent_cli
    from app.business_skills import BusinessSkillCatalogEntry, render_task_skill_discovery

    root = tmp_path / "skills"
    entries = []
    for name, use in (
        ("ceo-calendar-invite", "Review calendar invitations."),
        ("ceo-mail-review", "Review mail."),
        ("ceo-document-review", "Review documents."),
        ("ceo-work-tracking", "Track work."),
    ):
        path = root / name / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text(f"# {name}\n", encoding="utf-8")
        entries.append(BusinessSkillCatalogEntry(name, path, use))
    catalog = tuple(entries)
    monkeypatch.setattr("app.agent_skill_usage.AGENT_SKILL_ROOTS", (root,))
    for selected, expected, excluded in (
        (("ceo-calendar-invite",), "Review calendar invitations.", "Review mail."),
        (("ceo-mail-review",), "Review mail.", "Review documents."),
        (("ceo-document-review",), "Review documents.", "Track work."),
        (("ceo-work-tracking",), "Track work.", "Review calendar invitations."),
    ):
        rendered = render_task_skill_discovery(selected, catalog=catalog)
        assert expected in rendered
        assert excluded not in rendered
        assert "agent_cli.read_task_skill(name)" not in rendered
        match = re.search(r"agent_cli\.read_skill\(path=(\"[^\n]+\")\)", rendered)
        assert match is not None
        rendered_path = json.loads(match.group(1))
        result = agent_cli.read_skill(rendered_path)
        assert result["name"] == selected[0]
        assert result["path"] == str((root / selected[0] / "SKILL.md").resolve())


def test_frozen_selected_skill_uses_name_lookup_without_installed_path(tmp_path):
    from app.business_skills import render_task_skill_discovery

    rendered = render_task_skill_discovery(
        ("removed-installed-skill",),
        catalog=(),
        frozen_names=("removed-installed-skill",),
    )

    assert "agent_cli.read_task_skill(name)" in rendered
    assert "removed-installed-skill" in rendered
    assert "agent_cli.read_skill(path=" not in rendered


def test_unknown_task_gets_minimal_on_demand_skill_discovery(tmp_path):
    from app.business_skills import BusinessSkillCatalogEntry, render_task_skill_discovery

    catalog = (
        BusinessSkillCatalogEntry("ceo-calendar-invite", tmp_path / "calendar" / "SKILL.md", "Review invitations."),
        BusinessSkillCatalogEntry("ceo-mail-review", tmp_path / "mail" / "SKILL.md", "Review mail."),
    )
    rendered = render_task_skill_discovery((), catalog=catalog)
    assert "agent_cli.read_skill(path=" in rendered
    assert "Review invitations." in rendered and "Review mail." in rendered
    assert str((tmp_path / "calendar" / "SKILL.md").resolve()) in rendered
    assert str((tmp_path / "mail" / "SKILL.md").resolve()) in rendered
    assert "Required Skill protocol" not in rendered


def test_default_unknown_task_catalog_is_limited_to_service_business_skills():
    from app.consumer_agent import default_task_skill_catalog
    from app.business_skills import BUNDLED_BUSINESS_SKILL_NAMES, render_task_skill_discovery

    catalog = default_task_skill_catalog()
    names = {entry.name for entry in catalog}
    assert names >= set(BUNDLED_BUSINESS_SKILL_NAMES)
    assert "brainstorming" not in names
    assert "derek-movie-recommendation" not in names
    assert len(render_task_skill_discovery((), catalog=catalog)) < 12_000


def test_consumer_task_assembly_places_current_skill_entry_on_cold_and_resume_turns(tmp_path, monkeypatch):
    from app.business_skills import BusinessSkillCatalogEntry
    from app.prompt_composition import compose_consumer_task_assembly, load_prompt_configuration

    user = tmp_path / "user.md"
    user.write_text("TASK START\n{{task_context}}\nTASK END", encoding="utf-8")
    monkeypatch.setenv("CEO_USER_PROMPT_TEMPLATE_PATH", str(user))
    configuration = load_prompt_configuration(create_missing=False)
    catalog = (
        BusinessSkillCatalogEntry("ceo-calendar-invite", tmp_path / "calendar" / "SKILL.md", "Review invitations."),
        BusinessSkillCatalogEntry("ceo-mail-review", tmp_path / "mail" / "SKILL.md", "Review mail."),
    )

    cold = compose_consumer_task_assembly(
        configuration, task_context="CURRENT FACTS", scheduled_prompt="CALENDAR TASK",
        skill_names=("ceo-calendar-invite",),
        frozen_skill_names=("ceo-calendar-invite",), skill_catalog=catalog,
    )
    resumed = compose_consumer_task_assembly(
        configuration, task_context="CURRENT FACTS", scheduled_prompt="CALENDAR TASK",
        skill_names=("ceo-calendar-invite",),
        frozen_skill_names=("ceo-calendar-invite",), skill_catalog=catalog,
        continuation="RESULT CORRECTION",
    )

    for assembly in (cold, resumed):
        assert assembly.text.startswith("TASK START\n") and assembly.text.endswith("\nTASK END")
        assert "CURRENT FACTS" in assembly.text
        assert "ceo-calendar-invite" in assembly.text
        assert "agent_cli.read_task_skill(name)" in assembly.text
        assert "ceo-mail-review" not in assembly.text
        assert any(section.name == "任务 Skill 入口" for section in assembly.sections)
    assert "RESULT CORRECTION" in resumed.text


def test_legacy_selected_skill_uses_installed_lookup_and_preserves_saved_protocol(
    tmp_path, monkeypatch
):
    from app.business_skills import BusinessSkillCatalogEntry
    from app.prompt_composition import compose_consumer_task_assembly, load_prompt_configuration

    user = tmp_path / "user.md"
    user.write_text("{{task_context}}", encoding="utf-8")
    monkeypatch.setenv("CEO_USER_PROMPT_TEMPLATE_PATH", str(user))
    assembly = compose_consumer_task_assembly(
        load_prompt_configuration(create_missing=False),
        task_context="CURRENT FACTS",
        skill_names=("ceo-document-review",),
        skill_protocol="LEGACY INLINE TASK INSTRUCTIONS",
        skill_catalog=(
            BusinessSkillCatalogEntry(
                "ceo-document-review",
                tmp_path / "document" / "SKILL.md",
                "Review documents.",
            ),
        ),
    )

    assert "agent_cli.read_skill(path=" in assembly.text
    assert "agent_cli.read_task_skill(name)" not in assembly.text
    assert "LEGACY INLINE TASK INSTRUCTIONS" in assembly.text


def test_frozen_lookup_is_derived_from_structured_task_materials_only():
    import json

    from app.business_skills import frozen_task_skill_names

    selected = ("ceo-calendar-invite", "ceo-document-review")
    task_input = json.dumps({
        "raw_payload": {
            "scheduled_consumer": {
                "skill_materials": [
                    {"name": "ceo-calendar-invite", "content": "FROZEN BODY"}
                ]
            }
        }
    })

    assert frozen_task_skill_names(task_input, selected) == ("ceo-calendar-invite",)
    assert frozen_task_skill_names("{}", selected) == ()


def test_declared_but_damaged_frozen_material_never_falls_back_to_installed():
    import json
    from pathlib import Path

    from app.business_skills import (
        BusinessSkillCatalogEntry,
        frozen_task_skill_names,
        render_task_skill_discovery,
    )

    selected = ("ceo-calendar-invite",)
    task_input = json.dumps({
        "schema": "scheduled_agent_execution.v1",
        "skill_names": list(selected),
        "skill_materials": [
            {"name": "ceo-calendar-invite", "content": ""},
        ],
    })

    frozen = frozen_task_skill_names(task_input, selected)
    rendered = render_task_skill_discovery(
        selected,
        catalog=(
            BusinessSkillCatalogEntry(
                "ceo-calendar-invite",
                Path("/currently-installed/SKILL.md"),
                "Calendar work.",
            ),
        ),
        frozen_names=frozen,
    )

    assert frozen == selected
    assert "agent_cli.read_task_skill(name)" in rendered
    assert "agent_cli.read_skill(path=" not in rendered


def test_explicit_custom_task_protocol_is_preserved_without_selected_names(tmp_path, monkeypatch):
    from app.prompt_composition import compose_consumer_task_assembly, load_prompt_configuration

    user = tmp_path / "user.md"
    user.write_text("{{task_context}}", encoding="utf-8")
    monkeypatch.setenv("CEO_USER_PROMPT_TEMPLATE_PATH", str(user))
    assembly = compose_consumer_task_assembly(
        load_prompt_configuration(create_missing=False),
        task_context="CURRENT FACTS",
        scheduled_prompt="CUSTOM TASK",
        skill_protocol="EXPLICIT CUSTOM TASK PROTOCOL",
    )

    assert "EXPLICIT CUSTOM TASK PROTOCOL" in assembly.text
    skill_section = next(section for section in assembly.sections if section.name == "任务 Skill 入口")
    assert skill_section.source == "已保存自定义 Task Skill 约定"


def test_explicit_custom_protocol_is_preserved_with_fully_frozen_selection(
    tmp_path, monkeypatch
):
    from app.prompt_composition import compose_consumer_task_assembly, load_prompt_configuration

    user = tmp_path / "user.md"
    user.write_text("{{task_context}}", encoding="utf-8")
    monkeypatch.setenv("CEO_USER_PROMPT_TEMPLATE_PATH", str(user))
    assembly = compose_consumer_task_assembly(
        load_prompt_configuration(create_missing=False),
        task_context="CURRENT FACTS",
        skill_names=("frozen-skill",),
        frozen_skill_names=("frozen-skill",),
        skill_protocol="EXPLICIT CUSTOM TASK PROTOCOL",
        skill_protocol_is_custom=True,
        skill_catalog=(),
    )
    generated = compose_consumer_task_assembly(
        load_prompt_configuration(create_missing=False),
        task_context="CURRENT FACTS",
        skill_names=("frozen-skill",),
        frozen_skill_names=("frozen-skill",),
        skill_protocol="GENERATED DISCOVERY MUST NOT REPEAT",
        skill_protocol_is_custom=False,
        skill_catalog=(),
    )

    assert "EXPLICIT CUSTOM TASK PROTOCOL" in assembly.text
    assert "GENERATED DISCOVERY MUST NOT REPEAT" not in generated.text
    skill_section = next(
        section for section in assembly.sections if section.name == "任务 Skill 入口"
    )
    assert skill_section.source == "任务冻结 Skill 选择 + 已保存任务约定"


def test_example_consumer_task_uses_runtime_assembler_and_validates_one_slot():
    from app.agent_context import AgentTaskContext
    from app.consumer_agent import default_task_skill_catalog
    from app.developer_prompt import DeveloperPromptTemplateError
    from app.prompt_composition import (
        RawPromptConfiguration,
        compose_consumer_task_assembly,
        example_consumer_task,
    )

    configuration = RawPromptConfiguration("", "Before\n{{task_context}}\nAfter", "")
    context = AgentTaskContext(
        task_id=0,
        channel="example",
        conversation_id="example-conversation",
        conversation_title="Synthetic example",
        single_chat=True,
        trigger_message_id="fixture-message",
        trigger_sender="Example sender",
        trigger_text="Review the supplied document.",
        trigger_create_time="2026-01-01T12:00:00+00:00",
        messages=(),
        materials=(),
        prior_receipts=(),
    )
    selected = ("ceo-document-review",)
    expected = compose_consumer_task_assembly(
        configuration,
        task_context=context.render(current_time="2026-01-01T12:00:00+00:00"),
        skill_names=selected,
        skill_catalog=default_task_skill_catalog(selected),
    ).text

    rendered = example_consumer_task(configuration)
    assert rendered == expected
    assert "agent_cli.read_skill(path=" in rendered
    assert "ceo-document-review" in rendered
    assert "## Skill:" not in rendered
    with pytest.raises(DeveloperPromptTemplateError, match="exactly one"):
        compose_consumer_task_assembly(
            RawPromptConfiguration(
                "", "{{task_context}}\n{{task_context}}", ""
            ),
            task_context="facts",
        )


def test_common_developer_does_not_inject_domain_workflows_into_unrelated_tasks():
    from app.consumer_agent import consumer_developer_instructions

    rendered = consumer_developer_instructions(runtime_context="", work_profile="")
    for domain_instruction in (
        "dingtang-okr-review",
        "Xiaoqing",
        "originatorUserid",
        "For DingTalk OA",
        "For OKR review",
    ):
        assert domain_instruction not in rendered


def test_consumer_task_keeps_existing_complete_order():
    result = assemble_consumer_task(task_context='sources\nfeedback', scheduled_prompt='scheduled', continuation='\ncontinuation')
    assert result == ('## Runtime Invariants\nPreserve typed proposal contracts and session boundaries. The proposal must match the supplied JSON Schema exactly.\n\n'
                      '## Scheduled Consumer Prompt\nscheduled\n\nsources\nfeedback\ncontinuation')


def test_role_parts_preserve_empty_section_semantics_and_runtime_position():
    assert join_developer_sections('role', '', 'profile', omit_empty=True) == 'role\n\nprofile'
    assert join_developer_sections('role', '', 'profile', omit_empty=False) == 'role\n\n\n\nprofile'
    assert append_runtime_context('role\n\nprofile', 'environment') == 'role\n\nprofile\n\nenvironment'


def test_saved_templates_enter_roles_and_complete_consumer_task(tmp_path, monkeypatch):
    from app.prompt_composition import load_prompt_configuration, compose_consumer_task
    from app.consumer_agent import consumer_developer_instructions, audit_developer_instructions
    developer = tmp_path / 'developer.md'
    user = tmp_path / 'user.md'
    profile = tmp_path / 'profile.md'
    developer.write_text('Shared configured principles')
    user.write_text('TASK START\n{{task_context}}\nTASK END')
    profile.write_text('Profile fixture')
    monkeypatch.setenv('CEO_DEVELOPER_PROMPT_TEMPLATE_PATH', str(developer))
    monkeypatch.setenv('CEO_USER_PROMPT_TEMPLATE_PATH', str(user))
    monkeypatch.setenv('CEO_WORK_PROFILE_PATH', str(profile))
    configuration = load_prompt_configuration()
    developer.write_text('Changed after invocation started')
    user.write_text('changed {{task_context}}')
    consumer = consumer_developer_instructions(prompt_configuration=configuration, runtime_context='')
    audit = audit_developer_instructions('audit only', prompt_configuration=configuration, runtime_context='')
    assert 'Shared configured principles' in consumer and 'Shared configured principles' in audit
    assert 'Changed after invocation started' not in consumer
    assert 'Profile fixture' in consumer and 'Profile fixture' in audit
    rendered = compose_consumer_task(configuration, task_context='source facts\nfeedback\nreceipts', scheduled_prompt='scheduled', continuation='\ncontinuation')
    assert rendered.startswith('TASK START\n') and rendered.endswith('\nTASK END')
    for value in ('source facts', 'feedback', 'receipts', 'scheduled', 'continuation'):
        assert value in rendered
    assert consumer.count('Shared configured principles') == 1


def test_frozen_common_principles_occupy_one_core_position_in_both_roles(tmp_path, monkeypatch):
    from app.prompt_composition import load_prompt_configuration
    from app.consumer_agent import consumer_developer_instructions, audit_developer_instructions

    developer = tmp_path / 'developer.md'
    user = tmp_path / 'user.md'
    developer.write_text('Configured principle sentinel', encoding='utf-8')
    user.write_text('{{task_context}}', encoding='utf-8')
    monkeypatch.setenv('CEO_DEVELOPER_PROMPT_TEMPLATE_PATH', str(developer))
    monkeypatch.setenv('CEO_USER_PROMPT_TEMPLATE_PATH', str(user))
    configuration = load_prompt_configuration()
    developer.write_text('Changed after the frozen snapshot', encoding='utf-8')

    for instructions in (
        consumer_developer_instructions(prompt_configuration=configuration, runtime_context='', work_profile=''),
        audit_developer_instructions('audit only', prompt_configuration=configuration, runtime_context='', work_profile=''),
    ):
        assert instructions.count('Configured principle sentinel') == 1
        assert instructions.index('## Dynamic Skill') < instructions.index('Configured principle sentinel')
        assert instructions.index('Configured principle sentinel') < instructions.index('## System Action Contracts')
        assert '## Shared Developer Principles' not in instructions
        assert '## 原请求与取证' not in instructions
        assert 'Changed after the frozen snapshot' not in instructions


def test_default_common_principles_are_inserted_verbatim_once_in_both_roles(tmp_path, monkeypatch):
    from pathlib import Path
    from app.prompt_composition import load_prompt_configuration
    from app.consumer_agent import consumer_developer_instructions, audit_developer_instructions

    default = Path(__file__).resolve().parents[1] / 'app/defaults/developer_prompt.md'
    user = tmp_path / 'user.md'
    user.write_text('{{task_context}}', encoding='utf-8')
    monkeypatch.setenv('CEO_DEVELOPER_PROMPT_TEMPLATE_PATH', str(default))
    monkeypatch.setenv('CEO_USER_PROMPT_TEMPLATE_PATH', str(user))
    configuration = load_prompt_configuration(create_missing=False)
    principles = default.read_text(encoding='utf-8')
    assert configuration.developer_instructions == principles

    for instructions in (
        consumer_developer_instructions(prompt_configuration=configuration, runtime_context='', work_profile=''),
        audit_developer_instructions('audit only', prompt_configuration=configuration, runtime_context='', work_profile=''),
    ):
        assert instructions.count(principles) == 1
        assert f'\n\n{principles}\n\n## System Action Contracts' in instructions


def test_consumer_decision_evidence_precedes_application_result_contract(tmp_path, monkeypatch):
    from app.prompt_composition import load_prompt_configuration
    from app.consumer_agent import consumer_developer_instructions

    developer = tmp_path / 'developer.md'
    user = tmp_path / 'user.md'
    developer.write_text('Common principle sentinel', encoding='utf-8')
    user.write_text('{{task_context}}', encoding='utf-8')
    monkeypatch.setenv('CEO_DEVELOPER_PROMPT_TEMPLATE_PATH', str(developer))
    monkeypatch.setenv('CEO_USER_PROMPT_TEMPLATE_PATH', str(user))
    configuration = load_prompt_configuration()

    instructions = consumer_developer_instructions(
        prompt_configuration=configuration, runtime_context='', work_profile='',
    )
    assert instructions.index('## Decision Evidence') < instructions.index('## Application Result Contract')


def test_consumer_template_requires_one_complete_context_slot():
    import pytest
    from app.developer_prompt import validate_consumer_task_template, DeveloperPromptTemplateError
    validate_consumer_task_template('task {{task_context}}')
    for invalid in ('no facts', '{{task_context}} {{task_context}}', '{{current_message}}', '{{unknown}}'):
        with pytest.raises(DeveloperPromptTemplateError):
            validate_consumer_task_template(invalid)


def test_variables_block_cannot_hide_the_complete_task_slot():
    import pytest
    from app.developer_prompt import validate_consumer_task_template, DeveloperPromptTemplateError
    with pytest.raises(DeveloperPromptTemplateError):
        validate_consumer_task_template('<vars>\nx = {{task_context}}\n</vars>\nNo facts here')


def test_shared_user_writer_enforces_consumer_slot(tmp_path):
    import pytest
    from app.developer_prompt import write_user_prompt_template, DeveloperPromptTemplateError
    path = tmp_path / 'user.md'
    with pytest.raises(DeveloperPromptTemplateError):
        write_user_prompt_template('no source context', path)
    assert not path.exists()


def test_audit_does_not_depend_on_unused_user_template(tmp_path, monkeypatch):
    from app.prompt_composition import load_prompt_configuration
    user = tmp_path / 'user.md'
    user.write_text('bad unused consumer template')
    monkeypatch.setenv('CEO_USER_PROMPT_TEMPLATE_PATH', str(user))
    assert load_prompt_configuration(role='audit', create_missing=False).user_template == ''


def test_raw_saved_prompt_snapshot_keeps_invalid_user_text_without_weakening_runtime(tmp_path, monkeypatch):
    import pytest
    from app.developer_prompt import DeveloperPromptTemplateError
    from app.prompt_composition import load_prompt_configuration, read_prompt_configuration_raw

    developer = tmp_path / 'developer.md'
    user = tmp_path / 'user.md'
    profile = tmp_path / 'profile.md'
    developer.write_text('Current shared principles')
    legacy_user = '{{current_message}}\n{{context_messages}}\n'
    user.write_text(legacy_user)
    profile.write_text('Current work profile')
    monkeypatch.setenv('CEO_DEVELOPER_PROMPT_TEMPLATE_PATH', str(developer))
    monkeypatch.setenv('CEO_USER_PROMPT_TEMPLATE_PATH', str(user))
    monkeypatch.setenv('CEO_WORK_PROFILE_PATH', str(profile))

    raw = read_prompt_configuration_raw(create_missing=False)
    assert raw.developer_template == 'Current shared principles'
    assert raw.user_template == legacy_user
    assert raw.work_profile_text == 'Current work profile'
    with pytest.raises(DeveloperPromptTemplateError, match='exactly one'):
        load_prompt_configuration(create_missing=False)
    assert user.read_text() == legacy_user


def test_invalid_shared_developer_save_preserves_working_template(tmp_path):
    import pytest
    from app.developer_prompt import write_developer_prompt_template, DeveloperPromptTemplateError
    path = tmp_path / 'developer.md'
    path.write_text('Working principles')
    with pytest.raises(DeveloperPromptTemplateError, match='unknown template variable'):
        write_developer_prompt_template('<var: misspelled_principle>', path)
    assert path.read_text() == 'Working principles'


def test_task_source_template_syntax_is_inserted_literally_once():
    from app.developer_prompt import render_consumer_task_template
    source = 'Source says {{task_context}} and <code: app.user_prompt_blocks:current_message_block()>'
    assert render_consumer_task_template('Before\n{{task_context}}\nAfter', source) == 'Before\n' + source + '\nAfter'


def test_same_developer_template_changed_variable_updates_rendered_fingerprint_and_contract(tmp_path, monkeypatch):
    from hashlib import sha256
    from app.prompt_composition import load_prompt_configuration
    from app.consumer_agent import consumer_developer_instructions, consumer_wire_contract_hash
    developer = tmp_path / 'developer.md'
    developer.write_text('Responsibilities: <var: responsibility_summary>')
    monkeypatch.setenv('CEO_DEVELOPER_PROMPT_TEMPLATE_PATH', str(developer))
    monkeypatch.setenv('CEO_PROMPT_VAR_RESPONSIBILITY_SUMMARY', 'First responsibilities')
    first = load_prompt_configuration(create_missing=False)
    monkeypatch.setenv('CEO_PROMPT_VAR_RESPONSIBILITY_SUMMARY', 'Second responsibilities')
    second = load_prompt_configuration(create_missing=False)
    assert first.developer_template == second.developer_template
    assert first.fingerprints()['developer_template'] == second.fingerprints()['developer_template']
    assert first.fingerprints()['developer_instructions'] == sha256(first.developer_instructions.encode()).hexdigest()
    assert first.fingerprints()['developer_instructions'] != second.fingerprints()['developer_instructions']
    assert consumer_wire_contract_hash(prompt_configuration=first) != consumer_wire_contract_hash(prompt_configuration=second)
    assert 'First responsibilities' in consumer_developer_instructions(prompt_configuration=first, runtime_context='')
    assert 'Second responsibilities' in consumer_developer_instructions(prompt_configuration=second, runtime_context='')
