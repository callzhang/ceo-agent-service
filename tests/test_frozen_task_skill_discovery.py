from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from app.agent_cli import build_role_server
from app.store import AutoReplyStore
from tests.support.native_task_fields import set_native_task_fields
from app.agent_cron.commands import (
    ServiceCommandConsumerContext,
    ServiceCommandSkillMaterial,
)
from app.wechat.codex_safety import make_role_agent_command


def _task_db(path, *, scheduled_consumer: dict[str, object], generation="first"):
    """A fresh Store holding reply Task 1 whose trigger carries the frozen payload."""
    trigger = json.dumps(
        {"raw_payload": {"scheduled_consumer": scheduled_consumer}},
        ensure_ascii=False,
    )
    store = AutoReplyStore(path)
    assert store.enqueue_reply_task(
        conversation_id="scheduled-conversation",
        conversation_title="Scheduled task",
        single_chat=False,
        trigger_message_id="scheduled-message",
        trigger_create_time="",
        trigger_sender="",
        trigger_text="",
        trigger_message_json=trigger,
        execution_generation=generation,
    )
    assert [task.id for task in store.claim_reply_tasks(limit=1)] == [1]


def _context(*materials: ServiceCommandSkillMaterial) -> ServiceCommandConsumerContext:
    return ServiceCommandConsumerContext(
        scheduled_task_id=11,
        scheduled_task_run_id=29,
        prompt="Review the exact calendar invitation.",
        skill_names=("ceo-calendar-invite",),
        skill_protocol=(
            "Selected frozen Skills: `ceo-calendar-invite`. "
            "Call `agent_cli.read_task_skill(name)` before applying one."
        ),
        skill_materials=materials,
    )


def test_consumer_context_round_trips_frozen_materials_and_keeps_old_inline_text():
    frozen = ServiceCommandSkillMaterial(
        name="ceo-calendar-invite",
        content="## Managed Skill: ceo-calendar-invite\nrevision_id: 41\n"
        "sha256: frozen-sha\n\nORIGINAL BODY",
    )
    restored = ServiceCommandConsumerContext.from_payload(_context(frozen).to_payload())

    assert restored == _context(frozen)
    assert restored.to_payload()["skill_materials"] == [
        {"name": frozen.name, "content": frozen.content}
    ]

    legacy_custom = _context().to_payload()
    assert "skill_materials" not in legacy_custom
    legacy_custom["skill_protocol"] = "CUSTOM INLINE PROTOCOL SENTINEL"
    restored_custom = ServiceCommandConsumerContext.from_payload(legacy_custom)
    assert restored_custom.skill_materials == ()
    assert restored_custom.skill_protocol == "CUSTOM INLINE PROTOCOL SENTINEL"
    assert (
        restored_custom.materialized_skill_protocol()
        == "CUSTOM INLINE PROTOCOL SENTINEL"
    )
    assert restored.materialized_skill_protocol() == frozen.content


@pytest.mark.parametrize("role", ["consumer", "audit"])
def test_read_task_skill_returns_only_the_frozen_task_material(role, tmp_path):
    installed = tmp_path / "dingtalk-calendar" / "SKILL.md"
    installed.parent.mkdir()
    installed.write_text("ORIGINAL FROZEN BODY", encoding="utf-8")
    original = (
        "## Operation Skill: dingtalk-calendar\n"
        f"source: {installed}\n"
        "sha256: old-revision\n\nORIGINAL FROZEN BODY"
    )
    context = ServiceCommandConsumerContext(
        scheduled_task_id=11,
        scheduled_task_run_id=29,
        prompt="Review invitation",
        skill_names=("dingtalk-calendar",),
        skill_protocol="Read `dingtalk-calendar` with agent_cli.read_task_skill(name).",
        skill_materials=(
            ServiceCommandSkillMaterial(name="dingtalk-calendar", content=original),
        ),
    )
    db = tmp_path / "service.sqlite3"
    _task_db(db, scheduled_consumer=context.to_payload())
    installed.write_text("LATER INSTALLED BODY", encoding="utf-8")
    tool = build_role_server(
        role,
        task_id=1,
        db_path=db,
        execution_generation="first",
    )._tool_manager.get_tool("read_task_skill")

    assert tool is not None
    assert tool.fn("dingtalk-calendar") == {
        "name": "dingtalk-calendar",
        "content": original,
        "sha256": hashlib.sha256(original.encode("utf-8")).hexdigest(),
    }
    descriptor = {
        item.name: item for item in asyncio.run(build_role_server(role).list_tools())
    }["read_task_skill"]
    assert descriptor.annotations.readOnlyHint is True
    assert descriptor.annotations.destructiveHint is False


@pytest.mark.parametrize("role", ["consumer", "audit"])
def test_read_task_skill_native_response_keeps_one_complete_copy(role, tmp_path, monkeypatch):
    content = "FROZEN SKILL BODY\n" * 3000
    expected = {
        "name": "frozen-skill",
        "content": content,
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }
    monkeypatch.setattr("app.agent_cli._current_task_generation", lambda *_: "first")
    monkeypatch.setattr("app.agent_cli._read_bound_task_skill", lambda *_: expected)
    server = build_role_server(
        role,
        task_id=1,
        db_path=tmp_path / "unused.sqlite3",
        execution_generation="first",
    )

    descriptor = {tool.name: tool for tool in asyncio.run(server.list_tools())}["read_task_skill"]
    async def call_over_mcp():
        async with create_connected_server_and_client_session(server) as session:
            return await session.call_tool("read_task_skill", {"name": "frozen-skill"})

    response = asyncio.run(call_over_mcp())

    assert descriptor.outputSchema is None
    assert response.structuredContent is None
    assert len(response.content) == 1
    assert response.content[0].type == "text"
    assert json.loads(response.content[0].text) == expected


def test_read_task_skill_fails_for_missing_unknown_ambiguous_or_stale_material(
    tmp_path,
):
    db = tmp_path / "service.sqlite3"
    context = _context(
        ServiceCommandSkillMaterial(name="same", content="first"),
        ServiceCommandSkillMaterial(name="same", content="second"),
    )
    _task_db(db, scheduled_consumer=context.to_payload())
    server = build_role_server(
        "consumer", task_id=1, db_path=db, execution_generation="first"
    )
    read = server._tool_manager.get_tool("read_task_skill").fn

    with pytest.raises(ValueError, match="ambiguous"):
        read("same")
    with pytest.raises(ValueError, match="unknown"):
        read("missing")

    with sqlite3.connect(db) as connection:
        empty = _context().to_payload()
        set_native_task_fields(connection, "reply_tasks", 1, {
            "trigger_message_json": json.dumps({"raw_payload": {"scheduled_consumer": empty}}),
        })
    with pytest.raises(ValueError, match="unavailable"):
        read("ceo-calendar-invite")

    with sqlite3.connect(db) as connection:
        set_native_task_fields(connection, "reply_tasks", 1, {"execution_generation": "second"})
    with pytest.raises(ValueError, match="generation changed"):
        read("ceo-calendar-invite")


def test_read_task_skill_rejects_material_inside_invalid_service_wrapper(tmp_path):
    invalid = _context(
        ServiceCommandSkillMaterial(
            name="ceo-calendar-invite", content="MUST NOT BE SERVED"
        )
    ).to_payload()
    invalid["scheduled_task_id"] = "wrong-type"
    db = tmp_path / "service.sqlite3"
    _task_db(db, scheduled_consumer=invalid)
    read = build_role_server(
        "consumer", task_id=1, db_path=db, execution_generation="first"
    )._tool_manager.get_tool("read_task_skill").fn

    with pytest.raises(ValueError, match="scheduled consumer context is invalid"):
        read("ceo-calendar-invite")


@pytest.mark.parametrize("role", ["consumer", "audit"])
def test_role_command_replaces_selected_native_skill_config_with_empty_selection(
    monkeypatch, role,
):
    calls = []

    def frozen_only(names):
        calls.append(tuple(names))
        return 'skills.config=[{path="/service/selected/SKILL.md",enabled=false}]'

    monkeypatch.setattr(
        "app.wechat.codex_safety.codex_skill_config_override", frozen_only
    )
    command = [
        "codex",
        "exec",
        "-c",
        'skills.config=[{path="/service/selected/SKILL.md",enabled=true}]',
        "task",
    ]

    make_role_agent_command(
        command,
        role=role,
        task_workspace="/task/current" if role == "consumer" else None,
    )

    overrides = [
        command[index + 1]
        for index, item in enumerate(command[:-1])
        if item == "-c" and command[index + 1].startswith("skills.config=")
    ]
    assert calls == [()]
    assert overrides == [
        'skills.config=[{path="/service/selected/SKILL.md",enabled=false}]'
    ]
    enabled_tools = next(
        command[index + 1]
        for index, item in enumerate(command[:-1])
        if item == "-c"
        and command[index + 1].startswith("mcp_servers.agent_cli.enabled_tools=")
    )
    assert '"read_task_skill"' in enabled_tools


def test_role_prompt_carries_current_frozen_skill_catalog_without_body():
    material = ServiceCommandSkillMaterial(name="ceo-calendar-invite", content="FROZEN BODY")
    context = _context(material)
    assert context.role_prompt() == context.prompt + "\n\n" + context.skill_protocol
    assert "read_task_skill" in context.role_prompt()
    assert "FROZEN BODY" not in context.role_prompt()
    assert _context().role_prompt() == _context().prompt


def test_frozen_discovery_keeps_task_lookup_without_repeating_read_procedure():
    from app.business_skills import render_task_skill_discovery

    prompt = render_task_skill_discovery(
        ("ceo-calendar-invite",),
        catalog=(),
        frozen_names=("ceo-calendar-invite",),
    )
    assert "agent_cli.read_task_skill(name)" in prompt
    assert "Task-frozen." in prompt
    assert "agent_cli.read_skill(name=...)" in prompt
    assert "Reuse complete content already loaded in this session" not in prompt
    assert "Otherwise read the exact frozen content" not in prompt
    assert "before deciding or reviewing" not in prompt
