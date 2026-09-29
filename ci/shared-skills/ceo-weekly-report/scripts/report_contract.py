from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


BEIJING = ZoneInfo("Asia/Shanghai")
BUSINESS_LINES = ("MorningStar", "Friday", "International", "WorldModel")
CLAIM_TYPES = {
    "verified_fact",
    "participant_statement",
    "forecast_or_judgment",
    "ceo_confirmed_judgment",
    "current_judgment",
    "hypothesis_pending_validation",
}
ISSUE_PREFIXES = {
    "COMP",
    "SALES",
    "FRI",
    "MS",
    "INTL",
    "WM",
    "RD",
    "DEL",
    "ORG",
}
PRIVATE_FIELDS = {"compensation", "health", "family", "private_emotion"}


def reporting_window(
    target_date: str, run_at: datetime | None
) -> tuple[datetime, datetime]:
    target = date.fromisoformat(target_date)
    start = datetime.combine(target - timedelta(days=7), time.min, BEIJING)
    scheduled_end = datetime.combine(target, time.min, BEIJING)
    if run_at is None:
        return start, scheduled_end
    cutoff = run_at.astimezone(BEIJING)
    return start, min(cutoff, scheduled_end)


def validate_manifest(manifest: dict) -> list[str]:
    errors: list[str] = []
    target = manifest.get("target", {})
    if not all(target.get(key) for key in ("title", "node_id", "revision")):
        errors.append("target identity or revision is incomplete")

    previous = manifest.get("previous", {})
    if not all(previous.get(key) for key in ("title", "node_id")):
        errors.append("previous report identity is incomplete")

    minutes = manifest.get("minutes", {})
    if not minutes.get("inventory_complete"):
        errors.append("minutes inventory is incomplete")
    transcript_ids = set(minutes.get("full_transcript_ids", []))
    for minutes_id in minutes.get("relevant_ids", []):
        if minutes_id not in transcript_ids:
            errors.append(
                f"relevant minutes {minutes_id} is missing full transcript"
            )

    messages = manifest.get("messages", {})
    if not messages.get("inventory_complete"):
        errors.append("message inventory is incomplete")
    context_read_ids = set(messages.get("context_read_ids", []))
    for message_id in messages.get("selected_ids", []):
        if message_id not in context_read_ids:
            errors.append(
                f"message {message_id} is missing surrounding context"
            )

    reports = manifest.get("business_reports", {})
    for line in BUSINESS_LINES:
        if not reports.get(line):
            errors.append(f"{line} source report is missing")
    return errors


def validate_claim(claim: dict) -> list[str]:
    claim_type = claim.get("type")
    if claim_type not in CLAIM_TYPES:
        return ["claim type is invalid"]

    errors: list[str] = []
    evidence = claim.get("evidence", [])
    if claim_type == "current_judgment":
        independent_sources = {
            (item.get("source_kind"), item.get("source_id"))
            for item in evidence
        }
        if len(independent_sources) < 2:
            errors.append("current judgment needs two independent sources")
        if "counterevidence" not in claim:
            errors.append(
                "current judgment is missing counterevidence review"
            )
    if claim_type == "ceo_confirmed_judgment" and not any(
        item.get("speaker_is_derek") for item in evidence
    ):
        errors.append("CEO judgment lacks a verified Derek statement")
    if claim_type != "hypothesis_pending_validation" and not evidence:
        errors.append("published claim has no evidence")
    return errors


def validate_metric(metric: dict) -> list[str]:
    errors: list[str] = []
    key = metric.get("key")
    if (
        metric.get("current") == "无数据"
        and metric.get("decision_critical")
    ):
        if not metric.get("responsible_user_id"):
            errors.append(
                f"{key} is missing responsible DingTalk user identity"
            )
        if not metric.get("responsible_name"):
            errors.append(
                f"{key} is missing responsible DingTalk name"
            )
        if not metric.get("next_checkpoint"):
            errors.append(f"{key} is missing next data checkpoint")
    if metric.get("current") != "无数据":
        if not metric.get("source"):
            errors.append(f"{key} is missing source")
        if not metric.get("data_date"):
            errors.append(f"{key} is missing data date")
        if not metric.get("definition"):
            errors.append(f"{key} is missing definition")
    return errors


def validate_privacy(record: dict) -> list[str]:
    return [
        f"private field {key} must not be published"
        for key in PRIVATE_FIELDS
        if record.get(key)
    ]


def validate_issue(issue: dict) -> list[str]:
    errors: list[str] = []
    issue_id = issue.get("id", "")
    prefix = issue_id.split("-", 1)[0]
    if prefix not in ISSUE_PREFIXES:
        errors.append(f"issue {issue_id or '<missing>'} has invalid prefix")
    if issue.get("status") == "closed" and not issue.get(
        "closure_evidence"
    ):
        errors.append(f"closed issue {issue_id} lacks outcome evidence")
    if issue.get("deadline") and issue.get("as_of"):
        overdue = date.fromisoformat(issue["deadline"]) < date.fromisoformat(
            issue["as_of"]
        )
        if (
            overdue
            and issue.get("status") != "closed"
            and issue.get("severity") != "red"
        ):
            errors.append(f"overdue issue {issue_id} must be red or closed")
    if (
        issue.get("weeks_without_gate_movement", 0) >= 2
        and issue.get("status") != "closed"
        and issue.get("severity") not in {"yellow", "red"}
    ):
        errors.append(f"stalled issue {issue_id} must be yellow or red")
    if (
        issue.get("weeks_without_evidence", 0) >= 3
        and issue.get("status") != "closed"
        and issue.get("management_decision")
        not in {"continue", "change_owner", "downgrade", "stop"}
    ):
        errors.append(
            f"issue {issue_id} needs continue/change owner/downgrade/stop decision"
        )
    return errors


def validate_issue_continuity(
    previous: list[dict], current: list[dict]
) -> list[str]:
    current_ids = {item.get("id") for item in current}
    return [
        f"open issue {item['id']} disappeared"
        for item in previous
        if item.get("status") != "closed" and item.get("id") not in current_ids
    ]


def validate_report_text(report: dict) -> list[str]:
    """The text lists the renderer turns into paragraphs must hold plain text.

    2026-09-26: a `cockpit` item was an object ({status, text}); validation
    passed, the renderer put the object inside a text leaf and the document
    service rejected the whole write.
    """
    errors: list[str] = []
    for key in ("cockpit", "discussion"):
        for index, item in enumerate(report.get(key, [])):
            if not isinstance(item, str):
                errors.append(
                    f"{key}[{index}] must be a string, got {type(item).__name__}"
                )
    for key in ("ceo_judgment", "root_causes"):
        for index, item in enumerate(report.get(key, [])):
            fields = ("status", "text") if key == "ceo_judgment" else ("text",)
            if not isinstance(item, dict) or not all(
                isinstance(item.get(field), str) for field in fields
            ):
                errors.append(
                    f"{key}[{index}] must be an object with string {'/'.join(fields)}"
                )
    return errors
