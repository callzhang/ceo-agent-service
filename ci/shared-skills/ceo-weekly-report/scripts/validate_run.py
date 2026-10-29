"""Mechanical shape checks for this Skill's JSONML renderer.

the principal 2026-10-26: no data is never a reason to block a report. These checks
only reject input the renderer cannot place (wrong types, a row without the
identity it is keyed on). A value the report could not supply may be missing
or empty; the renderer shows it as `无数据`, and a missing owner as
`待确认负责人`.
"""

from collections.abc import Mapping

BUSINESS_LINE_KEYS = ("MorningStar", "Friday", "International", "WorldModel")
BUSINESS_LINE_TEXT_FIELDS = (
    "summary",
    "core_metrics",
    "milestone_deviation",
    "largest_risk",
    "management_decision",
)


def _named(record: object, fields: tuple[str, ...], location: str) -> list[str]:
    """Identity fields: the renderer keys a row on them and cannot invent them."""
    if not isinstance(record, Mapping):
        return [f"{location} must be an object for rendering"]
    return [
        f"{location} is missing renderer field {field}"
        for field in fields
        if not isinstance(record.get(field), str) or not record[field].strip()
    ]


def _owner(owner: object, location: str) -> list[str]:
    if owner is None:
        return []
    if not isinstance(owner, Mapping):
        return [f"{location} must be an object when given"]
    return [
        f"{location}.{key} must be a string"
        for key in ("name", "user_id")
        if key in owner and not isinstance(owner[key], str)
    ]


def _list_of_objects(value: object, location: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return [f"{location} must be an array"]
    return [
        f"{location}[{i}] must be an object"
        for i, item in enumerate(value)
        if not isinstance(item, Mapping)
    ]


def validate_render_input(report: dict, *, draft: bool = False) -> list[str]:
    errors: list[str] = []
    if not isinstance(report, dict):
        return ["report must be an object"]
    for field in ("ceo_judgment", "company_metrics", "issues"):
        if not isinstance(report.get(field), list):
            errors.append(f"{field} must be an array")
    if errors:
        return errors
    for i, judgment in enumerate(report["ceo_judgment"]):
        errors.extend(_named(judgment, ("text",), f"ceo_judgment[{i}]"))
    for i, metric in enumerate(report["company_metrics"]):
        errors.extend(_named(metric, ("name",), f"company_metrics[{i}]"))
    for i, issue in enumerate(report["issues"]):
        errors.extend(_named(issue, ("id", "question"), f"issues[{i}]"))
        if isinstance(issue, Mapping):
            errors.extend(_owner(issue.get("owner"), f"issues[{i}].owner"))
    if report["issues"] and not isinstance(report.get("as_of"), str):
        errors.append("as_of must be a string when rendering tracking rows")
    for field in ("cockpit", "discussion"):
        value = report.get(field)
        if value is not None and (
            not isinstance(value, list) or not all(isinstance(v, str) for v in value)
        ):
            errors.append(f"{field} must be an array of strings")
    errors.extend(_list_of_objects(report.get("root_causes"), "root_causes"))
    for i, cause in enumerate(report.get("root_causes") or []):
        if isinstance(cause, Mapping):
            errors.extend(_named(cause, ("text",), f"root_causes[{i}]"))
    lines = report.get("business_lines")
    if lines is not None and not isinstance(lines, Mapping):
        errors.append("business_lines must be an object")
    elif lines:
        for key, line in lines.items():
            location = f"business_lines.{key}"
            if not isinstance(line, Mapping):
                errors.append(f"{location} must be an object")
                continue
            for field in BUSINESS_LINE_TEXT_FIELDS:
                if line.get(field) is not None and not isinstance(line[field], str):
                    errors.append(f"{location}.{field} must be a string")
            for field in ("metrics", "milestones"):
                errors.extend(_list_of_objects(line.get(field), f"{location}.{field}"))
            if line.get("full_report_jsonml") is not None and not isinstance(
                line["full_report_jsonml"], list
            ):
                errors.append(f"{location}.full_report_jsonml must be an array")
            for i, metric in enumerate(line.get("metrics") or []):
                errors.extend(_named(metric, ("name",), f"{location}.metrics[{i}]"))
            for i, milestone in enumerate(line.get("milestones") or []):
                if isinstance(milestone, Mapping):
                    errors.extend(
                        _owner(milestone.get("owner"), f"{location}.milestones[{i}].owner")
                    )
    return list(dict.fromkeys(errors))
