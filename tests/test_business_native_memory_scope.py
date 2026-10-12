import pytest

from app.wechat.codex_safety import make_role_agent_command


@pytest.mark.parametrize('role', ['consumer', 'audit'])
def test_business_role_excludes_workspace_memory_but_keeps_service_instructions(
    role, monkeypatch
):
    monkeypatch.setattr('app.wechat.codex_safety.codex_skill_config_override', lambda _: '')
    developer = 'developer_instructions="Service Profile and current business facts."'
    command = [
        'codex', 'exec', '-c', developer,
        '-c', 'memories.use_memories=true',
        '-c', 'memories.generate_memories=true', '-'
    ]
    make_role_agent_command(
        command, role=role, task_workspace='/task/current' if role == 'consumer' else None
    )
    uses = [x for x in command if x.startswith('memories.use_memories=')]
    assert uses == ['memories.use_memories=false']
    assert 'memories.generate_memories=true' in command
    assert developer in command
    assert command[-1] == '-'
