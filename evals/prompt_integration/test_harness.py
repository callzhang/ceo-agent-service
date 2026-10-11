"""Focused harness regression checks; no native/provider/business calls."""

import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import pytest

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


def test_audit_render_compatibility_uses_archived_runner_call():
    manifest = eval_module.load_manifest()
    archived_ref = "9851aed72762e88ea942b41f528e5f74115a97cf"
    with TemporaryDirectory(prefix="prompt-eval-archived-render-") as raw:
        source = Path(raw) / "source"
        source.mkdir()
        eval_module.archive_ref(archived_ref, source)
        historical = eval_module.extract(source, manifest, "candidate")
    historical_audit = next(row for row in historical if row["role"] == "audit")
    assert "## Audit Rules\n" not in historical_audit["task"]

    restored = eval_module.extract(ROOT, manifest, "candidate")
    restored_audit = next(row for row in restored if row["role"] == "audit")
    assert restored_audit["task"].count("## Audit Rules\n") == 1


def test_real_caller_empty_audit_context_keeps_current_main_wire_inputs():
    manifest = eval_module.load_manifest()
    with TemporaryDirectory(prefix="prompt-eval-runtime-context-") as raw:
        source = Path(raw) / "source"
        source.mkdir()
        eval_module.archive_ref("1e3711a049df525d80ddab33c92c17d72098b276", source)
        control = eval_module.extract(source, manifest, "baseline", audit_context_rules="runtime-empty")
    candidate = eval_module.extract(ROOT, manifest, "candidate", audit_context_rules="runtime-empty")
    assert len(control) == len(candidate) == 40
    for old, new in zip(control, candidate):
        assert (old["case_id"], old["role"]) == (new["case_id"], new["role"])
        for field in ("developer", "task", "runtime_context", "source_bindings", "candidate_digest"):
            assert old[field] == new[field]


def test_resume_accepts_only_exact_inputs_and_completed_prefix():
    rows = [
        {"case_id": "a", "role": "consumer", "developer": "d", "task": "t", "static_developer_chars": 1},
        {"case_id": "a", "role": "audit", "developer": "d", "task": "t2", "static_developer_chars": 1},
    ]
    prepared = {
        "mode": "native_synthetic_tool_free",
        "manifest_sha256": "manifest",
        "cases_sha256": "cases",
        "assembler_sha256": "assembler",
        "refs": {"candidate": "ref"},
        "settings": {"model": "fixed"},
        "arms": {"candidate": {"inputs": rows, "size_summary": eval_module.sizes(rows)}},
    }
    previous = json.loads(json.dumps(prepared))
    previous["arms"]["candidate"]["results"] = [
        {"case_id": "a", "role": "consumer", "native": {"ok": True, "result": {}}}
    ]
    assert eval_module.resume_position(previous, prepared, "candidate", rows) == 1
    changed = json.loads(json.dumps(previous))
    changed["arms"]["candidate"]["inputs"][1]["task"] = "different"
    try:
        eval_module.resume_position(changed, prepared, "candidate", rows)
    except ValueError as error:
        assert "inputs differ" in str(error)
    else:
        assert False, "changed inputs must prevent resume"
    changed = json.loads(json.dumps(previous))
    changed["arms"]["candidate"]["results"][0]["role"] = "audit"
    try:
        eval_module.resume_position(changed, prepared, "candidate", rows)
    except ValueError as error:
        assert "ordered prefix" in str(error)
    else:
        assert False, "a different result order must prevent resume"


def test_blind_packet_supports_four_case_supplement_and_distinct_keys():
    from evals.prompt_integration.blind_review import build_packet

    manifest = {"cases": [{
        "id": "one", "category": "message", "context": {}, "feedback": None,
        "proposal_revision": 1, "audit_subject": {}, "semantic_rubric": {},
    }]}
    cases_sha = eval_module.fingerprint(manifest["cases"])
    def report(arm):
        return {
            "completed": True, "cases_sha256": cases_sha, "settings": {"model": "fixed"},
            "arms": {arm: {"inputs": [
                {"case_id": "one", "role": role, "developer": "## System Action Contracts\nshared\n## Pydantic Wire Contract\n"}
                for role in ("consumer", "audit")
            ], "results": [
                {"case_id": "one", "role": role, "normalized": {"ok": True, "result": {}}, "native": {"raw": "{}"}}
                for role in ("consumer", "audit")
            ]}},
        }
    first, key_first = build_packet(report("baseline"), report("candidate"), manifest, namespace="matching")
    second, key_second = build_packet(report("baseline"), report("candidate"), manifest, namespace="profile")
    assert len(first["cases"]) == 1
    assert first["shared_system_action_contract"] == "## System Action Contracts\nshared\n"
    assert len(key_first) == 4
    assert set(key_first).isdisjoint(key_second)
    incomplete = report("candidate")
    incomplete["arms"]["candidate"]["results"].pop()
    with pytest.raises(ValueError, match="complete manifest"):
        build_packet(report("baseline"), incomplete, manifest)
    changed_contract = report("candidate")
    changed_contract["arms"]["candidate"]["inputs"][0]["developer"] = "## System Action Contracts\ndifferent\n## Pydantic Wire Contract\n"
    with pytest.raises(ValueError, match="differ between actual role inputs"):
        build_packet(report("baseline"), changed_contract, manifest)
    missing_input = report("candidate")
    missing_input["arms"]["candidate"]["inputs"].pop()
    with pytest.raises(ValueError, match="complete manifest inputs"):
        build_packet(report("baseline"), missing_input, manifest)


def test_reuse_consumer_evidence_requires_exact_input_and_valid_source():
    def row(role, fingerprint_value):
        return {
            "case_id": "one", "role": role, "developer": "same", "task": "task",
            "runtime_context": "runtime", "source_bindings": [], "candidate_digest": None,
            "configuration_fingerprints": fingerprint_value,
        }
    old_rows = [row("consumer", None), row("audit", None)]
    new_rows = [row("consumer", {"developer_template": "sha"}), row("audit", {"developer_template": "sha"})]
    source = {
        "completed": True, "mode": "native_synthetic_tool_free",
        "refs": {"baseline": eval_module.BASELINE}, "cases_sha256": "cases",
        "manifest_sha256": "manifest", "settings": {"model": "fixed"},
        "arms": {"baseline": {"inputs": old_rows, "results": [
            {"case_id": "one", "role": role,
             "native": {"ok": True, "tool_item_types": [], "result": {}},
             "normalized": {"ok": True}}
            for role in ("consumer", "audit")
        ]}},
    }
    prepared = {"cases_sha256": "cases", "manifest_sha256": "manifest", "settings": {"model": "fixed"}}
    reused = eval_module.reusable_consumer_results(source, "source-sha", prepared, new_rows)
    assert list(reused) == ["one"]
    assert reused["one"]["evidence_origin"]["source_report_sha256"] == "source-sha"
    assert "evidence_origin" not in source["arms"]["baseline"]["results"][0]
    changed = json.loads(json.dumps(new_rows))
    changed[0]["developer"] = "different"
    with pytest.raises(ValueError, match="Consumer input differs"):
        eval_module.reusable_consumer_results(source, "source-sha", prepared, changed)
    invalid = json.loads(json.dumps(source))
    invalid["arms"]["baseline"]["results"][0]["native"]["tool_item_types"] = ["function_call"]
    with pytest.raises(ValueError, match="native evidence invalid"):
        eval_module.reusable_consumer_results(invalid, "source-sha", prepared, new_rows)
    wrong_settings = dict(prepared, settings={"model": "changed"})
    with pytest.raises(ValueError, match="provenance differs"):
        eval_module.reusable_consumer_results(source, "source-sha", wrong_settings, new_rows)


def test_summary_counts_reused_evidence_separately_from_new_calls():
    from evals.prompt_integration.summarize import summarize

    def result(role, reused):
        value = {
            "role": role, "case_id": "one",
            "native": {"ok": True, "usage": [{"input_tokens": 2, "output_tokens": 1}], "elapsed_seconds": 3.0, "tool_item_types": []},
            "normalized": {"ok": True},
            "screen": {"contract_and_binding_screen_passed": True, "errors": []},
        }
        if reused:
            value["evidence_origin"] = {"kind": "reused_baseline_native"}
        return value

    report = {
        "completed": True, "refs": {"candidate": "ref"}, "settings": {}, "cases_sha256": "cases",
        "model_evidence": {"new_native_calls": 1, "reused_model_evidence": 1},
        "arms": {"candidate": {"size_summary": {}, "results": [result("consumer", True), result("audit", False)]}},
    }
    summary = summarize(report)
    assert summary["model_evidence"] == report["model_evidence"]
    assert summary["arms"]["candidate"]["roles"]["consumer"]["new_native_calls"] == 0
    assert summary["arms"]["candidate"]["roles"]["consumer"]["reused_model_evidence"] == 1
    assert summary["arms"]["candidate"]["roles"]["consumer"]["provider_usage_includes_reused_history"]
    assert summary["arms"]["candidate"]["roles"]["audit"]["new_native_calls"] == 1


def test_resume_reuse_verifies_source_results_mode_and_counts():
    consumer = {"case_id": "one", "role": "consumer", "native": {"raw": "saved"}, "evidence_origin": {"kind": "reused_baseline_native"}}
    audit = {"case_id": "one", "role": "audit", "native": {"raw": "fresh"}}
    previous = {
        "arms": {"candidate": {"results": [consumer, audit]}},
        "model_evidence": {"source_report_sha256": "sha", "reused_model_evidence": 1, "new_native_calls": 1},
    }
    eval_module.validate_resume_evidence(previous, "candidate", {"one": consumer}, "sha")
    with pytest.raises(ValueError, match="reuse mode differs"):
        eval_module.validate_resume_evidence(previous, "candidate", {}, None)
    tampered = json.loads(json.dumps(previous))
    tampered["arms"]["candidate"]["results"][0]["native"]["raw"] = "changed"
    with pytest.raises(ValueError, match="source evidence differs"):
        eval_module.validate_resume_evidence(tampered, "candidate", {"one": consumer}, "sha")
    tampered = json.loads(json.dumps(previous))
    tampered["model_evidence"]["new_native_calls"] = 2
    with pytest.raises(ValueError, match="count differs"):
        eval_module.validate_resume_evidence(tampered, "candidate", {"one": consumer}, "sha")
    baseline_only = {"arms": {"baseline": {"results": [
        {"case_id": "one", "role": "consumer"},
        {"case_id": "one", "role": "audit"},
    ]}}}
    eval_module.validate_resume_evidence(baseline_only, "baseline", {}, None)
