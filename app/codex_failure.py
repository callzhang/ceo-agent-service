"""Stable classifications for native Codex process failures."""
from __future__ import annotations

CODEX_PROVIDER_UNAVAILABLE = "codex_provider_unavailable"
CODEX_PROVIDER_AUTH_FAILED = "codex_provider_auth_failed"
CODEX_PROCESS_FAILED = "codex_process_failed"


def is_codex_provider_auth_error(value: str) -> bool:
    """Return whether Codex reported one of its known provider auth failures."""
    detail = value.casefold()
    responses_api_auth_failed = (
        "unexpected status 401 unauthorized" in detail
        and (
            "missing bearer or basic authentication" in detail
            or "invalid api key" in detail
        )
        and "/v1/responses" in detail
    )
    chatgpt_codex_forbidden = (
        "unexpected status 403 forbidden" in detail
        and "chatgpt.com/backend-api/codex/responses" in detail
    )
    explicit_invalid_api_key = (
        "incorrect api key provided" in detail and "invalid_api_key" in detail
    )
    return (
        responses_api_auth_failed
        or chatgpt_codex_forbidden
        or explicit_invalid_api_key
    )


def classify_codex_process_failure(stdout: str, stderr: str) -> str:
    """Return the durable failure code for one native Codex process result."""
    detail = f"{stdout}\n{stderr}".casefold()
    if is_codex_provider_auth_error(detail) or (
        "missing bearer or basic authentication" in detail
        and "/v1/responses" in detail
    ):
        return CODEX_PROVIDER_AUTH_FAILED
    if any(
        marker in detail
        for marker in (
            "workspace is out of credits",
            "hit your usage limit",
            "quota exceeded",
        )
    ):
        return CODEX_PROVIDER_UNAVAILABLE
    return CODEX_PROCESS_FAILED
