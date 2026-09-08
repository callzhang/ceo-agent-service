from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path

from app.agent_cron.models import ScheduledTask, ScheduledTaskSkillRef
from app.agent_cron.options import RuntimeOption, ScheduledTaskOptionService
from app.managed_skills import (
    MINUTES_SYNC_SKILL_NAME,
    REPOSITORY_IMPORT_SOURCE,
    repository_managed_skill_content,
)
from app.store import AutoReplyStore


MINUTES_SYNC_MIGRATION_KEY = "ceo-minutes-sync-daily-v1"


def seed_scheduled_tasks(
    *,
    store: AutoReplyStore,
    options: ScheduledTaskOptionService,
    working_directory: Path,
    now: datetime | None = None,
) -> tuple[ScheduledTask, ...]:
    """Create repository-owned scheduled task defaults without overwriting edits."""
    existing = next(
        (
            task
            for task in store.list_scheduled_tasks(include_deleted=True)
            if task.migration_key == MINUTES_SYNC_MIGRATION_KEY
        ),
        None,
    )
    if existing is not None:
        return (existing,)

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
    return (task,)


def _runtime_unavailable_summary(runtime_options: tuple[RuntimeOption, ...]) -> str:
    return "; ".join(
        f"{option.route_name}={option.unavailable_reason or 'unavailable'}"
        for option in runtime_options
    )
