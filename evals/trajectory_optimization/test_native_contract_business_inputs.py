"""Keep the eval's Audit input aligned with the actual orchestrator and runner."""

import importlib.util
import json
from pathlib import Path

from app.audit_rules import render_audit_rules
from app.consumer_agent import audit_developer_instructions
from app.store import AgentRole

BASE = Path(__file__).parent
SPEC = importlib.util.spec_from_file_location(
    "business_inputs", BASE / "native_contract_compaction.business_inputs.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_audit_policy_only_in_developer_and_candidate_preserved():
    manifest = json.loads((BASE / "native_contract_compaction.business_cases.v6.json").read_text())
    case = next(case for case in manifest["cases"] if case["role"] == "audit")
    context = MODULE.audit_context(case)
    rules = render_audit_rules(AgentRole.AUDIT, create_missing=False)
    developer = audit_developer_instructions(rules, runtime_context="")
    task = context.render(current_time=manifest["fixed_runtime_time"])
    assert context.audit_rules == ""
    assert rules not in task
    assert (developer + task).count(rules) == 1
    assert context.candidate.proposal.actions[0].payload["content"] in task
    assert context.candidate_digest in task
