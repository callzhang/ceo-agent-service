"""Main-page adapter for the service's single routed Agent runtime."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.agent_runtime_production import build_production_routed_codex_execution
from app.agent_runtime_router import (
    CodexCommandFactory,
    RoutedCodexExecutionError,
    RoutedCodexExecutionCancelled,
    RoutedResultCodec,
    RoutedResultValidationError,
)
from app.agent_result import ResultParseError, parse_agent_text_result
from app.store import AutoReplyStore
from app.workbench.codex_runtime import (
    _AdapterFailure,
    _CancellableProcessExecutor,
    _CodexNormalizer,
)
from app.workbench.runtime import (
    RuntimeCapabilities,
    RuntimeEvent,
    RuntimeHandle,
    RuntimeRequest,
    RuntimeResult,
    _release_runtime_owner,
    _runtime_owner,
)


_WORKBENCH_RESULT_CODEC = RoutedResultCodec.text(
    schema_id="workbench.result.v1",
    allow_evidence_source_refs=True,
)
_WORKBENCH_DEVELOPER_INSTRUCTIONS = (
    "Complete the user's main-page task using the service runtime and its available "
    "Skills, MCP servers, and local tools. Return the final user-facing answer as "
    "ordinary text. Native CLI auto review is the only command approval mechanism."
)


@dataclass
class _ServiceRuntimeOwner:
    executor: Any
    normalizer: _CodexNormalizer
    request: RuntimeRequest
    done: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    result: RuntimeResult | None = None
    thread: threading.Thread | None = None
    stop_requested: bool = False
    stop_dispatched: bool = False
    wait_claimed: bool = False
    projection_failed: bool = False


class ServiceWorkbenchRuntime:
    """Project shared runtime events into the main-page UI contract."""

    kind = "codex"

    def __init__(
        self,
        *,
        workspace: Path,
        store: AutoReplyStore | None = None,
        routed_execution_factory: Callable[..., object] = (
            build_production_routed_codex_execution
        ),
        codex_bin: str = "codex",
        total_timeout_seconds: float = 900,
        idle_timeout_seconds: float = 300,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.store = store
        self.routed_execution_factory = routed_execution_factory
        self.codex_bin = codex_bin
        self.total_timeout_seconds = total_timeout_seconds
        self.idle_timeout_seconds = idle_timeout_seconds

    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            session_resume=True,
            streamed_text=True,
            structured_tools=True,
            image_input=True,
            model_selection=False,
            mcp_configuration=True,
            stoppable=True,
            recoverable=True,
        )

    def start(
        self,
        request: RuntimeRequest,
        *,
        on_event: Callable[[RuntimeEvent], None],
    ) -> RuntimeHandle:
        self._validate_request(request)
        executor = _CancellableProcessExecutor(cwd=request.workspace.resolve())
        owner = _ServiceRuntimeOwner(
            executor=executor,
            normalizer=_CodexNormalizer(on_event),
            request=request,
        )
        handle = RuntimeHandle.create(run_id=uuid4().hex, owner=owner)
        owner.thread = threading.Thread(
            target=self._run,
            args=(owner,),
            name=f"service-workbench-{handle.run_id}",
            daemon=True,
        )
        owner.thread.start()
        return handle

    def wait(self, handle: RuntimeHandle) -> RuntimeResult:
        owner = _runtime_owner(handle)
        if not isinstance(owner, _ServiceRuntimeOwner):
            raise ValueError("runtime handle does not belong to the service")
        with owner.lock:
            if owner.wait_claimed:
                raise ValueError("runtime handle is already being waited")
            owner.wait_claimed = True
        owner.done.wait()
        try:
            assert owner.result is not None
            return owner.result
        finally:
            _release_runtime_owner(handle)

    def stop(self, handle: RuntimeHandle) -> None:
        try:
            owner = _runtime_owner(handle)
        except ValueError:
            return
        if not isinstance(owner, _ServiceRuntimeOwner):
            raise ValueError("runtime handle does not belong to the service")
        with owner.lock:
            if owner.done.is_set() or owner.stop_dispatched:
                return
            owner.stop_requested = True
            owner.stop_dispatched = True
        owner.executor.stop()

    def _run(self, owner: _ServiceRuntimeOwner) -> None:
        try:
            routed = self.routed_execution_factory(
                store=self.store,
                workspace=owner.request.workspace,
                total_timeout_seconds=self.total_timeout_seconds,
                idle_timeout_seconds=self.idle_timeout_seconds,
                codex_bin=self.codex_bin,
                executor=owner.executor,
            )

            def parse_result(raw: str) -> str:
                try:
                    return parse_agent_text_result(raw)
                except ResultParseError as exc:
                    raise RoutedResultValidationError(str(exc)) from exc

            def project_line(line: str) -> None:
                if owner.projection_failed:
                    return
                try:
                    owner.normalizer.accept_line(line)
                except Exception:  # noqa: BLE001 - UI projection is non-authoritative
                    owner.projection_failed = True

            routed_result = routed.execute(
                workload_kind="workbench",
                workload_key=owner.request.turn_id,
                prompt=owner.request.prompt,
                command_factory=CodexCommandFactory.standard(
                    developer_instructions=_WORKBENCH_DEVELOPER_INSTRUCTIONS,
                    image_paths=owner.request.image_paths,
                ),
                parser=parse_result,
                result_codec=_WORKBENCH_RESULT_CODEC,
                conversation_id=owner.request.conversation_id,
                on_stdout_line=project_line,
                cancel_requested=lambda: owner.stop_requested,
            )
            with owner.lock:
                stopped = owner.stop_requested
            owner.result = (
                RuntimeResult(status="stopped")
                if stopped
                else RuntimeResult(
                    status="completed",
                    final_text=routed_result.value,
                    provider_session_ref=routed_result.session_id,
                )
            )
        except _AdapterFailure as exc:
            owner.result = RuntimeResult(
                status="failed", error_code=exc.code, error_detail=exc.detail
            )
        except RoutedCodexExecutionCancelled:
            owner.result = RuntimeResult(status="stopped")
        except RoutedCodexExecutionError as exc:
            with owner.lock:
                stopped = owner.stop_requested
            owner.result = (
                RuntimeResult(status="stopped")
                if stopped
                else RuntimeResult(
                    status="failed",
                    error_code=exc.code,
                    error_detail=exc.reason,
                )
            )
        except Exception as exc:  # noqa: BLE001
            owner.result = RuntimeResult(
                status="failed",
                error_code="runtime_failure",
                error_detail=f"{type(exc).__name__}: {exc}",
            )
        finally:
            owner.done.set()

    def _validate_request(self, request: RuntimeRequest) -> None:
        workspace = request.workspace.resolve()
        try:
            workspace.relative_to(self.workspace)
        except ValueError as exc:
            raise ValueError("workspace is outside runtime boundary") from exc
        for field_name, value in (
            ("turn_id", request.turn_id),
            ("conversation_id", request.conversation_id),
        ):
            try:
                if str(UUID(value)) != value:
                    raise ValueError
            except (AttributeError, ValueError) as exc:
                raise ValueError(f"{field_name} must be a canonical UUID") from exc
        if request.attachment_paths:
            raise ValueError("attachments are not supported by the service runtime")
