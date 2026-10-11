import importlib.util
from hashlib import sha256
import json
from pathlib import Path
import sys

import pytest


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


@pytest.mark.parametrize("corruption", [None, "manifest", "case_id"])
def test_rescore_cli_preserves_native_outputs_and_never_calls_provider(tmp_path, monkeypatch, corruption):
    manifest_path = SCRIPT_DIR.parent / "evals/message_audience/v2.json"
    manifest = json.loads(manifest_path.read_text())
    rows = []
    for case in manifest["cases"]:
        outcome = case["expected_consumer"]
        result = {"outcome": outcome, "proposal": None, "decision_options": [],
                  "requested_input": "Missing counterpart" if outcome == "needs_human" else None,
                  "error": case.get("expected_error", {})}
        if case["expected_deliveries"]:
            result["proposal"] = {"actions": [
                {"capability": "dingtalk-chat", "operation": "send_message",
                 "target": delivery["target"], "payload": {"content": delivery["content"]}}
                for delivery in case["expected_deliveries"]
            ]}
        rows.append({"id": case["id"], "score": {"audit_applicable": outcome != "failed", "audit_ok": True},
                     "consumer_contract": {"result": result, "digest": "digest"},
                     "audit_subject": None,
                     "audit_contract": {"result": {"candidate_digest": "digest", "proposal_revision": 0}},
                     "consumer": {"raw": "retained native turn"}})
    report = {"manifest_sha256": sha256(manifest_path.read_bytes()).hexdigest(),
              "harness_sha256": "original harness", "settings": manifest["settings"],
              "baseline": {"ref": "baseline", "cases": rows},
              "candidate": {"ref": "candidate", "cases": rows}}
    if corruption == "manifest":
        report["manifest_sha256"] = "wrong"
    if corruption == "case_id":
        rows[0]["id"] = "other-case"
    original = tmp_path / "raw.json"
    original.write_text(json.dumps(report))
    output = tmp_path / "rescored.json"
    monkeypatch.setattr(sys, "argv", ["eval", "--rescore", str(original), "--output", str(output)])
    def forbidden_provider(*args, **kwargs):
        raise AssertionError("rescore must not create provider turns")
    monkeypatch.setattr(module, "run_suite", forbidden_provider)
    if corruption:
        with pytest.raises(ValueError):
            module.main()
        assert not output.exists()
    else:
        assert module.main() == 0
        checked = json.loads(output.read_text())
        assert checked["harness_sha256"] == report["harness_sha256"]
        assert checked["settings"] == report["settings"]
        assert checked["candidate"]["ref"] == "candidate"
        for original_row, checked_row in zip(rows, checked["candidate"]["cases"], strict=True):
            assert checked_row["consumer"] == original_row["consumer"]
            assert checked_row["consumer_contract"] == original_row["consumer_contract"]
            assert checked_row["audit_binding_passed"]
