from __future__ import annotations

import os
import sys
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from app.agent_effects import McpToolEffectRegistry
from app.agent_runtime_config import AgentRuntimeConfig, load_runtime_config
from app.agent_runtime_contracts import (
    RuntimeCapabilitySnapshot,
    RuntimeKind,
    RuntimeRouteSurfaceManifest,
)
from app.agent_runtime_router import (
    AgentRuntimeRouter,
    ProcessExecutor,
    RoutedCodexExecution,
    _configured_mcp_server_transport_names,
    local_codex_session_effect_probe,
)
from app.claude_runtime_adapter import ClaudeRuntimeAdapter
from app.codex_runtime_adapter import CodexRuntimeAdapter
from app.native_cli_metadata import NativeCliMetadataClassifier
from app.service_codex_config import (
    ServiceMcpConfigError,
    ServiceMcpServer,
    load_service_mcp_servers,
)
from app.store import AutoReplyStore


class RuntimeCapabilityRegistry(Mapping[str, RuntimeCapabilitySnapshot]):
    """A live, atomically refreshable capability view shared by routers."""

    def __init__(
        self, snapshots: Mapping[str, RuntimeCapabilitySnapshot] | None = None
    ) -> None:
        self._lock = RLock()
        self._snapshots: dict[str, RuntimeCapabilitySnapshot] = {}
        self._surface_manifests: dict[str, RuntimeRouteSurfaceManifest] = {}
        self._surface_view = _RuntimeSurfaceManifestView(self)
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

    def refresh_surface_manifests(
        self, manifests: Mapping[str, RuntimeRouteSurfaceManifest]
    ) -> None:
        replacement = dict(manifests)
        for route_name, manifest in replacement.items():
            if route_name != manifest.route_name:
                raise ValueError("runtime surface manifest key mismatch")
        with self._lock:
            self._surface_manifests = replacement

    def surface_manifest(self, route_name: str) -> RuntimeRouteSurfaceManifest | None:
        with self._lock:
            return self._surface_manifests.get(route_name)

    @property
    def surface_manifests(self) -> Mapping[str, RuntimeRouteSurfaceManifest]:
        return self._surface_view


class _RuntimeSurfaceManifestView(Mapping[str, RuntimeRouteSurfaceManifest]):
    """Live read-only view so existing routers observe reviewed config refreshes."""

    def __init__(self, registry: RuntimeCapabilityRegistry) -> None:
        self._registry = registry

    def __getitem__(self, key: str) -> RuntimeRouteSurfaceManifest:
        with self._registry._lock:
            return self._registry._surface_manifests[key]

    def __iter__(self) -> Iterator[str]:
        with self._registry._lock:
            return iter(tuple(self._registry._surface_manifests))

    def __len__(self) -> int:
        with self._registry._lock:
            return len(self._registry._surface_manifests)


PRODUCTION_RUNTIME_CAPABILITIES = RuntimeCapabilityRegistry()


@dataclass(frozen=True)
class ProductionAgentRuntime:
    config: AgentRuntimeConfig
    router: AgentRuntimeRouter
    codex_adapter: CodexRuntimeAdapter
    claude_adapter: ClaudeRuntimeAdapter | None


def build_production_agent_runtime(
    *,
    store: AutoReplyStore,
    workspace: Path,
    codex_bin: str = "codex",
    claude_bin: str = "claude",
    capability_registry: RuntimeCapabilityRegistry = PRODUCTION_RUNTIME_CAPABILITIES,
) -> ProductionAgentRuntime:
    """Build the pure, pre-probed runtime dependencies for Agent workloads."""

    runtime_config = load_runtime_config(os.environ)
    effects = McpToolEffectRegistry.default()
    native_cli = NativeCliMetadataClassifier()
    has_claude_route = any(
        route.runtime_kind is RuntimeKind.CLAUDE_CLI
        for route in runtime_config.routes
    )
    service_servers = (
        _production_claude_service_mcp_servers() if has_claude_route else ()
    )
    return ProductionAgentRuntime(
        config=runtime_config,
        router=AgentRuntimeRouter(
            routes=runtime_config.routes,
            store=store,
            snapshots=capability_registry,
            surface_manifests=capability_registry.surface_manifests,
        ),
        codex_adapter=CodexRuntimeAdapter(
            workspace, runtime_config, codex_bin=codex_bin
        ),
        claude_adapter=(
            ClaudeRuntimeAdapter(
                workspace=workspace,
                config=runtime_config,
                claude_bin=claude_bin,
                effect_registry=effects,
                native_cli_classifier=native_cli,
                service_mcp_servers=service_servers,
            )
            if has_claude_route
            else None
        ),
    )


def _production_claude_service_mcp_servers() -> tuple[ServiceMcpServer, ...]:
    try:
        configured = list(load_service_mcp_servers(env=os.environ))
    except ServiceMcpConfigError as exc:
        configured = list(exc.valid_servers)
    if not any(server.name == "agent_cli" for server in configured):
        configured.append(
            ServiceMcpServer(
                name="agent_cli",
                command=sys.executable,
                args=("-m", "app.agent_cli"),
            )
        )
    return tuple(configured)


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
            surface_manifests=capability_registry.surface_manifests,
        ),
        "adapter": CodexRuntimeAdapter(workspace, runtime_config, codex_bin=codex_bin),
        "session_effect_probe": local_codex_session_effect_probe(),
        "total_timeout_seconds": total_timeout_seconds,
        "idle_timeout_seconds": idle_timeout_seconds,
        "allow_legacy_oauth_bootstrap": False,
    }
    if executor is not None:
        kwargs["executor"] = executor
    return RoutedCodexExecution(**kwargs)


def build_production_runtime_refresher(
    *,
    store: AutoReplyStore,
    codex_bin: str = "codex",
    claude_bin: str = "claude",
    executor: ProcessExecutor | None = None,
    capability_registry: RuntimeCapabilityRegistry = PRODUCTION_RUNTIME_CAPABILITIES,
    temporary_root: Path | None = None,
):
    """Build the route probe/refresher that owns the shared production view."""

    from app.agent_runtime_probe import AgentRuntimeProbe, RuntimeCapabilityRefresher

    runtime_config = load_runtime_config(os.environ)
    capability_registry.refresh_surface_manifests(
        _reviewed_surface_manifests(
            runtime_config,
            codex_bin=codex_bin,
        )
    )
    probe_kwargs = {
        "config": runtime_config,
        "codex_bin": codex_bin,
        "claude_bin": claude_bin,
        "temporary_root": temporary_root,
    }
    if executor is not None:
        probe_kwargs["executor"] = executor
    return RuntimeCapabilityRefresher(
        config=runtime_config,
        store=store,
        registry=capability_registry,
        probe=AgentRuntimeProbe(**probe_kwargs),
    )


def _reviewed_surface_manifests(
    runtime_config,
    *,
    codex_bin: str,
):
    adapter = CodexRuntimeAdapter(Path.cwd(), runtime_config, codex_bin=codex_bin)
    effects = McpToolEffectRegistry.default()
    claude_servers = {
        server.name for server in _production_claude_service_mcp_servers()
    }
    claude_read_tools = effects.reviewed_read_tools()
    manifests = {}
    for route in runtime_config.routes:
        if route.runtime_kind is RuntimeKind.CLAUDE_CLI:
            capabilities = set()
            if "agent_cli" in claude_servers and claude_read_tools.get("agent_cli"):
                capabilities.update(
                    {
                        "task_context",
                        "channel:dingtalk",
                        "channel:wechat",
                        "channel:lark",
                        "channel:feishu",
                        # Reconciliation is run through the same controlled
                        # read-only command policy as Consumer turns.
                        "consumer_read_only_enforcement",
                        "reviewed_read_tools",
                        "reconciliation_read_only",
                        "mcp:agent_cli:reviewed_read",
                        "native_cli:reviewed",
                        "native_cli:dws",
                        "native_cli:lark",
                        "reviewed_dws_read_instructions",
                        "dws_read",
                        "agent_cli.dws",
                        "agent_cli.lark-cli",
                    }
                )
            if (
                "memory_connector" in claude_servers
                and claude_read_tools.get("memory_connector")
            ):
                capabilities.update(
                    {"memory_connector_read", "mcp:memory_connector:read"}
                )
            manifests[route.name] = RuntimeRouteSurfaceManifest(
                route_name=route.name,
                capabilities=frozenset(capabilities),
            )
            continue
        # Consumer and Audit turns always install this local, service-owned
        # transport with their command policy.  It must therefore be reflected
        # in the surface manifest instead of depending on a user's global
        # Codex configuration.  The command policy still controls its tools.
        transports = frozenset(
            {
                "agent_cli",
                *_configured_mcp_server_transport_names(
                    (), env=adapter.build_env(route)
                ),
            }
        )
        capabilities = {
            "audit_effect_visibility",
            "reconciliation_read_only",
            "image_input",
            "task_context",
            "channel:dingtalk",
            "channel:wechat",
            "channel:lark",
            "channel:feishu",
            # The native controlled CLI rejects writes when Audit is in the
            # reconciliation phase, which satisfies the runner's read-only
            # execution requirement.
            "consumer_read_only_enforcement",
            "native_cli:reviewed",
            "native_cli:dws",
            "native_cli:lark",
            "reviewed_dws_read_instructions",
        }
        if "agent_cli" in transports:
            capabilities.update(
                {
                    "reviewed_read_tools",
                    "reviewed_write_tools",
                    "mcp:agent_cli:reviewed_read",
                    "mcp:agent_cli:reviewed_write",
                    "dws_read",
                    # The service owns this transport.  Its individual
                    # commands remain validated against their own reviewed
                    # metadata at execution time; route selection must not
                    # scan the complete DWS schema to establish that fact.
                    "agent_cli.dws",
                    "agent_cli.lark-cli",
                }
            )
        if "memory_connector" in transports:
            capabilities.update(
                {
                    "memory_connector_read",
                    "mcp:memory_connector:read",
                    "mcp:memory_connector:memory_write",
                }
            )
        manifests[route.name] = RuntimeRouteSurfaceManifest(
            route_name=route.name,
            capabilities=frozenset(capabilities),
        )
    return manifests
