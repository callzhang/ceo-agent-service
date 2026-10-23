"""Compute daily report windows from generic scheduled execution facts."""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo
import argparse
import json
from pathlib import Path


def report_window(context):
    zone = ZoneInfo("Asia/Shanghai")
    end = datetime.fromisoformat(context["scheduled_for"]).astimezone(UTC)
    local_end = end.astimezone(zone)
    day = local_end.date()
    day_start = datetime.combine(day, time.min, tzinfo=zone).astimezone(UTC)
    delivered = [
        datetime.fromisoformat(row["scheduled_for"]).astimezone(UTC)
        for row in context["history"]
        if row["run_id"] != context["run_id"]
        and datetime.fromisoformat(row["scheduled_for"]).astimezone(zone).date() < day
        and row["task_status"] == "done"
        and row["result_status"] == "completed"
    ]
    same_day_delivered = any(
        row["run_id"] != context["run_id"]
        and datetime.fromisoformat(row["scheduled_for"]).astimezone(zone).date() == day
        and row["task_status"] == "done"
        and row["result_status"] == "completed"
        for row in context["history"]
    )
    catchup_start = max(delivered) if delivered else end - timedelta(days=1)
    catchup = None
    if not same_day_delivered and catchup_start < day_start:
        catchup = {
            "start": catchup_start.isoformat(),
            "end": day_start.isoformat(),
        }
    return {
        "report_date": day.isoformat(),
        "time_zone": str(zone),
        "window_utc": {
            "start": day_start.isoformat(),
            "end": end.isoformat(),
        },
        "catchup_window_utc": catchup,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--context", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            report_window(json.loads(Path(args.context).read_text())),
            ensure_ascii=False,
        )
    )
