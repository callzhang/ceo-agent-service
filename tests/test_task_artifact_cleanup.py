from types import SimpleNamespace

import pytest

from app.worker import DingTalkAutoReplyWorker


@pytest.mark.parametrize("status", ["done", "needs_human", "skipped"])
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


def test_failed_task_keeps_artifacts_for_a_formal_retry(tmp_path, monkeypatch):
    from app import task_artifacts

    monkeypatch.setattr(task_artifacts, "TASK_ARTIFACT_ROOT", tmp_path / "task-artifacts")
    workspace = tmp_path / "workspace"
    monkeypatch.setattr("app.config.workspace_path", lambda: workspace)
    task_root = task_artifacts.TASK_ARTIFACT_ROOT / "7" / "first"
    task_root.mkdir(parents=True)
    artifact = task_root / "analysis.json"
    artifact.write_text('{"source":"verified"}')
    legacy_artifact = workspace / "consumer-artifacts/7/old-generation/roster.json"
    legacy_artifact.parent.mkdir(parents=True)
    legacy_artifact.write_text('{"roster":[]}')

    worker = object.__new__(DingTalkAutoReplyWorker)
    task = SimpleNamespace(
        id=7, status="failed", channel="scheduled", execution_generation="first"
    )
    worker.store = SimpleNamespace(get_reply_task=lambda task_id: task)
    worker._agent_orchestrator = lambda: SimpleNamespace(
        persisted_terminal_result=lambda current: object()
    )
    worker._apply_orchestration_result = lambda current, result: True

    worker._process_queued_task(None, task)

    assert artifact.read_text() == '{"source":"verified"}'
    assert legacy_artifact.read_text() == '{"roster":[]}'


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


@pytest.mark.parametrize(
    "result_status, task_status",
    [
        ("executed", "done"),
        ("failed_terminal", "failed"),
        ("needs_human", "needs_human"),
        ("skipped", "skipped"),
        ("failed_retryable", "pending"),
    ],
)
def test_email_finalizer_cleans_only_after_persisted_terminal_status(
    tmp_path, monkeypatch, result_status, task_status
):
    from app import email_worker, task_artifacts

    monkeypatch.setattr(task_artifacts, "TASK_ARTIFACT_ROOT", tmp_path / "task-artifacts")
    workspace = tmp_path / "workspace"
    monkeypatch.setattr("app.config.workspace_path", lambda: workspace)
    task_root = task_artifacts.TASK_ARTIFACT_ROOT / "7"
    artifact = task_root / "generation-7" / "result.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("temporary email task evidence")
    legacy_root = workspace / "consumer-artifacts/7"
    legacy_artifact = legacy_root / "old-generation" / "result.md"
    legacy_artifact.parent.mkdir(parents=True)
    legacy_artifact.write_text("legacy email task evidence")
    task = SimpleNamespace(
        id=7, status="processing", attempts=1,
        execution_generation="generation-7",
        conversation_id="email-thread:7", conversation_title="Email task",
        trigger_message_id="email-action:7", trigger_sender="sender@example.com",
        trigger_text="email action",
    )
    run = SimpleNamespace(
        id=70, codex_session_id="", transcript_start_line=0,
        transcript_end_line=0, tool_events=[],
    )
    events = []

    class Store:
        def get_agent_run(self, run_id):
            assert run_id == run.id
            return run

        def current_reviewed_candidate(self, task_id, generation):
            assert (task_id, generation) == (task.id, task.execution_generation)
            return {"id": 71, "review_id": 72}

        def finalize_orchestrated_reply_task(self, **kwargs):
            assert kwargs["task_id"] == task.id
            assert kwargs["expected_execution_generation"] == task.execution_generation
            assert kwargs["task_status"] == task_status
            assert artifact.exists()
            task.status = kwargs["task_status"]
            events.append("finalized")

        def get_reply_task(self, task_id):
            assert task_id == task.id
            assert events == ["finalized"]
            events.append("read_persisted_task")
            return task

        def defer_reply_task(self, task_id, error, **kwargs):
            assert task_id == task.id
            assert kwargs["expected_execution_generation"] == task.execution_generation
            task.status = "pending"
            events.append("deferred")

    result = SimpleNamespace(
        status=result_status, final_run_id=run.id, summary="reviewed email result",
        error=SimpleNamespace(code="", authorization_required=False),
        candidate_id=71, review_id=72,
    )

    email_worker._finalize_email_task(Store(), task, result)

    assert task.status == task_status
    if task_status == "pending":
        assert events == ["deferred"]
        assert artifact.read_text() == "temporary email task evidence"
        assert legacy_artifact.read_text() == "legacy email task evidence"
    else:
        assert events == ["finalized", "read_persisted_task"]
        assert not task_root.exists()
        assert not legacy_root.exists()
