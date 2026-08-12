import asyncio
import os
import subprocess
from pathlib import Path

import pytest

import app.agent_cli as agent_cli
from app.agent_result import EffectKind
from app.native_cli_metadata import NativeCliMetadataClassifier
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
        '[ceo_agent.local_read_policy]\nblocked_commands = ["rm"]\n',
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


def test_execute_reviewed_write_preserves_provider_error_instead_of_read_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(agent_cli.shutil, "which", lambda _: "/usr/local/bin/dws")

    receipt = agent_cli.execute_reviewed_write(
        [
            "dws",
            "chat",
            "message",
            "send",
            "--user",
            "user-1",
            "--text",
            "hello",
            "--yes",
        ],
        process_runner=lambda argv, **_: subprocess.CompletedProcess(
            argv, 1, '{"code":"1001","message":"provider rejected request"}', ""
        ),
    )

    assert receipt["error"] == {
        "channel": "dws",
        "code": "1001",
        "retryable": False,
        "gate_state": "blocked",
        "detail": '{"code": "1001"}',
    }


def _write_classifier() -> NativeCliMetadataClassifier:
    return NativeCliMetadataClassifier(
        reviewed_effects={
            ("dws", "chat message send"): EffectKind.EFFECTFUL,
            ("dws", "calendar event create"): EffectKind.EFFECTFUL,
        }
    )


def test_explicit_reviewed_write_authorization_is_exact_and_does_not_touch_environment(
    monkeypatch: pytest.MonkeyPatch,
):
    argv = [
        "dws",
        "chat",
        "message",
        "send",
        "--user",
        "user-1",
        "--text",
        "hello",
        "--yes",
    ]
    authorization = agent_cli.review_write_authorization(
        argv,
        authorization_id="confirmation-1",
        action_index=0,
        classifier=_write_classifier(),
    )
    before = dict(os.environ)
    calls: list[list[str]] = []
    monkeypatch.setattr(agent_cli.shutil, "which", lambda _: "/usr/local/bin/dws")
    consumed = False

    def consume(reviewed):
        nonlocal consumed
        if consumed:
            raise AgentReadOnlyViolationError("reviewed_write_authorization_consumed")
        assert reviewed == authorization
        consumed = True

    receipt = agent_cli.execute_reviewed_write(
        argv,
        authorization=authorization,
        authorization_id="confirmation-1",
        action_index=0,
        authorization_consumer=consume,
        classifier=_write_classifier(),
        process_runner=lambda command, **_: (
            calls.append(command)
            or subprocess.CompletedProcess(command, 0, '{"ok":true}', "")
        ),
    )

    assert len(calls) == 1
    assert receipt["authorization_id"] == "confirmation-1"
    assert receipt["action_index"] == 0
    assert dict(os.environ) == before

    with pytest.raises(
        AgentReadOnlyViolationError, match="reviewed_write_authorization_consumed"
    ):
        agent_cli.execute_reviewed_write(
            argv,
            authorization=authorization,
            authorization_id="confirmation-1",
            action_index=0,
            authorization_consumer=consume,
            classifier=_write_classifier(),
            process_runner=lambda command, **_: subprocess.CompletedProcess(
                command, 0, "", ""
            ),
        )
    assert len(calls) == 1


def test_explicit_authorization_requires_a_consuming_boundary_before_runner(
    monkeypatch: pytest.MonkeyPatch,
):
    argv = [
        "dws", "chat", "message", "send", "--user", "user-1", "--text",
        "hello", "--yes",
    ]
    authorization = agent_cli.review_write_authorization(
        argv, "confirmation-1", 0, classifier=_write_classifier()
    )
    calls = []
    monkeypatch.setattr(agent_cli.shutil, "which", lambda _: "/usr/local/bin/dws")
    with pytest.raises(
        AgentReadOnlyViolationError,
        match="reviewed_write_authorization_consumer_required",
    ):
        agent_cli.execute_reviewed_write(
            argv,
            authorization=authorization,
            authorization_id="confirmation-1",
            action_index=0,
            classifier=_write_classifier(),
            process_runner=lambda *args, **kwargs: calls.append((args, kwargs)),
        )
    assert calls == []


@pytest.mark.parametrize("argv", ("dws", ["dws", "bad\0argument"]))
def test_review_write_authorization_rejects_unsafe_argv(argv):
    with pytest.raises(AgentReadOnlyViolationError, match="agent_cli_command_invalid"):
        agent_cli.review_write_authorization(
            argv,
            authorization_id="confirmation-1",
            action_index=0,
            classifier=_write_classifier(),
        )


@pytest.mark.parametrize(
    ("changed_argv", "authorization_id", "action_index"),
    [
        (
            [
                "dws",
                "chat",
                "message",
                "send",
                "--user",
                "user-2",
                "--text",
                "hello",
                "--yes",
            ],
            "confirmation-1",
            0,
        ),
        (
            [
                "dws",
                "chat",
                "message",
                "send",
                "--user",
                "user-1",
                "--text",
                "changed",
                "--yes",
            ],
            "confirmation-1",
            0,
        ),
        (
            ["dws", "calendar", "event", "create", "--user", "user-1", "--yes"],
            "confirmation-1",
            0,
        ),
        (
            [
                "dws",
                "chat",
                "message",
                "send",
                "--user",
                "user-1",
                "--text",
                "hello",
                "--yes",
            ],
            "confirmation-2",
            0,
        ),
        (
            [
                "dws",
                "chat",
                "message",
                "send",
                "--user",
                "user-1",
                "--text",
                "hello",
                "--yes",
            ],
            "confirmation-1",
            1,
        ),
    ],
)
def test_explicit_reviewed_write_authorization_rejects_any_change_before_runner(
    monkeypatch: pytest.MonkeyPatch,
    changed_argv: list[str],
    authorization_id: str,
    action_index: int,
):
    original = [
        "dws",
        "chat",
        "message",
        "send",
        "--user",
        "user-1",
        "--text",
        "hello",
        "--yes",
    ]
    authorization = agent_cli.review_write_authorization(
        original,
        authorization_id="confirmation-1",
        action_index=0,
        classifier=_write_classifier(),
    )
    calls = 0

    def runner(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("runner must not execute")

    monkeypatch.setattr(agent_cli.shutil, "which", lambda _: "/usr/local/bin/dws")
    with pytest.raises(
        AgentReadOnlyViolationError, match="reviewed_write_authorization_mismatch"
    ):
        agent_cli.execute_reviewed_write(
            changed_argv,
            authorization=authorization,
            authorization_id=authorization_id,
            action_index=action_index,
            classifier=_write_classifier(),
            process_runner=runner,
        )
    assert calls == 0
