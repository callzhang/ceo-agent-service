from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from app.cli import build_parser
import app.managed_skills as managed_skills_module
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
    REPOSITORY_IMPORT_SOURCE,
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


LOCAL_PRODUCER_KEYS = frozenset(
    {
        "dingtalk-message-check-v1",
        "dingtalk-meeting-check-v1",
        "wechat-message-check-v1",
        "dingtalk-oa-check-v1",
        "work-source-scan-daily-v1",
        "weekly-okr-report-sunday-v1",
    }
)


@pytest.mark.parametrize("runtime_id", ("codex_oauth", "claude_api"))
def test_local_producer_seeds_require_local_runtime_surface(
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
        task for task in tasks if task.migration_key in LOCAL_PRODUCER_KEYS
    )

    assert len(producers) == 6
    assert all(task.enabled for task in producers)
    assert {task.runtime_id for task in producers} == {runtime_id}
    assert all(
        frozenset(task.required_runtime_capabilities)
        == LOCAL_SERVICE_RUNTIME_CAPABILITIES
        for task in producers
    )


def test_friday_only_keeps_local_producer_seeds_visible_and_disabled(
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
        task for task in tasks if task.migration_key in LOCAL_PRODUCER_KEYS
    )

    assert len(producers) == 6
    assert all(not task.enabled for task in producers)
    assert {task.runtime_id for task in producers} == {"friday_runtime"}
    assert all("missing_capabilities" in task.prompt for task in producers)


def test_reseeding_preserves_edits_but_disables_existing_friday_producer(
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
    original = _task_by_key(
        seed_scheduled_tasks(
            store=store, options=options, working_directory=tmp_path, now=NOW
        ),
        "dingtalk-message-check-v1",
    )
    edited = store.update_scheduled_task(
        original.id,
        expected_version=original.version,
        name="我的消息 producer",
        prompt="保留我的精确执行描述 $ceo-message-triage $dingtalk-chat",
        runtime_id="friday_runtime",
        runtime_options={"model": "default"},
        required_runtime_capabilities=(),
        now=NOW + timedelta(minutes=1),
    )

    repeated = _task_by_key(
        seed_scheduled_tasks(
            store=store,
            options=options,
            working_directory=tmp_path / "different",
            now=NOW + timedelta(minutes=2),
        ),
        "dingtalk-message-check-v1",
    )

    assert repeated.name == edited.name
    assert repeated.prompt == edited.prompt
    assert repeated.runtime_id == "friday_runtime"
    assert repeated.enabled is False
    assert repeated.version == edited.version + 1
    assert frozenset(repeated.required_runtime_capabilities) == (
        LOCAL_SERVICE_RUNTIME_CAPABILITIES
    )


def test_startup_seed_leaves_deleted_legacy_producer_exactly_unchanged(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "deleted-legacy-producer.sqlite3")
    options = _options(
        tmp_path,
        store,
        healthy_routes={"codex_oauth"},
        operation_skills=("dingtalk-chat",),
    )
    original = _task_by_key(
        seed_scheduled_tasks(
            store=store, options=options, working_directory=tmp_path, now=NOW
        ),
        "dingtalk-message-check-v1",
    )
    legacy = store.update_scheduled_task(
        original.id,
        expected_version=original.version,
        name="用户删除的旧消息任务",
        prompt="保留用户修改后的旧 Prompt",
        runtime_id="legacy-user-runtime",
        runtime_options={"model": "legacy-user-model"},
        required_runtime_capabilities=(),
        skill_refs=original.skill_refs,
        now=NOW + timedelta(minutes=1),
    )
    deleted = store.delete_scheduled_task(
        legacy.id,
        expected_version=legacy.version,
        now=NOW + timedelta(minutes=2),
    )
    before = store.list_scheduled_tasks(include_deleted=True)

    seeded = seed_scheduled_tasks(
        store=store,
        options=options,
        working_directory=tmp_path / "startup",
        now=NOW + timedelta(minutes=3),
    )

    after = store.list_scheduled_tasks(include_deleted=True)
    preserved = _task_by_key(after, "dingtalk-message-check-v1")
    assert len(after) == len(before)
    assert preserved == deleted
    assert preserved.deleted_at == NOW + timedelta(minutes=2)
    assert preserved.required_runtime_capabilities == ()
    assert preserved.name == legacy.name
    assert preserved.prompt == legacy.prompt
    assert preserved.runtime_id == legacy.runtime_id
    assert preserved.runtime_options == legacy.runtime_options
    assert preserved.skill_refs == legacy.skill_refs
    assert _task_by_key(seeded, "dingtalk-message-check-v1") == deleted
    assert all(
        task.migration_key != "dingtalk-message-check-v1"
        for task in store.list_scheduled_tasks()
    )


def test_startup_seed_backfills_active_legacy_producer_capabilities(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "active-legacy-producer.sqlite3")
    options = _options(
        tmp_path,
        store,
        healthy_routes={"codex_oauth"},
        operation_skills=("dingtalk-chat",),
    )
    original = _task_by_key(
        seed_scheduled_tasks(
            store=store, options=options, working_directory=tmp_path, now=NOW
        ),
        "dingtalk-message-check-v1",
    )
    legacy = store.update_scheduled_task(
        original.id,
        expected_version=original.version,
        name="用户保留的旧消息任务",
        required_runtime_capabilities=(),
        now=NOW + timedelta(minutes=1),
    )

    seeded = seed_scheduled_tasks(
        store=store,
        options=options,
        working_directory=tmp_path / "startup",
        now=NOW + timedelta(minutes=2),
    )

    updated = _task_by_key(seeded, "dingtalk-message-check-v1")
    assert updated.id == legacy.id
    assert updated.version == legacy.version + 1
    assert updated.name == legacy.name
    assert updated.deleted_at is None
    assert frozenset(updated.required_runtime_capabilities) == (
        LOCAL_SERVICE_RUNTIME_CAPABILITIES
    )


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
    assert task.runtime_id == "codex_oauth"
    assert task.enabled is True
    assert [(ref.skill_source, ref.skill_name) for ref in task.skill_refs] == [
        ("managed", "ceo-message-triage"),
        ("operation", "dingtalk-chat"),
    ]
    assert "$ceo-message-triage" in task.prompt
    assert "$dingtalk-chat" in task.prompt
    assert f"`{_cli_command(store, tmp_path, 'produce-once')}`" in task.prompt
    assert "只执行一次" in task.prompt
    assert "不要直接回复" in task.prompt


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
    assert [(ref.skill_source, ref.skill_name) for ref in task.skill_refs] == [
        ("managed", "ceo-meeting-work"),
        ("operation", "dingtalk-minutes"),
        ("operation", "dingtalk-calendar"),
    ]
    assert "ended_at + 10 minutes" in task.prompt
    assert f"`{_cli_command(store, tmp_path, 'scan-meetings-once')}`" in task.prompt
    assert "不要直接分析或发送" in task.prompt
    assert task.enabled is True


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
    assert [(ref.skill_source, ref.skill_name) for ref in task.skill_refs] == [
        ("managed", "ceo-wechat")
    ]
    assert "联系人" in task.prompt
    assert "群@" in task.prompt
    assert "auto-confirm" in task.prompt
    assert f"`{_wechat_cli_command(store)}`" in task.prompt
    assert "不要新增复盘或摘要" in task.prompt
    assert task.enabled is True


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
    assert [(ref.skill_source, ref.skill_name) for ref in task.skill_refs] == [
        ("operation", "dingtalk-oa-approval")
    ]
    assert "$dingtalk-oa-approval" in task.prompt
    assert f"`{_cli_command(store, tmp_path, 'scan-oa-approvals')}`" in task.prompt
    assert "不要直接审批或发送" in task.prompt
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
    assert [(ref.skill_source, ref.skill_name) for ref in task.skill_refs] == [
        ("managed", "ceo-work-tracking"),
        ("operation", "dingtalk-minutes"),
    ]
    assert f"`{_cli_command(store, tmp_path, 'scan-task-sources')}`" in task.prompt
    assert "本地工作目录中的增量文件" in task.prompt
    assert "不要直接修改工作对象" in task.prompt
    assert task.enabled is True


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
    expected = {
        "dingtalk-message-check-v1": "produce-once",
        "dingtalk-meeting-check-v1": "scan-meetings-once",
        "dingtalk-oa-check-v1": "scan-oa-approvals",
        "work-source-scan-daily-v1": "scan-task-sources",
        "weekly-okr-report-sunday-v1": "weekly-okr-report",
    }

    for migration_key, command_name in expected.items():
        prompt = _task_by_key(tasks, migration_key).prompt
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
    consumer = ScheduledTaskTriggerConsumer(
        store=store, option_service=options, now=lambda: due_at
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
    for task in tasks:
        runs = store.list_scheduled_task_runs(task.id)
        assert len(runs) == 1
        run = runs[0]
        dispatched = store.get_scheduled_task_run(run.id)
        assert dispatched is not None
        assert dispatched.dispatch_status == "dispatched"
        reply = store.get_reply_task(int(dispatched.execution_id))
        assert reply is not None
        assert reply.channel == "scheduled"
        assert reply.trigger_text == task.prompt
        assert task.name in reply.trigger_message_json


def test_seed_creates_daily_minutes_task_with_healthy_runtime_and_exact_revision(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "seed.sqlite3")
    options = _options(tmp_path, store, healthy_routes={"codex_oauth"})

    seeded = seed_scheduled_tasks(
        store=store,
        options=options,
        working_directory=tmp_path,
        now=NOW,
    )

    assert seeded == store.list_scheduled_tasks()
    assert len(seeded) == 7
    task = _task_by_key(seeded, "ceo-minutes-sync-daily-v1")
    assert task.migration_key == "ceo-minutes-sync-daily-v1"
    assert task.name == "每天同步 AI 听记"
    assert task.cron_expression == "0 0 20 * * *"
    assert task.timezone_name == "Asia/Shanghai"
    assert task.runtime_id == "codex_oauth"
    assert task.runtime_options == {"model": "gpt-5.6-sol"}
    assert task.working_directory == str(tmp_path.resolve())
    assert task.enabled is True
    assert len(task.skill_refs) == 1
    ref = task.skill_refs[0]
    assert ref.skill_source == "managed"
    assert ref.skill_name == "ceo-minutes-sync"
    revision = store.get_managed_skill_revision(ref.managed_revision_id)
    assert revision is not None
    assert revision.source == REPOSITORY_IMPORT_SOURCE


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


def test_existing_migration_task_is_not_rebound_after_repository_revision_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = AutoReplyStore(tmp_path / "existing-migration-revision.sqlite3")
    options = _options(tmp_path, store, healthy_routes={"codex_oauth"})
    original = _task_by_key(
        seed_scheduled_tasks(
            store=store, options=options, working_directory=tmp_path, now=NOW
        ),
        "ceo-minutes-sync-daily-v1",
    )
    original_ref = original.skill_refs[0]
    original_config = store.get_pending_or_active_runtime_skill_config()
    assert original_config is not None
    bindings_before = store.list_runtime_skill_bindings(original_config.id)
    skill = store.get_managed_skill_by_name("ceo-minutes-sync")
    assert skill is not None
    store.create_managed_skill_revision(
        skill.id,
        "---\nname: ceo-minutes-sync\ndescription: Use when testing settings.\n"
        "metadata:\n  managed_by: ceo-agent-service\n---\n\n# Settings\n",
        source="settings",
    )
    original_repository = managed_skills_module._repository_managed_skills
    changed_content = managed_skills_module.repository_managed_skill_content(
        "ceo-minutes-sync"
    ).replace("# CEO Minutes Sync", "# CEO Minutes Sync\n\nNew repository bytes", 1)
    monkeypatch.setattr(
        managed_skills_module,
        "_repository_managed_skills",
        lambda: tuple(
            (name, changed_content if name == "ceo-minutes-sync" else content)
            for name, content in original_repository()
        ),
    )
    import_repository_managed_skills(store)

    repeated = seed_scheduled_tasks(
        store=store,
        options=options,
        working_directory=tmp_path / "different",
        now=NOW + timedelta(minutes=1),
    )

    repeated_minutes = _task_by_key(repeated, "ceo-minutes-sync-daily-v1")
    assert repeated_minutes == original
    assert repeated_minutes.skill_refs == (original_ref,)
    assert store.get_pending_or_active_runtime_skill_config() == original_config
    assert store.list_runtime_skill_bindings(original_config.id) == bindings_before


def test_new_seed_binds_changed_repository_sha_after_settings_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = AutoReplyStore(tmp_path / "new-seed-current-repository.sqlite3")
    options = _options(tmp_path, store, healthy_routes={"codex_oauth"})
    skill = store.get_managed_skill_by_name("ceo-minutes-sync")
    assert skill is not None
    store.create_managed_skill_revision(
        skill.id,
        "---\nname: ceo-minutes-sync\ndescription: Use when testing settings.\n"
        "metadata:\n  managed_by: ceo-agent-service\n---\n\n# Settings\n",
        source="settings",
    )
    original_repository = managed_skills_module._repository_managed_skills
    changed_content = managed_skills_module.repository_managed_skill_content(
        "ceo-minutes-sync"
    ).replace("# CEO Minutes Sync", "# CEO Minutes Sync\n\nCurrent repository bytes", 1)
    changed_sha = hashlib.sha256(changed_content.encode("utf-8")).hexdigest()
    monkeypatch.setattr(
        managed_skills_module,
        "_repository_managed_skills",
        lambda: tuple(
            (name, changed_content if name == "ceo-minutes-sync" else content)
            for name, content in original_repository()
        ),
    )
    import_repository_managed_skills(store)

    task = _task_by_key(
        seed_scheduled_tasks(
            store=store, options=options, working_directory=tmp_path, now=NOW
        ),
        "ceo-minutes-sync-daily-v1",
    )

    revision = store.get_managed_skill_revision(
        task.skill_refs[0].managed_revision_id
    )
    assert revision is not None
    assert revision.sha256 == changed_sha
    assert revision.content == changed_content
    assert revision.source == REPOSITORY_IMPORT_SOURCE
    assert task.enabled is False
    assert "managed_revision_not_loaded" in task.prompt


def test_seed_without_healthy_runtime_is_disabled_with_visible_reason(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "disabled.sqlite3")
    options = _options(tmp_path, store, healthy_routes=set())

    task = _task_by_key(
        seed_scheduled_tasks(
            store=store, options=options, working_directory=tmp_path, now=NOW
        ),
        "ceo-minutes-sync-daily-v1",
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

    assert len(tasks) == 7
    assert all(not task.enabled for task in tasks)
    assert all("没有健康且已配置的 Runtime" in task.prompt for task in tasks)
    assert all(store.list_scheduled_task_runs(task.id) == () for task in tasks)


def test_seed_is_disabled_when_exact_repository_revision_is_not_loaded(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "revision-not-loaded.sqlite3")
    existing_skill = store.create_managed_skill("ceo-existing", "Existing Skill")
    existing_revision = store.create_managed_skill_revision(
        existing_skill.id,
        "---\nname: ceo-existing\ndescription: Existing\n"
        "metadata:\n  managed_by: ceo-agent-service\n---\n\n# Existing\n",
        source="settings",
    )
    existing_config = store.create_runtime_skill_config(
        {existing_skill.id: existing_revision.id}, expected_parent_id=None
    )
    import_repository_managed_skills(store)
    bindings_before = store.list_runtime_skill_bindings(existing_config.id)
    options = ScheduledTaskOptionService(
        store=store,
        environment={
            "CEO_AGENT_RUNTIME_ROUTES": "claude_api,codex_oauth",
            "CEO_CLAUDE_API_KEY": "test-secret",
            "CEO_CLAUDE_MODEL": "sonnet",
            "CEO_CODEX_MODEL": "gpt-5.6-sol",
        },
        runtime_snapshots={
            route: _snapshot(route, healthy=route == "codex_oauth")
            for route in ("claude_api", "codex_oauth")
        },
        operation_skill_files=SkillFileService(tmp_path / "operation-skills"),
        runtime_skill_snapshot=RuntimeSkillSnapshot(
            existing_config.id, (existing_revision,)
        ),
        now=lambda: NOW,
    )

    task = _task_by_key(
        seed_scheduled_tasks(
            store=store, options=options, working_directory=tmp_path, now=NOW
        ),
        "ceo-minutes-sync-daily-v1",
    )

    assert task.enabled is False
    assert task.runtime_id == "codex_oauth"
    assert "managed_revision_not_loaded" in task.prompt
    assert store.get_pending_or_active_runtime_skill_config() == existing_config
    assert store.list_runtime_skill_bindings(existing_config.id) == bindings_before
    ref = task.skill_refs[0]
    revision = store.get_managed_skill_revision(ref.managed_revision_id)
    assert revision is not None
    assert revision.source == REPOSITORY_IMPORT_SOURCE


def test_seed_binds_revision_matching_current_repository_sha_not_last_revision(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "current-repository-sha.sqlite3")
    options = _options(tmp_path, store, healthy_routes={"codex_oauth"})
    skill = store.get_managed_skill_by_name("ceo-minutes-sync")
    assert skill is not None
    current = store.list_managed_skill_revisions(skill.id)[0]
    later_different = store.create_managed_skill_revision(
        skill.id,
        current.content.replace("# CEO Minutes Sync", "# Old repository draft", 1),
        source=REPOSITORY_IMPORT_SOURCE,
    )

    task = _task_by_key(
        seed_scheduled_tasks(
            store=store, options=options, working_directory=tmp_path, now=NOW
        ),
        "ceo-minutes-sync-daily-v1",
    )

    assert task.skill_refs[0].managed_revision_id == current.id
    assert task.skill_refs[0].managed_revision_id != later_different.id
