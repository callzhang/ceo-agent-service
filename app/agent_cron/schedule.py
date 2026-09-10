from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import CroniterBadCronError, CroniterBadDateError, croniter


_WEEKDAYS = ("日", "一", "二", "三", "四", "五", "六")


def cron_human_description(expression: str) -> str:
    """Return a short Chinese description for the supported common Cron forms.

    Cron expressions outside the deliberately small, unambiguous set are kept
    as a custom schedule instead of guessing at their meaning.
    """
    fields = " ".join(expression.split()).split(" ")
    if len(fields) != 6:
        return "按自定义计划执行"

    seconds, minutes, hours, days, months, weekdays = fields
    if seconds == "0" and minutes == "*" and hours == "*" and days == months == weekdays == "*":
        return "每分钟执行"

    # The product's seeded six-field Cron uses this compact form for minute
    # intervals (*/15 * * * * *); retain that established user-facing meaning.
    if (
        seconds.startswith("*/")
        and seconds[2:].isdigit()
        and int(seconds[2:]) > 0
        and minutes == hours == days == months == weekdays == "*"
    ):
        return f"每{int(seconds[2:])}分钟执行"

    if seconds == minutes == "0" and hours == "*" and days == months == weekdays == "*":
        return "每小时整点执行"

    if (
        seconds == minutes == "0"
        and hours.isdigit()
        and days == months == weekdays == "*"
        and 0 <= int(hours) <= 23
    ):
        return f"每天{int(hours):02d}:00执行"

    if (
        seconds == minutes == "0"
        and hours.isdigit()
        and days == months == "*"
        and weekdays.isdigit()
        and 0 <= int(hours) <= 23
        and 0 <= int(weekdays) <= 6
    ):
        return f"每周{_WEEKDAYS[int(weekdays)]}{int(hours):02d}:00执行"

    return "按自定义计划执行"


@dataclass(frozen=True)
class CronSchedule:
    expression: str
    timezone_name: str

    @classmethod
    def parse(cls, expression: str, timezone_name: str) -> CronSchedule:
        normalized_expression = " ".join(expression.split())
        if len(normalized_expression.split(" ")) != 6:
            raise ValueError("Cron expression must contain exactly six fields")

        try:
            validator = croniter(
                normalized_expression,
                datetime.now(timezone.utc),
                ret_type=datetime,
                second_at_beginning=True,
            )
            validator.get_next(datetime)
        except (CroniterBadCronError, CroniterBadDateError, KeyError, ValueError) as exc:
            raise ValueError(f"Invalid Cron expression: {expression}") from exc

        try:
            ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Unknown timezone: {timezone_name}") from exc

        return cls(
            expression=normalized_expression,
            timezone_name=timezone_name,
        )

    def next_after(self, instant: datetime) -> datetime:
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("instant must be timezone-aware")

        zone = ZoneInfo(self.timezone_name)
        current_utc = instant.astimezone(timezone.utc)
        local_base = current_utc.astimezone(zone).replace(tzinfo=None)
        schedule = croniter(
            self.expression,
            local_base,
            ret_type=datetime,
            second_at_beginning=True,
        )

        while True:
            local_candidate = schedule.get_next(datetime)
            candidate = local_candidate.replace(tzinfo=zone, fold=0)
            candidate_utc = candidate.astimezone(timezone.utc)
            round_trip = candidate_utc.astimezone(zone)

            if round_trip.replace(tzinfo=None) != local_candidate:
                continue
            if candidate_utc > current_utc:
                return candidate_utc

    def describe(self) -> str:
        return f"{cron_human_description(self.expression)} · {self.timezone_name}"
