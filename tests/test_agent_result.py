import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.agent_result import (
    AgentError,
    AgentOutcome,
    AgentResult,
    EffectEventStatus,
    EffectKind,
    ExecutionReceipt,
    InconsistentAgentResultError,
    ResultParseError,
    SideEffectState,
    ToolEffectEvent,
    parse_agent_result,
    validate_completion_evidence,
)


SCHEMA_PATH = Path(__file__).parents[1] / "app" / "schemas" / "agent_result.schema.json"


def _result_json(
    *,
    outcome: str = "completed",
    summary: str = "work completed",
    side_effect_state: str = "none",
) -> str:
    return json.dumps(
        {
            "outcome": outcome,
            "summary": summary,
            "error": {
                "code": "",
                "retryable": False,
                "authorization_required": False,
                "side_effect_state": side_effect_state,
            },
        }
    )


def _completed_confirmed(*, summary: str = "work completed") -> AgentResult:
    return AgentResult(
        outcome=AgentOutcome.COMPLETED,
        summary=summary,
        error=AgentError(side_effect_state=SideEffectState.CONFIRMED),
    )


def test_parse_agent_result_from_last_agent_message():
    raw = json.dumps(
        {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": _result_json(summary="latest result"),
            },
        }
    )

    result = parse_agent_result(raw)

    assert result.outcome is AgentOutcome.COMPLETED
    assert result.summary == "latest result"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"message": _result_json(summary="message result")}, "message result"),
        (
            {"last_agent_message": _result_json(summary="task result")},
            "task result",
        ),
    ],
)
def test_parse_agent_result_accepts_message_fields(payload, expected):
    assert parse_agent_result(json.dumps(payload)).summary == expected


def test_agent_result_forbids_extra_fields_at_every_model_level():
    with pytest.raises(ValidationError):
        AgentResult.model_validate_json(
            json.dumps(
                {
                    "outcome": "completed",
                    "summary": "done",
                    "unexpected": True,
                }
            )
        )

    with pytest.raises(ValidationError):
        AgentResult.model_validate_json(
            json.dumps(
                {
                    "outcome": "completed",
                    "summary": "done",
                    "error": {"unexpected": True},
                }
            )
        )


@pytest.mark.parametrize(
    ("path", "wrong_value"),
    [
        (("outcome",), 1),
        (("summary",), 123),
        (("error", "retryable"), "false"),
        (("error", "authorization_required"), 0),
        (("error", "side_effect_state"), False),
    ],
)
def test_agent_result_rejects_wrong_runtime_types(path, wrong_value):
    payload = json.loads(_result_json())
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = wrong_value

    with pytest.raises(ResultParseError):
        parse_agent_result(json.dumps({"message": json.dumps(payload)}))


@pytest.mark.parametrize("raw", ["not json", json.dumps({"message": "not json"})])
def test_parse_agent_result_rejects_malformed_or_missing_json(raw):
    with pytest.raises(ResultParseError):
        parse_agent_result(raw)


def test_parse_agent_result_strips_markdown_json_fence():
    raw = json.dumps(
        {"message": f"```json\n{_result_json(summary='fenced')}\n```"}
    )

    assert parse_agent_result(raw).summary == "fenced"


def test_parse_agent_result_extracts_first_balanced_object_with_escaped_braces():
    result_json = _result_json(summary='kept string: "}" and { brace')
    raw = json.dumps({"message": f"Result follows. {result_json} trailing text {{"})

    assert parse_agent_result(raw).summary == 'kept string: "}" and { brace'


def test_parse_agent_result_selects_latest_jsonl_agent_result():
    raw = "\n".join(
        [
            json.dumps({"message": _result_json(summary="old")}),
            json.dumps({"message": _result_json(summary="new")}),
        ]
    )

    assert parse_agent_result(raw).summary == "new"


def test_parse_agent_result_rejects_latest_malformed_candidate():
    raw = "\n".join(
        [
            json.dumps({"message": _result_json(summary="old valid")}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "not json"},
                }
            ),
        ]
    )

    with pytest.raises(ResultParseError):
        parse_agent_result(raw)


def test_parse_agent_result_skips_later_non_agent_message_event():
    raw = "\n".join(
        [
            json.dumps({"message": _result_json(summary="agent result")}),
            json.dumps({"type": "error", "message": "transport failed"}),
        ]
    )

    assert parse_agent_result(raw).summary == "agent result"


def test_committed_schema_matches_model_and_forbids_all_object_extras():
    schema = json.loads(SCHEMA_PATH.read_text())

    assert schema == AgentResult.model_json_schema()
    object_nodes = []

    def collect(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                object_nodes.append(node)
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)

    collect(schema)
    assert object_nodes
    assert all(node.get("additionalProperties") is False for node in object_nodes)


def test_completed_confirmed_with_no_evidence_is_rejected():
    with pytest.raises(InconsistentAgentResultError):
        validate_completion_evidence(_completed_confirmed(), events=[])


def test_read_only_completed_event_does_not_confirm_completion():
    event = ToolEffectEvent(
        event_id="read-1",
        effect=EffectKind.READ_ONLY,
        status=EffectEventStatus.COMPLETED,
    )

    with pytest.raises(InconsistentAgentResultError):
        validate_completion_evidence(_completed_confirmed(), events=[event])


def test_completed_effectful_event_confirms_completion():
    event = ToolEffectEvent(
        event_id="write-1",
        effect=EffectKind.EFFECTFUL,
        status=EffectEventStatus.COMPLETED,
    )

    assert (
        validate_completion_evidence(_completed_confirmed(), events=[event])
        is SideEffectState.CONFIRMED
    )


def test_safe_persisted_completed_receipt_confirms_completion():
    receipt = ExecutionReceipt(
        receipt_id="receipt-1",
        operation_id="write-1",
        completed=True,
        persisted=True,
        safe_to_confirm=True,
    )

    assert (
        validate_completion_evidence(
            _completed_confirmed(), events=[], receipts=[receipt]
        )
        is SideEffectState.CONFIRMED
    )


def test_effectful_started_without_completion_cannot_confirm_completion():
    event = ToolEffectEvent(
        event_id="write-1",
        effect=EffectKind.EFFECTFUL,
        status=EffectEventStatus.STARTED,
    )

    with pytest.raises(InconsistentAgentResultError) as exc_info:
        validate_completion_evidence(_completed_confirmed(), events=[event])

    assert exc_info.value.evidence_state is SideEffectState.UNKNOWN


def test_completed_different_operation_does_not_mask_incomplete_effect():
    events = [
        ToolEffectEvent(
            event_id="write-incomplete",
            effect=EffectKind.EFFECTFUL,
            status=EffectEventStatus.STARTED,
        ),
        ToolEffectEvent(
            event_id="write-complete",
            effect=EffectKind.EFFECTFUL,
            status=EffectEventStatus.COMPLETED,
        ),
    ]

    with pytest.raises(InconsistentAgentResultError) as exc_info:
        validate_completion_evidence(_completed_confirmed(), events=events)

    assert exc_info.value.evidence_state is SideEffectState.UNKNOWN


def test_receipt_for_different_operation_does_not_mask_incomplete_effect():
    event = ToolEffectEvent(
        event_id="write-incomplete",
        effect=EffectKind.EFFECTFUL,
        status=EffectEventStatus.STARTED,
    )
    receipt = ExecutionReceipt(
        receipt_id="receipt-1",
        operation_id="write-complete",
        completed=True,
        persisted=True,
        safe_to_confirm=True,
    )

    with pytest.raises(InconsistentAgentResultError) as exc_info:
        validate_completion_evidence(
            _completed_confirmed(), events=[event], receipts=[receipt]
        )

    assert exc_info.value.evidence_state is SideEffectState.UNKNOWN


def test_matching_receipt_completes_started_operation():
    event = ToolEffectEvent(
        event_id="write-1",
        effect=EffectKind.EFFECTFUL,
        status=EffectEventStatus.STARTED,
    )
    receipt = ExecutionReceipt(
        receipt_id="receipt-1",
        operation_id="write-1",
        completed=True,
        persisted=True,
        safe_to_confirm=True,
    )

    assert (
        validate_completion_evidence(
            _completed_confirmed(), events=[event], receipts=[receipt]
        )
        is SideEffectState.CONFIRMED
    )


def test_duplicate_out_of_order_events_correlate_by_operation_id():
    events = [
        ToolEffectEvent(
            event_id="write-1",
            effect=EffectKind.EFFECTFUL,
            status=EffectEventStatus.COMPLETED,
        ),
        ToolEffectEvent(
            event_id="write-1",
            effect=EffectKind.EFFECTFUL,
            status=EffectEventStatus.STARTED,
        ),
        ToolEffectEvent(
            event_id="write-1",
            effect=EffectKind.EFFECTFUL,
            status=EffectEventStatus.STARTED,
        ),
    ]

    assert (
        validate_completion_evidence(_completed_confirmed(), events=events)
        is SideEffectState.CONFIRMED
    )


def test_completion_validation_never_infers_evidence_from_summary_text():
    result = _completed_confirmed(
        summary="The write was requested and I definitely sent and verified it."
    )

    with pytest.raises(InconsistentAgentResultError):
        validate_completion_evidence(result, events=[])
