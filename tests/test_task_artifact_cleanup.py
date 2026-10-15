from types import SimpleNamespace

import pytest

from app.worker import DingTalkAutoReplyWorker


@pytest.mark.parametrize("status", ["done", "failed", "needs_human", "skipped"])
def test_terminal_task_removes_all_task_generation_artifacts(tmp_path, monkeypatch, status):
    from app import task_artifacts

    monkeypatch.setattr(task_artifacts, "TASK_ARTIFACT_ROOT", tmp_path / "task-artifacts")
    workspace = tmp_path / "workspace"
    monkeypatch.setattr("app.config.workspace_path", lambda: workspace)
    task_root = task_artifacts.TASK_ARTIFACT_ROOT / "7"
    for generation in ("first", "second"):
        directory = task_root / generation
        directory.mkdir(parents=True)
        (directory / "result.md").write_text(generation)
    legacy_task_root = workspace / "consumer-artifacts/7"
    (legacy_task_root / "old-generation").mkdir(parents=True)
    (legacy_task_root / "old-generation/result.md").write_text("legacy")

    worker = object.__new__(DingTalkAutoReplyWorker)
    task = SimpleNamespace(id=7, status=status, execution_generation="second")
    worker.store = SimpleNamespace(get_reply_task=lambda task_id: task)
    worker._agent_orchestrator = lambda: SimpleNamespace(
        persisted_terminal_result=lambda current: object()
    )
    worker._apply_orchestration_result = lambda current, result: True

    worker._process_queued_task(None, task)

    assert not task_root.exists()
    assert not legacy_task_root.exists()


@pytest.mark.parametrize("status", ["pending", "processing"])
def test_nonterminal_task_keeps_artifacts_for_resume_and_audit(tmp_path, monkeypatch, status):
    from app import task_artifacts

    monkeypatch.setattr(task_artifacts, "TASK_ARTIFACT_ROOT", tmp_path / "task-artifacts")
    workspace = tmp_path / "workspace"
    monkeypatch.setattr("app.config.workspace_path", lambda: workspace)
    task_root = task_artifacts.TASK_ARTIFACT_ROOT / "7" / "first"
    task_root.mkdir(parents=True)
    artifact = task_root / "result.md"
    artifact.write_text("keep until terminal")
    legacy_artifact = workspace / "consumer-artifacts/7/old-generation/result.md"
    legacy_artifact.parent.mkdir(parents=True)
    legacy_artifact.write_text("legacy remains until task access or terminal")

    worker = object.__new__(DingTalkAutoReplyWorker)
    task = SimpleNamespace(id=7, status=status, execution_generation="first")
    worker.store = SimpleNamespace(get_reply_task=lambda task_id: task)
    worker._agent_orchestrator = lambda: SimpleNamespace(
        persisted_terminal_result=lambda current: object()
    )
    worker._apply_orchestration_result = lambda current, result: True

    worker._process_queued_task(None, task)

    assert artifact.read_text() == "keep until terminal"
    assert legacy_artifact.read_text() == "legacy remains until task access or terminal"
