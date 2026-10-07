"""Executed in each archived ref so production imports belong to that ref."""

import json
from hashlib import sha256
import sys
from types import SimpleNamespace
from pathlib import Path

from app.agent_context import (
    AgentTaskContext,
    AgentContextMessage,
    AuditTurnContext,
    MaterialReference,
    PriorReceipt,
    ManualRerunInstruction,
)
from app.agent_contracts import AuditFeedback, ConsumerAgentResult
from app.agent_runtime_contracts import RuntimeRoute
from app.audit_rules import render_audit_rules
from app.consumer_agent import (
    consumer_developer_instructions,
    audit_developer_instructions,
)
from app.reviewed_candidates import candidate_digest
from app.runtime_prompt_context import render_runtime_context
from app.store import AgentRole

payload = json.load(sys.stdin)
manifest = payload["manifest"]
settings = manifest["settings"]
configuration = None
audit_configuration = None
if payload["arm"] == "candidate":
    from app.prompt_composition import load_prompt_configuration

    configuration = load_prompt_configuration(create_missing=False)
    audit_configuration = load_prompt_configuration(create_missing=False, role="audit")
route = RuntimeRoute(
    name=settings["route_name"],
    runtime_kind="codex_cli",
    credential_mode="local_oauth",
    model=settings["model"],
)
rows = []
for case in manifest["cases"]:
    raw = dict(case["context"])
    raw["messages"] = tuple(AgentContextMessage(**item) for item in raw["messages"])
    raw["materials"] = tuple(
        MaterialReference(**{**item, "read_commands": tuple(item["read_commands"])})
        for item in raw["materials"]
    )
    raw["prior_receipts"] = tuple(
        PriorReceipt(**item) for item in raw["prior_receipts"]
    )
    if raw.get("manual_rerun"):
        raw["manual_rerun"] = ManualRerunInstruction(**raw["manual_rerun"])
    context = AgentTaskContext(**raw)
    feedback = (
        AuditFeedback.model_validate(case["feedback"]) if case["feedback"] else None
    )
    body = context.render(
        proposal_revision=case["proposal_revision"],
        feedback=feedback,
        current_time=settings["fixed_time"],
    )
    if configuration is not None:
        from app.prompt_composition import compose_consumer_task

        consumer_task = compose_consumer_task(
            configuration,
            task_context=body,
            scheduled_prompt=context.consumer_prompt,
            continuation=case["continuation"],
        )
    else:
        # Exact baseline ConsumerAgentRunner._execute_claimed semantic assembly.
        consumer_task = (
            "## Runtime Invariants\nPreserve typed proposal contracts and session boundaries. The proposal must match the supplied JSON Schema exactly.\n\n"
            + (
                "## Scheduled Consumer Prompt\n" + context.consumer_prompt + "\n\n"
                if context.consumer_prompt
                else ""
            )
            + body
            + case["continuation"]
        )
    rules = render_audit_rules(AgentRole.AUDIT, create_missing=False)
    subject = ConsumerAgentResult.model_validate(case["audit_subject"])
    digest = candidate_digest(subject)
    audit_context = AuditTurnContext(
        task=context,
        proposal_revision=case["proposal_revision"],
        operation_id="synthetic-operation-" + case["id"],
        candidate=subject,
        candidate_digest=digest,
        audit_rules=rules,
    )
    audit_task = (
        audit_context.render(
            current_time=settings["fixed_time"], developer_audit_rules=rules
        )
        if audit_configuration is not None
        else audit_context.render(current_time=settings["fixed_time"])
    )
    from app.prompt import work_profile_instruction

    fixture_profile = work_profile_instruction(create_missing=False)
    kwargs = {"runtime_context": "", "work_profile": fixture_profile}
    if configuration is not None:
        kwargs["prompt_configuration"] = configuration
    consumer_developer = consumer_developer_instructions(
        skill_protocol=context.skill_protocol_override or "", **kwargs
    )
    audit_kwargs = {**kwargs}
    if audit_configuration is not None:
        audit_kwargs["prompt_configuration"] = audit_configuration
    audit_developer = audit_developer_instructions(rules, **audit_kwargs)
    if context.skill_protocol_override:
        audit_developer += "\n\n" + context.skill_protocol_override
    task = SimpleNamespace(
        id=context.task_id,
        execution_generation="synthetic-generation",
        channel=context.channel,
        business_object_key="synthetic:" + case["id"],
        trigger_message_id=context.trigger_message_id,
        trigger_message_json=json.dumps({"raw_payload": context.trigger_raw_payload}),
    )
    for role, developer, task_prompt in (
        ("consumer", consumer_developer, consumer_task),
        ("audit", audit_developer, audit_task),
    ):
        command = [
            "codex",
            "exec",
            "--cd",
            "/synthetic/evaluation/workspace",
            "-c",
            "model_reasoning_effort=" + json.dumps(settings["reasoning_effort"]),
        ]
        runtime = render_runtime_context(
            role=role,
            route=route,
            command=command,
            task=task,
            current_time=settings["fixed_time"],
            working_directory=Path("/synthetic/evaluation/workspace"),
            invocation_facts={
                "stage_index": context.stage_index,
                "proposal_revision": case["proposal_revision"],
            },
        )
        developer += "\n\n" + runtime
        role_configuration = (
            configuration if role == "consumer" else audit_configuration
        )
        rows.append(
            {
                "case_id": case["id"],
                "role": role,
                "developer": developer,
                "task": task_prompt,
                "runtime_context": runtime,
                "static_developer_chars": len(developer) - len(runtime) - 2,
                "candidate_digest": digest if role == "audit" else None,
                "source_bindings": case["audit_subject"]["source_bindings"],
                "proposal_revision": case["proposal_revision"],
                "stage_index": context.stage_index,
                "predecessor_review_id": context.predecessor_review_id,
                "configuration_fingerprints": role_configuration.fingerprints()
                if role_configuration
                else None,
                "fixture_profile_instruction_sha256": sha256(
                    fixture_profile.encode()
                ).hexdigest(),
            }
        )
print(json.dumps(rows, ensure_ascii=False))
