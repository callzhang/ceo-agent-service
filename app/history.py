from typing import Literal

from pydantic import BaseModel, Field

from app.leak_check import contains_credential, contains_local_runtime_leak


class UniversalActionObservation(BaseModel):
    index: int
    kind: str
    status: str
    error: str = ""


class UniversalExecutionObservation(BaseModel):
    planner_kind: Literal["universal"] = "universal"
    capability: str
    dependencies: list[str]
    blocking_dependency: str = ""
    actions: list[UniversalActionObservation]


def safe_observability_error(value: str, *, limit: int = 500) -> str:
    cleaned = " ".join(value.split())
    if contains_credential(cleaned) or contains_local_runtime_leak(cleaned):
        return "[redacted sensitive error]"
    return cleaned[:limit]


class HistoryItem(BaseModel):
    kind: Literal["reply", "meeting", "task"]
    source_id: int
    source_title: str
    source_actor: str
    input_label: str
    input_text: str
    output_label: str
    output_text: str
    action: str
    status: str
    target_title: str = ""
    codex_session_id: str = ""
    project_id: int = 0
    todo_id: int = 0
    follow_up_id: int = 0
    planner_kind: str = ""
    capability: str = ""
    blocking_dependency: str = ""
    planned_actions: list[UniversalActionObservation] = Field(default_factory=list)
    channel: str = "dingtalk"
    created_at: str
