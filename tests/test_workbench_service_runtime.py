from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.workbench.runtime import RuntimeRequest
from app.workbench.service_runtime import ServiceWorkbenchRuntime
from app.agent_runtime_contracts import LOCAL_SERVICE_RUNTIME_CAPABILITIES


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
        turn_id="2",
        conversation_id="1",
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
        turn_id="2",
        conversation_id="1",
        workspace=tmp_path,
        prompt="检查销售进度",
    )

    result = runtime.wait(runtime.start(request, on_event=lambda _event: None))

    assert result.status == "completed"
    assert result.final_text == "共享执行已完成"


def test_workbench_file_inputs_require_local_capabilities_and_reach_agent(
    tmp_path: Path,
):
    routed = _FakeRoutedExecution()
    runtime = ServiceWorkbenchRuntime(
        workspace=tmp_path, routed_execution_factory=lambda **_: routed
    )
    attachment = tmp_path / "source file.txt"
    attachment.write_text("source")
    request = RuntimeRequest(
        turn_id="2",
        conversation_id="1",
        workspace=tmp_path,
        prompt="Read the source",
        attachment_paths=(attachment,),
    )

    assert (
        runtime.wait(runtime.start(request, on_event=lambda _: None)).status
        == "completed"
    )
    assert (
        routed.calls[0]["required_capabilities"] == LOCAL_SERVICE_RUNTIME_CAPABILITIES
    )
    assert str(attachment) in routed.calls[0]["prompt"]


def test_workbench_without_files_keeps_existing_route_requirements(tmp_path: Path):
    routed = _FakeRoutedExecution()
    runtime = ServiceWorkbenchRuntime(
        workspace=tmp_path, routed_execution_factory=lambda **_: routed
    )
    request = RuntimeRequest(
        turn_id="2", conversation_id="1", workspace=tmp_path, prompt="Hello"
    )

    assert (
        runtime.wait(runtime.start(request, on_event=lambda _: None)).status
        == "completed"
    )
    assert not routed.calls[0].get("required_capabilities")
    assert routed.calls[0]["prompt"] == "Hello"


def test_workbench_does_not_override_requested_output_format(tmp_path: Path):
    routed = _FakeRoutedExecution()
    runtime = ServiceWorkbenchRuntime(
        workspace=tmp_path, routed_execution_factory=lambda **kwargs: routed,
    )
    request = RuntimeRequest(turn_id='2', conversation_id='1', workspace=tmp_path,
                             prompt='Return ONLY a JSON object with outcome and summary.')
    runtime.wait(runtime.start(request, on_event=lambda event: None))
    instructions = routed.calls[0]['command_factory'].developer_instructions
    assert 'ordinary text' not in instructions
    assert 'requested output format' in instructions
    assert routed.calls[0]['prompt'] == request.prompt


def test_routed_failure_preserves_specific_failure_code(tmp_path: Path):
    from app.agent_runtime_contracts import RuntimeFailureClass
    from app.agent_runtime_router import RoutedCodexExecutionError

    class FailedExecution:
        def execute(self, **kwargs):
            raise RoutedCodexExecutionError(
                'runtime_execution_failed', 'no_next_route',
                failure_class=RuntimeFailureClass.TRANSPORT,
                failure_code='codex_total_timeout',
            )

    runtime = ServiceWorkbenchRuntime(
        workspace=tmp_path, routed_execution_factory=lambda **_: FailedExecution(),
    )
    request = RuntimeRequest(
        turn_id='2', conversation_id='1', workspace=tmp_path, prompt='Inspect',
    )
    result = runtime.wait(runtime.start(request, on_event=lambda _: None))
    assert result.status == 'failed'
    assert result.error_code == 'codex_total_timeout'
