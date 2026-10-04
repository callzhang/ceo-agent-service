"""The frozen contract comparison runs one executable probe per manifest case."""

from pathlib import Path

from evals.consumer_audit_system_execution.scenarios import SCENARIOS
from scripts.eval_consumer_audit_system_execution import ROOT, _manifest, _run_case


def test_frozen_manifest_has_exactly_the_executable_scenario_ids():
    manifest = _manifest()
    assert len(manifest["cases"]) == 20
    assert {case["id"] for case in manifest["cases"]} == set(SCENARIOS)
    assert manifest["settings"] == {
        "runtime_mode": "persisted-contract-replay",
        "provider": "scripted-domain-clients",
        "concurrency": 1,
        "timeout_seconds": 30,
        "model": None,
        "thinking": None,
    }


def test_case_process_uses_explicit_source_root(tmp_path: Path):
    assert _run_case("readonly_audit", ROOT)["ok"] is True
    empty_checkout = tmp_path / "empty"
    empty_checkout.mkdir()
    failed = _run_case("readonly_audit", empty_checkout)
    assert failed["ok"] is False
    assert failed["error"] == "scenario process failed without JSON result (exit 1)"
