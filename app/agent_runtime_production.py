from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from pathlib import Path
from threading import RLock

from app.agent_runtime_config import load_runtime_config
from app.agent_runtime_contracts import RuntimeCapabilitySnapshot
from app.agent_runtime_router import (
    AgentRuntimeRouter,
    ProcessExecutor,
    RoutedCodexExecution,
    local_codex_session_effect_probe,
)
from app.codex_runtime_adapter import CodexRuntimeAdapter
from app.store import AutoReplyStore


class RuntimeCapabilityRegistry(Mapping[str, RuntimeCapabilitySnapshot]):
    """A live, atomically refreshable capability view shared by routers."""

    def __init__(
        self, snapshots: Mapping[str, RuntimeCapabilitySnapshot] | None = None
    ) -> None:
        self._lock = RLock()
        self._snapshots: dict[str, RuntimeCapabilitySnapshot] = {}
        self.refresh(snapshots or {})

    def refresh(self, snapshots: Mapping[str, RuntimeCapabilitySnapshot]) -> None:
        replacement = dict(snapshots)
        for route_name, snapshot in replacement.items():
            if route_name != snapshot.route_name:
                raise ValueError("runtime capability registry key mismatch")
        with self._lock:
            self._snapshots = replacement

    def __getitem__(self, key: str) -> RuntimeCapabilitySnapshot:
        with self._lock:
            return self._snapshots[key]

    def __iter__(self) -> Iterator[str]:
        with self._lock:
            return iter(tuple(self._snapshots))

    def __len__(self) -> int:
        with self._lock:
            return len(self._snapshots)


PRODUCTION_RUNTIME_CAPABILITIES = RuntimeCapabilityRegistry()


def build_production_routed_codex_execution(
    *,
    store: AutoReplyStore,
    workspace: Path,
    total_timeout_seconds: float,
    idle_timeout_seconds: float,
    codex_bin: str = "codex",
    executor: ProcessExecutor | None = None,
    capability_registry: RuntimeCapabilityRegistry = PRODUCTION_RUNTIME_CAPABILITIES,
) -> RoutedCodexExecution:
    """Build the single reviewed production routing stack.

    The legacy exception is deliberately limited to the first OAuth attempt
    when no OAuth snapshot has ever been published. Service-API routes always
    require a current capability snapshot.
    """

    runtime_config = load_runtime_config(os.environ)
    kwargs = {
        "store": store,
        "config": runtime_config,
        "router": AgentRuntimeRouter(
            routes=runtime_config.routes,
            store=store,
            snapshots=capability_registry,
        ),
        "adapter": CodexRuntimeAdapter(workspace, runtime_config, codex_bin=codex_bin),
        "session_effect_probe": local_codex_session_effect_probe(),
        "total_timeout_seconds": total_timeout_seconds,
        "idle_timeout_seconds": idle_timeout_seconds,
        "allow_legacy_oauth_bootstrap": True,
    }
    if executor is not None:
        kwargs["executor"] = executor
    return RoutedCodexExecution(**kwargs)
