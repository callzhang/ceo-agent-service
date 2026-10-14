"""Paired native Audit comparison of fixed failure-derived evidence cases."""

# ruff: noqa: E402 -- direct script entry
from hashlib import sha256
import argparse
import json
from pathlib import Path
import subprocess
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import agent_context
from app.agent_contracts import AuditAgentResult, ConsumerAgentResult
from app.agent_wire_contracts import AuditAgentWireResult, parse_audit_agent_wire_result
from app.audit_rules import render_audit_rules
from app.consumer_agent import audit_developer_instructions
from app.reviewed_candidates import candidate_digest
from app.reviewed_sources import capture_candidate_sources
from app.store import AgentRole
from app.action_contract_catalog import read_system_action_contract
from scripts.eval_consumer_audit_business import native_command, run_role


def subject(module, kind):
    card = kind == "card"
    original = "original-card" if card else "original-request"
    trigger_text = "请处理立项。taskId=original-node" if card else "请核对结算单和读取能力。"
    messages = [module.AgentContextMessage(original, "Applicant", trigger_text,
                                           "2026-10-14 14:41:07")]
    if card:
        messages = [module.AgentContextMessage(original, "Applicant",
                    "请处理立项。taskId=next-node", "2026-10-14 14:41:07")]
        messages.append(module.AgentContextMessage("approval-notice", "Principal",
                        "已同意 original-node，当前 next-node 由另一位审批人处理。",
                        "2026-10-14 14:46:26"))
    elif kind != "unanswered":
        messages.extend([
            module.AgentContextMessage("existing-reply", "Principal",
                "结算单盖章页金额和签章已读到；原请求已回应。", "2026-10-14 14:46:26"),
            module.AgentContextMessage("followup-request", "Applicant",
                "请再核对全部资料。", "2026-10-14 14:47:48"),
            module.AgentContextMessage("followup-result", "Principal",
                "新消息另有任务；已完成材料读取并回复，扫描页 OCR 成功。", "2026-10-14 14:50:00"),
        ])
    task = module.AgentTaskContext(1, "dingtalk", "conversation", "Conversation", True,
        original, "Applicant", trigger_text, "2026-10-14 14:41:07", tuple(messages), (), (),
        trigger_raw_payload={"task_id": "original-node"} if card else {})
    candidate = ConsumerAgentResult.model_validate({
        "outcome": "no_action", "summary": (
            "不再回复。原节点已由 Principal 同意；同一消息的当前卡片指向另一审批人的后续节点，"
            "不声称整个流程结束。" if card else "原请求已经得到后续回应，新消息已由另一任务处理，不重复发送。"),
        "proposal": None, "risk": "low", "confidence": .95, "information_completeness": 1,
        "rule_coverage": 1, "error": {"code": "", "retryable": False},
    })
    if kind == "wrong_target":
        data = candidate.model_dump(mode="json")
        data["outcome"] = "proposal"
        data["proposal"] = {"objective": "Reply", "sourced_facts": [], "authored_judgment": "Reply to the followup", "actions": [{
            "action_identity": "reply", "capability": "dingtalk-chat", "operation": "reply_to_message",
            "target": {"conversation_id": "conversation", "message_id": "followup-request"},
            "payload": {"content": "核对结果已完成，本次按原请求说明。"}, "description": "Reply"}]}
        candidate = ConsumerAgentResult.model_validate(data)
    candidate = capture_candidate_sources(candidate, task, None)
    previous = None
    if card:
        previous = AuditAgentResult.model_validate({"outcome": "return", "summary": "change binding",
            "error": {"code": "", "retryable": False}, "proposal_revision": 2, "candidate_digest": "a" * 64, "evidence_refs": [original],
            "feedback": {"rule": "exact source", "observation": "current card points next-node",
                "requested_revision": "保留 no_action，将 source_bindings 的旧任务号和链接改成 next-node。"},
            "risk": "low", "confidence": .95, "information_completeness": 1, "rule_coverage": 1})
    return module.AuditTurnContext(task, 3, "eval-operation", candidate,
        candidate_digest(candidate), "", previous_revision_review=previous)


def run(output, manifest_path):
    manifest = json.loads(manifest_path.read_text())
    source = subprocess.check_output(["git", "show", f"{manifest['baseline_ref']}:app/agent_context.py"],
                                     cwd=ROOT, text=True)
    baseline = types.ModuleType("app._attempt_context_baseline")
    sys.modules[baseline.__name__] = baseline
    exec(compile(source, "baseline_agent_context.py", "exec"), baseline.__dict__)
    developer = audit_developer_instructions(render_audit_rules(AgentRole.AUDIT))
    command = native_command(model=manifest["model"], effort=manifest["reasoning_effort"],
                             developer_instructions=developer, workdir=ROOT)
    prefix = ("Failure-derived sanitized read-only Audit evaluation. All verified facts below are complete. "
              "No provider or tool is available. Stable identity: Principal is the current authorized principal; "
              "Applicant is the verified conversation participant. The old request is open only in the "
              "unanswered control. Existing replies and the separate followup result are verified if present. "
              "Judge the exact fixed candidate; return strict Audit wire JSON.\n" +
              json.dumps(read_system_action_contract("dingtalk-chat", "reply_to_message"), ensure_ascii=False) +
              "\nWire schema:\n" + json.dumps(AuditAgentWireResult.model_json_schema(), ensure_ascii=False))
    results = []
    for case in manifest["cases"]:
        for side, module in (("baseline", baseline), ("candidate", agent_context)):
            context = subject(module, case["kind"])
            prompt = prefix + "\n" + context.render(current_time="2026-10-14T06:51:00+00:00")
            raw = run_role(command=command, prompt=prompt, timeout=180)
            result, error = None, raw["error"]
            if raw["ok"]:
                try:
                    result = parse_audit_agent_wire_result(json.dumps({"type": "item.completed",
                        "item": {"type": "agent_message", "text": json.dumps(raw["result"])}})).model_dump(mode="json")
                except ValueError as exc:
                    error = str(exc)
            bound = bool(result and result["candidate_digest"] == context.candidate_digest
                         and result["proposal_revision"] == context.proposal_revision)
            row = {"id": case["id"], "side": side, "prompt_sha256": sha256(prompt.encode()).hexdigest(),
                "result": result, "raw": raw["raw"], "error": error, "binding_correct": bound,
                "outcome_passed": bool(bound and result["outcome"] in case["expected_outcomes"]),
                "semantic_criterion": case["criterion"]}
            results.append(row)
            output.write_text(json.dumps({"manifest": manifest, "baseline_source_sha256": sha256(source.encode()).hexdigest(),
                "candidate_source_sha256": sha256(Path(agent_context.__file__).read_bytes()).hexdigest(),
                "developer_sha256": sha256(developer.encode()).hexdigest(), "results": results}, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps({k: row[k] for k in ("id", "side", "outcome_passed", "error")}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=ROOT / "evals/attempt_review_cases/context.v2.json")
    args = parser.parse_args()
    run(args.output, args.manifest)
