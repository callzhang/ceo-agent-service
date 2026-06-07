import json
from datetime import datetime
from pathlib import Path


def codex_config_has_memory_connector(config_path: Path) -> bool:
    if not config_path.exists():
        return False
    return "[mcp_servers.memory_connector]" in config_path.read_text(encoding="utf-8")


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
    if "[mcp_servers.memory_connector]" in existing:
        return backup_path

    block = f"""

[mcp_servers.memory_connector]
url = {json.dumps(url)}
bearer_token_env_var = {json.dumps(bearer_token_env_var)}
"""
    config_path.write_text(existing.rstrip() + block, encoding="utf-8")
    return backup_path


def ensure_claude_memory_connector_config(
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

    payload = _load_json_object(existing)
    mcp_servers = payload.setdefault("mcpServers", {})
    if not isinstance(mcp_servers, dict):
        mcp_servers = {}
        payload["mcpServers"] = mcp_servers
    if "memory_connector" not in mcp_servers:
        mcp_servers["memory_connector"] = {
            "url": url,
            "headers": {
                "Authorization": f"Bearer ${{{bearer_token_env_var}}}",
            },
        }

    config_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return backup_path


def _backup_path(config_path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    suffix = config_path.suffix or ".config"
    return config_path.with_suffix(f"{suffix}.{timestamp}.bak")


def _load_json_object(text: str) -> dict:
    if not text.strip():
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("Claude config must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Claude config must be a JSON object")
    return payload
