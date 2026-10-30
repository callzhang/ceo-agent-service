"""Failures whose cause is outside this service, so nobody here can act on them.

Derek, 2026-09-25: a task that fails because a third party's page rejected
the request stays ``failed`` (History keeps it), but it is not an Attention
item: there is nothing to fix on our side, and rerunning reads the same page.

This is a list in code, not a column: the category of an unsubscribe browser
failure is already a fixed enum recorded in the task's error text
(``email_unsubscribe_browser_failed: category=<name>``), and which categories
count as external can be revised here without a data change.
"""

from __future__ import annotations

from app.email_unsubscribe import UnsubscribeBrowserFailure

# The page or the network answered badly, or did not answer. Categories that
# describe our own browser, session or model (runtime_unavailable,
# session_*_rejected, state_readback_failed, ...) are deliberately absent.
EXTERNAL_UNSUBSCRIBE_CATEGORIES = (
    UnsubscribeBrowserFailure.FORM_RESPONSE_REJECTED,
    UnsubscribeBrowserFailure.ONE_CLICK_RESPONSE_REJECTED,
    UnsubscribeBrowserFailure.OPERATION_FAILED,
    UnsubscribeBrowserFailure.OPERATION_TIMEOUT,
    UnsubscribeBrowserFailure.NAVIGATION_TARGET_INVALID,
    UnsubscribeBrowserFailure.CONTROL_UNAVAILABLE,
)

# Exact task error texts of an unsubscribe that failed for an external reason.
EXTERNAL_TASK_ERRORS = (
    *(
        f"email_unsubscribe_browser_failed: category={category.value}"
        for category in EXTERNAL_UNSUBSCRIBE_CATEGORIES
    ),
    "email_unsubscribe_browser_timeout",
    "unsubscribe_operation_rejected:TimeoutError",
)


def is_external_task_error(error: str) -> bool:
    """Return whether a persisted task error has a known external cause.

    The ``failed_browser`` form predates the current
    ``email_unsubscribe_browser_failed`` code but still appears on historical
    Attempts. Keep it aligned with the fixed external-category allowlist.
    """
    value = str(error or "").strip()
    if value in EXTERNAL_TASK_ERRORS:
        return True
    if any(value.startswith(prefix) for prefix in EXTERNAL_TASK_ERROR_PREFIXES):
        return True
    category_prefixes = (
        f"{code}: category={category.value}"
        for code in ("email_unsubscribe_browser_failed", "failed_browser")
        for category in EXTERNAL_UNSUBSCRIBE_CATEGORIES
    )
    if any(
        value == prefix or value.startswith(prefix + ";")
        for prefix in category_prefixes
    ):
        return True
    operation_marker = ";operation="
    if operation_marker in value:
        base, _, _operation = value.partition(operation_marker)
        if base in EXTERNAL_TASK_ERRORS:
            return True
    return False


# Error texts that start with one of these: the Dingteam OKR session expired or
# needs a login. Only a person can log in again, and a rerun reads the same
# expired session (Derek 2026-09-25).
EXTERNAL_TASK_ERROR_PREFIXES = (
    "email_provider_transient",
    "okr_headless_session_expired",
    "okr_authorization_required",
)


def external_task_error_sql(column: str = "reply_tasks.error") -> str:
    """SQL condition true when ``column`` holds an external-cause error text."""
    quoted = ", ".join("'" + error.replace("'", "''") + "'" for error in EXTERNAL_TASK_ERRORS)
    operation_context = (
        f"(instr({column}, ';operation=') > 0 and "
        f"substr({column}, 1, instr({column}, ';operation=') - 1) in ({quoted}))"
    )
    prefixes = " or ".join(
        f"{column} like '{prefix.replace(chr(39), chr(39) * 2)}%'"
        for prefix in EXTERNAL_TASK_ERROR_PREFIXES
    )
    category_texts = " or ".join(
        (
            f"(substr({column}, 1, {len(prefix)}) = '{prefix}' and "
            f"(length({column}) = {len(prefix)} or "
            f"substr({column}, {len(prefix) + 1}, 1) = ';'))"
        )
        for prefix in (
            f"{code}: category={category.value}"
            for code in ("email_unsubscribe_browser_failed", "failed_browser")
            for category in EXTERNAL_UNSUBSCRIBE_CATEGORIES
        )
    )
    return (
        f"({column} in ({quoted}) or {operation_context} or {prefixes} "
        f"or {category_texts})"
    )
