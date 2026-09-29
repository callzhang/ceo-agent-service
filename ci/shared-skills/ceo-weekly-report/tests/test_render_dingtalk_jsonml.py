import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.jsonml_check import check_document
from scripts.render_dingtalk_jsonml import (
    main,
    node_text,
    render_document,
    render_draft,
    render_meeting_document,
)


class RendererTests(unittest.TestCase):
    def setUp(self):
        self.report = {
            "ceo_judgment": [
                {"text": "Friday上线标准需要闭环", "status": "⚠️"}
            ],
            "company_metrics": [
                {
                    "name": "新签订单",
                    "target": "5000万",
                    "previous": "无数据",
                    "current": "无数据",
                    "change": "无数据",
                    "status": "❓",
                    "data_date": "2026-09-13",
                    "definition_evidence": "CRM，口径待财务确认",
                    "decision_critical": True,
                    "responsible_user_id": "finance123",
                    "responsible_name": "张丽丽(Lily)",
                    "next_checkpoint": "2026-09-11 18:00",
                }
            ],
            "issues": [
                {
                    "id": "FRI-003",
                    "question": "Friday上线标准是否已共同验收？",
                    "current_root_cause": "业务Eval与通用Eval尚未共同验收",
                    "prior_state": "上线标准未统一",
                    "new_evidence": "测试周报",
                    "status_icon": "⚠️",
                    "weeks_open": 2,
                    "next_checkpoint": "2026-09-11",
                    "owner": {"name": "王靖", "user_id": "u123"},
                    "closure_standard": "两类Eval通过并保留Gate记录",
                }
            ],
            "cockpit": [],
            "business_lines": {
                "MorningStar": self.business_line(
                    "规划缺少未来市场机会验证",
                    "source-ms",
                    [
                        "span",
                        {"data-type": "text"},
                        [
                            "span",
                            {"data-type": "leaf", "bold": True},
                            "完整内容",
                        ],
                    ],
                ),
                "Friday": self.business_line(
                    "产品与技术里程碑待对齐",
                    "source-fri",
                    [
                        "a",
                        {"href": "https://example.com/friday"},
                        "Friday原始周报",
                    ],
                ),
                "International": self.business_line(
                    "无数据", "source-intl", "完整内容"
                ),
                "WorldModel": self.business_line(
                    "无数据", "source-wm", "完整内容"
                ),
            },
            "root_causes": [],
            "discussion": [],
        }
        self.before = [
            "root",
            {},
            ["p", {"uuid": "preface"}, "保留的会议信息"],
            ["h1", {"uuid": "anchor"}, "第一部分：指标"],
            ["p", {"uuid": "old"}, "旧内容"],
        ]

    @staticmethod
    def business_line(summary, source_id, source_child):
        return {
            "summary": summary,
            "core_metrics": "无数据",
            "milestone_deviation": "无数据",
            "largest_risk": "无数据",
            "management_decision": "无",
            "metrics": [],
            "milestones": [
                {
                    "date": "2026-09-11",
                    "owner": {"name": "王靖", "user_id": "u123"},
                    "name": "数字分身上线Gate",
                    "deliverable_evidence": "上线Gate记录",
                    "progress": "业务与通用Eval待共同验收",
                    "status": "⌛",
                }
            ],
            "full_report_jsonml": [
                ["p", {"uuid": source_id}, source_child]
            ],
        }

    def render(self):
        return render_document(
            self.before, self.report, "第一部分：指标"
        )

    def test_preserves_content_before_managed_anchor(self):
        self.assertEqual(
            self.render()[2],
            ["p", {"uuid": "preface"}, "保留的会议信息"],
        )

    def test_removes_content_after_managed_anchor(self):
        self.assertNotIn("旧内容", str(self.render()))

    def test_uses_native_mention_node(self):
        rendered = str(self.render())
        self.assertIn("'data-type': 'mention'", rendered)
        self.assertIn("'id': 'u123'", rendered)
        self.assertIn("'name': '王靖'", rendered)

    def test_missing_metric_renders_native_owner_and_checkpoint(self):
        rendered = str(self.render())
        self.assertIn("'id': 'finance123'", rendered)
        self.assertIn("'name': '张丽丽(Lily)'", rendered)
        self.assertIn("下次检查：2026-09-11 18:00", rendered)

    def test_business_line_full_report_is_folded(self):
        rendered = str(self.render())
        self.assertIn("'fold': True", rendered)
        self.assertIn("MorningStar完整周报", rendered)

    def test_output_contains_no_literal_markdown_bold(self):
        self.assertNotIn("**", str(self.render()))

    def test_preserves_source_report_rich_text_and_links(self):
        rendered = str(self.render())
        self.assertIn("'bold': True", rendered)
        self.assertIn("https://example.com/friday", rendered)

    def test_missing_managed_anchor_blocks_render(self):
        with self.assertRaisesRegex(ValueError, "managed anchor not found"):
            render_document(self.before, self.report, "不存在的标题")

    def test_business_line_summary_and_metrics_tables_exist(self):
        rendered = str(self.render())
        self.assertIn("本周经营结论", rendered)
        self.assertIn("里程碑偏差", rendered)
        self.assertIn("口径与证据", rendered)

    def test_business_line_milestone_table_uses_native_owner(self):
        rendered = str(self.render())
        self.assertIn("交付物和验收证据", rendered)
        self.assertIn("数字分身上线Gate", rendered)
        self.assertIn("上线Gate记录", rendered)
        self.assertIn("'id': 'u123'", rendered)


if __name__ == "__main__":
    unittest.main()


def _tc(value):
    return ["tc", {}, ["p", {}, ["span", {"data-type": "text"}, value]]]


class MeetingDocumentTests(unittest.TestCase):
    def setUp(self):
        base = RendererTests()
        base.setUp()
        self.report = {**base.report, "as_of": "2026-09-26"}
        self.before = [
            "root",
            {},
            ["table", {}, ["tr", {}, _tc("日程")]],
            ["h1", {}, "一、重点问题及待办跟踪（10分钟）可发起新的事项跟踪"],
            [
                "table",
                {},
                ["tr", {}, *[_tc(h) for h in ["ID", "事项/跟踪主题", "责任人", "待办关闭标准", "待办发起时间", "最新状态（延期/风险/进行中/完成）"]]],
                ["tr", {}, *[_tc(v) for v in ["FRI-001", "已有事项", "", "标准", "20260921", "进行中"]]],
            ],
            ["h1", {}, "二、CEO本周判断 （10分钟）"],
            ["p", {}, "上周的判断"],
            ["h1", {}, "三、公司级重点指标（10分钟）"],
            ["table", {}, ["tr", {}, _tc("旧指标")]],
            ["h1", {}, "四、各业务线情况"],
            ["h2", {}, "MorningStar （15分钟）"],
            ["p", {}, "负责人填写的内容"],
            ["h2", {}, "五、主题讨论（30分钟）"],
            ["p", {}, "议题：定价"],
        ]

    def render(self):
        return render_meeting_document(self.before, self.report)

    def tracking_rows(self, document):
        return [row for row in document[4][2:] if row[0] == "tr"]

    def test_fills_judgment_and_metrics_and_keeps_the_rest(self):
        rendered = self.render()
        text = str(rendered)
        self.assertNotIn("上周的判断", text)
        self.assertNotIn("旧指标", text)
        self.assertIn("Friday上线标准需要闭环", text)
        self.assertIn("口径与证据", text)
        self.assertEqual(rendered[:4], self.before[:4])
        self.assertEqual(rendered[-5:], self.before[-5:])

    def test_appends_new_issue_rows_under_the_tables_own_columns(self):
        rows = self.tracking_rows(self.render())
        self.assertEqual(len(rows), 3)
        cells = [node_text(cell) for cell in rows[-1][2:]]
        self.assertEqual(cells[0], "FRI-003")
        self.assertEqual(cells[3], "两类Eval通过并保留Gate记录")
        self.assertEqual(cells[4], "20260926")
        self.assertIn("'id': 'u123'", str(rows[-1]))

    def test_rerun_does_not_duplicate_rows(self):
        once = self.render()
        twice = render_meeting_document(once, self.report)
        self.assertEqual(len(self.tracking_rows(twice)), 3)

    def test_existing_issue_rows_are_left_alone(self):
        self.report["issues"][0]["id"] = "FRI-001"
        rows = self.tracking_rows(self.render())
        self.assertEqual(rows, self.tracking_rows(self.before))

    def test_missing_section_blocks_render(self):
        del self.before[5:7]
        with self.assertRaisesRegex(ValueError, "CEO本周判断"):
            self.render()

    def test_draft_holds_the_full_seven_sections(self):
        draft = str(render_draft(self.report))
        self.assertIn("五、四条业务线", draft)
        self.assertIn("七、需讨论与决策", draft)


class JsonmlCheckTests(unittest.TestCase):
    def leaf_holding(self, content):
        return [
            "root",
            {},
            ["p", {}, ["span", {"data-type": "text"}, ["span", {"data-type": "leaf"}, content]]],
        ]

    def test_accepts_a_well_formed_document(self):
        self.assertEqual(check_document(self.leaf_holding("正文")), [])

    def test_rejects_an_object_where_text_belongs_with_its_path(self):
        errors = check_document(
            self.leaf_holding({"status": "⚠️", "text": "对象"})
        )
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("$[2][2][2][2]: content must be string"))

    def test_rejects_a_document_without_root(self):
        self.assertTrue(check_document(["p", {}]))

    def test_rejects_a_string_spliced_into_a_block_container(self):
        errors = check_document(["root", {}, "abc", ["p", {}]])
        self.assertEqual(len(errors), 1)
        self.assertIn("bare string", errors[0])

    def test_rejects_a_nested_root(self):
        self.assertTrue(check_document(["root", {}, ["root", {}]]))

    def test_rejects_attrs_after_the_first_child(self):
        self.assertTrue(check_document(["root", {}, ["p", {}, "a", {"x": 1}]]))

    def test_render_writes_nothing_when_a_report_text_is_an_object(self):
        # The 2026-09-26 weekly report: `cockpit` held an object.
        case = RendererTests("test_preserves_content_before_managed_anchor")
        case.setUp()
        case.report["cockpit"] = [{"status": "⚠️", "text": "对象"}]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "before.json").write_text(json.dumps(case.before), encoding="utf-8")
            (root / "report.json").write_text(json.dumps(case.report), encoding="utf-8")
            argv = [
                "render",
                "--before", str(root / "before.json"),
                "--report", str(root / "report.json"),
                "--anchor", "第一部分：指标",
                "--output", str(root / "after.jsonml"),
                "--draft-output", str(root / "draft.jsonml"),
            ]
            with mock.patch("sys.argv", argv), mock.patch("sys.stderr"):
                self.assertEqual(main(), 1)
            self.assertFalse((root / "after.jsonml").exists())
            self.assertFalse((root / "draft.jsonml").exists())
