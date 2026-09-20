from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.attempt_what_happened import build_what_happened


@dataclass
class Run:
    id: int
    role: str
    status: str
    completed_at: str = "2026-09-20 07:12:37"
    started_at: str = "2026-09-20 07:06:00"
    final_result_json: str = ""
    structured_error_json: str = ""
    tool_events: list[Any] = field(default_factory=list)


def _send(text: str = "你的年假申请已同意") -> dict:
    return {
        "type": "item.completed",
        "item": {
            "type": "command_execution",
            "exit_code": 0,
            "status": "completed",
            "command": (
                "/bin/zsh -lc \"dws chat +messages-send --open-dingtalk-id DVPP "
                f"--text '{text}'\""
            ),
            "aggregated_output": '{"ok":true,"result":{"errorCode":null}}',
        },
    }


def _scores(**overrides: Any) -> str:
    payload = {
        "outcome": "proposal",
        "risk": "low",
        "confidence": 0.96,
        "rule_coverage": 1.0,
        "information_completeness": 1.0,
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def _refused(sentence: str) -> str:
    return json.dumps(
        {"code": "codex_result_invalid", "detail": sentence, "retryable": True}
    )


def _environment(code: str = "codex_total_timeout") -> str:
    return json.dumps({"code": code, "detail": "Codex execution timed out."})


def test_the_page_says_what_reached_the_outside_world() -> None:
    """Attempt 9698 read as "nothing happened" with the leave already approved."""

    runs = [
        Run(20305, "consumer", "completed", final_result_json=_scores()),
        Run(20309, "audit", "failed", tool_events=[_send()],
            structured_error_json=_refused("external_result: executed requires evidence")),
    ]

    answer = build_what_happened(runs)

    assert answer["reached_the_outside_world"] is True
    assert answer["acted_in_run_id"] == 20309
    recorded_by = {action["recorded_by"] for action in answer["external_actions"]}
    assert "turn" in recorded_by
    # An action already completed is not a question.
    assert answer["open_for_human"] is False


def test_nothing_reaching_the_outside_world_is_itself_the_answer() -> None:
    runs = [
        Run(1, "consumer", "completed", final_result_json=_scores()),
        Run(2, "audit", "failed", structured_error_json=_refused("no tool call")),
    ]

    answer = build_what_happened(runs)

    assert answer["reached_the_outside_world"] is False
    assert answer["external_actions"] == []
    assert answer["open_for_human"] is True


def test_a_rule_refusing_is_not_the_environment_failing() -> None:
    """The page called both "the runtime returned nothing verifiable"."""

    refused = build_what_happened(
        [Run(1, "audit", "failed", structured_error_json=_refused("This turn sent a message from its own shell."))]
    )
    broke = build_what_happened(
        [Run(1, "audit", "failed", structured_error_json=_environment())]
    )

    assert refused["stopped_because"]["kind"] == "rule"
    assert "its own shell" in refused["stopped_because"]["sentence"]
    assert broke["stopped_because"]["kind"] == "environment"
    assert broke["stopped_because"]["code"] == "codex_total_timeout"


def test_scores_come_from_the_turn_the_action_was_taken_on() -> None:
    """86% belonged to a later revision that never reached anyone.

    Attributing it to the whole attempt reads as "we approved on 86% complete
    material", which is false and worse than showing nothing.
    """
    runs = [
        Run(20305, "consumer", "completed", final_result_json=_scores()),
        Run(20309, "audit", "failed", tool_events=[_send()],
            structured_error_json=_refused("evidence required")),
        Run(20324, "consumer", "completed",
            final_result_json=_scores(information_completeness=0.86, confidence=0.98)),
    ]

    scores = build_what_happened(runs)["deciding_scores"]

    assert scores["from_run_id"] == 20305
    assert scores["information_completeness"] == 1.0


def test_a_generation_that_never_stopped_says_so() -> None:
    answer = build_what_happened([Run(1, "audit", "completed", final_result_json=_scores())])

    assert answer["stopped_because"]["kind"] == "none"
    assert answer["open_for_human"] is False


def test_it_reads_no_channel_specific_field() -> None:
    """A calendar response and a WeChat delivery ask the same three questions.

    Reading OA's fields specifically would put us back here the first time
    another channel goes wrong, so the answers come from the generation's own
    record and nothing else.
    """
    calendar = build_what_happened(
        [
            Run(1, "consumer", "completed", final_result_json=_scores()),
            Run(
                2,
                "audit",
                "failed",
                tool_events=[
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "command_execution",
                            "exit_code": 0,
                            "status": "completed",
                            "command": "/bin/zsh -lc 'dws chat +dm --to A --content \"已接受\"'",
                            "aggregated_output": '{"ok":true}',
                        },
                    }
                ],
                structured_error_json=_refused("prepared text required"),
            ),
        ]
    )

    assert calendar["reached_the_outside_world"] is True
    assert calendar["stopped_because"]["kind"] == "rule"
