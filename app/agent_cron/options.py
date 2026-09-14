from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib

from app.agent_cron.commands import (
    SERVICE_COMMAND_OPTIONS,
    ServiceCommandOption,
    service_command_option,
)
from app.agent_runtime_config import AgentRuntimeConfig, load_runtime_config
from app.agent_runtime_contracts import (
    CredentialMode,
    PROBE_VERIFIED_RUNTIME_CAPABILITIES,
    RuntimeCapabilitySnapshot,
    RuntimeKind,
    RuntimeRoute,
    runtime_route_surface_capabilities,
)
from app.agent_runtime_router import AgentRuntimeRouter
from app.managed_skills import ManagedSkillRevision, RuntimeSkillSnapshot
from app.skill_files import (
    SkillDocument,
    SkillFileError,
    SkillFileOwnershipError,
    SkillFileService,
)
from app.store import AutoReplyStore


class ScheduledTaskOptionUnavailableError(ValueError):
    """The exact saved Runtime or Skill choice cannot currently execute."""


@dataclass(frozen=True)
class RuntimeOption:
    route_name: str
    runtime_kind: RuntimeKind
    credential_mode: CredentialMode
    model: str
    available: bool
    unavailable_reason: str | None
    supported_thinking: tuple[str, ...]
    capabilities: tuple[str, ...]


@dataclass(frozen=True)
class ManagedSkillRevisionOption:
    revision_id: int
    revision_number: int
    sha256: str
    source: str
    available: bool
    unavailable_reason: str | None


@dataclass(frozen=True)
class ManagedSkillOption:
    skill_id: int
    name: str
    display_name: str
    revisions: tuple[ManagedSkillRevisionOption, ...]


@dataclass(frozen=True)
class OperationSkillOption:
    name: str
    source: str
    content_summary: str
    sha256: str
    available: bool
    unavailable_reason: str | None


class ScheduledTaskOptionService:
    """Project current Runtime and Skill facts into Cron editor choices.

    The service deliberately evaluates saved route names and immutable managed
    revision ids exactly.  It never asks the general router to choose a
    replacement route or a newer Skill revision.
    """

    def __init__(
        self,
        *,
        store: AutoReplyStore,
        environment: Mapping[str, str],
        runtime_snapshots: Mapping[str, RuntimeCapabilitySnapshot],
        operation_skill_files: SkillFileService,
        runtime_skill_snapshot: RuntimeSkillSnapshot | None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._runtime_config = load_runtime_config(environment)
        self._runtime_snapshots = runtime_snapshots
        self._operation_skill_files = operation_skill_files
        self._runtime_skill_snapshot = runtime_skill_snapshot
        self._now = now or (lambda: datetime.now(UTC))

    @property
    def runtime_config(self) -> AgentRuntimeConfig:
        return self._runtime_config

    def list_runtime_options(
        self,
        *,
        required_capabilities: frozenset[str] = frozenset(),
    ) -> tuple[RuntimeOption, ...]:
        return tuple(
            self._runtime_option(route, required_capabilities=required_capabilities)
            for route in self._runtime_config.routes
        )

    def resolve_runtime_route(
        self,
        route_name: str,
        *,
        required_capabilities: frozenset[str] = frozenset(),
    ) -> RuntimeRoute:
        route = next(
            (
                configured
                for configured in self._runtime_config.routes
                if configured.name == route_name
            ),
            None,
        )
        if route is None:
            raise ScheduledTaskOptionUnavailableError(
                f"runtime route {route_name}: runtime_not_configured"
            )
        option = self._runtime_option(
            route,
            required_capabilities=required_capabilities,
        )
        if not option.available:
            raise ScheduledTaskOptionUnavailableError(
                f"runtime route {route_name}: {option.unavailable_reason}"
            )
        return route

    def validate_runtime_capabilities(
        self,
        route_name: str,
        *,
        required_capabilities: frozenset[str],
    ) -> RuntimeRoute:
        route = next(
            (
                configured
                for configured in self._runtime_config.routes
                if configured.name == route_name
            ),
            None,
        )
        if route is None:
            raise ScheduledTaskOptionUnavailableError(
                f"runtime route {route_name}: runtime_not_configured"
            )
        if not required_capabilities:
            return route
        capabilities = self._reported_runtime_capabilities(route)
        missing = sorted(required_capabilities - capabilities)
        if missing:
            raise ScheduledTaskOptionUnavailableError(
                f"runtime route {route_name}: missing_capabilities:"
                + ",".join(missing)
            )
        return route

    def runtime_ids_supporting_capabilities(
        self,
        required_capabilities: frozenset[str],
    ) -> frozenset[str]:
        return frozenset(
            route.name
            for route in self._runtime_config.routes
            if not (
                required_capabilities - self._reported_runtime_capabilities(route)
            )
        )

    def list_managed_skill_options(self) -> tuple[ManagedSkillOption, ...]:
        return tuple(
            ManagedSkillOption(
                skill_id=skill.id,
                name=skill.name,
                display_name=skill.display_name,
                revisions=tuple(
                    self._managed_revision_option(revision)
                    for revision in self._store.list_managed_skill_revisions(skill.id)
                ),
            )
            for skill in self._store.list_managed_skills()
        )

    def resolve_managed_skill_revision(
        self, *, skill_id: int, revision_id: int, skill_name: str
    ) -> ManagedSkillRevision:
        skill = self._store.get_managed_skill(skill_id)
        revision = self._store.get_managed_skill_revision(revision_id)
        if (
            skill is None
            or revision is None
            or revision.skill_id != skill_id
            or skill.name != skill_name
        ):
            raise ScheduledTaskOptionUnavailableError(
                f"managed revision {revision_id}: managed_revision_missing"
            )
        option = self._managed_revision_option(revision)
        if not option.available:
            raise ScheduledTaskOptionUnavailableError(
                f"managed revision {revision_id}: {option.unavailable_reason}"
            )
        return revision

    def list_service_command_options(self) -> tuple[ServiceCommandOption, ...]:
        return SERVICE_COMMAND_OPTIONS

    def resolve_service_command(self, name: str) -> ServiceCommandOption:
        try:
            return service_command_option(name)
        except ValueError as exc:
            raise ScheduledTaskOptionUnavailableError(str(exc)) from exc

    def list_operation_skill_options(self) -> tuple[OperationSkillOption, ...]:
        documents, invalid_options = self._read_operation_skill_catalog()
        options = [*invalid_options]
        for name, matches in documents.items():
            if len(matches) == 1:
                options.append(self._operation_skill_option(matches[0]))
            else:
                options.append(
                    OperationSkillOption(
                        name=name,
                        source=";".join(sorted(str(match.path) for match in matches)),
                        content_summary="",
                        sha256="",
                        available=False,
                        unavailable_reason="operation_skill_name_conflict",
                    )
                )
        return tuple(sorted(options, key=lambda option: option.name))

    def resolve_operation_skill(self, name: str) -> SkillDocument:
        if not isinstance(name, str) or not name:
            raise ScheduledTaskOptionUnavailableError(
                f"operation Skill {name}: operation_skill_unavailable"
            )
        documents, _invalid_options = self._read_operation_skill_catalog()
        matches = documents.get(name, ())
        if len(matches) > 1:
            raise ScheduledTaskOptionUnavailableError(
                f"operation Skill {name}: operation_skill_name_conflict"
            )
        if not matches:
            raise ScheduledTaskOptionUnavailableError(
                f"operation Skill {name}: operation_skill_unavailable"
            )
        return matches[0]

    def _read_operation_skill_catalog(
        self,
    ) -> tuple[dict[str, tuple[SkillDocument, ...]], tuple[OperationSkillOption, ...]]:
        documents: dict[str, list[SkillDocument]] = {}
        invalid_options: list[OperationSkillOption] = []
        for project_skill in self._operation_skill_files.list_skills():
            try:
                document = self._operation_skill_files.read_operation_skill(project_skill)
            except SkillFileOwnershipError:
                continue
            except SkillFileError:
                try:
                    digest = hashlib.sha256(project_skill.path.read_bytes()).hexdigest()
                except OSError:
                    digest = ""
                invalid_options.append(
                    OperationSkillOption(
                        name=project_skill.name,
                        source=str(project_skill.path),
                        content_summary="",
                        sha256=digest,
                        available=False,
                        unavailable_reason="operation_skill_invalid",
                    )
                )
                continue
            documents.setdefault(document.name, []).append(document)
        return (
            {name: tuple(matches) for name, matches in documents.items()},
            tuple(invalid_options),
        )

    def _runtime_option(
        self,
        route: RuntimeRoute,
        *,
        required_capabilities: frozenset[str] = frozenset(),
    ) -> RuntimeOption:
        all_required = PROBE_VERIFIED_RUNTIME_CAPABILITIES | required_capabilities
        decision = AgentRuntimeRouter(
            routes=(route,),
            store=self._store,
            snapshots=self._runtime_snapshots,
            now=self._now,
        ).first_route_decision(
            required_capabilities=all_required
        )
        available = decision.route is not None
        reason = (
            None if available else _single_route_reason(decision.reason, route.name)
        )
        capabilities = tuple(
            sorted(self._reported_runtime_capabilities(route))
        )
        return RuntimeOption(
            route_name=route.name,
            runtime_kind=route.runtime_kind,
            credential_mode=route.credential_mode,
            model=route.model,
            available=available,
            unavailable_reason=reason,
            supported_thinking=(
                ("low", "medium", "high", "xhigh")
                if route.runtime_kind is RuntimeKind.CODEX_CLI
                else ()
            ),
            capabilities=capabilities,
        )

    def _reported_runtime_capabilities(
        self,
        route: RuntimeRoute,
    ) -> frozenset[str]:
        snapshot = self._runtime_snapshots.get(route.name)
        capabilities = runtime_route_surface_capabilities(route)
        if snapshot is not None and snapshot.route_name == route.name:
            capabilities |= snapshot.capabilities
        return capabilities

    def _managed_revision_option(
        self, revision: ManagedSkillRevision
    ) -> ManagedSkillRevisionOption:
        snapshot = self._runtime_skill_snapshot
        if snapshot is None:
            reason = "runtime_skill_snapshot_missing"
        elif any(
            loaded.id == revision.id
            and loaded.skill_id == revision.skill_id
            and loaded.sha256 == revision.sha256
            for loaded in snapshot.revisions
        ):
            reason = None
        elif self._snapshot_binding_is_disabled(revision):
            reason = "managed_skill_disabled"
        else:
            reason = "managed_revision_not_loaded"
        return ManagedSkillRevisionOption(
            revision_id=revision.id,
            revision_number=revision.revision_number,
            sha256=revision.sha256,
            source=revision.source,
            available=reason is None,
            unavailable_reason=reason,
        )

    def _snapshot_binding_is_disabled(self, revision: ManagedSkillRevision) -> bool:
        snapshot = self._runtime_skill_snapshot
        if snapshot is None or snapshot.config_id <= 0:
            return False
        return any(
            binding.skill_id == revision.skill_id
            and binding.revision_id == revision.id
            and not binding.enabled
            for binding in self._store.list_runtime_skill_bindings(snapshot.config_id)
        )

    @staticmethod
    def _operation_skill_option(document: SkillDocument) -> OperationSkillOption:
        return OperationSkillOption(
            name=document.name,
            source=str(document.path),
            content_summary=document.description,
            sha256=document.sha256,
            available=True,
            unavailable_reason=None,
        )


def _single_route_reason(reason: str, route_name: str) -> str:
    prefix = f"no_eligible_route:{route_name}="
    return reason.removeprefix(prefix)
