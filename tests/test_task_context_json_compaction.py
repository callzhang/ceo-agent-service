import json

from app.store import AutoReplyStore
from app.task_models import WorkItem
from app.task_retrieval import render_task_semantic_context, retrieve_task_semantic_context


def test_semantic_context_is_compact_and_preserves_exact_source_ranges(tmp_path):
    body = '项目甲：保留 "引号"、\\路径、换行\n下一行的原文，以及🙂和空格。'
    item = WorkItem.model_validate({
        "source": {"type": "reply_attempt", "ref": "message:compact-source"},
        "summary": body,
        "context": {"source_conversation_kind": "group"},
    })
    store = AutoReplyStore(tmp_path / "compact.sqlite3")
    context = retrieve_task_semantic_context(store, item)
    rendered = render_task_semantic_context(context, work_item=item)
    payload = json.loads(rendered)
    document_id = payload["current_work_item"]["document_id"]
    document = next(row for row in payload["source_documents"]
                    if row["document_id"] == document_id)
    assert document["full_length"] == len(body)
    for span in document["visible_ranges"]:
        assert span["text"] == body[span["start"]:span["end"]]
    assert ''.join(span["text"] for span in document["visible_ranges"]) == body
    assert payload["current_work_item"]["source"]["ref"] == "message:compact-source"
    assert "\n" not in rendered
    assert json.loads(json.dumps(payload, ensure_ascii=False, indent=2)) == payload
