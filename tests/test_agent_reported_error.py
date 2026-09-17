import pytest

from app.agent_reported_error import AGENT_REPORTED_FAILURE, agent_error_payload


def test_a_known_code_gets_the_service_policy_whatever_the_turn_said():
    payload = agent_error_payload("authorization_required", failed=True)

    assert payload["code"] == "authorization_required"
    assert payload["retryable"] is False
    assert payload["authorization_required"] is True


def test_a_known_code_in_another_spelling_is_the_same_code():
    """Seen live: AUTHORIZATION_REQUIRED beside authorization_required."""
    payload = agent_error_payload("AUTHORIZATION_REQUIRED", failed=True)

    assert payload["code"] == "authorization_required"
    assert payload["source_code"] == "AUTHORIZATION_REQUIRED"


def test_an_invented_failure_code_takes_the_bounded_retry_and_is_kept_for_diagnosis():
    """Seen live: one missing tool reported as xiaoqing_mcp_not_injected."""
    payload = agent_error_payload("xiaoqing_mcp_not_injected", failed=True)

    assert payload == {
        "code": AGENT_REPORTED_FAILURE,
        "retryable": True,
        "authorization_required": False,
        "source": "agent",
        "source_code": "xiaoqing_mcp_not_injected",
    }


@pytest.mark.parametrize(
    "code",
    ["runtime_provider_unreachable", "codex_provider_overloaded", "agent_run_unavailable"],
)
def test_a_turn_cannot_write_a_code_only_the_service_observes(code):
    assert agent_error_payload(code, failed=True)["code"] == AGENT_REPORTED_FAILURE


def test_a_browser_code_follows_what_the_service_knows_a_rerun_can_clear():
    assert agent_error_payload("email_unsubscribe_browser_timeout", failed=True)["retryable"] is True
    assert agent_error_payload("email_unsubscribe_page_state_unknown", failed=True)["retryable"] is False


def test_a_reason_on_an_outcome_that_is_not_a_failure_drives_nothing():
    payload = agent_error_payload("CONFIRMED_FACT_MISSING", failed=False)

    assert payload["code"] == "confirmed_fact_missing"
    assert payload["retryable"] is False
    assert payload["authorization_required"] is False


def test_no_reported_code_is_no_error():
    assert agent_error_payload(None, failed=True) == {
        "code": "",
        "retryable": False,
        "authorization_required": False,
    }


def test_a_provider_duplicate_does_not_retry_into_the_same_suppression():
    """Reply task 135612 failed four runs in a row on this exact loop.

    DingTalk suppressed the repeated notification as a duplicate, no receipt
    came back, and the bounded retry sent the turn straight back into the
    same suppression.
    """
    payload = agent_error_payload("PROVIDER_DUPLICATE_NO_READBACK", failed=True)

    assert payload["code"] == "provider_duplicate_no_readback"
    assert payload["retryable"] is False
    assert payload["authorization_required"] is False
    assert payload["source_code"] == "PROVIDER_DUPLICATE_NO_READBACK"
