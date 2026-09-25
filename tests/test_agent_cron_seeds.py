from __future__ import annotations

from dataclasses import replace
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
from app.agent_cron.scheduler import (
    AgentCronScheduler,
    ExecutionTerminalResolverRegistry,
)
from app.agent_cron.seeds import (
    DINGTALK_MESSAGE_LEGACY_CONSUMER_PROMPT,
    seed_scheduled_tasks,
)
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


def _cli_command(store: AutoReplyStore, working_directory: Path, command: str) -> str:
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
        if (revision := store.get_managed_skill_revision(binding.revision_id))
        is not None
    )
    operation_root = tmp_path / "operation-skills"
    required_operation_skills = {
        "dingtalk-chat",
        "dingtalk-minutes",
        "dingtalk-wiki",
        "dingtalk-doc",
        "dingtalk-calendar",
        "dingtalk-oa-approval",
        "stardust-oa-finance-review",
        "stardust-oa-project-review",
        "stardust-oa-contract-review",
        "stardust-oa-people-review",
        "stardust-oa-attendance-travel-review",
        "stardust-oa-cloud-resource-review",
        *operation_skills,
    }
    for name in required_operation_skills:
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
        "email-message-check-v1",
        "dingtalk-meeting-check-v1",
        "dingtalk-oa-check-v1",
        "work-source-scan-daily-v1",
    }
)


READABLE_BUILTIN_COPY = {
    "task-memory-write-v1": (
        "写入任务长期记忆",
        "把已结束任务的执行结果里指明的长期信息写入 Memory。Derek 2026-09-24：由执行 Agent 给出、系统写入、不再审核；失败按退避重试，重试上限后进 Attention。",
    ),
    "ceo-daily-report-daily-v1": (
        "发送 CEO 每日总结",
        "每晚汇总当天的会议、Tasks 项目变化、已处理和等你处理的事项，并扫描当天群消息，写成重要进展、风险、需介入、需关注和管理建议；发布为钉钉文档，由机器人单聊把要点和链接发给 Derek。",
    ),
    "ceo-weekly-report-saturday-v1": (
        "准备 CEO 管理周报",
        "按本周的听记、群消息和四条业务线来源报告整理 CEO 管理周报草稿，核对上期未结问题，产出可校验的报告数据；发布到钉钉文档需要 Derek 明确授权，本任务不自行发布。",
    ),
    "ceo-minutes-access-daily-v1": (
        "申请读不到的钉钉 AI 听记",
        "读取听记管理后台，对本账号读不到的听记逐条在其页面提交查看权限申请；对方同意后由听记下载任务归档。需要组织管理员身份和一份已登录的后台会话。",
    ),
    "email-message-check-v1": (
        "分类新邮件",
        "发现已配置未分类入口中的新未读邮件后，由 Agent 按邮件分类规则判断业务类别和重要性；分类结果再由现有执行队列按邮箱策略处理。",
    ),
    "dingtalk-message-check-v1": (
        "处理新的钉钉消息",
        "发现新的单聊或群聊 @ 消息后，由 Agent 读取最新上下文，决定回复、表态、澄清或不处理。",
    ),
    "dingtalk-calendar-invite-check-v1": (
        "处理新的钉钉日历邀请",
        "发现新的会议邀请后，由 Agent 读取最新邀请和日程冲突，决定接受、暂定、拒绝或向邀请人澄清；执行后核验日历状态。",
    ),
    "dingtalk-meeting-check-v1": (
        "同步会议结论与管理者视角",
        "会议结束且听记可用后，由 Agent 总结关键结论、分歧和行动项，确保管理者观点被准确传达；发现未对齐时发送会后澄清。",
    ),
    "wechat-message-check-v1": (
        "处理已授权会话的新微信消息",
        "发现已启用的好友新消息或群聊 @ 后，由 Agent 判断是否回复；仅按已配置范围和发送模式处理，不读取或回复其他会话。",
    ),
    "dingtalk-oa-check-v1": (
        "处理新的钉钉 OA 审批",
        "发现新的或有新处理记录的待审批 OA 后，由 Agent 读取完整材料与审批流水，判断同意、拒绝或评论补充要求，并在执行后核验结果。",
    ),
    "work-source-scan-daily-v1": (
        "将会议行动项整理到 Tasks",
        "发现钉钉会议中新增或修改的行动项后，由 Agent 核验任务内容、负责人证据、截止时间和现有 Tasks，创建或更新需要持续跟进的任务；不因参会或发言推断负责人。",
    ),
    "weekly-okr-report-sunday-v1": (
        "生成并发送每周 OKR 管理周报",
        "读取所有管理者的实时 OKR，由 Agent 结合工作证据分析进展、风险、领导力和文化表现，生成周报并发布到管理知识库和 CEO-2 管理群。",
    ),
    "ceo-minutes-sync-daily-v1": (
        "下载新增的钉钉 AI 听记",
        "发现尚未归档且可访问的钉钉 AI 听记后，读取可用的摘要和逐字稿并归档到工作区；权限受限或内容不可读时保留同步状态，待后续检查。",
    ),
    "dingtalk-message-recovery-v1": (
        "补查遗漏的钉钉消息和日历更新",
        "扩大读取范围，找回常规检查遗漏的单聊、群聊 @ 消息和原地更新的日历邀请；发现后由 Agent 按对应的消息或日程规则处理。",
    ),
    "follow-up-delivery-v1": (
        "投递到期的跟进事项",
        "把已到期的跟进事项按既有投递规则发出；只投递已生成的内容，不产生新的判断。Derek 2026-09-18 要求它作为定时任务可见可开关，而不是隐藏的常驻循环。",
    ),
}


def test_seed_uses_readable_copy_for_every_builtin_task(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "readable-copy.sqlite3")
    options = _options(
        tmp_path,
        store,
        healthy_routes={"codex_oauth"},
        operation_skills=(
            "dingtalk-minutes",
            "dingtalk-calendar",
            "dingtalk-oa-approval",
        ),
    )

    tasks = seed_scheduled_tasks(
        store=store, options=options, working_directory=tmp_path, now=NOW
    )

    assert {
        task.migration_key: (task.name, task.description) for task in tasks
    } == READABLE_BUILTIN_COPY


@pytest.mark.parametrize(
    ("name", "description", "expected_name", "expected_description"),
    [
        (
            "每天同步 AI 听记",
            "同步 AI 听记的摘要、逐字稿和归档游标到工作区。",
            "下载新增的钉钉 AI 听记",
            "发现尚未归档且可访问的钉钉 AI 听记后，读取可用的摘要和逐字稿并归档到工作区；权限受限或内容不可读时保留同步状态，待后续检查。",
        ),
        (
            "我的听记归档",
            "同步 AI 听记的摘要、逐字稿和归档游标到工作区。",
            "我的听记归档",
            "发现尚未归档且可访问的钉钉 AI 听记后，读取可用的摘要和逐字稿并归档到工作区；权限受限或内容不可读时保留同步状态，待后续检查。",
        ),
        (
            "每天同步 AI 听记",
            "仅同步指定项目的听记。",
            "下载新增的钉钉 AI 听记",
            "仅同步指定项目的听记。",
        ),
    ],
)
def test_reseed_upgrades_each_untouched_default_copy_field_independently(
    tmp_path: Path,
    name: str,
    description: str,
    expected_name: str,
    expected_description: str,
) -> None:
    store = AutoReplyStore(tmp_path / "legacy-minutes-copy.sqlite3")
    original = store.create_scheduled_task(
        migration_key="ceo-minutes-sync-daily-v1",
        name=name,
        description=description,
        command="sync-minutes-once",
        cron_expression="0 17 4 * * *",
        timezone_name="America/Los_Angeles",
        enabled=False,
        now=NOW,
    )
    options = _options(tmp_path, store, healthy_routes={"codex_oauth"})

    reseeded = _task_by_key(
        seed_scheduled_tasks(
            store=store, options=options, working_directory=tmp_path, now=NOW
        ),
        "ceo-minutes-sync-daily-v1",
    )

    assert reseeded.id == original.id
    assert (reseeded.name, reseeded.description) == (
        expected_name,
        expected_description,
    )
    assert reseeded.cron_expression == original.cron_expression
    assert reseeded.timezone_name == original.timezone_name
    assert reseeded.command == original.command
    assert reseeded.prompt == original.prompt
    assert reseeded.skill_refs == original.skill_refs
    assert reseeded.runtime_id == original.runtime_id
    assert reseeded.runtime_options == original.runtime_options
    assert reseeded.enabled is original.enabled


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

    assert len(producers) == 4
    assert not any(task.enabled for task in producers)
    assert all(task.command for task in producers)
    assert all(task.runtime_id == "" for task in producers)
    assert all(task.required_runtime_capabilities == () for task in producers)


def test_friday_only_still_creates_fixed_discovery_seeds(
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

    assert len(producers) == 4
    assert not any(task.enabled for task in producers)
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
    assert "$dingtalk-oa-approval" in repeated.prompt
    assert "$stardust-oa-finance-review" in repeated.prompt
    assert "$stardust-oa-project-review" in repeated.prompt
    assert "$stardust-oa-contract-review" in repeated.prompt
    assert "$stardust-oa-people-review" in repeated.prompt
    assert "$stardust-oa-attendance-travel-review" in repeated.prompt
    assert "$stardust-oa-cloud-resource-review" in repeated.prompt
    assert "只依据通用审批 Skill 与匹配的 Stardust 业务 Skill" in repeated.prompt
    assert "rule_coverage" in repeated.prompt
    assert "needs_human" in repeated.prompt
    assert [ref.skill_name for ref in repeated.skill_refs] == [
        "dingtalk-oa-approval",
        "stardust-oa-finance-review",
        "stardust-oa-project-review",
        "stardust-oa-contract-review",
        "stardust-oa-people-review",
        "stardust-oa-attendance-travel-review",
        "stardust-oa-cloud-resource-review",
    ]
    assert repeated.runtime_id == ""
    assert repeated.command == "scan-oa-approvals"
    assert repeated.enabled is False
    assert repeated.version == original.version + 1
    assert repeated.required_runtime_capabilities == ()


def test_oa_seed_binds_generic_and_stardust_finance_review_skills(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "oa-finance-skill.sqlite3")
    options = _options(
        tmp_path,
        store,
        healthy_routes={"codex_oauth"},
        operation_skills=("stardust-oa-finance-review",),
    )

    task = _task_by_key(
        seed_scheduled_tasks(
            store=store,
            options=options,
            working_directory=tmp_path,
            now=NOW,
        ),
        "dingtalk-oa-check-v1",
    )

    assert "$dingtalk-oa-approval" in task.prompt
    assert "$stardust-oa-finance-review" in task.prompt
    assert "$stardust-oa-project-review" in task.prompt
    assert "$stardust-oa-contract-review" in task.prompt
    assert "$stardust-oa-people-review" in task.prompt
    assert "$stardust-oa-attendance-travel-review" in task.prompt
    assert "$stardust-oa-cloud-resource-review" in task.prompt
    assert "只依据通用审批 Skill 与匹配的 Stardust 业务 Skill" in task.prompt
    assert [ref.skill_name for ref in task.skill_refs] == [
        "dingtalk-oa-approval",
        "stardust-oa-finance-review",
        "stardust-oa-project-review",
        "stardust-oa-contract-review",
        "stardust-oa-people-review",
        "stardust-oa-attendance-travel-review",
        "stardust-oa-cloud-resource-review",
    ]


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
    assert (task.name, task.description) == READABLE_BUILTIN_COPY[
        "dingtalk-message-check-v1"
    ]
    assert task.cron_expression == "0 * * * * *"
    assert task.timezone_name == "Asia/Shanghai"
    assert task.command == "produce-once"
    assert task.enabled is False
    assert all(
        skill_name in task.prompt
        for skill_name in (
            "$ceo-message-triage",
            "$dingtalk-chat",
        )
    )
    assert task.runtime_id == ""
    assert [ref.skill_name for ref in task.skill_refs] == [
        "ceo-message-triage",
        "dingtalk-chat",
    ]
    assert task.runtime_options == {} and task.required_runtime_capabilities == ()
    assert task.working_directory == ""


def test_seed_creates_email_discovery_trigger_with_only_classifier_skill(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "email-message.sqlite3")
    options = _options(tmp_path, store, healthy_routes={"codex_oauth"})

    task = _task_by_key(
        seed_scheduled_tasks(
            store=store, options=options, working_directory=tmp_path, now=NOW
        ),
        "email-message-check-v1",
    )

    assert (task.name, task.description) == READABLE_BUILTIN_COPY[
        "email-message-check-v1"
    ]
    assert task.command == "email-message-check-once"
    assert task.cron_expression == "0 * * * * *"
    assert task.timezone_name == "Asia/Shanghai"
    assert task.enabled is False
    assert "$ceo-email-classifier" in task.prompt
    assert [ref.skill_name for ref in task.skill_refs] == ["ceo-email-classifier"]
    assert task.runtime_id == ""
    assert task.runtime_options == {}
    assert task.required_runtime_capabilities == ()
    assert task.working_directory == ""


def test_seed_creates_calendar_invitation_trigger_separately_from_messages(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "calendar-invitation.sqlite3")
    options = _options(
        tmp_path,
        store,
        healthy_routes={"codex_oauth"},
        operation_skills=("dingtalk-chat", "dingtalk-calendar"),
    )

    task = _task_by_key(
        seed_scheduled_tasks(
            store=store, options=options, working_directory=tmp_path, now=NOW
        ),
        "dingtalk-calendar-invite-check-v1",
    )

    assert (task.name, task.description) == READABLE_BUILTIN_COPY[
        "dingtalk-calendar-invite-check-v1"
    ]
    assert task.command == "calendar-invites-once"
    assert task.cron_expression == "10 * * * * *"
    assert task.timezone_name == "Asia/Shanghai"
    assert [ref.skill_name for ref in task.skill_refs] == [
        "ceo-calendar-invite",
        "dingtalk-calendar",
        "dingtalk-chat",
    ]
    assert all(
        skill_name in task.prompt
        for skill_name in (
            "$ceo-calendar-invite",
            "$dingtalk-calendar",
            "$dingtalk-chat",
        )
    )
    assert "$ceo-message-triage" not in task.prompt


def test_seed_replaces_only_the_exact_old_message_consumer_configuration(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "calendar-prompt-migration.sqlite3")
    options = _options(
        tmp_path,
        store,
        healthy_routes={"codex_oauth"},
        operation_skills=("dingtalk-chat", "dingtalk-calendar"),
    )
    initial = seed_scheduled_tasks(
        store=store, options=options, working_directory=tmp_path, now=NOW
    )
    message = _task_by_key(initial, "dingtalk-message-check-v1")
    recovery = _task_by_key(initial, "dingtalk-message-recovery-v1")
    old_default = store.update_scheduled_task(
        message.id,
        expected_version=message.version,
        name="检查 DingTalk 消息",
        description="增量检查 DingTalk 消息，并将新消息送入统一处理队列。",
        prompt=DINGTALK_MESSAGE_LEGACY_CONSUMER_PROMPT,
        skill_refs=tuple(
            replace(ref, scheduled_task_id=0)
            for ref in recovery.skill_refs
        ),
        now=NOW + timedelta(minutes=1),
    )

    migrated = _task_by_key(
        seed_scheduled_tasks(
            store=store,
            options=options,
            working_directory=tmp_path,
            now=NOW + timedelta(minutes=2),
        ),
        "dingtalk-message-check-v1",
    )

    assert migrated.id == old_default.id
    assert migrated.prompt != DINGTALK_MESSAGE_LEGACY_CONSUMER_PROMPT
    assert [ref.skill_name for ref in migrated.skill_refs] == [
        "ceo-message-triage",
        "dingtalk-chat",
    ]
    assert "$ceo-calendar-invite" not in migrated.prompt


def _legacy_message_agent_task(
    store, options, tmp_path, *, migration_key="dingtalk-message-check-v1", enabled=True
):
    dingtalk_chat = next(
        item
        for item in options.list_operation_skill_options()
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
        tmp_path,
        store,
        healthy_routes={"codex_oauth"},
        operation_skills=("dingtalk-chat",),
    )
    legacy = _legacy_message_agent_task(store, options, tmp_path, enabled=False)

    seeded = _task_by_key(
        seed_scheduled_tasks(
            store=store,
            options=options,
            working_directory=tmp_path,
            now=NOW + timedelta(minutes=1),
        ),
        "dingtalk-message-check-v1",
    )

    assert seeded.id == legacy.id
    assert seeded.version == legacy.version + 1
    assert seeded.command == "produce-once"
    assert all(
        skill_name in seeded.prompt
        for skill_name in (
            "$ceo-message-triage",
            "$dingtalk-chat",
        )
    )
    assert seeded.runtime_id == ""
    assert [ref.skill_name for ref in seeded.skill_refs] == [
        "ceo-message-triage",
        "dingtalk-chat",
    ]
    assert seeded.required_runtime_capabilities == ()
    assert seeded.working_directory == ""
    assert seeded.name == legacy.name
    assert seeded.cron_expression == "0 */2 * * * *"
    # Seeding never switches a task on: the legacy task was paused, and the
    # command form keeps it paused (Derek, 2026-09-23).
    assert seeded.enabled is False
    assert store.list_scheduled_tasks(include_deleted=True).count(seeded) == 1
    again = _task_by_key(
        seed_scheduled_tasks(
            store=store,
            options=options,
            working_directory=tmp_path,
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
        tmp_path,
        store,
        healthy_routes={"codex_oauth"},
        operation_skills=("dingtalk-chat",),
    )
    legacy = _legacy_message_agent_task(store, options, tmp_path)
    deleted = store.delete_scheduled_task(
        legacy.id, expected_version=legacy.version, now=NOW + timedelta(minutes=1)
    )

    seeded = _task_by_key(
        seed_scheduled_tasks(
            store=store,
            options=options,
            working_directory=tmp_path,
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

    assert (task.name, task.description) == READABLE_BUILTIN_COPY[
        "dingtalk-meeting-check-v1"
    ]
    assert task.cron_expression == "0 */10 * * * *"
    assert task.command == "scan-meetings-once"
    assert "$ceo-meeting-work" in task.prompt
    assert "$dingtalk-minutes" in task.prompt
    assert "$dingtalk-calendar" in task.prompt
    assert task.runtime_id == ""
    assert task.runtime_options == {}
    assert task.required_runtime_capabilities == ()
    assert task.working_directory == ""
    assert [ref.skill_name for ref in task.skill_refs] == [
        "ceo-meeting-work",
        "dingtalk-minutes",
        "dingtalk-calendar",
    ]
    assert task.enabled is False


def test_every_fixed_discovery_check_is_a_service_command(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "fixed-checks.sqlite3")
    options = _options(
        tmp_path,
        store,
        healthy_routes=set(),
        operation_skills=(
            "dingtalk-minutes",
            "dingtalk-calendar",
            "dingtalk-oa-approval",
        ),
    )

    tasks = seed_scheduled_tasks(
        store=store,
        options=options,
        working_directory=tmp_path,
        now=NOW,
    )

    expected_commands = {
        "email-message-check-v1": "email-message-check-once",
        "dingtalk-message-check-v1": "produce-once",
        "dingtalk-calendar-invite-check-v1": "calendar-invites-once",
        "dingtalk-message-recovery-v1": "recover-recent-messages",
        "dingtalk-meeting-check-v1": "scan-meetings-once",
        "wechat-message-check-v1": "wechat-produce-once",
        "dingtalk-oa-check-v1": "scan-oa-approvals",
        "work-source-scan-daily-v1": "scan-meeting-todos-once",
    }
    expected_skills = {
        "email-message-check-v1": ["ceo-email-classifier"],
        "dingtalk-message-check-v1": [
            "ceo-message-triage",
            "dingtalk-chat",
        ],
        "dingtalk-calendar-invite-check-v1": [
            "ceo-calendar-invite",
            "dingtalk-calendar",
            "dingtalk-chat",
        ],
        "dingtalk-message-recovery-v1": [
            "ceo-message-triage",
            "ceo-calendar-invite",
            "dingtalk-chat",
            "dingtalk-calendar",
        ],
        "dingtalk-meeting-check-v1": [
            "ceo-meeting-work",
            "dingtalk-minutes",
            "dingtalk-calendar",
        ],
        "wechat-message-check-v1": ["ceo-wechat"],
        "dingtalk-oa-check-v1": [
            "dingtalk-oa-approval",
            "stardust-oa-finance-review",
            "stardust-oa-project-review",
            "stardust-oa-contract-review",
            "stardust-oa-people-review",
            "stardust-oa-attendance-travel-review",
            "stardust-oa-cloud-resource-review",
        ],
        "work-source-scan-daily-v1": [
            "ceo-meeting-work",
            "ceo-work-tracking",
            "dingtalk-minutes",
        ],
    }
    for migration_key, command in expected_commands.items():
        task = _task_by_key(tasks, migration_key)
        assert task.command == command
        assert task.prompt
        assert task.runtime_id == ""
        assert task.runtime_options == {}
        assert task.required_runtime_capabilities == ()
        assert task.working_directory == ""
        assert [ref.skill_name for ref in task.skill_refs] == expected_skills[
            migration_key
        ]
        assert task.enabled is False

    # Every discovery check is a service command. The weekly and daily reports
    # are the Agent tasks: which meetings matter and what the evidence
    # supports is judgement, not a fixed rule.
    weekly_report = _task_by_key(tasks, "ceo-weekly-report-saturday-v1")
    daily_report = _task_by_key(tasks, "ceo-daily-report-daily-v1")
    assert all(
        task.command
        for task in tasks
        if task.migration_key
        not in {weekly_report.migration_key, daily_report.migration_key}
    )
    assert daily_report.command == "" and daily_report.runtime_id
    assert daily_report.cron_expression == "0 0 21 * * *"
    assert daily_report.timezone_name == "Asia/Shanghai"
    assert daily_report.enabled is False
    assert [ref.skill_name for ref in daily_report.skill_refs] == [
        "ceo-daily-report",
        "dingtalk-chat",
        "dingtalk-minutes",
        "dingtalk-wiki",
        "dingtalk-doc",
    ]
    assert "-m app.cli daily-report-facts --scheduled-run" in daily_report.prompt
    assert weekly_report.command == "" and weekly_report.runtime_id
    assert weekly_report.cron_expression == "0 0 12 * * 6"
    assert [ref.skill_name for ref in weekly_report.skill_refs] == [
        "ceo-weekly-report",
        "dingtalk-minutes",
        "dingtalk-chat",
    ]
    minutes = _task_by_key(tasks, "ceo-minutes-sync-daily-v1")
    assert minutes.command == "sync-minutes-once"
    assert minutes.skill_refs == ()
    okr = _task_by_key(tasks, "weekly-okr-report-sunday-v1")
    assert okr.command == "weekly-okr-report"
    assert okr.skill_refs == ()


def test_seed_creates_wechat_existing_producer_every_five_minutes(
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

    assert (task.name, task.description) == READABLE_BUILTIN_COPY[
        "wechat-message-check-v1"
    ]
    assert task.cron_expression == "0 */5 * * * *"
    assert task.timezone_name == "Asia/Shanghai"
    assert task.command == "wechat-produce-once"
    assert task.enabled is False
    assert "$ceo-wechat" in task.prompt and task.runtime_id == ""
    assert [ref.skill_name for ref in task.skill_refs] == ["ceo-wechat"]
    assert task.runtime_options == {} and task.required_runtime_capabilities == ()


def test_startup_seed_keeps_untouched_legacy_wechat_agent_task_paused_as_a_command(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "legacy-wechat-agent.sqlite3")
    options = _options(
        tmp_path,
        store,
        healthy_routes={"codex_oauth"},
        operation_skills=("dingtalk-chat",),
    )
    untouched = _legacy_message_agent_task(
        store, options, tmp_path, migration_key="wechat-message-check-v1", enabled=False
    )
    edited_seed = _legacy_message_agent_task(
        store,
        options,
        tmp_path,
        migration_key="dingtalk-message-check-v1",
        enabled=True,
    )
    edited = store.set_scheduled_task_enabled(
        edited_seed.id, enabled=False, expected_version=edited_seed.version, now=NOW
    )

    seeded = seed_scheduled_tasks(
        store=store,
        options=options,
        working_directory=tmp_path,
        now=NOW + timedelta(minutes=1),
    )

    wechat = _task_by_key(seeded, "wechat-message-check-v1")
    assert wechat.id == untouched.id and untouched.version == 1
    assert wechat.command == "wechat-produce-once"
    assert wechat.enabled is False
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

    assert (task.name, task.description) == READABLE_BUILTIN_COPY[
        "dingtalk-oa-check-v1"
    ]
    assert task.cron_expression == "0 0 * * * *"
    assert task.command == "scan-oa-approvals"
    assert "$dingtalk-oa-approval" in task.prompt
    assert "$stardust-oa-finance-review" in task.prompt
    assert task.runtime_id == ""
    assert task.runtime_options == {}
    assert task.required_runtime_capabilities == ()
    assert task.working_directory == ""
    assert [ref.skill_name for ref in task.skill_refs] == [
        "dingtalk-oa-approval",
        "stardust-oa-finance-review",
        "stardust-oa-project-review",
        "stardust-oa-contract-review",
        "stardust-oa-people-review",
        "stardust-oa-attendance-travel-review",
        "stardust-oa-cloud-resource-review",
    ]
    assert task.enabled is False


def test_reseed_renames_untouched_oa_default_so_the_approval_task_is_discoverable(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "oa-readable-name.sqlite3")
    original = store.create_scheduled_task(
        migration_key="dingtalk-oa-check-v1",
        name="审阅新的或有进展的钉钉 OA",
        description="发现新的或有新处理记录的待审批 OA 后，由 Agent 读取完整材料与审批流水，判断同意、拒绝或评论补充要求，并在执行后核验结果。",
        prompt="",
        command="scan-oa-approvals",
        cron_expression="0 0 * * * *",
        timezone_name="Asia/Shanghai",
        enabled=True,
        now=NOW,
    )
    options = _options(
        tmp_path,
        store,
        healthy_routes={"codex_oauth"},
        operation_skills=("dingtalk-oa-approval",),
    )

    reseeded = _task_by_key(
        seed_scheduled_tasks(
            store=store,
            options=options,
            working_directory=tmp_path,
            now=NOW + timedelta(minutes=1),
        ),
        "dingtalk-oa-check-v1",
    )

    assert reseeded.id == original.id
    assert reseeded.name == "处理新的钉钉 OA 审批"
    assert reseeded.description == original.description
    assert reseeded.command == "scan-oa-approvals"
    assert "$dingtalk-oa-approval" in reseeded.prompt
    assert "$stardust-oa-finance-review" in reseeded.prompt
    assert [ref.skill_name for ref in reseeded.skill_refs] == [
        "dingtalk-oa-approval",
        "stardust-oa-finance-review",
        "stardust-oa-project-review",
        "stardust-oa-contract-review",
        "stardust-oa-people-review",
        "stardust-oa-attendance-travel-review",
        "stardust-oa-cloud-resource-review",
    ]


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

    assert (task.name, task.description) == READABLE_BUILTIN_COPY[
        "work-source-scan-daily-v1"
    ]
    assert task.cron_expression == "0 0 0 * * *"
    assert task.command == "scan-meeting-todos-once"
    assert "$ceo-meeting-work" in task.prompt
    assert "$ceo-work-tracking" in task.prompt
    assert task.runtime_id == ""
    assert task.runtime_options == {}
    assert task.required_runtime_capabilities == ()
    assert task.working_directory == ""
    assert [ref.skill_name for ref in task.skill_refs] == [
        "ceo-meeting-work",
        "ceo-work-tracking",
        "dingtalk-minutes",
    ]
    assert task.enabled is False


def test_reseed_migrates_workspace_scan_to_meeting_todos_in_place(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "work-source-migration.sqlite3")
    original = store.create_scheduled_task(
        migration_key="work-source-scan-daily-v1",
        name="整理工作区中的新工作记录",
        description="发现工作区中新建或修改的 Markdown、文本文件后，由 Agent 判断其中是否有值得持续跟进的承诺，并按证据创建或更新项目、TODO 和跟进。",
        prompt="",
        command="scan-work-sources-once",
        cron_expression="0 15 1 * * *",
        timezone_name="Asia/Shanghai",
        enabled=True,
        now=NOW,
    )
    options = _options(
        tmp_path,
        store,
        healthy_routes={"codex_oauth"},
        operation_skills=("dingtalk-minutes",),
    )

    migrated = _task_by_key(
        seed_scheduled_tasks(
            store=store,
            options=options,
            working_directory=tmp_path,
            now=NOW + timedelta(minutes=1),
        ),
        "work-source-scan-daily-v1",
    )

    assert migrated.id == original.id
    assert migrated.name == "将会议行动项整理到 Tasks"
    assert migrated.description == READABLE_BUILTIN_COPY[
        "work-source-scan-daily-v1"
    ][1]
    assert migrated.command == "scan-meeting-todos-once"
    assert migrated.cron_expression == "0 15 1 * * *"
    assert [ref.skill_name for ref in migrated.skill_refs] == [
        "ceo-meeting-work",
        "ceo-work-tracking",
        "dingtalk-minutes",
    ]


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

    assert (task.name, task.description) == READABLE_BUILTIN_COPY[
        "dingtalk-message-recovery-v1"
    ]
    assert task.command == "recover-recent-messages"
    assert task.cron_expression == "0 30 * * * *"
    assert task.timezone_name == "Asia/Shanghai"
    assert task.enabled is False
    assert all(
        skill_name in task.prompt
        for skill_name in (
            "$ceo-message-triage",
            "$dingtalk-chat",
            "$ceo-calendar-invite",
            "$dingtalk-calendar",
        )
    )
    assert task.runtime_id == ""
    assert [ref.skill_name for ref in task.skill_refs] == [
        "ceo-message-triage",
        "ceo-calendar-invite",
        "dingtalk-chat",
        "dingtalk-calendar",
    ]


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

    assert (task.name, task.description) == READABLE_BUILTIN_COPY[
        "weekly-okr-report-sunday-v1"
    ]
    assert task.cron_expression == "0 0 18 * * 0"
    # The service command owns both the headless reads and Agent analysis.
    assert task.command == "weekly-okr-report"
    assert task.prompt == ""
    assert task.skill_refs == ()
    assert task.runtime_id == ""
    assert task.enabled is False


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
    assert (
        build_parser()
        .parse_args(
            ["produce-once", "--db", str(store.path), "--workspace", str(tmp_path)]
        )
        .command
        == "produce-once"
    )
    expected = {
        "dingtalk-message-recovery-v1": "recover-recent-messages",
        "dingtalk-meeting-check-v1": "scan-meetings-once",
        "dingtalk-oa-check-v1": "scan-oa-approvals",
        "work-source-scan-daily-v1": "scan-meeting-todos-once",
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
    # Seeds start paused (Derek, 2026-09-23); switch them on to exercise dispatch.
    tasks = tuple(
        store.set_scheduled_task_enabled(
            task.id, enabled=True, expected_version=task.version, now=NOW
        )
        for task in tasks
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
        store=store,
        option_service=options,
        now=lambda: due_at,
        commands=ServiceCommandRegistry(
            {
                "email-message-check-once": (
                    lambda: produced.append("email-message-check-once") or "discovered=0"
                ),
                "produce-once": lambda: produced.append("produce-once") or "queued=0",
                "calendar-invites-once": (
                    lambda: produced.append("calendar-invites-once") or "queued=0"
                ),
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
                "scan-meeting-todos-once": (
                    lambda: produced.append("scan-meeting-todos-once") or "queued=0"
                ),
                "request-minutes-access": (
                    lambda: produced.append("request-minutes-access") or "requested=0"
                ),
                "sync-minutes-once": (
                    lambda: produced.append("sync-minutes-once") or "queued=0"
                ),
                "weekly-okr-report": (
                    lambda: produced.append("weekly-okr-report") or "status=sent"
                ),
                "process-follow-ups": (
                    lambda: produced.append("process-follow-ups") or "sent=0"
                ),
                "write-task-memories": (
                    lambda: produced.append("write-task-memories") or "claimed=0"
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
        guard = ClaimGuard(adapter=adapter, envelope=envelope, owner="seed-dispatch")
        consumer(envelope, guard)
    assert sorted(produced) == [
        "calendar-invites-once",
        "email-message-check-once",
        "process-follow-ups",
        "produce-once",
        "recover-recent-messages",
        "request-minutes-access",
        "scan-meeting-todos-once",
        "scan-meetings-once",
        "scan-oa-approvals",
        "sync-minutes-once",
        "wechat-produce-once",
        "weekly-okr-report",
        "write-task-memories",
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
    # Every discovery check is a service command running in-process on its own
    # trigger, so a Cron tick creates one scheduled reply task per report: the
    # Agent turns that prepare the weekly management report and the daily report.
    reports = {
        _task_by_key(tasks, "ceo-weekly-report-saturday-v1").prompt,
        _task_by_key(tasks, "ceo-daily-report-daily-v1").prompt,
    }
    scheduled = store.list_reply_tasks(channel="scheduled")
    assert sorted(task.trigger_text for task in scheduled) == sorted(reports)


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
    assert (
        _task_by_key(store.list_scheduled_tasks(), "ceo-minutes-sync-daily-v1")
        == edited
    )


def test_seeds_no_longer_depend_on_runtime_health(tmp_path: Path) -> None:
    """No seed needs a Runtime, so an unhealthy fleet cannot disable one.

    The weekly OKR report was the last seed that bound a Runtime, and it was
    seeded disabled with a written reason whenever no route was healthy.  As a
    service command it runs in this process, so the Cron keeps working while
    every model route is down.
    """
    store = AutoReplyStore(tmp_path / "disabled.sqlite3")
    options = _options(tmp_path, store, healthy_routes=set())

    tasks = seed_scheduled_tasks(
        store=store, options=options, working_directory=tmp_path, now=NOW
    )

    assert len(tasks) == 15
    agent_tasks = {"ceo-weekly-report-saturday-v1", "ceo-daily-report-daily-v1"}
    for task in tasks:
        assert task.enabled is False
        if task.migration_key in agent_tasks:
            continue
        assert task.command, task.migration_key
        if task.command in {
            "sync-minutes-once",
            "request-minutes-access",
            "weekly-okr-report",
            "process-follow-ups",
            "write-task-memories",
        }:
            assert task.prompt == "" and task.skill_refs == ()
        else:
            assert task.prompt and task.skill_refs
        assert task.runtime_id == ""
        assert store.list_scheduled_task_runs(task.id) == ()


def test_the_default_oa_prompt_names_no_document_and_no_personal_rule() -> None:
    """Derek, 2026-09-23: rules live only in Skills, and this repository is public.

    The default named a background principles document the agent must not read,
    and carried Derek's own approval rules. Those belong in his scheduled task,
    not in every install's default.
    """
    from app.agent_cron.seeds import OA_CONSUMER_PROMPT

    assert ".md" not in OA_CONSUMER_PROMPT
    assert "Derek" not in OA_CONSUMER_PROMPT
