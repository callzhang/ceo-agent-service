"""Read-only models for Agent result rows written before the review split.

These models are never accepted from a live Agent turn. They let History read
old Audit executions without restoring execution fields to the current Audit
wire contract.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class HistoricalAuditExternalResult(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    operation_id: str = Field(min_length=1)
    live_result_reference: dict[str, Any]


class HistoricalAuditExecutionResult(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    outcome: Literal["executed"]
    proposal_revision: int = Field(ge=0)
    external_result: HistoricalAuditExternalResult
