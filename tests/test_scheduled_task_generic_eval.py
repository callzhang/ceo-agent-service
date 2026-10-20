"""Evaluation transport must not change the frozen business acceptance oracle."""

import ast
import hashlib
import inspect
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from evals.scheduled_task_generic import run

HERE = Path(run.__file__).parent
CASES = json.loads((HERE / "cases.json").read_text())["cases"]
PRIOR = json.loads((HERE / "results.v2.json").read_text())


def test_original_cases_and_scores_remain_frozen():
    assert (
        hashlib.sha256((HERE / "cases.json").read_bytes()).hexdigest()
        == "938dcb51c9207d7d79bdc4240a50d827fbd1b376b606541b62799517fe862868"
    )
    for arm in ("baseline", "candidate"):
        for data in (PRIOR["arms"][arm], PRIOR["feedback_phases"][arm]["correction"]):
            scored = run.evaluate(CASES, [c["decision"] for c in data["cases"]], arm)
            assert scored == data["cases"]
    assert (
        hashlib.sha256(
            ast.dump(
                ast.parse(inspect.getsource(run.evaluate)), include_attributes=False
            ).encode()
        ).hexdigest()
        == "b016cc78e6254299521296e541eb4307f672d1a8632540bf49b6e8fe2f0133a7"
    )
    assert PRIOR["arms"]["candidate"]["common_pass_count"] == 5
    assert PRIOR["feedback_phases"]["candidate"]["post_feedback_common_pass_count"] == 4


def test_native_schema_rejects_recorded_production_names_in_eval_disposition():
    decisions = [
        c["decision"]
        for c in PRIOR["feedback_phases"]["candidate"]["correction"]["cases"]
    ]
    errors = list(
        Draft202012Validator(run.SCHEMA).iter_errors({"decisions": decisions})
    )
    assert errors, (
        "Audit rewrote respond/pending_source_failure to proposal/failed; old native schema silently accepted both"
    )


def test_audit_gets_same_dispositions_and_projection_contract(tmp_path, monkeypatch):
    captured = {}

    class Native:
        returncode = 0

        def __init__(self, command, **kwargs):
            captured["command"] = command
            captured["events"] = kwargs["stdout"]

        def communicate(self, prompt, **kwargs):
            captured["prompt"] = prompt
            path = Path(
                captured["command"][
                    captured["command"].index("--output-last-message") + 1
                ]
            )
            path.write_text('{"verdict":"approve","feedback":[]}')
            captured["events"].write('{"type":"turn.completed","usage":{}}\n')
            captured["events"].flush()

    monkeypatch.setattr(run.subprocess, "Popen", Native)
    run.run_arm("candidate", {}, CASES, [], tmp_path, phase="audit", previous=[])
    for label in (
        "respond",
        "pending_source_failure",
        "await_source_verification",
        "classify_only",
    ):
        assert label in captured["prompt"], f"Audit was never given eval label {label}"
    assert '"expected":' not in captured["prompt"]
    assert "provenance" in captured["prompt"] and "output" in captured["prompt"]


def test_case_schema_binds_identities_to_explicit_fact_ids_without_answers():
    schema = run.annotation_schema(CASES)
    Draft202012Validator.check_schema(schema)
    decisions = [c["decision"] for c in PRIOR["arms"]["candidate"]["cases"]]
    assert list(Draft202012Validator(schema).iter_errors({"decisions": decisions}))
    # Every rubric ID must be expressible, but neither its relevance nor the
    # expected disposition may be used to construct the model's schema.
    for case in CASES:
        ids = set(run.fact_identities(case["facts"]))
        expected = case["expected"]
        assert (
            set(
                expected.get("include_ids", [])
                + expected.get("exclude_ids", [])
                + expected.get("unverified_ids", [])
            )
            <= ids
        )
    stripped = [{k: v for k, v in c.items() if k != "expected"} for c in CASES]
    assert run.annotation_schema(stripped) == schema


def _product(case):
    from copy import deepcopy

    expected = case["expected"]
    items = [
        {
            "source_id": ident,
            "section": "draft",
            "content": "Synthetic authored draft",
            "owner": None,
            "verification": "verified",
        }
        for ident in expected.get("include_ids", [])
    ]
    items.extend(
        {
            "source_id": ident,
            "section": "evidence gap",
            "content": "Still unverified",
            "owner": None,
            "verification": "unverified",
        }
        for ident in expected.get("unverified_ids", [])
    )
    output = {
        "action": "no_action",
        "reason": "Only an offline draft exists",
        "target": {},
        "payload": {},
        "evidence_refs": [],
    }
    if "respond" in expected["dispositions"]:
        output = {
            "action": "reply",
            "reason": "A reply is warranted",
            "target": {
                "conversation_id": "eval-conversation",
                "message_id": "trigger-1",
            },
            "payload": {"content": "Here is the sourced response draft"},
            "evidence_refs": ["trigger-1"],
        }
    if "decline_updated_invitation" in expected["dispositions"]:
        output = {
            "action": "plan",
            "reason": "Current invitation conflicts",
            "target": {},
            "evidence_refs": [],
            "payload": {
                "objective": "Respond to current invitation",
                "actions": [
                    {
                        "description": "Decline current event",
                        "action_identity": "fixture-decline",
                        "capability": "dingtalk-calendar",
                        "operation": "respond_calendar_event",
                        "target": {"event_id": case["facts"]["event_id"]},
                        "payload": {"response_status": "declined"},
                    }
                ],
                "sourced_facts": [],
                "authored_judgment": "Verified conflict",
            },
        }
    if "applicant_omission" in expected:
        output = {
            "action": "failed",
            "reason": "Required source timed out, no applicant fault established",
            "target": {},
            "payload": {},
            "evidence_refs": ["source-error-1"],
        }
    classification = None
    if "category" in expected:
        classification = json.dumps(
            {
                "category": expected["category"],
                "important": expected["important"],
                "certainty": "certain",
                "confidence": 1.0,
                "reason": "Synthetic classification",
                "unsubscribe_candidate_index": expected["unsubscribe_candidate_index"],
            }
        )
    return deepcopy(
        {
            "case_id": case["id"],
            "destination_id": None,
            "work_items": items,
            "purpose_output_json": json.dumps(output),
            "classification_json": classification,
        }
    )


def test_actual_work_product_codec_checks_all_frozen_requirements():
    from evals.scheduled_task_generic import behavior

    for case in CASES:
        assert behavior.check_work_product(case, _product(case))["passed"]


def test_actual_draft_cannot_hide_irrelevant_output_behind_correct_annotation():
    from evals.scheduled_task_generic import behavior

    for case in CASES:
        if case["expected"].get("exclude_ids"):
            product = _product(case)
            product["work_items"].append(
                {
                    "source_id": case["expected"]["exclude_ids"][0],
                    "section": "management item",
                    "content": "This would actually be published",
                    "owner": None,
                    "verification": "verified",
                }
            )
            assert not behavior.check_work_product(case, product)["passed"]


def test_production_action_and_classification_payloads_are_not_eval_labels():
    from evals.scheduled_task_generic import behavior
    from pydantic import ValidationError
    import pytest

    product = _product(CASES[2])
    output = json.loads(product["purpose_output_json"])
    output["action"] = "respond"
    product["purpose_output_json"] = json.dumps(output)
    with pytest.raises(ValidationError):
        behavior.check_work_product(CASES[2], product)
    product = _product(CASES[4])
    output = json.loads(product["classification_json"])
    output["important"] = True
    product["classification_json"] = json.dumps(output)
    assert not behavior.check_work_product(CASES[4], product)["passed"]


def test_actual_copied_otp_body_fails_even_when_annotation_ids_are_correct():
    from evals.scheduled_task_generic import behavior

    case = CASES[0]
    product = _product(case)
    product["work_items"][0]["content"] += case["facts"]["important_emails"][0]["body"]
    checked = behavior.check_work_product(case, product)
    assert checked["checks"]["excluded_facts_absent_from_actual_draft_or_target"]
    assert not checked["checks"]["excluded_source_content_not_copied"]
    assert not checked["passed"]


def test_actual_probe_rejects_unregistered_action_operations():
    from evals.scheduled_task_generic import behavior

    product = _product(CASES[2])
    output = json.loads(product["purpose_output_json"])
    output["action"] = "plan"
    output["target"] = {}
    output["payload"] = {
        "objective": "Reply",
        "actions": [
            {
                "description": "Reply to trigger",
                "action_identity": "fixture",
                "capability": "dingtalk-chat",
                "operation": "reply",
                "target": {
                    "conversation_id": "eval-conversation",
                    "message_id": "trigger-1",
                },
                "payload": {"content": "Reply body"},
            }
        ],
        "sourced_facts": [],
        "authored_judgment": "Reply is needed",
    }
    product["purpose_output_json"] = json.dumps(output)
    assert not behavior.check_work_product(CASES[2], product)["passed"]


def test_actual_probe_prompt_supplies_required_operation_contracts_without_oracle():
    from evals.scheduled_task_generic import behavior

    material = behavior.business_material({}, CASES)
    assert "reply_to_message" in json.dumps(material)
    assert "declined" in json.dumps(material)
    assert '"expected":' not in json.dumps(material)


def test_actual_draft_identity_is_read_from_body_not_only_provenance_field():
    from evals.scheduled_task_generic import behavior

    case = CASES[1]
    product = _product(case)
    product["work_items"] = [
        {
            "source_id": case["facts"]["target"]["id"],
            "section": "Issue tracker",
            "content": "问题 ID："
            + case["facts"]["report"]["new_issue_id"]
            + "。保持未补证字段。",
            "owner": None,
            "verification": "verified",
        }
    ]
    assert behavior.check_work_product(case, product)["passed"]
    product["work_items"][0]["content"] = "No issue identity in the report"
    assert not behavior.check_work_product(case, product)["passed"]


def test_readback_identity_matching_does_not_promote_longer_identifier():
    from evals.scheduled_task_generic import behavior

    assert behavior.text_identities("问题 issue-1。issue-10", ["issue-1"]) == {
        "issue-1"
    }
    assert (
        behavior.text_identities("issue-10 xissue-1 issue-1-tail", ["issue-1"]) == set()
    )


def test_readback_rescore_preserves_native_outputs_and_failure_history():
    from evals.scheduled_task_generic import behavior
    from copy import deepcopy

    case = CASES[1]
    product = _product(case)
    product["work_items"] = [
        {
            "source_id": case["facts"]["target"]["id"],
            "section": "Issue tracker",
            "content": "问题 ID：" + case["facts"]["report"]["new_issue_id"] + "。",
            "owner": None,
            "verification": "verified",
        }
    ]
    saved = {
        "arms": {
            "candidate": {
                "status": "failed",
                "tool_item_count": 0,
                "raw_deliverables": {"deliverables": [product]},
                "checks": [
                    {
                        "case_id": case["id"],
                        "checks": {"body_readback": False},
                        "passed": False,
                    }
                ],
            }
        }
    }
    original = deepcopy(saved)
    rescored = behavior.rescore_saved_result(saved, [case])
    assert saved == original
    assert rescored["status"] == "passed"
    assert (
        rescored["arms"]["candidate"]["raw_deliverables"]
        == original["arms"]["candidate"]["raw_deliverables"]
    )
    assert rescored["arms"]["candidate"]["score_history"][0]["status"] == "failed"
