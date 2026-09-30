import json

from app.store import AutoReplyStore
from app.task_agent import apply_task_agent_decision
from app.task_models import TaskAgentDecision, WorkItem
from app.web_api.tasks import business_project_detail


def test_project_summary_reads_latest_report_registry_fields(tmp_path):
    store = AutoReplyStore(tmp_path / "project-summary.sqlite3")
    row = "| Example project | Data delivery and acceptance | Complete customer acceptance | 2026-10-03 | ⚠️ at risk |"
    markdown = (
        "## **手头项目**\n\n"
        "| 项目名 | 负责内容 | 目标 | DDL | 状态 |\n"
        "|---|---|---|---|---|\n"
        f"{row}\n"
    )
    item = WorkItem.model_validate({
        "source": {
            "type": "project_weekly_report",
            "ref": "report:project-summary",
            "title": "项目管理部周报｜2026-W40",
        },
        "summary": json.dumps({
            "report": {
                "title": "项目管理部周报｜2026-W40",
                "url": "https://example.test/report",
                "reporting_period": "2026-W40",
            },
            "markdown": markdown,
        }, ensure_ascii=False),
        "context": {"source_conversation_kind": "group"},
    })
    decision = TaskAgentDecision.model_validate({"task_decisions": [{
        "action": "record_candidate",
        "transition": "none",
        "source_excerpt": row,
        "source_ref": item.source.ref,
        "title": "完成 Example project 客户验收",
        "missing_evidence": ["owner"],
    }]})

    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )
    project = store.list_business_projects()[0]
    detail = business_project_detail(store, project.id)

    assert result.task_ids
    assert detail is not None
    summary = detail.summary
    assert summary.responsible_content == "Data delivery and acceptance"
    assert summary.goal == "Complete customer acceptance"
    assert summary.deadline == "2026-10-03"
    assert summary.current_status == "⚠️ at risk"
    assert summary.source_title == "项目管理部周报｜2026-W40"
    assert summary.reporting_period == "2026-W40"
    assert summary.source_url == "https://example.test/report"
    assert summary.source_excerpt == row
    assert summary.open_task_count == 1
    assert summary.done_task_count == 0
