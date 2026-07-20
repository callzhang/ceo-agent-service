import json
import tomllib
from datetime import datetime
from pathlib import Path


def codex_config_has_memory_connector(config_path: Path) -> bool:
    if not config_path.exists():
        return False
    text = config_path.read_text(encoding="utf-8")
    try:
        payload = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return (
            "[mcp_servers.memory_connector]" in text
            or "mcp_servers.memory_connector." in text
        )
    mcp_servers = payload.get("mcp_servers")
    return isinstance(mcp_servers, dict) and "memory_connector" in mcp_servers


def codex_memory_connector_url(config_path: Path) -> str:
    if not config_path.exists():
        return ""
    try:
        payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return ""
    mcp_servers = payload.get("mcp_servers")
    if not isinstance(mcp_servers, dict):
        return ""
    memory_connector = mcp_servers.get("memory_connector")
    if not isinstance(memory_connector, dict):
        return ""
    url = memory_connector.get("url")
    return url.strip() if isinstance(url, str) else ""


def claude_config_has_memory_connector(config_path: Path) -> bool:
    if not config_path.exists():
        return False
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return '"memory_connector"' in config_path.read_text(encoding="utf-8")
    return "memory_connector" in (payload.get("mcpServers") or {})


def ensure_codex_memory_connector_config(
    config_path: Path,
    *,
    url: str,
    bearer_token_env_var: str = "CONNECTOR_API_KEY",
) -> Path:
    config_path = config_path.expanduser()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    backup_path = _backup_path(config_path)
    backup_path.write_text(existing, encoding="utf-8")
    if codex_config_has_memory_connector(config_path):
        return backup_path

    block = f"""

[mcp_servers.memory_connector]
url = {json.dumps(url)}
bearer_token_env_var = {json.dumps(bearer_token_env_var)}
"""
    config_path.write_text(existing.rstrip() + block, encoding="utf-8")
    return backup_path


def claude_memory_connector_status(config_path: Path) -> dict[str, str]:
    config_path = config_path.expanduser()
    configured = claude_config_has_memory_connector(config_path)
    return {
        "config": str(config_path),
        "status": "already_configured" if configured else "manual_required",
        "manual_action": (
            "Add the remote MCP server named memory_connector through "
            "Claude Settings > Connectors."
            if not configured
            else ""
        ),
    }


def _backup_path(config_path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    suffix = config_path.suffix or ".config"
    return config_path.with_suffix(f"{suffix}.{timestamp}.bak")
