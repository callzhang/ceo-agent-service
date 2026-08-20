"""Shared validation helpers for persisted legacy execution receipts."""


def legacy_receipt_has_explicit_failure(value: object) -> bool:
    """Return whether any nested receipt field explicitly records failure."""

    if isinstance(value, list):
        return any(legacy_receipt_has_explicit_failure(item) for item in value)
    if not isinstance(value, dict):
        return False
    if (
        value.get("success") is False
        or value.get("ok") is False
        or value.get("result") is False
    ):
        return True
    error = value.get("error")
    if error is not None and error is not False and error != "":
        return True
    for field in ("errcode", "errorCode", "dingOpenErrcode", "code"):
        code = value.get(field)
        if isinstance(code, int) and not isinstance(code, bool) and code != 0:
            return True
        if isinstance(code, str) and code.strip().lstrip("-").isdigit():
            if int(code.strip()) != 0:
                return True
    if str(value.get("status") or "").strip().casefold() in {
        "failed",
        "blocked",
        "unknown",
    }:
        return True
    if str(value.get("state") or "").strip().casefold() in {
        "failed",
        "blocked",
        "unknown",
    }:
        return True
    if str(value.get("outcome") or "").strip().casefold() in {
        "failed",
        "unknown",
        "preflight_failed",
    }:
        return True
    return any(
        legacy_receipt_has_explicit_failure(item)
        for item in value.values()
        if isinstance(item, (dict, list))
    )
