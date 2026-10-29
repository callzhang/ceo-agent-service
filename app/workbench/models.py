from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TurnStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"


class WorkbenchTask(StrictModel):
    id: str
    title: str
    runtime_kind: str
    provider_session_ref: str = ""
    archived_at: str = ""
    created_at: str
    updated_at: str


class WorkbenchTurn(StrictModel):
    id: str
    task_id: str
    client_request_id: str
    task_sequence: int = Field(ge=1)
    user_text: str
    status: TurnStatus
    stop_requested: bool = False
    final_text: str = ""
    error_code: str = ""
    error_detail: str = ""
    started_at: str = ""
    completed_at: str = ""
    created_at: str
    updated_at: str


class WorkbenchEvent(StrictModel):
    id: int
    turn_id: str
    sequence: int
    event_type: Literal[
        "text_delta",
        "thinking_summary",
        "tool_started",
        "tool_completed",
        "file_changed",
        "artifact_created",
        "status_changed",
        "turn_completed",
        "turn_failed",
    ]
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class WorkbenchArtifact(StrictModel):
    id: str
    turn_id: str
    label: str
    path: str
    media_type: str
    created_at: str


class WorkbenchAttachment(StrictModel):
    id: str
    task_id: str
    filename: str
    media_type: str
    size_bytes: int
    created_at: str


