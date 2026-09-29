import argparse
import json
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
    exception_count = len(set(metric_by_key) - set(FIXED_METRICS))
    if exception_count > 3:
        errors.append("company report has more than 3 exception metrics")

    for claim in report.get("claims", []):
        errors.extend(validate_claim(claim))
    for issue in report.get("issues", []):
        errors.extend(validate_issue(issue))
    errors.extend(
        validate_issue_continuity(
            previous_issues, report.get("issues", [])
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
