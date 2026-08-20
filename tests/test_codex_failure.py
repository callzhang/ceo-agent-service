from app.codex_failure import is_codex_provider_auth_error


def test_invalid_api_key_is_bound_to_codex_responses_endpoint():
    assert is_codex_provider_auth_error(
        "Incorrect API key provided; code=invalid_api_key; /v1/responses"
    )
    assert not is_codex_provider_auth_error(
        "Incorrect API key provided; code=invalid_api_key; /v1/embeddings"
    )
