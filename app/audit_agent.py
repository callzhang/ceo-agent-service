"""Read-only, whole-candidate Audit turns."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from uuid import uuid4

from app.agent_context import AuditTurnContext
from app.agent_contracts import AuditAgentResult
from app.agent_effects import LEASE_SECONDS
from app.agent_result import ResultParseError
from app.agent_runtime_config import AgentRuntimeConfig
from app.agent_runtime_router import AgentRuntimeRouter
from app.agent_turn_runner import (
    AgentTurnProcess,
    AgentTurnRunResult,
    ProcessExecutor,
    repeated_result_failure_requires_fresh_session,
    result_correction_prompt,
)
from app.agent_wire_contracts import parse_audit_agent_wire_result
from app.audit_rules import render_audit_rules
from app.claude_runtime_adapter import ClaudeRuntimeAdapter
from app.codex_runtime_adapter import CodexRuntimeAdapter
from app.friday_runtime_adapter import FridayRuntimeAdapter
from app.reviewed_candidates import candidate_digest
from app.runtime_prompt_context import explicit_participant_timezones
from app.store import AgentRole, AgentRun, AutoReplyStore, ReplyTask
from app.wechat.codex_safety import ControlledCliConfig, make_audit_agent_command


SERVICE_ROOT = Path(__file__).resolve().parent.parent


class AuditAgentRunner:
    """Run an Audit Agent that can inspect, but cannot execute, a candidate."""

    def __init__(
        self,
        *,
        store: AutoReplyStore,
        workspace: Path,
        codex_bin: str = "codex",
        runtime_config: AgentRuntimeConfig | None = None,
        runtime_router: AgentRuntimeRouter | None = None,
        codex_adapter: CodexRuntimeAdapter | None = None,
        claude_adapter: ClaudeRuntimeAdapter | None = None,
        friday_adapter: FridayRuntimeAdapter | None = None,
        executor: ProcessExecutor | None = None,
        owner: str | None = None,
        refresh_runtime_capabilities: Callable[[], object] | None = None,
        forced_runtime_route=None,
        reasoning_effort: str = "",
        skill_protocol_override: str = "",
        skill_protocol_source: str = "explicit_custom",
        execution_environment: Mapping[str, str] | None = None,
    ) -> None:
        self.store = store
        self.workspace = workspace
        self.codex_bin = codex_bin
        self.runtime_config = runtime_config
        self.runtime_router = runtime_router
        self.codex_adapter = codex_adapter
        self.claude_adapter = claude_adapter
        self.friday_adapter = friday_adapter
        self.executor = executor
        self.owner = owner or f"audit-agent-{uuid4().hex}"
        self.refresh_runtime_capabilities = refresh_runtime_capabilities
        self.forced_runtime_route = forced_runtime_route
        self.reasoning_effort = reasoning_effort
        self.skill_protocol_override = skill_protocol_override
        self.skill_protocol_source = skill_protocol_source
        self.execution_environment = dict(execution_environment or {})

    @staticmethod
    def _required_capabilities(context: AuditTurnContext) -> frozenset[str]:
        required = {"role_bound_agent_tools"}
        if context.task.image_paths:
            required.add("image_input")
        return frozenset(required)

    def run(
        self,
        task: ReplyTask,
        context: AuditTurnContext,
        *,
        turn_attempt: int,
        parent_agent_run_id: int,
    ) -> AgentTurnRunResult[AuditAgentResult]:
        if context.task.task_id != task.id:
            raise ValueError("agent context task does not match reply task")
        exact_digest = candidate_digest(context.candidate)
        if context.candidate_digest != exact_digest:
            raise ValueError("Audit candidate digest mismatch")
        parent = self.store.get_agent_run(parent_agent_run_id)
        adopted = self.store.adopted_candidate_for_consumer_run(parent_agent_run_id)
        if (
            parent is None
            or parent.role is not AgentRole.CONSUMER
            or parent.status != "completed"
            or parent.reply_task_id != task.id
            or parent.execution_generation != task.execution_generation
            or parent.proposal_revision != context.proposal_revision
            or adopted is None
            or json.loads(adopted["candidate_json"]) != context.candidate.model_dump(mode="json")
        ):
            raise ValueError("Audit parent candidate mismatch")
        force_new_session = repeated_result_failure_requires_fresh_session(
            self.store, task, role=AgentRole.AUDIT,
            proposal_revision=context.proposal_revision,
        )
        claim = self.store.claim_agent_run(
            task.id,
            task.execution_generation,
            role=AgentRole.AUDIT,
            proposal_revision=context.proposal_revision,
            turn_attempt=turn_attempt,
            parent_agent_run_id=parent_agent_run_id,
            operation_id=context.operation_id,
            owner=self.owner,
            fresh_session=force_new_session,
            lease_seconds=LEASE_SECONDS,
        )
        if not claim.claimed:
            raise RuntimeError("agent_run_unavailable")
        return self._execute_claimed(
            task, context, run=claim.run,
            rendered_rules=render_audit_rules(AgentRole.AUDIT),
            force_new_session=force_new_session,
        )

    def _execute_claimed(
        self,
        task: ReplyTask,
        context: AuditTurnContext,
        *,
        run: AgentRun,
        rendered_rules: str,
        force_new_session: bool,
    ) -> AgentTurnRunResult[AuditAgentResult]:
        correction = result_correction_prompt(
            self.store,
            task,
            role=AgentRole.AUDIT,
            proposal_revision=context.proposal_revision,
        )
        from app.business_skills import (
            frozen_task_skill_names,
            render_task_skill_discovery,
        )
        from app.consumer_agent import audit_developer_sections, default_task_skill_catalog
        from app.prompt_composition import (
            PromptSection,
            compose_plain_task_assembly,
            load_prompt_configuration,
            prompt_section_facts,
            render_prompt_sections,
            task_specific_instructions,
        )

        configuration = load_prompt_configuration(role="audit")
        developer_sections = audit_developer_sections(
            rendered_rules,
            runtime_context="",
            prompt_configuration=configuration,
        )
        frozen_names = frozen_task_skill_names(
            task.trigger_message_json, context.task.skill_names,
        )
        unfrozen_names = tuple(
            name for name in context.task.skill_names if name not in frozen_names
        )
        task_skill_protocol = (
            context.task.skill_protocol_override
            if context.task.skill_protocol_override is not None
            else self.skill_protocol_override
        )
        task_skill_protocol_source = (
            context.task.skill_protocol_source
            if context.task.skill_protocol_override is not None
            else self.skill_protocol_source
        )
        task_skill_protocol_is_custom = (
            task_skill_protocol_source == "explicit_custom"
        )
        if context.task.skill_names:
            task_skill_text = render_task_skill_discovery(
                context.task.skill_names,
                catalog=default_task_skill_catalog(context.task.skill_names),
                frozen_names=frozen_names,
            )
            if task_skill_protocol_is_custom and task_skill_protocol:
                task_skill_text += (
                    "\n\n## Saved Task Skill Instructions\n"
                    + task_skill_protocol
                )
            if frozen_names and unfrozen_names:
                task_skill_source = (
                    "任务冻结与当前 Skill 选择 + 已保存任务约定"
                    if task_skill_protocol_is_custom and task_skill_protocol
                    else "任务冻结与当前 Skill 选择"
                )
            elif frozen_names:
                task_skill_source = (
                    "任务冻结 Skill 选择 + 已保存任务约定"
                    if task_skill_protocol_is_custom and task_skill_protocol
                    else "任务冻结 Skill 选择 + 当前 Skill 用途目录"
                )
            else:
                task_skill_source = (
                    "当前选中 Skill 用途目录 + 已保存任务约定"
                    if task_skill_protocol_is_custom and task_skill_protocol
                    else "当前选中 Skill 用途目录"
                )
        elif task_skill_protocol and task_skill_protocol_is_custom:
            task_skill_text = task_skill_protocol
            task_skill_source = "已保存自定义 Task Skill 约定"
        else:
            task_skill_text = render_task_skill_discovery(
                (), catalog=default_task_skill_catalog()
            )
            task_skill_source = "当前 Skill 用途目录"
        if context.task.skill_names:
            skill_protocol_fact = (
                task_skill_protocol or "" if task_skill_protocol_is_custom else ""
            )
            skill_protocol_source = (
                "explicit_custom"
                if task_skill_protocol_is_custom and task_skill_protocol
                else "selected_installed_skills" if unfrozen_names
                else "frozen_task_skills"
            )
        else:
            skill_protocol_fact = (
                task_skill_protocol or "" if task_skill_protocol_is_custom else ""
            )
            skill_protocol_source = (
                "task_override"
                if task_skill_protocol_is_custom and task_skill_protocol
                else "runtime_catalog"
            )
        task_sections = [
            PromptSection(
                "任务 Skill 入口",
                task_skill_source,
                "task",
                task_skill_text,
            ),
            PromptSection(
                "当前审核任务",
                "当前任务与候选",
                "task",
                context.render(),
            ),
        ]
        saved_task_instructions = task_specific_instructions(
            context.task.consumer_prompt, task_skill_protocol or "",
        )
        if saved_task_instructions:
            task_sections.insert(
                0,
                PromptSection(
                    "任务专项指令",
                    "已保存任务配置",
                    "task",
                    "## Scheduled Consumer Prompt\n" + saved_task_instructions,
                ),
            )
        if correction:
            task_sections.append(
                PromptSection(
                    "本轮结果修正",
                    "当前运行修正",
                    "task",
                    correction.strip("\n"),
                )
            )
        task_assembly = compose_plain_task_assembly(*task_sections)
        process = AgentTurnProcess[AuditAgentResult](
            store=self.store,
            task=task,
            workspace=self.workspace,
            owner=self.owner,
            executor=self.executor,
            codex_bin=self.codex_bin,
            runtime_config=self.runtime_config,
            runtime_router=self.runtime_router,
            codex_adapter=self.codex_adapter,
            claude_adapter=self.claude_adapter,
            friday_adapter=self.friday_adapter,
            refresh_runtime_capabilities=self.refresh_runtime_capabilities,
            forced_runtime_route=self.forced_runtime_route,
            reasoning_effort=self.reasoning_effort,
            execution_mode_environment=self.execution_environment,
        )

        def parse_result(raw: str) -> AuditAgentResult:
            result = parse_audit_agent_wire_result(raw)
            if result.proposal_revision != context.proposal_revision:
                raise ResultParseError("Audit proposal_revision does not match candidate")
            if result.candidate_digest != context.candidate_digest:
                raise ResultParseError("Audit candidate_digest does not match candidate")
            return result

        return process.execute(
            run=run,
            invocation_facts={
                "stage_index": context.task.stage_index,
                "skill_names": list(context.task.skill_names),
                "skill_protocol": skill_protocol_fact,
                "skill_protocol_source": skill_protocol_source,
                "participant_timezones": explicit_participant_timezones(
                    context.task.trigger_raw_payload
                ),
                "prompt_configuration": configuration.fingerprints(),
                "prompt_sections": prompt_section_facts(
                    developer_sections, task_assembly.sections
                ),
            },
            skill_names=context.task.skill_names,
            prompt=task_assembly.text,
            session_id=run.codex_session_id or None,
            force_new_session=force_new_session,
            developer_instructions=render_prompt_sections(
                developer_sections,
                placement="developer",
                omit_empty=False,
            ),
            configure_command=lambda command: make_audit_agent_command(
                command,
                controlled_cli=ControlledCliConfig(
                    command=sys.executable,
                    args=(
                        "-m", "app.agent_cli", "--role", "audit",
                        "--task-id", str(task.id), "--db", str(self.store.path),
                        "--execution-generation", task.execution_generation,
                    ),
                    cwd=str(SERVICE_ROOT),
                ),
            ),
            parse_result=parse_result,
            persist_conversation_session=False,
            image_paths=[Path(path) for path in context.task.image_paths],
            required_capabilities=self._required_capabilities(context),
        )
