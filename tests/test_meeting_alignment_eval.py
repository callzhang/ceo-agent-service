import json
from pathlib import Path

import pytest

from app.meeting_alignment_agent import (
    MeetingAlignmentAgent,
    MeetingAlignmentCodexRunner,
)
from app.meeting_alignment_models import MeetingSource


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "meeting_alignment_cases.json"
CASES = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _source(case: dict) -> MeetingSource:
    return MeetingSource.model_validate(
        {
            "meeting_id": f"live-{case['id']}",
            "title": f"语义评估：{case['id']}",
            "status": "ended",
            "started_at": "2026-07-14T10:00:00+08:00",
            "ended_at": "2026-07-14T11:00:00+08:00",
            "participants": [
                {"name": "Derek", "user_id": "derek"},
                {"name": "Alex", "user_id": "alex"},
                {"name": "Mina", "user_id": "mina"},
            ],
            "current_user_id": "derek",
            "summary": case["summary"],
            "transcript": [
                {"speaker_name": speaker, "text": text}
                for speaker, text in case["transcript"]
            ],
            "source_url": "",
        }
    )


@pytest.mark.live
@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_live_meeting_alignment_semantics(case: dict):
    workspace = Path(__file__).resolve().parents[1]
    decision = MeetingAlignmentAgent(
        MeetingAlignmentCodexRunner(workspace=workspace)
    ).decide(_source(case))
    fixture_id = case["id"]

    assert decision.action == case["expected_action"], fixture_id
    if expected_state := case.get("expected_state"):
        assert any(
            topic.state == expected_state for topic in decision.topics
        ), fixture_id
    if "expected_question_count" in case:
        assert (
            len(decision.key_questions) == case["expected_question_count"]
        ), fixture_id
    if expected_trigger := case.get("expected_trigger"):
        assert expected_trigger in decision.trigger_reasons, fixture_id
    if forbidden_trigger := case.get("forbidden_trigger"):
        assert forbidden_trigger not in decision.trigger_reasons, fixture_id
