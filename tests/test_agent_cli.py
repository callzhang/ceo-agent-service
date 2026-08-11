import asyncio
import subprocess
from pathlib import Path

import pytest

import app.agent_cli as agent_cli
from app.native_cli_metadata import AgentReadOnlyViolationError


def test_agent_cli_mcp_tools_publish_searchable_descriptions():
    tools = asyncio.run(agent_cli.server.list_tools())
    descriptions = {tool.name: tool.description for tool in tools}

    assert set(descriptions) == {
        "read_skill",
        "execute_reviewed_read",
        "execute_reviewed_write",
    }
    assert all(description.strip() for description in descriptions.values())
    assert "calendar event" in descriptions["execute_reviewed_read"]


def test_read_skill_allows_markdown_referenced_by_an_installed_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "skills"
    skill = root / "dingtalk-misc"
    reference = skill / "references" / "oa.md"
    reference.parent.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# DingTalk misc", encoding="utf-8")
    reference.write_text("# OA reference", encoding="utf-8")
    monkeypatch.setattr(agent_cli, "AGENT_SKILL_ROOTS", (root,))

    result = agent_cli.read_skill(str(reference))

    assert result["content"] == "# OA reference"
    assert result["sha256"]


@pytest.mark.parametrize("filename", ("notes.md", "references/oa.txt"))
def test_read_skill_rejects_files_outside_an_installed_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, filename: str
):
    root = tmp_path / "skills"
    target = root / filename
    target.parent.mkdir(parents=True)
    target.write_text("not an installed skill document", encoding="utf-8")
    monkeypatch.setattr(agent_cli, "AGENT_SKILL_ROOTS", (root,))

    with pytest.raises(AgentReadOnlyViolationError, match="skill_path_forbidden"):
        agent_cli.read_skill(str(target))


def test_execute_reviewed_read_allows_python_when_principal_policy_allows_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[ceo_agent.local_read_policy]\nblocked_commands = [\"rm\"]\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CEO_AGENT_CODEX_CONFIG_PATH", str(config_path))
    monkeypatch.setattr(agent_cli.shutil, "which", lambda _: "/usr/bin/python3")

    receipt = agent_cli.execute_reviewed_read(
        ["python3", "-c", "print('workbook parsed')"],
        process_runner=lambda argv, **_: subprocess.CompletedProcess(
            argv, 0, "workbook parsed\n", ""
        ),
    )

    assert receipt["cli"] == "local-shell"
    assert receipt["stdout"] == "workbook parsed\n"
