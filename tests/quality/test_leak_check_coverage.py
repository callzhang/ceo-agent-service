from __future__ import annotations

import pytest

from app.leak_check import (
    _replace_case_insensitive,
    contains_credential,
    contains_forbidden_leak,
    contains_local_runtime_leak,
    redact_forbidden_leak_markers,
)


@pytest.mark.parametrize(
    "text",
    [
        "sk-proj-abcdefghijklmno",
        "AKIAABCDEFGHIJKLMNOP",  # pragma: allowlist secret
        "Bearer abcdefghijklmnop",
        "api_key=synthetic-secret",
        "-----BEGIN PRIVATE KEY-----",  # pragma: allowlist secret
    ],
)
def test_credential_patterns_are_rejected(text: str) -> None:
    assert contains_credential(text) is True
    assert contains_forbidden_leak(text) is True


@pytest.mark.parametrize("path", ["/tmp/output", "/var/log/x", "/private/var/folders/x"])
def test_local_runtime_paths_are_rejected(path: str) -> None:
    assert contains_local_runtime_leak(path) is True


def test_configured_private_prefix_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr("app.leak_check.forbidden_path_prefixes", lambda: ("/sensitive/",))

    assert contains_local_runtime_leak("see /sensitive/result") is True


def test_forbidden_markers_and_citations_are_rejected() -> None:
    assert contains_forbidden_leak("local Codex workspace") is True
    assert contains_forbidden_leak("evidence 【1】") is True
    assert contains_forbidden_leak("ordinary business answer") is False


def test_redaction_is_case_insensitive_and_normalizes_whitespace() -> None:
    redacted = redact_forbidden_leak_markers("CODEX\nsource: /tmp/result")

    assert "CODEX" not in redacted
    assert "source:" not in redacted
    assert "/tmp/" not in redacted
    assert "  " not in redacted
    assert _replace_case_insensitive("unchanged", "", "x") == "unchanged"
    assert _replace_case_insensitive("safe", "absent", "x") == "safe"
