from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from app.cli import build_parser
from app.agent_cron.commands import (
    SERVICE_COMMAND_EXECUTION_KIND,
    ServiceCommandRegistry,
)
from app.agent_cron.models import ScheduledTaskSkillRef
from app.agent_cron.options import ScheduledTaskOptionService
from app.agent_cron.consumer import ScheduledTaskTriggerConsumer
from app.agent_cron.scheduler import AgentCronScheduler, ExecutionTerminalResolverRegistry
from app.agent_cron.seeds import seed_scheduled_tasks
from app.agent_runtime_contracts import (
    LOCAL_SERVICE_RUNTIME_CAPABILITIES,
    PROBE_VERIFIED_RUNTIME_CAPABILITIES,
    RuntimeCapabilitySnapshot,
)
from app.managed_skills import (
    RuntimeSkillSnapshot,
    import_repository_managed_skills,
)
from app.skill_files import SkillFileService
from app.dispatcher.adapters import ScheduledTaskQueueAdapter
from app.dispatcher.models import ClaimGuard
from app.store import AutoReplyStore


NOW = datetime(2026, 9, 8, 19, 0, tzinfo=UTC)


def _task_by_key(tasks: tuple, migration_key: str):
    return next(task for task in tasks if task.migration_key == migration_key)


def _cli_command(
    store: AutoReplyStore, working_directory: Path, command: str
) -> str:
    service_root = Path(__file__).resolve().parents[1]
    return (
        f"cd {shlex.quote(str(service_root))} && "
        f"{shlex.quote(str(Path(sys.executable).resolve()))} -m app.cli {command} "
        f"--db {shlex.quote(str(store.path.resolve()))} "
        f"--workspace {shlex.quote(str(working_directory.resolve()))}"
    )


def _wechat_cli_command(store: AutoReplyStore) -> str:
    service_root = Path(__file__).resolve().parents[1]
    return (
        f"cd {shlex.quote(str(service_root))} && "
        f"{shlex.quote(str(Path(sys.executable).resolve()))} -m app.wechat.cli "
        f"produce-once --db {shlex.quote(str(store.path.resolve()))}"
    )


def _snapshot(route_name: str, *, healthy: bool) -> RuntimeCapabilitySnapshot:
    return RuntimeCapabilitySnapshot(
        route_name=route_name,
        capabilities=(
            PROBE_VERIFIED_RUNTIME_CAPABILITIES
            | (
                frozenset()
                if route_name == "friday_runtime"
                else LOCAL_SERVICE_RUNTIME_CAPABILITIES
            )
        ),
        healthy=healthy,
        checked_at=NOW.isoformat(),
        expires_at=(NOW + timedelta(minutes=5)).isoformat(),
    )


def _options(
    tmp_path: Path,
    store: AutoReplyStore,
    *,
    healthy_routes: set[str],
    operation_skills: tuple[str, ...] = (),
    runtime_routes: tuple[str, ...] = ("claude_api", "codex_oauth"),
) -> ScheduledTaskOptionService:
    import_repository_managed_skills(store)
    skill = store.get_managed_skill_by_name("ceo-minutes-sync")
    assert skill is not None
    config = store.get_pending_or_active_runtime_skill_config()
    assert config is not None
    revisions = tuple(
        revision
        for binding in store.list_runtime_skill_bindings(config.id)
        if (revision := store.get_managed_skill_revision(binding.revision_id)) is not None
    )
    operation_root = tmp_path / "operation-skills"
    for name in operation_skills:
        path = operation_root / name / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\nname: {name}\ndescription: Test operation Skill {name}\n---\n\n# {name}\n",
            encoding="utf-8",
        )
    environment = {
        "CEO_AGENT_RUNTIME_ROUTES": ",".join(runtime_routes),
        "CEO_CLAUDE_API_KEY": "test-secret",
        "CEO_CLAUDE_MODEL": "sonnet",
        "CEO_CODEX_MODEL": "gpt-5.6-sol",
    }
    if "friday_runtime" in runtime_routes:
        environment.update(
            {
                "CEO_FRIDAY_RUNTIME_PROJECT_ID": "project-1",
                "CEO_FRIDAY_RUNTIME_AUTH_DISABLED": "1",
            }
        )
    return ScheduledTaskOptionService(
        store=store,
        environment=environment,
        runtime_snapshots={
            route: _snapshot(route, healthy=route in healthy_routes)
            for route in runtime_routes
        },
        operation_skill_files=SkillFileService(operation_root),
        runtime_skill_snapshot=RuntimeSkillSnapshot(config.id, revisions),
        now=lambda: NOW,
    )


FIXED_DISCOVERY_KEYS = frozenset(
    {
        "dingtalk-meeting-check-v1",
        "dingtalk-oa-check-v1",
        "work-source-scan-daily-v1",
    }
)


@pytest.mark.parametrize("runtime_id", ("codex_oauth", "claude_api"))
def test_fixed_discovery_seeds_do_not_require_an_agent_runtime(
    tmp_path: Path, runtime_id: str
) -> None:
    store = AutoReplyStore(tmp_path / f"local-{runtime_id}.sqlite3")
    options = _options(
        tmp_path,
        store,
        healthy_routes={runtime_id},
        runtime_routes=(runtime_id,),
        operation_skills=(
            "dingtalk-chat",
            "dingtalk-minutes",
            "dingtalk-calendar",
            "dingtalk-oa-approval",
            "ceo-weekly-report",
            "dingtang-okr-review",
        ),
    )

    tasks = seed_scheduled_tasks(
        store=store, options=options, working_directory=tmp_path, now=NOW
    )
    producers = tuple(
        task for task in tasks if task.migration_key in FIXED_DISCOVERY_KEYS
    )

    assert len(producers) == 3
    assert all(task.enabled for task in producers)
    assert all(task.command for task in producers)
    assert all(task.runtime_id == "" for task in producers)
    assert all(task.required_runtime_capabilities == () for task in producers)


def test_friday_only_still_enables_fixed_discovery_seeds(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "friday-only.sqlite3")
    options = _options(
        tmp_path,
        store,
        healthy_routes={"friday_runtime"},
        runtime_routes=("friday_runtime",),
        operation_skills=(
            "dingtalk-chat",
            "dingtalk-minutes",
            "dingtalk-calendar",
            "dingtalk-oa-approval",
            "ceo-weekly-report",
            "dingtang-okr-review",
        ),
    )

    tasks = seed_scheduled_tasks(
        store=store, options=options, working_directory=tmp_path, now=NOW
    )
    producers = tuple(
        task for task in tasks if task.migration_key in FIXED_DISCOVERY_KEYS
    )

    assert len(producers) == 3
    assert all(task.enabled for task in producers)
    assert all(task.command for task in producers)
    assert all(task.runtime_id == "" for task in producers)


def test_reseeding_preserves_user_edits_when_adopting_a_legacy_fixed_check(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "friday-reseed.sqlite3")
    operation_skills = (
        "dingtalk-chat",
        "dingtalk-minutes",
        "dingtalk-calendar",
        "dingtalk-oa-approval",
        "ceo-weekly-report",
        "dingtang-okr-review",
    )
    options = _options(
        tmp_path,
        store,
        healthy_routes={"codex_oauth", "friday_runtime"},
        runtime_routes=("codex_oauth", "friday_runtime"),
        operation_skills=operation_skills,
    )
    original = store.create_scheduled_task(
        migration_key="dingtalk-oa-check-v1",
        name="我的 OA producer",
        prompt="保留我的精确执行描述",
        cron_expression="0 0 * * * *",
        timezone_name="Asia/Shanghai",
        runtime_id="friday_runtime",
        runtime_options={"model": "default"},
        required_runtime_capabilities=(),
        working_directory=str(tmp_path),
        enabled=False,
        now=NOW,
    )
    original = store.set_scheduled_task_enabled(
        original.id, enabled=False, expected_version=original.version, now=NOW
    )

    repeated = _task_by_key(
        seed_scheduled_tasks(
            store=store,
            options=options,
            working_directory=tmp_path / "different",
            now=NOW + timedelta(minutes=2),
        ),
        "dingtalk-oa-check-v1",
    )

    assert repeated.name == original.name
    assert repeated.prompt == ""
    assert repeated.runtime_id == ""
    assert repeated.command == "scan-oa-approvals"
    assert repeated.enabled is False
    assert repeated.version == original.version + 1
    assert repeated.required_runtime_capabilities == ()


def test_startup_seed_leaves_deleted_legacy_fixed_check_unchanged(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "deleted-legacy-producer.sqlite3")
    options = _options(
        tmp_path,
        store,
        healthy_routes={"codex_oauth"},
        operation_skills=("dingtalk-oa-approval",),
    )
    original = store.create_scheduled_task(
        migration_key="dingtalk-oa-check-v1",
        name="用户删除的旧 OA 任务",
        prompt="保留用户修改后的旧 Prompt",
        cron_expression="0 0 * * * *",
        timezone_name="Asia/Shanghai",
        runtime_id="legacy-user-runtime",
        runtime_options={"model": "legacy-user-model"},
        required_runtime_capabilities=(),
        working_directory=str(tmp_path),
        enabled=True,
        now=NOW,
    )
    deleted = store.delete_scheduled_task(
        original.id,
        expected_version=original.version,
        now=NOW + timedelta(minutes=2),
    )
    seeded = seed_scheduled_tasks(
        store=store,
        options=options,
        working_directory=tmp_path / "startup",
        now=NOW + timedelta(minutes=3),
    )

    after = store.list_scheduled_tasks(include_deleted=True)
    preserved = _task_by_key(after, "dingtalk-oa-check-v1")
    assert preserved == deleted
    assert preserved.deleted_at == NOW + timedelta(minutes=2)
    assert preserved.required_runtime_capabilities == ()
    assert preserved.name == original.name
    assert preserved.prompt == original.prompt
    assert preserved.runtime_id == original.runtime_id
    assert preserved.runtime_options == original.runtime_options
    assert preserved.skill_refs == original.skill_refs
    assert _task_by_key(seeded, "dingtalk-oa-check-v1") == deleted
    assert all(
        task.migration_key != "dingtalk-oa-check-v1"
        for task in store.list_scheduled_tasks()
    )


def test_startup_seed_adopts_active_legacy_fixed_check(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "active-legacy-producer.sqlite3")
    options = _options(
        tmp_path,
        store,
        healthy_routes={"codex_oauth"},
        operation_skills=("dingtalk-oa-approval",),
    )
    original = store.create_scheduled_task(
        migration_key="dingtalk-oa-check-v1",
        name="用户保留的旧 OA 任务",
        prompt="旧 Prompt",
        cron_expression="0 0 * * * *",
        timezone_name="Asia/Shanghai",
        runtime_id="codex_oauth",
        runtime_options={"model": "gpt-5.6-sol"},
        required_runtime_capabilities=(),
        working_directory=str(tmp_path),
        enabled=True,
        now=NOW,
    )

    seeded = seed_scheduled_tasks(
        store=store,
        options=options,
        working_directory=tmp_path / "startup",
        now=NOW + timedelta(minutes=2),
    )

    updated = _task_by_key(seeded, "dingtalk-oa-check-v1")
    assert updated.id == original.id
    assert updated.version == original.version + 1
    assert updated.name == original.name
    assert updated.deleted_at is None
    assert updated.command == "scan-oa-approvals"
    assert updated.required_runtime_capabilities == ()


def test_seed_creates_dingtalk_message_check_every_minute(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "dingtalk-message.sqlite3")
    options = _options(
        tmp_path,
        store,
        healthy_routes={"codex_oauth"},
        operation_skills=("dingtalk-chat",),
    )

    tasks = seed_scheduled_tasks(
        store=store, options=options, working_directory=tmp_path, now=NOW
    )

    task = next(
        item for item in tasks if item.migration_key == "dingtalk-message-check-v1"
    )
    assert task.name == "检查 DingTalk 消息"
    assert task.cron_expression == "0 * * * * *"
    assert task.timezone_name == "Asia/Shanghai"
    assert task.command == "produce-once"
    assert task.enabled is True
    assert task.prompt == "" and task.runtime_id == "" and task.skill_refs == ()
    assert task.runtime_options == {} and task.required_runtime_capabilities == ()
    assert task.working_directory == ""


def _legacy_message_agent_task(
    store, options, tmp_path, *, migration_key="dingtalk-message-check-v1", enabled=True
):
    dingtalk_chat = next(
        item for item in options.list_operation_skill_options()
        if item.name == "dingtalk-chat"
    )
    assert dingtalk_chat.available
    return store.create_scheduled_task(
        migration_key=migration_key,
        name="用户改名的消息检查",
        prompt=(
            "使用 $dingtalk-chat 理解现有消息发现边界。只执行一次确定性 producer 命令："
            f"`{_cli_command(store, tmp_path, 'produce-once')}`。"
        ),
        cron_expression="0 */2 * * * *",
        timezone_name="Asia/Shanghai",
        runtime_id="codex_oauth",
        runtime_options={"model": "gpt-5.6-sol"},
        required_runtime_capabilities=sorted(LOCAL_SERVICE_RUNTIME_CAPABILITIES),
        working_directory=str(tmp_path),
        skill_refs=(
            ScheduledTaskSkillRef(
                skill_source="operation", skill_name="dingtalk-chat", position=0
            ),
        ),
        enabled=enabled,
        now=NOW,
    )


def test_startup_seed_moves_legacy_agent_message_check_to_the_service_command(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "legacy-message-agent.sqlite3")
    options = _options(
        tmp_path, store, healthy_routes={"codex_oauth"}, operation_skills=("dingtalk-chat",)
    )
    legacy = _legacy_message_agent_task(store, options, tmp_path, enabled=False)

    seeded = _task_by_key(
        seed_scheduled_tasks(
            store=store, options=options, working_directory=tmp_path,
            now=NOW + timedelta(minutes=1),
        ),
        "dingtalk-message-check-v1",
    )

    assert seeded.id == legacy.id
    assert seeded.version == legacy.version + 1
    assert seeded.command == "produce-once"
    assert seeded.prompt == "" and seeded.runtime_id == "" and seeded.skill_refs == ()
    assert seeded.required_runtime_capabilities == ()
    assert seeded.working_directory == ""
    assert seeded.name == legacy.name
    assert seeded.cron_expression == "0 */2 * * * *"
    # Nobody edited the legacy seed, so its disabled state was the Agent form's
    # own availability decision and the command form starts enabled.
    assert seeded.enabled is True
    assert store.list_scheduled_tasks(include_deleted=True).count(seeded) == 1
    again = _task_by_key(
        seed_scheduled_tasks(
            store=store, options=options, working_directory=tmp_path,
            now=NOW + timedelta(minutes=2),
        ),
        "dingtalk-message-check-v1",
    )
    assert again == seeded


def test_startup_seed_leaves_deleted_legacy_message_check_unchanged(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "deleted-message-agent.sqlite3")
    options = _options(
        tmp_path, store, healthy_routes={"codex_oauth"}, operation_skills=("dingtalk-chat",)
    )
    legacy = _legacy_message_agent_task(store, options, tmp_path)
    deleted = store.delete_scheduled_task(
        legacy.id, expected_version=legacy.version, now=NOW + timedelta(minutes=1)
    )

    seeded = _task_by_key(
        seed_scheduled_tasks(
            store=store, options=options, working_directory=tmp_path,
            now=NOW + timedelta(minutes=2),
        ),
        "dingtalk-message-check-v1",
    )

    assert seeded == deleted
    assert seeded.command == "" and seeded.prompt == legacy.prompt
    assert all(
        task.migration_key != "dingtalk-message-check-v1"
        for task in store.list_scheduled_tasks()
    )


def test_seed_creates_meeting_check_with_fixed_ten_minute_eligibility(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "meeting.sqlite3")
    options = _options(
        tmp_path,
        store,
        healthy_routes={"codex_oauth"},
        operation_skills=("dingtalk-minutes", "dingtalk-calendar"),
    )

    task = next(
        item
        for item in seed_scheduled_tasks(
            store=store, options=options, working_directory=tmp_path, now=NOW
        )
        if item.migration_key == "dingtalk-meeting-check-v1"
    )

    assert task.name == "检查 DingTalk 会议"
    assert task.cron_expression == "0 * * * * *"
    assert task.command == "scan-meetings-once"
    assert task.prompt == ""
    assert task.runtime_id == ""
    assert task.runtime_options == {}
    assert task.required_runtime_capabilities == ()
    assert task.working_directory == ""
    assert task.skill_refs == ()
    assert task.enabled is True


def test_every_fixed_discovery_check_is_a_service_command(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "fixed-checks.sqlite3")
    options = _options(
        tmp_path,
        store,
        healthy_routes=set(),
        operation_skills=("dingtalk-minutes", "dingtalk-calendar", "dingtalk-oa-approval"),
    )

    tasks = seed_scheduled_tasks(
        store=store,
        options=options,
        working_directory=tmp_path,
        now=NOW,
    )

    expected_commands = {
        "dingtalk-message-check-v1": "produce-once",
        "dingtalk-message-recovery-v1": "recover-recent-messages",
        "dingtalk-meeting-check-v1": "scan-meetings-once",
        "wechat-message-check-v1": "wechat-produce-once",
        "dingtalk-oa-check-v1": "scan-oa-approvals",
        "work-source-scan-daily-v1": "scan-work-sources-once",
    }
    for migration_key, command in expected_commands.items():
        task = _task_by_key(tasks, migration_key)
        assert task.command == command
        assert task.prompt == ""
        assert task.runtime_id == ""
        assert task.runtime_options == {}
        assert task.required_runtime_capabilities == ()
        assert task.working_directory == ""
        assert task.skill_refs == ()
        assert task.enabled is True

    # The OKR weekly report is the one remaining Agent task: every fixed
    # discovery check, the AI minutes sync included, is a service command.
    for migration_key in ("weekly-okr-report-sunday-v1",):
        agent_task = _task_by_key(tasks, migration_key)
        assert agent_task.command == ""
        assert agent_task.prompt
        assert agent_task.runtime_id == "claude_api"
        assert agent_task.enabled is False
    # The AI minutes sync is a service command now, so it carries no Skill ref:
    # the sync it used to describe lives in app/minutes_sync.py.
    minutes = _task_by_key(tasks, "ceo-minutes-sync-daily-v1")
    assert minutes.command == "sync-minutes-once"
    assert minutes.skill_refs == ()


def test_seed_creates_wechat_existing_producer_every_fifteen_seconds(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "wechat.sqlite3")
    options = _options(tmp_path, store, healthy_routes={"codex_oauth"})

    task = next(
        item
        for item in seed_scheduled_tasks(
            store=store, options=options, working_directory=tmp_path, now=NOW
        )
        if item.migration_key == "wechat-message-check-v1"
    )

    assert task.name == "检查微信消息"
    assert task.cron_expression == "*/15 * * * * *"
    assert task.timezone_name == "Asia/Shanghai"
    assert task.command == "wechat-produce-once"
    assert task.enabled is True
    assert task.prompt == "" and task.runtime_id == "" and task.skill_refs == ()
    assert task.runtime_options == {} and task.required_runtime_capabilities == ()


def test_startup_seed_enables_untouched_legacy_wechat_agent_task_as_a_command(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "legacy-wechat-agent.sqlite3")
    options = _options(
        tmp_path, store, healthy_routes={"codex_oauth"}, operation_skills=("dingtalk-chat",)
    )
    untouched = _legacy_message_agent_task(
        store, options, tmp_path, migration_key="wechat-message-check-v1", enabled=False
    )
    edited_seed = _legacy_message_agent_task(
        store, options, tmp_path, migration_key="dingtalk-message-check-v1", enabled=True
    )
    edited = store.set_scheduled_task_enabled(
        edited_seed.id, enabled=False, expected_version=edited_seed.version, now=NOW
    )

    seeded = seed_scheduled_tasks(
        store=store, options=options, working_directory=tmp_path,
        now=NOW + timedelta(minutes=1),
    )

    wechat = _task_by_key(seeded, "wechat-message-check-v1")
    assert wechat.id == untouched.id and untouched.version == 1
    assert wechat.command == "wechat-produce-once"
    assert wechat.enabled is True
    assert wechat.cron_expression == untouched.cron_expression
    message = _task_by_key(seeded, "dingtalk-message-check-v1")
    assert message.id == edited.id and message.command == "produce-once"
    assert message.enabled is False


def test_seed_creates_hourly_oa_check_with_real_operation_skill(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "oa.sqlite3")
    options = _options(
        tmp_path,
        store,
        healthy_routes={"codex_oauth"},
        operation_skills=("dingtalk-oa-approval",),
    )

    task = next(
        item
        for item in seed_scheduled_tasks(
            store=store, options=options, working_directory=tmp_path, now=NOW
        )
        if item.migration_key == "dingtalk-oa-check-v1"
    )

    assert task.name == "检查 DingTalk OA 审批"
    assert task.cron_expression == "0 0 * * * *"
    assert task.command == "scan-oa-approvals"
    assert task.prompt == ""
    assert task.runtime_id == ""
    assert task.runtime_options == {}
    assert task.required_runtime_capabilities == ()
    assert task.working_directory == ""
    assert task.skill_refs == ()
    assert task.enabled is True


def test_seed_creates_daily_work_source_scan(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "work-source.sqlite3")
    options = _options(
        tmp_path,
        store,
        healthy_routes={"codex_oauth"},
        operation_skills=("dingtalk-minutes",),
    )

    task = next(
        item
        for item in seed_scheduled_tasks(
            store=store, options=options, working_directory=tmp_path, now=NOW
        )
        if item.migration_key == "work-source-scan-daily-v1"
    )

    assert task.name == "每天扫描工作来源"
    assert task.cron_expression == "0 0 0 * * *"
    assert task.command == "scan-work-sources-once"
    assert task.prompt == ""
    assert task.runtime_id == ""
    assert task.runtime_options == {}
    assert task.required_runtime_capabilities == ()
    assert task.working_directory == ""
    assert task.skill_refs == ()
    assert task.enabled is True


def test_seed_creates_hourly_recent_message_recovery_at_half_past(
    tmp_path: Path,
) -> None:
    """The recovery runs on its own hourly schedule, offset from the OA check.

    The whole point of splitting it out of the every-minute produce-once pass
    is that it runs on a schedule of its own; nothing else pins that schedule,
    so a change to the cron would otherwise go unnoticed.
    """
    store = AutoReplyStore(tmp_path / "recovery.sqlite3")
    options = _options(tmp_path, store, healthy_routes={"codex_oauth"})

    task = _task_by_key(
        seed_scheduled_tasks(
            store=store, options=options, working_directory=tmp_path, now=NOW
        ),
        "dingtalk-message-recovery-v1",
    )

    assert task.name == "恢复近期 DingTalk 消息"
    assert task.command == "recover-recent-messages"
    assert task.cron_expression == "0 30 * * * *"
    assert task.timezone_name == "Asia/Shanghai"
    assert task.enabled is True
    # A service command task carries no Agent configuration.
    assert task.prompt == "" and task.runtime_id == "" and task.skill_refs == ()


def test_seed_creates_sunday_evening_weekly_okr_task(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "weekly.sqlite3")
    options = _options(
        tmp_path,
        store,
        healthy_routes={"codex_oauth"},
        operation_skills=("ceo-weekly-report", "dingtang-okr-review"),
    )

    task = next(
        item
        for item in seed_scheduled_tasks(
            store=store, options=options, working_directory=tmp_path, now=NOW
        )
        if item.migration_key == "weekly-okr-report-sunday-v1"
    )

    assert task.name == "周日生成 OKR 周报"
    assert task.cron_expression == "0 0 18 * * 0"
    assert [(ref.skill_source, ref.skill_name) for ref in task.skill_refs] == [
        ("operation", "ceo-weekly-report"),
        ("operation", "dingtang-okr-review"),
    ]
    assert "--force" in task.prompt
    assert (
        f"`{_cli_command(store, tmp_path, 'weekly-okr-report --force')}`"
        in task.prompt
    )
    assert "命令自身完成分析、发布和发送" in task.prompt
    assert "不要在命令外重复" in task.prompt
    assert task.enabled is True


def test_proactive_seeds_do_not_include_lark_default(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "no-lark.sqlite3")
    options = _options(tmp_path, store, healthy_routes={"codex_oauth"})

    tasks = seed_scheduled_tasks(
        store=store, options=options, working_directory=tmp_path, now=NOW
    )

    assert all("lark" not in (task.migration_key or "").lower() for task in tasks)
    assert all(store.list_scheduled_task_runs(task.id) == () for task in tasks)


def test_non_wechat_seed_commands_are_registered_one_shot_cli_entries(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "commands.sqlite3")
    options = _options(
        tmp_path,
        store,
        healthy_routes={"codex_oauth"},
        operation_skills=(
            "dingtalk-chat",
            "dingtalk-minutes",
            "dingtalk-calendar",
            "dingtalk-oa-approval",
            "ceo-weekly-report",
            "dingtang-okr-review",
        ),
    )
    tasks = seed_scheduled_tasks(
        store=store, options=options, working_directory=tmp_path, now=NOW
    )
    message_check = _task_by_key(tasks, "dingtalk-message-check-v1")
    assert message_check.command == "produce-once"
    assert build_parser().parse_args(
        ["produce-once", "--db", str(store.path), "--workspace", str(tmp_path)]
    ).command == "produce-once"
    expected = {
        "dingtalk-message-recovery-v1": "recover-recent-messages",
        "dingtalk-meeting-check-v1": "scan-meetings-once",
        "dingtalk-oa-check-v1": "scan-oa-approvals",
        "work-source-scan-daily-v1": "scan-work-sources-once",
        "weekly-okr-report-sunday-v1": "weekly-okr-report",
    }

    for migration_key, command_name in expected.items():
        task = _task_by_key(tasks, migration_key)
        if task.command:
            assert task.command == command_name
            continue
        prompt = task.prompt
        command = prompt.split("`", 2)[1]
        argv = shlex.split(command)
        assert "--dry-run" not in argv
        assert "--not-send-message" not in argv
        assert argv[:3] == ["cd", str(Path(__file__).resolve().parents[1]), "&&"]
        assert argv[3:6] == [str(Path(sys.executable).resolve()), "-m", "app.cli"]
        parsed = build_parser().parse_args(argv[6:])
        assert parsed.command == command_name
        assert Path(parsed.db) == store.path.resolve()
        assert Path(parsed.workspace) == tmp_path.resolve()


def test_seeded_python_entrypoint_is_executable_from_business_workspace(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "commands.sqlite3")
    for command, expected_usage in (
        (_cli_command(store, tmp_path, "produce-once"), "usage: ceo-agent"),
        (_wechat_cli_command(store), "usage:"),
    ):
        prefix, separator, _business_args = command.partition(" produce-once ")
        assert separator
        probe = subprocess.run(
            f"{prefix} --help",
            cwd=tmp_path,
            env={"PATH": "/usr/bin:/bin"},
            shell=True,
            text=True,
            capture_output=True,
            check=False,
        )

        assert probe.returncode == 0, probe.stderr
        assert expected_usage in probe.stdout


def test_proactive_cron_triggers_create_snapshotted_business_inputs(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "dispatch.sqlite3")
    options = _options(
        tmp_path,
        store,
        healthy_routes={"codex_oauth"},
        operation_skills=(
            "dingtalk-chat",
            "dingtalk-minutes",
            "dingtalk-calendar",
            "dingtalk-oa-approval",
            "ceo-weekly-report",
            "dingtang-okr-review",
        ),
    )
    tasks = seed_scheduled_tasks(
        store=store, options=options, working_directory=tmp_path, now=NOW
    )
    assert all(task.enabled for task in tasks)
    scheduler = AgentCronScheduler(
        store=store,
        option_service=options,
        terminal_resolver=ExecutionTerminalResolverRegistry({}),
    )
    scheduler.start(NOW)
    due_at = NOW + timedelta(days=8)

    assert scheduler.tick(due_at) == len(tasks)
    adapter = ScheduledTaskQueueAdapter(store, owner_alive=lambda _pid: False)
    produced: list[str] = []
    consumer = ScheduledTaskTriggerConsumer(
        store=store, option_service=options, now=lambda: due_at,
        commands=ServiceCommandRegistry(
            {
                "produce-once": lambda: produced.append("produce-once") or "queued=0",
                "recover-recent-messages": (
                    lambda: produced.append("recover-recent-messages") or "queued=0"
                ),
                "wechat-produce-once": (
                    lambda: produced.append("wechat-produce-once") or "queued=0"
                ),
                "scan-meetings-once": (
                    lambda: produced.append("scan-meetings-once") or "queued=0"
                ),
                "scan-oa-approvals": (
                    lambda: produced.append("scan-oa-approvals") or "queued=0"
                ),
                "scan-work-sources-once": (
                    lambda: produced.append("scan-work-sources-once") or "queued=0"
                ),
                "sync-minutes-once": (
                    lambda: produced.append("sync-minutes-once") or "queued=0"
                ),
            }
        ),
    )
    for _task in tasks:
        envelope = adapter.claim(
            due_at,
            owner="seed-dispatch",
            owner_pid=42,
            lease=timedelta(minutes=5),
        )
        assert envelope is not None
        guard = ClaimGuard(
            adapter=adapter, envelope=envelope, owner="seed-dispatch"
        )
        consumer(envelope, guard)
    assert sorted(produced) == [
        "produce-once",
        "recover-recent-messages",
        "scan-meetings-once",
        "scan-oa-approvals",
        "scan-work-sources-once",
        "sync-minutes-once",
        "wechat-produce-once",
    ]
    for task in tasks:
        runs = store.list_scheduled_task_runs(task.id)
        assert len(runs) == 1
        run = runs[0]
        dispatched = store.get_scheduled_task_run(run.id)
        assert dispatched is not None
        assert dispatched.dispatch_status == "dispatched"
        if task.command:
            assert dispatched.execution_kind == SERVICE_COMMAND_EXECUTION_KIND
            assert dispatched.execution_id == task.command
            continue
        reply = store.get_reply_task(int(dispatched.execution_id))
        assert reply is not None
        assert reply.channel == "scheduled"
        assert reply.trigger_text == task.prompt
        assert task.name in reply.trigger_message_json
    assert len(store.list_reply_tasks(channel="scheduled")) == len(tasks) - 7


def test_seed_is_idempotent_and_preserves_user_edits(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "idempotent.sqlite3")
    options = _options(tmp_path, store, healthy_routes={"codex_oauth"})
    original = _task_by_key(
        seed_scheduled_tasks(
            store=store, options=options, working_directory=tmp_path, now=NOW
        ),
        "ceo-minutes-sync-daily-v1",
    )
    edited = store.update_scheduled_task(
        original.id,
        expected_version=original.version,
        name="我修改后的听记同步",
        cron_expression="0 30 21 * * *",
        now=NOW + timedelta(minutes=1),
    )

    repeated = seed_scheduled_tasks(
        store=store,
        options=options,
        working_directory=tmp_path / "different",
        now=NOW + timedelta(minutes=2),
    )

    assert _task_by_key(repeated, "ceo-minutes-sync-daily-v1") == edited
    assert _task_by_key(
        store.list_scheduled_tasks(), "ceo-minutes-sync-daily-v1"
    ) == edited


def test_seed_without_healthy_runtime_is_disabled_with_visible_reason(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "disabled.sqlite3")
    options = _options(tmp_path, store, healthy_routes=set())

    task = _task_by_key(
        seed_scheduled_tasks(
            store=store, options=options, working_directory=tmp_path, now=NOW
        ),
        "weekly-okr-report-sunday-v1",
    )

    assert task.enabled is False
    assert task.runtime_id == "claude_api"
    assert "未启用" in task.prompt
    assert "没有健康且已配置的 Runtime" in task.prompt
    assert "snapshot_unhealthy" in task.prompt
    assert task.runtime_id in {
        option.route_name for option in options.list_runtime_options()
    }
    assert store.list_scheduled_task_runs(task.id) == ()


def test_all_proactive_seeds_stay_visible_and_disabled_without_healthy_runtime(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "all-disabled.sqlite3")
    options = _options(tmp_path, store, healthy_routes=set())

    tasks = seed_scheduled_tasks(
        store=store, options=options, working_directory=tmp_path, now=NOW
    )

    assert len(tasks) == 8
    agent_tasks = [task for task in tasks if not task.command]
    assert {task.migration_key for task in agent_tasks} == {
        "weekly-okr-report-sunday-v1",
    }
    assert all(not task.enabled for task in agent_tasks)
    assert all("没有健康且已配置的 Runtime" in task.prompt for task in agent_tasks)
    assert all(task.enabled for task in tasks if task.command)
    assert all(store.list_scheduled_task_runs(task.id) == () for task in tasks)

