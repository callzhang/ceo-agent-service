"""A link followed by its page text is prose, not one signed URL.

2026-10-25: a WeChat context message of the form "<url>\n【正文摘录】<page text>" was
parsed as a single URL (urlsplit drops the newline), the page's "...Password Show
Forgot password?..." became its query string, and the whole Consumer result was
refused as containing a credential. A genuine signed URL must still be refused.
"""

import pytest

from app.agent_effects import _is_signed_url
from app.agent_turn_runner import _contains_sensitive_value

LINK_WITH_PAGE_TEXT = (
    "https://www.linkedin.com/in/alfredo-barreto-jr\n【正文摘录】Alfredo Barreto, Jr. "
    "Are you new to LinkedIn? Join now Sign in with Email or phone Password Show "
    "Forgot password? Sign in By clicking Continue, you agree to the User Agreement."
)


def test_a_link_followed_by_page_text_is_not_a_signed_url():
    assert not _is_signed_url(LINK_WITH_PAGE_TEXT)
    assert not _contains_sensitive_value({"text": LINK_WITH_PAGE_TEXT})


@pytest.mark.parametrize("value", [
    "https://bucket.s3.amazonaws.com/file.pdf?X-Amz-Signature=abcdef0123456789",
    "  https://example.com/download?token=abc123\n",
    "https://example.com/download?token=abc123 here is the file you asked for",
])
def test_a_signed_url_is_still_refused(value):
    assert _is_signed_url(value)
    assert _contains_sensitive_value({"text": value})


@pytest.mark.parametrize("value", ["", "   ", "plain words?password=x", "https://example.com/a/b"])
def test_text_without_a_signed_leading_url_is_not_one(value):
    assert not _is_signed_url(value)
