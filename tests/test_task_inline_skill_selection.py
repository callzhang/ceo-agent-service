import tomllib

import pytest

from app.agent_runtime_config import load_runtime_config
from app.agent_runtime_router import CodexCommandFactory
from app.codex_runtime_adapter import CodexRuntimeAdapter


@pytest.mark.parametrize("session_id", [None, "same-task-session"])
def test_selected_and_inlined_skills_compile_to_one_native_override(tmp_path, monkeypatch, session_id):
    root = tmp_path / "skills"
    for name in ("ceo-work-tracking", "other"):
        path = root / name / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text(f"---\nname: {name}\ndescription: Test source.\n---\nFull body.")
    monkeypatch.setattr("app.business_skills.codex_skill_roots", lambda **kwargs: (root, tmp_path / "empty1", tmp_path / "empty2"))
    monkeypatch.setattr("app.codex_runtime_adapter.service_mcp_config_options", lambda **kwargs: [])
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
