from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3

import pytest

from app.agent_cli import build_role_server
from app.agent_cron.commands import (
    ServiceCommandConsumerContext,
    ServiceCommandSkillMaterial,
)
from app.wechat.codex_safety import make_role_agent_command


def _task_db(path, *, scheduled_consumer: dict[str, object], generation="first"):
    trigger = json.dumps(
        {"raw_payload": {"scheduled_consumer": scheduled_consumer}},
        ensure_ascii=False,
    )
    with sqlite3.connect(path) as db:
        db.execute(
            "create table reply_tasks ("
            "id integer primary key, execution_generation text not null, "
            "trigger_message_json text not null)"
        )
        db.execute("insert into reply_tasks values (7, ?, ?)", (generation, trigger))


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
        task_id=7,
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
        "consumer", task_id=7, db_path=db, execution_generation="first"
    )
    read = server._tool_manager.get_tool("read_task_skill").fn

    with pytest.raises(ValueError, match="ambiguous"):
        read("same")
    with pytest.raises(ValueError, match="unknown"):
        read("missing")

    with sqlite3.connect(db) as connection:
        empty = _context().to_payload()
        connection.execute(
            "update reply_tasks set trigger_message_json=? where id=7",
            (json.dumps({"raw_payload": {"scheduled_consumer": empty}}),),
        )
    with pytest.raises(ValueError, match="unavailable"):
        read("ceo-calendar-invite")

    with sqlite3.connect(db) as connection:
        connection.execute(
            "update reply_tasks set execution_generation='second' where id=7"
        )
    with pytest.raises(ValueError, match="generation changed"):
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
