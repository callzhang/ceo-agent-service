import json
from pathlib import Path

from app.store import AutoReplyStore
from app.task_models import WorkItem


def _store(tmp_path: Path) -> AutoReplyStore:
    return AutoReplyStore(tmp_path / "task.sqlite3")


def _work_item() -> WorkItem:
    return WorkItem.model_validate(
        {
            "source": {
                "type": "reply_attempt",
                "ref": "1",
                "title": "项目消息",
                "conversation_id": "cid-1",
                "conversation_title": "项目群",
                "created_at": "2026-06-07 09:00:00",
            },
            "summary": "P1 项目需要三天内确认进展。",
            "project_name": "P1 项目",
            "context": {
                "sender": "Alex",
                "participants": ["Alex"],
                "source_conversation_kind": "group",
                "source_conversation_title": "项目群",
            },
        }
    )


def test_enqueue_and_claim_work_summary_input(tmp_path):
    store = _store(tmp_path)
    item = _work_item()

    inserted_id = store.enqueue_work_summary_input(
        source_type=item.source.type.value,
        source_ref=item.source.ref,
        payload_json=item.model_dump_json(),
    )
    duplicate_id = store.enqueue_work_summary_input(
        source_type=item.source.type.value,
        source_ref=item.source.ref,
        payload_json=item.model_dump_json(),
    )

    assert inserted_id > 0
    assert duplicate_id == inserted_id

    claimed = store.claim_work_summary_inputs(limit=1)
    second_claim = store.claim_work_summary_inputs(limit=1)

    assert len(claimed) == 1
    assert claimed[0].status == "processing"
    assert second_claim == []


def test_create_project_todo_update_and_follow_up(tmp_path):
    store = _store(tmp_path)

    project_id = store.create_work_project(
        title="售前知识库建设",
        category="sales",
        tags_json=json.dumps(["售前"], ensure_ascii=False),
        status="active",
        priority="P1",
        risk_level="medium",
        needs_derek_attention=True,
        owner_user_id="owner-1",
        owner_name="Alex",
        goal="沉淀售前材料",
        background="销售支持项目。",
        facts_json=json.dumps(
            [
                {
                    "description": "正式本地知识导入位置是 business/售前知识库。",
                    "source": "memory_recall",
                    "created": "2026-06-05",
                    "updated": "2026-06-07",
                }
            ],
            ensure_ascii=False,
        ),
        current_state="开始整理",
        next_step="补齐来源链接",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="补齐来源链接",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
        priority="P1",
        next_follow_up_at="2026-06-10 09:00:00",
    )
    update_id = store.create_work_update(
        project_id=project_id,
        source_type="reply_attempt",
        source_ref="1",
        summary="创建项目和行动项",
        changes_json=json.dumps({"created_todo_id": todo_id}),
        merge_reason="新项目信息明确",
        confidence=0.91,
    )
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="来源链接补齐到哪一步了？",
        risk_check_json=json.dumps({"owner_in_group": True}),
        scheduled_at="2026-06-10 09:00:00",
    )

    project = store.get_work_project(project_id)
    assert project is not None
    assert project.title == "售前知识库建设"
    assert project.category == "sales"
    assert project.needs_derek_attention is True
    assert json.loads(project.facts_json)[0]["description"].startswith("正式本地")

    todos = store.list_work_todos(project_id=project_id)
    assert [todo.id for todo in todos] == [todo_id]

    updates = store.list_work_updates(project_id=project_id)
    assert [update.id for update in updates] == [update_id]

    drafts = store.list_follow_up_drafts(statuses=("draft",))
    assert [draft.id for draft in drafts] == [draft_id]


def test_scan_state_round_trip(tmp_path):
    store = _store(tmp_path)

    store.set_daily_scan_state(
        "local_files",
        last_success_at="2026-06-07T10:00:00+00:00",
        cursor_json='{"mtime": 123}',
        last_error="",
    )

    state = store.get_daily_scan_state("local_files")
    assert state is not None
    assert state["last_success_at"] == "2026-06-07T10:00:00+00:00"
    assert state["cursor_json"] == '{"mtime": 123}'
