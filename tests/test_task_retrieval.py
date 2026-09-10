import json

from app.store import AutoReplyStore
from app.task_retrieval import (
    load_project_task_detail,
    render_candidate_prompt,
    render_project_task_details,
    retrieve_project_candidates,
    retrieve_project_task_details,
    resolve_task_owner_display,
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


def test_retrieve_project_candidates_prioritizes_structured_project_id(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    authoritative_project_id = store.create_work_project(
        title="实际项目",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
        background="与文本检索词不相似。",
    )
    for index in range(5):
        store.create_work_project(
            title=f"项目闭环 {index}",
            category="projects",
            status="active",
            priority="P2",
            risk_level="low",
            background="项目闭环相关的文本候选。",
        )

    summary = json.dumps(
        {
            "project": {"id": authoritative_project_id},
            "todo": {"title": "推进项目闭环"},
        },
        ensure_ascii=False,
    )

    candidates = retrieve_project_candidates(
        store,
        summary=summary,
        project_name="项目闭环",
        limit=1,
    )

    assert candidates[0].project.id == authoritative_project_id


def test_render_candidate_prompt_returns_project_context_json(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    memory_context = {
        "query": "售前知识库",
        "summary": "已确认的售前知识库背景。",
        "memories": [{"source": "memory_recall", "text": "稳定背景"}],
    }
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
        memory_context_json=json.dumps(memory_context, ensure_ascii=False),
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
    assert payload[0]["memory_context"] == memory_context
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


def test_resolve_task_owner_display_summarizes_multiple_todo_owners(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="多 owner 项目")
    store.create_work_todo(
        project_id=project_id,
        title="任务 A",
        owner_name="周俊杰",
        owner_user_id="owner-1",
        status="open",
    )
    store.create_work_todo(
        project_id=project_id,
        title="任务 B",
        owner_name="张晓民",
        owner_user_id="owner-2",
        status="open",
    )
    store.create_work_todo(
        project_id=project_id,
        title="任务 C",
        owner_name="Mina",
        owner_user_id="owner-3",
        status="open",
    )
    store.create_work_todo(
        project_id=project_id,
        title="任务 D",
        owner_name="ET",
        owner_user_id="owner-4",
        status="open",
    )

    project = store.get_work_project(project_id)
    todos = store.list_work_todos(project_id=project_id)

    assert project is not None
    assert resolve_task_owner_display(project, todos) == "多人：周俊杰、张晓民、Mina 等 4 人"


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


def test_load_project_task_detail_uses_batch_follow_up_lookup(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="批量读取 follow-up",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    todo_one = store.create_work_todo(
        project_id=project_id,
        title="TODO A",
        status="open",
        priority="P1",
    )
    todo_two = store.create_work_todo(
        project_id=project_id,
        title="TODO B",
        status="open",
        priority="P1",
    )
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_one,
        target_conversation_id="cid-a",
        target_kind="group",
        question_text="A?",
        scheduled_at="2026-07-24 10:00:00",
    )
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_two,
        target_conversation_id="cid-b",
        target_kind="group",
        question_text="B?",
        scheduled_at="2026-07-24 11:00:00",
    )

    batch_calls = {"count": 0}
    original_batch = store.list_follow_up_drafts_for_todos

    def wrapped_batch(todo_ids, *, statuses=None):
        batch_calls["count"] += 1
        return original_batch(todo_ids, statuses=statuses)

    def forbidden_follow_up_lookup(*args, **kwargs):
        raise AssertionError("load_project_task_detail should use batch follow-up lookup")

    store.list_follow_up_drafts_for_todos = wrapped_batch  # type: ignore[method-assign]
    store.list_follow_up_drafts = forbidden_follow_up_lookup  # type: ignore[method-assign]

    detail = load_project_task_detail(store, project_id)

    assert detail is not None
    assert batch_calls["count"] == 1
    assert [todo.id for todo in detail.todos] == [todo_one, todo_two]
    assert detail.follow_ups_by_todo[todo_one][0].question_text == "A?"
    assert detail.follow_ups_by_todo[todo_two][0].question_text == "B?"


def test_render_project_task_details_uses_todo_owner_as_project_display_fallback(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
        source_conversations_json=json.dumps(
            [{"id": "cid-delivery", "title": "客户交付群"}],
            ensure_ascii=False,
        ),
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="确认客户验收 ETA",
        owner_user_id="owner-1",
        owner_name="Mina",
        status="open",
        priority="P1",
    )

    details = retrieve_project_task_details(
        store,
        query="客户验收 ETA",
        conversation_id="cid-delivery",
    )
    payload = json.loads(render_project_task_details(details))

    assert payload[0]["project"]["id"] == project_id
    assert payload[0]["project"]["owner"] == "Mina"
    assert payload[0]["todos"][0]["id"] == todo_id
    assert payload[0]["todos"][0]["owner"] == "Mina"
