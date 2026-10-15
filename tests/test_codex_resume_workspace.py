from pathlib import Path

import pytest

from app.codex_runner import CodexRunner


@pytest.mark.parametrize("session_id", [None, "existing-native-session"])
def test_native_turn_keeps_selected_workspace_independent_of_launcher(tmp_path, session_id):
    workspace = tmp_path / "task workspace"
    runner = CodexRunner(workspace=workspace)
    command = runner.build_command(prompt="Exact next turn.", session_id=session_id)
    assert command.count("--cd") == 1
    assert command[command.index("--cd") + 1] == str(workspace)
    if session_id:
        assert command[:5] == ["codex", "--cd", str(workspace), "exec", "resume"]
        assert command[-2:] == [session_id, "-"]
    else:
        assert command[-3:] == ["--cd", str(workspace), "-"]
    assert Path.cwd() != workspace


def test_workbench_resume_overlays_do_not_split_global_workspace(tmp_path):
    from app.workbench.codex_runtime import CodexRuntime, _command_with_mcp_startup_timeouts

    session_id = "019ff6ad-c139-7411-9169-6220e8b39688"
    command = CodexRuntime(workspace=tmp_path).build_command(
        prompt="Exact next turn.", provider_session_ref=session_id, model="gpt-example",
    )
    config = tmp_path / "config.toml"
    config.write_text('[mcp_servers.fixture]\nurl="https://example.invalid/mcp"\n')
    combined = _command_with_mcp_startup_timeouts(command, config)
    assert combined[:5] == ["codex", "--cd", str(tmp_path), "exec", "resume"]
    assert combined[-2:] == [session_id, "-"]
    assert combined.index("-m") > combined.index("resume")
    assert combined[combined.index("-m") + 1] == "gpt-example"
    timeout = "mcp_servers.fixture.startup_timeout_sec=120"
    assert combined.index(timeout) > combined.index("resume")
