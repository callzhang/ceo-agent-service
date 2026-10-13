"""The native copy harness must not create transport-less MCP server entries."""
import json
from pathlib import Path

from scripts.validate_native_copy_lifecycle import restricted_command


def command(db_path):
    return ["codex", "exec", "-c", 'mcp_servers.agent_cli.command="python"',
        "-c", "mcp_servers.agent_cli.args=" + json.dumps(["-m", "app.agent_cli", "--db", str(db_path)]),
        "-c", 'sandbox_mode="workspace-write"', "--json", "-"]


def test_empty_service_manifest_does_not_create_unconfigured_mcp_transports(monkeypatch):
    monkeypatch.setattr("app.wechat.codex_safety.inherited_native_server_names", lambda: ())
    db = Path("/private/tmp/copy/candidate.sqlite3")
    args = restricted_command(command(db), workspace=db.parent, db_path=db)
    assert not any(arg.startswith("mcp_servers.exa.") for arg in args)
    assert not any(arg.startswith("mcp_servers.memory_connector.") for arg in args)
    assert 'mcp_servers.agent_cli.enabled_tools=["read_task_artifact","read_system_action_contract"]' in args


def test_only_real_inherited_servers_are_disabled_and_native_roles_are_preserved(monkeypatch):
    monkeypatch.setattr("app.wechat.codex_safety.inherited_native_server_names", lambda: ("actual_server",))
    db = Path("/private/tmp/copy/candidate.sqlite3")
    args = restricted_command(command(db), workspace=db.parent, db_path=db)
    assert "mcp_servers.actual_server.enabled=false" in args
    assert 'sandbox_mode="workspace-write"' in args
    assert "--skip-git-repo-check" in args
    env = next(arg for arg in args if arg.startswith("mcp_servers.agent_cli.env="))
    assert str(db) in env and '"CEO_WORKSPACE"' in env


def test_resume_scoped_options_follow_the_resume_subcommand(monkeypatch):
    monkeypatch.setattr("app.wechat.codex_safety.inherited_native_server_names", lambda: ())
    db = Path("/private/tmp/copy/candidate.sqlite3")
    cmd = command(db)
    cmd.insert(cmd.index("exec") + 1, "resume")
    args = restricted_command(cmd, workspace=db.parent, db_path=db)
    env_index = next(i for i, value in enumerate(args) if value.startswith("mcp_servers.agent_cli.env="))
    assert env_index > args.index("resume")


def test_scoped_tool_list_overrides_existing_role_tool_list(monkeypatch):
    monkeypatch.setattr("app.wechat.codex_safety.inherited_native_server_names", lambda: ())
    db = Path("/private/tmp/copy/candidate.sqlite3")
    cmd = command(db)
    cmd[-1:-1] = ["-c", 'mcp_servers.agent_cli.enabled_tools=["read_dingtalk_conversations"]']
    args = restricted_command(cmd, workspace=db.parent, db_path=db)
    lists = [x for x in args if x.startswith("mcp_servers.agent_cli.enabled_tools=")]
    assert lists[-1] == 'mcp_servers.agent_cli.enabled_tools=["read_task_artifact","read_system_action_contract"]'
