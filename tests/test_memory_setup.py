import json

from app.memory_setup import (
    claude_memory_connector_status,
    claude_config_has_memory_connector,
    codex_config_has_memory_connector,
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


def test_codex_config_detects_dotted_key_memory_connector(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        'mcp_servers.memory_connector.url = "https://memory.example/mcp/"\n',
        encoding="utf-8",
    )

    assert codex_config_has_memory_connector(config) is True

    ensure_codex_memory_connector_config(config, url="https://memory.example/mcp/")

    assert "[mcp_servers.memory_connector]" not in config.read_text(encoding="utf-8")


def test_claude_config_reports_manual_required_without_writing_remote(tmp_path):
    config = tmp_path / "claude_desktop_config.json"
    original = json.dumps({"mcpServers": {"other": {"url": "https://other"}}})
    config.write_text(original, encoding="utf-8")

    assert claude_config_has_memory_connector(config) is False

    status = claude_memory_connector_status(config)

    assert status["status"] == "manual_required"
    assert "Settings > Connectors" in status["manual_action"]
    assert config.read_text(encoding="utf-8") == original


def test_claude_config_reports_existing_memory_connector(tmp_path):
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

    status = claude_memory_connector_status(config)

    assert status["status"] == "already_configured"
    assert status["manual_action"] == ""
