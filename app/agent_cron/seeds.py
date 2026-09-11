from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shlex
import sys

from app.agent_cron.models import ScheduledTask, ScheduledTaskSkillRef
from app.agent_cron.options import RuntimeOption, ScheduledTaskOptionService
from app.agent_runtime_contracts import LOCAL_SERVICE_RUNTIME_CAPABILITIES
from app.store import AutoReplyStore


MINUTES_SYNC_MIGRATION_KEY = "ceo-minutes-sync-daily-v1"
DINGTALK_MESSAGE_MIGRATION_KEY = "dingtalk-message-check-v1"
DINGTALK_MESSAGE_SERVICE_COMMAND = "produce-once"
DINGTALK_MESSAGE_RECOVERY_MIGRATION_KEY = "dingtalk-message-recovery-v1"
DINGTALK_MESSAGE_RECOVERY_SERVICE_COMMAND = "recover-recent-messages"
WECHAT_MESSAGE_MIGRATION_KEY = "wechat-message-check-v1"
WECHAT_MESSAGE_SERVICE_COMMAND = "wechat-produce-once"
DINGTALK_MEETING_MIGRATION_KEY = "dingtalk-meeting-check-v1"
DINGTALK_MEETING_SERVICE_COMMAND = "scan-meetings-once"
DINGTALK_OA_MIGRATION_KEY = "dingtalk-oa-check-v1"
DINGTALK_OA_SERVICE_COMMAND = "scan-oa-approvals"
WORK_SOURCE_MIGRATION_KEY = "work-source-scan-daily-v1"
WORK_SOURCE_SERVICE_COMMAND = "scan-work-sources-once"
PRODUCER_RUNTIME_CAPABILITIES = LOCAL_SERVICE_RUNTIME_CAPABILITIES


def _one_shot_command(
    store: AutoReplyStore,
    working_directory: Path,
    command: str,
    *,
    module: str = "app.cli",
    include_workspace: bool = True,
) -> str:
    service_root = shlex.quote(str(Path(__file__).resolve().parents[2]))
    python = shlex.quote(str(Path(sys.executable).resolve()))
    database_path = shlex.quote(str(store.path.expanduser().resolve()))
    workspace_path = shlex.quote(str(working_directory.expanduser().resolve()))
    invocation = (
        f"cd {service_root} && {python} -m {module} {command} --db {database_path}"
    )
    return (
        f"{invocation} --workspace {workspace_path}"
        if include_workspace
        else invocation
    )


def seed_scheduled_tasks(
    *,
    store: AutoReplyStore,
    options: ScheduledTaskOptionService,
    working_directory: Path,
    now: datetime | None = None,
) -> tuple[ScheduledTask, ...]:
    """Create repository-owned scheduled task defaults without overwriting edits."""
    dingtalk_message = _seed_dingtalk_message_task(
        store=store,
        options=options,
        working_directory=working_directory,
        now=now,
    )
    dingtalk_message_recovery = _seed_dingtalk_message_recovery_task(
        store=store,
        options=options,
        working_directory=working_directory,
        now=now,
    )
    dingtalk_meeting = _seed_dingtalk_meeting_task(
        store=store,
        options=options,
        working_directory=working_directory,
        now=now,
    )
    wechat = _seed_wechat_task(
        store=store,
        options=options,
        working_directory=working_directory,
        now=now,
    )
    oa = _seed_oa_task(
        store=store,
        options=options,
        working_directory=working_directory,
        now=now,
    )
    work_sources = _seed_work_source_task(
        store=store,
        options=options,
        working_directory=working_directory,
        now=now,
    )
    weekly_okr = _seed_weekly_okr_task(
        store=store,
        options=options,
        working_directory=working_directory,
        now=now,
    )
    minutes = _seed_minutes_task(
        store=store,
        options=options,
        working_directory=working_directory,
        now=now,
    )
    return (
        dingtalk_message,
        dingtalk_message_recovery,
        dingtalk_meeting,
        wechat,
        oa,
        work_sources,
        weekly_okr,
        minutes,
    )


def _seed_dingtalk_message_task(
    *,
    store: AutoReplyStore,
    options: ScheduledTaskOptionService,
    working_directory: Path,
    now: datetime | None,
) -> ScheduledTask:
    """Seed the DingTalk message check as a service command, not an Agent task.

    The check is one deterministic producer pass, so it runs in-process without
    a runtime or Skills.  A task seeded earlier in the Agent form is moved to
    the command form in place; its name, Cron, timezone, and enabled state are
    kept.
    """
    del options, working_directory
    adopted = store.adopt_scheduled_task_service_command(
        migration_key=DINGTALK_MESSAGE_MIGRATION_KEY,
        command=DINGTALK_MESSAGE_SERVICE_COMMAND,
        seed_enabled=True,
        now=now,
    )
    if adopted is not None:
        return adopted
    return store.create_scheduled_task(
        migration_key=DINGTALK_MESSAGE_MIGRATION_KEY,
        name="检查 DingTalk 消息",
        command=DINGTALK_MESSAGE_SERVICE_COMMAND,
        cron_expression="0 * * * * *",
        timezone_name="Asia/Shanghai",
        enabled=True,
        now=now,
    )


def _seed_dingtalk_message_recovery_task(
    *,
    store: AutoReplyStore,
    options: ScheduledTaskOptionService,
    working_directory: Path,
    now: datetime | None,
) -> ScheduledTask:
    """Seed the widened DingTalk read as its own hourly service command.

    The recovery pass reads far more than the minute-by-minute check, so it
    runs on its own Cron at :30 instead of inside the :00 checks.
    """
    del options, working_directory
    adopted = store.adopt_scheduled_task_service_command(
        migration_key=DINGTALK_MESSAGE_RECOVERY_MIGRATION_KEY,
        command=DINGTALK_MESSAGE_RECOVERY_SERVICE_COMMAND,
        seed_enabled=True,
        now=now,
    )
    if adopted is not None:
        return adopted
    return store.create_scheduled_task(
        migration_key=DINGTALK_MESSAGE_RECOVERY_MIGRATION_KEY,
        name="恢复近期 DingTalk 消息",
        command=DINGTALK_MESSAGE_RECOVERY_SERVICE_COMMAND,
        cron_expression="0 30 * * * *",
        timezone_name="Asia/Shanghai",
        enabled=True,
        now=now,
    )


def _seed_dingtalk_meeting_task(
    *,
    store: AutoReplyStore,
    options: ScheduledTaskOptionService,
    working_directory: Path,
    now: datetime | None,
) -> ScheduledTask:
    del options, working_directory
    adopted = store.adopt_scheduled_task_service_command(
        migration_key=DINGTALK_MEETING_MIGRATION_KEY,
        command=DINGTALK_MEETING_SERVICE_COMMAND,
        seed_enabled=True,
        now=now,
    )
    if adopted is not None:
        return adopted
    return store.create_scheduled_task(
        migration_key=DINGTALK_MEETING_MIGRATION_KEY,
        name="检查 DingTalk 会议",
        command=DINGTALK_MEETING_SERVICE_COMMAND,
        cron_expression="0 * * * * *",
        timezone_name="Asia/Shanghai",
        enabled=True,
        now=now,
    )


def _seed_wechat_task(
    *,
    store: AutoReplyStore,
    options: ScheduledTaskOptionService,
    working_directory: Path,
    now: datetime | None,
) -> ScheduledTask:
    """Seed the WeChat message check as a service command, not an Agent task.

    The check is one deterministic producer pass over the ready WeChat
    account, so it runs in-process without a runtime or Skills. A task seeded
    earlier in the Agent form is moved to the command form in place; an
    untouched seed becomes enabled because its disabled state only reflected
    the Agent form's missing Skill revision.
    """
    del options, working_directory
    adopted = store.adopt_scheduled_task_service_command(
        migration_key=WECHAT_MESSAGE_MIGRATION_KEY,
        command=WECHAT_MESSAGE_SERVICE_COMMAND,
        seed_enabled=True,
        now=now,
    )
    if adopted is not None:
        return adopted
    return store.create_scheduled_task(
        migration_key=WECHAT_MESSAGE_MIGRATION_KEY,
        name="检查微信消息",
        command=WECHAT_MESSAGE_SERVICE_COMMAND,
        cron_expression="*/15 * * * * *",
        timezone_name="Asia/Shanghai",
        enabled=True,
        now=now,
    )


def _seed_oa_task(
    *,
    store: AutoReplyStore,
    options: ScheduledTaskOptionService,
    working_directory: Path,
    now: datetime | None,
) -> ScheduledTask:
    del options, working_directory
    adopted = store.adopt_scheduled_task_service_command(
        migration_key=DINGTALK_OA_MIGRATION_KEY,
        command=DINGTALK_OA_SERVICE_COMMAND,
        seed_enabled=True,
        now=now,
    )
    if adopted is not None:
        return adopted
    return store.create_scheduled_task(
        migration_key=DINGTALK_OA_MIGRATION_KEY,
        name="检查 DingTalk OA 审批",
        command=DINGTALK_OA_SERVICE_COMMAND,
        cron_expression="0 0 * * * *",
        timezone_name="Asia/Shanghai",
        enabled=True,
        now=now,
    )


def _seed_work_source_task(
    *,
    store: AutoReplyStore,
    options: ScheduledTaskOptionService,
    working_directory: Path,
    now: datetime | None,
) -> ScheduledTask:
    del options, working_directory
    adopted = store.adopt_scheduled_task_service_command(
        migration_key=WORK_SOURCE_MIGRATION_KEY,
        command=WORK_SOURCE_SERVICE_COMMAND,
        seed_enabled=True,
        now=now,
    )
    if adopted is not None:
        return adopted
    return store.create_scheduled_task(
        migration_key=WORK_SOURCE_MIGRATION_KEY,
        name="每天扫描工作来源",
        command=WORK_SOURCE_SERVICE_COMMAND,
        cron_expression="0 0 0 * * *",
        timezone_name="Asia/Shanghai",
        enabled=True,
        now=now,
    )


def _seed_weekly_okr_task(
    *,
    store: AutoReplyStore,
    options: ScheduledTaskOptionService,
    working_directory: Path,
    now: datetime | None,
) -> ScheduledTask:
    migration_key = "weekly-okr-report-sunday-v1"
    existing = _existing_task(
        store,
        migration_key,
        options=options,
        required_capabilities=PRODUCER_RUNTIME_CAPABILITIES,
        now=now,
    )
    if existing is not None:
        return _bound_to_this_checkout(store, existing, now=now)
    runtime, runtime_reason = _select_runtime(
        options, required_capabilities=PRODUCER_RUNTIME_CAPABILITIES
    )
    report_ref, report_reason = _operation_ref(
        options=options, name="ceo-weekly-report", position=0
    )
    okr_ref, okr_reason = _operation_ref(
        options=options, name="dingtang-okr-review", position=1
    )
    command = _one_shot_command(
        store, working_directory, "weekly-okr-report --force"
    )
    prompt = (
        "使用 $ceo-weekly-report 与 $dingtang-okr-review 理解现有周报边界。"
        f"只执行一次确定性命令：`{command}`，使时间资格只由本 Cron 控制。"
        "命令自身完成分析、发布和发送；不要在命令外重复读取 OKR、创建文档或"
        "发送群消息，并以命令返回的结构化状态报告本次结果。"
    )
    reasons = tuple(
        reason
        for reason in (runtime_reason, report_reason, okr_reason)
        if reason is not None
    )
    if reasons:
        prompt += "\n\n未启用：" + "；".join(reasons) + "。"
    return store.create_scheduled_task(
        migration_key=migration_key,
        name="周日生成 OKR 周报",
        prompt=prompt,
        cron_expression="0 0 18 * * 0",
        timezone_name="Asia/Shanghai",
        runtime_id=runtime.route_name,
        runtime_options={"model": runtime.model},
        required_runtime_capabilities=PRODUCER_RUNTIME_CAPABILITIES,
        working_directory=str(working_directory.expanduser().resolve()),
        skill_refs=(report_ref, okr_ref),
        enabled=not reasons,
        now=now,
    )


def _seed_minutes_task(
    *,
    store: AutoReplyStore,
    options: ScheduledTaskOptionService,
    working_directory: Path,
    now: datetime | None,
) -> ScheduledTask:
    """Seed AI minutes synchronization as a deterministic service command."""
    del options, working_directory
    adopted = store.adopt_scheduled_task_service_command(
        migration_key=MINUTES_SYNC_MIGRATION_KEY,
        command="sync-minutes-once",
        seed_enabled=True,
        now=now,
    )
    if adopted is not None:
        return adopted
    return store.create_scheduled_task(
        migration_key=MINUTES_SYNC_MIGRATION_KEY,
        name="每天同步 AI 听记",
        command="sync-minutes-once",
        cron_expression="0 0 20 * * *",
        timezone_name="Asia/Shanghai",
        enabled=True,
        now=now,
    )


def _service_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _prompt_bound_to_this_checkout(prompt: str, service_root: str) -> str | None:
    """Point a stored one-shot command at this checkout, or None if unchanged.

    The seed writes the repository path into the prompt when it first creates
    the task and never rewrites an existing task, so moving the checkout leaves
    the command pointing at a directory that no longer exists.  Only the path
    inside the command changes; later edits to the rest of the prompt survive.
    """
    segments = prompt.split("`")
    changed = False
    for index in range(1, len(segments), 2):
        segment = segments[index]
        if not segment.startswith("cd ") or " && " not in segment:
            continue
        head, rest = segment.split(" && ", 1)
        try:
            parts = shlex.split(head)
        except ValueError:
            continue
        if len(parts) != 2 or parts[1] == service_root:
            continue
        segments[index] = f"cd {shlex.quote(service_root)} && {rest}"
        changed = True
    return "`".join(segments) if changed else None


def _bound_to_this_checkout(
    store: AutoReplyStore, task: ScheduledTask, *, now: datetime | None
) -> ScheduledTask:
    """Heal a seeded prompt whose one-shot command outlived its checkout."""
    if task.deleted_at is not None or not task.prompt:
        return task
    prompt = _prompt_bound_to_this_checkout(task.prompt, str(_service_root()))
    if prompt is None:
        return task
    return store.update_scheduled_task(
        task.id, expected_version=task.version, prompt=prompt, now=now
    )


def _existing_task(
    store: AutoReplyStore,
    migration_key: str,
    *,
    options: ScheduledTaskOptionService | None = None,
    required_capabilities: frozenset[str] = frozenset(),
    now: datetime | None = None,
) -> ScheduledTask | None:
    if required_capabilities:
        if options is None:
            raise ValueError("Runtime options are required for capability migration")
        return store.backfill_scheduled_task_runtime_capabilities(
            migration_key=migration_key,
            required_capabilities=required_capabilities,
            eligible_runtime_ids=options.runtime_ids_supporting_capabilities(
                required_capabilities
            ),
            now=now,
        )
    return next(
        (
            task
            for task in store.list_scheduled_tasks(include_deleted=True)
            if task.migration_key == migration_key
        ),
        None,
    )


def _select_runtime(
    options: ScheduledTaskOptionService,
    *,
    required_capabilities: frozenset[str] = frozenset(),
) -> tuple[RuntimeOption, str | None]:
    runtime_options = options.list_runtime_options(
        required_capabilities=required_capabilities
    )
    selected = next((option for option in runtime_options if option.available), None)
    if selected is not None:
        return selected, None
    return (
        runtime_options[0],
        "没有健康且已配置的 Runtime（"
        + _runtime_unavailable_summary(runtime_options)
        + "）",
    )


def _operation_ref(
    *, options: ScheduledTaskOptionService, name: str, position: int
) -> tuple[ScheduledTaskSkillRef, str | None]:
    option = next(
        (item for item in options.list_operation_skill_options() if item.name == name),
        None,
    )
    reason = None
    if option is None or not option.available:
        reason = (
            f"operation Skill {name} 当前不可用（"
            + (
                option.unavailable_reason
                if option is not None and option.unavailable_reason
                else "operation_skill_unavailable"
            )
            + "）"
        )
    return (
        ScheduledTaskSkillRef(
            skill_source="operation",
            skill_name=name,
            position=position,
        ),
        reason,
    )


def _runtime_unavailable_summary(runtime_options: tuple[RuntimeOption, ...]) -> str:
    return "; ".join(
        f"{option.route_name}={option.unavailable_reason or 'unavailable'}"
        for option in runtime_options
    )
