from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib

from app.agent_runtime_config import AgentRuntimeConfig, load_runtime_config
from app.agent_runtime_contracts import (
    CredentialMode,
    RuntimeCapabilitySnapshot,
    RuntimeKind,
    RuntimeRoute,
)
from app.agent_runtime_router import AgentRuntimeRouter
from app.managed_skills import ManagedSkillRevision, RuntimeSkillBinding
from app.skill_files import SkillDocument, SkillFileError, SkillFileService
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
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._runtime_config = load_runtime_config(environment)
        self._runtime_snapshots = runtime_snapshots
        self._operation_skill_files = operation_skill_files
        self._now = now or (lambda: datetime.now(UTC))

    @property
    def runtime_config(self) -> AgentRuntimeConfig:
        return self._runtime_config

    def list_runtime_options(self) -> tuple[RuntimeOption, ...]:
        return tuple(
            self._runtime_option(route) for route in self._runtime_config.routes
        )

    def resolve_runtime_route(self, route_name: str) -> RuntimeRoute:
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
        option = self._runtime_option(route)
        if not option.available:
            raise ScheduledTaskOptionUnavailableError(
                f"runtime route {route_name}: {option.unavailable_reason}"
            )
        return route

    def list_managed_skill_options(self) -> tuple[ManagedSkillOption, ...]:
        active = self._store.get_active_runtime_skill_config()
        bindings = (
            {
                binding.skill_id: binding
                for binding in self._store.list_runtime_skill_bindings(active.id)
            }
            if active is not None
            else {}
        )
        return tuple(
            ManagedSkillOption(
                skill_id=skill.id,
                name=skill.name,
                display_name=skill.display_name,
                revisions=tuple(
                    self._managed_revision_option(revision, bindings.get(skill.id))
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
        active = self._store.get_active_runtime_skill_config()
        binding = (
            next(
                (
                    item
                    for item in self._store.list_runtime_skill_bindings(active.id)
                    if item.skill_id == skill_id
                ),
                None,
            )
            if active is not None
            else None
        )
        option = self._managed_revision_option(revision, binding)
        if not option.available:
            raise ScheduledTaskOptionUnavailableError(
                f"managed revision {revision_id}: {option.unavailable_reason}"
            )
        return revision

    def list_operation_skill_options(self) -> tuple[OperationSkillOption, ...]:
        options: list[OperationSkillOption] = []
        for project_skill in self._operation_skill_files.list_skills():
            try:
                document = self._operation_skill_files.get_operation_skill(
                    project_skill.name
                )
            except SkillFileError:
                try:
                    digest = hashlib.sha256(project_skill.path.read_bytes()).hexdigest()
                except OSError:
                    digest = ""
                options.append(
                    OperationSkillOption(
                        name=project_skill.name,
                        source=str(project_skill.path),
                        content_summary="",
                        sha256=digest,
                        available=False,
                        unavailable_reason="operation_skill_invalid",
                    )
                )
            else:
                options.append(self._operation_skill_option(document))
        return tuple(options)

    def resolve_operation_skill(self, name: str) -> SkillDocument:
        try:
            return self._operation_skill_files.get_operation_skill(name)
        except SkillFileError as exc:
            raise ScheduledTaskOptionUnavailableError(
                f"operation Skill {name}: operation_skill_unavailable"
            ) from exc

    def _runtime_option(self, route: RuntimeRoute) -> RuntimeOption:
        decision = AgentRuntimeRouter(
            routes=(route,),
            store=self._store,
            snapshots=self._runtime_snapshots,
            now=self._now,
        ).first_route_decision(required_capabilities=frozenset())
        available = decision.route is not None
        reason = (
            None if available else _single_route_reason(decision.reason, route.name)
        )
        return RuntimeOption(
            route_name=route.name,
            runtime_kind=route.runtime_kind,
            credential_mode=route.credential_mode,
            model=route.model,
            available=available,
            unavailable_reason=reason,
        )

    @staticmethod
    def _managed_revision_option(
        revision: ManagedSkillRevision, binding: RuntimeSkillBinding | None
    ) -> ManagedSkillRevisionOption:
        if binding is None or binding.revision_id != revision.id:
            reason = "managed_revision_not_loaded"
        elif not binding.enabled:
            reason = "managed_skill_disabled"
        else:
            reason = None
        return ManagedSkillRevisionOption(
            revision_id=revision.id,
            revision_number=revision.revision_number,
            sha256=revision.sha256,
            source=revision.source,
            available=reason is None,
            unavailable_reason=reason,
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
