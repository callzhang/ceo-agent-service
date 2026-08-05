import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.agent_result import AgentOutcome, AgentResult, ResultParseError, parse_agent_result


SCHEMA_PATH = Path(__file__).parents[1] / "app" / "schemas" / "agent_result.schema.json"


def _result_json(
    *,
    outcome: str = "completed",
    summary: str = "work completed",
) -> str:
    return json.dumps(
        {
            "outcome": outcome,
            "summary": summary,
            "error": {
                "code": "",
                "retryable": False,
                "authorization_required": False,
            },
        }
    )


def test_agent_result_schema_requires_every_declared_property():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert set(schema["required"]) == set(schema["properties"])
    error_schema = schema["$defs"]["AgentError"]
    assert set(error_schema["required"]) == set(error_schema["properties"])
    assert "side_effect_state" not in error_schema["properties"]
    assert all(
        "default" not in property_schema
        for property_schema in error_schema["properties"].values()
    )


def test_committed_schema_matches_model():
    assert json.loads(SCHEMA_PATH.read_text()) == AgentResult.model_json_schema()


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


def test_parse_agent_result_normalizes_null_error_from_schema_less_resume():
    payload = {
        "outcome": "completed",
        "summary": "resume completed",
        "error": None,
    }

    result = parse_agent_result(json.dumps({"message": json.dumps(payload)}))

    assert result.summary == "resume completed"
    assert result.error.code == ""
    assert result.error.retryable is False
    assert result.error.authorization_required is False


def test_agent_result_forbids_extra_fields():
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

    compatible = AgentResult.model_validate_json(
        json.dumps(
            {
                "outcome": "completed",
                "summary": "done",
                "error": {
                    "code": "",
                    "retryable": False,
                    "authorization_required": False,
                    "side_effect_state": "confirmed",
                },
            }
        )
    )
    assert "side_effect_state" not in compatible.model_dump()["error"]


@pytest.mark.parametrize(
    ("path", "wrong_value"),
    [
        (("outcome",), 1),
        (("summary",), 123),
        (("error", "retryable"), "false"),
        (("error", "authorization_required"), 0),
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


def test_parse_agent_result_rejects_truncated_record_after_valid_result():
    raw = "\n".join(
        [
            json.dumps({"message": _result_json(summary="old valid")}),
            '{"type":"item.completed","item":',
        ]
    )

    with pytest.raises(ResultParseError, match="JSONL record is malformed"):
        parse_agent_result(raw)


def test_parse_agent_result_ignores_malformed_leading_noise():
    raw = "\n".join(
        [
            "Codex CLI startup notice: checking configuration",
            json.dumps({"message": _result_json(summary="valid result")}),
        ]
    )

    assert parse_agent_result(raw).summary == "valid result"


def test_parse_agent_result_skips_later_non_agent_message_event():
    raw = "\n".join(
        [
            json.dumps({"message": _result_json(summary="agent result")}),
            json.dumps({"type": "error", "message": "transport failed"}),
        ]
    )

    assert parse_agent_result(raw).summary == "agent result"
