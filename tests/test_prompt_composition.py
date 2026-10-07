from app.prompt_composition import append_runtime_context, assemble_consumer_task, join_developer_sections


def test_consumer_task_keeps_existing_complete_order():
    result = assemble_consumer_task(task_context='sources\nfeedback', scheduled_prompt='scheduled', continuation='\ncontinuation')
    assert result == ('## Runtime Invariants\nPreserve typed proposal contracts and session boundaries. The proposal must match the supplied JSON Schema exactly.\n\n'
                      '## Scheduled Consumer Prompt\nscheduled\n\nsources\nfeedback\ncontinuation')


def test_role_parts_preserve_empty_section_semantics_and_runtime_position():
    assert join_developer_sections('role', '', 'profile', omit_empty=True) == 'role\n\nprofile'
    assert join_developer_sections('role', '', 'profile', omit_empty=False) == 'role\n\n\n\nprofile'
    assert append_runtime_context('role\n\nprofile', 'environment') == 'role\n\nprofile\n\nenvironment'
