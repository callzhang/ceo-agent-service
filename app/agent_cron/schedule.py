from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import CroniterBadCronError, CroniterBadDateError, croniter


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
        return f"{self.expression} · {self.timezone_name}"
