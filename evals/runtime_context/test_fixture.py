"""Focused guards for the synthetic read boundary used by native evaluation."""

import json
from pathlib import Path
import unittest

from evals.runtime_context.fixture_mcp import response, tool_catalog


MANIFEST = json.loads((Path(__file__).parent / "cases.v1.json").read_text(encoding="utf-8"))


class FixtureTests(unittest.TestCase):
    def setUp(self):
        base = MANIFEST["cases"][0]
        self.case = {**base, "skill_content": MANIFEST["skill_content"]}

    def test_catalog_exposes_only_synthetic_reads(self):
        names = {tool["name"] for tool in tool_catalog(self.case)}
        self.assertEqual(names, {"read_skill", "list_dingtalk_calendar_events",
                                 "read_dingtalk_messages"})
        self.assertTrue(all(tool["annotations"]["readOnlyHint"] for tool in tool_catalog(self.case)))
        self.case["absent_tools"] = ["list_dingtalk_calendar_events"]
        self.assertNotIn("list_dingtalk_calendar_events",
                         {tool["name"] for tool in tool_catalog(self.case)})

    def test_calendar_is_limited_to_explicit_queried_window(self):
        result, error = response(self.case, "list_dingtalk_calendar_events", {
            "start": "2026-10-27T16:00:00Z", "end": "2026-10-27T17:00:00Z"})
        self.assertFalse(error)
        self.assertEqual([event["event_id"] for event in result["events"]],
                         ["synthetic-event-1"])
        result, error = response(self.case, "list_dingtalk_calendar_events", {
            "start": "2026-10-27T16:00:00Z"})
        self.assertTrue(error)
        self.assertEqual(result["code"], "explicit_time_window_required")

    def test_skill_read_returns_synthetic_content_for_declared_skill(self):
        result, error = response(self.case, "read_skill", {
            "path": "/isolated/skills/dingtalk-calendar/SKILL.md"})
        self.assertFalse(error)
        self.assertEqual(result["content"], MANIFEST["skill_content"]["dingtalk-calendar"])


if __name__ == "__main__":
    unittest.main()
