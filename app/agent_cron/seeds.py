from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path
import shlex
import sys

from app.agent_cron.models import ScheduledTask, ScheduledTaskSkillRef
from app.agent_cron.options import RuntimeOption, ScheduledTaskOptionService
from app.managed_skills import (
    MINUTES_SYNC_SKILL_NAME,
    REPOSITORY_IMPORT_SOURCE,
    repository_managed_skill_content,
)
from app.store import AutoReplyStore


MINUTES_SYNC_MIGRATION_KEY = "ceo-minutes-sync-daily-v1"
DINGTALK_MESSAGE_MIGRATION_KEY = "dingtalk-message-check-v1"


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
    existing = _existing_task(store, DINGTALK_MESSAGE_MIGRATION_KEY)
    if existing is not None:
        return existing
    runtime, runtime_reason = _select_runtime(options)
    managed_ref, managed_reason = _managed_ref(
        store=store,
        options=options,
        name="ceo-message-triage",
        position=0,
    )
    operation_ref, operation_reason = _operation_ref(
        options=options,
        name="dingtalk-chat",
        position=1,
    )
    command = _one_shot_command(store, working_directory, "produce-once")
    prompt = (
        "使用 $ceo-message-triage 与 $dingtalk-chat 理解现有消息发现边界。"
        f"只执行一次确定性 producer 命令：`{command}`。"
        "该命令负责增量读取、去重并写入 reply task，后续由统一 Dispatcher 消费；"
        "不要直接回复、重复消费、新增复盘或摘要任务。"
    )
    reasons = tuple(
        reason
        for reason in (runtime_reason, managed_reason, operation_reason)
        if reason is not None
    )
    if reasons:
        prompt += "\n\n未启用：" + "；".join(reasons) + "。"
    return store.create_scheduled_task(
        migration_key=DINGTALK_MESSAGE_MIGRATION_KEY,
        name="检查 DingTalk 消息",
        prompt=prompt,
        cron_expression="0 * * * * *",
        timezone_name="Asia/Shanghai",
        runtime_id=runtime.route_name,
        runtime_options={"model": runtime.model},
        working_directory=str(working_directory.expanduser().resolve()),
        skill_refs=(managed_ref, operation_ref),
        enabled=not reasons,
        now=now,
    )


def _seed_dingtalk_meeting_task(
    *,
    store: AutoReplyStore,
    options: ScheduledTaskOptionService,
    working_directory: Path,
    now: datetime | None,
) -> ScheduledTask:
    migration_key = "dingtalk-meeting-check-v1"
    existing = _existing_task(store, migration_key)
    if existing is not None:
        return existing
    runtime, runtime_reason = _select_runtime(options)
    managed_ref, managed_reason = _managed_ref(
        store=store,
        options=options,
        name="ceo-meeting-work",
        position=0,
    )
    minutes_ref, minutes_reason = _operation_ref(
        options=options, name="dingtalk-minutes", position=1
    )
    calendar_ref, calendar_reason = _operation_ref(
        options=options, name="dingtalk-calendar", position=2
    )
    command = _one_shot_command(store, working_directory, "scan-meetings-once")
    prompt = (
        "使用 $ceo-meeting-work、$dingtalk-minutes 与 $dingtalk-calendar 理解"
        "会议发现边界。只执行一次确定性 producer 命令："
        f"`{command}`。该命令仅为当前时间达到 ended_at + 10 minutes（10 分钟）"
        "且资料可读取的会议去重创建 meeting alignment job，后续由统一 Dispatcher "
        "消费；不要直接分析或发送会议结果。"
    )
    reasons = tuple(
        reason
        for reason in (
            runtime_reason,
            managed_reason,
            minutes_reason,
            calendar_reason,
        )
        if reason is not None
    )
    if reasons:
        prompt += "\n\n未启用：" + "；".join(reasons) + "。"
    return store.create_scheduled_task(
        migration_key=migration_key,
        name="检查 DingTalk 会议",
        prompt=prompt,
        cron_expression="0 * * * * *",
        timezone_name="Asia/Shanghai",
        runtime_id=runtime.route_name,
        runtime_options={"model": runtime.model},
        working_directory=str(working_directory.expanduser().resolve()),
        skill_refs=(managed_ref, minutes_ref, calendar_ref),
        enabled=not reasons,
        now=now,
    )


def _seed_wechat_task(
    *,
    store: AutoReplyStore,
    options: ScheduledTaskOptionService,
    working_directory: Path,
    now: datetime | None,
) -> ScheduledTask:
    migration_key = "wechat-message-check-v1"
    existing = _existing_task(store, migration_key)
    if existing is not None:
        return existing
    runtime, runtime_reason = _select_runtime(options)
    managed_ref, managed_reason = _managed_ref(
        store=store,
        options=options,
        name="ceo-wechat",
        position=0,
    )
    command = _one_shot_command(
        store,
        working_directory,
        "produce-once",
        module="app.wechat.cli",
        include_workspace=False,
    )
    prompt = (
        f"使用 $ceo-wechat 执行 `{command}`，完成现有 producer "
        "的一次检查，并严格保留"
        "已配置联系人、群@、auto-confirm 和 sender 授权边界；投递及状态确认仍由"
        "内部机制处理。不要新增复盘或摘要。"
    )
    reasons = tuple(
        reason for reason in (runtime_reason, managed_reason) if reason is not None
    )
    if reasons:
        prompt += "\n\n未启用：" + "；".join(reasons) + "。"
    return store.create_scheduled_task(
        migration_key=migration_key,
        name="检查微信消息",
        prompt=prompt,
        cron_expression="*/15 * * * * *",
        timezone_name="Asia/Shanghai",
        runtime_id=runtime.route_name,
        runtime_options={"model": runtime.model},
        working_directory=str(working_directory.expanduser().resolve()),
        skill_refs=(managed_ref,),
        enabled=not reasons,
        now=now,
    )


def _seed_oa_task(
    *,
    store: AutoReplyStore,
    options: ScheduledTaskOptionService,
    working_directory: Path,
    now: datetime | None,
) -> ScheduledTask:
    migration_key = "dingtalk-oa-check-v1"
    existing = _existing_task(store, migration_key)
    if existing is not None:
        return existing
    runtime, runtime_reason = _select_runtime(options)
    operation_ref, operation_reason = _operation_ref(
        options=options, name="dingtalk-oa-approval", position=0
    )
    command = _one_shot_command(store, working_directory, "scan-oa-approvals")
    prompt = (
        "使用 $dingtalk-oa-approval 理解现有 OA 扫描边界。"
        f"只执行一次确定性 scanner 命令：`{command}`。"
        "该命令负责按 revision 去重并写入既有业务输入；"
        "不要直接审批或发送，后续处理交给统一 Dispatcher。"
    )
    reasons = tuple(
        reason
        for reason in (runtime_reason, operation_reason)
        if reason is not None
    )
    if reasons:
        prompt += "\n\n未启用：" + "；".join(reasons) + "。"
    return store.create_scheduled_task(
        migration_key=migration_key,
        name="检查 DingTalk OA 审批",
        prompt=prompt,
        cron_expression="0 0 * * * *",
        timezone_name="Asia/Shanghai",
        runtime_id=runtime.route_name,
        runtime_options={"model": runtime.model},
        working_directory=str(working_directory.expanduser().resolve()),
        skill_refs=(operation_ref,),
        enabled=not reasons,
        now=now,
    )


def _seed_work_source_task(
    *,
    store: AutoReplyStore,
    options: ScheduledTaskOptionService,
    working_directory: Path,
    now: datetime | None,
) -> ScheduledTask:
    migration_key = "work-source-scan-daily-v1"
    existing = _existing_task(store, migration_key)
    if existing is not None:
        return existing
    runtime, runtime_reason = _select_runtime(options)
    managed_ref, managed_reason = _managed_ref(
        store=store,
        options=options,
        name="ceo-work-tracking",
        position=0,
    )
    minutes_ref, minutes_reason = _operation_ref(
        options=options, name="dingtalk-minutes", position=1
    )
    command = _one_shot_command(store, working_directory, "scan-task-sources")
    prompt = (
        "使用 $ceo-work-tracking 与 $dingtalk-minutes 理解现有工作来源边界。"
        f"只执行一次确定性 scanner 命令：`{command}`。"
        "该命令扫描本地工作目录中的增量文件和 AI 听记，并仅创建尚未入队的"
        "工作摘要输入；不要直接修改工作对象，后续消费交给统一 Dispatcher。"
    )
    reasons = tuple(
        reason
        for reason in (runtime_reason, managed_reason, minutes_reason)
        if reason is not None
    )
    if reasons:
        prompt += "\n\n未启用：" + "；".join(reasons) + "。"
    return store.create_scheduled_task(
        migration_key=migration_key,
        name="每天扫描工作来源",
        prompt=prompt,
        cron_expression="0 0 0 * * *",
        timezone_name="Asia/Shanghai",
        runtime_id=runtime.route_name,
        runtime_options={"model": runtime.model},
        working_directory=str(working_directory.expanduser().resolve()),
        skill_refs=(managed_ref, minutes_ref),
        enabled=not reasons,
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
    existing = _existing_task(store, migration_key)
    if existing is not None:
        return existing
    runtime, runtime_reason = _select_runtime(options)
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
    existing = next(
        (
            task
            for task in store.list_scheduled_tasks(include_deleted=True)
            if task.migration_key == MINUTES_SYNC_MIGRATION_KEY
        ),
        None,
    )
    if existing is not None:
        return existing

    skill = store.get_managed_skill_by_name(MINUTES_SYNC_SKILL_NAME)
    if skill is None:
        raise ValueError("ceo-minutes-sync repository managed Skill is missing")
    repository_content = repository_managed_skill_content(MINUTES_SYNC_SKILL_NAME)
    repository_sha256 = hashlib.sha256(repository_content.encode("utf-8")).hexdigest()
    repository_revisions = tuple(
        revision
        for revision in store.list_managed_skill_revisions(skill.id)
        if revision.source == REPOSITORY_IMPORT_SOURCE
        and revision.sha256 == repository_sha256
        and revision.content == repository_content
    )
    if not repository_revisions:
        raise ValueError("ceo-minutes-sync repository revision is missing")
    revision = repository_revisions[-1]

    managed_option = next(
        option
        for option in options.list_managed_skill_options()
        if option.skill_id == skill.id
    )
    revision_option = next(
        option
        for option in managed_option.revisions
        if option.revision_id == revision.id
    )
    runtime_options = options.list_runtime_options()
    selected = next((option for option in runtime_options if option.available), None)
    enabled = selected is not None and revision_option.available
    if selected is None:
        selected = runtime_options[0]

    prompt = "同步新增或新获得访问权限的 DingTalk AI 听记，并使用 $ceo-minutes-sync 输出可核验结果。"
    disabled_reasons: list[str] = []
    if not any(option.available for option in runtime_options):
        disabled_reasons.append(
            "没有健康且已配置的 Runtime（"
            + _runtime_unavailable_summary(runtime_options)
            + "）"
        )
    if not revision_option.available:
        disabled_reasons.append(
            "精确 repository Skill revision 当前不可用（"
            + (revision_option.unavailable_reason or "unavailable")
            + "）"
        )
    if disabled_reasons:
        prompt += (
            "\n\n未启用："
            + "；".join(disabled_reasons)
            + "。请先修复 Runtime 或加载精确 Skill revision，再编辑并启用此任务。"
        )

    task = store.create_scheduled_task(
        migration_key=MINUTES_SYNC_MIGRATION_KEY,
        name="每天同步 AI 听记",
        prompt=prompt,
        cron_expression="0 0 20 * * *",
        timezone_name="Asia/Shanghai",
        runtime_id=selected.route_name,
        runtime_options={"model": selected.model},
        working_directory=str(working_directory.expanduser().resolve()),
        skill_refs=(
            ScheduledTaskSkillRef(
                skill_source="managed",
                skill_name=skill.name,
                managed_skill_id=skill.id,
                managed_revision_id=revision.id,
                position=0,
            ),
        ),
        enabled=enabled,
        now=now,
    )
    return task


def _existing_task(
    store: AutoReplyStore, migration_key: str
) -> ScheduledTask | None:
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
) -> tuple[RuntimeOption, str | None]:
    runtime_options = options.list_runtime_options()
    selected = next((option for option in runtime_options if option.available), None)
    if selected is not None:
        return selected, None
    return (
        runtime_options[0],
        "没有健康且已配置的 Runtime（"
        + _runtime_unavailable_summary(runtime_options)
        + "）",
    )


def _managed_ref(
    *,
    store: AutoReplyStore,
    options: ScheduledTaskOptionService,
    name: str,
    position: int,
) -> tuple[ScheduledTaskSkillRef, str | None]:
    skill = store.get_managed_skill_by_name(name)
    if skill is None:
        raise ValueError(f"{name} repository managed Skill is missing")
    repository_content = repository_managed_skill_content(name)
    repository_sha256 = hashlib.sha256(repository_content.encode("utf-8")).hexdigest()
    revision = next(
        (
            revision
            for revision in reversed(store.list_managed_skill_revisions(skill.id))
            if revision.source == REPOSITORY_IMPORT_SOURCE
            and revision.sha256 == repository_sha256
            and revision.content == repository_content
        ),
        None,
    )
    if revision is None:
        raise ValueError(f"{name} repository revision is missing")
    managed_option = next(
        option
        for option in options.list_managed_skill_options()
        if option.skill_id == skill.id
    )
    revision_option = next(
        option
        for option in managed_option.revisions
        if option.revision_id == revision.id
    )
    reason = None
    if not revision_option.available:
        reason = (
            f"精确 repository Skill revision {name} 当前不可用（"
            + (revision_option.unavailable_reason or "unavailable")
            + "）"
        )
    return (
        ScheduledTaskSkillRef(
            skill_source="managed",
            skill_name=name,
            managed_skill_id=skill.id,
            managed_revision_id=revision.id,
            position=position,
        ),
        reason,
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
