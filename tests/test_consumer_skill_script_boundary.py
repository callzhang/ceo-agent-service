from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.agent_cli import _approved_consumer_skill_script, build_role_server


def test_person_facing_skill_script_is_not_a_consumer_entrypoint() -> None:
    # The selected WeChat Skill ships this sender, but Consumer cannot execute it.
    with pytest.raises(ValueError, match="not reviewed"):
        _approved_consumer_skill_script("ceo-wechat", "scripts/sender.py")


def test_consumer_tool_rejects_sender_before_starting_a_process(monkeypatch) -> None:
    monkeypatch.setattr("app.agent_cli._read_bound_task_skill", lambda *args: {})
    monkeypatch.setattr(
        "app.agent_cli.subprocess.run",
        lambda *args, **kwargs: pytest.fail("sender process must not start"),
    )
    tool = build_role_server("consumer")._tool_manager.get_tool(
        "consumer_run_skill_script"
    )
    with pytest.raises(ValueError, match="not reviewed"):
        tool.fn("ceo-wechat", "scripts/sender.py", ["approve", "--id", "1"])


def test_report_skill_only_exposes_reviewed_entrypoints() -> None:
    assert _approved_consumer_skill_script(
        "ceo-weekly-report", "scripts/materials.py"
    ).is_file()
    with pytest.raises(ValueError, match="not reviewed"):
        _approved_consumer_skill_script(
            "ceo-weekly-report", "scripts/report_contract.py"
        )


def test_okr_report_prepare_script_matches_reviewed_package(tmp_path, monkeypatch) -> None:
    source = Path(__file__).resolve().parent.parent / "ci/shared-skills"
    skill = source / "ceo-weekly-okr-report"
    monkeypatch.setattr("app.agent_cli.CONSUMER_SCRIPT_SOURCE_ROOT", source)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    installed = tmp_path / ".agents/skills/ceo-weekly-okr-report"
    shutil.copytree(skill, installed)

    assert _approved_consumer_skill_script(
        "ceo-weekly-okr-report", "scripts/report.py"
    ).is_file()


def test_installed_script_must_match_reviewed_package(tmp_path, monkeypatch) -> None:
    source = Path(__file__).resolve().parent.parent / "ci/shared-skills"
    monkeypatch.setattr("app.agent_cli.CONSUMER_SCRIPT_SOURCE_ROOT", source)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    installed = tmp_path / ".agents/skills/ceo-daily-report/scripts"
    installed.mkdir(parents=True)
    original = source / "ceo-daily-report/scripts"
    for path in original.glob("*.py"):
        (installed / path.name).write_bytes(path.read_bytes())
    assert _approved_consumer_skill_script(
        "ceo-daily-report", "scripts/window.py"
    ).is_file()
    (installed / "window.py").write_text("print('changed')\n")
    with pytest.raises(ValueError, match="not reviewed"):
        _approved_consumer_skill_script("ceo-daily-report", "scripts/window.py")
