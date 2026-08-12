"""Stable classifications for native Codex process failures."""
from __future__ import annotations


CODEX_PROVIDER_UNAVAILABLE = "codex_provider_unavailable"
CODEX_PROVIDER_AUTH_FAILED = "codex_provider_auth_failed"
CODEX_PROCESS_FAILED = "codex_process_failed"


def classify_codex_process_failure(stdout: str, stderr: str) -> str:
    """Return the durable failure code for one native Codex process result."""
    detail = f"{stdout}\n{stderr}".casefold()
    if (
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
