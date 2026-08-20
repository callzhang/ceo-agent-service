"""Synthetic, route-scoped health probes for production agent runtimes."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock

from app.agent_runtime_config import AgentRuntimeConfig
from app.agent_runtime_contracts import (
    PROBE_VERIFIED_RUNTIME_CAPABILITIES,
    RuntimeCapabilitySnapshot,
    RuntimeFailure,
    RuntimeFailureClass,
    RuntimeRoute,
)
from app.agent_runtime_production import RuntimeCapabilityRegistry
from app.agent_runtime_router import (
    ApprovedCodexCommandFactory,
    ProcessExecutor,
)
from app.codex_runtime_adapter import CodexRuntimeAdapter
from app.process_runner import run_process_with_idle_timeout
from app.store import AutoReplyStore

PROBE_TOTAL_TIMEOUT_SECONDS = 60.0
PROBE_IDLE_TIMEOUT_SECONDS = 30.0
_PROBE_PROMPT = 'Return only the synthetic probe result {"ok":true}.'
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


class AgentRuntimeProbe:
    """Run one isolated, schema-constrained, non-business route probe."""

    def __init__(
        self,
        *,
        config: AgentRuntimeConfig,
        codex_bin: str = "codex",
        executor: ProcessExecutor = run_process_with_idle_timeout,
        now: Callable[[], datetime] | None = None,
        temporary_root: Path | None = None,
        total_timeout_seconds: float = PROBE_TOTAL_TIMEOUT_SECONDS,
        idle_timeout_seconds: float = PROBE_IDLE_TIMEOUT_SECONDS,
    ) -> None:
        self._config = config
        self._codex_bin = codex_bin
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

    def _route(self, route_name: str) -> RuntimeRoute:
        route = next(
            (candidate for candidate in self._config.routes if candidate.name == route_name),
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
                if not force and current is not None and _snapshot_is_current(current, now):
                    continue
                try:
                    snapshot = self._probe.run(route_name=route_name)
                except Exception:  # noqa: BLE001 - isolate one route from all others
                    route = next(
                        route for route in self._config.routes if route.name == route_name
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


def _route_capabilities(
    *, adapter: CodexRuntimeAdapter, route: RuntimeRoute
) -> frozenset[str]:
    # This no-tools probe proves only the transport/schema/read-only boundary.
    # Configured MCP transports are not evidence that any business tool works.
    del adapter, route
    return _BASE_CAPABILITIES
