import argparse
import copy
import json
import sys
import uuid
from pathlib import Path

from scripts.jsonml_check import check_document


LINE_LABELS = {
    "MorningStar": "MorningStar",
    "Friday": "Friday",
    "International": "国际业务",
    "WorldModel": "世界模型/营销线",
}


def node_id() -> str:
    return f"ceo-weekly-{uuid.uuid4()}"


def leaf(value: str, **attrs) -> list:
    return ["span", {"data-type": "leaf", **attrs}, value]


def text(value: str, **attrs) -> list:
    return ["span", {"data-type": "text"}, leaf(value, **attrs)]


def heading(level: int, value: str, fold: bool = False) -> list:
    attrs = {"uuid": node_id()}
    if fold:
        attrs["fold"] = True
    return [f"h{level}", attrs, text(value)]


def paragraph(value: str) -> list:
    return ["p", {"uuid": node_id()}, text(value)]


def mention(person: dict) -> list:
    return [
        "span",
        {
            "data-type": "mention",
            "id": person["user_id"],
            "name": person["name"],
        },
    ]


def metric_evidence(metric: dict) -> list:
    nodes = [text(metric["definition_evidence"])]
    if metric.get("current") == "无数据" and metric.get("decision_critical"):
        nodes.extend(
            [
                text("；待补："),
                mention(
                    {
                        "user_id": metric["responsible_user_id"],
                        "name": metric["responsible_name"],
                    }
                ),
                text(f"；下次检查：{metric['next_checkpoint']}"),
            ]
        )
    return nodes


def _cell_children(value) -> list:
    if isinstance(value, list):
        if value and isinstance(value[0], str):
            return [value]
        if all(isinstance(item, list) for item in value):
            return value
    return [text(str(value))]


def cell(value) -> list:
    return [
        "tc",
        {"colSpan": 1, "rowSpan": 1, "uuid": node_id()},
        ["p", {"uuid": node_id()}, *_cell_children(value)],
    ]


def table(headers: list[str], rows: list[list]) -> list:
    width = round(100 / len(headers), 2)
    result = [
        "table",
        {
            "colsWidth": [width] * len(headers),
            "tblW": {"w": 100, "type": "pct"},
            "uuid": node_id(),
            "styleId": "tableHeader",
            "tblLook": {"firstRow": 1, "firstColumn": 0, "lastRow": 0},
        },
    ]
    result.append(
        [
            "tr",
            {"uuid": node_id(), "isTblHeader": True},
            *[cell(value) for value in headers],
        ]
    )
    result.extend(
        ["tr", {"uuid": node_id()}, *[cell(value) for value in row]]
        for row in rows
    )
    return result


def node_text(node) -> str:
    if isinstance(node, str):
        return node
    if not isinstance(node, list):
        return ""
    return "".join(node_text(child) for child in node[2:])


def render_managed_sections(report: dict) -> list:
    nodes: list = [heading(1, "一、CEO本周判断")]
    nodes.extend(
        paragraph(f"{item['status']} {item['text']}")
        for item in report["ceo_judgment"]
    )

    nodes.append(heading(1, "二、公司级重点指标"))
    nodes.append(
        table(
            [
                "指标",
                "季度目标",
                "上周",
                "本周",
                "变化/差距",
                "状态",
                "数据截止",
                "口径与证据",
            ],
            [
                [
                    metric["name"],
                    metric["target"],
                    metric["previous"],
                    metric["current"],
                    metric["change"],
                    metric["status"],
                    metric["data_date"],
                    metric_evidence(metric),
                ]
                for metric in report["company_metrics"]
            ],
        )
    )

    nodes.append(heading(1, "三、跨周问题与行动"))
    nodes.append(
        table(
            [
                "ID",
                "原始问题",
                "当前根因",
                "上周状态",
                "新增证据",
                "当前状态",
                "持续周数",
                "下次检查点",
                "协同牵头",
                "关闭标准",
            ],
            [
                [
                    issue["id"],
                    issue["question"],
                    issue["current_root_cause"],
                    issue["prior_state"],
                    issue["new_evidence"],
                    issue["status_icon"],
                    issue["weeks_open"],
                    issue["next_checkpoint"],
                    [mention(issue["owner"])],
                    issue["closure_standard"],
                ]
                for issue in report["issues"]
            ],
        )
    )

    nodes.append(heading(1, "四、经营驾驶舱与CRM"))
    nodes.extend(
        paragraph(item)
        for item in report["cockpit"] or ["无需要管理层介入的异常"]
    )

    nodes.append(heading(1, "五、四条业务线"))
    for key, label in LINE_LABELS.items():
        line = report["business_lines"][key]
        nodes.append(heading(2, label))
        nodes.append(
            table(
                [
                    "本周经营结论",
                    "核心指标",
                    "里程碑偏差",
                    "最大风险",
                    "需管理层决策",
                ],
                [
                    [
                        line["summary"],
                        line["core_metrics"],
                        line["milestone_deviation"],
                        line["largest_risk"],
                        line["management_decision"],
                    ]
                ],
            )
        )
        nodes.append(
            table(
                ["指标", "目标", "本周", "状态", "数据截止", "口径与证据"],
                [
                    [
                        metric["name"],
                        metric["target"],
                        metric["current"],
                        metric["status"],
                        metric["data_date"],
                        metric_evidence(metric),
                    ]
                    for metric in line["metrics"]
                ],
            )
        )
        nodes.append(
            table(
                [
                    "日期",
                    "负责人",
                    "里程碑",
                    "交付物和验收证据",
                    "进度",
                    "状态",
                ],
                [
                    [
                        milestone["date"],
                        [mention(milestone["owner"])],
                        milestone["name"],
                        milestone["deliverable_evidence"],
                        milestone["progress"],
                        milestone["status"],
                    ]
                    for milestone in line["milestones"]
                ],
            )
        )
        nodes.append(heading(3, f"{label}完整周报", fold=True))
        nodes.extend(copy.deepcopy(line["full_report_jsonml"]))

    nodes.append(heading(1, "六、根因判断与管理指导"))
    nodes.extend(
        paragraph(item["text"])
        for item in report.get("root_causes", [])
        or [{"text": "无新增判断"}]
    )

    nodes.append(heading(1, "七、需讨论与决策"))
    nodes.extend(
        paragraph(item) for item in report["discussion"] or ["无"]
    )
    return nodes


def render_document(before: list, report: dict, managed_anchor: str) -> list:
    preserved = copy.deepcopy(before[:2])
    found = False
    for node in before[2:]:
        if node_text(node).strip() == managed_anchor:
            found = True
            break
        preserved.append(copy.deepcopy(node))
    if not found:
        raise ValueError(f"managed anchor not found: {managed_anchor}")
    return [*preserved, *render_managed_sections(report)]


MEETING_TRACKING_SECTION = "重点问题及待办跟踪"
MEETING_JUDGMENT_SECTION = "CEO本周判断"
MEETING_METRICS_SECTION = "公司级重点指标"


def _compact(value: str) -> str:
    return "".join(value.split())


def _h1_index(document: list, name: str) -> int:
    """The level-1 heading whose title, after its number, starts with name.

    The meeting template numbers its sections (`二、CEO本周判断 （10分钟）`),
    and the numbering and time notes change between weeks, so only the
    section name is compared.
    """
    matches = [
        index
        for index, node in enumerate(document)
        if index >= 2
        and isinstance(node, list)
        and node[0] == "h1"
        and _compact(node_text(node)).partition("、")[2].startswith(name)
    ]
    if len(matches) != 1:
        raise ValueError(f"expected one h1 section {name!r}, found {len(matches)}")
    return matches[0]


def _section_end(document: list, heading_index: int) -> int:
    for index in range(heading_index + 1, len(document)):
        node = document[index]
        if isinstance(node, list) and node[0] == "h1":
            return index
    return len(document)


def _tracking_row(header: str, issue: dict, as_of: str) -> object:
    values = {
        "ID": issue["id"],
        "事项/跟踪主题": issue.get("topic") or issue["question"],
        "责任人": [mention(issue["owner"])],
        "原始问题": issue["question"],
        "待办关闭标准": issue["closure_standard"],
        "上周计划": "—",
        "上周计划完成情况": "—",
        "本周计划": issue["next_checkpoint"],
        "待办发起时间": as_of.replace("-", ""),
        "最新状态（延期/风险/进行中/完成）": f"{issue['status_icon']} 新增",
    }
    return values.get(_compact(header), "")


def _append_tracking_rows(document: list, report: dict) -> None:
    start = _h1_index(document, MEETING_TRACKING_SECTION)
    end = _section_end(document, start)
    tables = [
        node
        for node in document[start + 1 : end]
        if isinstance(node, list) and node[0] == "table"
    ]
    if len(tables) != 1:
        raise ValueError(
            f"expected one tracking table under {MEETING_TRACKING_SECTION!r}, found {len(tables)}"
        )
    rows = [child for child in tables[0][2:] if isinstance(child, list) and child[0] == "tr"]
    if not rows:
        raise ValueError("tracking table has no header row")
    headers = [
        node_text(cell_node).strip()
        for cell_node in rows[0][2:]
        if isinstance(cell_node, list) and cell_node[0] == "tc"
    ]
    if "ID" not in headers:
        raise ValueError("tracking table has no ID column")
    id_column = headers.index("ID")
    existing = {
        _compact(node_text([c for c in row[2:] if isinstance(c, list) and c[0] == "tc"][id_column]))
        for row in rows[1:]
        if len([c for c in row[2:] if isinstance(c, list) and c[0] == "tc"]) > id_column
    }
    for issue in report["issues"]:
        if _compact(issue["id"]) in existing:
            continue
        tables[0].append(
            [
                "tr",
                {"uuid": node_id()},
                *[cell(_tracking_row(header, issue, report["as_of"])) for header in headers],
            ]
        )


def _judgment_nodes(report: dict) -> list:
    nodes = [
        paragraph(f"{item['status']} {item['text']}")
        for item in report["ceo_judgment"]
    ]
    nodes.append(heading(3, "跨周问题与行动"))
    nodes.append(
        table(
            ["ID", "原始问题", "当前根因", "上周状态", "新增证据", "当前状态",
             "持续周数", "下次检查点", "协同牵头", "关闭标准"],
            [
                [
                    issue["id"], issue["question"], issue["current_root_cause"],
                    issue["prior_state"], issue["new_evidence"], issue["status_icon"],
                    issue["weeks_open"], issue["next_checkpoint"],
                    [mention(issue["owner"])], issue["closure_standard"],
                ]
                for issue in report["issues"]
            ],
        )
    )
    nodes.append(heading(3, "经营驾驶舱与CRM"))
    nodes.extend(paragraph(item) for item in report["cockpit"] or ["无需要管理层介入的异常"])
    nodes.append(heading(3, "根因判断与管理指导"))
    nodes.extend(
        paragraph(item["text"])
        for item in report.get("root_causes", []) or [{"text": "无新增判断"}]
    )
    nodes.append(heading(3, "需讨论与决策"))
    nodes.extend(paragraph(item) for item in report["discussion"] or ["无"])
    return nodes


def _metrics_table(report: dict) -> list:
    return table(
        ["指标", "季度目标", "上周", "本周", "变化/差距", "状态", "数据截止", "口径与证据"],
        [
            [
                metric["name"], metric["target"], metric["previous"], metric["current"],
                metric["change"], metric["status"], metric["data_date"],
                metric_evidence(metric),
            ]
            for metric in report["company_metrics"]
        ],
    )


def render_meeting_document(before: list, report: dict) -> list:
    """Write the CEO parts of a 管理层周会 document and keep everything else.

    Derek 2026-09-24: the report fills `CEO本周判断` and `公司级重点指标`
    and appends its new issues to the `重点问题及待办跟踪` table. Existing
    tracking rows, the business-line sections the line owners fill in, the
    topic discussion and the report links are left as they are.
    """
    document = copy.deepcopy(before)
    _append_tracking_rows(document, report)
    for name, nodes in (
        (MEETING_JUDGMENT_SECTION, _judgment_nodes(report)),
        (MEETING_METRICS_SECTION, [_metrics_table(report)]),
    ):
        start = _h1_index(document, name)
        document[start + 1 : _section_end(document, start)] = nodes
    return document


def render_draft(report: dict) -> list:
    """The complete seven-section report as a standalone document."""
    return ["root", {}, *render_managed_sections(report)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", required=True)
    parser.add_argument("--report", required=True)
    layout = parser.add_mutually_exclusive_group(required=True)
    layout.add_argument("--anchor")
    layout.add_argument("--meeting-document", action="store_true")
    parser.add_argument("--output", required=True)
    parser.add_argument("--draft-output")
    args = parser.parse_args()

    before = json.loads(Path(args.before).read_text(encoding="utf-8"))
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    rendered = (
        render_meeting_document(before, report)
        if args.meeting_document
        else render_document(before, report, args.anchor)
    )
    outputs = [(args.output, rendered)]
    if args.draft_output:
        outputs.append((args.draft_output, render_draft(report)))
    # The document service rejects a whole write for one malformed node, so
    # nothing is written unless every rendered document is well formed.
    problems = [
        f"{path}: {error}"
        for path, document in outputs
        for error in check_document(document)[:20]
    ]
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    for path, document in outputs:
        Path(path).write_text(
            json.dumps(document, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
