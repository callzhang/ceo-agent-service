from urllib.parse import urlencode

import pytest

from app.feedback_spike import sanitize_source_feedback_links


def _source(token, host="https://feedback.example.com"):
    return "Historical source\n" + " | ".join(
        f"[{rating}]({host}/api/dingtalk-feedback-spike?" +
        urlencode({"feedback_token": token, "rating": rating}) + ")"
        for rating in ("up", "down")
    )


def test_source_callback_identity_is_opaque_not_a_new_outgoing_token():
    source = _source("historical-source-identity")
    inspected = sanitize_source_feedback_links(source,
        vercel_base_url="https://feedback.example.com")
    assert "Historical source" in inspected
    assert "historical-source-identity" not in inspected


def test_source_callback_does_not_trust_foreign_origin_or_unpaired_identity():
    for source in (_source("historical", host="https://foreign.example.com"),
                   _source("historical").replace("feedback_token=historical&rating=down",
                                              "feedback_token=other&rating=down")):
        with pytest.raises(ValueError, match="feedback_callback_pair_invalid"):
            sanitize_source_feedback_links(source,
                vercel_base_url="https://feedback.example.com")
