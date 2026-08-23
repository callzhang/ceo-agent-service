import asyncio
import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

import app.agent_cli as agent_cli
from app.agent_result import EffectKind
from app.feedback_spike import prepare_outgoing_reply_text
from app.native_cli_metadata import NativeCliMetadataClassifier
from app.native_cli_metadata import AgentReadOnlyViolationError
from app.store import AgentRole, AutoReplyStore


def test_agent_cli_mcp_tools_publish_searchable_descriptions():
    tools = asyncio.run(agent_cli.server.list_tools())
    descriptions = {tool.name: tool.description for tool in tools}

    assert set(descriptions) == {
        "read_skill",
        "read_text_file",
        "read_spreadsheet",
        "execute_reviewed_read",
        "execute_reviewed_write",
    }
    assert all(description.strip() for description in descriptions.values())
    assert "calendar event" in descriptions["execute_reviewed_read"]


def test_registered_reaction_write_is_accepted_when_dws_schema_is_incomplete():
    class Unclassified:
        @staticmethod
        def classify(_item):
            return None

    argv = [
        "dws",
        "chat",
        "message",
        "reaction",
        "add",
        "--message-id",
        "msg-1",
        "--emoji",
        "👍",
        "--yes",
    ]

    classifier = Unclassified()
    canonical, command = agent_cli._classify_reviewed_write(
        argv, classifier=classifier
    )

    assert canonical == tuple(argv)
    assert command.cli == "dws"
    assert command.command_path == "chat message reaction add"
    assert command.effect is EffectKind.EFFECTFUL

    authorization = agent_cli.review_write_authorization(
        argv,
        authorization_id="reaction-authorization",
        action_index=0,
        classifier=classifier,
    )
    receipt = agent_cli.execute_reviewed_write(
        argv,
        authorization_id="reaction-authorization",
        action_index=0,
        authorization=authorization,
        authorization_consumer=lambda consumed: None,
        classifier=classifier,
        process_runner=lambda command, **_: subprocess.CompletedProcess(
            command, 0, '{"success":true}', ""
        ),
    )

    assert receipt["operation"] == "chat message reaction add"
    assert receipt["authorization_id"] == "reaction-authorization"


def test_dws_message_allows_exact_service_feedback_callbacks_in_content(
    monkeypatch: pytest.MonkeyPatch,
):
    base_url = "https://feedback.example.test"
    monkeypatch.setenv("CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL", base_url)
    content = prepare_outgoing_reply_text(
        reply_text="已完成复核。",
        original_text="触发消息",
        feedback_base_url=base_url,
        feedback_token="spike_1787501265_e576c9d1",
    ).text
    argv = [
        "dws",
        "chat",
        "+send-to-group",
        "--group",
        "cid-agent",
        "--content",
        content,
        "--yes",
    ]

    assert agent_cli._validate_reviewed_argv(argv) == tuple(argv)


def test_dws_message_rejects_non_service_tokenized_url_in_content(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv(
        "CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL", "https://feedback.example.test"
    )
    argv = [
        "dws",
        "chat",
        "+send-to-group",
        "--group",
        "cid-agent",
        "--content",
        "请访问 https://attacker.example/api?feedback_token=opaque-token&rating=up",
        "--yes",
    ]

    with pytest.raises(AgentReadOnlyViolationError, match="sensitive_argument"):
        agent_cli._validate_reviewed_argv(argv)


def test_read_text_file_reads_bounded_temp_material(tmp_path: Path):
    material = tmp_path / "material.md"
    material.write_text("# Verified material", encoding="utf-8")

    result = agent_cli.read_text_file(str(material))

    assert result["content"] == "# Verified material"
    assert result["path"] == str(material.resolve())
    assert result["sha256"]


def test_read_text_file_rejects_non_utf8_material(tmp_path: Path):
    material = tmp_path / "material.bin"
    material.write_bytes(b"\xff\xfe")

    with pytest.raises(
        AgentReadOnlyViolationError,
        match="text_material_invalid_utf8",
    ):
        agent_cli.read_text_file(str(material))


def test_read_text_file_detects_xlsx_without_filename_extension(tmp_path: Path):
    material = tmp_path / "downloaded-material"
    with zipfile.ZipFile(material, "w") as workbook:
        workbook.writestr("[Content_Types].xml", "<Types/>")
        workbook.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" r:id="rId1"/></sheets></workbook>',
        )
        workbook.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        workbook.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Verified</t></is></c></row></sheetData></worksheet>',
        )

    result = agent_cli.read_text_file(str(material))

    assert result["format"] == "xlsx"
    assert result["sheets"][0]["rows"][0]["cells"] == {"A": "Verified"}


def test_read_text_file_detects_pptx_without_filename_extension(tmp_path: Path):
    material = tmp_path / "downloaded-deck"
    with zipfile.ZipFile(material, "w") as presentation:
        presentation.writestr("[Content_Types].xml", "<Types/>")
        presentation.writestr("ppt/presentation.xml", "<presentation/>")
        presentation.writestr(
            "ppt/slides/slide1.xml",
            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:t>Verified deck</p:t></p:sld>',
        )

    result = agent_cli.read_text_file(str(material))

    assert result["format"] == "pptx"
    assert result["slides"] == [{"index": 1, "text": "Verified deck"}]


def test_read_skill_allows_markdown_referenced_by_an_installed_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "skills"
    skill = root / "dingtalk-misc"
    reference = skill / "references" / "oa.md"
    reference.parent.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# DingTalk misc", encoding="utf-8")
    reference.write_text("# OA reference", encoding="utf-8")
    monkeypatch.setattr("app.agent_skill_usage.AGENT_SKILL_ROOTS", (root,))

    result = agent_cli.read_skill(str(reference))

    assert result["content"] == "# OA reference"
    assert result["sha256"]
    assert result["path"] == str(reference.resolve())
    assert result["name"] == "dingtalk-misc"


def test_read_skill_ignores_missing_configured_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "skills"
    skill_path = root / "business-review" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Business review", encoding="utf-8")
    monkeypatch.setattr(
        "app.agent_skill_usage.AGENT_SKILL_ROOTS",
        (tmp_path / "missing-skills", root),
    )

    result = agent_cli.read_skill(str(skill_path))

    assert result["path"] == str(skill_path.resolve())
    assert result["name"] == "business-review"


@pytest.mark.parametrize("alias", ("tilde", "symlink"))
def test_read_skill_returns_canonical_identity_for_supported_aliases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    alias: str,
):
    home = tmp_path / "home"
    root = home / ".agents" / "skills"
    skill_path = root / "business-review" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Business review", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("app.agent_skill_usage.AGENT_SKILL_ROOTS", (root,))
    if alias == "tilde":
        requested = "~/.agents/skills/business-review/SKILL.md"
    else:
        link = tmp_path / "skill-alias.md"
        link.symlink_to(skill_path)
        requested = str(link)

    result = agent_cli.read_skill(requested)

    assert result == {
        "content": "# Business review",
        "sha256": result["sha256"],
        "path": str(skill_path.resolve()),
        "name": "business-review",
    }


def test_read_skill_rejects_symlink_escape_from_authorized_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "skills"
    skill = root / "business-review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Business review", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    escaped = skill / "escape.md"
    escaped.symlink_to(outside)
    monkeypatch.setattr("app.agent_skill_usage.AGENT_SKILL_ROOTS", (root,))

    with pytest.raises(AgentReadOnlyViolationError, match="skill_path_forbidden"):
        agent_cli.read_skill(str(escaped))


@pytest.mark.parametrize("filename", ("notes.md", "references/oa.txt"))
def test_read_skill_rejects_files_outside_an_installed_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, filename: str
):
    root = tmp_path / "skills"
    target = root / filename
    target.parent.mkdir(parents=True)
    target.write_text("not an installed skill document", encoding="utf-8")
    monkeypatch.setattr("app.agent_skill_usage.AGENT_SKILL_ROOTS", (root,))

    with pytest.raises(AgentReadOnlyViolationError, match="skill_path_forbidden"):
        agent_cli.read_skill(str(target))


def test_execute_reviewed_read_rejects_arbitrary_python(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(agent_cli.shutil, "which", lambda _: "/usr/bin/python3")

    with pytest.raises(
        AgentReadOnlyViolationError,
        match="agent_cli_command_unreviewed",
    ):
        agent_cli.execute_reviewed_read(
            ["python3", "-c", "open('/tmp/escape', 'w').write('escaped')"],
            process_runner=lambda argv, **_: subprocess.CompletedProcess(
                argv, 0, "", ""
            ),
        )


def test_execute_reviewed_read_classifies_dws_from_exact_schema_without_prewarm(
    monkeypatch: pytest.MonkeyPatch,
):
    classifier = NativeCliMetadataClassifier()
    metadata_calls: list[list[str]] = []
    monkeypatch.setattr(agent_cli.shutil, "which", lambda _: "/usr/local/bin/dws")
    monkeypatch.setattr(
        classifier,
        "prewarm",
        lambda: pytest.fail("DWS command validation must not preload every tool"),
    )

    def exact_schema(argv, *, timeout):
        metadata_calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, '{"effect":"read"}', "")

    monkeypatch.setattr("app.native_cli_metadata.run_bounded_process", exact_schema)

    receipt = agent_cli.execute_reviewed_read(
        ["dws", "chat", "message", "get", "--message-id", "message-1"],
        classifier=classifier,
        process_runner=lambda argv, **_: subprocess.CompletedProcess(argv, 0, "{}", ""),
    )

    assert metadata_calls == [
        [
            "dws",
            "schema",
            "--cli-path",
            "chat message get",
            "--compact",
            "--format",
            "json",
        ]
    ]
    assert receipt["operation"] == "chat message get"


def test_execute_reviewed_write_preserves_provider_error_instead_of_read_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(agent_cli.shutil, "which", lambda _: "/usr/local/bin/dws")
    classifier = NativeCliMetadataClassifier(
        reviewed_effects={
            ("dws", "chat message send"): EffectKind.EFFECTFUL,
        }
    )

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
        classifier=classifier,
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


def test_mcp_write_tool_consumes_durable_intent_and_persists_ack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    assert store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Group",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-08-21 00:00:00",
        trigger_sender="Derek",
        trigger_text="Send the reply",
    )
    task = store.claim_reply_tasks(limit=1)[0]
    run = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="operation-1",
        owner="audit-owner",
    ).run
    argv = [
        "dws", "chat", "message", "send", "--group", "cid-1",
        "--text", "done", "--yes",
    ]
    descriptor = agent_cli.describe_native_command(
        {"type": "command_execution", "argv": argv}
    )
    assert descriptor is not None
    authorization = {
        "authorization_id": "authorization-1",
        "action_index": 0,
        "receipt_operation_id": "operation-action-0",
        "capability": "agent_cli.dws",
        "operation": descriptor.command_path,
        "operation_digest": descriptor.command_digest,
        "arguments_digest": agent_cli._json_digest({"argv": argv}),
        "target_identifiers": descriptor.target_identifiers,
    }
    store.prepare_agent_effect_intents(
        run.id, (authorization,), owner="audit-owner",
    )
    monkeypatch.setenv(
        agent_cli.RECOVERY_WRITE_ALLOWLIST_ENV,
        json.dumps([authorization], sort_keys=True, separators=(",", ":")),
    )
    monkeypatch.setenv(
        agent_cli.EFFECT_INTENT_CONTEXT_ENV,
        json.dumps({"db_path": str(store.path), "run_id": run.id}),
    )
    monkeypatch.setattr(agent_cli.shutil, "which", lambda _: "/bin/dws")
    monkeypatch.setattr(
        agent_cli,
        "run_bounded_process",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, '{"messageId":"sent-1"}', ""
        ),
    )
    receipt = agent_cli.execute_reviewed_write_tool(
        argv, authorization_id="authorization-1",
    )
    assert receipt["authorization_id"] == "authorization-1"
    [persisted] = store.list_agent_execution_receipts(run.id)
    assert persisted.receipt_id == "authorization-1"
    assert persisted.safe_to_confirm is True
    with pytest.raises(ValueError, match="effect intent already dispatched"):
        agent_cli.execute_reviewed_write_tool(
            argv, authorization_id="authorization-1",
        )
