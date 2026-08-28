from __future__ import annotations

from app.agent_contracts import AuditAgentResult
from app.agent_result import AgentError
from app.workbench.store import _begin_immediate_with_retry


def test_audit_feedback_is_a_first_class_structured_outcome() -> None:
    result = AuditAgentResult(
        outcome="feedback_provided",
        summary="候选需要补充信息",
        proposal_revision=3,
        feedback={
            "rule": "结果必须可执行",
            "observation": "缺少必要字段",
            "requested_revision": "补齐字段后重新输出",
        },
        external_result=None,
        error=AgentError(),
    )
    assert result.outcome.value == "feedback_provided"
    assert result.feedback is not None


def test_workbench_begin_immediate_retries_transient_lock() -> None:
    class LockedOnce:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, statement: str) -> None:
            self.calls += 1
            if self.calls == 1:
                raise __import__("sqlite3").OperationalError("database is locked")

    db = LockedOnce()
    _begin_immediate_with_retry(db, attempts=2, delay_seconds=0)
    assert db.calls == 2
