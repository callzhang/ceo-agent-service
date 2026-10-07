"""Focused harness regression checks; no native/provider/business calls."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "prompt_eval", ROOT / "scripts/eval_prompt_integration.py"
)
eval_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eval_module)


def test_frozen_corpus_complete_sources_and_coverage():
    manifest = eval_module.load_manifest()
    assert len(manifest["cases"]) == 20
    assert {c["category"] for c in manifest["cases"]} == {
        "message",
        "oa",
        "email",
        "meeting",
        "calendar",
        "scheduled",
        "feedback",
        "prior_receipts",
    }
    for case in manifest["cases"]:
        assert (
            case["context"]["trigger_raw_payload"]["source_bindings"]
            == case["audit_subject"]["source_bindings"]
        )
        assert (
            eval_module.fingerprint(case["audit_subject"]["source_bindings"])
            == case["source_binding_sha256"]
        )


def test_audit_screen_checks_exact_digest_revision_not_just_outcome():
    case = {"expected_audit_outcomes": ["approve"]}
    row = {"role": "audit", "candidate_digest": "a" * 64, "proposal_revision": 2}
    native = {"tool_item_types": []}
    good = {
        "ok": True,
        "result": {
            "outcome": "approve",
            "candidate_digest": "a" * 64,
            "proposal_revision": 2,
        },
    }
    assert eval_module.screen(case, row, native, good)[
        "contract_and_binding_screen_passed"
    ]
    bad = {
        "ok": True,
        "result": {
            "outcome": "approve",
            "candidate_digest": "b" * 64,
            "proposal_revision": 1,
        },
    }
    result = eval_module.screen(case, row, native, bad)
    assert not result["contract_and_binding_screen_passed"]
    assert result["quality_pass"] is None
    assert result["semantic_review"] == "pending-independent-review"


def test_provider_usage_is_actual_event_only():
    assert eval_module.usage_from_stdout("") == []
    line = json.dumps(
        {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 8}}
    )
    assert eval_module.usage_from_stdout(line) == [
        {"input_tokens": 100, "output_tokens": 8}
    ]


def test_actual_candidate_preparation_contains_full_audit_bindings():
    manifest = eval_module.load_manifest()
    rows = eval_module.extract(ROOT, manifest, "candidate")
    assert len(rows) == 40
    for case in manifest["cases"]:
        row = next(
            r for r in rows if r["case_id"] == case["id"] and r["role"] == "audit"
        )
        assert row["source_bindings"] == case["audit_subject"]["source_bindings"]
        # Every source leaf remains present in the complete Audit context.
        assert case["context"]["messages"][0]["text"] in row["task"]
        assert "source_bindings" in row["task"]
        actual_candidate = json.loads(row["task"].split("Candidate revision\n")[-1])[
            "candidate"
        ]
        assert (
            actual_candidate["source_bindings"]
            == case["audit_subject"]["source_bindings"]
        )
        assert row["candidate_digest"] in row["task"]
