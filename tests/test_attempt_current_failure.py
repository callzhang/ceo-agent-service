"""Current stop reasons must not inherit failures recovered by later turns."""

import json
from types import SimpleNamespace

from app.attempt_what_happened import build_what_happened
from app.audit_web import _agent_failure_reason_text
from app.store import AgentRole, AgentRun


def run(run_id, status, *, detail="", summary="", outcome="", role=AgentRole.AUDIT):
    return AgentRun.model_construct(
        id=run_id, role=role, status=status,
        structured_error_json=json.dumps({"code": "codex_result_invalid", "detail": detail}),
        adopted_result_json=json.dumps({"summary": summary, "outcome": outcome}),
        final_result_json="", tool_events=[], reply_task_id=None,
        execution_generation="current", side_effect_state="none",
    )


def attempt(summary="", code=""):
    return SimpleNamespace(send_status="failed", audit_summary=summary,
                           send_error=code, codex_reason="", agent_run_id=3)


def test_current_failure_uses_latest_failed_turn_only():
    runs = [run(1, "failed", detail="old sensitive-value error"),
            run(2, "failed", detail="old document permission error"),
            run(3, "failed", detail="current provider unavailable")]
    reason = _agent_failure_reason_text(attempt(), runs)
    assert "current provider unavailable" in reason
    assert "old sensitive" not in reason
    assert "old document" not in reason


def test_current_failure_after_recovered_retry_uses_persisted_stop():
    runs = [run(1, "failed", detail="agent_result_contains_sensitive_value"),
            run(3, "completed", summary="Current revision still unsuitable", outcome="return")]
    reason = _agent_failure_reason_text(
        attempt("audit_revision_exhausted: Current revision still unsuitable", "audit_revision_exhausted"), runs,
    )
    assert reason == "audit_revision_exhausted: Current revision still unsuitable"
    assert "agent_result_contains_sensitive_value" not in reason
    assert "旧记录" not in reason


def test_later_completed_turn_clears_historical_runtime_stop():
    result = build_what_happened([
        run(1, "failed", detail="agent_result_contains_sensitive_value"),
        run(2, "completed", outcome="approve"),
    ])
    assert result["stopped_because"] == {"kind": "none", "code": "", "sentence": ""}


def test_final_audit_return_is_current_stop():
    result = build_what_happened([
        run(1, "failed", detail="old sensitive-value error"),
        run(2, "completed", summary="Current candidate requires correction", outcome="return"),
    ])
    assert result["stopped_because"] == {
        "kind": "rule", "code": "return", "sentence": "Current candidate requires correction",
    }


def test_persisted_execution_stop_overrides_recovered_agent_errors():
    stop = {"kind": "execution", "code": "external_action_uncertain",
            "sentence": "Accepted return awaits native readback"}
    result = build_what_happened([
        run(1, "failed", detail="old document access error"),
        run(2, "completed", outcome="approve"),
    ], stop_reason=stop)
    assert result["stopped_because"] == stop
    assert result["reached_the_outside_world"] is False
    assert result["external_actions"] == []
