from pathlib import Path

import pytest

from app import consumer_agent


@pytest.mark.parametrize("role", ["Consumer", "Audit"])
def test_role_prompt_does_not_inject_host_development_rules(tmp_path, monkeypatch, role):
    host_rules = tmp_path / "AGENT.md"
    host_rules.write_text("HOST_DEVELOPMENT_RULE_SENTINEL", encoding="utf-8")
    monkeypatch.setattr(consumer_agent, "SHARED_RULES_PATH", host_rules, raising=False)
    original_read = Path.read_text

    def guarded_read(path, *args, **kwargs):
        assert path != host_rules, "service prompt must not read host development rules"
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read)
    from app.prompt_composition import render_prompt_sections

    if role == "Consumer":
        sections = consumer_agent.consumer_developer_sections(
            runtime_context="ROLE_CAPABILITIES"
        )
    else:
        sections = consumer_agent.audit_developer_sections(
            "", runtime_context="ROLE_CAPABILITIES"
        )
    instructions = render_prompt_sections(sections, placement="developer")
    assert "Shared Agent Rules" not in instructions
    assert "HOST_DEVELOPMENT_RULE_SENTINEL" not in instructions
    assert "Do not reopen AGENT.md" not in instructions
    assert role in instructions
    assert "ROLE_CAPABILITIES" in instructions
    assert "Role Boundary" in instructions
    assert "bootstrap requirements do not apply" in instructions
