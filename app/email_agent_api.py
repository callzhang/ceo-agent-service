"""Direct Responses API adapter for read-only email classification."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import time

import httpx

from app.email_classifier_agent import validate_agent_classification_result


class EmailClassifierApiError(RuntimeError):
    """A sanitized, classified failure from the email classifier API."""


class EmailClassifierApiBackend:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        client: httpx.Client | None = None,
        max_retries: int = 2,
        sleeper: Callable[[float], None] = time.sleep,
        recorder: Callable[[Mapping[str, object]], None] | None = None,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        self._url = base_url.rstrip("/") + "/responses"
        self._model = model
        self._api_key = api_key
        self._client = client or httpx.Client()
        self._max_retries = max_retries
        self._sleeper = sleeper
        self._recorder = recorder or (lambda _event: None)

    def classify(
        self,
        *,
        prompt: str,
        task_id: str,
        allowed_category_keys: Sequence[str],
        unsubscribe_candidates: Sequence[str],
    ) -> str:
        payload = {
            "model": self._model,
            "input": prompt,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "AgentClassificationResult",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "category": {"type": ["string", "null"]},
                            "important": {"type": "boolean"},
                            "certainty": {"type": "string", "enum": ["certain", "uncertain"]},
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                            "reason": {"type": "string"},
                            "unsubscribe_candidate_index": {"type": ["integer", "null"], "minimum": 0},
                            "unsubscribe_url": {"type": ["string", "null"]},
                        },
                        "required": [
                            "category", "important", "certainty", "confidence",
                            "reason", "unsubscribe_candidate_index", "unsubscribe_url",
                        ],
                    },
                }
            },
        }
        for attempt in range(1, self._max_retries + 2):
            started = time.monotonic()
            try:
                response = self._client.post(
                    self._url,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=payload,
                )
                usage = _usage(response)
                if response.status_code >= 400:
                    retryable = response.status_code >= 500
                    if retryable and attempt <= self._max_retries:
                        self._event(task_id, attempt, started, usage, "retry", f"http_{response.status_code}")
                        self._sleeper(0.1 * (2 ** (attempt - 1)))
                        continue
                    code = f"http_{response.status_code}"
                    self._event(task_id, attempt, started, usage, "error", code)
                    kind = "retryable" if retryable else "non-retryable"
                    raise EmailClassifierApiError(f"{kind} email classifier API error ({code})")
                try:
                    raw = _response_text(response)
                    validated = validate_agent_classification_result(
                        raw,
                        allowed_category_keys=allowed_category_keys,
                        unsubscribe_candidates=unsubscribe_candidates,
                    )
                except (TypeError, ValueError):
                    self._event(
                        task_id,
                        attempt,
                        started,
                        usage,
                        "error",
                        "invalid_classification",
                    )
                    raise EmailClassifierApiError(
                        "non-retryable email classifier API error (invalid_classification)"
                    ) from None
                self._event(task_id, attempt, started, usage, "success", None)
                return validated.model_dump_json()
            except httpx.RequestError as exc:
                code = "timeout" if isinstance(exc, httpx.TimeoutException) else "request_error"
                if attempt <= self._max_retries:
                    self._event(task_id, attempt, started, {}, "retry", code)
                    self._sleeper(0.1 * (2 ** (attempt - 1)))
                    continue
                self._event(task_id, attempt, started, {}, "error", code)
                raise EmailClassifierApiError(f"retryable email classifier API error ({code})") from None
            except ValueError:
                self._event(task_id, attempt, started, {}, "error", "invalid_classification")
                raise

        raise AssertionError("unreachable")

    def _event(
        self,
        task_id: str,
        attempt: int,
        started: float,
        usage: Mapping[str, object],
        request_status: str,
        error_code: str | None,
    ) -> None:
        health_status = {
            "success": "ready",
            "retry": "degraded",
            "error": "unavailable",
        }[request_status]
        event = {
            "task_id": task_id,
            "model": self._model,
            "attempt": attempt,
            "latency_ms": round((time.monotonic() - started) * 1000),
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "status": health_status,
            "request_status": request_status,
            "error_code": error_code,
        }
        self._recorder(event)


def _response_text(response: httpx.Response) -> str:
    body = response.json()
    if not isinstance(body, Mapping):
        raise ValueError("Responses API returned an invalid object")
    output_text = body.get("output_text")
    if isinstance(output_text, str):
        return output_text
    if output_text is not None:
        raise ValueError("Responses API returned invalid output text")
    output = body.get("output")
    if not isinstance(output, list):
        raise ValueError("Responses API returned invalid output")
    for item in output:
        if not isinstance(item, Mapping):
            raise ValueError("Responses API returned invalid output item")
        content = item.get("content")
        if not isinstance(content, list):
            raise ValueError("Responses API returned invalid content")
        for content_item in content:
            if not isinstance(content_item, Mapping):
                raise ValueError("Responses API returned invalid content item")
            text = content_item.get("text")
            if isinstance(text, str):
                return text
            if text is not None:
                raise ValueError("Responses API returned invalid content text")
    raise ValueError("Responses API returned no output text")


def _usage(response: httpx.Response) -> Mapping[str, object]:
    try:
        body = response.json()
    except (ValueError, TypeError):
        return {}
    if not isinstance(body, Mapping):
        return {}
    usage = body.get("usage")
    if not isinstance(usage, Mapping):
        return {}
    return {
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
    }
