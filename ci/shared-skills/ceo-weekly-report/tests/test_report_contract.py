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
from scripts.validate_run import validate_render_input

FIXED_METRICS = ("example_a", "example_b")


class ReportContractTests(unittest.TestCase):
    def complete_manifest(self):
        return {
            "target": {
                "title": "2026年9月14日管理层周会",
                "node_id": "node",
                "revision": 7,
            "exists": True,
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
                "name": key,
                "target": "无数据",
                "previous": "无数据",
                "current": "无数据",
                "change": "无数据",
                "status": "❓",
                "data_date": "2026-09-27",
                "definition_evidence": "synthetic fixture",
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

    def test_missing_target_needs_title_and_resolved_creation_parent(self):
        manifest = self.complete_manifest()
        manifest["target"] = {
            "title": "2026年9月14日管理层周会",
            "exists": False,
            "year_page_id": "parent",
        }
        self.assertEqual(validate_manifest(manifest), [])

    def test_missing_target_requires_resolved_creation_parent(self):
        manifest = self.complete_manifest()
        manifest["target"] = {
            "title": "2026年9月14日管理层周会",
            "exists": False,
        }
        self.assertIn(
            "target creation parent is incomplete", validate_manifest(manifest)
        )

    def test_existing_target_requires_integer_revision_and_identity(self):
        manifest = self.complete_manifest()
        manifest["target"] = {
            "title": "2026年9月14日管理层周会",
            "node_id": "node",
            "revision": -1,
            "exists": True,
        }
        self.assertIn(
            "target identity or revision is incomplete", validate_manifest(manifest)
        )

    def test_existing_target_accepts_provider_revision_string(self):
        manifest = self.complete_manifest()
        manifest["target"]["revision"] = "7"
        self.assertEqual(validate_manifest(manifest), [])

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
            "CEO judgment lacks a verified principal statement",
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

    def test_a_metric_without_data_is_never_an_error(self):
        # the principal 2026-10-26: no data does not block the report, not even for a
        # decision-critical metric with no named owner or checkpoint.
        self.assertEqual(
            validate_metric(
                {"key": "payment_collected", "current": "无数据", "decision_critical": True}
            ),
            [],
        )

    def test_a_business_line_without_a_report_is_not_a_manifest_error(self):
        manifest = self.complete_manifest()
        manifest["business_reports"] = {}
        self.assertEqual(validate_manifest(manifest), [])

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

    def test_missing_business_metrics_are_a_skill_review_decision(self):
        report = self.complete_report()
        report["company_metrics"] = []
        self.assertEqual(validate_render_input(report), [])

    def test_missing_renderer_field_is_a_mechanical_error(self):
        report = self.complete_report()
        del report["company_metrics"][0]["name"]
        self.assertIn("company_metrics[0] is missing renderer field name", validate_render_input(report))

    def test_issue_identity_is_checked_but_the_owner_is_optional(self):
        report = self.complete_report()
        report["issues"] = [{"owner": {}}]
        errors = validate_render_input(report)
        self.assertIn("issues[0] is missing renderer field id", errors)
        self.assertIn("issues[0] is missing renderer field question", errors)
        self.assertFalse([e for e in errors if "owner" in e])
        report["issues"] = [{"id": "SALES-001", "question": "q", "owner": "Lily"}]
        self.assertIn("issues[0].owner must be an object when given", validate_render_input(report))

    def test_no_fixed_business_line_list_is_required_for_meeting_render(self):
        report = {"ceo_judgment": [], "company_metrics": [], "issues": []}
        self.assertEqual(validate_render_input(report), [])

    def test_company_metric_shape_is_checked_without_business_thresholds(self):
        report = self.complete_report()
        self.assertEqual(validate_render_input(report), [])
        report["company_metrics"] = {"metric": {}}
        self.assertIn("company_metrics must be an array", validate_render_input(report))


if __name__ == "__main__":
    unittest.main()
