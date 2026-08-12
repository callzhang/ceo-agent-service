import pytest

from app.leak_check import (
    assert_no_credential_arguments,
    assert_no_credentials,
    contains_credential,
    redact_credentials,
)


@pytest.mark.parametrize(
    "secret",
    [
        "ghp_abcdefghijklmnopqrstuvwxyz1234567890",
        "github_pat_11AA22BB33_cccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        "glpat-abcdefghijklmnopqrst",
        "xoxb-123456789012-123456789012-abcdefghijklmnopqrstuvwx",
        "sk-proj-abcdefghijklmnopqrstuvwxyz1234567890",
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz1234",
        "Authorization: Basic dXNlcjpwYXNzd29yZA==",
        "https://user:password@example.com/private",
        "-----BEGIN PRIVATE KEY-----\nopaque\n-----END PRIVATE KEY-----",
    ],
)
def test_contains_credential_recognizes_common_secret_families(secret: str):
    assert contains_credential(secret)


@pytest.mark.parametrize(
    "benign",
    [
        "Please summarize the quarterly operating review.",
        "550e8400-e29b-41d4-a716-446655440000",
        "/Users/example/Documents/report-final.md",
        "0123456789abcdef0123456789abcdef01234567",
        "The deployment token budget is 1200 words.",
        "cidAbCDefghIJklMNopQRstUVwxYZ0123456789+/==conversation",
        "sha512-AbCDefghIJklMNopQRstUVwxYZ0123456789+/==digest",
        "AbCDefghIJklMNopQRstUVwxYZ0123456789+/==",
    ],
)
def test_contains_credential_keeps_common_benign_text_readable(benign: str):
    assert not contains_credential(benign)


def test_assert_no_credentials_checks_nested_string_leaves_and_sensitive_keys():
    with pytest.raises(ValueError, match="credential-bearing data is not allowed"):
        assert_no_credentials({"outer": ["safe", {"value": "gho_abcdefghijklmnopqrstuvwxyz"}]})
    with pytest.raises(ValueError, match="credential-bearing data is not allowed"):
        assert_no_credentials({"api_token": "opaque"})

    assert_no_credentials({"outer": ["safe", {"value": "ordinary text"}]})


def test_opaque_entropy_requires_an_explicit_credential_context():
    opaque = "AbCDefghIJklMNopQRstUVwxYZ0123456789+/=="

    assert not contains_credential(opaque)
    assert contains_credential(opaque, credential_context=True)
    assert_no_credentials({"conversation_id": opaque, "digest": opaque})
    for field_name in (
        "token",
        "password",
        "authorization",
        "private_key",
        "access_key",
        "credentials",
    ):
        with pytest.raises(ValueError, match="credential-bearing data is not allowed"):
            assert_no_credentials({field_name: opaque})


def test_redact_credentials_removes_secret_without_changing_benign_text():
    secret = "ghp_abcdefghijklmnopqrstuvwxyz1234567890"
    text = f"credential={secret}; summary remains readable"

    redacted = redact_credentials(text)

    assert secret not in redacted
    assert "summary remains readable" in redacted


@pytest.mark.parametrize(
    "argv",
    [
        ["curl", "-H", "Authorization: AbCDefghIJklMNopQRstUVwxYZ0123456789+/=="],
        ["curl", "--header=X-API-Key: AbCDefghIJklMNopQRstUVwxYZ0123456789+/=="],
        ["curl", "-HProxy-Authorization: opaque-scheme AbCDefghIJklMNopQRstUVwxYZ0123456789+/=="],
        ["curl", "--header", "Authorization: Bearer credentialvalue1234"],
        ["curl", "--header", "Authorization: Basic dXNlcjpwYXNzd29yZA=="],
        ["curl", "--header", "X-Secret-Key: AbCDefghIJklMNopQRstUVwxYZ0123456789+/=="],
        ["curl", "--header", "Ocp-Apim-Subscription-Key: AbCDefghIJklMNopQRstUVwxYZ0123456789+/=="],
    ],
)
def test_header_argument_forms_reject_sensitive_values_without_disclosure(argv: list[str]):
    with pytest.raises(ValueError, match="^credential-bearing data is not allowed$"):
        assert_no_credential_arguments(argv)


@pytest.mark.parametrize(
    "argv",
    [
        ["curl", "-H", "Content-Type: application/json"],
        ["curl", "--header=Accept: application/json"],
        ["curl", "-HX-Correlation-ID: cidAbCDefghIJklMNopQRstUVwxYZ0123456789+/=="],
    ],
)
def test_header_argument_forms_allow_benign_headers_and_identifiers(argv: list[str]):
    assert_no_credential_arguments(argv)
