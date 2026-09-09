from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.workbench.runtime import RuntimeRequest
from app.workbench.service_runtime import ServiceWorkbenchRuntime


@dataclass(frozen=True)
class _RoutedResult:
    value: str
    session_id: str


class _FakeRoutedExecution:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        on_stdout_line = kwargs["on_stdout_line"]
        on_stdout_line(
            json.dumps(
                {
                    "type": "thread.started",
                    "thread_id": "00000000-0000-0000-0000-000000000099",
                }
            )
        )
        on_stdout_line(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "完成"},
                }
            )
        )
        on_stdout_line(json.dumps({"type": "turn.completed"}))
        raw = "\n".join(
            (
                json.dumps({"type": "turn.started"}),
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"type": "agent_message", "text": "完成"},
                    }
                ),
                json.dumps({"type": "turn.completed"}),
            )
        )
        return _RoutedResult(
            value=kwargs["parser"](raw),
            session_id="00000000-0000-0000-0000-000000000099",
        )


def test_workbench_uses_shared_service_execution_and_projects_events(tmp_path: Path):
    routed = _FakeRoutedExecution()
    runtime = ServiceWorkbenchRuntime(
        workspace=tmp_path,
        routed_execution_factory=lambda **_kwargs: routed,
    )
    events = []
    request = RuntimeRequest(
        turn_id="00000000-0000-0000-0000-000000000002",
        conversation_id="00000000-0000-0000-0000-000000000001",
        workspace=tmp_path,
        prompt="检查销售进度",
    )

    result = runtime.wait(runtime.start(request, on_event=events.append))

    assert result.status == "completed"
    assert result.final_text == "完成"
    assert result.provider_session_ref == "00000000-0000-0000-0000-000000000099"
    assert len(routed.calls) == 1
    call = routed.calls[0]
    assert call["workload_kind"] == "workbench"
    assert call["workload_key"] == request.turn_id
    assert call["conversation_id"] == request.conversation_id
    assert call["prompt"] == request.prompt
    assert [event.event_type for event in events] == [
        "status_changed",
        "text_delta",
    ]


def test_ui_event_projection_cannot_abort_shared_service_execution(tmp_path: Path):
    class ProjectionBreakingExecution(_FakeRoutedExecution):
        def execute(self, **kwargs):
            on_stdout_line = kwargs["on_stdout_line"]
            on_stdout_line(
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "command_execution",
                            "id": "never-started",
                        },
                    }
                )
            )
            return _RoutedResult(value="共享执行已完成", session_id="session-1")

    runtime = ServiceWorkbenchRuntime(
        workspace=tmp_path,
        routed_execution_factory=lambda **_kwargs: ProjectionBreakingExecution(),
    )
    request = RuntimeRequest(
        turn_id="00000000-0000-0000-0000-000000000002",
        conversation_id="00000000-0000-0000-0000-000000000001",
        workspace=tmp_path,
        prompt="检查销售进度",
    )

    result = runtime.wait(runtime.start(request, on_event=lambda _event: None))

    assert result.status == "completed"
    assert result.final_text == "共享执行已完成"
