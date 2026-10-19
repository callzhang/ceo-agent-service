"""Compute daily report windows from generic scheduled execution facts."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo
import argparse
import json
from pathlib import Path


def report_window(context):
    zone = ZoneInfo("Asia/Shanghai")
    end = datetime.fromisoformat(context["scheduled_for"]).astimezone(UTC)
    day = end.astimezone(zone).date()
    delivered = [
        datetime.fromisoformat(row["scheduled_for"]).astimezone(UTC)
        for row in context["history"]
        if row["run_id"] != context["run_id"]
        and datetime.fromisoformat(row["scheduled_for"]).astimezone(zone).date() < day
        and row["task_status"] == "done"
        and row["result_status"] == "completed"
    ]
    return {
        "report_date": day.isoformat(),
        "time_zone": str(zone),
        "window_utc": {
            "start": (
                max(delivered) if delivered else end - timedelta(days=1)
            ).isoformat(),
            "end": end.isoformat(),
        },
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
