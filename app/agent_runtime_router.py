# The route-loop closures below are passed only to `_finalized_step`, which
# invokes them synchronously and never stores them beyond the current attempt.
# ruff: noqa: B023

from __future__ import annotations

import hashlib
import json
import os
import tomllib
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TypeVar

from app.agent_effects import (
    IDLE_TIMEOUT_SECONDS,
    TOTAL_TIMEOUT_SECONDS,
    McpToolEffectRegistry,
)
from app.agent_result import EffectKind
from app.agent_runtime_config import AgentRuntimeConfig
from app.agent_runtime_contracts import (
    RuntimeCapabilitySnapshot,
    RuntimeFailure,
    RuntimeFailureClass,
    RuntimeRoute,
)
from app.codex_decision import extract_codex_session_id
from app.codex_history import count_codex_session_lines, find_codex_session_path
from app.codex_runtime_adapter import CodexRuntimeAdapter
from app.leak_check import contains_credential, contains_local_runtime_leak
from app.native_cli_metadata import NativeCliMetadataClassifier
from app.process_runner import ProcessRunResult, run_process_with_idle_timeout
from app.store import (
    MAX_RUNTIME_RESULT_ENVELOPE_BYTES,
    AgentRun,
    AgentRuntimeAttempt,
    AgentRuntimeAttemptStartConflictError,
    AutoReplyStore,
    RuntimeAttemptSessionMode,
    RuntimeRoutePausedError,
)

ResultT = TypeVar("ResultT")
StepT = TypeVar("StepT")
ProcessExecutor = Callable[..., ProcessRunResult]
_APPROVED_COMMAND_FACTORY_SEAL = object()
_ROUTED_RESULT_CODEC_SEAL = object()
_RESULT_VALIDATION_RETRY_SEAL = object()


class RoutedResultEnvelopeTooLarge(ValueError):
    """Raised when a durable result exceeds the reviewed byte budget."""


class RoutedResultValidationError(ValueError):
    """A typed business-result validation failure eligible for one correction."""

    def __init__(self, message: str, *, raw_output: str = "") -> None:
        self.raw_output = raw_output
        super().__init__(message)


@dataclass(frozen=True, slots=True, init=False)
class RoutedResultValidationRetry:
    """Sealed policy permitting exactly one fresh read-only correction turn."""

    correction_instructions: str
    correction_prompt: Callable[[str], str] | None
    resume_same_session: bool
    _seal: object

    def __init__(
        self,
        *,
        correction_instructions: str,
        correction_prompt: Callable[[str], str] | None = None,
        resume_same_session: bool = False,
        seal: object,
    ) -> None:
        if seal is not _RESULT_VALIDATION_RETRY_SEAL:
            raise ValueError("result validation retry policies use named constructors")
        correction_instructions = correction_instructions.strip()
        if not correction_instructions:
            raise ValueError("correction_instructions must be non-empty")
        if contains_credential(correction_instructions) or contains_local_runtime_leak(
            correction_instructions
        ):
            raise ValueError("correction instructions contain sensitive runtime data")
        object.__setattr__(self, "correction_instructions", correction_instructions)
        object.__setattr__(self, "correction_prompt", correction_prompt)
        object.__setattr__(self, "resume_same_session", resume_same_session)
        object.__setattr__(self, "_seal", seal)

    @classmethod
    def exactly_once(
        cls, *, correction_instructions: str
    ) -> RoutedResultValidationRetry:
        return cls(
            correction_instructions=correction_instructions,
            seal=_RESULT_VALIDATION_RETRY_SEAL,
        )

    @classmethod
    def same_session_exactly_once(
        cls, *, correction_prompt: Callable[[str], str]
    ) -> RoutedResultValidationRetry:
        if not callable(correction_prompt):
            raise ValueError("correction_prompt must be callable")
        return cls(
            correction_instructions="Resume the same session and correct the result.",
            correction_prompt=correction_prompt,
            resume_same_session=True,
            seal=_RESULT_VALIDATION_RETRY_SEAL,
        )

    def corrected_prompt(
        self, original_prompt: str, failure: RoutedResultValidationError
    ) -> str:
        if self.correction_prompt is not None:
            prompt = self.correction_prompt(failure.raw_output).strip()
            if not prompt:
                raise ValueError("correction prompt must be non-empty")
            return prompt
        detail = " ".join(str(failure).split())[:1000]
        if (
            not detail
            or contains_credential(detail)
            or contains_local_runtime_leak(detail)
        ):
            detail = "the prior result did not satisfy the required validation"
        return (
            f"{original_prompt}\n\n"
            f"上一轮输出未通过结构化校验：{detail}。"
            f"{self.correction_instructions}"
        )

    @property
    def policy_id(self) -> str:
        payload = json.dumps(
            {
                "contract": "result_validation_retry.v1",
                "correction_instructions": self.correction_instructions,
                "resume_same_session": self.resume_same_session,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return f"result_validation_retry.v1:{digest}"


class ExecutionEffectMode(StrEnum):
    READ_ONLY = "read_only"
    EFFECTFUL = "effectful"


class _ReadOnlyCommandIsolation(StrEnum):
    STANDARD = "standard"
    NO_TOOLS = "no_tools"
    MEMORY_RECALL_ONLY = "memory_recall_only"


_READ_ONLY_DISABLED_DYNAMIC_FEATURES = (
    "plugins",
    "apps",
    "chronicle",
    "computer_use",
    "browser_use",
    "in_app_browser",
    "memories",
    "skill_search",
)


@dataclass(frozen=True, slots=True)
class _ApprovedExecutionPolicy:
    effect_mode: ExecutionEffectMode
    seal: object

    def __post_init__(self) -> None:
        if self.seal is not _APPROVED_COMMAND_FACTORY_SEAL:
            raise ValueError("execution policy was not issued by the approved factory")


@dataclass(frozen=True, slots=True, init=False)
class ApprovedCodexCommandFactory:
    """Build only the two reviewed Codex command policy shapes.

    The sealed policy is consumed internally by ``RoutedCodexExecution``. A
    caller cannot opt into failover by passing a boolean or an arbitrary policy
    object.
    """

    _policy: _ApprovedExecutionPolicy
    _developer_instructions: str
    _output_schema_path: Path | None
    _use_output_schema: bool
    _image_paths: tuple[Path, ...]
    _command_isolation: _ReadOnlyCommandIsolation

    def __init__(
        self,
        *,
        effect_mode: ExecutionEffectMode,
        developer_instructions: str,
        output_schema_path: Path | None,
        use_output_schema: bool,
        image_paths: tuple[Path, ...],
        command_isolation: _ReadOnlyCommandIsolation,
        seal: object,
    ) -> None:
        if seal is not _APPROVED_COMMAND_FACTORY_SEAL:
            raise ValueError("approved command factories use named constructors")
        developer_instructions = developer_instructions.strip()
        if not developer_instructions:
            raise ValueError("developer_instructions must be non-empty")
        object.__setattr__(self, "_policy", _ApprovedExecutionPolicy(effect_mode, seal))
        object.__setattr__(self, "_developer_instructions", developer_instructions)
        object.__setattr__(self, "_output_schema_path", output_schema_path)
        object.__setattr__(self, "_use_output_schema", use_output_schema)
        object.__setattr__(self, "_image_paths", image_paths)
        object.__setattr__(self, "_command_isolation", command_isolation)

    @classmethod
    def read_only(
        cls,
        *,
        developer_instructions: str,
        output_schema_path: Path | None = None,
        use_output_schema: bool = False,
        image_paths: Sequence[Path] = (),
    ) -> ApprovedCodexCommandFactory:
        return cls(
            effect_mode=ExecutionEffectMode.READ_ONLY,
            developer_instructions=developer_instructions,
            output_schema_path=output_schema_path,
            use_output_schema=use_output_schema,
            image_paths=tuple(image_paths),
            command_isolation=_ReadOnlyCommandIsolation.STANDARD,
            seal=_APPROVED_COMMAND_FACTORY_SEAL,
        )

    @classmethod
    def read_only_without_tools(
        cls,
        *,
        developer_instructions: str,
        output_schema_path: Path | None = None,
        use_output_schema: bool = False,
        image_paths: Sequence[Path] = (),
    ) -> ApprovedCodexCommandFactory:
        return cls(
            effect_mode=ExecutionEffectMode.READ_ONLY,
            developer_instructions=developer_instructions,
            output_schema_path=output_schema_path,
            use_output_schema=use_output_schema,
            image_paths=tuple(image_paths),
            command_isolation=_ReadOnlyCommandIsolation.NO_TOOLS,
            seal=_APPROVED_COMMAND_FACTORY_SEAL,
        )

    @classmethod
    def read_only_memory_recall(
        cls,
        *,
        developer_instructions: str,
        output_schema_path: Path | None = None,
        use_output_schema: bool = False,
        image_paths: Sequence[Path] = (),
    ) -> ApprovedCodexCommandFactory:
        return cls(
            effect_mode=ExecutionEffectMode.READ_ONLY,
            developer_instructions=developer_instructions,
            output_schema_path=output_schema_path,
            use_output_schema=use_output_schema,
            image_paths=tuple(image_paths),
            command_isolation=_ReadOnlyCommandIsolation.MEMORY_RECALL_ONLY,
            seal=_APPROVED_COMMAND_FACTORY_SEAL,
        )

    @classmethod
    def effectful(
        cls,
        *,
        developer_instructions: str,
        output_schema_path: Path | None = None,
        use_output_schema: bool = False,
        image_paths: Sequence[Path] = (),
    ) -> ApprovedCodexCommandFactory:
        return cls(
            effect_mode=ExecutionEffectMode.EFFECTFUL,
            developer_instructions=developer_instructions,
            output_schema_path=output_schema_path,
            use_output_schema=use_output_schema,
            image_paths=tuple(image_paths),
            command_isolation=_ReadOnlyCommandIsolation.STANDARD,
            seal=_APPROVED_COMMAND_FACTORY_SEAL,
        )

    @property
    def _approved_policy(self) -> _ApprovedExecutionPolicy:
        return self._policy

    def build(
        self,
        *,
        adapter: CodexRuntimeAdapter,
        route: RuntimeRoute,
        prompt: str,
        session_id: str | None,
    ) -> tuple[list[str], dict[str, str]]:
        read_only = self._policy.effect_mode is ExecutionEffectMode.READ_ONLY
        command = adapter.build_command(
            route=route,
            prompt=prompt,
            session_id=session_id,
            image_paths=list(self._image_paths),
            output_schema_path=self._output_schema_path,
            use_output_schema=self._use_output_schema,
            approval_policy="never" if read_only else "untrusted",
            developer_instructions=self._developer_instructions,
            use_approval_bypass=not read_only,
            sandbox_mode="read-only" if read_only else None,
        )
        env = adapter.build_env(route)
        if self._command_isolation is not _ReadOnlyCommandIsolation.STANDARD:
            _apply_read_only_command_isolation(
                command,
                env=env,
                isolation=self._command_isolation,
            )
        return command, env


def _apply_read_only_command_isolation(
    command: list[str],
    *,
    env: Mapping[str, str],
    isolation: _ReadOnlyCommandIsolation,
) -> None:
    server_names = _configured_mcp_server_names(command, env=env)
    _remove_read_only_isolation_conflicts(command)
    options = [
        *(
            option
            for feature in _READ_ONLY_DISABLED_DYNAMIC_FEATURES
            for option in ("--disable", feature)
        ),
        "-c",
        "tools.enabled_tools=[]",
        "-c",
        'web_search="disabled"',
    ]
    for server_name in server_names:
        if (
            isolation is _ReadOnlyCommandIsolation.MEMORY_RECALL_ONLY
            and server_name == "memory_connector"
        ):
            continue
        options.extend(["-c", f"mcp_servers.{server_name}.enabled=false"])
    if isolation is _ReadOnlyCommandIsolation.NO_TOOLS and (
        "memory_connector" not in server_names
    ):
        options.extend(["-c", "mcp_servers.memory_connector.enabled=false"])
    elif isolation is _ReadOnlyCommandIsolation.MEMORY_RECALL_ONLY:
        options.extend(
            [
                "-c",
                "mcp_servers.memory_connector.enabled=true",
                "-c",
                'mcp_servers.memory_connector.enabled_tools=["memory_recall"]',
                "-c",
                'mcp_servers.memory_connector.disabled_tools=["memory_write"]',
            ]
        )
    insertion_index = len(command) - 1
    if command[1:3] == ["exec", "resume"]:
        insertion_index -= 1
    command[insertion_index:insertion_index] = options


def _remove_read_only_isolation_conflicts(command: list[str]) -> None:
    index = 0
    feature_prefixes = tuple(
        f"features.{feature}=" for feature in _READ_ONLY_DISABLED_DYNAMIC_FEATURES
    )
    while index + 1 < len(command):
        if (
            command[index] == "--enable"
            and command[index + 1] in _READ_ONLY_DISABLED_DYNAMIC_FEATURES
        ):
            del command[index : index + 2]
            continue
        if command[index] == "-c" and command[index + 1].startswith(
            ("tools.enabled_tools=", "web_search=", *feature_prefixes)
        ):
            del command[index : index + 2]
            continue
        index += 1


def _configured_mcp_server_names(
    command: Sequence[str], *, env: Mapping[str, str]
) -> tuple[str, ...]:
    names: set[str] = set()
    for index, value in enumerate(command[:-1]):
        if value != "-c":
            continue
        option = command[index + 1]
        if option.startswith("mcp_servers."):
            parts = option.split(".", 2)
            if len(parts) == 3 and parts[1]:
                names.add(parts[1])
    codex_home_text = env.get("CODEX_HOME", os.environ.get("CODEX_HOME", "")).strip()
    config_path = Path(codex_home_text) / "config.toml" if codex_home_text else None
    if config_path is not None and config_path.is_file():
        try:
            configured = tomllib.loads(config_path.read_text(encoding="utf-8")).get(
                "mcp_servers", {}
            )
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ValueError("Codex MCP configuration is not safely readable") from exc
        if isinstance(configured, dict):
            names.update(str(name) for name in configured if str(name).strip())
    return tuple(sorted(names))


@dataclass(frozen=True, slots=True, init=False)
class RoutedResultCodec[ResultT]:
    """A sealed, versioned codec for durable generalized-operation results."""

    schema_id: str
    _kind: str
    _seal: object

    def __init__(self, *, schema_id: str, kind: str, seal: object) -> None:
        if seal is not _ROUTED_RESULT_CODEC_SEAL:
            raise ValueError("result codecs use named constructors")
        schema_id = schema_id.strip()
        if not schema_id or not all(
            part.replace("-", "").replace("_", "").isalnum()
            for part in schema_id.split(".")
        ):
            raise ValueError("schema_id must be a versioned identifier")
        object.__setattr__(self, "schema_id", schema_id)
        object.__setattr__(self, "_kind", kind)
        object.__setattr__(self, "_seal", seal)

    @classmethod
    def integer(cls, *, schema_id: str) -> RoutedResultCodec[int]:
        return cls(schema_id=schema_id, kind="integer", seal=_ROUTED_RESULT_CODEC_SEAL)

    @classmethod
    def text(cls, *, schema_id: str) -> RoutedResultCodec[str]:
        return cls(schema_id=schema_id, kind="text", seal=_ROUTED_RESULT_CODEC_SEAL)

    def encode(self, value: ResultT) -> str:
        self._validate(value)
        encoded = json.dumps(
            {"schema_id": self.schema_id, "value": value},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(encoded.encode("utf-8")) > MAX_RUNTIME_RESULT_ENVELOPE_BYTES:
            raise RoutedResultEnvelopeTooLarge("result envelope exceeds size limit")
        if contains_credential(encoded) or contains_local_runtime_leak(encoded):
            raise ValueError("result envelope contains sensitive runtime data")
        return encoded

    def decode(self, encoded: str) -> ResultT:
        if len(encoded.encode("utf-8")) > MAX_RUNTIME_RESULT_ENVELOPE_BYTES:
            raise RoutedResultEnvelopeTooLarge(
                "persisted result envelope exceeds size limit"
            )
        try:
            envelope = json.loads(encoded)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError("invalid persisted result envelope") from exc
        if (
            not isinstance(envelope, dict)
            or set(envelope) != {"schema_id", "value"}
            or envelope["schema_id"] != self.schema_id
        ):
            raise ValueError("persisted result schema mismatch")
        value = envelope["value"]
        self._validate(value)
        return value

    def _validate(self, value: object) -> None:
        valid = type(value) is int if self._kind == "integer" else type(value) is str
        if not valid:
            raise ValueError(f"result does not match {self._kind} codec")


@dataclass(frozen=True, slots=True)
class RoutedCodexExecutionResult[ResultT]:
    value: ResultT
    route_name: str
    attempt_id: int
    session_id: str
    transcript_start: int
    transcript_end: int


class RoutedCodexExecutionError(RuntimeError):
    def __init__(
        self,
        code: str,
        reason: str = "",
        *,
        failure_class: RuntimeFailureClass | None = None,
        failure_code: str = "",
        retryable_external_dependency: bool = False,
    ) -> None:
        self.code = code
        self.reason = reason
        self.failure_class = failure_class
        self.failure_code = failure_code
        self.retryable_external_dependency = retryable_external_dependency
        super().__init__(code)


def _is_retryable_external_runtime_failure(failure: RuntimeFailure) -> bool:
    """Return whether an exhausted provider failure belongs in caller backoff."""

    return failure.failure_class in {
        RuntimeFailureClass.CAPACITY,
        RuntimeFailureClass.TRANSPORT,
    }


class RoutedCodexPolicyAbort(RuntimeError):
    """Abort the child process immediately after fail-closed policy evidence."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class RuntimeRouteDecision:
    """A bounded runtime-route decision with display-safe static reasons."""

    route: RuntimeRoute | None
    fresh_session: bool
    reason: str


def failover_is_safe(
    *,
    run: AgentRun,
    attempt: AgentRuntimeAttempt,
    failure: RuntimeFailure,
    has_confirmed_receipt: bool,
    recovery_phase: str,
) -> tuple[bool, str]:
    if recovery_phase:
        return False, "recovery_pinned"
    if has_confirmed_receipt:
        return False, "confirmed_receipt"
    if run.side_effect_state != "none":
        return False, "side_effect_state"
    if run.effect_started_count or attempt.first_effect_started_at:
        return False, "effect_started"
    if not failure.failover_permitted:
        return False, "failure_not_eligible"
    return True, "safe"


class AgentRuntimeRouter:
    """Select one untried, healthy route without starting or mutating work."""

    def __init__(
        self,
        *,
        routes: Sequence[RuntimeRoute],
        store: AutoReplyStore,
        snapshots: Mapping[str, RuntimeCapabilitySnapshot],
        now: Callable[[], datetime | str] | None = None,
    ) -> None:
        self._routes = tuple(routes)
        self._store = store
        self._snapshots = snapshots
        self._now = now or (lambda: datetime.now(UTC))

    def first_eligible_route(
        self,
        *,
        required_capabilities: frozenset[str],
        allow_legacy_oauth_bootstrap: bool = False,
    ) -> RuntimeRoute | None:
        """Select an initial route from current evidence.

        The bootstrap exception preserves the pre-failover OAuth path only. It
        never asserts probe health, never applies to service credentials, and
        is disabled whenever an explicit OAuth snapshot exists.
        """
        return self.first_route_decision(
            required_capabilities=required_capabilities,
            allow_legacy_oauth_bootstrap=allow_legacy_oauth_bootstrap,
        ).route

    def first_route_decision(
        self,
        *,
        required_capabilities: frozenset[str],
        allow_legacy_oauth_bootstrap: bool = False,
    ) -> RuntimeRouteDecision:
        """Return the initial route plus a safe, persisted eligibility reason."""
        now = _parse_timestamp(self._now())
        ineligible: list[str] = []
        for route in self._routes:
            if self._store.active_runtime_route_pause(route.name, now=now) is not None:
                ineligible.append(f"{route.name}=paused")
                continue
            if self._snapshot_is_current_and_eligible(
                route=route,
                required_capabilities=required_capabilities,
                now=now,
            ):
                return RuntimeRouteDecision(route, False, "eligible_route")
            if (
                allow_legacy_oauth_bootstrap
                and route.name == "codex_oauth"
                and route.name not in self._snapshots
            ):
                return RuntimeRouteDecision(route, False, "legacy_oauth_bootstrap")
            snapshot = self._snapshots.get(route.name)
            if snapshot is None:
                reason = "snapshot_missing"
            elif snapshot.route_name != route.name:
                reason = "snapshot_invalid"
            elif not snapshot.healthy or snapshot.failure is not None:
                reason = "snapshot_unhealthy"
            else:
                try:
                    checked_at = _parse_timestamp(snapshot.checked_at)
                    expires_at = _parse_timestamp(snapshot.expires_at)
                except (TypeError, ValueError):
                    reason = "snapshot_invalid"
                else:
                    if checked_at > now:
                        reason = "snapshot_invalid"
                    elif expires_at <= now:
                        reason = "snapshot_expired"
                    else:
                        missing = sorted(required_capabilities - snapshot.capabilities)
                        reason = "missing_capabilities:" + ",".join(missing)
            ineligible.append(f"{route.name}={reason}")
        return RuntimeRouteDecision(
            None,
            False,
            "no_eligible_route:" + ";".join(ineligible),
        )

    def next_route(
        self,
        *,
        run: AgentRun,
        failed_attempt: AgentRuntimeAttempt,
        failure: RuntimeFailure,
        required_capabilities: frozenset[str],
        recovery_phase: str,
        has_confirmed_receipt: bool = False,
    ) -> RuntimeRouteDecision:
        persisted_run = self._store.get_agent_run(run.id)
        if persisted_run is None:
            return RuntimeRouteDecision(None, False, "run_not_found")
        if not _run_identity_matches(run, persisted_run):
            return RuntimeRouteDecision(None, False, "run_identity_mismatch")
        if persisted_run.status != "running":
            return RuntimeRouteDecision(None, False, "run_not_eligible")

        persisted_attempt = self._store.get_agent_runtime_attempt(failed_attempt.id)
        if (
            failed_attempt.agent_run_id != persisted_run.id
            or persisted_attempt is None
            or persisted_attempt.agent_run_id != persisted_run.id
            or persisted_attempt != failed_attempt
        ):
            return RuntimeRouteDecision(None, False, "attempt_run_mismatch")
        if persisted_attempt.status != "failed":
            return RuntimeRouteDecision(None, False, "attempt_not_failed")
        if not _failure_matches_persisted_attempt(failure, persisted_attempt):
            return RuntimeRouteDecision(None, False, "failure_mismatch")

        attempts = self._store.list_agent_runtime_attempts(persisted_run.id)
        has_persisted_confirmed_receipt = any(
            receipt.completed and receipt.persisted and receipt.safe_to_confirm
            for receipt in self._store.list_agent_execution_receipts(persisted_run.id)
        )
        safe, reason = failover_is_safe(
            run=persisted_run,
            attempt=persisted_attempt,
            failure=failure,
            has_confirmed_receipt=(
                has_confirmed_receipt or has_persisted_confirmed_receipt
            ),
            recovery_phase=recovery_phase,
        )
        if not safe:
            return RuntimeRouteDecision(None, False, reason)

        now = _parse_timestamp(self._now())
        attempted_routes = {attempt.route_name for attempt in attempts}
        return self._next_eligible_decision(
            attempted_routes=attempted_routes,
            failed_attempt=persisted_attempt,
            failure=failure,
            attempts=attempts,
            required_capabilities=required_capabilities,
            now=now,
        )

    def next_operation_route(
        self,
        *,
        workload_kind: str,
        workload_key: str,
        failed_attempt: AgentRuntimeAttempt,
        failure: RuntimeFailure,
        required_capabilities: frozenset[str],
        read_only_policy_proven: bool,
    ) -> RuntimeRouteDecision:
        """Select a bounded fallback for one persisted non-Agent operation."""
        if not self._store.runtime_operation_parent_is_runnable(
            workload_kind, workload_key
        ):
            return RuntimeRouteDecision(None, False, "operation_not_runnable")
        persisted_attempt = self._store.get_agent_runtime_attempt(failed_attempt.id)
        if (
            failed_attempt.agent_run_id is not None
            or failed_attempt.workload_kind != workload_kind
            or failed_attempt.workload_key != workload_key
            or persisted_attempt is None
            or persisted_attempt != failed_attempt
            or persisted_attempt.agent_run_id is not None
            or persisted_attempt.workload_kind != workload_kind
            or persisted_attempt.workload_key != workload_key
        ):
            return RuntimeRouteDecision(None, False, "attempt_workload_mismatch")
        if persisted_attempt.status != "failed":
            return RuntimeRouteDecision(None, False, "attempt_not_failed")
        if not _failure_matches_persisted_attempt(failure, persisted_attempt):
            return RuntimeRouteDecision(None, False, "failure_mismatch")
        if not read_only_policy_proven:
            return RuntimeRouteDecision(None, False, "read_only_policy_unproven")
        if not failure.failover_permitted:
            return RuntimeRouteDecision(None, False, "failure_not_eligible")

        attempts = self._store.list_runtime_operation_attempts(
            workload_kind, workload_key
        )
        if any(attempt.first_effect_started_at for attempt in attempts):
            return RuntimeRouteDecision(None, False, "effect_started")
        now = _parse_timestamp(self._now())
        attempted_routes = {attempt.route_name for attempt in attempts}
        return self._next_eligible_decision(
            attempted_routes=attempted_routes,
            failed_attempt=persisted_attempt,
            failure=failure,
            attempts=attempts,
            required_capabilities=required_capabilities,
            now=now,
        )

    def _next_eligible_decision(
        self,
        *,
        attempted_routes: set[str],
        failed_attempt: AgentRuntimeAttempt,
        failure: RuntimeFailure,
        attempts: Sequence[AgentRuntimeAttempt],
        required_capabilities: frozenset[str],
        now: datetime,
    ) -> RuntimeRouteDecision:
        """Apply the shared pause, capability, and bounded-route selector."""
        for route in self._routes:
            fresh_session_retry = False
            if route.name in attempted_routes:
                fresh_session_retry = self._fresh_session_retry_is_permitted(
                    route=route,
                    failed_attempt=failed_attempt,
                    failure=failure,
                    attempts=attempts,
                )
                if not fresh_session_retry:
                    continue
            if self._store.active_runtime_route_pause(route.name, now=now) is not None:
                continue
            if not self._snapshot_is_current_and_eligible(
                route=route,
                required_capabilities=required_capabilities,
                now=now,
            ):
                continue
            return RuntimeRouteDecision(
                route,
                fresh_session_retry,
                "fresh_session_retry" if fresh_session_retry else "eligible_route",
            )
        return RuntimeRouteDecision(None, False, "no_eligible_route")

    @staticmethod
    def _fresh_session_retry_is_permitted(
        *,
        route: RuntimeRoute,
        failed_attempt: AgentRuntimeAttempt,
        failure: RuntimeFailure,
        attempts: Sequence[AgentRuntimeAttempt],
    ) -> bool:
        return not any(
            attempt.route_name == "codex_api"
            and attempt.session_mode == RuntimeAttemptSessionMode.FRESH
            for attempt in attempts
        ) and (
            route.name == "codex_api"
            and failed_attempt.route_name == "codex_api"
            and failed_attempt.session_mode == RuntimeAttemptSessionMode.RESUME
            and bool(failed_attempt.source_session_id.strip())
            and failed_attempt.failure_class == RuntimeFailureClass.SESSION.value
            and failure.failure_class == RuntimeFailureClass.SESSION
            and failure.code == "session_route_incompatible"
        )

    def _snapshot_is_current_and_eligible(
        self,
        *,
        route: RuntimeRoute,
        required_capabilities: frozenset[str],
        now: datetime,
    ) -> bool:
        snapshot = self._snapshots.get(route.name)
        if snapshot is None or snapshot.route_name != route.name:
            return False
        if not snapshot.healthy or snapshot.failure is not None:
            return False
        try:
            expires_at = _parse_timestamp(snapshot.expires_at)
            checked_at = _parse_timestamp(snapshot.checked_at)
        except (TypeError, ValueError):
            return False
        if checked_at > now or expires_at <= now:
            return False
        return required_capabilities.issubset(snapshot.capabilities)


class RoutedCodexExecution:
    """Execute one persisted generalized workload through bounded Codex routes."""

    def __init__(
        self,
        *,
        store: AutoReplyStore,
        config: AgentRuntimeConfig,
        router: AgentRuntimeRouter,
        adapter: CodexRuntimeAdapter,
        executor: ProcessExecutor = run_process_with_idle_timeout,
        session_id_parser: Callable[[str], str | None] = extract_codex_session_id,
        session_line_counter: Callable[[str], int] = count_codex_session_lines,
        session_effect_probe: Callable[[str, int, int], bool | None] | None = None,
        total_timeout_seconds: float = TOTAL_TIMEOUT_SECONDS,
        idle_timeout_seconds: float = IDLE_TIMEOUT_SECONDS,
        effect_registry: McpToolEffectRegistry | None = None,
        native_cli_classifier: NativeCliMetadataClassifier | None = None,
        owner: str | None = None,
        lease_seconds: int = 1800,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._config = config
        self._router = router
        self._adapter = adapter
        self._executor = executor
        self._session_id_parser = session_id_parser
        self._session_line_counter = session_line_counter
        self._session_effect_probe = session_effect_probe or (
            lambda _session_id, _start, _end: None
        )
        self._total_timeout_seconds = total_timeout_seconds
        self._idle_timeout_seconds = idle_timeout_seconds
        self._effect_registry = effect_registry or McpToolEffectRegistry.default()
        self._native_cli_classifier = (
            native_cli_classifier or NativeCliMetadataClassifier()
        )
        self._owner = (owner or f"routed-codex-{uuid.uuid4().hex}").strip()
        if not self._owner:
            raise ValueError("owner must be non-empty")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self._lease_seconds = max(lease_seconds, int(total_timeout_seconds) + 60)
        self._now = now or (lambda: datetime.now(UTC))

    def execute(
        self,
        *,
        workload_kind: str,
        workload_key: str,
        prompt: str,
        command_factory: ApprovedCodexCommandFactory,
        parser: Callable[[str], ResultT],
        result_codec: RoutedResultCodec[ResultT],
        conversation_id: str | None = None,
        required_capabilities: frozenset[str] = frozenset(),
        result_validation_retry: RoutedResultValidationRetry | None = None,
    ) -> RoutedCodexExecutionResult[ResultT]:
        if type(command_factory) is not ApprovedCodexCommandFactory:
            raise ValueError("command_factory must be approved")
        policy = command_factory._approved_policy
        if policy.seal is not _APPROVED_COMMAND_FACTORY_SEAL:
            raise ValueError("command_factory policy is not approved")
        if (
            type(result_codec) is not RoutedResultCodec
            or result_codec._seal is not _ROUTED_RESULT_CODEC_SEAL
        ):
            raise ValueError("result_codec must be approved")
        if result_validation_retry is not None and (
            type(result_validation_retry) is not RoutedResultValidationRetry
            or result_validation_retry._seal is not _RESULT_VALIDATION_RETRY_SEAL
        ):
            raise ValueError("result_validation_retry must be approved")
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("prompt must be non-empty")
        original_prompt = prompt
        result_validation_retries_used = 0
        forced_retry_session_id: str | None = None
        terminal_failure: RuntimeFailure | None = None
        next_attempt_purpose = "normal"
        next_validation_retry_policy_id = ""
        next_validation_result_schema_id = ""

        self._store.recover_expired_runtime_operation_attempt(
            workload_kind, workload_key, now=self._now()
        )
        existing_attempts = self._store.list_runtime_operation_attempts(
            workload_kind, workload_key
        )
        if existing_attempts:
            latest = existing_attempts[-1]
            if latest.status in {"starting", "running"}:
                if latest.first_effect_started_at:
                    raise RoutedCodexExecutionError("runtime_effectful_replay_blocked")
                raise RoutedCodexExecutionError("runtime_attempt_active")
            if latest.status == "completed":
                try:
                    value = result_codec.decode(latest.result_envelope_json)
                except RoutedResultEnvelopeTooLarge as exc:
                    raise RoutedCodexExecutionError("runtime_result_invalid") from exc
                except ValueError as exc:
                    raise RoutedCodexExecutionError(
                        "runtime_result_schema_mismatch"
                    ) from exc
                return RoutedCodexExecutionResult(
                    value=value,
                    route_name=latest.route_name,
                    attempt_id=latest.id,
                    session_id=latest.session_id,
                    transcript_start=latest.transcript_start,
                    transcript_end=latest.transcript_end,
                )
            if policy.effect_mode is ExecutionEffectMode.EFFECTFUL:
                raise RoutedCodexExecutionError("runtime_effectful_replay_blocked")
            if latest.status != "failed":
                raise RoutedCodexExecutionError("runtime_attempt_state_invalid")
            if latest.attempt_purpose == "result_validation_correction":
                raise RoutedCodexExecutionError(
                    "runtime_result_validation_retry_consumed",
                    failure_class=RuntimeFailureClass.RESULT,
                    failure_code="runtime_result_validation_retry_consumed",
                )
            validation_failures = sum(
                attempt.failure_code == "runtime_result_validation_failed"
                for attempt in existing_attempts
            )
            can_resume_validation_retry = (
                result_validation_retry is not None
                and latest.failure_code == "runtime_result_validation_failed"
                and validation_failures == 1
                and not latest.first_effect_started_at
                and (
                    not result_validation_retry.resume_same_session
                    or (
                        bool(latest.session_id)
                        and self._probe_session_effect(
                            latest.session_id,
                            latest.transcript_start,
                            latest.transcript_end,
                        )
                        is False
                    )
                )
            )
            if can_resume_validation_retry:
                eligible = self._router.first_route_decision(
                    required_capabilities=required_capabilities
                )
                if (
                    eligible.route is not None
                    and eligible.route.name == latest.route_name
                ):
                    decision = RuntimeRouteDecision(
                        route=eligible.route,
                        fresh_session=not result_validation_retry.resume_same_session,
                        reason="persisted_result_validation_retry",
                    )
                    if result_validation_retry.resume_same_session:
                        forced_retry_session_id = latest.session_id
                    prompt = result_validation_retry.corrected_prompt(
                        original_prompt,
                        RoutedResultValidationError(
                            "the prior persisted result did not satisfy validation"
                        ),
                    )
                    result_validation_retries_used = 1
                    next_attempt_purpose = "result_validation_correction"
                    next_validation_retry_policy_id = result_validation_retry.policy_id
                    next_validation_result_schema_id = result_codec.schema_id
                else:
                    decision = RuntimeRouteDecision(
                        route=None,
                        fresh_session=True,
                        reason="persisted_result_validation_route_unavailable",
                    )
            else:
                persisted_failure = RuntimeFailure(
                    failure_class=RuntimeFailureClass(latest.failure_class),
                    code=latest.failure_code,
                    detail="persisted runtime failure",
                    failover_permitted=latest.failover_permitted,
                )
                terminal_failure = persisted_failure
                decision = self._router.next_operation_route(
                    workload_kind=workload_kind,
                    workload_key=workload_key,
                    failed_attempt=latest,
                    failure=persisted_failure,
                    required_capabilities=required_capabilities,
                    read_only_policy_proven=True,
                )
        else:
            decision = self._router.first_route_decision(
                required_capabilities=required_capabilities
            )
        if decision.route is None:
            raise RoutedCodexExecutionError(
                "runtime_route_unavailable",
                decision.reason,
                failure_class=(
                    terminal_failure.failure_class if terminal_failure else None
                ),
                failure_code=terminal_failure.code if terminal_failure else "",
                retryable_external_dependency=(
                    _is_retryable_external_runtime_failure(terminal_failure)
                    if terminal_failure
                    else False
                ),
            )
        route = decision.route
        route_session_id = (
            forced_retry_session_id
            if forced_retry_session_id is not None
            else (
                None
                if decision.fresh_session
                else self._session_for_route(conversation_id, route.name)
            )
        )
        active_attempt = self._claim_and_start(
            workload_kind,
            workload_key,
            route,
            route_session_id,
            policy.effect_mode,
            attempt_purpose=next_attempt_purpose,
            validation_retry_policy_id=next_validation_retry_policy_id,
            validation_result_schema_id=next_validation_result_schema_id,
        )
        if existing_attempts and existing_attempts[-1].status == "failed":
            previous = existing_attempts[-1]
            self._finalized_step(
                active_attempt,
                stage="attempt_supersede",
                evidence=lambda: (
                    route_session_id or "",
                    f"codex_session:{route_session_id}" if route_session_id else "",
                    0,
                    0,
                ),
                action=lambda: self._store.mark_agent_runtime_attempt_superseded(
                    previous.id
                ),
            )

        while True:
            transcript_start = 0
            transcript_end = 0
            line_count = 0
            effect_policy_violated = False
            observed_session_id = route_session_id or ""
            transcript_reference = (
                f"codex_session:{observed_session_id}" if observed_session_id else ""
            )

            def current_evidence() -> tuple[str, str, int, int]:
                return (
                    observed_session_id,
                    transcript_reference,
                    transcript_start,
                    max(
                        transcript_start + line_count,
                        transcript_start,
                        transcript_end,
                    ),
                )

            if route_session_id:
                transcript_start = self._finalized_step(
                    active_attempt,
                    stage="transcript_evidence",
                    evidence=current_evidence,
                    action=lambda: self._session_line_counter(route_session_id),
                )

            def observe_stdout_line(line: str) -> None:
                nonlocal line_count, effect_policy_violated, active_attempt
                nonlocal observed_session_id, transcript_reference
                line_count += 1
                streamed_session_id = self._session_id_parser(line)
                if streamed_session_id:
                    if (
                        observed_session_id
                        and streamed_session_id != observed_session_id
                    ):
                        raise RoutedCodexPolicyAbort("runtime_session_conflict")
                    observed_session_id = streamed_session_id
                    transcript_reference = f"codex_session:{streamed_session_id}"
                    active_attempt = self._store.set_agent_runtime_attempt_session(
                        active_attempt.id,
                        streamed_session_id,
                        transcript_reference,
                        owner=self._owner,
                        now=self._now(),
                    )
                if _line_violates_read_only_policy(
                    line,
                    effect_registry=self._effect_registry,
                    native_cli_classifier=self._native_cli_classifier,
                ):
                    persisted = self._store.get_agent_runtime_attempt(active_attempt.id)
                    if persisted is not None and not persisted.first_effect_started_at:
                        active_attempt = (
                            self._store.note_runtime_attempt_effect_started(
                                active_attempt.id,
                                owner=self._owner,
                                at=self._now(),
                            )
                        )
                    if policy.effect_mode is ExecutionEffectMode.READ_ONLY:
                        effect_policy_violated = True
                        raise RoutedCodexPolicyAbort("runtime_effect_policy_violation")

            active_attempt = self._finalized_step(
                active_attempt,
                stage="lease_renewal",
                evidence=current_evidence,
                action=lambda: self._store.renew_runtime_operation_attempt_lease(
                    active_attempt.id,
                    owner=self._owner,
                    lease_seconds=self._lease_seconds,
                    now=self._now(),
                ),
            )
            command, env = self._finalized_step(
                active_attempt,
                stage="command_build",
                evidence=current_evidence,
                action=lambda: command_factory.build(
                    adapter=self._adapter,
                    route=route,
                    prompt=prompt,
                    session_id=route_session_id,
                ),
            )
            active_attempt = self._finalized_step(
                active_attempt,
                stage="lease_renewal",
                evidence=current_evidence,
                action=lambda: self._store.renew_runtime_operation_attempt_lease(
                    active_attempt.id,
                    owner=self._owner,
                    lease_seconds=self._lease_seconds,
                    now=self._now(),
                ),
            )
            process = self._finalized_step(
                active_attempt,
                stage="process_execution",
                evidence=current_evidence,
                action=lambda: self._executor(
                    command,
                    prompt=prompt,
                    env=env,
                    total_timeout_seconds=self._total_timeout_seconds,
                    idle_timeout_seconds=self._idle_timeout_seconds,
                    on_stdout_line=observe_stdout_line,
                ),
            )

            buffered_session_id = self._finalized_step(
                active_attempt,
                stage="transcript_evidence",
                evidence=current_evidence,
                action=lambda: self._session_id_parser(process.stdout),
            )
            if (
                buffered_session_id
                and observed_session_id
                and buffered_session_id != observed_session_id
            ):

                def abort_conflicting_session() -> None:
                    raise RoutedCodexPolicyAbort("runtime_session_conflict")

                self._finalized_step(
                    active_attempt,
                    stage="transcript_evidence",
                    evidence=current_evidence,
                    action=abort_conflicting_session,
                )
            observed_session_id = buffered_session_id or observed_session_id
            transcript_end = max(transcript_start + line_count, transcript_start)
            if observed_session_id:
                transcript_end = max(
                    transcript_end,
                    self._finalized_step(
                        active_attempt,
                        stage="transcript_evidence",
                        evidence=current_evidence,
                        action=lambda: self._session_line_counter(observed_session_id),
                    ),
                )
            transcript_reference = (
                f"codex_session:{observed_session_id}" if observed_session_id else ""
            )

            if process.returncode == 0 and not process.timed_out:
                try:
                    value = parser(process.stdout)
                except RoutedResultValidationError as exc:
                    can_retry_validation = (
                        result_validation_retry is not None
                        and result_validation_retries_used == 0
                        and policy.effect_mode is ExecutionEffectMode.READ_ONLY
                        and (
                            not result_validation_retry.resume_same_session
                            or bool(observed_session_id)
                        )
                    )
                    if (
                        can_retry_validation
                        and observed_session_id
                        and self._probe_session_effect(
                            observed_session_id, transcript_start, transcript_end
                        )
                        is not False
                    ):
                        active_attempt = (
                            self._store.note_runtime_attempt_effect_started(
                                active_attempt.id, owner=self._owner, at=self._now()
                            )
                        )
                        self._terminalize_active_attempt(
                            active_attempt,
                            failure_class=RuntimeFailureClass.CAPABILITY,
                            failure_code="runtime_effect_policy_violation",
                            session_id=observed_session_id,
                            transcript_reference=transcript_reference,
                            transcript_start=transcript_start,
                            transcript_end=transcript_end,
                        )
                        raise RoutedCodexExecutionError(
                            "runtime_effect_policy_violation",
                            failure_class=RuntimeFailureClass.CAPABILITY,
                            failure_code="runtime_effect_policy_violation",
                        ) from exc
                    self._terminalize_active_attempt(
                        active_attempt,
                        failure_class=RuntimeFailureClass.RESULT,
                        failure_code="runtime_result_validation_failed",
                        session_id=observed_session_id,
                        transcript_reference=transcript_reference,
                        transcript_start=transcript_start,
                        transcript_end=transcript_end,
                    )
                    failed_validation_attempt = self._store.get_agent_runtime_attempt(
                        active_attempt.id
                    )
                    if not can_retry_validation or failed_validation_attempt is None:
                        raise RoutedCodexExecutionError(
                            "runtime_result_validation_failed",
                            failure_class=RuntimeFailureClass.RESULT,
                            failure_code="runtime_result_validation_failed",
                        ) from exc
                    successor_session_id = (
                        observed_session_id
                        if result_validation_retry.resume_same_session
                        else None
                    )
                    successor = self._claim_and_start(
                        workload_kind,
                        workload_key,
                        route,
                        successor_session_id,
                        policy.effect_mode,
                        attempt_purpose="result_validation_correction",
                        validation_retry_policy_id=result_validation_retry.policy_id,
                        validation_result_schema_id=result_codec.schema_id,
                    )
                    self._finalized_step(
                        successor,
                        stage="attempt_supersede",
                        evidence=lambda: (
                            successor_session_id or "",
                            (
                                f"codex_session:{successor_session_id}"
                                if successor_session_id
                                else ""
                            ),
                            0,
                            0,
                        ),
                        action=lambda: (
                            self._store.mark_agent_runtime_attempt_superseded(
                                failed_validation_attempt.id
                            )
                        ),
                    )
                    active_attempt = successor
                    route_session_id = successor_session_id
                    prompt = result_validation_retry.corrected_prompt(
                        original_prompt, exc
                    )
                    result_validation_retries_used = 1
                    continue
                except Exception as exc:  # noqa: BLE001
                    self._terminalize_active_attempt(
                        active_attempt,
                        failure_class=RuntimeFailureClass.RESULT,
                        failure_code="runtime_result_invalid",
                        session_id=observed_session_id,
                        transcript_reference=transcript_reference,
                        transcript_start=transcript_start,
                        transcript_end=transcript_end,
                    )
                    raise RoutedCodexExecutionError(
                        "runtime_result_invalid", "result_parse"
                    ) from exc
                result_envelope = self._finalized_step(
                    active_attempt,
                    stage="result_persistence",
                    evidence=current_evidence,
                    action=lambda: result_codec.encode(value),
                )
                if (
                    observed_session_id
                    and policy.effect_mode is ExecutionEffectMode.READ_ONLY
                    and self._probe_session_effect(
                        observed_session_id, transcript_start, transcript_end
                    )
                    is not False
                ):
                    active_attempt = self._store.note_runtime_attempt_effect_started(
                        active_attempt.id, owner=self._owner, at=self._now()
                    )
                    self._terminalize_active_attempt(
                        active_attempt,
                        failure_class=RuntimeFailureClass.CAPABILITY,
                        failure_code="runtime_effect_policy_violation",
                        session_id=observed_session_id,
                        transcript_reference=transcript_reference,
                        transcript_start=transcript_start,
                        transcript_end=transcript_end,
                    )
                    raise RoutedCodexExecutionError("runtime_effect_policy_violation")
                completed = self._finalized_step(
                    active_attempt,
                    stage="attempt_completion",
                    evidence=current_evidence,
                    action=lambda: self._store.complete_agent_runtime_attempt(
                        active_attempt.id,
                        observed_session_id,
                        transcript_reference,
                        transcript_start,
                        transcript_end,
                        owner=self._owner,
                        result_schema_id=result_codec.schema_id,
                        result_envelope_json=result_envelope,
                        conversation_id=conversation_id or "",
                        route_name=route.name,
                        now=self._now(),
                    ),
                )
                return RoutedCodexExecutionResult(
                    value=value,
                    route_name=route.name,
                    attempt_id=completed.id,
                    session_id=observed_session_id,
                    transcript_start=transcript_start,
                    transcript_end=transcript_end,
                )

            failure = self._finalized_step(
                active_attempt,
                stage="failure_classification",
                evidence=current_evidence,
                action=lambda: self._adapter.classify_failure(
                    process.stdout,
                    process.stderr,
                    process.returncode,
                    timed_out=process.timed_out,
                    timeout_kind=process.timeout_kind,
                ),
            )
            if (
                observed_session_id
                and policy.effect_mode is ExecutionEffectMode.READ_ONLY
                and self._probe_session_effect(
                    observed_session_id, transcript_start, transcript_end
                )
                is not False
            ):
                persisted = self._store.get_agent_runtime_attempt(active_attempt.id)
                if persisted is not None and not persisted.first_effect_started_at:
                    active_attempt = self._store.note_runtime_attempt_effect_started(
                        active_attempt.id, owner=self._owner, at=self._now()
                    )
                effect_policy_violated = True
            if failure.route_pause_required:
                self._finalized_step(
                    active_attempt,
                    stage="route_pause",
                    evidence=current_evidence,
                    action=lambda: self._store.open_runtime_route_pause(
                        route.name,
                        failure.code,
                        self._now() + self._config.retry_delay,
                    ),
                )
            failed_attempt = self._finalized_step(
                active_attempt,
                stage="attempt_failure",
                evidence=current_evidence,
                action=lambda: self._store.fail_agent_runtime_attempt(
                    active_attempt.id,
                    failure.failure_class.value,
                    failure.code,
                    failure.failover_permitted,
                    session_id=observed_session_id,
                    transcript_reference=transcript_reference,
                    transcript_start=transcript_start,
                    transcript_end=transcript_end,
                    owner=self._owner,
                    now=self._now(),
                ),
            )
            if effect_policy_violated:
                raise RoutedCodexExecutionError("runtime_effect_policy_violation")
            if (
                policy.effect_mode is ExecutionEffectMode.EFFECTFUL
                or result_validation_retries_used > 0
            ):
                raise RoutedCodexExecutionError(
                    "runtime_execution_failed",
                    failure_class=failure.failure_class,
                    failure_code=failure.code,
                    retryable_external_dependency=(
                        _is_retryable_external_runtime_failure(failure)
                    ),
                )

            next_decision = self._router.next_operation_route(
                workload_kind=workload_kind,
                workload_key=workload_key,
                failed_attempt=failed_attempt,
                failure=failure,
                required_capabilities=required_capabilities,
                read_only_policy_proven=True,
            )
            if next_decision.route is None:
                raise RoutedCodexExecutionError(
                    "runtime_execution_failed",
                    next_decision.reason,
                    failure_class=failure.failure_class,
                    failure_code=failure.code,
                    retryable_external_dependency=(
                        _is_retryable_external_runtime_failure(failure)
                    ),
                )
            route = next_decision.route
            route_session_id = (
                None
                if next_decision.fresh_session
                else self._session_for_route(conversation_id, route.name)
            )
            successor = self._claim_and_start(
                workload_kind, workload_key, route, route_session_id, policy.effect_mode
            )
            self._finalized_step(
                successor,
                stage="attempt_supersede",
                evidence=lambda: (
                    route_session_id or "",
                    (f"codex_session:{route_session_id}" if route_session_id else ""),
                    0,
                    0,
                ),
                action=lambda: self._store.mark_agent_runtime_attempt_superseded(
                    failed_attempt.id
                ),
            )
            active_attempt = successor

    def _claim_and_start(
        self,
        workload_kind: str,
        workload_key: str,
        route: RuntimeRoute,
        session_id: str | None,
        effect_mode: ExecutionEffectMode,
        *,
        attempt_purpose: str = "normal",
        validation_retry_policy_id: str = "",
        validation_result_schema_id: str = "",
    ) -> AgentRuntimeAttempt:
        try:
            attempt = self._store.claim_runtime_operation_attempt(
                workload_kind,
                workload_key,
                route.name,
                route.runtime_kind.value,
                route.credential_mode.value,
                route.model,
                session_mode=(
                    RuntimeAttemptSessionMode.RESUME
                    if session_id
                    else RuntimeAttemptSessionMode.FRESH
                ),
                source_session_id=session_id or "",
                attempt_purpose=attempt_purpose,
                validation_retry_policy_id=validation_retry_policy_id,
                validation_result_schema_id=validation_result_schema_id,
                owner=self._owner,
                lease_seconds=self._lease_seconds,
                now=self._now(),
            )
        except RuntimeRoutePausedError as exc:
            raise RoutedCodexExecutionError("runtime_route_unavailable") from exc
        try:
            running = self._store.mark_agent_runtime_attempt_running_once(
                attempt.id,
                owner=self._owner,
                lease_seconds=self._lease_seconds,
                effectful=effect_mode is ExecutionEffectMode.EFFECTFUL,
                now=self._now(),
            )
        except AgentRuntimeAttemptStartConflictError as exc:
            raise RoutedCodexExecutionError("runtime_attempt_active") from exc
        return running

    def _session_for_route(
        self, conversation_id: str | None, route_name: str
    ) -> str | None:
        if not conversation_id:
            return None
        return self._store.get_conversation_runtime_session(conversation_id, route_name)

    def _probe_session_effect(
        self, session_id: str, transcript_start: int, transcript_end: int
    ) -> bool | None:
        try:
            return self._session_effect_probe(
                session_id, transcript_start, transcript_end
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            return None

    def _finalized_step(
        self,
        attempt: AgentRuntimeAttempt,
        *,
        stage: str,
        evidence: Callable[[], tuple[str, str, int, int]],
        action: Callable[[], StepT],
    ) -> StepT:
        try:
            return action()
        except RoutedCodexPolicyAbort as exc:
            session_id, reference, start, end = evidence()
            self._terminalize_active_attempt(
                attempt,
                failure_class=(
                    RuntimeFailureClass.SESSION
                    if exc.code == "runtime_session_conflict"
                    else RuntimeFailureClass.CAPABILITY
                ),
                failure_code=exc.code,
                session_id=session_id,
                transcript_reference=reference,
                transcript_start=start,
                transcript_end=end,
            )
            raise RoutedCodexExecutionError(exc.code) from exc
        except Exception as exc:
            session_id, reference, start, end = evidence()
            failure_code = {
                "process_execution": "runtime_executor_failed",
                "result_parse": "runtime_result_invalid",
            }.get(stage, f"runtime_{stage}_failed")
            self._terminalize_active_attempt(
                attempt,
                failure_class=(
                    RuntimeFailureClass.RESULT
                    if stage in {"result_parse", "result_persistence"}
                    else RuntimeFailureClass.PROCESS
                ),
                failure_code=failure_code,
                session_id=session_id,
                transcript_reference=reference,
                transcript_start=start,
                transcript_end=end,
            )
            error_code = {
                "process_execution": "runtime_executor_failed",
                "result_parse": "runtime_result_invalid",
                "result_persistence": "runtime_result_invalid",
            }.get(stage, "runtime_post_start_failed")
            raise RoutedCodexExecutionError(error_code, stage) from exc

    def _terminalize_active_attempt(
        self,
        attempt: AgentRuntimeAttempt,
        *,
        failure_class: RuntimeFailureClass,
        failure_code: str,
        session_id: str,
        transcript_reference: str,
        transcript_start: int,
        transcript_end: int,
    ) -> None:
        persisted = self._store.get_agent_runtime_attempt(attempt.id)
        if persisted is None or persisted.status not in {"starting", "running"}:
            return
        self._store.fail_agent_runtime_attempt(
            persisted.id,
            failure_class.value,
            failure_code,
            False,
            session_id=session_id or persisted.session_id,
            transcript_reference=(
                transcript_reference or persisted.transcript_reference
            ),
            transcript_start=max(transcript_start, 0),
            transcript_end=max(transcript_end, transcript_start, 0),
            owner=self._owner,
            now=self._now(),
        )


def local_codex_session_effect_probe(
    *,
    codex_home: Path | None = None,
    effect_registry: McpToolEffectRegistry | None = None,
    native_cli_classifier: NativeCliMetadataClassifier | None = None,
) -> Callable[[str, int, int], bool | None]:
    """Build a fail-closed probe over an exact persisted Codex transcript range."""

    registry = effect_registry or McpToolEffectRegistry.default()
    classifier = native_cli_classifier or NativeCliMetadataClassifier()

    def probe(session_id: str, start_line: int, end_line: int) -> bool | None:
        if start_line < 0 or end_line < start_line:
            return None
        path = find_codex_session_path(session_id, codex_home=codex_home)
        if path is None:
            return None
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return None
        if end_line > len(lines):
            return None
        for raw_line in lines[start_line:end_line]:
            try:
                payload = json.loads(raw_line)
            except json.JSONDecodeError:
                return None
            item = _persisted_session_effect_item(payload)
            if item is None:
                continue
            outcome = _classify_session_effect_item(
                item,
                effect_registry=registry,
                native_cli_classifier=classifier,
            )
            if outcome is not False:
                return outcome
        return False

    return probe


def _persisted_session_effect_item(payload: object) -> dict[str, object] | None:
    if not isinstance(payload, dict):
        return None
    if payload.get("type") == "item.started":
        item = payload.get("item")
        return item if isinstance(item, dict) else None
    if payload.get("type") != "response_item":
        return None
    item = payload.get("payload")
    if not isinstance(item, dict) or item.get("type") != "function_call":
        return None
    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        return {"type": "unknown_tool_call"}
    arguments = item.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return {"type": "unknown_tool_call"}
    if not isinstance(arguments, dict):
        return {"type": "unknown_tool_call"}
    if name.startswith("mcp__"):
        parts = name.split("__", 2)
        if len(parts) != 3 or not parts[1] or not parts[2]:
            return {"type": "unknown_tool_call"}
        return {
            "type": "mcp_tool_call",
            "server": parts[1],
            "tool": parts[2],
            "arguments": arguments,
        }
    if name in {"exec_command", "shell", "command_execution"}:
        command = arguments.get("cmd") or arguments.get("command")
        return {"type": "command_execution", "command": command}
    return {"type": "unknown_tool_call"}


def _classify_session_effect_item(
    item: dict[str, object],
    *,
    effect_registry: McpToolEffectRegistry,
    native_cli_classifier: NativeCliMetadataClassifier,
) -> bool | None:
    metadata = item.get("metadata")
    if isinstance(metadata, dict):
        if metadata.get("effect") == "effectful":
            return True
        if metadata.get("effect") == "read_only":
            return False
    if item.get("type") == "mcp_tool_call":
        call = effect_registry.classify(item)
        if call is None:
            return None
        return call.effect is EffectKind.EFFECTFUL
    if item.get("type") == "command_execution":
        try:
            command = native_cli_classifier.classify(item)
        except RuntimeError:
            return None
        if command is None or command.effect is None:
            return None
        return command.effect is EffectKind.EFFECTFUL
    return None


def _line_violates_read_only_policy(
    line: str,
    *,
    effect_registry: McpToolEffectRegistry,
    native_cli_classifier: NativeCliMetadataClassifier,
) -> bool:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict) or payload.get("type") != "item.started":
        return False
    item = payload.get("item")
    if not isinstance(item, dict):
        return False
    metadata = item.get("metadata")
    if isinstance(metadata, dict) and metadata.get("effect") == "effectful":
        return True
    item_type = item.get("type")
    if item_type == "command_execution":
        command = native_cli_classifier.classify(item)
        return command is None or command.effect is not EffectKind.READ_ONLY
    if item_type == "mcp_tool_call":
        call = effect_registry.classify(item)
        return call is None or call.effect is not EffectKind.READ_ONLY
    return False


def _parse_timestamp(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value.strip())
    else:
        raise ValueError("timestamp must be a non-empty ISO value")
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _run_identity_matches(caller: AgentRun, persisted: AgentRun) -> bool:
    """Compare the immutable turn identity, but deliberately not mutable safety state."""
    return (
        caller.id,
        caller.reply_task_id,
        caller.execution_generation,
        caller.role,
        caller.proposal_revision,
        caller.turn_attempt,
        caller.parent_agent_run_id,
        caller.operation_id,
    ) == (
        persisted.id,
        persisted.reply_task_id,
        persisted.execution_generation,
        persisted.role,
        persisted.proposal_revision,
        persisted.turn_attempt,
        persisted.parent_agent_run_id,
        persisted.operation_id,
    )


def _failure_matches_persisted_attempt(
    failure: RuntimeFailure, attempt: AgentRuntimeAttempt
) -> bool:
    """Accept only failure fields recorded in the attempt ledger.

    The attempt ledger intentionally persists failure class, code, and failover
    permission. RuntimeFailure's retry and pause hints are not persisted and do
    not affect route selection at this layer.
    """
    return (
        failure.failure_class.value == attempt.failure_class
        and failure.code == attempt.failure_code
        and failure.failover_permitted == attempt.failover_permitted
    )
