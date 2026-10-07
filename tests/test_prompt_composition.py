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
