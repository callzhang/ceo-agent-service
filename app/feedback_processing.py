"""Typed persistence models for feedback processing workflows.

The models in this module intentionally describe only the bounded processing
states persisted by :class:`app.store.AutoReplyStore`.  Extra fields and
implicit coercions are rejected so callers cannot accidentally persist a
different workflow shape.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FEEDBACK_PROCESSING_CLAIM_ERROR = "feedback processing claim rejected"
FEEDBACK_PROCESSING_BATCH_ERROR = "feedback processing batch definition conflict"


class FeedbackProcessingClaimError(ValueError):
    """Raised when a feedback batch cannot be claimed atomically."""


class FeedbackProcessingBatchError(ValueError):
    """Raised when a batch id is reused with a different key set."""


class _StrictProcessingModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        validate_assignment=True,
        validate_default=True,
    )


class FeedbackProcessingBatch(_StrictProcessingModel):
    """A group of feedback items processed together."""

    batch_id: str
    status: Literal["pending", "processing", "resolved"] = "pending"
    requested_count: int = 0
    created_at: str = ""
    updated_at: str = ""
    resolved_at: str = ""


class FeedbackProcessingItem(_StrictProcessingModel):
    """Persisted state and evidence for one feedback event."""

    feedback_key: str
    batch_id: str = ""
    status: Literal["pending", "processing", "resolved"] = "pending"
    workbench_task_id: str = ""
    workbench_turn_id: str = ""
    attempt_id: int = 0
    agent_run_id: int = 0
    commit_sha: str = ""
    test_evidence: dict[str, object] = Field(default_factory=dict)
    restart_evidence: dict[str, object] = Field(default_factory=dict)
    health_evidence: dict[str, object] = Field(default_factory=dict)
    note: str = ""
    resolved_at: str = ""
    created_at: str = ""
    updated_at: str = ""
