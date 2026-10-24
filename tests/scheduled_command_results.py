"""Helpers for service-command fixtures with deliberately unknown effects."""

from app.agent_cron.commands import ServiceCommandResult


def unknown_service_result(summary: str) -> ServiceCommandResult:
    return ServiceCommandResult(summary=summary, producer_counts=None, complete=False)
