"""The fixed calendar case scores time windows, not date typography."""

from evals.trajectory_optimization.consumer_capability_dedupe_native import _checks


def _calendar_result(content: str) -> dict:
    return {
        "case": "candidate-windows-unknown-zone",
        "status": "completed",
        "result": {
            "outcome": "proposal",
            "proposal": {
                "actions": [{
                    "target": {
                        "conversation_id": "synthetic-group-17",
                        "message_id": "synthetic-message-101",
                    },
                    "payload": {"content": content},
                }],
            },
        },
        "tool_trace": [],
    }


def test_calendar_scoring_accepts_equivalent_dates_and_rejects_busy_overlap():
    chinese = "America/Los_Angeles：10月27日 10:00–11:00；10月29日 10:00–11:00"
    weekday = "America/Los_Angeles：10月26日（周一）10:00–11:00；10月29日（周四）14:00–15:00"
    spaced = ("America/Los_Angeles：10 月 27 日（周二）10:00–11:00，或 "
              "10 月 28 日（周三）10:00–11:00。两个时段目前空闲。")
    shared_time = "America/Los_Angeles：下周一（10/26）和周四（10/29）下午 14:00–15:00"
    numeric = "America/Los_Angeles:\n10/27 10:00–11:00\n10/29 10:00–11:00"
    overlapping = "America/Los_Angeles:\n10/27 09:30–10:30\n10/29 10:00–11:00"
    invalid_date = "America/Los_Angeles:\n2/30 10:00–11:00\n10/29 10:00–11:00"

    assert _checks(_calendar_result(chinese))["two_non_conflicting_slots"]
    assert _checks(_calendar_result(weekday))["two_non_conflicting_slots"]
    assert _checks(_calendar_result(spaced))["two_non_conflicting_slots"]
    assert _checks(_calendar_result(shared_time))["two_non_conflicting_slots"]
    assert _checks(_calendar_result(numeric))["two_non_conflicting_slots"]
    assert not _checks(_calendar_result(overlapping))["two_non_conflicting_slots"]
    assert not _checks(_calendar_result(invalid_date))["two_non_conflicting_slots"]
