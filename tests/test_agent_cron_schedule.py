from datetime import datetime, timezone

import pytest

from app.agent_cron.schedule import CronSchedule, cron_human_description


@pytest.mark.parametrize(
    ("expression", "description"),
    [
        ("0 * * * * *", "每分钟执行"),
        ("*/15 * * * * *", "每15秒执行"),
        ("0 0 * * * *", "每小时整点执行"),
        ("0 0 9 * * *", "每天09:00执行"),
        ("0 0 18 * * 0", "每周日18:00执行"),
        ("5 10 9 * * 1", "按自定义计划执行"),
    ],
)
def test_cron_human_description_translates_common_schedules(
    expression: str, description: str
) -> None:
    assert cron_human_description(expression) == description


def test_schedule_description_uses_human_description_and_timezone() -> None:
    schedule = CronSchedule.parse("0 0 9 * * *", "Asia/Shanghai")

    assert schedule.describe() == "每天09:00执行 · Asia/Shanghai"


def test_six_field_schedule_supports_seconds_at_the_beginning() -> None:
    schedule = CronSchedule.parse("*/15 * * * * *", "UTC")

    assert schedule.next_after(datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)) == datetime(
        2026, 9, 8, 12, 0, 15, tzinfo=timezone.utc
    )


def test_every_minute_returns_the_next_strictly_future_minute() -> None:
    schedule = CronSchedule.parse("0 * * * * *", "UTC")

    assert schedule.next_after(datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)) == datetime(
        2026, 9, 8, 12, 1, 0, tzinfo=timezone.utc
    )


def test_daily_beijing_schedule_is_calculated_in_its_timezone() -> None:
    schedule = CronSchedule.parse("0 0 20 * * *", "Asia/Shanghai")

    assert schedule.next_after(datetime(2026, 9, 8, 11, 59, 59, tzinfo=timezone.utc)) == datetime(
        2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc
    )


def test_weekly_beijing_schedule_uses_cron_weekday() -> None:
    schedule = CronSchedule.parse("0 0 18 * * 0", "Asia/Shanghai")

    assert schedule.next_after(datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)) == datetime(
        2026, 9, 13, 10, 0, 0, tzinfo=timezone.utc
    )


def test_los_angeles_spring_forward_skips_nonexistent_wall_time() -> None:
    schedule = CronSchedule.parse("0 30 2 * * *", "America/Los_Angeles")

    assert schedule.next_after(datetime(2026, 3, 8, 9, 0, 0, tzinfo=timezone.utc)) == datetime(
        2026, 3, 9, 9, 30, 0, tzinfo=timezone.utc
    )


def test_los_angeles_fall_back_runs_repeated_wall_time_once() -> None:
    schedule = CronSchedule.parse("0 30 1 * * *", "America/Los_Angeles")

    first = schedule.next_after(datetime(2026, 11, 1, 7, 0, 0, tzinfo=timezone.utc))
    second = schedule.next_after(first)
    after_fallback = schedule.next_after(
        datetime(2026, 11, 1, 9, 0, 0, tzinfo=timezone.utc)
    )

    assert first == datetime(2026, 11, 1, 8, 30, 0, tzinfo=timezone.utc)
    assert second == datetime(2026, 11, 2, 9, 30, 0, tzinfo=timezone.utc)
    assert after_fallback == datetime(2026, 11, 2, 9, 30, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("expression", ["* * * * *", "* * * * * * *"])
def test_parse_rejects_non_six_field_expressions(expression: str) -> None:
    with pytest.raises(ValueError, match="six fields"):
        CronSchedule.parse(expression, "UTC")


def test_parse_rejects_unknown_timezone() -> None:
    with pytest.raises(ValueError, match="Unknown timezone"):
        CronSchedule.parse("0 * * * * *", "Mars/Olympus_Mons")


@pytest.mark.parametrize(
    "expression",
    [
        "0 0 0 31 2 *",
        "0 0 0 30 2 *",
        "0 0 0 31 4 *",
    ],
)
def test_parse_rejects_schedules_that_can_never_run(expression: str) -> None:
    with pytest.raises(ValueError, match="^Invalid Cron expression"):
        CronSchedule.parse(expression, "UTC")


def test_parse_accepts_leap_day_schedule() -> None:
    assert CronSchedule.parse("0 0 0 29 2 *", "UTC").expression == "0 0 0 29 2 *"


def test_parse_maps_invalid_field_values_to_public_error() -> None:
    with pytest.raises(ValueError, match="^Invalid Cron expression"):
        CronSchedule.parse("61 * * * * *", "UTC")


def test_next_after_rejects_naive_datetime() -> None:
    schedule = CronSchedule.parse("*/15 * * * * *", "UTC")

    with pytest.raises(ValueError, match="timezone-aware"):
        schedule.next_after(datetime(2026, 9, 8, 12, 0, 0))


def test_description_uses_human_schedule_and_timezone() -> None:
    schedule = CronSchedule.parse("0 0 20 * * *", "Asia/Shanghai")

    assert schedule.describe() == "每天20:00执行 · Asia/Shanghai"
