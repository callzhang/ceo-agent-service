from __future__ import annotations

import json

from app.dws_client import DwsDocumentSearchResult
from app.store import AutoReplyStore
from app.task_models import WorkItemSourceType
from app.task_report_scanner import (
    REPORT_SCANNER,
    classify_report_title,
    report_period,
    scan_task_reports,
)


class FakeDws:
    def __init__(self) -> None:
        self.page_sizes: list[int] = []
        self.documents = {
            "management": (
                DwsDocumentSearchResult(
                    node_id="management-39",
                    name="2026年9月26日-管理周报收集",
                    extension="adoc",
                    doc_url="https://docs/management-39",
                ),
                DwsDocumentSearchResult(
                    node_id="template",
                    name="管理周报-模版建议",
                    extension="adoc",
                ),
            ),
            "project": (
                DwsDocumentSearchResult(
                    node_id="project-39",
                    name="项目管理部周报-2026-W39",
                    extension="adoc",
                    doc_url="https://docs/project-39",
                ),
            ),
            "department": (
                DwsDocumentSearchResult(
                    node_id="department-39",
                    name="营销线产研管理周报-20260918",
                    extension="adoc",
                    doc_url="https://docs/department-39",
                ),
            ),
        }

    def search_documents(self, query: str, *, page_size: int) -> list[DwsDocumentSearchResult]:
        self.page_sizes.append(page_size)
        return list(self.documents.get({
            "管理周报": "management",
            "项目管理部周报": "project",
            "产研管理周报": "department",
        }[query], ()))

    def read_doc(self, node_id: str) -> dict[str, str]:
        return {
            "markdown": {
                "management-39": "统计周期：2026-09-21 至 2026-09-25\n## Friday\n状态：推进中",
                "project-39": "# 项目管理部周报（2026 年第 39 周）\n\n项目：Einride POC",
                "department-39": "统计周期：2026-09-14 至 2026-09-18\n\n营销项目推进",
            }.get(node_id, ""),
        }


def test_report_title_classification_requires_period_and_keeps_authority_classes():
    assert classify_report_title("管理周报-模版建议") is None
    assert classify_report_title("2026年9月26日-管理周报收集") is WorkItemSourceType.MANAGEMENT_WEEKLY_REPORT
    assert classify_report_title("项目管理部周报-2026-W39") is WorkItemSourceType.PROJECT_WEEKLY_REPORT
    assert classify_report_title("营销线产研管理周报-20260918") is WorkItemSourceType.DEPARTMENT_WEEKLY_REPORT
    assert report_period("项目管理部周报-2026-W39") == "2026-W39"


def test_scan_task_reports_is_idempotent_and_preserves_report_provenance(tmp_path):
    store = AutoReplyStore(tmp_path / "task-reports.sqlite3")
    dws = FakeDws()

    assert scan_task_reports(store, dws) == 3
    assert scan_task_reports(store, dws) == 0
    assert dws.page_sizes == [30, 30, 30, 30, 30, 30]

    with store._connect() as db:
        rows = db.execute(
            "select source_type, source_ref, payload_json from work_summary_inputs order by id"
        ).fetchall()
    assert {row["source_type"] for row in rows} == {
        WorkItemSourceType.MANAGEMENT_WEEKLY_REPORT.value,
        WorkItemSourceType.PROJECT_WEEKLY_REPORT.value,
        WorkItemSourceType.DEPARTMENT_WEEKLY_REPORT.value,
    }
    management = next(row for row in rows if row["source_type"] == WorkItemSourceType.MANAGEMENT_WEEKLY_REPORT.value)
    payload = json.loads(management["payload_json"])
    assert payload["source"]["ref"].startswith("dingtalk-doc:management-39#sha256=")
    assert payload["source"]["title"] == "2026年9月26日-管理周报收集"
    assert "Friday" in json.loads(payload["summary"])["markdown"]
    state = store.get_daily_scan_state(REPORT_SCANNER)
    assert state is not None
    assert state["last_error"] == ""
