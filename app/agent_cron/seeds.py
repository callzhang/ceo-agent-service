from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.agent_cron.models import ScheduledTask
from app.agent_cron.options import RuntimeOption, ScheduledTaskOptionService
from app.store import AutoReplyStore


MINUTES_SYNC_MIGRATION_KEY = "ceo-minutes-sync-daily-v1"
WEEKLY_OKR_MIGRATION_KEY = "weekly-okr-report-sunday-v1"
WEEKLY_OKR_SERVICE_COMMAND = "weekly-okr-report"
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
    """Seed the weekly OKR report as a deterministic service command.

    It was an Agent task whose whole prompt was "run exactly one deterministic
    command", and that command reads every manager's live OKR through a
    headless browser.  One real run took over fifty minutes and produced no
    output until the end, so the Agent runtime killed it at its 900-second
    idle limit and the orphaned command kept running outside any run record.
    No Agent timeout can hold this work, and there is no judgement in it.
    """
    del options, working_directory
    adopted = store.adopt_scheduled_task_service_command(
        migration_key=WEEKLY_OKR_MIGRATION_KEY,
        command=WEEKLY_OKR_SERVICE_COMMAND,
        seed_enabled=True,
        now=now,
    )
    if adopted is not None:
        return adopted
    return store.create_scheduled_task(
        migration_key=WEEKLY_OKR_MIGRATION_KEY,
        name="周日生成 OKR 周报",
        command=WEEKLY_OKR_SERVICE_COMMAND,
        cron_expression="0 0 18 * * 0",
        timezone_name="Asia/Shanghai",
        enabled=True,
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


def _runtime_unavailable_summary(runtime_options: tuple[RuntimeOption, ...]) -> str:
    return "; ".join(
        f"{option.route_name}={option.unavailable_reason or 'unavailable'}"
        for option in runtime_options
    )
