"""Mechanical input checks for this Skill's JSONML renderer."""

from collections.abc import Mapping

METRIC_RENDER_FIELDS = (
    "name",
    "target",
    "previous",
    "current",
    "change",
    "status",
    "data_date",
    "definition_evidence",
)
BUSINESS_LINE_METRIC_RENDER_FIELDS = (
    "name",
    "target",
    "current",
    "status",
    "data_date",
    "definition_evidence",
)
ISSUE_RENDER_FIELDS = (
    "id",
    "question",
    "current_root_cause",
    "prior_state",
    "new_evidence",
    "status_icon",
    "weeks_open",
    "next_checkpoint",
    "closure_standard",
    "owner",
)
MILESTONE_RENDER_FIELDS = (
    "date",
    "owner",
    "name",
    "deliverable_evidence",
    "progress",
    "status",
)
BUSINESS_LINE_TEXT_FIELDS = (
    "summary",
    "core_metrics",
    "milestone_deviation",
    "largest_risk",
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


def validate_render_input(report: dict, *, draft: bool = False) -> list[str]:
    errors = []
    if not isinstance(report, dict):
        return ["report must be an object"]
    for field in ("ceo_judgment", "company_metrics", "issues"):
        if not isinstance(report.get(field), list):
            errors.append(f"{field} must be an array")
    if errors:
        return errors
    for i, judgment in enumerate(report.get("ceo_judgment", []) or []):
        errors.extend(
            _missing_renderer_fields(judgment, ("status", "text"), f"ceo_judgment[{i}]")
        )
    for i, metric in enumerate(report.get("company_metrics", []) or []):
        errors.extend(
            _missing_renderer_fields(
                metric, METRIC_RENDER_FIELDS, f"company_metrics[{i}]"
            )
        )
        if (
            isinstance(metric, Mapping)
            and metric.get("current") == "无数据"
            and metric.get("decision_critical")
        ):
            errors.extend(
                _missing_renderer_fields(
                    metric,
                    ("responsible_name", "responsible_user_id", "next_checkpoint"),
                    f"company_metrics[{i}]",
                )
            )
    for i, issue in enumerate(report.get("issues", []) or []):
        errors.extend(
            _missing_renderer_fields(issue, ISSUE_RENDER_FIELDS, f"issues[{i}]")
        )
        if isinstance(issue, Mapping):
            errors.extend(
                _validate_renderer_owner(issue.get("owner"), f"issues[{i}].owner")
            )
    if report["issues"] and not isinstance(report.get("as_of"), str):
        errors.append("as_of must be a string when rendering tracking rows")
    if draft:
        for field in ("cockpit", "root_causes", "discussion"):
            if not isinstance(report.get(field), list):
                errors.append(f"{field} must be an array")
        for field in ("cockpit", "discussion"):
            if isinstance(report.get(field), list):
                for i, value in enumerate(report[field]):
                    if not isinstance(value, str):
                        errors.append(f"{field}[{i}] must be a string")
        if isinstance(report.get("root_causes"), list):
            for i, value in enumerate(report["root_causes"]):
                errors.extend(_missing_renderer_fields(value, ("text",), f"root_causes[{i}]"))
        lines = report.get("business_lines")
        if not isinstance(lines, dict):
            errors.append("business_lines must be an object")
        else:
            for key in ("MorningStar", "Friday", "International", "WorldModel"):
                if key not in lines:
                    errors.append(f"business_lines.{key} is required by the full draft renderer")
            for key, line in lines.items():
                if not isinstance(line, dict):
                    errors.append(f"business_lines.{key} must be an object")
                    continue
                for field in BUSINESS_LINE_TEXT_FIELDS:
                    if not isinstance(line.get(field), str):
                        errors.append(f"business_lines.{key}.{field} must be a string")
                for field in ("metrics", "milestones", "full_report_jsonml"):
                    if not isinstance(line.get(field), list):
                        errors.append(f"business_lines.{key}.{field} must be an array")
                for i, metric in enumerate(line.get("metrics", []) or []):
                    errors.extend(
                        _missing_renderer_fields(
                            metric,
                            BUSINESS_LINE_METRIC_RENDER_FIELDS,
                            f"business_lines.{key}.metrics[{i}]",
                        )
                    )
                for i, milestone in enumerate(line.get("milestones", []) or []):
                    errors.extend(
                        _missing_renderer_fields(
                            milestone,
                            MILESTONE_RENDER_FIELDS,
                            f"business_lines.{key}.milestones[{i}]",
                        )
                    )
                    if isinstance(milestone, Mapping):
                        errors.extend(
                            _validate_renderer_owner(
                                milestone.get("owner"),
                                f"business_lines.{key}.milestones[{i}].owner",
                            )
                        )
    return list(dict.fromkeys(errors))
