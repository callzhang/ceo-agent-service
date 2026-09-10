from app.codex_failure import (
    CODEX_PROVIDER_OVERLOADED,
    classify_codex_process_failure,
    is_codex_provider_auth_error,
)


def test_invalid_api_key_is_bound_to_codex_responses_endpoint():
    assert is_codex_provider_auth_error(
        "Incorrect API key provided; code=invalid_api_key; /v1/responses"
    )
    assert not is_codex_provider_auth_error(
        "Incorrect API key provided; code=invalid_api_key; /v1/embeddings"
    )


def test_model_at_capacity_is_classified_as_provider_overloaded():
    assert (
        classify_codex_process_failure(
            "Selected model is at capacity. Please try a different model.", ""
        )
        == CODEX_PROVIDER_OVERLOADED
    )
    assert classify_codex_process_failure("", "stream error") != CODEX_PROVIDER_OVERLOADED


def test_high_demand_wrapper_is_classified_as_provider_overloaded():
    # Codex hides the provider body (429 rate limit, exhausted token plan,
    # upstream overload) behind this generic message.
    assert (
        classify_codex_process_failure(
            '{"type":"task_complete","error":{"message":"We\'re currently '
            'experiencing high demand, which may cause temporary errors."}}',
            "",
        )
        == CODEX_PROVIDER_OVERLOADED
    )
