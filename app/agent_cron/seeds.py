from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.agent_cron.models import ScheduledTask, ScheduledTaskSkillRef
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

DINGTALK_MESSAGE_CONSUMER_PROMPT = (
    "使用 $ceo-message-triage 判断 Trigger 发现的真实 DingTalk 消息是否需要 CEO "
    "关注或回复，并使用 $dingtalk-chat 读取所需上下文；不要新增复盘或摘要任务。"
)
WECHAT_MESSAGE_CONSUMER_PROMPT = (
    "使用 $ceo-wechat 处理 Trigger 发现的真实微信消息，严格保留已配置联系人、"
    "群@、auto-confirm 和 sender 授权边界；不要新增复盘或摘要任务。"
)
MEETING_CONSUMER_PROMPT = (
    "使用 $ceo-meeting-work 处理 Trigger 发现的真实会议，并按需使用 "
    "$dingtalk-minutes 与 $dingtalk-calendar 读取会议和日历证据。"
)
OA_CONSUMER_PROMPT = (
    "使用 $dingtalk-oa-approval 处理 Trigger 发现的真实 DingTalk OA 待审批事项，"
    "沿用现有审批判断、回复与投递边界。"
)
WORK_SOURCE_CONSUMER_PROMPT = (
    "使用 $ceo-work-tracking 处理 Trigger 发现的真实工作来源，只更新已有工作记录或"
    "创建有明确证据的新工作项。"
)


def _consumer_skill_refs(
    options: ScheduledTaskOptionService,
    *,
    managed: tuple[str, ...] = (),
    operation: tuple[str, ...] = (),
) -> tuple[ScheduledTaskSkillRef, ...]:
    refs: list[ScheduledTaskSkillRef] = []
    managed_options = {item.name: item for item in options.list_managed_skill_options()}
    for name in managed:
        skill = managed_options.get(name)
        revisions = tuple(
            revision for revision in (skill.revisions if skill else ()) if revision.available
        )
        if skill is None or not revisions:
            raise ValueError(f"scheduled consumer managed Skill unavailable: {name}")
        revision = max(revisions, key=lambda item: item.revision_number)
        refs.append(
            ScheduledTaskSkillRef(
                skill_source="managed",
                skill_name=name,
                managed_skill_id=skill.skill_id,
                managed_revision_id=revision.revision_id,
                position=len(refs),
            )
        )
    operation_options = {
        item.name: item
        for item in options.list_operation_skill_options()
        if item.available
    }
    for name in operation:
        if name not in operation_options:
            raise ValueError(f"scheduled consumer operation Skill unavailable: {name}")
        refs.append(
            ScheduledTaskSkillRef(
                skill_source="operation",
                skill_name=name,
                position=len(refs),
            )
        )
    return tuple(refs)


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

    The check is one deterministic producer pass. It runs in-process without a
    task-level Runtime, then supplies a targeted prompt and exact Skills only to
    the Consumer Agent when a real message is found.
    """
    del working_directory
    skill_refs = _consumer_skill_refs(
        options,
        managed=("ceo-message-triage",),
        operation=("dingtalk-chat",),
    )
    adopted = store.adopt_scheduled_task_service_command(
        migration_key=DINGTALK_MESSAGE_MIGRATION_KEY,
        command=DINGTALK_MESSAGE_SERVICE_COMMAND,
        seed_enabled=True,
        seed_description="增量检查 DingTalk 消息，并将新消息送入统一处理队列。",
        consumer_prompt=DINGTALK_MESSAGE_CONSUMER_PROMPT,
        consumer_skill_refs=skill_refs,
        now=now,
    )
    if adopted is not None:
        return adopted
    return store.create_scheduled_task(
        migration_key=DINGTALK_MESSAGE_MIGRATION_KEY,
        name="检查 DingTalk 消息",
        description="增量检查 DingTalk 消息，并将新消息送入统一处理队列。",
        prompt=DINGTALK_MESSAGE_CONSUMER_PROMPT,
        command=DINGTALK_MESSAGE_SERVICE_COMMAND,
        cron_expression="0 * * * * *",
        timezone_name="Asia/Shanghai",
        skill_refs=skill_refs,
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
    del working_directory
    skill_refs = _consumer_skill_refs(
        options,
        managed=("ceo-message-triage",),
        operation=("dingtalk-chat",),
    )
    adopted = store.adopt_scheduled_task_service_command(
        migration_key=DINGTALK_MESSAGE_RECOVERY_MIGRATION_KEY,
        command=DINGTALK_MESSAGE_RECOVERY_SERVICE_COMMAND,
        seed_enabled=True,
        seed_description="按较宽时间范围恢复近期 DingTalk 消息，补齐漏读记录。",
        consumer_prompt=DINGTALK_MESSAGE_CONSUMER_PROMPT,
        consumer_skill_refs=skill_refs,
        now=now,
    )
    if adopted is not None:
        return adopted
    return store.create_scheduled_task(
        migration_key=DINGTALK_MESSAGE_RECOVERY_MIGRATION_KEY,
        name="恢复近期 DingTalk 消息",
        description="按较宽时间范围恢复近期 DingTalk 消息，补齐漏读记录。",
        prompt=DINGTALK_MESSAGE_CONSUMER_PROMPT,
        command=DINGTALK_MESSAGE_RECOVERY_SERVICE_COMMAND,
        cron_expression="0 30 * * * *",
        timezone_name="Asia/Shanghai",
        skill_refs=skill_refs,
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
    del working_directory
    skill_refs = _consumer_skill_refs(
        options,
        managed=("ceo-meeting-work",),
        operation=("dingtalk-minutes", "dingtalk-calendar"),
    )
    adopted = store.adopt_scheduled_task_service_command(
        migration_key=DINGTALK_MEETING_MIGRATION_KEY,
        command=DINGTALK_MEETING_SERVICE_COMMAND,
        seed_enabled=True,
        seed_description="扫描已结束的 DingTalk 会议，并创建会议处理任务。",
        consumer_prompt=MEETING_CONSUMER_PROMPT,
        consumer_skill_refs=skill_refs,
        now=now,
    )
    if adopted is not None:
        return adopted
    return store.create_scheduled_task(
        migration_key=DINGTALK_MEETING_MIGRATION_KEY,
        name="检查 DingTalk 会议",
        description="扫描已结束的 DingTalk 会议，并创建会议处理任务。",
        prompt=MEETING_CONSUMER_PROMPT,
        command=DINGTALK_MEETING_SERVICE_COMMAND,
        cron_expression="0 * * * * *",
        timezone_name="Asia/Shanghai",
        skill_refs=skill_refs,
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

    The check is one deterministic producer pass over the ready WeChat account.
    It runs in-process without a task-level Runtime, then supplies its targeted
    prompt and Skill only when a real message is found.
    """
    del working_directory
    skill_refs = _consumer_skill_refs(options, managed=("ceo-wechat",))
    adopted = store.adopt_scheduled_task_service_command(
        migration_key=WECHAT_MESSAGE_MIGRATION_KEY,
        command=WECHAT_MESSAGE_SERVICE_COMMAND,
        seed_enabled=True,
        seed_description="检查已配置微信会话的新消息，并创建后续处理任务。",
        consumer_prompt=WECHAT_MESSAGE_CONSUMER_PROMPT,
        consumer_skill_refs=skill_refs,
        now=now,
    )
    if adopted is not None:
        return adopted
    return store.create_scheduled_task(
        migration_key=WECHAT_MESSAGE_MIGRATION_KEY,
        name="检查微信消息",
        description="检查已配置微信会话的新消息，并创建后续处理任务。",
        prompt=WECHAT_MESSAGE_CONSUMER_PROMPT,
        command=WECHAT_MESSAGE_SERVICE_COMMAND,
        cron_expression="*/15 * * * * *",
        timezone_name="Asia/Shanghai",
        skill_refs=skill_refs,
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
    del working_directory
    skill_refs = _consumer_skill_refs(
        options,
        operation=("dingtalk-oa-approval",),
    )
    adopted = store.adopt_scheduled_task_service_command(
        migration_key=DINGTALK_OA_MIGRATION_KEY,
        command=DINGTALK_OA_SERVICE_COMMAND,
        seed_enabled=True,
        seed_description="检查待处理的 DingTalk OA 审批，并创建后续处理任务。",
        consumer_prompt=OA_CONSUMER_PROMPT,
        consumer_skill_refs=skill_refs,
        now=now,
    )
    if adopted is not None:
        return adopted
    return store.create_scheduled_task(
        migration_key=DINGTALK_OA_MIGRATION_KEY,
        name="检查 DingTalk OA 审批",
        description="检查待处理的 DingTalk OA 审批，并创建后续处理任务。",
        prompt=OA_CONSUMER_PROMPT,
        command=DINGTALK_OA_SERVICE_COMMAND,
        cron_expression="0 0 * * * *",
        timezone_name="Asia/Shanghai",
        skill_refs=skill_refs,
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
    del working_directory
    skill_refs = _consumer_skill_refs(options, managed=("ceo-work-tracking",))
    adopted = store.adopt_scheduled_task_service_command(
        migration_key=WORK_SOURCE_MIGRATION_KEY,
        command=WORK_SOURCE_SERVICE_COMMAND,
        seed_enabled=True,
        seed_description="扫描日历、待办和其他工作来源，生成可处理的工作输入。",
        consumer_prompt=WORK_SOURCE_CONSUMER_PROMPT,
        consumer_skill_refs=skill_refs,
        now=now,
    )
    if adopted is not None:
        return adopted
    return store.create_scheduled_task(
        migration_key=WORK_SOURCE_MIGRATION_KEY,
        name="每天扫描工作来源",
        description="扫描日历、待办和其他工作来源，生成可处理的工作输入。",
        prompt=WORK_SOURCE_CONSUMER_PROMPT,
        command=WORK_SOURCE_SERVICE_COMMAND,
        cron_expression="0 0 0 * * *",
        timezone_name="Asia/Shanghai",
        skill_refs=skill_refs,
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
        seed_description="读取管理者实时 OKR，生成本周管理进度周报。",
        now=now,
    )
    if adopted is not None:
        return adopted
    return store.create_scheduled_task(
        migration_key=WEEKLY_OKR_MIGRATION_KEY,
        name="周日生成 OKR 周报",
        description="读取管理者实时 OKR，生成本周管理进度周报。",
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
        seed_description="同步 AI 听记的摘要、逐字稿和归档游标到工作区。",
        now=now,
    )
    if adopted is not None:
        return adopted
    return store.create_scheduled_task(
        migration_key=MINUTES_SYNC_MIGRATION_KEY,
        name="每天同步 AI 听记",
        description="同步 AI 听记的摘要、逐字稿和归档游标到工作区。",
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
