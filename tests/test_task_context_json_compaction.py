import json

from app.store import AutoReplyStore
from app.task_models import WorkItem
from app.task_retrieval import render_task_semantic_context, retrieve_task_semantic_context


def test_semantic_context_is_compact_and_does_not_repeat_the_work_item(tmp_path):
    body = '项目甲：保留 "引号"、\\路径、换行\n下一行的原文，以及🙂和空格。'
    item = WorkItem.model_validate({
        "source": {"type": "reply_attempt", "ref": "message:compact-source"},
        "summary": body,
        "context": {"source_conversation_kind": "group"},
    })
    store = AutoReplyStore(tmp_path / "compact.sqlite3")
    context = retrieve_task_semantic_context(store, item)
    rendered = render_task_semantic_context(context)
    payload = json.loads(rendered)
    # The Work Item's own text travels in the Work Item JSON, not in this context.
    assert "current_work_item" not in payload
    assert "source_metrics" not in payload
    assert payload["source_documents"] == []
    assert body not in rendered
    assert "\n" not in rendered
    assert json.loads(json.dumps(payload, ensure_ascii=False, indent=2)) == payload
