import pytest

from app.agent_runtime_config import load_runtime_config
from app.friday_runtime_adapter import (
    FridayExecutionResult,
    FridayHttpResponse,
    FridayRuntimeAdapter,
    FridayRuntimeError,
)

from app.friday_runtime_contract import (
    FridayExecutionInput,
    FridayOperationStatus,
    FridayRuntimeContract,
    FridayRuntimeContractError,
)


def test_contract_requires_thread_turn_operation_and_final_artifact():
    contract = FridayRuntimeContract.from_documented_api()

    assert contract.create_thread_path == "/v1/threads"
    assert contract.send_message_path("thread-1") == "/v1/threads/thread-1/turns"
    assert contract.run_turn_path("turn-1") == "/v1/turns/turn-1/runs"
    assert contract.operation_path("op-1") == "/v1/operations/op-1"
    assert contract.artifacts_path() == "/v1/artifacts"
    assert contract.final_artifact_field == "artifact"


def test_contract_extracts_thread_id_from_thread_response():
    contract = FridayRuntimeContract.from_documented_api()

    assert contract.thread_id_from_create_response(
        {"thread": {"thread_id": "thread-1"}}
    ) == "thread-1"


def test_contract_requires_project_and_maps_execution_input():
    contract = FridayRuntimeContract.from_documented_api()
    execution = FridayExecutionInput(
        project_id="ceo-project", prompt="produce result", auth_disabled=True
    )

    assert contract.create_thread_payload(execution)["project_id"] == "ceo-project"
    with pytest.raises(FridayRuntimeContractError, match="project_id"):
        FridayExecutionInput(project_id="", prompt="x", auth_disabled=True)


def test_contract_supports_runtime_ticket_or_session_token_auth():
    contract = FridayRuntimeContract.from_documented_api()

    assert contract.authentication_headers(runtime_ticket="ticket") == {
        "Authorization": "Bearer ticket"
    }
    assert contract.authentication_headers(friday_session_token="session") == {
        "X-Friday-Session-Token": "session"
    }
    assert contract.authentication_headers(auth_disabled=True) == {}

    with pytest.raises(FridayRuntimeContractError, match="auth"):
        contract.authentication_headers(runtime_ticket="   ")


@pytest.mark.parametrize(
    ("status", "terminal"),
    [
        ("pending", False),
        ("running", False),
        ("completed", True),
        ("failed", True),
    ],
)
def test_contract_classifies_operation_status(status, terminal):
    contract = FridayRuntimeContract.from_documented_api()
    payload = {
        "result": "success",
        "data": {"operation": {"status": status}},
    }

    actual = contract.operation_status(payload)
    assert actual == FridayOperationStatus(status)
    assert contract.is_terminal_operation_status(actual) is terminal


def test_contract_selects_nonempty_final_artifact_for_thread():
    contract = FridayRuntimeContract.from_documented_api()
    payload = {
        "result": "success",
        "data": {
            "items": [
                {"thread_id": "other", "final_message": "wrong"},
                {"thread_id": "thread-1", "final_message": "final result"},
            ]
        },
    }

    assert contract.select_final_artifact(payload, thread_id="thread-1")[
        "final_message"
    ] == "final result"


def test_contract_rejects_missing_final_artifact():
    contract = FridayRuntimeContract.from_documented_api()
    payload = {"result": "success", "data": {"items": []}}

    with pytest.raises(FridayRuntimeContractError, match="artifact"):
        contract.select_final_artifact(payload, thread_id="thread-1")


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, path, *, headers, body=None, timeout_seconds):
        self.calls.append(
            type(
                "Call",
                (),
                {
                    "method": method,
                    "path": path,
                    "headers": dict(headers),
                    "body": body,
                    "timeout_seconds": timeout_seconds,
                },
            )
        )
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


@pytest.fixture
def runtime_config():
    return load_runtime_config(
        {
            "CEO_AGENT_RUNTIME_ROUTES": "friday_runtime",
            "CEO_FRIDAY_RUNTIME_PROJECT_ID": "ceo-project",
            "CEO_FRIDAY_RUNTIME_AUTH_DISABLED": "1",
            "CEO_FRIDAY_RUNTIME_MODEL": "MiniMax-M3",
        }
    )


def _success(data):
    return {"result": "success", "code": 200, "message": None, "data": data}


def test_execute_creates_thread_sends_message_polls_and_returns_artifact(runtime_config):
    transport = FakeTransport(
        [
            _success({"thread": {"thread_id": "thread-1"}}),
            _success(
                {
                    "thread": {"thread_id": "thread-1"},
                    "turn": {"turn_id": "turn-1"},
                    "operation": {
                        "operation_id": "op-1",
                        "request_payload": {"turn_id": "turn-1"},
                    },
                }
            ),
            _success({"operation": {"status": "running"}}),
            _success({"operation": {"status": "completed"}}),
            _success(
                {
                    "items": [
                        {"thread_id": "thread-1", "final_message": '{"ok":true}'}
                    ]
                }
            ),
        ]
    )

    result = FridayRuntimeAdapter(
        runtime_config, transport=transport, poll_interval_seconds=0
    ).execute("produce result", project_id="explicit-project", timeout_seconds=10)

    assert isinstance(result, FridayExecutionResult)
    assert result.text == '{"ok":true}'
    assert (result.thread_id, result.turn_id, result.operation_id) == (
        "thread-1",
        "turn-1",
        "op-1",
    )
    assert [call.path for call in transport.calls] == [
        "/v1/threads",
        "/v1/threads/thread-1/turns",
        "/v1/operations/op-1",
        "/v1/operations/op-1",
        "/v1/artifacts?thread_id=thread-1",
    ]
    assert transport.calls[0].body["project_id"] == "explicit-project"
    assert transport.calls[1].body["message"]["text"] == "produce result"
    assert all(call.headers == {} for call in transport.calls)


@pytest.mark.parametrize(
    ("status_code", "code"),
    [(401, "friday_runtime_auth_failed"), (403, "friday_runtime_auth_failed")],
)
def test_execute_maps_auth_http_errors(runtime_config, status_code, code):
    transport = FakeTransport([(status_code, {"message": "bad credentials"})])

    with pytest.raises(FridayRuntimeError) as raised:
        FridayRuntimeAdapter(runtime_config, transport=transport).execute(
            "prompt", project_id="ceo-project"
        )

    assert raised.value.code == code
    assert raised.value.retryable is False
    assert "bad credentials" not in str(raised.value)


def test_execute_never_persists_provider_token_from_error_body():
    token = "runtime-ticket-secret"
    config = load_runtime_config(
        {
            "CEO_AGENT_RUNTIME_ROUTES": "friday_runtime",
            "CEO_FRIDAY_RUNTIME_PROJECT_ID": "ceo-project",
            "CEO_FRIDAY_RUNTIME_TICKET": token,
        }
    )
    transport = FakeTransport(
        [(500, {"message": f"provider rejected bearer token {token}"})]
    )

    with pytest.raises(FridayRuntimeError) as raised:
        FridayRuntimeAdapter(config, transport=transport).execute(
            "prompt", project_id="ceo-project"
        )

    assert token not in str(raised.value)
    assert raised.value.detail == "Friday Runtime request failed"


def test_execute_maps_transport_error_to_unreachable(runtime_config):
    transport = FakeTransport([TimeoutError("secret-ticket must not leak")])

    with pytest.raises(FridayRuntimeError) as raised:
        FridayRuntimeAdapter(runtime_config, transport=transport).execute(
            "prompt", project_id="ceo-project"
        )

    assert raised.value.code == "friday_runtime_unreachable"
    assert raised.value.retryable is True
    assert "secret-ticket" not in str(raised.value)


def test_execute_maps_malformed_json_shape_to_result_invalid(runtime_config):
    transport = FakeTransport([FridayHttpResponse(200, ["not", "an", "object"])])

    with pytest.raises(FridayRuntimeError) as raised:
        FridayRuntimeAdapter(runtime_config, transport=transport).execute(
            "prompt", project_id="ceo-project"
        )

    assert raised.value.code == "friday_runtime_result_invalid"
    assert raised.value.retryable is False


def test_execute_maps_terminal_operation_failure(runtime_config):
    transport = FakeTransport(
        [
            _success({"thread": {"thread_id": "thread-1"}}),
            _success(
                {
                    "operation": {
                        "operation_id": "op-1",
                        "request_payload": {"turn_id": "turn-1"},
                    }
                }
            ),
            _success(
                {
                    "operation": {
                        "status": "failed",
                        "last_error": "provider failed",
                    }
                }
            ),
        ]
    )

    with pytest.raises(FridayRuntimeError) as raised:
        FridayRuntimeAdapter(runtime_config, transport=transport).execute(
            "prompt", project_id="ceo-project"
        )

    assert raised.value.code == "friday_runtime_failed"
    assert raised.value.retryable is True
    assert raised.value.detail == "provider failed"
    assert raised.value.thread_id == "thread-1"
    assert raised.value.operation_id == "op-1"
    assert raised.value.turn_id == "turn-1"


def test_execute_timeout_is_bounded_and_does_not_create_extra_run(runtime_config):
    transport = FakeTransport(
        [
            _success({"thread": {"thread_id": "thread-1"}}),
            _success(
                {
                    "operation": {
                        "operation_id": "op-1",
                        "request_payload": {"turn_id": "turn-1"},
                    }
                }
            ),
            _success({"operation": {"status": "running"}}),
        ]
    )
    adapter = FridayRuntimeAdapter(
        runtime_config, transport=transport, poll_interval_seconds=0.02
    )

    with pytest.raises(FridayRuntimeError) as raised:
        adapter.execute("prompt", project_id="ceo-project", timeout_seconds=0.01)

    assert raised.value.code == "friday_runtime_unreachable"
    assert [call.method for call in transport.calls] == ["POST", "POST", "GET"]


def test_execute_rejects_missing_project_id_before_transport(runtime_config):
    transport = FakeTransport([])

    with pytest.raises(FridayRuntimeError) as raised:
        FridayRuntimeAdapter(runtime_config, transport=transport).execute("prompt")

    assert raised.value.code == "friday_runtime_result_invalid"
    assert transport.calls == []


def test_execute_propagates_runtime_ticket_on_every_request():
    config = load_runtime_config(
        {
            "CEO_AGENT_RUNTIME_ROUTES": "friday_runtime",
            "CEO_FRIDAY_RUNTIME_PROJECT_ID": "unused-default",
            "CEO_FRIDAY_RUNTIME_TICKET": "runtime-ticket",
        }
    )
    transport = FakeTransport(
        [
            _success({"thread": {"thread_id": "thread-1"}}),
            _success(
                {
                    "operation": {
                        "operation_id": "op-1",
                        "request_payload": {"turn_id": "turn-1"},
                    }
                }
            ),
            _success({"operation": {"status": "completed"}}),
            _success(
                {"items": [{"thread_id": "thread-1", "final_message": "done"}]}
            ),
        ]
    )

    FridayRuntimeAdapter(config, transport=transport, poll_interval_seconds=0).execute(
        "prompt", project_id="explicit-project"
    )

    assert [call.headers for call in transport.calls] == [
        {"Authorization": "Bearer runtime-ticket"}
    ] * 4


def test_execute_propagates_session_token_without_converting_to_bearer():
    config = load_runtime_config(
        {
            "CEO_AGENT_RUNTIME_ROUTES": "friday_runtime",
            "CEO_FRIDAY_RUNTIME_PROJECT_ID": "unused-default",
            "CEO_FRIDAY_SESSION_TOKEN": "session-token",
        }
    )
    transport = FakeTransport(
        [
            _success({"thread": {"thread_id": "thread-1"}}),
            _success(
                {
                    "operation": {
                        "operation_id": "op-1",
                        "request_payload": {"turn_id": "turn-1"},
                    }
                }
            ),
            _success({"operation": {"status": "completed"}}),
            _success(
                {"items": [{"thread_id": "thread-1", "final_message": "done"}]}
            ),
        ]
    )

    FridayRuntimeAdapter(config, transport=transport, poll_interval_seconds=0).execute(
        "prompt", project_id="explicit-project"
    )

    assert [call.headers for call in transport.calls] == [
        {"X-Friday-Session-Token": "session-token"}
    ] * 4
