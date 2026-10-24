from app.calendar_timezone import convert_calendar_timezone


def test_converts_using_date_specific_dst_offsets():
    result = convert_calendar_timezone(
        "2026-10-27T08:00", "America/Los_Angeles", "Europe/London"
    )

    assert result["status"] == "converted"
    assert result["conversion"] == {
        "fold": 0,
        "source_local": "2026-10-27T08:00:00-07:00",
        "source_offset": "UTC-07:00",
        "utc": "2026-10-27T15:00:00Z",
        "target_local": "2026-10-27T15:00:00+00:00",
        "target_offset": "UTC+00:00",
    }


def test_returns_both_folds_for_repeated_wall_time():
    result = convert_calendar_timezone(
        "2026-11-01T01:30", "America/Los_Angeles", "Europe/London"
    )

    assert result["status"] == "ambiguous"
    assert [option["utc"] for option in result["options"]] == [
        "2026-11-01T08:30:00Z",
        "2026-11-01T09:30:00Z",
    ]
    assert [option["source_offset"] for option in result["options"]] == [
        "UTC-07:00",
        "UTC-08:00",
    ]


def test_handles_london_fallback_as_counterpart_timezone():
    result = convert_calendar_timezone(
        "2026-10-25T01:30", "Europe/London", "America/Los_Angeles"
    )

    assert result["status"] == "ambiguous"
    assert [option["utc"] for option in result["options"]] == [
        "2026-10-25T00:30:00Z",
        "2026-10-25T01:30:00Z",
    ]
    assert [option["target_local"] for option in result["options"]] == [
        "2026-10-24T17:30:00-07:00",
        "2026-10-24T18:30:00-07:00",
    ]


def test_reports_nonexistent_wall_time_without_inventing_conversion():
    result = convert_calendar_timezone(
        "2026-03-08T02:30", "America/Los_Angeles", "Europe/London"
    )

    assert result["status"] == "nonexistent"
    assert result["options"] == []


def test_reports_london_spring_gap_for_counterpart_timezone():
    result = convert_calendar_timezone(
        "2026-03-29T01:30", "Europe/London", "America/Los_Angeles"
    )

    assert result["status"] == "nonexistent"
    assert result["options"] == []


def test_rejects_offset_bearing_local_time_and_unknown_zone():
    offset_bearing = convert_calendar_timezone(
        "2026-10-27T08:00-07:00", "America/Los_Angeles", "Europe/London"
    )
    unknown_zone = convert_calendar_timezone(
        "2026-10-27T08:00", "Mars/Olympus", "Europe/London"
    )

    assert offset_bearing["status"] == "offset_not_allowed"
    assert unknown_zone["status"] == "invalid_timezone"
