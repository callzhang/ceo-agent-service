from __future__ import annotations

import json
from hashlib import sha256
import sys
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from app.agent_context import (
    IMAGE_DEPENDENCY_UNAVAILABLE_SUMMARY,
    AgentTaskContext,
)
from app.agent_contracts import (
    AuditAgentResult,
    AuditFeedback,
    ConsumerAgentResult,
    ConsumerOutcome,
)
from app.agent_result import ResultParseError
from app.agent_wire_contracts import (
    AuditAgentWireResult,
    ConsumerAgentWireResult,
    parse_consumer_agent_wire_result,
)
from app.native_cli_metadata import NativeCliMetadataClassifier
from app.audit_rules import render_audit_rules, validate_audit_rules_text
from app.agent_effects import LEASE_SECONDS, McpToolEffectRegistry
from app.agent_turn_runner import AgentTurnProcess, AgentTurnRunResult, ProcessExecutor
from app.codex_history import find_codex_session_path
from app.store import AgentRole, AutoReplyStore, ReplyTask
from app.wechat.codex_safety import ControlledCliConfig, make_consumer_agent_command


SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "consumer_agent_wire.schema.json"
SERVICE_ROOT = Path(__file__).resolve().parent.parent
DYNAMIC_SKILL_MARKER = "[dynamic-skill]"
CONSUMER_DYNAMIC_SKILL_SENTENCE = (
    "Consumer Agent A independently selects and reads every applicable business and "
    "operation Skill with `agent_cli.read_skill` before forming the candidate."
)
AUDIT_DYNAMIC_SKILL_SENTENCE = (
    "Audit Agent B independently determines every business and operation Skill "
    "applicable to the candidate, requires the corresponding verified Consumer A "
    "receipt for each applicable Skill, rereads each exact receipt path with "
    "`agent_cli.read_skill`, verifies its sha256, and returns revision_required if "
    "any applicable receipt is absent, unreadable, changed, or mismatched."
)
CONSUMER_DYNAMIC_SKILL_BODY = (
    f"{DYNAMIC_SKILL_MARKER} {CONSUMER_DYNAMIC_SKILL_SENTENCE}"
)
AUDIT_DYNAMIC_SKILL_BODY = (
    f"{DYNAMIC_SKILL_MARKER} {AUDIT_DYNAMIC_SKILL_SENTENCE}"
)
CORE_DYNAMIC_SKILL_BODY = (
    f"{CONSUMER_DYNAMIC_SKILL_BODY} {AUDIT_DYNAMIC_SKILL_SENTENCE}"
)


def consumer_wire_contract_hash() -> str:
    """Fingerprint the strict wire schema used to decide session compatibility."""
    schema = ConsumerAgentWireResult.model_json_schema()
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()



class ConsumerAgentRunner:
    def __init__(
        self,
        *,
        store: AutoReplyStore,
        workspace: Path,
        codex_bin: str = "codex",
        executor: ProcessExecutor | None = None,
        owner: str | None = None,
        mcp_effect_registry: McpToolEffectRegistry | None = None,
        native_cli_classifier: NativeCliMetadataClassifier | None = None,
        codex_session_exists: Callable[[str], bool] | None = None,
    ) -> None:
        self.store = store
        self.workspace = workspace
        self.codex_bin = codex_bin
        self.executor = executor
        self.owner = owner or f"consumer-agent-{uuid4().hex}"
        self.effects = mcp_effect_registry or McpToolEffectRegistry.default()
        self.native_cli_classifier = native_cli_classifier
        self.codex_session_exists = codex_session_exists or (
            lambda session_id: find_codex_session_path(session_id) is not None
        )

    def run(
        self,
        task: ReplyTask,
        context: AgentTaskContext,
        *,
        proposal_revision: int,
        parent_agent_run_id: int | None,
        feedback: AuditFeedback | None = None,
    ) -> AgentTurnRunResult[ConsumerAgentResult]:
        if context.task_id != task.id:
            raise ValueError("agent context task does not match reply task")
        rendered_rules = render_audit_rules(AgentRole.CONSUMER)
        lock_owner = f"consumer-agent:{task.id}:{task.execution_generation}"
        try:
            with self.store.codex_session_lock(task.conversation_id, lock_owner):
                return self._run_locked(
                    task,
                    context,
                    lock_owner=lock_owner,
                    proposal_revision=proposal_revision,
                    parent_agent_run_id=parent_agent_run_id,
                    rendered_rules=rendered_rules,
                    feedback=feedback,
                )
        except RuntimeError as exc:
            if str(exc).startswith("codex session locked:"):
                raise RuntimeError("codex_session_locked") from exc
            raise

    def _run_locked(
        self,
        task: ReplyTask,
        context: AgentTaskContext,
        *,
        lock_owner: str,
        proposal_revision: int,
        parent_agent_run_id: int | None,
        rendered_rules: str,
        feedback: AuditFeedback | None,
    ) -> AgentTurnRunResult[ConsumerAgentResult]:
        contract_hash = consumer_wire_contract_hash()
        conversation_session_id = (
            self.store.get_codex_session_id(task.conversation_id) or None
        )
        if task.force_new_decision and conversation_session_id:
            # A forced rerun must reassess the task with the current tools and
            # instructions. Resuming the old conversation can replay a failed
            # tool path before the agent sees those changes.
            self.store.clear_codex_session_if_matches(
                task.conversation_id,
                conversation_session_id,
            )
            conversation_session_id = None
        if (
            conversation_session_id
            and self.store.get_codex_session_contract_hash(task.conversation_id)
            != contract_hash
        ):
            # A resumed session retains its old output contract. Start a fresh
            # session when the strict wire schema changes instead of accepting
            # an incompatible result or treating it as a permanent task error.
            self.store.clear_codex_session_if_matches(
                task.conversation_id,
                conversation_session_id,
            )
            conversation_session_id = None
        if (
            conversation_session_id
            and not self.codex_session_exists(conversation_session_id)
        ):
            self.store.clear_codex_session_if_matches(
                task.conversation_id,
                conversation_session_id,
            )
            conversation_session_id = None
        claim = self.store.claim_agent_run(
            task.id,
            task.execution_generation,
            role=AgentRole.CONSUMER,
            proposal_revision=proposal_revision,
            turn_attempt=0,
            parent_agent_run_id=parent_agent_run_id,
            operation_id="",
            owner=self.owner,
            lease_seconds=LEASE_SECONDS,
        )
        if not claim.claimed:
            raise RuntimeError("agent_run_unavailable")
        session_id = (
            claim.run.codex_session_id
            if conversation_session_id is not None
            else None
        ) or conversation_session_id
        persist_conversation_session = not (
            claim.run.codex_session_id
            and conversation_session_id
            and claim.run.codex_session_id != conversation_session_id
        )
        process = AgentTurnProcess[ConsumerAgentResult](
            store=self.store,
            task=task,
            workspace=self.workspace,
            owner=self.owner,
            executor=self.executor,
            codex_bin=self.codex_bin,
            mcp_effect_registry=self.effects,
            native_cli_classifier=self.native_cli_classifier,
        )

        if (image_error := context.image_dependency_error) is not None:
            result = ConsumerAgentResult(
                outcome=ConsumerOutcome.FAILED,
                summary=IMAGE_DEPENDENCY_UNAVAILABLE_SUMMARY,
                proposal=None,
                error=image_error,
            )
            failed = self.store.fail_agent_run(
                claim.run.id,
                result.error.model_dump(mode="json"),
                owner=self.owner,
            )
            return AgentTurnRunResult(
                run_id=failed.id,
                result=result,
                transcript_start_line=failed.transcript_start_line,
                transcript_end_line=failed.transcript_end_line,
            )

        def renew_session_lock() -> None:
            if not self.store.renew_codex_session_lock(
                task.conversation_id,
                lock_owner,
            ):
                raise RuntimeError("codex_session_lock_lost")

        try:
            result = process.execute(
                run=claim.run,
                prompt=(
                    context.render(
                        proposal_revision=proposal_revision,
                        feedback=feedback,
                    )
                ),
                session_id=session_id,
                schema_path=SCHEMA_PATH,
                expected_schema=ConsumerAgentWireResult.model_json_schema(),
                developer_instructions=consumer_developer_instructions(
                    rendered_rules
                ),
                configure_command=lambda command: make_consumer_agent_command(
                    command,
                    controlled_cli=ControlledCliConfig(
                        command=sys.executable,
                        args=("-m", "app.agent_cli"),
                        cwd=str(SERVICE_ROOT),
                    ),
                ),
                parse_result=parse_consumer_agent_wire_result,
                persist_conversation_session=persist_conversation_session,
                on_progress=renew_session_lock,
                image_paths=[Path(path) for path in context.image_paths],
            )
            self.store.set_codex_session_contract_hash(
                task.conversation_id,
                contract_hash,
            )
            if (
                result.result.outcome.value == "failed"
                and result.result.error.retryable
            ):
                persisted = self.store.get_agent_run(claim.run.id)
                if persisted is not None and not persisted.tool_events:
                    failed_session_id = persisted.codex_session_id or session_id
                    if failed_session_id:
                        # A retryable result without any controlled tool event
                        # made no evidence progress. Retry with the current
                        # instructions instead of resuming that dead-end turn.
                        self.store.clear_codex_session_if_matches(
                            task.conversation_id,
                            failed_session_id,
                        )
            return result
        except ResultParseError as exc:
            persisted = self.store.get_agent_run(claim.run.id)
            if (
                str(exc) == "no valid typed result JSON found in Codex JSONL"
                and persisted is not None
                and not persisted.tool_events
            ):
                failed_session_id = session_id or persisted.codex_session_id
                if failed_session_id:
                    self.store.clear_codex_session_if_matches(
                        task.conversation_id,
                        failed_session_id,
                    )
            raise


def consumer_developer_instructions(audit_rules: str) -> str:
    return _developer_instructions(
        audit_rules=audit_rules,
        skill_instruction=CONSUMER_DYNAMIC_SKILL_BODY,
        wire_model=ConsumerAgentWireResult,
        result_model=ConsumerAgentResult,
    )


def audit_developer_instructions(audit_rules: str) -> str:
    return _developer_instructions(
        audit_rules=audit_rules,
        skill_instruction=AUDIT_DYNAMIC_SKILL_BODY,
        wire_model=AuditAgentWireResult,
        result_model=AuditAgentResult,
    )


def _developer_instructions(
    *,
    audit_rules: str,
    skill_instruction: str,
    wire_model: type[ConsumerAgentWireResult] | type[AuditAgentWireResult],
    result_model: type[ConsumerAgentResult] | type[AuditAgentResult],
) -> str:
    validate_audit_rules_text(audit_rules)
    return "\n\n".join(
        (
            f"## Audit Rules\n{audit_rules}",
            f"## Dynamic Skill\n{skill_instruction}",
            f"## Pydantic Wire Contract\n{_schema_json(wire_model)}",
            f"## Pydantic Result Contract\n{_schema_json(result_model)}",
        )
    )


def _schema_json(
    model: type[
        ConsumerAgentWireResult
        | AuditAgentWireResult
        | ConsumerAgentResult
        | AuditAgentResult
    ],
) -> str:
    return json.dumps(
        model.model_json_schema(),
        ensure_ascii=False,
        separators=(",", ":"),
    )
