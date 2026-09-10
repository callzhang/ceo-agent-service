"""Deterministic service commands that a scheduled task runs without an Agent.

A service command is an in-process operation of this service (the same
operation the matching ``app.cli`` subcommand performs).  The catalog below is
the only source of command names that a scheduled task may reference; the
running dispatcher binds every catalogued name to its implementation exactly
once at startup.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal


SERVICE_COMMAND_EXECUTION_KIND = "service_command"

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


SERVICE_COMMAND_OPTIONS: tuple[ServiceCommandOption, ...] = (
    ServiceCommandOption(
        name="produce-once",
        display_name="检查钉钉消息",
        description=(
            "增量读取 DingTalk 未读消息，去重后写入 reply task，"
            "由统一 Dispatcher 继续消费。"
        ),
        channel="dingtalk",
    ),
    ServiceCommandOption(
        name="wechat-produce-once",
        display_name="检查微信消息",
        description=(
            "读取已就绪微信账号的新消息，按已配置联系人和群@边界去重后写入 reply task；"
            "Reader 不可用时只记录健康状态。"
        ),
        channel="wechat",
    ),
    ServiceCommandOption(
        name="scan-meetings-once",
        display_name="检查 DingTalk 会议",
        description=(
            "读取已结束且满足资料条件的 DingTalk 会议，去重后写入会议对齐队列；"
            "由会议 Agent 处理真实会议。"
        ),
        channel="meeting",
    ),
    ServiceCommandOption(
        name="scan-oa-approvals",
        display_name="检查 DingTalk OA 审批",
        description=(
            "增量读取待处理的 DingTalk OA 审批，去重后写入审批回复队列；"
            "由 DingTalk Consumer 处理真实审批。"
        ),
        channel="dingtalk",
    ),
    ServiceCommandOption(
        name="scan-work-sources-once",
        display_name="扫描工作来源",
        description=(
            "扫描已配置的本地工作目录，去重后写入工作摘要队列；"
            "由 Work Summary Consumer 处理真实来源。"
        ),
        channel="work_summary",
    ),
    ServiceCommandOption(
        name="sync-minutes-once",
        display_name="同步 AI 听记",
        description=(
            "增量同步 DingTalk AI 听记到本地工作来源队列；"
            "由 Work Summary Consumer 处理真实内容。"
        ),
        channel="work_summary",
    ),
    ServiceCommandOption(
        name="recover-recent-messages",
        display_name="恢复近期 DingTalk 消息",
        description=(
            "把 DingTalk 读取范围放宽到最近的单聊和被点名的会话，"
            "找回快路径可能漏掉的消息，去重后写入 reply task。"
        ),
        channel="dingtalk",
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

    def run(self, name: str) -> str:
        """Run one catalogued command and return its one-line result summary."""
        return self._implementations[service_command_option(name).name]()
