from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "skills" / "ceo-feedback-processing" / "SKILL.md"
PRESSURE_EVIDENCE_PATH = (
    ROOT
    / "skills"
    / "ceo-feedback-processing"
    / "pressure-test-evidence.md"
)


def _skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def test_feedback_processing_skill_documents_local_console_api_operations():
    text = _skill_text()
    base = "http://127.0.0.1:8765/api/console/feedback"
    assert base in text

    for operation in (
        "GET /api/console/feedback",
        "GET /api/console/feedback/{feedback_key}",
        "POST /api/console/feedback/batches",
        "GET /api/console/feedback/batches/{batch_id}",
        "PATCH /api/console/feedback/batches/{batch_id}",
        "PATCH /api/console/feedback/items/{feedback_key}",
        "POST /api/console/feedback/items/{feedback_key}/reopen",
        "POST /api/console/feedback/batches/{batch_id}/resolve",
    ):
        assert operation in text


def test_feedback_processing_skill_requires_identity_evidence_and_safe_workflow():
    text = _skill_text()
    for required in (
        "feedback_key",
        "stable identity",
        "supplied task",
        "attempt",
        "run",
        "persisted summaries",
        "references",
        "pending batch",
        "batch detail",
        "brainstorming skill",
        "reproduce before editing",
        "uncommitted user changes",
        "regression test",
        "focused tests",
        "broad tests",
        "git commit",
        "com.ceo-agent-service.main",
        "new PID",
        "/healthz",
        "failed/processing backlog",
        "per-item evidence",
        "only after all items complete",
        "incomplete evidence",
        "processing",
        "Confirm the repository root",
        "current branch",
        "current HEAD",
        "read every selected",
        "before claiming the batch",
        "every processing conversation",
        "exact command",
        "exit_code=0",
        "run time",
        "brief output",
        "git rev-parse HEAD",
        "matches the committed SHA",
        "launchctl print",
        "Every feedback-processing resolution requires restarting",
        "before/after PID",
        "failure or interruption",
        "same batch",
        "Workbench task",
        "workbench_task_id",
        "workbench_turn_id",
        "attempt_id",
        "agent_run_id",
        "commit SHA",
        "restart evidence",
        "health evidence",
        "before/after PID",
    ):
        assert required in text


def test_feedback_processing_skill_states_local_import_and_forbidden_paths():
    text = _skill_text().casefold()
    for required in (
        "import formatting never calls a model",
        "local-only",
        "no auth",
        "direct sqlite writes",
        "evidence-free resolve",
        "no new ui workflow",
        "service_bugfix",
    ):
        assert required in text

    assert text.endswith("\n")
    assert not text.endswith("\n\n")
    assert "no restart was applicable" not in text


def test_feedback_processing_skill_requires_fresh_current_round_after_reopen():
    text = " ".join(_skill_text().casefold().split())
    for required in (
        'post /api/console/feedback/items/{feedback_key}/reopen',
        '{"reason":',
        "returns to `pending`",
        "claim a new batch",
        "new processing round",
        "never copy or reuse",
        "old evidence",
        "current round",
        "retryable",
        "persist",
        "read back",
        "api",
    ):
        assert required in text

    assert "direct sqlite" in text
    assert "before marking the item resolved" in text


def test_feedback_processing_skill_links_persisted_pressure_test_evidence():
    skill_text = _skill_text()
    assert "[Pressure-test evidence](pressure-test-evidence.md)" in skill_text
    assert PRESSURE_EVIDENCE_PATH.is_file()

    evidence = PRESSURE_EVIDENCE_PATH.read_text(encoding="utf-8")
    for required in (
        "# Feedback Reopen Pressure-Test Evidence",
        "## RED baseline",
        "## Observed failures and rationalizations",
        "could not name the reopen endpoint",
        '“created or selected”',
        '“may avoid another code change”',
        "omitted explicit `retryable=0`",
        "## GREEN observable behaviors",
        "reopen creates no round",
        "claim creates the new batch and round",
        "old evidence and associations remain historical",
        "zero `processing`, `failed`, and `retryable`",
        "API persist and readback before resolution",
    ):
        assert required in evidence

    assert evidence.endswith("\n")
    assert "copy this prompt" not in evidence.casefold()
