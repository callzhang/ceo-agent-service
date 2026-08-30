from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "skills" / "ceo-feedback-processing" / "SKILL.md"
OLD_PRESSURE_EVIDENCE_PATH = SKILL_PATH.parent / "pressure-test-evidence.md"
PRESSURE_EVIDENCE_PATH = ROOT / "tests" / "evidence" / "feedback_reopen_skill_pressure.md"


def _skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def _normalized_skill_text() -> str:
    return " ".join(_skill_text().split())


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
    text = _normalized_skill_text()
    for required in (
        "feedback_key",
        "stable identity",
        "Confirm the repository root, current branch, and current HEAD",
        "preserve uncommitted user changes",
        "brainstorming skill",
        "reproduce before editing",
        "regression test",
        "focused tests",
        "broad tests",
        "git commit",
        "git rev-parse HEAD",
        "matches the committed SHA",
        "com.ceo-agent-service.main",
        "launchctl print",
        "before/after PID",
        "/healthz",
        "failed/processing backlog",
        "per-item evidence",
        "incomplete evidence",
        "before claiming the batch",
        "every processing conversation",
        "exact command",
        "exit_code=0",
        "run time",
        "brief output",
        "Every feedback-processing resolution requires restarting",
        "failure or interruption",
        "same batch",
        "Workbench task",
        "workbench_task_id",
        "workbench_turn_id",
        "attempt_id",
        "agent_run_id",
        "restart evidence",
        "health evidence",
    ):
        assert required in text


def test_feedback_processing_skill_states_local_import_and_forbidden_paths():
    raw_text = _skill_text()
    text = " ".join(raw_text.casefold().split())
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

    assert raw_text.endswith("\n")
    assert not raw_text.endswith("\n\n")
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


def test_feedback_processing_skill_is_concise_and_excludes_run_narrative():
    skill_text = _skill_text()
    assert len(skill_text.split()) < 500
    assert "pressure-test-evidence" not in skill_text
    assert not OLD_PRESSURE_EVIDENCE_PATH.exists()


def test_feedback_processing_pressure_evidence_is_external_and_auditable():
    assert PRESSURE_EVIDENCE_PATH.is_file()

    evidence = PRESSURE_EVIDENCE_PATH.read_text(encoding="utf-8")
    for required in (
        "# Feedback Reopen Skill Pressure Verification",
        "## Execution metadata",
        "- Date: `2026-08-30`",
        "- RED input condition: Skill unavailable",
        "- GREEN input condition: same scenario with Skill available",
        "## Exact combined-pressure scenario",
        "deadline pressure",
        "old green receipt",
        "existing commit",
        "skip restart and backlog checks",
        "direct SQLite",
        "## Bounded verbatim RED baseline excerpts",
        "## Bounded verbatim GREEN excerpts",
        "## Explicit observed assertions",
        "This deterministic contract test validates artifact structure and derived Skill rules; it does not prove that the agent runs occurred.",
    ):
        assert required in evidence

    red_task = re.search(r"^- RED task path: `(/root/[^`]+)`$", evidence, re.MULTILINE)
    green_task = re.search(r"^- GREEN task path: `(/root/[^`]+)`$", evidence, re.MULTILINE)
    assert red_task is not None
    assert green_task is not None
    assert red_task.group(1) != green_task.group(1)
    assert evidence.count("- [x]") >= 6
    assert "```text" in evidence
    assert evidence.endswith("\n")
    assert "copy this prompt" not in evidence.casefold()
