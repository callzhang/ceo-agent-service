import json
import tomllib
from types import SimpleNamespace

import pytest

from app.agent_runtime_config import load_runtime_config
from app.agent_runtime_router import _claude_turn_text, _friday_turn_text
from app.codex_runtime_adapter import CodexRuntimeAdapter
from app.email_classifier_agent import EmailClassifierRoutedBackend
from tests.runtime_route_env import codex_api_env


@pytest.mark.parametrize("route_name", ["codex_oauth", "codex_api", "qwen_gpu4"])
@pytest.mark.parametrize("session_id", [None, "existing-classifier-session"])
def test_classifier_preserves_managed_skill_and_suppresses_only_native_copy(
    tmp_path, monkeypatch, session_id, route_name
):
    def no_catalog_scan(**kwargs):
        raise AssertionError("Native name selectors do not require a filesystem scan")

    monkeypatch.setattr("app.business_skills.codex_skill_roots", no_catalog_scan)
    manifest = tmp_path / "mcp.json"
    manifest.write_text('{"servers":{}}')
    monkeypatch.setenv("CEO_SERVICE_MCP_CONFIG_PATH", str(manifest))
    recorded = {}

    class Execution:
        def execute(self, **kwargs):
            recorded.update(kwargs)
            return SimpleNamespace(
                value=kwargs["parser"](
                    json.dumps(
                        {
                            "category": "work",
                            "important": False,
                            "certainty": "certain",
                            "confidence": 0.9,
                            "reason": "Source-backed project discussion.",
                            "unsubscribe_candidate_index": None,
                        }
                    )
                )
            )

    prompt = "EXACT MANAGED SKILL AND CURRENT FACTS"
    EmailClassifierRoutedBackend(Execution()).classify(
        prompt=prompt,
        task_id="synthetic",
        allowed_category_keys=("work",),
        unsubscribe_candidates=(),
    )
    cfg = load_runtime_config(
        {
            "CEO_AGENT_RUNTIME_ROUTES": "codex_oauth,codex_api,qwen_gpu4",
            **codex_api_env("test-secret"),
            **codex_api_env(
                "test-gateway-key",
                name="qwen_gpu4",
                model="qwen3.8-27b",
                base_url="http://example.invalid/v1",
            ),
        }
    )
    factory = recorded["command_factory"]
    command, _ = factory.build(
        adapter=CodexRuntimeAdapter(tmp_path, cfg),
        route=next(route for route in cfg.routes if route.name == route_name),
        prompt=prompt,
        session_id=session_id,
    )
    overrides = [value for value in command if value.startswith("skills.config=")]
    assert len(overrides) == 1
    entries = tomllib.loads(overrides[0])["skills"]["config"]
    assert entries == [{"name": "ceo-email-classifier", "enabled": False}]
    assert recorded["prompt"] == prompt
    assert recorded["conversation_id"] is None
    assert "approval_policy=never" not in command
    assert _claude_turn_text(factory, prompt).count(prompt) == 1
    assert _friday_turn_text(factory, prompt).count(prompt) == 1
