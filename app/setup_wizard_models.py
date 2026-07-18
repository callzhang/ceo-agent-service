from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


SetupStatus = Literal[
    "not_started",
    "checking",
    "needs_action",
    "running",
    "done",
    "failed",
    "blocked",
]
SetupActionStatus = Literal["not_started", "running", "done", "failed"]


class SetupAction(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    label: str
    step_id: str
    kind: Literal["check", "run", "confirm"]
    destructive: bool = False
    external_side_effect: bool = False


class SetupStepDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    phase: str
    description: str
    depends_on: tuple[str, ...] = Field(default_factory=tuple)
    actions: tuple[SetupAction, ...] = Field(default_factory=tuple)


class SetupStepStatus(BaseModel):
    step_id: str
    title: str
    status: SetupStatus = "not_started"
    summary: str = ""
    evidence: dict[str, str | int | bool] = Field(default_factory=dict)
    available_actions: list[SetupAction] = Field(default_factory=list)
    manual_confirmation_allowed: bool = False
    updated_at: str = ""


class SetupWizardEvent(BaseModel):
    id: int = 0
    step_id: str
    action_id: str
    status: SetupActionStatus
    next_step_status: str = ""
    summary: str = ""
    evidence: dict[str, str | int | bool] = Field(default_factory=dict)
    stdout_excerpt: str = ""
    stderr_excerpt: str = ""
    started_at: str = ""
    finished_at: str = ""


class SetupWizardStatus(BaseModel):
    steps: list[SetupStepStatus]
