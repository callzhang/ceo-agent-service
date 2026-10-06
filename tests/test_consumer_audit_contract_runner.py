import json
from pathlib import Path
import subprocess

import pytest

from scripts import eval_consumer_audit_system_execution as runner


def test_contract_comparison_archives_both_refs_and_ignores_dirty_checkout(tmp_path, monkeypatch):
    def git(*args):
        return subprocess.run(
            ["git", "-c", "user.name=Eval fixture", "-c", "user.email=eval@example.test", *args],
            cwd=tmp_path, check=True, capture_output=True, text=True,
        ).stdout.strip()

    git("init", "-q")
    marker = tmp_path / "marker.txt"
    marker.write_text("baseline")
    git("add", "marker.txt")
    git("commit", "-qm", "baseline")
    baseline = git("rev-parse", "HEAD")
    marker.write_text("candidate")
    git("add", "marker.txt")
    git("commit", "-qm", "candidate")
    candidate = git("rev-parse", "HEAD")
    marker.write_text("uncommitted change")
    manifest = {"baseline_ref": baseline, "settings": {"model": None},
                "cases": [{"id": "probe", "expected": "bound source"}]}
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "MANIFEST", manifest_path)
    monkeypatch.setattr(runner, "SCENARIO_SCRIPT", Path(__file__))
    monkeypatch.setattr(runner, "_manifest", lambda: manifest)
    observed = []

    def run_case(case_id, source_root):
        observed.append((source_root, (source_root / "marker.txt").read_text()))
        return {"case_id": case_id, "ok": True}

    monkeypatch.setattr(runner, "_run_case", run_case)
    report = runner.compare(candidate_ref=candidate)
    assert report["candidate_ref"] == candidate
    assert report["baseline_ref"] == baseline
    assert report["candidate_root"] is None
    assert [text for _, text in observed] == ["baseline", "candidate"]
    assert observed[0][0] != observed[1][0]
    assert all(root != tmp_path for root, _ in observed)
    assert report["manifest_sha256"] and report["harness_sha256"] and report["scenario_sha256"]
    assert marker.read_text() == "uncommitted change"


def test_contract_comparison_rejects_mixed_checkout_and_commit():
    with pytest.raises(ValueError, match="mutually exclusive"):
        runner.compare(candidate_root=Path.cwd(), candidate_ref="HEAD")
