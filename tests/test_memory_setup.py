import json

from app.memory_setup import (
    claude_config_has_memory_connector,
    codex_config_has_memory_connector,
    ensure_claude_memory_connector_config,
    ensure_codex_memory_connector_config,
)


def test_codex_config_detection_and_update(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('[mcp_servers.other]\nurl = "https://other"\n', encoding="utf-8")

    assert codex_config_has_memory_connector(config) is False

    backup_path = ensure_codex_memory_connector_config(
        config,
        url="https://memory.example/mcp/",
        bearer_token_env_var="CONNECTOR_API_KEY",
    )

    content = config.read_text(encoding="utf-8")
    assert "[mcp_servers.memory_connector]" in content
    assert 'url = "https://memory.example/mcp/"' in content
    assert 'bearer_token_env_var = "CONNECTOR_API_KEY"' in content
    assert backup_path.exists()
    assert backup_path.read_text(encoding="utf-8") == (
        '[mcp_servers.other]\nurl = "https://other"\n'
    )


def test_codex_config_update_is_idempotent(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        '[mcp_servers.memory_connector]\nurl = "https://memory.example/mcp/"\n',
        encoding="utf-8",
    )

    ensure_codex_memory_connector_config(config, url="https://memory.example/mcp/")

    assert config.read_text(encoding="utf-8").count(
        "[mcp_servers.memory_connector]"
    ) == 1


def test_claude_config_detection_and_update(tmp_path):
    config = tmp_path / "claude_desktop_config.json"
    config.write_text(json.dumps({"mcpServers": {"other": {"url": "https://other"}}}))

    assert claude_config_has_memory_connector(config) is False

    backup_path = ensure_claude_memory_connector_config(
        config,
        url="https://memory.example/mcp/",
        bearer_token_env_var="CONNECTOR_API_KEY",
    )

    payload = json.loads(config.read_text(encoding="utf-8"))
    assert payload["mcpServers"]["memory_connector"] == {
        "url": "https://memory.example/mcp/",
        "headers": {
            "Authorization": "Bearer ${CONNECTOR_API_KEY}",
        },
    }
    assert backup_path.exists()


def test_claude_config_update_preserves_existing_memory_connector(tmp_path):
    config = tmp_path / "claude_desktop_config.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "memory_connector": {
                        "url": "https://existing.example/mcp/",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    ensure_claude_memory_connector_config(config, url="https://memory.example/mcp/")

    payload = json.loads(config.read_text(encoding="utf-8"))
    assert payload["mcpServers"]["memory_connector"]["url"] == (
        "https://existing.example/mcp/"
    )
