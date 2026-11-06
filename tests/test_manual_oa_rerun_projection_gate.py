"""Small release-gate coverage for audited OA manual-rerun projection."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app import cli
from tests.test_store import (
    test_manual_oa_rerun_reconciles_only_audited_no_action_projection as _exercise_projection,
)


@pytest.mark.parametrize("audit_approved", [True, False])
def test_rerun_projection_requires_current_audit_approval(
    tmp_path: Path, audit_approved: bool,
) -> None:
    _exercise_projection(tmp_path, audit_approved)


def test_startup_recovery_runs_manual_oa_no_action_reconciliation(monkeypatch):
    calls: list[str] = []

    def method(name, result=0):
        return lambda *args, **kwargs: calls.append(name) or result

    store = SimpleNamespace(
        reconcile_failed_reply_tasks_with_recorded_deliveries=method("deliveries"),
        reconcile_done_reply_tasks_with_failed_current_run=method("current-run"),
        reconcile_failed_reply_tasks_with_confirmation_required_runs=method(
            "confirmation"
        ),
        reconcile_failed_email_unsubscribe_tasks_with_terminal_receipts=method(
            "unsubscribe"
        ),
        skip_failed_reply_tasks_superseded_by_terminal_business_object=method(
            "superseded"
        ),
        reconcile_unresolved_reply_tasks_with_terminal_attempts=method("attempts"),
        reconcile_manual_oa_rerun_no_action_attempts=method("manual-oa-no-action"),
        reconcile_unresolved_reply_tasks_with_settlement_evidence=method("settled"),
        skip_failed_reply_tasks_with_terminal_no_action_run=method("no-action"),
        reconcile_valid_needs_human_projections=method("valid-human", 1),
        reconcile_invalid_needs_human_projections=method("invalid-human", 1),
        recover_orphaned_agent_runs_for_terminal_reply_tasks=method("orphaned-runs"),
        recover_orphaned_processing_reply_tasks=method("processing", []),
        recover_interrupted_agent_runs_after_service_restart=method("interrupted", []),
        resume_completed_agent_turns_after_service_restart=method("completed", []),
    )
    monkeypatch.setattr(cli, "AutoReplyStore", lambda path: store)

    recovered = cli._recover_orphaned_reply_tasks_on_service_start(
        SimpleNamespace(db_path=Path("/tmp/unused.sqlite3"))
    )

    assert "manual-oa-no-action" in calls
    assert recovered == 2
