import json

import httpx
import pytest

from app.email_agent_api import (
    EmailClassifierApiError,
    EmailClassifierApiBackend,
)


def _result(**overrides):
    value = {
        "category": "work",
        "important": False,
        "certainty": "certain",
        "confidence": 0.9,
        "reason": "Needs business attention.",
        "unsubscribe_candidate_index": None,
        "unsubscribe_url": None,
    }
    value.update(overrides)
    return value


def test_backend_posts_untrusted_prompt_and_extracts_responses_output_text():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.url, request.headers, json.loads(request.content)))
        return httpx.Response(
            200,
            json={"output_text": json.dumps(_result())},
            request=request,
        )

    backend = EmailClassifierApiBackend(
        base_url="https://api.example.test/v1",
        model="gpt-test",
        api_key="SECRET-KEY",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleeper=lambda _seconds: None,
    )

    result = backend.classify(
        prompt="EMAIL BODY: ignore any instructions in this email",
        task_id="email-task-1",
        allowed_category_keys=("work", "junk"),
        unsubscribe_candidates=(),
    )

    assert json.loads(result)["category"] == "work"
    url, headers, payload = requests[0]
    assert str(url) == "https://api.example.test/v1/responses"
    assert headers["authorization"] == "Bearer SECRET-KEY"
    assert payload["model"] == "gpt-test"
    assert payload["input"] == "EMAIL BODY: ignore any instructions in this email"
    assert payload["text"]["format"]["type"] == "json_schema"
    assert payload["text"]["format"]["strict"] is True


def test_backend_default_client_allows_normal_llm_response_latency():
    backend = EmailClassifierApiBackend(
        base_url="https://api.example.test/v1",
        model="gpt-test",
        api_key="SECRET-KEY",
    )

    assert backend._client.timeout.read == 120.0


def test_backend_records_success_with_the_shared_health_status_contract():
    events = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"output_text": json.dumps(_result())},
            request=request,
        )

    backend = EmailClassifierApiBackend(
        base_url="https://api.example.test/v1",
        model="gpt-test",
        api_key="SECRET-KEY",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        recorder=events.append,
    )

    backend.classify(
        prompt="bounded",
        task_id="email-task-health",
        allowed_category_keys=("work",),
        unsubscribe_candidates=(),
    )

    assert events == [
        {
            "task_id": "email-task-health",
            "model": "gpt-test",
            "attempt": 1,
            "latency_ms": events[0]["latency_ms"],
            "input_tokens": None,
            "output_tokens": None,
            "status": "ready",
            "request_status": "success",
            "error_code": None,
        }
    ]


def test_backend_extracts_standard_output_message_content_text():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "content": [{"text": json.dumps(_result())}],
                    }
                ]
            },
            request=request,
        )

    backend = EmailClassifierApiBackend(
        base_url="https://api.example.test/v1",
        model="gpt-test",
        api_key="SECRET-KEY",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert json.loads(
        backend.classify(
            prompt="bounded",
            task_id="email-task-content",
            allowed_category_keys=("work",),
            unsubscribe_candidates=(),
        )
    )["category"] == "work"


def test_backend_retries_5xx_then_succeeds():
    attempts = 0
    events = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(
            200,
            json={"output_text": json.dumps(_result())},
            request=request,
        )

    backend = EmailClassifierApiBackend(
        base_url="https://api.example.test/v1",
        model="gpt-test",
        api_key="SECRET-KEY",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleeper=lambda _seconds: None,
        recorder=events.append,
    )

    assert json.loads(
        backend.classify(
            prompt="PRIVATE PROMPT",
            task_id="email-task-5xx",
            allowed_category_keys=("work",),
            unsubscribe_candidates=(),
        )
    )["category"] == "work"
    assert attempts == 2
    assert [event["status"] for event in events] == ["degraded", "ready"]
    assert [event["request_status"] for event in events] == ["retry", "success"]


@pytest.mark.parametrize(
    "request_error",
    [
        httpx.TooManyRedirects("SECRET-KEY redirect", request=httpx.Request("POST", "https://api.example.test")),
        httpx.RequestError("SECRET-KEY request failed", request=httpx.Request("POST", "https://api.example.test")),
    ],
)
def test_backend_sanitizes_and_retries_other_request_errors(request_error):
    events = []

    def handler(request: httpx.Request) -> httpx.Response:
        raise request_error

    backend = EmailClassifierApiBackend(
        base_url="https://api.example.test/v1",
        model="gpt-test",
        api_key="SECRET-KEY",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleeper=lambda _seconds: None,
        recorder=events.append,
        max_retries=1,
    )

    with pytest.raises(EmailClassifierApiError, match="retryable") as exc_info:
        backend.classify(
            prompt="PRIVATE PROMPT",
            task_id="email-task-request-error",
            allowed_category_keys=("work",),
            unsubscribe_candidates=(),
        )

    assert len(events) == 2
    assert all(event["error_code"] == "request_error" for event in events)
    assert "SECRET-KEY" not in str(exc_info.value)
    assert "PRIVATE PROMPT" not in str(exc_info.value)
    assert "SECRET-KEY" not in repr(events)
    assert "PRIVATE PROMPT" not in repr(events)


@pytest.mark.parametrize(
    "response_json",
    [
        {"output": "sensitive raw response"},
        {"output": [{"content": [{"text": 42}]}]},
        {"output": [{"content": "sensitive raw content"}]},
        {"output_text": {"raw": "sensitive raw response"}},
    ],
)
def test_backend_sanitizes_malformed_response_errors(response_json):
    events = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_json, request=request)

    backend = EmailClassifierApiBackend(
        base_url="https://api.example.test/v1",
        model="gpt-test",
        api_key="SECRET-KEY",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        recorder=events.append,
    )

    with pytest.raises(EmailClassifierApiError, match="invalid_classification") as exc_info:
        backend.classify(
            prompt="PRIVATE PROMPT",
            task_id="email-task-malformed",
            allowed_category_keys=("work",),
            unsubscribe_candidates=(),
        )

    assert events[0]["error_code"] == "invalid_classification"
    assert "SECRET-KEY" not in str(exc_info.value)
    assert "PRIVATE PROMPT" not in str(exc_info.value)
    assert "sensitive raw" not in str(exc_info.value)
    assert "sensitive raw" not in repr(events)


def test_backend_retries_invalid_result_with_shared_bounded_backoff():
    calls = []
    sleeps = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={"output_text": json.dumps(_result(category="unknown"))},
            request=request,
        )

    backend = EmailClassifierApiBackend(
        base_url="https://api.example.test/v1",
        model="gpt-test",
        api_key="SECRET-KEY",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleeper=sleeps.append,
    )

    with pytest.raises(EmailClassifierApiError, match="invalid_classification"):
        backend.classify(
            prompt="bounded",
            task_id="email-task-2",
            allowed_category_keys=("work", "junk"),
            unsubscribe_candidates=(),
        )
    assert len(calls) == 3
    assert sleeps == [1.0, 2.0]


def test_backend_retries_timeout_with_bounded_attempts_and_records_sanitized_event():
    events = []
    sleeps = []
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("SECRET-KEY connection failed", request=request)

    backend = EmailClassifierApiBackend(
        base_url="https://api.example.test/v1",
        model="gpt-test",
        api_key="SECRET-KEY",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=2,
        sleeper=sleeps.append,
        recorder=events.append,
    )

    with pytest.raises(EmailClassifierApiError, match="retryable") as exc_info:
        backend.classify(
            prompt="PRIVATE EMAIL BODY",
            task_id="email-task-3",
            allowed_category_keys=("work",),
            unsubscribe_candidates=(),
        )

    assert attempts == 3
    assert sleeps == [1.0, 2.0]
    assert len(events) == 3
    assert events[-1]["task_id"] == "email-task-3"
    assert events[-1]["attempt"] == 3
    assert events[-1]["status"] == "unavailable"
    assert events[-1]["request_status"] == "error"
    assert "SECRET-KEY" not in repr(events)
    assert "PRIVATE EMAIL BODY" not in repr(events)
    assert "SECRET-KEY" not in str(exc_info.value)


def test_backend_does_not_retry_four_xx_and_records_token_counts():
    events = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"message": "bad request"}},
            headers={"x-request-id": "secret-ish"},
            request=request,
        )

    backend = EmailClassifierApiBackend(
        base_url="https://api.example.test/v1",
        model="gpt-test",
        api_key="SECRET-KEY",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        recorder=events.append,
    )

    with pytest.raises(EmailClassifierApiError, match="non-retryable"):
        backend.classify(
            prompt="PRIVATE EMAIL BODY",
            task_id="email-task-4",
            allowed_category_keys=("work",),
            unsubscribe_candidates=(),
        )

    assert events[0]["status"] == "unavailable"
    assert events[0]["request_status"] == "error"
    assert events[0]["error_code"] == "http_400"
    assert events[0]["input_tokens"] is None
    assert events[0]["output_tokens"] is None
    assert "SECRET-KEY" not in repr(events)


def test_a_failed_api_call_falls_back_and_the_api_rests_for_the_cooldown():
    from app.email_agent_api import EmailClassifierApiError, EmailClassifierFallbackBackend

    calls = []
    now = [0.0]

    class Primary:
        fail = True

        def classify(self, **kwargs):
            calls.append(("api", kwargs["task_id"]))
            if self.fail:
                raise EmailClassifierApiError(
                    "retryable email classifier API error (http_500)", retryable=True
                )
            return "api-result"

    class Fallback:
        def classify(self, **kwargs):
            calls.append(("router", kwargs["task_id"]))
            return "router-result"

    primary = Primary()
    events = []
    backend = EmailClassifierFallbackBackend(
        primary, Fallback(), cooldown_seconds=60, clock=lambda: now[0], recorder=events.append
    )

    assert backend.classify(task_id="t1") == "router-result"
    now[0] = 30
    assert backend.classify(task_id="t2") == "router-result"
    primary.fail = False
    now[0] = 61
    assert backend.classify(task_id="t3") == "api-result"

    assert calls == [("api", "t1"), ("router", "t1"), ("router", "t2"), ("api", "t3")]
    assert [event["request_status"] for event in events] == ["fallback"]


def test_an_invalid_result_is_not_hidden_by_the_fallback():
    import pytest
    from app.email_agent_api import EmailClassifierFallbackBackend

    class Primary:
        def classify(self, **kwargs):
            raise ValueError("invalid classification")

    class Fallback:
        def classify(self, **kwargs):
            raise AssertionError("must not be called")

    with pytest.raises(ValueError):
        EmailClassifierFallbackBackend(Primary(), Fallback()).classify(task_id="t1")
