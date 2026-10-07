import importlib.util
from pathlib import Path
import sys


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
spec = importlib.util.spec_from_file_location("audience_eval", SCRIPT_DIR / "eval_message_audience.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_exact_delivery_oracle_rejects_self_copy_and_cross_audience_content():
    case = {"expected_consumer": "proposal", "expected_deliveries": [
        {"target": {"conversation_id": "group"}, "content": "public"},
        {"target": {"open_dingtalk_id": "counterpart"}, "content": "private"},
    ]}
    actions = [
        {"capability": "dingtalk-chat", "operation": "send_message", **delivery,
         "payload": {"content": delivery["content"]}}
        for delivery in case["expected_deliveries"]
    ]
    result = {"outcome": "proposal", "proposal": {"actions": actions}}
    assert module.delivery_matches(case, result)
    actions[1]["target"] = {"open_dingtalk_id": "principal"}
    assert not module.delivery_matches(case, result)
    actions[1]["target"] = {"open_dingtalk_id": "counterpart"}
    actions[0]["payload"]["content"] = "public private"
    assert not module.delivery_matches(case, result)


def test_gap_and_refusal_oracle_rejects_external_actions():
    for outcome in ("needs_human", "failed"):
        case = {"expected_consumer": outcome, "expected_deliveries": []}
        assert module.delivery_matches(case, {"outcome": outcome, "proposal": None})
        assert not module.delivery_matches(case, {"outcome": outcome, "proposal": {"actions": [{}]}})


def test_factual_gap_cannot_hide_self_delivery_in_a_human_option():
    case = {"expected_consumer": "needs_human", "expected_deliveries": [], "require_factual_input": True}
    result = {"outcome": "needs_human", "proposal": None, "requested_input": "Who is the counterpart?",
              "decision_options": [{"plan": {"actions": [{"target": {"open_dingtalk_id": "principal"}}]}}]}
    assert not module.delivery_matches(case, result)
    result["decision_options"] = []
    assert module.delivery_matches(case, result)
    result["requested_input"] = None
    assert not module.delivery_matches(case, result)


def test_refusal_requires_original_nonretryable_diagnosis():
    case = {"expected_consumer": "failed", "expected_deliveries": [],
            "expected_error": {"code": "provider_risk_rejected", "retryable": False}}
    result = {"outcome": "failed", "proposal": None, "error": {"code": "unrelated", "retryable": True}}
    assert not module.delivery_matches(case, result)
    result["error"] = case["expected_error"]
    assert module.delivery_matches(case, result)


def test_audit_counterfactual_matches_identity_not_action_order(monkeypatch):
    import eval_consumer_audit_business as harness

    monkeypatch.setattr(harness, "normalize_review_subject", lambda root, result: {"ok": True, "result": result})
    result = {"proposal": {"actions": [
        {"target": {"open_dingtalk_id": "counterpart"}, "payload": {"content": "private"}},
        {"target": {"conversation_id": "group"}, "payload": {"content": "public"}},
    ]}}
    case = {"audit_override": {"action_overrides": [
        {"target_match": {"open_dingtalk_id": "counterpart"}, "target": {"open_dingtalk_id": "principal"}},
    ]}}
    subject = harness.audit_subject(Path("."), case, result, None)
    assert subject["result"]["proposal"]["actions"][0]["target"] == {"open_dingtalk_id": "principal"}
    assert result["proposal"]["actions"][0]["target"] == {"open_dingtalk_id": "counterpart"}
    case["audit_override"]["action_overrides"][0]["target_match"] = {"open_dingtalk_id": "absent"}
    assert not harness.audit_subject(Path("."), case, result, None)["ok"]


def test_audit_gate_requires_exact_subject_digest_and_revision():
    row = {"score": {"audit_applicable": True},
           "consumer_contract": {"digest": "original"},
           "audit_subject": {"digest": "mutated"},
           "audit_contract": {"result": {"candidate_digest": "mutated", "proposal_revision": 0}}}
    assert module.audit_binding_matches(row)
    row["audit_contract"]["result"]["candidate_digest"] = "original"
    assert not module.audit_binding_matches(row)
    row["audit_contract"]["result"]["candidate_digest"] = "mutated"
    row["audit_contract"]["result"]["proposal_revision"] = 99
    assert not module.audit_binding_matches(row)
