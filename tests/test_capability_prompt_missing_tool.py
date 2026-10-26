from app.consumer_agent import AGENT_CAPABILITY_INSTRUCTIONS, OA_AGENT_CAPABILITY_INSTRUCTIONS


def test_a_missing_tool_is_not_a_failure_by_itself():
    # If the requested effect is unavailable, a truthful helpful reply is still
    # a valid task outcome and must use the normal reviewed message flow.
    for text in (AGENT_CAPABILITY_INSTRUCTIONS, OA_AGENT_CAPABILITY_INSTRUCTIONS):
        flat = " ".join(text.split())
        assert "decide whether the task can still be completed from the information already available" in flat
        assert "neither the requested effect nor a useful, truthful reply can be completed" in flat
        assert "return failed with error_code `operation_unavailable`. A declared" not in flat
        assert "reply to the requester" in flat
        assert "Explain what could not be done" in flat
        assert "never claim the unavailable operation succeeded" in flat
