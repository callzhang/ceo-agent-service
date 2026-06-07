import json

from app.store import AutoReplyStore
from app.task_retrieval import render_candidate_prompt, retrieve_project_candidates


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
