"""The service, not the Agent turn, decides what a reported error means.

An Agent turn reports what it observed as a free-text `error_code`, and used
to decide on its own whether the task should be retried or wait for a person.
That let the model invent codes (`AUTHORIZATION_REQUIRED` beside
`authorization_required`, one missing tool under two names) and steer the
orchestrator with them: the retry decision followed whatever it wrote.

Derek, 2026-09-17: the service decides every error, with no exceptions. A code
the service knows keeps its name and gets the service's policy. Anything else
on a failed outcome becomes `agent_reported_failure`, keeps the Agent's wording in `source_code`
for diagnosis, and takes the ordinary bounded retry: the service saw no cause
it can act on, so it tries a limited number of times and then stops.

The flags the Agent sends with a code are never read.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

AGENT_REPORTED_FAILURE = "agent_reported_failure"
AGENT_ERROR_SOURCE = "agent"
PROVIDER_RISK_REJECTION_RERUN_BLOCK_REASON = (
    "运行时已拒绝当前对外动作；此入口不能重放历史候选。若仍需办理，"
    "请通过正式流程提交新候选并重新审核。"
)


def is_provider_risk_rejection(
    structured_error_json: str, *, attempt_error: str = ""
) -> bool:
    """Recognize the service refusal even when an Agent wrapper normalized its code."""
    if attempt_error.strip().lower() == "provider_risk_rejected":
        return True
    try:
        payload = json.loads(structured_error_json or "")
    except (TypeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    return any(
        isinstance(payload.get(field), str)
        and payload[field].strip().lower() == "provider_risk_rejected"
        for field in ("code", "source_code")
    )


@dataclass(frozen=True, slots=True)
class ErrorPolicy:
    retryable: bool
    authorization_required: bool


_WAIT_FOR_PERSON = ErrorPolicy(retryable=False, authorization_required=True)
_RECOVERS_BY_ITSELF = ErrorPolicy(retryable=True, authorization_required=False)
_FINAL = ErrorPolicy(retryable=False, authorization_required=False)


def _reportable_policies() -> dict[str, ErrorPolicy]:
    # Imported here: the unsubscribe module pulls in the browser stack, and
    # the wire contract is loaded by every Agent turn.
    from app.email_unsubscribe import (
        BROWSER_EXECUTION_ERROR_CODES,
        TRANSIENT_BROWSER_ERROR_CODES,
    )

    policies = {
        # Only a person can supply these; retrying reaches the same wall.
        "authorization_required": _WAIT_FOR_PERSON,
        "confirmation_required": _WAIT_FOR_PERSON,
        "management_confirmation_required": _WAIT_FOR_PERSON,
        "management_authorization_missing": _WAIT_FOR_PERSON,
        # The run was told not to execute; nothing to retry.
        "dry_run_execution_suppressed": _FINAL,
        # Runtime risk refusal requires a safer action or informed approval,
        # not another automatic Audit turn. Keep it failed, not needs_human.
        "provider_risk_rejected": _FINAL,
        # The provider refused the send because the identical message is
        # already delivered. Reply task 135612 failed four runs in a row on
        # this: the turn notifies the applicant, DingTalk suppresses the
        # repeat, no receipt comes back, the run fails, it retries, and the
        # provider suppresses it again. Duplicate suppression is the provider
        # saying the effect exists, so retrying can only repeat the loop.
        "provider_duplicate_no_readback": _FINAL,
        # A dependency that was not there this turn may be there the next.
        "dependency_read_unavailable": _RECOVERS_BY_ITSELF,
        "xiaoqing_interview_mcp_not_injected": _RECOVERS_BY_ITSELF,
        "xiaoqing_interview_unavailable": _RECOVERS_BY_ITSELF,
    }
    # Browser codes are written by the service's own browser tool and relayed
    # by the turn; the service already knows which of them a rerun can clear.
    for code in BROWSER_EXECUTION_ERROR_CODES:
        policies[code] = (
            _RECOVERS_BY_ITSELF if code in TRANSIENT_BROWSER_ERROR_CODES else _FINAL
        )
    return policies


def agent_error_payload(reported_code: str | None, *, failed: bool) -> dict[str, object]:
    """Turn what an Agent turn reported into the service's error record."""
    reported = (reported_code or "").strip()
    if not reported:
        return {"code": "", "retryable": False, "authorization_required": False}
    normalized = reported.lower()
    policy = _reportable_policies().get(normalized)
    if policy is not None:
        return {
            "code": normalized,
            "retryable": policy.retryable,
            "authorization_required": policy.authorization_required,
            "source": AGENT_ERROR_SOURCE,
            "source_code": reported,
        }
    if not failed:
        # On an outcome that is not a failure the code is the reason shown
        # beside it (why a person is needed, why nothing was done). It carries
        # no retry or authorization, so it drives nothing.
        return {
            "code": normalized,
            "retryable": False,
            "authorization_required": False,
            "source": AGENT_ERROR_SOURCE,
            "source_code": reported,
        }
    return {
        "code": AGENT_REPORTED_FAILURE,
        "retryable": True,
        "authorization_required": False,
        "source": AGENT_ERROR_SOURCE,
        "source_code": reported,
    }
