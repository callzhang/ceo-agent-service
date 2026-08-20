"""Synthetic, route-scoped health probes for production agent runtimes."""

from __future__ import annotations

import json
import sys
import tempfile
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock

from app.agent_effects import McpToolEffectRegistry
from app.agent_result import EffectKind
from app.agent_runtime_config import AgentRuntimeConfig
from app.agent_runtime_contracts import (
    PROBE_VERIFIED_RUNTIME_CAPABILITIES,
    RuntimeCapabilitySnapshot,
    RuntimeEventType,
    RuntimeFailure,
    RuntimeFailureClass,
    RuntimeKind,
    RuntimeRoute,
)
from app.agent_runtime_production import RuntimeCapabilityRegistry
from app.agent_runtime_router import (
    ApprovedCodexCommandFactory,
    ProcessExecutor,
)
from app.claude_runtime_adapter import ClaudeCommandPolicy, ClaudeRuntimeAdapter
from app.codex_runtime_adapter import CodexRuntimeAdapter
from app.process_runner import run_process_with_idle_timeout
from app.service_codex_config import ServiceMcpServer
from app.store import AutoReplyStore

PROBE_TOTAL_TIMEOUT_SECONDS = 60.0
PROBE_IDLE_TIMEOUT_SECONDS = 30.0
_PROBE_PROMPT = 'Return only the synthetic probe result {"ok":true}.'
_PROBE_CANONICAL_RESULT = '{"ok":true}'
_PROBE_DEVELOPER_INSTRUCTIONS = (
    "This is a synthetic runtime health probe. Do not call tools, read files, "
    'or access business data. Return exactly {"ok":true}.'
)
_PROBE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {"ok": {"const": True}},
    "required": ["ok"],
    "additionalProperties": False,
}
_BASE_CAPABILITIES = PROBE_VERIFIED_RUNTIME_CAPABILITIES
_CLAUDE_EFFECT_TOOL = "mcp__runtime_probe__record_effect_start"
_CLAUDE_EFFECT_PROMPT = (
    "Call the dedicated runtime probe tool exactly once with marker "
    '"ceo-agent-runtime-probe-v1", then return only {"ok":true}.'
)


class AgentRuntimeProbe:
    """Run one isolated, schema-constrained, non-business route probe."""

    def __init__(
        self,
        *,
        config: AgentRuntimeConfig,
        codex_bin: str = "codex",
        claude_bin: str = "claude",
        executor: ProcessExecutor = run_process_with_idle_timeout,
        now: Callable[[], datetime] | None = None,
        temporary_root: Path | None = None,
        total_timeout_seconds: float = PROBE_TOTAL_TIMEOUT_SECONDS,
        idle_timeout_seconds: float = PROBE_IDLE_TIMEOUT_SECONDS,
    ) -> None:
        self._config = config
        self._codex_bin = codex_bin
        self._claude_bin = claude_bin
        self._executor = executor
        self._now = now or (lambda: datetime.now(UTC))
        self._temporary_root = temporary_root
        self._total_timeout_seconds = total_timeout_seconds
        self._idle_timeout_seconds = idle_timeout_seconds

    def run(self, *, route_name: str) -> RuntimeCapabilitySnapshot:
        route = self._route(route_name)
        checked_at = self._now().astimezone(UTC)
        expires_at = checked_at + self._config.probe_interval
        try:
            with tempfile.TemporaryDirectory(
                prefix="ceo-agent-runtime-probe-",
                dir=self._temporary_root,
            ) as raw_workspace:
                workspace = Path(raw_workspace)
                schema_path = workspace / "probe-result.schema.json"
                schema_path.write_text(
                    json.dumps(_PROBE_SCHEMA, separators=(",", ":")),
                    encoding="utf-8",
                )
                if route.runtime_kind is RuntimeKind.CLAUDE_CLI:
                    return self._run_claude_probe(
                        route=route,
                        workspace=workspace,
                        checked_at=checked_at,
                        expires_at=expires_at,
                    )
                adapter = CodexRuntimeAdapter(
                    workspace,
                    self._config,
                    codex_bin=self._codex_bin,
                )
                factory = ApprovedCodexCommandFactory.read_only_without_tools(
                    developer_instructions=_PROBE_DEVELOPER_INSTRUCTIONS,
                    output_schema_path=schema_path,
                    use_output_schema=True,
                )
                command, env = factory.build(
                    adapter=adapter,
                    route=route,
                    prompt=_PROBE_PROMPT,
                    session_id=None,
                )
                completed = self._executor(
                    command,
                    prompt=_PROBE_PROMPT,
                    env=env,
                    total_timeout_seconds=self._total_timeout_seconds,
                    idle_timeout_seconds=self._idle_timeout_seconds,
                )
                if completed.returncode != 0 or completed.timed_out:
                    failure = adapter.classify_failure(
                        completed.stdout,
                        completed.stderr,
                        completed.returncode,
                        timed_out=completed.timed_out,
                        timeout_kind=completed.timeout_kind,
                    )
                    return _snapshot(
                        route=route,
                        checked_at=checked_at,
                        expires_at=expires_at,
                        failure=failure,
                    )
                stream_failure_code = _probe_stream_failure_code(completed.stdout)
                if stream_failure_code is not None:
                    return _snapshot(
                        route=route,
                        checked_at=checked_at,
                        expires_at=expires_at,
                        failure=_probe_failure(
                            stream_failure_code,
                            (
                                "Runtime probe attempted an action."
                                if stream_failure_code
                                == "runtime_probe_policy_violation"
                                else "Runtime probe did not produce one complete structured turn."
                            ),
                        ),
                    )
                capabilities = _route_capabilities(adapter=adapter, route=route)
        except Exception:  # noqa: BLE001 - fail closed across provider/runtime code
            return _snapshot(
                route=route,
                checked_at=checked_at,
                expires_at=expires_at,
                failure=_probe_failure(
                    "runtime_probe_failed",
                    "Runtime probe could not be completed.",
                ),
            )
        return RuntimeCapabilitySnapshot(
            route_name=route.name,
            capabilities=capabilities,
            healthy=True,
            checked_at=checked_at.isoformat(),
            expires_at=expires_at.isoformat(),
        )

    def _run_claude_probe(
        self,
        *,
        route: RuntimeRoute,
        workspace: Path,
        checked_at: datetime,
        expires_at: datetime,
    ) -> RuntimeCapabilitySnapshot:
        effects = McpToolEffectRegistry(
            {("runtime_probe", "record_effect_start"): EffectKind.READ_ONLY}
        )
        adapter = ClaudeRuntimeAdapter(
            workspace=workspace,
            config=self._config,
            claude_bin=self._claude_bin,
            effect_registry=effects,
            service_mcp_servers=(
                ServiceMcpServer(
                    name="runtime_probe",
                    command=sys.executable,
                    args=("-m", "app.claude_runtime_probe_tool"),
                ),
            ),
        )
        try:
            baseline_failure, baseline_events = self._run_one_claude_probe(
                adapter=adapter,
                route=route,
                prompt=_PROBE_PROMPT,
                policy=ClaudeCommandPolicy.no_tools(),
            )
            if baseline_failure is not None:
                return _snapshot(
                    route=route,
                    checked_at=checked_at,
                    expires_at=expires_at,
                    failure=baseline_failure,
                )
            if not _claude_probe_grammar_valid(baseline_events, effect=False):
                return _snapshot(
                    route=route,
                    checked_at=checked_at,
                    expires_at=expires_at,
                    failure=_probe_failure(
                        "runtime_probe_grammar_invalid",
                        "Runtime probe normalized grammar is invalid.",
                    ),
                )
            effect_failure, effect_events = self._run_one_claude_probe(
                adapter=adapter,
                route=route,
                prompt=_CLAUDE_EFFECT_PROMPT,
                policy=ClaudeCommandPolicy.reviewed(mcp_tools=(_CLAUDE_EFFECT_TOOL,)),
            )
            if effect_failure is not None:
                return _snapshot(
                    route=route,
                    checked_at=checked_at,
                    expires_at=expires_at,
                    failure=effect_failure,
                )
            starts = [
                event
                for event in effect_events
                if _matching_probe_tool_event(
                    event, RuntimeEventType.ITEM_STARTED.value
                )
            ]
            completions = [
                event
                for event in effect_events
                if _matching_probe_tool_event(
                    event, RuntimeEventType.ITEM_COMPLETED.value
                )
            ]
            failed = any(
                event.get("type") == RuntimeEventType.ITEM_FAILED.value
                for event in effect_events
            )
            turn_completions = sum(
                event.get("type") == RuntimeEventType.TURN_COMPLETED.value
                for event in effect_events
            )
            turn_starts = [
                event
                for event in effect_events
                if event.get("type") == RuntimeEventType.TURN_STARTED.value
            ]
            completed_turns = [
                event
                for event in effect_events
                if event.get("type") == RuntimeEventType.TURN_COMPLETED.value
            ]
            all_item_starts = sum(
                event.get("type") == RuntimeEventType.ITEM_STARTED.value
                for event in effect_events
            )
            expected_call = effects.classify(
                {
                    "type": "mcp_tool_call",
                    "server": "runtime_probe",
                    "tool": "record_effect_start",
                    "arguments": {"marker": "ceo-agent-runtime-probe-v1"},
                }
            )
            assert expected_call is not None
            if not starts:
                return _snapshot(
                    route=route,
                    checked_at=checked_at,
                    expires_at=expires_at,
                    failure=_probe_failure(
                        "runtime_probe_effect_visibility_missing",
                        "Runtime probe effect-start evidence is missing.",
                    ),
                )
            if not _claude_probe_grammar_valid(effect_events, effect=True):
                return _snapshot(
                    route=route,
                    checked_at=checked_at,
                    expires_at=expires_at,
                    failure=_probe_failure(
                        "runtime_probe_grammar_invalid",
                        "Runtime probe normalized grammar is invalid.",
                    ),
                )
            exact_evidence = (
                len(starts) == 1
                and len(completions) == 1
                and not failed
                and all_item_starts == 1
                and len(turn_starts) == 1
                and len(completed_turns) == 1
                and turn_completions == 1
                and starts[0]["item"]["id"] == completions[0]["item"]["id"]
                and completions[0]["item"].get("status") == "completed"
                and starts[0]["item"].get("metadata", {}).get("operation_digest")
                == expected_call.operation_digest
                and completions[0]["item"].get("metadata", {}).get("operation_digest")
                == expected_call.operation_digest
                and turn_starts[0].get("session_id")
                == completed_turns[0].get("session_id")
            )
            if not exact_evidence:
                return _snapshot(
                    route=route,
                    checked_at=checked_at,
                    expires_at=expires_at,
                    failure=_probe_failure(
                        "runtime_probe_effect_visibility_missing",
                        "Runtime probe effect-start evidence is missing.",
                    ),
                )
        finally:
            adapter._mcp_proxy.close()
        return RuntimeCapabilitySnapshot(
            route_name=route.name,
            capabilities=_BASE_CAPABILITIES | {"audit_effect_visibility"},
            healthy=True,
            checked_at=checked_at.isoformat(),
            expires_at=expires_at.isoformat(),
        )

    def _run_one_claude_probe(
        self,
        *,
        adapter: ClaudeRuntimeAdapter,
        route: RuntimeRoute,
        prompt: str,
        policy: ClaudeCommandPolicy,
    ) -> tuple[RuntimeFailure | None, tuple[dict[str, object], ...]]:
        command = adapter.build_command(
            route=route,
            session_id=None,
            max_turns=2,
            policy=policy,
        )
        env = adapter.build_env(route, command=command)
        normalizer = adapter.new_event_normalizer(command=command)
        try:
            completed = self._executor(
                command,
                prompt=prompt,
                env=env,
                total_timeout_seconds=self._total_timeout_seconds,
                idle_timeout_seconds=self._idle_timeout_seconds,
            )
            if completed.returncode != 0 or completed.timed_out:
                return (
                    adapter.classify_failure(
                        completed.stdout,
                        completed.stderr,
                        completed.returncode,
                        timed_out=completed.timed_out,
                        timeout_kind=completed.timeout_kind,
                    ),
                    (),
                )
            normalized = []
            for line in completed.stdout.splitlines():
                if line.strip():
                    normalized.extend(normalizer.normalize_events(json.loads(line)))
            normalizer.finalize()
            result = adapter.parse_final_result(
                normalizer=normalizer,
                proof=normalizer.terminal_proof(),
                parser=_parse_probe_result,
            )
            if result != {"ok": True}:
                raise ValueError("Claude probe result is invalid")
            return None, tuple(normalized)
        finally:
            adapter.finish_invocation(command)

    def _route(self, route_name: str) -> RuntimeRoute:
        route = next(
            (
                candidate
                for candidate in self._config.routes
                if candidate.name == route_name
            ),
            None,
        )
        if route is None:
            raise ValueError("runtime route is not configured")
        return route


class RuntimeCapabilityRefresher:
    """Refresh one shared registry and maintain independent route pauses."""

    def __init__(
        self,
        *,
        config: AgentRuntimeConfig,
        store: AutoReplyStore,
        registry: RuntimeCapabilityRegistry,
        probe: AgentRuntimeProbe,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._config = config
        self._store = store
        self._registry = registry
        self._probe = probe
        self._now = now or (lambda: datetime.now(UTC))
        self._lock = RLock()

    @property
    def interval_seconds(self) -> float:
        return self._config.probe_interval.total_seconds()

    def refresh_expired(
        self,
        *,
        route_names: Iterable[str] | None = None,
        force: bool = False,
    ) -> Mapping[str, RuntimeCapabilitySnapshot]:
        selected = tuple(route_names or (route.name for route in self._config.routes))
        configured = {route.name for route in self._config.routes}
        if len(selected) != len(set(selected)) or set(selected) - configured:
            raise ValueError("runtime refresh routes must be uniquely configured")
        with self._lock:
            now = self._now().astimezone(UTC)
            snapshots = dict(self._registry)
            for route_name in selected:
                current = snapshots.get(route_name)
                if (
                    not force
                    and current is not None
                    and _snapshot_is_current(current, now)
                ):
                    continue
                try:
                    snapshot = self._probe.run(route_name=route_name)
                except Exception:  # noqa: BLE001 - isolate one route from all others
                    route = next(
                        route
                        for route in self._config.routes
                        if route.name == route_name
                    )
                    snapshot = _snapshot(
                        route=route,
                        checked_at=now,
                        expires_at=now + self._config.probe_interval,
                        failure=_probe_failure(
                            "runtime_probe_failed",
                            "Runtime probe could not be completed.",
                        ),
                    )
                snapshots[route_name] = snapshot
                if snapshot.healthy and snapshot.failure is None:
                    self._store.close_runtime_route_pause(route_name)
                else:
                    failure_code = (
                        snapshot.failure.code
                        if snapshot.failure is not None
                        else "runtime_probe_failed"
                    )
                    self._store.open_runtime_route_pause(
                        route_name,
                        failure_code,
                        retry_at=(now + self._config.retry_delay).isoformat(),
                    )
            self._registry.refresh(snapshots)
            return {name: snapshots[name] for name in selected if name in snapshots}


def _snapshot_is_current(snapshot: RuntimeCapabilitySnapshot, now: datetime) -> bool:
    try:
        expires_at = datetime.fromisoformat(snapshot.expires_at)
    except ValueError:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at.astimezone(UTC) > now


def _snapshot(
    *,
    route: RuntimeRoute,
    checked_at: datetime,
    expires_at: datetime,
    failure: RuntimeFailure,
) -> RuntimeCapabilitySnapshot:
    return RuntimeCapabilitySnapshot(
        route_name=route.name,
        healthy=False,
        checked_at=checked_at.isoformat(),
        expires_at=expires_at.isoformat(),
        failure=failure,
    )


def _probe_failure(code: str, detail: str) -> RuntimeFailure:
    return RuntimeFailure(
        failure_class=RuntimeFailureClass.CAPABILITY,
        code=code,
        detail=detail,
        route_pause_required=True,
    )


def _probe_stream_failure_code(raw: str) -> str | None:
    try:
        payloads = [json.loads(line) for line in raw.splitlines() if line.strip()]
    except json.JSONDecodeError:
        return "runtime_probe_incomplete"
    if not payloads or any(not isinstance(payload, dict) for payload in payloads):
        return "runtime_probe_incomplete"
    types = [payload.get("type") for payload in payloads]
    allowed_types = {
        "thread.started",
        "turn.started",
        "item.completed",
        "turn.completed",
    }
    if any(payload_type not in allowed_types for payload_type in types):
        return "runtime_probe_policy_violation"
    for payload in payloads:
        if payload.get("type") != "item.completed":
            continue
        item = payload.get("item")
        if not isinstance(item, dict) or item.get("type") != "agent_message":
            return "runtime_probe_policy_violation"
    if types.count("thread.started") != 1:
        return "runtime_probe_incomplete"
    if types.count("turn.started") != 1 or types.count("turn.completed") != 1:
        return "runtime_probe_incomplete"
    if "turn.failed" in types:
        return "runtime_probe_incomplete"
    thread_index = types.index("thread.started")
    start_index = types.index("turn.started")
    end_index = types.index("turn.completed")
    thread_id = payloads[thread_index].get("thread_id")
    if not isinstance(thread_id, str) or not thread_id.strip():
        return "runtime_probe_incomplete"
    if not thread_index < start_index < end_index:
        return "runtime_probe_incomplete"
    candidates = []
    for payload in payloads[start_index + 1 : end_index]:
        item = payload.get("item")
        if (
            payload.get("type") == "item.completed"
            and isinstance(item, dict)
            and item.get("type") == "agent_message"
            and isinstance(item.get("text"), str)
        ):
            candidates.append(item["text"])
    if len(candidates) != 1:
        return "runtime_probe_incomplete"
    try:
        result = json.loads(candidates[0])
    except json.JSONDecodeError:
        return "runtime_probe_incomplete"
    return None if result == {"ok": True} else "runtime_probe_incomplete"


def _parse_probe_result(raw: str) -> dict[str, object]:
    result = json.loads(raw)
    if result != {"ok": True}:
        raise ValueError("runtime probe result is invalid")
    return result


def _matching_probe_tool_event(event: dict[str, object], event_type: str) -> bool:
    item = event.get("item")
    return (
        event.get("type") == event_type
        and isinstance(item, dict)
        and item.get("server") == "runtime_probe"
        and item.get("tool") == "record_effect_start"
        and isinstance(item.get("id"), str)
        and bool(item["id"])
    )


def _claude_probe_grammar_valid(
    events: tuple[dict[str, object], ...], *, effect: bool
) -> bool:
    expected_types = (
        (
            RuntimeEventType.TURN_STARTED.value,
            RuntimeEventType.ITEM_STARTED.value,
            RuntimeEventType.ITEM_COMPLETED.value,
            RuntimeEventType.ITEM_COMPLETED.value,
            RuntimeEventType.TURN_COMPLETED.value,
        )
        if effect
        else (
            RuntimeEventType.TURN_STARTED.value,
            RuntimeEventType.ITEM_COMPLETED.value,
            RuntimeEventType.TURN_COMPLETED.value,
        )
    )
    if tuple(event.get("type") for event in events) != expected_types:
        return False
    message = events[3 if effect else 1].get("item")
    if not (
        isinstance(message, dict)
        and message.get("type") == "agent_message"
        and message.get("text") == _PROBE_CANONICAL_RESULT
    ):
        return False
    start_session = events[0].get("session_id")
    end_session = events[-1].get("session_id")
    return (
        isinstance(start_session, str)
        and bool(start_session)
        and start_session == end_session
    )


def _route_capabilities(
    *, adapter: CodexRuntimeAdapter, route: RuntimeRoute
) -> frozenset[str]:
    # This no-tools probe proves only the transport/schema/read-only boundary.
    # Configured MCP transports are not evidence that any business tool works.
    del adapter, route
    return _BASE_CAPABILITIES
