"""Small, shared task lifecycle contract.

Business progress belongs in the Agent trace.  These values only control
scheduling and recovery.
"""

from enum import StrEnum


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    NEEDS_HUMAN = "needs_human"


class TraceEvent(StrEnum):
    AGENT_OUTPUT = "agent_output"
    AUDIT_FEEDBACK = "audit_feedback"
    AGENT_REVISION = "agent_revision"
    AUDIT_RESULT = "audit_result"
    EXTERNAL_EFFECT = "external_effect"
    EXTERNAL_READBACK = "external_readback"


def is_schedulable(status: str) -> bool:
    """Return whether a task needs a worker turn under the canonical model."""

    return status in {TaskStatus.PENDING, TaskStatus.RUNNING}
