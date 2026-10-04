from pathlib import Path
from hashlib import sha256
import json
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
from app.wechat.codex_safety import ROLE_DISABLED_NATIVE_FEATURES


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
    for disabled in (
        *(f"features.{feature}=false" for feature in ROLE_DISABLED_NATIVE_FEATURES),
        'web_search="disabled"',
        "mcp_servers.node_repl.enabled=false", "mcp_servers.future.enabled=false",
    ):
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


def test_native_tool_events_fail_the_no_tools_run_even_with_valid_final_json(monkeypatch):
    stdout = "\n".join(json.dumps(event) for event in (
        {"type": "thread.started", "thread_id": "synthetic"},
        {"type": "item.started", "item": {"type": "reasoning", "id": "reason"}},
        {"type": "item.completed", "item": {"type": "mcp_tool_call", "id": "write"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": '{"outcome":"proposal"}'}},
    ))
    monkeypatch.setattr(business_eval.subprocess, "run", lambda *a, **kw:
                        subprocess.CompletedProcess(a[0], 0, stdout, ""))
    result = business_eval.run_role(command=["codex"], prompt="synthetic", timeout=1)
    assert result["ok"] is False
    assert result["error"] == "native_tool_use_violation"
    assert result["tool_item_types"] == ["mcp_tool_call"]


def test_native_reasoning_events_do_not_count_as_tool_use(monkeypatch):
    stdout = "\n".join(json.dumps(event) for event in (
        {"type": "item.started", "item": {"type": "reasoning", "id": "reason"}},
        {"type": "item.completed", "item": {"type": "reasoning", "id": "reason"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": '{"ok":true}'}},
    ))
    monkeypatch.setattr(business_eval.subprocess, "run", lambda *a, **kw:
                        subprocess.CompletedProcess(a[0], 0, stdout, ""))
    result = business_eval.run_role(command=["codex"], prompt="synthetic", timeout=1)
    assert result["ok"] is True
    assert result["tool_item_types"] == []


def test_native_error_item_does_not_count_as_tool_use(monkeypatch):
    stdout = "\n".join(json.dumps(event) for event in (
        {"type": "item.completed", "item": {"type": "error", "message": "tool unavailable"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": '{"wrote":false}'}},
    ))
    monkeypatch.setattr(business_eval.subprocess, "run", lambda *a, **kw:
                        subprocess.CompletedProcess(a[0], 0, stdout, ""))
    result = business_eval.run_role(command=["codex"], prompt="synthetic", timeout=1)
    assert result["ok"] is True
    assert result["tool_item_types"] == []


def test_tool_use_violation_cannot_be_scored_or_sent_to_audit(tmp_path: Path, monkeypatch):
    case = load_manifest()["cases"][0]
    manifest = {"settings": {"model": "gpt-5.6-sol", "reasoning_effort": "high",
                             "timeout_seconds_per_case": 1}, "cases": [case]}
    monkeypatch.setattr(business_eval, "role_instructions", lambda root: {"consumer": "c", "audit": "a"})
    monkeypatch.setattr(business_eval, "native_command", lambda **kw: ["codex"])
    calls = []

    def tool_using_role(**kwargs):
        calls.append(kwargs)
        return {"ok": False, "error": "native_tool_use_violation",
                "result": {"outcome": case["expected_consumer"]},
                "tool_item_types": ["mcp_tool_call"]}

    monkeypatch.setattr(business_eval, "run_role", tool_using_role)
    monkeypatch.setattr(business_eval, "normalize_role_result",
                        lambda *args: pytest.fail("invalid run was normalized"))
    report = business_eval.run_suite(ROOT, manifest)
    row = report["cases"][0]
    assert len(calls) == 1
    assert row["consumer_contract"]["error"] == "native_tool_use_violation"
    assert row["score"]["consumer_ok"] is False
    assert row["score"]["audit_ok"] is False


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
    assert report["harness_sha256"] == sha256(Path(business_eval.__file__).read_bytes()).hexdigest()
    assert report["source_flags"]["native_disabled_features"] == list(ROLE_DISABLED_NATIVE_FEATURES)
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
