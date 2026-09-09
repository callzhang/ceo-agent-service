from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Protocol

from app.agent_context import AgentTaskContext
from app.agent_cron.models import ScheduledTaskRun
from app.agent_runtime_contracts import RuntimeKind, RuntimeRoute
from app.managed_skills import ManagedSkillRevision
from app.skill_files import SkillDocument


class ScheduledContextOptions(Protocol):
    def resolve_runtime_route(
        self,
        route_name: str,
        *,
        required_capabilities: frozenset[str] = frozenset(),
    ) -> RuntimeRoute: ...

    def resolve_managed_skill_revision(
        self, *, skill_id: int, revision_id: int, skill_name: str
    ) -> ManagedSkillRevision: ...

    def resolve_operation_skill(self, name: str) -> SkillDocument: ...


def validate_scheduled_execution_availability(
    options: ScheduledContextOptions, built: ScheduledAgentContext
) -> None:
    """Validate saved identities without rebuilding any saved execution content."""
    required = built.context.trigger_raw_payload.get(
        "required_runtime_capabilities", []
    )
    if not isinstance(required, list) or any(
        not isinstance(item, str) or not item for item in required
    ):
        raise ValueError("scheduled execution runtime requirements are invalid")
    current_route = options.resolve_runtime_route(
        built.route.name,
        required_capabilities=frozenset(required),
    )
    if (
        current_route.runtime_kind != built.route.runtime_kind
        or current_route.credential_mode != built.route.credential_mode
    ):
        raise ValueError("scheduled runtime route identity changed")
    if not built.workspace.is_dir():
        raise ValueError("scheduled task working directory is unavailable")
    skills = built.context.trigger_raw_payload.get("skills")
    if not isinstance(skills, list):
        raise ValueError("scheduled execution skill identities are invalid")
    for fact in skills:
        if not isinstance(fact, dict):
            raise ValueError("scheduled execution skill identity is invalid")
        if fact.get("source") == "managed":
            revision = options.resolve_managed_skill_revision(
                skill_id=int(fact["skill_id"]),
                revision_id=int(fact["revision_id"]),
                skill_name=str(fact["name"]),
            )
            if revision.sha256 != fact.get("sha256"):
                raise ValueError("scheduled managed revision identity changed")
        elif fact.get("source") == "operation":
            # Public operation Skill content is immutable in this execution fact;
            # only its installed name remains an execution availability condition.
            options.resolve_operation_skill(str(fact["name"]))
        else:
            raise ValueError("scheduled execution skill source is invalid")


@dataclass(frozen=True)
class ScheduledAgentContext:
    context: AgentTaskContext
    route: RuntimeRoute
    workspace: Path
    reasoning_effort: str
    skill_protocol: str

    def to_execution_json(self) -> str:
        payload = {
            "schema": "scheduled_agent_execution.v1",
            "context": {
                "conversation_id": self.context.conversation_id,
                "conversation_title": self.context.conversation_title,
                "single_chat": self.context.single_chat,
                "trigger_message_id": self.context.trigger_message_id,
                "trigger_sender": self.context.trigger_sender,
                "trigger_text": self.context.trigger_text,
                "trigger_create_time": self.context.trigger_create_time,
                "trigger_raw_payload": self.context.trigger_raw_payload,
            },
            "route": self.route.model_dump(mode="json"),
            "workspace": str(self.workspace),
            "reasoning_effort": self.reasoning_effort,
            "skill_protocol": self.skill_protocol,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_execution_json(
        cls, value: str, *, reply_task_id: int
    ) -> ScheduledAgentContext:
        try:
            payload = json.loads(value)
            if payload.get("schema") != "scheduled_agent_execution.v1":
                raise ValueError("scheduled execution context schema is invalid")
            saved = payload["context"]
            context = AgentTaskContext(
                task_id=reply_task_id,
                channel="scheduled",
                conversation_id=saved["conversation_id"],
                conversation_title=saved["conversation_title"],
                single_chat=bool(saved["single_chat"]),
                trigger_message_id=saved["trigger_message_id"],
                trigger_sender=saved["trigger_sender"],
                trigger_text=saved["trigger_text"],
                trigger_create_time=saved["trigger_create_time"],
                messages=(), materials=(), prior_receipts=(),
                trigger_raw_payload=saved["trigger_raw_payload"],
            )
            return cls(
                context=context,
                route=RuntimeRoute.model_validate(payload["route"]),
                workspace=Path(payload["workspace"]),
                reasoning_effort=payload["reasoning_effort"],
                skill_protocol=payload["skill_protocol"],
            )
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("scheduled execution context is invalid") from exc


class ScheduledAgentContextBuilder:
    """Materialize only the immutable run snapshot's structured references."""

    def __init__(self, options: ScheduledContextOptions) -> None:
        self._options = options

    def build(
        self, run: ScheduledTaskRun, *, reply_task_id: int
    ) -> ScheduledAgentContext:
        snapshot = run.snapshot
        route = self._options.resolve_runtime_route(
            snapshot.runtime_id,
            required_capabilities=frozenset(
                snapshot.required_runtime_capabilities
            ),
        )
        runtime_options = snapshot.runtime_options
        model = runtime_options.get("model")
        if model is not None:
            if not isinstance(model, str) or not model.strip():
                raise ValueError("scheduled task runtime model must be nonempty")
            route = route.model_copy(update={"model": model.strip()})
        effort = runtime_options.get(
            "reasoning_effort", runtime_options.get("thinking", "")
        )
        if not isinstance(effort, str):
            raise ValueError("scheduled task reasoning effort must be text")
        if effort.strip() and route.runtime_kind is not RuntimeKind.CODEX_CLI:
            raise ValueError(
                f"scheduled task runtime {route.runtime_kind.value} "
                "does not support reasoning effort"
            )
        unknown = set(runtime_options) - {"model", "reasoning_effort", "thinking"}
        if unknown:
            raise ValueError(
                "scheduled task runtime options are unsupported: "
                + ", ".join(sorted(unknown))
            )
        workspace = Path(snapshot.working_directory).expanduser().resolve()
        if not workspace.is_dir():
            raise ValueError("scheduled task working directory is unavailable")

        protocols: list[str] = []
        skill_facts: list[dict[str, object]] = []
        for ref in snapshot.skill_refs:
            if ref.skill_source == "managed":
                assert ref.managed_skill_id is not None
                assert ref.managed_revision_id is not None
                revision = self._options.resolve_managed_skill_revision(
                    skill_id=ref.managed_skill_id,
                    revision_id=ref.managed_revision_id,
                    skill_name=ref.skill_name,
                )
                protocols.append(
                    f"## Managed Skill: {ref.skill_name}\n"
                    f"revision_id: {revision.id}\nsha256: {revision.sha256}\n\n"
                    f"{revision.content}"
                )
                skill_facts.append(
                    {
                        "source": "managed",
                        "name": ref.skill_name,
                        "skill_id": revision.skill_id,
                        "revision_id": revision.id,
                        "sha256": revision.sha256,
                    }
                )
            else:
                document = self._options.resolve_operation_skill(ref.skill_name)
                source = str(document.path.resolve())
                protocols.append(
                    f"## Operation Skill: {ref.skill_name}\n"
                    f"source: {source}\nsha256: {document.sha256}\n\n"
                    f"{document.content}"
                )
                skill_facts.append(
                    {
                        "source": "operation",
                        "name": ref.skill_name,
                        "path": source,
                        "sha256": document.sha256,
                    }
                )

        context = AgentTaskContext(
            task_id=reply_task_id,
            channel="scheduled",
            conversation_id=f"scheduled-task-run:{run.id}",
            conversation_title=snapshot.name,
            single_chat=False,
            trigger_message_id=run.event_id,
            trigger_sender="Agent Cron",
            trigger_text=snapshot.prompt,
            trigger_create_time=run.scheduled_for.isoformat(),
            messages=(),
            materials=(),
            prior_receipts=(),
            trigger_raw_payload={
                "schema": "scheduled_agent_trigger.v1",
                "scheduled_task_id": snapshot.task_id,
                "scheduled_task_version": snapshot.task_version,
                "scheduled_task_run_id": run.id,
                "trigger_kind": run.trigger_kind,
                "scheduled_for": run.scheduled_for.isoformat(),
                "required_runtime_capabilities": list(
                    snapshot.required_runtime_capabilities
                ),
                "skills": skill_facts,
            },
        )
        return ScheduledAgentContext(
            context=context,
            route=route,
            workspace=workspace,
            reasoning_effort=effort.strip(),
            skill_protocol="\n\n".join(protocols),
        )
