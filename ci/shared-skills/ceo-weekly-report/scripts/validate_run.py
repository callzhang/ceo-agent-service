import argparse
import json
from collections.abc import Mapping
from pathlib import Path

from scripts.report_contract import (
    BUSINESS_LINES,
    validate_claim,
    validate_issue,
    validate_issue_continuity,
    validate_manifest,
    validate_metric,
    validate_privacy,
    validate_report_text,
)
from scripts.jsonml_check import check_node


FIXED_METRICS = (
    "new_signed_orders",
    "recognized_revenue",
    "payment_collected",
    "gross_margin",
    "overdue_receivables",
    "weighted_pipeline",
    "opportunities_advanced",
    "friday_ka_poc_orders",
    "friday_smb_wau_paid_conversion",
    "morningstar_orders_margin_new_opportunities",
    "international_pipeline_paid_poc",
    "worldmodel_validated_demand_paid_poc",
)
REQUIRED_REPORT_KEYS = (
    "ceo_judgment",
    "company_metrics",
    "issues",
    "cockpit",
    "business_lines",
    "root_causes",
    "discussion",
    "as_of",
)
REQUIRED_BUSINESS_LINE_KEYS = (
    "summary",
    "core_metrics",
    "milestone_deviation",
    "largest_risk",
    "management_decision",
    "metrics",
    "milestones",
    "full_report_jsonml",
)
METRIC_RENDER_FIELDS = (
    "name", "target", "previous", "current", "change", "status",
    "data_date", "definition_evidence",
)
ISSUE_RENDER_FIELDS = (
    "question", "current_root_cause", "prior_state", "new_evidence",
    "status_icon", "weeks_open", "next_checkpoint", "closure_standard",
    "owner",
)
MILESTONE_RENDER_FIELDS = (
    "date", "owner", "name", "deliverable_evidence", "progress", "status",
)
BUSINESS_LINE_TEXT_FIELDS = (
    "summary", "core_metrics", "milestone_deviation", "largest_risk",
    "management_decision",
)


def _missing_renderer_fields(
    record: object, fields: tuple[str, ...], location: str
) -> list[str]:
    if not isinstance(record, Mapping):
        return [f"{location} must be an object for rendering"]
    return [
        f"{location} is missing renderer field {field}"
        for field in fields
        if field not in record or record[field] is None or record[field] == ""
    ]


def _validate_renderer_owner(owner: object, location: str) -> list[str]:
    errors = _missing_renderer_fields(owner, ("name", "user_id"), location)
    if isinstance(owner, Mapping):
        if "name" in owner and not isinstance(owner["name"], str):
            errors.append(f"{location}.name must be a string")
        if "user_id" in owner and not isinstance(owner["user_id"], str):
            errors.append(f"{location}.user_id must be a string")
    return errors


def validate_run(
    manifest: dict, report: dict, previous_issues: list[dict]
) -> dict:
    errors = validate_manifest(manifest)

    for key in REQUIRED_REPORT_KEYS:
        if key not in report:
            errors.append(f"report section {key} is missing")

    metrics = report.get("company_metrics", [])
    metric_by_key = {item.get("key"): item for item in metrics}
    if len(metric_by_key) != len(metrics):
        errors.append("company report has duplicate metric keys")
    for key in FIXED_METRICS:
        if key not in metric_by_key:
            errors.append(f"fixed metric {key} is missing")
        else:
            errors.extend(validate_metric(metric_by_key[key]))
    for index, metric in enumerate(metrics):
        key = metric.get("key")
        if key not in FIXED_METRICS:
            errors.extend(validate_metric(metric))
        errors.extend(
            _missing_renderer_fields(
                metric, METRIC_RENDER_FIELDS, f"company_metrics[{index}]"
            )
        )
    exception_count = len(set(metric_by_key) - set(FIXED_METRICS))
    if exception_count > 3:
        errors.append("company report has more than 3 exception metrics")

    for claim in report.get("claims", []):
        errors.extend(validate_claim(claim))
    issues = report.get("issues", [])
    for index, issue in enumerate(issues):
        errors.extend(validate_issue(issue))
        errors.extend(
            _missing_renderer_fields(
                issue, ISSUE_RENDER_FIELDS, f"issues[{index}]"
            )
        )
        if isinstance(issue, Mapping) and "owner" in issue:
            errors.extend(
                _validate_renderer_owner(issue["owner"], f"issues[{index}].owner")
            )
    errors.extend(
        validate_issue_continuity(
            previous_issues, issues
        )
    )

    business_lines = report.get("business_lines", {})
    for line_name in BUSINESS_LINES:
        if line_name not in business_lines:
            errors.append(f"business line {line_name} is missing from report")
            continue
        for key in REQUIRED_BUSINESS_LINE_KEYS:
            if key not in business_lines[line_name]:
                errors.append(
                    f"business line {line_name} is missing field {key}"
                )
        line = business_lines[line_name]
        if not isinstance(line, Mapping):
            continue
        for field in BUSINESS_LINE_TEXT_FIELDS:
            if not isinstance(line.get(field), str):
                errors.append(
                    f"business line {line_name} field {field} must be a string"
                )
        line_metrics = line.get("metrics", [])
        if isinstance(line_metrics, list):
            for index, metric in enumerate(line_metrics):
                errors.extend(validate_metric(metric))
                errors.extend(
                    _missing_renderer_fields(
                        metric,
                        METRIC_RENDER_FIELDS,
                        f"business_lines.{line_name}.metrics[{index}]",
                    )
                )
        else:
            errors.append(f"business line {line_name} metrics must be an array")
        milestones = line.get("milestones", [])
        if isinstance(milestones, list):
            for index, milestone in enumerate(milestones):
                location = f"business_lines.{line_name}.milestones[{index}]"
                errors.extend(
                    _missing_renderer_fields(
                        milestone, MILESTONE_RENDER_FIELDS, location
                    )
                )
                if isinstance(milestone, Mapping) and "owner" in milestone:
                    errors.extend(
                        _validate_renderer_owner(
                            milestone["owner"], f"{location}.owner"
                        )
                    )
        else:
            errors.append(f"business line {line_name} milestones must be an array")

    errors.extend(validate_report_text(report))
    for line_name in BUSINESS_LINES:
        nodes = business_lines.get(line_name, {}).get("full_report_jsonml", [])
        if not isinstance(nodes, list):
            errors.append(f"{line_name} full_report_jsonml must be an array")
            continue
        for index, node in enumerate(nodes):
            if not isinstance(node, list):
                errors.append(
                    f"{line_name} full_report_jsonml[{index}] must be a block node"
                )
                continue
            errors.extend(
                f"{line_name} full_report_jsonml[{index}] {error}"
                for error in check_node(node)
            )

    errors.extend(validate_privacy(report.get("private", {})))
    unique_errors = list(dict.fromkeys(errors))
    return {"publishable": not unique_errors, "errors": unique_errors}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--previous-issues", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    def load(path: str):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    result = validate_run(
        load(args.manifest),
        load(args.report),
        load(args.previous_issues),
    )
    Path(args.output).write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["publishable"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
