from pathlib import Path
import subprocess

import pytest

from scripts import eval_consumer_audit_business as business_eval
from scripts.eval_consumer_audit_business import (
    MANIFEST_PATH,
    _event_error,
    load_manifest,
    native_command,
    native_mcp_server_names,
    normalize_role_result,
    score_case,
    source_identity,
    ROOT,
)


def test_frozen_native_business_corpus_is_separate_from_contract_replay():
    manifest = load_manifest()
    assert MANIFEST_PATH.name == "v2.json"
    assert manifest["baseline_ref"] == "a7d4738a3b6591abc4d8fc69236b5517b4f98004"
    assert manifest["settings"] == {
        "model": "gpt-5.6-sol", "reasoning_effort": "high", "concurrency": 1,
        "timeout_seconds_per_case": 300, "tools": "none", "external_facts": "synthetic-inline-only",
    }
    assert len(manifest["cases"]) == 8
    assert len({case["id"] for case in manifest["cases"]}) == 8
    assert all(case["skill_excerpt"] and case["facts"] for case in manifest["cases"])


def test_v2_changes_only_model_from_frozen_v1():
    v1 = load_manifest(MANIFEST_PATH.with_name("v1.json"))
    v2 = load_manifest(MANIFEST_PATH)
    assert v1["settings"]["model"] == "gpt-6.1-sol"
    assert v2["settings"]["model"] == "gpt-5.6-sol"
    assert v1["cases"] == v2["cases"]
    assert v1["baseline_ref"] == v2["baseline_ref"]
    assert {key: value for key, value in v1["settings"].items() if key != "model"} == {
        key: value for key, value in v2["settings"].items() if key != "model"
    }


def test_native_invocation_disables_inherited_tools_and_pins_model(tmp_path: Path, monkeypatch):
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "config.toml").write_text(
        '[mcp_servers.node_repl]\ncommand="/usr/bin/node"\n'
        '[mcp_servers.future]\nurl="https://example.test/mcp"\n', encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(home))
    assert native_mcp_server_names() == ("future", "node_repl")
    command = native_command(model="gpt-5.6-sol", effort="high",
                             developer_instructions="Synthetic instructions", workdir=tmp_path)
    joined = " ".join(command)
    assert command[:2] == ["codex", "exec"]
    assert "--ignore-user-config" not in command
    assert "--ephemeral" in command
    assert "--sandbox read-only" in joined
    assert "--model gpt-5.6-sol" in joined
    assert 'model_reasoning_effort="high"' in joined
    for disabled in ("features.shell_tool=false", "features.unified_exec=false",
                     "features.apply_patch=false", "features.computer_use=false",
                     "features.apps=false", "features.plugins=false", 'web_search="disabled"',
                     "mcp_servers.node_repl.enabled=false", "mcp_servers.future.enabled=false"):
        assert disabled in joined
    assert "tools.enabled_tools=[]" not in joined
    assert "mcp_servers={}" not in joined


def test_business_score_requires_judgment_and_reviewer_response():
    case = {"expected_consumer": "proposal", "required_concepts": ["approve", "801"],
            "forbidden_concepts": ["needs_human"]}
    correct = {"outcome": "proposal", "summary": "Approve task 801"}
    assert score_case(case, correct, {"outcome": "approve"})["consumer_ok"]
    assert score_case(case, correct, {"outcome": "approve"})["audit_ok"]
    assert not score_case(case, correct, {"outcome": "return"})["audit_ok"]
    wrong = {"outcome": "needs_human", "summary": "Ask the principal"}
    assert not score_case(case, wrong, {"outcome": "approve"})["consumer_ok"]
    assert score_case(case, wrong, {"outcome": "reject"})["audit_ok"]


def test_business_score_normalizes_contract_enum_spelling():
    case = {"expected_consumer": "proposal", "required_concepts": ["approve"],
            "forbidden_concepts": ["needs_human"]}
    result = score_case(case, {"outcome": "proposal", "summary": "Approve after needs_human review"},
                        {"outcome": "approve"})
    assert not result["consumer_ok"]


def test_native_json_error_is_reported_before_rollout_warnings():
    assert _event_error('{"type":"turn.failed","error":{"message":"model unavailable"}}') == "model unavailable"
    assert _event_error('{"type":"error","message":"authorization: abc123 failed"}') == "authorization=[REDACTED] failed"


def test_current_role_wire_is_normalized_with_exact_candidate_digest():
    from tests.test_system_executor import _candidate

    wire = _candidate().model_dump(mode="json")
    wire.pop("source_bindings")  # service-populated, never model-authored
    error = wire.pop("error")
    wire.update(error_code=error["code"], error_retryable=error["retryable"],
                error_authorization_required=error["authorization_required"])

    normalized = normalize_role_result(ROOT, "consumer", wire)
    assert normalized["ok"]
    assert normalized["result"]["outcome"] == "proposal"
    assert len(normalized["digest"]) == 64


def test_source_identity_marks_dirty_tree_without_claiming_exact_commit(tmp_path: Path):
    identity = source_identity(tmp_path)
    assert identity["head"] is None
    assert identity["dirty"] is None
    assert len(identity["source_fingerprint_sha256"]) == 64


def test_archived_source_identity_never_claims_a_worktree_head(tmp_path: Path, monkeypatch):
    def unexpected_git(*args, **kwargs):
        raise AssertionError("archive identity must not inspect surrounding Git checkout")

    monkeypatch.setattr(business_eval.subprocess, "run", unexpected_git)
    identity = source_identity(tmp_path, archived=True)
    assert identity["head"] is None
    assert identity["dirty"] is None
    assert len(identity["source_fingerprint_sha256"]) == 64


def test_candidate_ref_archives_exact_sha_into_independent_tree_without_model_calls(monkeypatch):
    expected_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
        text=True, check=True,
    ).stdout.strip()
    archived = []
    evaluated = []

    def fake_archive(ref, destination):
        archived.append((ref, destination))
        (destination / "archive-marker").write_text(ref, encoding="utf-8")

    def fake_run_suite(root, manifest, *, archived=False):
        evaluated.append((root, archived))
        return {"identity": source_identity(root, archived=archived),
                "archive_marker": (root / "archive-marker").read_text(encoding="utf-8")}

    monkeypatch.setattr(business_eval, "archive_ref", fake_archive)
    monkeypatch.setattr(business_eval, "run_suite", fake_run_suite)
    report = business_eval.compare(candidate_ref="HEAD")

    assert report["candidate_ref"] == expected_sha
    assert [ref for ref, _ in archived] == [report["baseline_ref"], expected_sha]
    assert archived[0][1] != archived[1][1]
    assert evaluated == [(archived[0][1], True), (archived[1][1], True)]
    assert report["candidate"]["archive_marker"] == expected_sha
    assert report["candidate"]["identity"]["head"] is None
    assert report["candidate"]["identity"]["dirty"] is None


def test_candidate_ref_and_root_are_mutually_exclusive_without_running_models(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(business_eval, "run_suite", lambda *args, **kwargs: pytest.fail("model suite ran"))
    with pytest.raises(ValueError, match="candidate_root and candidate_ref"):
        business_eval.compare(candidate_root=tmp_path, candidate_ref="HEAD")
    with pytest.raises(SystemExit) as raised:
        business_eval.main(["--candidate-root", str(tmp_path), "--candidate-ref", "HEAD"])
    assert raised.value.code == 2


def test_candidate_ref_cli_forwards_ref_without_starting_native_model(monkeypatch, capsys):
    calls = []

    def fake_compare(**kwargs):
        calls.append(kwargs)
        return {"mode": "test", "candidate_ref": "a" * 40}

    monkeypatch.setattr(business_eval, "compare", fake_compare)
    assert business_eval.main(["--candidate-ref", "HEAD"]) == 0
    assert calls == [{"candidate_root": None, "candidate_ref": "HEAD",
                      "manifest_path": MANIFEST_PATH}]
    assert '"candidate_ref": "' + "a" * 40 + '"' in capsys.readouterr().out


def test_manifest_refuses_changed_runtime_settings(tmp_path: Path):
    import json

    manifest = load_manifest()
    manifest["settings"]["concurrency"] = 2
    altered = tmp_path / "changed.json"
    altered.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="settings changed"):
        load_manifest(altered)


def test_manifest_refuses_changed_business_facts(tmp_path: Path):
    import json

    manifest = load_manifest()
    manifest["cases"][0]["facts"] += " Extra invented fact."
    altered = tmp_path / "changed.json"
    altered.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="cases changed"):
        load_manifest(altered)


def test_v1_probe_failure_is_recorded_without_changing_manifest():
    import json

    record = json.loads(MANIFEST_PATH.with_name("v1_probe_failure.json").read_text())
    assert record["manifest_version"] == "consumer-audit-business.v1"
    assert record["provider_status"] == 400
    assert record["provider_error_type"] == "invalid_request_error"
    assert "not supported" in record["message"]
