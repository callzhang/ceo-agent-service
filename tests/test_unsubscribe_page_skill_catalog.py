from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent_runtime_config import load_runtime_config
from app.agent_runtime_router import CodexCommandFactory
from app.codex_runtime_adapter import CodexRuntimeAdapter
from app.email_unsubscribe_page_judge import build_page_judge


@pytest.mark.parametrize('selection', [None, (), ('page-guide',)])
def test_native_catalog_distinguishes_unspecified_from_explicit_empty(
    tmp_path, monkeypatch, selection
):
    home = tmp_path / 'home'
    skills = home / '.agents' / 'skills'
    for name in ('page-guide', 'unrelated'):
        path = skills / name / 'SKILL.md'
        path.parent.mkdir(parents=True)
        path.write_text(f'---\nname: {name}\ndescription: Test guide.\n---\nBody\n')
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    config = load_runtime_config({'CEO_AGENT_RUNTIME_ROUTES': 'codex_oauth'})
    adapter = CodexRuntimeAdapter(tmp_path, config, codex_bin='codex-test')
    kwargs = {} if selection is None else {'skill_names': selection}
    factory = CodexCommandFactory.standard(
        developer_instructions='Judge the supplied page.', **kwargs
    )
    command, _ = factory.build(
        adapter=adapter, route=config.routes[0], prompt='Page text', session_id=None
    )
    overrides = [
        command[i + 1] for i, part in enumerate(command[:-1])
        if part == '-c' and command[i + 1].startswith('skills.config=')
    ]
    if selection is None:
        assert overrides == []
    else:
        assert len(overrides) == 1
        assert f'{{path="{skills / "unrelated" / "SKILL.md"}",enabled=false}}' in overrides[0]
        enabled = 'true' if selection else 'false'
        assert f'{{path="{skills / "page-guide" / "SKILL.md"}",enabled={enabled}}}' in overrides[0]
    assert '--output-schema' not in command
    assert '--dangerously-bypass-approvals-and-sandbox' not in command


def test_page_judge_explicitly_selects_no_discoverable_skills():
    calls = []

    def execute(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(value=kwargs['parser']('{"state":"done","evidence":"Unsubscribed"}'))

    judgement = build_page_judge(SimpleNamespace(execute=execute))(
        'synthetic-action', 'example.test', 'Unsubscribed', []
    )
    assert judgement.evidence == 'Unsubscribed'
    assert calls[0]['command_factory'].skill_names == ()
