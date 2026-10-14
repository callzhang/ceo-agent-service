"""Paired tool-free native Audit diagnostic with unchanged synthetic facts."""

# ruff: noqa: E402 -- support the direct script entry before application imports

from dataclasses import replace
from hashlib import sha256
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.action_contract_catalog import read_system_action_contract
from app.agent_context import AgentContextMessage, AgentTaskContext, AuditTurnContext
from app.agent_contracts import AuditAgentResult, ConsumerAgentResult
from app.agent_wire_contracts import parse_audit_agent_wire_result
from app.audit_rules import render_audit_rules
from app.consumer_agent import audit_developer_instructions
from app.reviewed_candidates import candidate_digest
from app.store import AgentRole
from scripts.eval_consumer_audit_business import native_command, run_role


def run(output: Path) -> None:
    manifest_path = ROOT / "evals/audit_revision_context/v1.json"
    manifest = json.loads(manifest_path.read_text())
    developer = audit_developer_instructions(render_audit_rules(AgentRole.AUDIT))
    command = native_command(model=manifest["model"], effort=manifest["reasoning_effort"],
                             developer_instructions=developer, workdir=ROOT)
    task = AgentTaskContext(
        task_id=1, channel="dingtalk", conversation_id="synthetic-group",
        conversation_title="Management", single_chat=False, trigger_message_id="request-1",
        trigger_sender="Requester", trigger_text="直接给个一句话建议，周会怎么开、谁参加。",
        trigger_create_time="2026-10-13 10:00:00 +0000", materials=(), prior_receipts=(),
        messages=(AgentContextMessage("source-1", "Requester",
            "原材料只是建议稿，尚未成为正式制度；具体姓名和职责对应尚未核实。请给简短建议。",
            "2026-10-13 10:00:00 +0000"),),
    )
    previous = AuditAgentResult.model_validate(dict(
        outcome="return", summary="Clarify trial boundaries", proposal_revision=2,
        candidate_digest="a" * 64, evidence_refs=["source-1"],
        feedback=dict(rule="supported_facts", observation="Draft is not an enacted policy.",
            requested_revision="补入‘职责确认前沿用现有参会要求’，缺席代理由主持人确认，无法替代时不作最终决策。"),
        error=dict(code="", retryable=False), risk="low", confidence=0.9,
        rule_coverage=1.0, information_completeness=0.9,
    ))
    results = []
    for case in manifest["cases"]:
        candidate = ConsumerAgentResult.model_validate(dict(
            outcome="proposal", summary="One-sentence meeting recommendation",
            proposal=dict(objective="Reply with requested meeting advice", actions=[dict(
                action_identity="meeting-advice", capability="dingtalk-chat",
                operation="send_group_message", target=dict(conversation_id="synthetic-group"),
                payload=dict(content=case["content"]), description="Reply in the original group",
            )], sourced_facts=[dict(assertion="Draft is not enacted; named duties unverified.",
                references=["source-1"])], authored_judgment="Provide requested advice, without invented duties."),
            error=dict(code="", retryable=False), risk="low", confidence=0.9,
            rule_coverage=1.0, information_completeness=0.9,
        ))
        base = AuditTurnContext(task, 3, "synthetic-op", candidate, candidate_digest(candidate), "")
        for side in ("baseline", "candidate"):
            context = base if side == "baseline" else replace(base, previous_revision_review=previous)
            prefix = (
                "Synthetic read-only evaluation. All facts and exact action contract are supplied inline; "
                "no tool or external action is available or needed. Complete current membership is "
                "Requester (HR manager) and Principal (CEO), with verified stable identities; "
                "this ordinary meeting advice is within their established discussion audience. "
                "There is no duplicate, cancellation, missing provider dependency or elapsed-time issue. "
                "The original request remains open. Return the exact Audit wire JSON.\n\n"
                + json.dumps(read_system_action_contract("dingtalk-chat", "send_group_message"), ensure_ascii=False)
                + "\n\n"
            )
            prompt = prefix + context.render(current_time="2026-10-13 10:01:00 +0000")
            raw = run_role(command=command, prompt=prompt, timeout=180)
            result = None
            if raw["ok"]:
                result = parse_audit_agent_wire_result(json.dumps({"type": "item.completed", "item": {
                    "type": "agent_message", "text": json.dumps(raw["result"]),
                }})).model_dump(mode="json")
            bound = bool(result and result["proposal_revision"] == 3
                         and result["candidate_digest"] == base.candidate_digest)
            item = dict(id=case["id"], side=side, prompt_sha256=sha256(prompt.encode()).hexdigest(),
                result=result, error=raw["error"], binding_correct=bound,
                expected_outcomes=case["expected_outcomes"], semantic_criterion=case["criterion"],
                outcome_passed=bool(bound and result["outcome"] in case["expected_outcomes"]))
            results.append(item)
            print(json.dumps({k: item[k] for k in ("id", "side", "outcome_passed", "error")}), flush=True)
    output.write_text(json.dumps(dict(version=manifest["version"],
        manifest_sha256=sha256(manifest_path.read_bytes()).hexdigest(),
        developer_sha256=sha256(developer.encode()).hexdigest(),
        model=manifest["model"], reasoning_effort=manifest["reasoning_effort"],
        source_boundary=manifest["source"], results=results), ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output)
