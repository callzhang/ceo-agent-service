from pathlib import Path

import pytest

import app.agent_cli as agent_cli
from app.native_cli_metadata import AgentReadOnlyViolationError


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
