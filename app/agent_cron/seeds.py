from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path
import shlex

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
    prompt = (
        "检查新增的 DingTalk 消息，仅处理现有消息发现与分诊行为。"
        "使用 $ceo-message-triage 判断是否需要 CEO 关注或回复，"
        "并使用 $dingtalk-chat 读取所需上下文；不要新增复盘或摘要任务。"
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
    prompt = (
        "检查 DingTalk 中新结束且尚未处理的会议；只有当前时间达到 "
        "ended_at + 10 minutes（10 分钟）时才符合处理资格。"
        "使用 $ceo-meeting-work 执行现有"
        "会议处理流程，并按需使用 $dingtalk-minutes 与 $dingtalk-calendar；"
        "同一会议只创建一次业务输入。"
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
    database_path = shlex.quote(str(store.path.expanduser().resolve()))
    prompt = (
        "使用 $ceo-wechat 执行 `python -m app.wechat.cli produce-once --db "
        f"{database_path}`，完成现有 producer "
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
    prompt = (
        "使用 $dingtalk-oa-approval 检查新增或变化的 DingTalk OA 待审批事项，"
        "沿用现有审批扫描与处理边界，只生成现有类型的业务输入。"
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
    prompt = (
        "每天扫描现有工作来源：使用 $ceo-work-tracking 处理本地 transcripts，"
        "并使用 $dingtalk-minutes 检查听记来源；仅创建尚未入队的工作摘要输入，"
        "后续消费交给统一 Dispatcher。"
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
    prompt = (
        "使用 $ceo-weekly-report 与 $dingtang-okr-review 执行现有周报与 OKR "
        "复核流程；调用 weekly-okr-report --force，使时间资格只由本 Cron 控制，"
        "并保留原有业务证据与发送边界。"
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
