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
        if not isinstance(value, dict) or value.get("schema") != "scheduled_consumer.v1":
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
            or any(not isinstance(name, str) or not name.strip() for name in skill_names)
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


_ACTIVE_CONSUMER_CONTEXT: ContextVar[ServiceCommandConsumerContext | None] = (
    ContextVar("scheduled_service_command_consumer_context", default=None)
)


def current_service_command_consumer_context() -> ServiceCommandConsumerContext | None:
    return _ACTIVE_CONSUMER_CONTEXT.get()


def consumer_skill_names_from_prompt(prompt: str) -> tuple[str, ...]:
    """Extract unique ``$skill`` names in their semantic prompt order."""
    return tuple(dict.fromkeys(_SKILL_REFERENCE_PATTERN.findall(prompt)))


SERVICE_COMMAND_OPTIONS: tuple[ServiceCommandOption, ...] = (
    ServiceCommandOption(
        name="produce-once",
        display_name="检查钉钉消息",
        description=(
            "增量读取 DingTalk 未读消息，去重后写入 reply task，"
            "由统一 Dispatcher 继续消费。"
        ),
        channel="dingtalk",
        consumer_prompt_enabled=True,
    ),
    ServiceCommandOption(
        name="wechat-produce-once",
        display_name="检查微信消息",
        description=(
            "读取已就绪微信账号的新消息，按已配置联系人和群@边界去重后写入 reply task；"
            "Reader 不可用时只记录健康状态。"
        ),
        channel="wechat",
        consumer_prompt_enabled=True,
    ),
    ServiceCommandOption(
        name="scan-meetings-once",
        display_name="检查 DingTalk 会议",
        description=(
            "读取已结束且满足资料条件的 DingTalk 会议，去重后写入会议对齐队列；"
            "由会议 Agent 处理真实会议。"
        ),
        channel="meeting",
        consumer_prompt_enabled=True,
    ),
    ServiceCommandOption(
        name="scan-oa-approvals",
        display_name="检查 DingTalk OA 审批",
        description=(
            "增量读取待处理的 DingTalk OA 审批，去重后写入审批回复队列；"
            "由 DingTalk Consumer 处理真实审批。"
        ),
        channel="dingtalk",
        consumer_prompt_enabled=True,
    ),
    ServiceCommandOption(
        name="scan-work-sources-once",
        display_name="扫描工作来源",
        description=(
            "扫描已配置的本地工作目录，去重后写入工作摘要队列；"
            "由 Work Summary Consumer 处理真实来源。"
        ),
        channel="work_summary",
        consumer_prompt_enabled=True,
    ),
    ServiceCommandOption(
        name="sync-minutes-once",
        display_name="同步 AI 听记",
        description=(
            "增量同步 DingTalk AI 听记的摘要、逐字稿和归档游标到本地工作区；"
            "同步过程为确定性代码，不触发 Agent。"
        ),
        channel="work_summary",
        consumer_prompt_enabled=False,
    ),
    ServiceCommandOption(
        name="weekly-okr-report",
        display_name="生成 OKR 周报",
        description=(
            "读取管理者的实时 OKR、生成本周周报并发送；整轮读取会超过 Agent 的"
            "空闲与总时长上限，因此只能以服务命令形式在本进程内执行。"
        ),
        channel="dingtalk",
        consumer_prompt_enabled=False,
    ),
    ServiceCommandOption(
        name="recover-recent-messages",
        display_name="恢复近期 DingTalk 消息",
        description=(
            "把 DingTalk 读取范围放宽到最近的单聊和被点名的会话，"
            "找回快路径可能漏掉的消息，去重后写入 reply task。"
        ),
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
