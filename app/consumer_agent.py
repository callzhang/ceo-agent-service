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
    _CONSUMER_AGENT_RULES,
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
from app.business_skills import (
    installed_business_skill_catalog,
    render_business_skill_protocol,
)
from app.native_cli_metadata import (
    NativeCliMetadataClassifier,
    service_read_command_contract,
)
from app.audit_rules import render_audit_rules, validate_audit_rules_text
from app.agent_effects import LEASE_SECONDS, McpToolEffectRegistry
from app.agent_turn_runner import AgentTurnProcess, AgentTurnRunResult, ProcessExecutor
from app.codex_history import find_codex_session_path
from app.store import AgentRole, AutoReplyStore, ReplyTask
from app.wechat.codex_safety import ControlledCliConfig, make_consumer_agent_command


SERVICE_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = SERVICE_ROOT / "app" / "schemas" / "consumer_agent_result.schema.json"
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
    "any applicable receipt is absent, unreadable, changed, or mismatched. For an "
    "already-unknown effect only, B may perform strictly read-only evidence "
    "reconciliation without a receipt when no business Skill is needed to decide "
    "whether the effect happened; B must not execute or retry the candidate."
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
SHARED_RULES_PATH = Path.home() / ".agents" / "AGENT.md"
REVIEWED_DWS_READ_INSTRUCTIONS = """
Before making a domain judgment, inspect the installed Skill catalog or native
Skills list and call `agent_cli.read_skill` for the most specific applicable business Skill.
Then load the operation Skill named by that business Skill before proposing a
concrete CLI or MCP action. Do not ask the service to classify the domain for you.

For live DingTalk, Lark, or local file evidence, call `agent_cli.execute_reviewed_read`
with the exact reviewed read command. This
lets the Agent use the principal's local CLI credential store and makes a
reviewed local read command independently repeatable by Audit B. Unknown shell
commands and every write command remain forbidden for Consumer Agent A.
For a DWS or Lark download, set its destination to a fresh filename directly
under `/tmp/ceo-agent-service-materials`; downloads without that exact bounded
destination or attempts to overwrite an existing file are rejected.

Before composing a DWS command that was not supplied as an exact read command,
query its local runtime contract with `dws schema --cli-path "<product> <command>"
--compact --format json` through `agent_cli.execute_reviewed_read`. Copy the
returned command path and flags exactly; do not invent shortcut names or flag
aliases. A rejected read has no external effect: inspect its error, then use the
runtime contract or the installed skill to correct it in the same turn.

For a downloaded local material file, call `agent_cli.read_text_file` first. It
automatically returns a bounded UTF-8 text file or an OOXML workbook preview,
including when the download filename has no extension. Use
`agent_cli.read_spreadsheet` only when the material is already known to be an
xlsx workbook. Arbitrary local shell and Python execution remain forbidden by
the service read boundary. Do not return `needs_human` merely because a
supported material requires one of these reads.

Before proposing a DingTalk message send, read
`/Users/derek/.agents/skills/dws/multi/dingtalk-chat/SKILL.md` with
`agent_cli.read_skill` and use its documented command shape. Unknown send
syntax is an evidence-reading task, not a reason to return `needs_human`.

Requests to inspect, evaluate, or improve a referenced skill, document,
configuration, or other readable material are normal Agent work. Read the
material, complete the requested analysis, and propose the resulting reply or
safe follow-up yourself. Do not return `needs_human` merely because the work
requires tool use, research, or technical judgment. Reserve `needs_human` for
an actual unresolved management choice or an ambiguous irreversible target.

Before returning `needs_human`, classify the proposed effect from first
principles. A low-consequence operating choice is limited to the principal's
own availability, preparation, acknowledgement, or follow-up, or to a bounded
internal participant action that implements an already-confirmed event or
tracked commitment without changing its scope, timing, owner, or business
meaning. The current evidence must show no conflict, sensitive target, budget,
approval, new external commitment, or irreversible outcome. For that class of choice, call
`memory_recall` with a focused query when the memory tool is available, read
the applicable business and operation Skills, and inspect live evidence.
Memory is context, not proof of the current external state. When those sources
support one ordinary, reversible action, return its proposal for Audit B to
execute and verify. Do not escalate merely because another reasonable default
exists. Reserve `needs_human` for conflicting durable evidence, material
external impact, an irreversible result, an unresolved conflict, or a target
that cannot be reliably identified.

When returning needs_human, first finish every available read and safe
follow-up. Then offer two to four materially different, actionable choices.
Do not offer "investigate", "ask me", or an option that merely repeats the
ambiguity.
""".strip()


def consumer_wire_contract_hash() -> str:
    """Fingerprint stable Consumer output, instructions, and read-tool policy."""
    contract = {
        "consumer_rules": _CONSUMER_AGENT_RULES,
        "role_boundary": CONSUMER_ROLE_BOUNDARY,
        "reviewed_dws_read_instructions": REVIEWED_DWS_READ_INSTRUCTIONS,
        "business_skill_protocol": render_business_skill_protocol(
            installed_business_skill_catalog()
        ),
        "service_read_commands": service_read_command_contract(),
        "wire_schema": ConsumerAgentWireResult.model_json_schema(),
    }
    encoded = json.dumps(contract, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()

CONSUMER_ROLE_BOUNDARY = """
Authoritative Consumer role boundary: configurable Audit Rules are review
criteria, not instructions for you to execute, approve, publish, or return a
candidate to another Agent. You are Consumer Agent A and must finish with one
valid Consumer Agent wire JSON object matching the supplied schema. The service
converts it into a valid ConsumerAgentResult JSON object after strict validation.
Nested proposal data is supplied directly in the proposal field and is strictly
validated before it can affect execution.

Wire field encoding: proposal is an object only when outcome is proposal;
otherwise it must be null. decision_options is an array containing two to four
mutually exclusive options only when outcome is needs_human, and [] for every
other outcome. For needs_human,
each array item must contain exactly these non-empty string fields: `key`,
`label`, `instruction`, and `consequence`. `key` must be unique within the
array and stable enough for the audit page to submit the selected instruction;
use concise identifiers such as `option_1`, not the display label. Do not put a
JSON string, markdown, or an additional wrapper object in proposal.
Every outcome also uses the three top-level wire fields error_code, error_retryable, and error_authorization_required.
Do not return a nested error object; the service creates that result-model
object after wire validation.

For every DWS write command in proposal, include the non-interactive
confirmation flag --yes. It confirms the already-reviewed command to the CLI;
it does not broaden the action or change its business meaning.

Write commands belong only as data inside proposal for Audit Agent B. Never
invoke, test, verify, or otherwise execute a write command yourself, including
through agent_cli. You may execute only reviewed read commands; Audit Agent B
executes an accepted proposal and performs its verification.
""".strip()

AUDIT_ROLE_BOUNDARY = """
Authoritative Audit role boundary: configurable Audit Rules are review
criteria. You are Audit Agent B; follow the supplied turn-specific execution
permission and finish with one valid Audit Agent wire JSON object matching the
supplied schema. The service converts it into a valid AuditAgentResult JSON
object after strict validation. Do not apply Consumer Agent A read-only
restrictions to an allowed Audit execution.

Wire field encoding: feedback and external_result are each either null or an
object in their own field. For revision_required, feedback is required and its
object has exactly these string fields:
rule, observation, and requested_revision. Do not use aliases such as
failed_rule, evidence, or required_change. For executed, external_result must
contain exactly operation_id, verification_summary, and
live_result_reference. operation_id must equal the candidate proposal
operation_id, verification_summary is a non-empty string describing the live
readback, and live_result_reference is an object containing the identifiers
needed to locate that readback. reconciliation is always an array: use [] unless
outcome is reconciled, and only reconciled
may contain reconciliation entries. Do not put receipt summaries, operation
metadata, or an object wrapper in reconciliation.
Every outcome also uses the three top-level wire fields error_code, error_retryable, and error_authorization_required.
Do not return a nested error object; the service creates that result-model
object after wire validation.

Outcome field combinations: executed requires side_effect_state=confirmed,
feedback=null, external_result as an object, and reconciliation=[];
revision_required requires side_effect_state=none, feedback as above,
external_result=null, and reconciliation=[]; reconciled requires
side_effect_state=unknown, feedback=null, external_result=null, and
reconciliation entries with exactly action_index, disposition (present,
absent, or ambiguous), and read_result_digest. needs_human and failed require
side_effect_state=none with all three nested fields empty (null, null, []).

The reconciled outcome is reserved for unknown-outcome recovery turns that
explicitly request read-only reconciliation. During a normal candidate review,
if live evidence shows that the proposed action already happened, do not execute
it and do not return reconciled. Instead, return revision_required and ask
Consumer Agent A to return no_action because the requested effect is already
present.

Never execute a DWS write command without --yes. Return concrete feedback for
Consumer Agent A to add the non-interactive confirmation flag before execution.
""".strip()


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
        if json.loads(SCHEMA_PATH.read_text(encoding="utf-8")) != (
            ConsumerAgentResult.model_json_schema()
        ):
            raise ValueError("consumer result schema does not match Pydantic model")
        claim = self.store.claim_agent_run(
            task.id,
            task.execution_generation,
            role=AgentRole.CONSUMER,
            proposal_revision=proposal_revision,
            turn_attempt=self.store.next_agent_run_turn_attempt(
                task.id,
                task.execution_generation,
                role=AgentRole.CONSUMER,
                proposal_revision=proposal_revision,
            ),
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
        persist_conversation_session = conversation_session_id is None
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
                prompt=context.render(
                    proposal_revision=proposal_revision,
                    feedback=feedback,
                ),
                session_id=session_id,
                developer_instructions=consumer_developer_instructions(
                    rendered_rules,
                    skill_protocol=render_business_skill_protocol(
                        installed_business_skill_catalog()
                    ),
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
                    failed_session_id = session_id or persisted.codex_session_id
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


def consumer_developer_instructions(
    audit_rules: str,
    *,
    skill_protocol: str = "",
) -> str:
    core = _developer_instructions(
        audit_rules=audit_rules,
        skill_instruction=CONSUMER_DYNAMIC_SKILL_BODY,
        wire_model=ConsumerAgentWireResult,
        result_model=ConsumerAgentResult,
    )
    instructions = _role_developer_instructions(
        core,
        capability_instructions=REVIEWED_DWS_READ_INSTRUCTIONS,
        role_boundary=CONSUMER_ROLE_BOUNDARY,
    )
    return instructions + (f"\n\n{skill_protocol}" if skill_protocol else "")


def audit_developer_instructions(
    audit_rules: str,
    *,
    allow_write: bool = True,
) -> str:
    core = _developer_instructions(
        audit_rules=audit_rules,
        skill_instruction=AUDIT_DYNAMIC_SKILL_BODY,
        wire_model=AuditAgentWireResult,
        result_model=AuditAgentResult,
    )
    return _role_developer_instructions(
        core,
        capability_instructions=(
            "Reread every verified Skill path supplied from Consumer A with "
            "agent_cli.read_skill and compare the returned sha256 with the supplied "
            "receipt before review or execution. Also read the operation Skill for "
            "each proposed capability. A missing, unreadable, changed, or mismatched "
            "Skill requires revision_required rather than a guess. Use "
            "agent_cli.execute_reviewed_read for live reads. "
            "For an unfamiliar DWS command, inspect its runtime contract with "
            '`dws schema --cli-path "<product> <command>" --compact --format json` '
            "before review; schema discovery is not an unavailable-tool result, "
            "and missing command syntax is a read-only evidence task. Execute the "
            "result only as a reviewed local read command. "
            + (
                "Use agent_cli.execute_reviewed_write only for allowed external "
                "writes. "
                if allow_write
                else "External writes are unavailable in this turn. "
            )
            + "Do not "
            "use native shell execution; the turn-specific permission determines "
            "whether a write is allowed, and unknown outcomes remain read-only."
        ),
        role_boundary=AUDIT_ROLE_BOUNDARY,
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


def _role_developer_instructions(
    role_instruction: str,
    *,
    capability_instructions: str,
    role_boundary: str,
) -> str:
    shared = (
        SHARED_RULES_PATH.read_text(encoding="utf-8").strip()
        if SHARED_RULES_PATH.is_file()
        else ""
    )
    instructions = (
        role_instruction
        + "\n\n## Capability Instructions\n"
        + capability_instructions
    )
    if shared:
        quoted_shared = "\n".join(f"> {line}" for line in shared.splitlines())
        instructions += "\n\n## Shared Agent Rules\n" + quoted_shared
    return instructions + "\n\n## Role Boundary\n" + role_boundary
