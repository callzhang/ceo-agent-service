import json

from app.task_source_projection_sharing import share_source_document_projections
from app.store import AutoReplyStore
from app.task_agent import build_task_agent_prompt
from app.task_models import WorkItem
from app.task_retrieval import render_task_semantic_context, retrieve_task_semantic_context


def _expanded_documents(payload):
    by_id = {
        document["document_id"]: document
        for document in payload["source_documents"]
    }
    expanded = []
    for document in payload["source_documents"]:
        value = dict(document)
        source_id = value.pop("duplicate_of_document_id", None)
        if source_id is not None:
            source = by_id[source_id]
            value["visible_ranges"] = source["visible_ranges"]
            value["decoded_excerpts"] = source["decoded_excerpts"]
        expanded.append(value)
    return expanded


def test_shares_exact_projection_without_merging_source_identity_or_metadata():
    visible_ranges = [{"start": 0, "end": 5, "text": "事实原文"}]
    decoded_excerpts = [{"path": ["owner"], "start": 0, "end": 2, "text": "甲"}]
    source_documents = [
        {
            "document_id": 10,
            "full_length": 2048,
            "truncated": True,
            "visible_ranges": visible_ranges,
            "decoded_excerpts": decoded_excerpts,
            "citation_budget_exceeded": False,
        },
        {
            "document_id": 11,
            "full_length": 4096,
            "truncated": True,
            "visible_ranges": visible_ranges,
            "decoded_excerpts": decoded_excerpts,
            "citation_budget_exceeded": True,
        },
    ]
    original = {
        "source_documents": source_documents,
        "source_signals": [
            {"id": 21, "document_id": 10, "source_ref": "source:a"},
            {"id": 22, "document_id": 11, "source_ref": "source:b"},
        ],
        "citations": [{"signal_id": 22, "source_excerpt": "甲"}],
    }

    compacted = share_source_document_projections(original)

    assert compacted["source_documents"][1]["duplicate_of_document_id"] == 10
    assert _expanded_documents(compacted) == source_documents
    assert compacted["source_signals"] == original["source_signals"]
    assert compacted["citations"] == original["citations"]
    assert compacted["source_documents"][1]["full_length"] == 4096
    assert compacted["source_documents"][1]["citation_budget_exceeded"] is True
    assert len(json.dumps(compacted, ensure_ascii=False)) < len(
        json.dumps(original, ensure_ascii=False)
    )


def test_does_not_share_different_or_empty_projections():
    original = {
        "source_documents": [
            {"document_id": 1, "visible_ranges": [], "decoded_excerpts": []},
            {"document_id": 2, "visible_ranges": [], "decoded_excerpts": []},
            {
                "document_id": 3,
                "visible_ranges": [{"start": 0, "end": 1, "text": "甲"}],
                "decoded_excerpts": [],
            },
            {
                "document_id": 4,
                "visible_ranges": [{"start": 0, "end": 1, "text": "乙"}],
                "decoded_excerpts": [],
            },
        ]
    }

    assert share_source_document_projections(original) is original


def test_semantic_context_renders_pointer_and_prompt_explains_lookup(
    monkeypatch, tmp_path
):
    import app.task_retrieval as retrieval

    ranges = [{"start": 0, "end": 120, "text": "同一事实" * 15}]
    bundle = {
        "source_documents": [
            {
                "document_id": 10,
                "full_length": 2048,
                "truncated": True,
                "visible_ranges": ranges,
                "decoded_excerpts": [],
                "citation_budget_exceeded": False,
            },
            {
                "document_id": 11,
                "full_length": 4096,
                "truncated": True,
                "visible_ranges": ranges,
                "decoded_excerpts": [],
                "citation_budget_exceeded": True,
            },
        ],
        "source_signals": [
            {"id": 21, "document_id": 10},
            {"id": 22, "document_id": 11},
        ],
        "current_work_item": {"document_id": 10},
        "source_citations": [],
    }
    monkeypatch.setattr(retrieval, "source_bundle", lambda *args, **kwargs: bundle)
    store = AutoReplyStore(tmp_path / "projection.sqlite3")
    item = WorkItem.model_validate(
        {
            "source": {"type": "reply_attempt", "ref": "message:current"},
            "summary": "本次工作项正文",
            "context": {"source_conversation_kind": "group"},
        }
    )
    context = retrieve_task_semantic_context(store, item)

    rendered = render_task_semantic_context(context)
    payload = json.loads(rendered)
    prompt = build_task_agent_prompt(item, rendered)

    assert payload["source_documents"][1]["duplicate_of_document_id"] == 10
    assert _expanded_documents(payload) == bundle["source_documents"]
    assert payload["source_signals"] == bundle["source_signals"]
    assert "duplicate_of_document_id" in prompt
    assert "its source identity stays" in prompt
