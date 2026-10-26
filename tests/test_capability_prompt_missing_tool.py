from app.consumer_agent import AGENT_CAPABILITY_INSTRUCTIONS, OA_AGENT_CAPABILITY_INSTRUCTIONS


def test_a_missing_tool_is_not_a_failure_by_itself():
    # Derek 2026-10-25: without a tool the task may fail, or may continue from
    # the information already available; the prompt must not make it a failure.
    for text in (AGENT_CAPABILITY_INSTRUCTIONS, OA_AGENT_CAPABILITY_INSTRUCTIONS):
        flat = " ".join(text.split())
        assert "decide whether the task can still be completed from the information already available" in flat
        assert "do not fail" in flat
        assert "only when the missing operation is itself the required effect" in flat
        assert "return failed with error_code `operation_unavailable`. A declared" not in flat
