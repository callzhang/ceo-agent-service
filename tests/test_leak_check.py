import pytest

from app.leak_check import assert_no_credentials, contains_credential, redact_credentials


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
        "AbCDefghIJklMNopQRstUVwxYZ0123456789+/==",
    ],
)
def test_contains_credential_recognizes_common_and_opaque_secret_families(secret: str):
    assert contains_credential(secret)


@pytest.mark.parametrize(
    "benign",
    [
        "Please summarize the quarterly operating review.",
        "550e8400-e29b-41d4-a716-446655440000",
        "/Users/example/Documents/report-final.md",
        "0123456789abcdef0123456789abcdef01234567",
        "The deployment token budget is 1200 words.",
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


def test_redact_credentials_removes_secret_without_changing_benign_text():
    secret = "ghp_abcdefghijklmnopqrstuvwxyz1234567890"
    text = f"credential={secret}; summary remains readable"

    redacted = redact_credentials(text)

    assert secret not in redacted
    assert "summary remains readable" in redacted
