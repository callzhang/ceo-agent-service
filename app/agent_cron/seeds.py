from __future__ import annotations

from dataclasses import dataclass
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
DINGTALK_CALENDAR_INVITE_MIGRATION_KEY = "dingtalk-calendar-invite-check-v1"
DINGTALK_CALENDAR_INVITE_SERVICE_COMMAND = "calendar-invites-once"
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


@dataclass(frozen=True)
class ScheduledTaskDefaultCopy:
    name: str
    description: str
    old_name: str
    old_description: str


SCHEDULED_TASK_DEFAULT_COPY = {
    DINGTALK_MESSAGE_MIGRATION_KEY: ScheduledTaskDefaultCopy(
        name="处理新的钉钉消息",
        description="发现新的单聊或群聊 @ 消息后，由 Agent 读取最新上下文，决定回复、表态、澄清或不处理。",
        old_name="检查 DingTalk 消息",
        old_description="增量检查 DingTalk 消息，并将新消息送入统一处理队列。",
    ),
    DINGTALK_MESSAGE_RECOVERY_MIGRATION_KEY: ScheduledTaskDefaultCopy(
        name="补查遗漏的钉钉消息和日历更新",
        description="扩大读取范围，找回常规检查遗漏的单聊、群聊 @ 消息和原地更新的日历邀请；发现后由 Agent 按对应的消息或日程规则处理。",
        old_name="恢复近期 DingTalk 消息",
        old_description="按较宽时间范围恢复近期 DingTalk 消息，补齐漏读记录及原地更新的待响应日程邀请。",
    ),
    DINGTALK_CALENDAR_INVITE_MIGRATION_KEY: ScheduledTaskDefaultCopy(
        name="处理新的钉钉日历邀请",
        description="发现新的会议邀请后，由 Agent 读取最新邀请和日程冲突，决定接受、暂定、拒绝或向邀请人澄清；执行后核验日历状态。",
        old_name="",
        old_description="",
    ),
    DINGTALK_MEETING_MIGRATION_KEY: ScheduledTaskDefaultCopy(
        name="同步会议结论与管理者视角",
        description="会议结束且听记可用后，由 Agent 总结关键结论、分歧和行动项，确保管理者观点被准确传达；发现未对齐时发送会后澄清。",
        old_name="检查 DingTalk 会议",
        old_description="扫描已结束的 DingTalk 会议，并创建会议处理任务。",
    ),
    WECHAT_MESSAGE_MIGRATION_KEY: ScheduledTaskDefaultCopy(
        name="处理已授权会话的新微信消息",
        description="发现已启用的好友新消息或群聊 @ 后，由 Agent 判断是否回复；仅按已配置范围和发送模式处理，不读取或回复其他会话。",
        old_name="检查微信消息",
        old_description="检查已配置微信会话的新消息，并创建后续处理任务。",
    ),
    DINGTALK_OA_MIGRATION_KEY: ScheduledTaskDefaultCopy(
        name="处理新的钉钉 OA 审批",
        description="发现新的或有新处理记录的待审批 OA 后，由 Agent 读取完整材料与审批流水，判断同意、拒绝或评论补充要求，并在执行后核验结果。",
        old_name="审阅新的或有进展的钉钉 OA",
        old_description="发现新的或有新处理记录的待审批 OA 后，由 Agent 读取完整材料与审批流水，判断同意、拒绝或评论补充要求，并在执行后核验结果。",
    ),
    WORK_SOURCE_MIGRATION_KEY: ScheduledTaskDefaultCopy(
        name="整理工作区中的新工作记录",
        description="发现工作区中新建或修改的 Markdown、文本文件后，由 Agent 判断其中是否有值得持续跟进的承诺，并按证据创建或更新项目、TODO 和跟进。",
        old_name="每天扫描工作来源",
        old_description="扫描日历、待办和其他工作来源，生成可处理的工作输入。",
    ),
    WEEKLY_OKR_MIGRATION_KEY: ScheduledTaskDefaultCopy(
        name="生成并发送每周 OKR 管理周报",
        description="读取所有管理者的实时 OKR，由 Agent 结合工作证据分析进展、风险、领导力和文化表现，生成周报并发布到管理知识库和 CEO-2 管理群。",
        old_name="周日生成 OKR 周报",
        old_description="读取管理者实时 OKR，生成本周管理进度周报。",
    ),
    MINUTES_SYNC_MIGRATION_KEY: ScheduledTaskDefaultCopy(
        name="归档新增的钉钉 AI 听记",
        description="发现尚未归档且可访问的钉钉 AI 听记后，读取可用的摘要和逐字稿并归档到工作区；权限受限或内容不可读时保留同步状态，待后续检查。",
        old_name="每天同步 AI 听记",
        old_description="同步 AI 听记的摘要、逐字稿和归档游标到工作区。",
    ),
}


def _default_copy(migration_key: str) -> ScheduledTaskDefaultCopy:
    return SCHEDULED_TASK_DEFAULT_COPY[migration_key]


def _upgrade_default_copy(
    store: AutoReplyStore,
    task: ScheduledTask,
    *,
    now: datetime | None,
) -> ScheduledTask:
    copy = _default_copy(task.migration_key or "")
    if not copy.old_name or not copy.old_description:
        return task
    return store.upgrade_scheduled_task_default_copy(
        task.id,
        expected_version=task.version,
        old_name=copy.old_name,
        old_description=copy.old_description,
        name=copy.name,
        description=copy.description,
        now=now,
    )


DINGTALK_MESSAGE_LEGACY_CONSUMER_PROMPT = (
    "使用 $ceo-message-triage 判断 Trigger 发现的真实 DingTalk 消息是否需要 CEO "
    "关注或回复；如果消息是日程邀请或原地更新的日程卡片，使用 "
    "$ceo-calendar-invite 处理。使用 $dingtalk-chat 读取所需消息上下文，并使用 "
    "$dingtalk-calendar 核验最新日程状态；"
    "原地更新后的日程卡片是新的输入版本，更新前基于旧时段或旧冲突发送的澄清"
    "不构成精确重复，必须根据最新日程重新处置；"
    "不要新增复盘或摘要任务。"
)
DINGTALK_MESSAGE_CONSUMER_PROMPT = (
    "使用 $ceo-message-triage 判断 Trigger 发现的真实 DingTalk 消息是否需要 CEO "
    "关注或回复；使用 $dingtalk-chat 读取所需消息上下文；不要新增复盘或摘要任务。"
)
CALENDAR_INVITE_CONSUMER_PROMPT = (
    "使用 $ceo-calendar-invite 处理 Trigger 发现的真实钉钉日历邀请；使用 "
    "$dingtalk-calendar 读取最新邀请和冲突日程，并在需要向邀请人澄清时使用 "
    "$dingtalk-chat。不要新增复盘或摘要任务。"
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
            revision
            for revision in (skill.revisions if skill else ())
            if revision.available
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
    dingtalk_calendar_invite = _seed_dingtalk_calendar_invite_task(
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
    return tuple(
        _upgrade_default_copy(store, task, now=now)
        for task in (
            dingtalk_message,
            dingtalk_calendar_invite,
            dingtalk_message_recovery,
            dingtalk_meeting,
            wechat,
            oa,
            work_sources,
            weekly_okr,
            minutes,
        )
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
        seed_description=_default_copy(DINGTALK_MESSAGE_MIGRATION_KEY).description,
        consumer_prompt=DINGTALK_MESSAGE_CONSUMER_PROMPT,
        consumer_skill_refs=skill_refs,
        legacy_consumer_prompt=DINGTALK_MESSAGE_LEGACY_CONSUMER_PROMPT,
        legacy_consumer_skill_refs=_consumer_skill_refs(
            options,
            managed=("ceo-message-triage", "ceo-calendar-invite"),
            operation=("dingtalk-chat", "dingtalk-calendar"),
        ),
        now=now,
    )
    if adopted is not None:
        return adopted
    return store.create_scheduled_task(
        migration_key=DINGTALK_MESSAGE_MIGRATION_KEY,
        name=_default_copy(DINGTALK_MESSAGE_MIGRATION_KEY).name,
        description=_default_copy(DINGTALK_MESSAGE_MIGRATION_KEY).description,
        prompt=DINGTALK_MESSAGE_CONSUMER_PROMPT,
        command=DINGTALK_MESSAGE_SERVICE_COMMAND,
        cron_expression="0 * * * * *",
        timezone_name="Asia/Shanghai",
        skill_refs=skill_refs,
        enabled=True,
        now=now,
    )


def _seed_dingtalk_calendar_invite_task(
    *,
    store: AutoReplyStore,
    options: ScheduledTaskOptionService,
    working_directory: Path,
    now: datetime | None,
) -> ScheduledTask:
    """Seed calendar invitation handling as a separate service Trigger."""
    del working_directory
    skill_refs = _consumer_skill_refs(
        options,
        managed=("ceo-calendar-invite",),
        operation=("dingtalk-calendar", "dingtalk-chat"),
    )
    adopted = store.adopt_scheduled_task_service_command(
        migration_key=DINGTALK_CALENDAR_INVITE_MIGRATION_KEY,
        command=DINGTALK_CALENDAR_INVITE_SERVICE_COMMAND,
        seed_enabled=True,
        seed_description=_default_copy(
            DINGTALK_CALENDAR_INVITE_MIGRATION_KEY
        ).description,
        consumer_prompt=CALENDAR_INVITE_CONSUMER_PROMPT,
        consumer_skill_refs=skill_refs,
        now=now,
    )
    if adopted is not None:
        return adopted
    return store.create_scheduled_task(
        migration_key=DINGTALK_CALENDAR_INVITE_MIGRATION_KEY,
        name=_default_copy(DINGTALK_CALENDAR_INVITE_MIGRATION_KEY).name,
        description=_default_copy(
            DINGTALK_CALENDAR_INVITE_MIGRATION_KEY
        ).description,
        prompt=CALENDAR_INVITE_CONSUMER_PROMPT,
        command=DINGTALK_CALENDAR_INVITE_SERVICE_COMMAND,
        cron_expression="10 * * * * *",
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
        managed=("ceo-message-triage", "ceo-calendar-invite"),
        operation=("dingtalk-chat", "dingtalk-calendar"),
    )
    adopted = store.adopt_scheduled_task_service_command(
        migration_key=DINGTALK_MESSAGE_RECOVERY_MIGRATION_KEY,
        command=DINGTALK_MESSAGE_RECOVERY_SERVICE_COMMAND,
        seed_enabled=True,
        seed_description=_default_copy(
            DINGTALK_MESSAGE_RECOVERY_MIGRATION_KEY
        ).description,
        consumer_prompt=DINGTALK_MESSAGE_LEGACY_CONSUMER_PROMPT,
        consumer_skill_refs=skill_refs,
        now=now,
    )
    if adopted is not None:
        return adopted
    return store.create_scheduled_task(
        migration_key=DINGTALK_MESSAGE_RECOVERY_MIGRATION_KEY,
        name=_default_copy(DINGTALK_MESSAGE_RECOVERY_MIGRATION_KEY).name,
        description=_default_copy(DINGTALK_MESSAGE_RECOVERY_MIGRATION_KEY).description,
        prompt=DINGTALK_MESSAGE_LEGACY_CONSUMER_PROMPT,
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
        seed_description=_default_copy(DINGTALK_MEETING_MIGRATION_KEY).description,
        consumer_prompt=MEETING_CONSUMER_PROMPT,
        consumer_skill_refs=skill_refs,
        now=now,
    )
    if adopted is not None:
        return adopted
    return store.create_scheduled_task(
        migration_key=DINGTALK_MEETING_MIGRATION_KEY,
        name=_default_copy(DINGTALK_MEETING_MIGRATION_KEY).name,
        description=_default_copy(DINGTALK_MEETING_MIGRATION_KEY).description,
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
        seed_description=_default_copy(WECHAT_MESSAGE_MIGRATION_KEY).description,
        consumer_prompt=WECHAT_MESSAGE_CONSUMER_PROMPT,
        consumer_skill_refs=skill_refs,
        now=now,
    )
    if adopted is not None:
        return adopted
    return store.create_scheduled_task(
        migration_key=WECHAT_MESSAGE_MIGRATION_KEY,
        name=_default_copy(WECHAT_MESSAGE_MIGRATION_KEY).name,
        description=_default_copy(WECHAT_MESSAGE_MIGRATION_KEY).description,
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
        seed_description=_default_copy(DINGTALK_OA_MIGRATION_KEY).description,
        consumer_prompt=OA_CONSUMER_PROMPT,
        consumer_skill_refs=skill_refs,
        now=now,
    )
    if adopted is not None:
        return adopted
    return store.create_scheduled_task(
        migration_key=DINGTALK_OA_MIGRATION_KEY,
        name=_default_copy(DINGTALK_OA_MIGRATION_KEY).name,
        description=_default_copy(DINGTALK_OA_MIGRATION_KEY).description,
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
        seed_description=_default_copy(WORK_SOURCE_MIGRATION_KEY).description,
        consumer_prompt=WORK_SOURCE_CONSUMER_PROMPT,
        consumer_skill_refs=skill_refs,
        now=now,
    )
    if adopted is not None:
        return adopted
    return store.create_scheduled_task(
        migration_key=WORK_SOURCE_MIGRATION_KEY,
        name=_default_copy(WORK_SOURCE_MIGRATION_KEY).name,
        description=_default_copy(WORK_SOURCE_MIGRATION_KEY).description,
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

    The service command owns the long-running browser reads and invokes the
    weekly-report Agent analysis inside the tracked service run. Keeping that
    whole workflow in process gives it one durable run record and bounded
    source/Agent timeouts instead of an untracked child command.
    """
    del options, working_directory
    adopted = store.adopt_scheduled_task_service_command(
        migration_key=WEEKLY_OKR_MIGRATION_KEY,
        command=WEEKLY_OKR_SERVICE_COMMAND,
        seed_enabled=True,
        seed_description=_default_copy(WEEKLY_OKR_MIGRATION_KEY).description,
        now=now,
    )
    if adopted is not None:
        return adopted
    return store.create_scheduled_task(
        migration_key=WEEKLY_OKR_MIGRATION_KEY,
        name=_default_copy(WEEKLY_OKR_MIGRATION_KEY).name,
        description=_default_copy(WEEKLY_OKR_MIGRATION_KEY).description,
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
        seed_description=_default_copy(MINUTES_SYNC_MIGRATION_KEY).description,
        now=now,
    )
    if adopted is not None:
        return adopted
    return store.create_scheduled_task(
        migration_key=MINUTES_SYNC_MIGRATION_KEY,
        name=_default_copy(MINUTES_SYNC_MIGRATION_KEY).name,
        description=_default_copy(MINUTES_SYNC_MIGRATION_KEY).description,
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
