import json

from app.store import AutoReplyStore
from app.task_retrieval import (
    render_candidate_prompt,
    render_project_task_details,
    retrieve_project_candidates,
    retrieve_project_task_details,
)


def test_retrieve_project_candidates_uses_summary_and_project_name(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    sales_project_id = store.create_work_project(
        title="售前知识库建设",
        category="sales",
        tags_json=json.dumps(["售前", "知识库"], ensure_ascii=False),
        status="active",
        priority="P1",
        risk_level="medium",
        background="复用售前材料和来源链接。",
        facts_json=json.dumps(
            [{"description": "材料放在 business/售前知识库", "source": "memory"}],
            ensure_ascii=False,
        ),
        current_state="正在整理",
    )
    store.create_work_project(
        title="招聘复盘",
        category="recruiting",
        tags_json=json.dumps(["招聘"], ensure_ascii=False),
        status="active",
        priority="P2",
        risk_level="low",
        background="候选人流程复盘。",
    )

    candidates = retrieve_project_candidates(
        store,
        summary="售前材料来源链接需要 owner 补齐",
        project_name="售前知识库",
        limit=3,
    )

    assert candidates[0].project.id == sales_project_id
    assert "business/售前知识库" in candidates[0].document
    assert candidates[0].score > 0


def test_render_candidate_prompt_returns_project_context_json(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    store.create_work_project(
        title="售前知识库建设",
        category="sales",
        tags_json=json.dumps(["售前", "知识库"], ensure_ascii=False),
        status="active",
        priority="P1",
        risk_level="medium",
        owner_name="Alex",
        goal="沉淀可复用售前材料",
        background="复用售前材料和来源链接。",
        facts_json=json.dumps(
            [{"description": "材料放在 business/售前知识库", "source": "memory"}],
            ensure_ascii=False,
        ),
        source_conversations_json=json.dumps(
            [{"id": "cid-1", "title": "售前项目群"}],
            ensure_ascii=False,
        ),
    )

    candidates = retrieve_project_candidates(
        store,
        summary="售前材料来源链接需要 owner 补齐",
        project_name="售前知识库",
    )

    payload = json.loads(render_candidate_prompt(candidates))
    assert payload[0]["category"] == "sales"
    assert payload[0]["title"] == "售前知识库建设"
    assert payload[0]["facts"][0]["source"] == "memory"
    assert payload[0]["source_conversations"][0]["id"] == "cid-1"


def test_retrieve_project_candidates_excludes_archived_and_done_projects(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    store.create_work_project(
        title="售前知识库归档",
        category="sales",
        tags_json=json.dumps(["售前"], ensure_ascii=False),
        status="archived",
        priority="P1",
        risk_level="low",
        background="归档项目。",
    )
    store.create_work_project(
        title="售前知识库完成",
        category="sales",
        tags_json=json.dumps(["售前"], ensure_ascii=False),
        status="done",
        priority="P1",
        risk_level="low",
        background="完成项目。",
    )

    candidates = retrieve_project_candidates(
        store,
        summary="售前知识库",
        project_name="售前",
    )

    assert candidates == []


def test_retrieve_project_candidates_returns_empty_for_empty_query_or_no_projects(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")

    assert retrieve_project_candidates(store, summary="", project_name="") == []

    store.create_work_project(
        title="售前知识库建设",
        category="sales",
        tags_json=json.dumps(["售前"], ensure_ascii=False),
        status="active",
        priority="P1",
        risk_level="low",
        background="复用售前材料。",
    )

    assert retrieve_project_candidates(store, summary="", project_name="") == []
    assert (
        retrieve_project_candidates(
            store,
            summary="售前知识库",
            project_name="",
            limit=0,
        )
        == []
    )


def test_retrieve_project_task_details_expands_group_matched_project(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="技术部招聘",
        category="recruiting",
        status="active",
        priority="P1",
        risk_level="medium",
        owner_user_id="hr-owner",
        owner_name="Mina",
        goal="招聘关键技术岗位",
        background="技术部候选人推进。",
        current_state="候选人评估中",
        next_step="确认售前解决方案候选人复试结论",
        source_conversations_json=json.dumps(
            [{"id": "cid-hiring", "title": "技术部招聘群"}],
            ensure_ascii=False,
        ),
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="评估 Colin 售前解决方案候选人",
        description="确认技术面、售前方案能力、薪资预期和下一轮安排。",
        owner_user_id="hr-owner",
        owner_name="Mina",
        priority="P1",
        deadline_at="2026-07-25 18:00:00",
        next_follow_up_at="2026-07-24 15:00:00",
        follow_up_question="Colin 的复试结论和下一步安排定了吗？",
    )
    follow_up_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="hr-owner",
        owner_name="Mina",
        target_conversation_id="cid-hiring",
        target_kind="group",
        question_text="Colin 的复试结论和下一步安排定了吗？",
        scheduled_at="2026-07-24 15:00:00",
    )
    store.create_work_update(
        project_id=project_id,
        source_type="reply_attempt",
        source_ref="123",
        summary="新增 Colin 候选人评估 TODO",
        changes_json=json.dumps({"todo_id": todo_id}, ensure_ascii=False),
        confidence=0.91,
    )

    details = retrieve_project_task_details(
        store,
        query="这个任务现在是什么状态？",
        conversation_id="cid-hiring",
        owner_user_id="hr-owner",
    )
    rendered = render_project_task_details(details)
    payload = json.loads(rendered)

    assert payload[0]["project"]["id"] == project_id
    assert "source_conversation_match" in payload[0]["match"]["reasons"]
    assert payload[0]["todos"][0]["id"] == todo_id
    assert payload[0]["todos"][0]["deadline_at"] == "2026-07-25 18:00:00"
    assert payload[0]["todos"][0]["follow_ups"][0]["id"] == follow_up_id
    assert payload[0]["recent_updates"][0]["summary"] == "新增 Colin 候选人评估 TODO"
