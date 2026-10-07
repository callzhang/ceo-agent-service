from app.prompt_composition import append_runtime_context, assemble_consumer_task, join_developer_sections


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
