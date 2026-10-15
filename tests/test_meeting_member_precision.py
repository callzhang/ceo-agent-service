from types import SimpleNamespace

from app.dingtalk_models import DingTalkConversation
from app.meeting_alignment import _search_meeting_group_candidates
from app.meeting_alignment_models import MeetingSource


def discover(rosters, *, participant_count=3, complete=True):
    source = MeetingSource.model_validate({
        "meeting_id": "precision", "title": "项目进展", "status": "ended",
        "started_at": "2026-10-01T09:00:00+08:00",
        "ended_at": "2026-10-01T10:00:00+08:00",
        "participants": [
            {"name": f"P{i}", "user_id": f"u{i}", "open_dingtalk_id": f"p{i}"}
            for i in range(participant_count)
        ],
        "attendee_evidence": "calendar", "attendee_roster_complete": complete,
        "current_user_id": "u0", "summary": "项目进展", "transcript": [],
    })
    groups = [DingTalkConversation(open_conversation_id=key, title="项目群",
        single_chat=False, unread_point=0) for key in rosters]
    dws = SimpleNamespace(
        search_conversations=lambda query: groups,
        list_group_member_open_dingtalk_ids=lambda group: rosters[group],
        read_recent_messages=lambda conversation, limit: [],
    )
    return _search_meeting_group_candidates(dws, source,
        SimpleNamespace(recurring_meeting_group_targets=lambda title: []))


def test_large_group_reports_three_over_full_group_size_not_three_over_three():
    members = {"p0", "p1", "p2", *(f"extra{i}" for i in range(27))}
    [candidate] = discover({"broad": members})
    assert candidate.get("member_precision") == "3/30"
    assert candidate["member_precision_ratio"] == 0.1
    assert candidate["member_count"] == 30
    assert candidate["member_open_dingtalk_ids"] == sorted(members)
    assert "participant_coverage" not in candidate
    assert "attendee_overlap" not in candidate


def test_precise_group_is_not_filtered_for_missing_most_meeting_participants():
    # Old 60% attendee recall discards this 2/2 group, favoring the 3/30 group.
    candidates = discover({"broad": {"p0", "p1", "p2", *(f"x{i}" for i in range(27))},
        "focused": {"p0", "p1"}}, participant_count=5)
    assert [row["conversation_id"] for row in candidates] == ["focused", "broad"]
    assert candidates[0]["member_precision"] == "2/2"
    assert candidates[1]["member_precision"] == "3/30"


def test_empty_group_precision_is_unknown_not_perfect_match():
    [candidate] = discover({"empty": set()})
    assert candidate["member_precision_ratio"] is None
    assert candidate["member_count"] == 0


def test_incomplete_meeting_roster_still_reports_group_denominator_and_gap():
    [candidate] = discover({"partial": {"p0", "p1", "other"}}, complete=False)
    assert candidate["member_precision"] == "2/3"
    assert candidate["reference_audience_complete"] is False
    assert candidate["member_roster_complete"] is True
