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


SERVICE_COMMAND_EXECUTION_KIND = "service_command"


@dataclass(frozen=True)
class ServiceCommandOption:
    name: str
    description: str


SERVICE_COMMAND_OPTIONS: tuple[ServiceCommandOption, ...] = (
    ServiceCommandOption(
        name="produce-once",
        description=(
            "增量读取 DingTalk 未读消息，去重后写入 reply task，"
            "由统一 Dispatcher 继续消费。"
        ),
    ),
    ServiceCommandOption(
        name="wechat-produce-once",
        description=(
            "读取已就绪微信账号的新消息，按已配置联系人和群@边界去重后写入 reply task；"
            "Reader 不可用时只记录健康状态。"
        ),
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
