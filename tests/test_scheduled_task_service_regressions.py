from __future__ import annotations

from pathlib import Path

from app.agent_cron.commands import SERVICE_COMMAND_OPTIONS
from app.agent_cron.options import ScheduledTaskOptionService
from app.agent_cron.seeds import seed_scheduled_tasks
from app.skill_files import SkillFileService
from app.store import AutoReplyStore


def _options(store: AutoReplyStore, root: Path) -> ScheduledTaskOptionService:
    return ScheduledTaskOptionService(
        store=store,
        environment={},
        runtime_snapshots={},
        operation_skill_files=SkillFileService(root / "operation-skills"),
        runtime_skill_snapshot=None,
    )


def test_minutes_sync_is_a_service_command_and_is_catalogued(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "scheduled.sqlite3")
    options = _options(store, tmp_path)

    tasks = seed_scheduled_tasks(
        store=store,
        options=options,
        working_directory=tmp_path,
    )
    minutes = next(
        task for task in tasks if task.migration_key == "ceo-minutes-sync-daily-v1"
    )

    assert minutes.command == "sync-minutes-once"
    assert minutes.prompt == ""
    assert minutes.runtime_id == ""
    assert minutes.skill_refs == ()
    assert any(option.name == "sync-minutes-once" for option in SERVICE_COMMAND_OPTIONS)


def test_minutes_agent_task_is_adopted_as_service_command(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "scheduled.sqlite3")
    existing = store.create_scheduled_task(
        migration_key="ceo-minutes-sync-daily-v1",
        name="我的听记同步",
        prompt="旧的 Agent 描述",
        command="",
        cron_expression="0 30 21 * * *",
        timezone_name="Asia/Shanghai",
        runtime_id="codex_oauth",
        runtime_options={"model": "gpt-5.6-sol"},
        working_directory=str(tmp_path),
        enabled=False,
    )
    options = _options(store, tmp_path)

    tasks = seed_scheduled_tasks(
        store=store,
        options=options,
        working_directory=tmp_path,
    )
    minutes = next(task for task in tasks if task.id == existing.id)

    assert minutes.command == "sync-minutes-once"
    assert minutes.prompt == ""
    assert minutes.runtime_id == ""
    assert minutes.runtime_options == {}
    assert minutes.skill_refs == ()
    assert minutes.name == existing.name
    assert minutes.cron_expression == existing.cron_expression
    assert minutes.enabled is True
