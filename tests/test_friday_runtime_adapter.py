import pytest

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
