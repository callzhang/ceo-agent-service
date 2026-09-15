"""Deterministic service commands that a scheduled task runs without an Agent.

A service command is an in-process operation of this service (the same
operation the matching ``app.cli`` subcommand performs).  The catalog below is
the only source of command names that a scheduled task may reference; the
running dispatcher binds every catalogued name to its implementation exactly
once at startup.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
import re
from typing import Literal


SERVICE_COMMAND_EXECUTION_KIND = "service_command"
SERVICE_COMMAND_CONSUMER_CONTEXT_KEY = "scheduled_consumer"
_SKILL_REFERENCE_PATTERN = re.compile(r"\$([A-Za-z0-9][A-Za-z0-9_-]*)")

ServiceCommandChannel = Literal[
    "dingtalk",
    "email",
    "wechat",
    "meeting",
    "work_summary",
]


@dataclass(frozen=True)
class ServiceCommandOption:
    name: str
    display_name: str
    description: str
    channel: ServiceCommandChannel
    """The reply-task channel the command produces; it decides which consumer runs."""
    consumer_prompt_enabled: bool
    """Whether outputs of this trigger are interpreted by a downstream Agent."""


@dataclass(frozen=True)
class ServiceCommandConsumerContext:
    """Immutable prompt and exact Skill material for outputs of one Cron run."""

    scheduled_task_id: int
    scheduled_task_run_id: int
    prompt: str
    skill_names: tuple[str, ...]
    skill_protocol: str

    def to_payload(self) -> dict[str, object]:
        return {
            "schema": "scheduled_consumer.v1",
            "scheduled_task_id": self.scheduled_task_id,
            "scheduled_task_run_id": self.scheduled_task_run_id,
            "prompt": self.prompt,
            "skill_names": list(self.skill_names),
            "skill_protocol": self.skill_protocol,
        }

    @classmethod
    def from_payload(cls, value: object) -> ServiceCommandConsumerContext | None:
        if value is None:
            return None
        if (
            not isinstance(value, dict)
            or value.get("schema") != "scheduled_consumer.v1"
        ):
            raise ValueError("scheduled consumer context is invalid")
        task_id = value.get("scheduled_task_id")
        run_id = value.get("scheduled_task_run_id")
        prompt = value.get("prompt")
        skill_names = value.get("skill_names")
        skill_protocol = value.get("skill_protocol")
        if (
            not isinstance(task_id, int)
            or task_id <= 0
            or not isinstance(run_id, int)
            or run_id <= 0
            or not isinstance(prompt, str)
            or not prompt.strip()
            or not isinstance(skill_names, list)
            or not skill_names
            or any(
                not isinstance(name, str) or not name.strip() for name in skill_names
            )
            or not isinstance(skill_protocol, str)
            or not skill_protocol.strip()
        ):
            raise ValueError("scheduled consumer context is invalid")
        return cls(
            scheduled_task_id=task_id,
            scheduled_task_run_id=run_id,
            prompt=prompt,
            skill_names=tuple(skill_names),
            skill_protocol=skill_protocol,
        )


_ACTIVE_CONSUMER_CONTEXT: ContextVar[ServiceCommandConsumerContext | None] = ContextVar(
    "scheduled_service_command_consumer_context", default=None
)


def current_service_command_consumer_context() -> ServiceCommandConsumerContext | None:
    return _ACTIVE_CONSUMER_CONTEXT.get()


def consumer_skill_names_from_prompt(prompt: str) -> tuple[str, ...]:
    """Extract unique ``$skill`` names in their semantic prompt order."""
    return tuple(dict.fromkeys(_SKILL_REFERENCE_PATTERN.findall(prompt)))


SERVICE_COMMAND_OPTIONS: tuple[ServiceCommandOption, ...] = (
    ServiceCommandOption(
        name="email-message-check-once",
        display_name="读取新邮件",
        description="读取已配置未分类入口中的新未读邮件；发现后由 Agent 按邮件分类规则判断业务类别和重要性。",
        channel="email",
        consumer_prompt_enabled=True,
    ),
    ServiceCommandOption(
        name="produce-once",
        display_name="读取新钉钉消息",
        description="读取新的单聊和群聊 @ 消息；发现后由 Agent 根据最新上下文决定是否回复、表态、澄清或不处理。",
        channel="dingtalk",
        consumer_prompt_enabled=True,
    ),
    ServiceCommandOption(
        name="calendar-invites-once",
        display_name="读取新日历邀请",
        description="读取新的钉钉日历邀请；由 Agent 核验邀请详情和日程冲突，决定接受、暂定、拒绝或向邀请人澄清。",
        channel="dingtalk",
        consumer_prompt_enabled=True,
    ),
    ServiceCommandOption(
        name="wechat-produce-once",
        display_name="读取新微信消息",
        description="读取已启用好友和群聊 @ 的新消息；仅按已配置范围和发送模式交由 Agent 判断是否回复。",
        channel="wechat",
        consumer_prompt_enabled=True,
    ),
    ServiceCommandOption(
        name="scan-meetings-once",
        display_name="读取已结束会议",
        description="读取已结束且会议资料可用的钉钉会议；由 Agent 整理结论、分歧、行动项和必要的会后澄清。",
        channel="meeting",
        consumer_prompt_enabled=True,
    ),
    ServiceCommandOption(
        name="scan-oa-approvals",
        display_name="读取待审批 OA",
        description="读取新的或有新处理记录的待审批 OA；由 Agent 审阅材料与审批流水，作出处理或评论补充要求。",
        channel="dingtalk",
        consumer_prompt_enabled=True,
    ),
    ServiceCommandOption(
        name="scan-meeting-todos-once",
        display_name="读取会议行动项",
        description="读取钉钉会议中新增或修改的行动项；由 Agent 核验证据，并在 Tasks 中创建或更新需要持续跟进的任务。",
        channel="work_summary",
        consumer_prompt_enabled=True,
    ),
    ServiceCommandOption(
        name="sync-minutes-once",
        display_name="同步听记到工作区",
        description="归档尚未归档且可访问的钉钉 AI 听记，把可用摘要和逐字稿保存到工作区；权限受限或内容不可读时保留同步状态。",
        channel="work_summary",
        consumer_prompt_enabled=False,
    ),
    ServiceCommandOption(
        name="weekly-okr-report",
        display_name="生成并发送 OKR 周报",
        description="读取所有管理者的实时 OKR，由 Agent 结合工作证据分析后生成周报，并发布到管理知识库和 CEO-2 管理群。",
        channel="dingtalk",
        consumer_prompt_enabled=False,
    ),
    ServiceCommandOption(
        name="recover-recent-messages",
        display_name="补查近期钉钉消息",
        description="扩大读取范围，找回常规检查可能遗漏的单聊、群聊 @ 消息和原地更新的日历邀请；发现后由 Agent 按对应规则处理。",
        channel="dingtalk",
        consumer_prompt_enabled=True,
    ),
)


def service_command_option(name: str) -> ServiceCommandOption:
    for option in SERVICE_COMMAND_OPTIONS:
        if option.name == name:
            return option
    raise ValueError(f"service command {name}: service_command_not_registered")


class ServiceCommandRegistry:
    """Bind every catalogued service command to one in-process implementation."""

    def __init__(self, implementations: Mapping[str, Callable[[], str]]) -> None:
        expected = {option.name for option in SERVICE_COMMAND_OPTIONS}
        if set(implementations) != expected:
            raise ValueError(
                "service command implementations must match the catalog exactly"
            )
        self._implementations = dict(implementations)

    def run(
        self,
        name: str,
        *,
        consumer_context: ServiceCommandConsumerContext | None = None,
    ) -> str:
        """Run one catalogued command and return its one-line result summary."""
        token = _ACTIVE_CONSUMER_CONTEXT.set(consumer_context)
        try:
            return self._implementations[service_command_option(name).name]()
        finally:
            _ACTIVE_CONSUMER_CONTEXT.reset(token)
