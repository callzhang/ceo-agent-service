import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from scripts.report_contract import (
    reporting_window,
    validate_claim,
    validate_issue,
    validate_issue_continuity,
    validate_manifest,
    validate_metric,
    validate_privacy,
)
from scripts.validate_run import FIXED_METRICS, validate_run


class ReportContractTests(unittest.TestCase):
    def complete_manifest(self):
        return {
            "target": {
                "title": "2026年9月14日管理层周会",
                "node_id": "node",
                "revision": 7,
            },
            "previous": {
                "title": "2026年9月7日管理层周会",
                "node_id": "prev",
            },
            "minutes": {
                "inventory_complete": True,
                "screened_ids": ["m1"],
                "relevant_ids": ["m1"],
                "full_transcript_ids": ["m1"],
            },
            "messages": {
                "inventory_complete": True,
                "selected_ids": ["c1"],
                "context_read_ids": ["c1"],
            },
            "business_reports": {
                "MorningStar": "u1",
                "Friday": "u2",
                "International": "u3",
                "WorldModel": "u4",
            },
        }

    def complete_report(self):
        metrics = [
            {
                "key": key,
                "current": "无数据",
                "decision_critical": False,
            }
            for key in FIXED_METRICS
        ]
        line = {
            "summary": "无数据",
            "core_metrics": "无数据",
            "milestone_deviation": "无数据",
            "largest_risk": "无数据",
            "management_decision": "无",
            "metrics": [],
            "milestones": [],
            "full_report_jsonml": [],
        }
        return {
            "ceo_judgment": [],
            "company_metrics": metrics,
            "claims": [],
            "issues": [],
            "private": {},
            "business_lines": {
                name: dict(line)
                for name in (
                    "MorningStar",
                    "Friday",
                    "International",
                    "WorldModel",
                )
            },
            "cockpit": [],
            "root_causes": [],
            "discussion": [],
            "as_of": "2026-09-27",
        }

    def test_window_uses_previous_beijing_calendar_week(self):
        start, end = reporting_window("2026-09-14", None)
        self.assertEqual(start.isoformat(), "2026-09-07T00:00:00+08:00")
        self.assertEqual(end.isoformat(), "2026-09-14T00:00:00+08:00")

    def test_early_run_uses_actual_cutoff(self):
        cutoff = datetime(
            2026, 9, 11, 21, 0, tzinfo=ZoneInfo("Asia/Shanghai")
        )
        _, end = reporting_window("2026-09-14", cutoff)
        self.assertEqual(end, cutoff)

    def test_complete_manifest_has_no_errors(self):
        self.assertEqual(validate_manifest(self.complete_manifest()), [])

    def test_manifest_rejects_incomplete_pagination(self):
        manifest = self.complete_manifest()
        manifest["minutes"]["inventory_complete"] = False
        self.assertIn(
            "minutes inventory is incomplete", validate_manifest(manifest)
        )

    def test_manifest_requires_context_for_each_selected_message(self):
        manifest = self.complete_manifest()
        manifest["messages"]["context_read_ids"] = []
        self.assertIn(
            "message c1 is missing surrounding context",
            validate_manifest(manifest),
        )

    def test_manifest_requires_full_transcript_for_relevant_minutes(self):
        manifest = self.complete_manifest()
        manifest["minutes"]["full_transcript_ids"] = []
        self.assertIn(
            "relevant minutes m1 is missing full transcript",
            validate_manifest(manifest),
        )

    def test_current_judgment_requires_two_independent_sources(self):
        claim = {
            "type": "current_judgment",
            "text": "销售预测纪律不足",
            "evidence": [
                {"source_kind": "minutes", "source_id": "meeting-1"},
                {"source_kind": "minutes", "source_id": "meeting-1"},
            ],
            "counterevidence": [],
        }
        self.assertIn(
            "current judgment needs two independent sources",
            validate_claim(claim),
        )

    def test_current_judgment_requires_counterevidence_review(self):
        claim = {
            "type": "current_judgment",
            "text": "需求未完成业务收敛",
            "evidence": [
                {"source_kind": "minutes", "source_id": "meeting-1"},
                {"source_kind": "chat", "source_id": "message-1"},
            ],
        }
        self.assertIn(
            "current judgment is missing counterevidence review",
            validate_claim(claim),
        )

    def test_ceo_judgment_requires_verified_derek_statement(self):
        claim = {
            "type": "ceo_confirmed_judgment",
            "text": "Friday进度由CEO统一规划",
            "evidence": [
                {
                    "source_kind": "chat",
                    "source_id": "c1",
                    "speaker_is_derek": False,
                }
            ],
        }
        self.assertIn(
            "CEO judgment lacks a verified Derek statement",
            validate_claim(claim),
        )

    def test_numeric_metric_requires_source_date_and_definition(self):
        errors = validate_metric(
            {"key": "new_signed_orders", "current": 5_000_000, "source": "CRM"}
        )
        self.assertEqual(
            errors,
            [
                "new_signed_orders is missing data date",
                "new_signed_orders is missing definition",
            ],
        )

    def test_decision_critical_missing_metric_requires_native_mention(self):
        errors = validate_metric(
            {
                "key": "payment_collected",
                "current": "无数据",
                "decision_critical": True,
                "responsible_name": "张丽丽(Lily)",
            }
        )
        self.assertEqual(
            errors,
            [
                "payment_collected is missing responsible DingTalk user identity",
                "payment_collected is missing next data checkpoint",
            ],
        )

    def test_decision_critical_missing_metric_requires_next_checkpoint(self):
        errors = validate_metric(
            {
                "key": "recognized_revenue",
                "current": "无数据",
                "decision_critical": True,
                "responsible_user_id": "finance-user-id",
                "responsible_name": "张丽丽(Lily)",
            }
        )
        self.assertEqual(
            errors,
            ["recognized_revenue is missing next data checkpoint"],
        )

    def test_decision_critical_missing_metric_requires_responsible_name(self):
        errors = validate_metric(
            {
                "key": "gross_margin",
                "current": "无数据",
                "decision_critical": True,
                "responsible_user_id": "finance-user-id",
                "next_checkpoint": "2026-09-11 18:00",
            }
        )
        self.assertEqual(
            errors,
            ["gross_margin is missing responsible DingTalk name"],
        )

    def test_private_personnel_detail_is_blocked(self):
        self.assertIn(
            "private field compensation must not be published",
            validate_privacy({"compensation": "100000"}),
        )

    def test_open_prior_issue_cannot_disappear(self):
        previous = [{"id": "FRI-003", "status": "open"}]
        self.assertEqual(
            validate_issue_continuity(previous, []),
            ["open issue FRI-003 disappeared"],
        )

    def test_activity_without_evidence_cannot_close_issue(self):
        issue = {
            "id": "MS-004",
            "status": "closed",
            "result": "已沟通，推进中",
            "closure_evidence": [],
        }
        self.assertIn(
            "closed issue MS-004 lacks outcome evidence",
            validate_issue(issue),
        )

    def test_explicit_overdue_issue_must_be_red_or_closed(self):
        issue = {
            "id": "RD-002",
            "status": "open",
            "severity": "yellow",
            "deadline": "2026-09-10",
            "as_of": "2026-09-13",
        }
        self.assertIn(
            "overdue issue RD-002 must be red or closed",
            validate_issue(issue),
        )

    def test_three_weeks_without_evidence_requires_decision(self):
        issue = {
            "id": "SALES-006",
            "status": "open",
            "weeks_without_evidence": 3,
            "management_decision": "",
        }
        self.assertIn(
            "issue SALES-006 needs continue/change owner/downgrade/stop decision",
            validate_issue(issue),
        )

    def test_complete_run_blocks_missing_fixed_metric(self):
        report = self.complete_report()
        report["company_metrics"] = []
        result = validate_run(self.complete_manifest(), report, [])
        self.assertFalse(result["publishable"])
        self.assertIn(
            "fixed metric new_signed_orders is missing", result["errors"]
        )

    def test_complete_run_requires_all_business_line_views(self):
        report = self.complete_report()
        report["business_lines"].pop("MorningStar")
        result = validate_run(self.complete_manifest(), report, [])
        self.assertIn(
            "business line MorningStar is missing from report",
            result["errors"],
        )

    def test_complete_run_limits_exception_metrics(self):
        report = self.complete_report()
        report["company_metrics"].extend(
            {"key": f"exception_{index}", "current": "无数据"}
            for index in range(4)
        )
        result = validate_run(self.complete_manifest(), report, [])
        self.assertIn(
            "company report has more than 3 exception metrics",
            result["errors"],
        )

    def test_complete_run_is_publishable(self):
        result = validate_run(
            self.complete_manifest(), self.complete_report(), []
        )
        self.assertEqual(result, {"publishable": True, "errors": []})

    def test_cockpit_item_must_be_plain_text(self):
        # 2026-09-26: an object here rendered into a text leaf and the
        # document service rejected the whole overwrite.
        report = self.complete_report()
        report["cockpit"] = [
            "正常一句话",
            {"status": "⚠️", "text": "对象不是合法的段落文本"},
        ]
        result = validate_run(self.complete_manifest(), report, [])
        self.assertFalse(result["publishable"])
        self.assertIn("cockpit[1] must be a string, got dict", result["errors"])

    def test_discussion_and_judgment_shapes_are_checked(self):
        report = self.complete_report()
        report["discussion"] = [{"text": "x"}]
        report["ceo_judgment"] = ["缺少状态的裸字符串"]
        report["root_causes"] = [{"status": "⚠️"}]
        errors = validate_run(self.complete_manifest(), report, [])["errors"]
        self.assertIn("discussion[0] must be a string, got dict", errors)
        self.assertIn(
            "ceo_judgment[0] must be an object with string status/text", errors
        )
        self.assertIn("root_causes[0] must be an object with string text", errors)

    def test_business_line_source_nodes_must_be_valid_jsonml(self):
        report = self.complete_report()
        report["business_lines"]["Friday"]["full_report_jsonml"] = [
            ["p", {}, ["span", {"data-type": "leaf"}, {"text": "x"}]]
        ]
        errors = validate_run(self.complete_manifest(), report, [])["errors"]
        self.assertTrue(
            any(e.startswith("Friday full_report_jsonml[0] $[2][2]") for e in errors)
        )

    def test_business_line_source_must_be_a_list_of_block_nodes(self):
        report = self.complete_report()
        report["business_lines"]["Friday"]["full_report_jsonml"] = '["root",{}]'
        errors = validate_run(self.complete_manifest(), report, [])["errors"]
        self.assertIn("Friday full_report_jsonml must be an array", errors)
        report["business_lines"]["Friday"]["full_report_jsonml"] = ["x"]
        errors = validate_run(self.complete_manifest(), report, [])["errors"]
        self.assertIn("Friday full_report_jsonml[0] must be a block node", errors)


if __name__ == "__main__":
    unittest.main()
