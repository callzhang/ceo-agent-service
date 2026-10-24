import hashlib
import json
from dataclasses import replace

import pytest

from app.task_source_documents import source_contains_quote, source_document_key
from app import task_source_documents
from app.store import AutoReplyStore
from app.task_models import WorkItem
from app.task_semantic_models import SourceCitation
from app.business_source_reader import (
    BusinessTaskSignalFact, PreparedSources, RequestSourceReader,
    SourceUnavailableReason, SourceVersionBinding,
)
from app.task_semantic_models import BusinessTaskSignal


SOURCE = {
    "source_type": "message",
    "source_ref": "m:1",
    "source_time": "2026-10-04T12:00:00+08:00",
    "conversation_id": "c1",
    "author_user_id": "u1",
    "author_name": "张三",
    "author_kind": "human",
    "evidence_text": "李四负责材料，王五负责商务。\n",
}


def _bundle(store, signals, *, bodies=None, **kwargs):
    prepared = PreparedSources.load(store, (signal.id for signal in signals))
    # Body availability is an explicit request-local test input; the database
    # contains only the exact version binding, never the original text.
    for signal in signals:
        if bodies is not None and signal.id in bodies:
            prepared.add_current(signal, bodies[signal.id])
    return task_source_documents.source_bundle(
        signals,
        prepared_sources=prepared,
        **kwargs,
    )


def test_unavailable_original_keeps_saved_quote_and_unknown_character_length():
    signal = BusinessTaskSignal(
        id=9, **SOURCE,
        conversation_title="", context_json="{}", dedupe_key="saved:9",
        created_at="2026-10-18T01:00:00Z",
    )
    fact = BusinessTaskSignalFact.from_signal(
        signal, binding=SourceVersionBinding.from_signal(signal)
    )
    prepared = PreparedSources(RequestSourceReader())
    prepared.add(fact)
    result = task_source_documents.source_bundle(
        (fact,), prepared_sources=prepared,
        citations=(SourceCitation(signal_id=9, source_ref="m:1", source_excerpt="李四负责材料"),),
    )
    doc = result["source_documents"][0]
    assert doc["document_id"] == fact.source.identity_key
    assert doc["source_unavailable_reason"] == SourceUnavailableReason.MISSING_LOCATOR.value
    assert doc["saved_quotations"] == ["李四负责材料"]
    assert doc["full_length"] is None
    assert doc["body_utf8_bytes"] == len(signal.evidence_text.encode("utf-8"))
    assert "source_metrics" not in result


def test_conflicting_bindings_with_same_key_cannot_borrow_first_original():
    signal = BusinessTaskSignal(
        id=9, **SOURCE,
        conversation_title="", context_json="{}", dedupe_key="saved:9",
        created_at="2026-10-18T01:00:00Z",
    )
    fact = BusinessTaskSignalFact.from_signal(
        signal, binding=SourceVersionBinding.from_signal(signal)
    )
    conflicting = replace(
        fact, id=10, dedupe_key="saved:10",
        source=replace(fact.source, provider_locator_json="{}"),
    )
    prepared = PreparedSources(RequestSourceReader())
    prepared.add_current(fact, signal.evidence_text)
    prepared.add(conflicting)

    documents = task_source_documents.source_bundle(
        (fact, conflicting), prepared_sources=prepared
    )

    (document,) = documents["source_documents"]
    assert document["source_available"] is False
    assert document["source_unavailable_reason"] == "binding_conflict"
    assert document["visible_ranges"] == []


def test_source_document_key_is_exact_utf8_json_identity():
    encoded = json.dumps(
        list(SOURCE.values()), ensure_ascii=False, separators=(",", ":")
    )
    assert (
        source_document_key(**SOURCE)
        == hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    )
    assert source_document_key(**SOURCE) == source_document_key(**dict(SOURCE))


@pytest.mark.parametrize(
    "change",
    [
        {"source_ref": "m:2"},
        {"source_type": "meeting"},
        {"source_type": "memory_provenance"},
        {"source_type": "session_provenance"},
        {"source_time": "2026-10-04T04:00:00Z"},
        {"conversation_id": "c2"},
        {"author_user_id": "u2"},
        {"author_name": "李四"},
        {"author_kind": "system"},
        {"evidence_text": "李四已提交材料，王五负责商务。\n"},
        {"evidence_text": SOURCE["evidence_text"].strip()},
        {"evidence_text": " " + SOURCE["evidence_text"]},
    ],
)
def test_source_document_key_separates_every_source_version_field(change):
    assert source_document_key(**SOURCE) != source_document_key(**(SOURCE | change))


def test_source_contains_quote_accepts_one_decoded_nested_string_but_not_joined_fields():
    raw = json.dumps(
        {"report": {"line": "张三负责\n交付", "next": "李四负责材料"}},
        ensure_ascii=True,
    )
    assert source_contains_quote(raw, "张三负责\n交付")
    assert not source_contains_quote(raw, "张三负责\n交付李四负责材料")
    assert not source_contains_quote(raw, " ")


def test_shared_source_bundle_keeps_every_signal_and_one_body_per_exact_version(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "bundle.sqlite3")
    ids = [
        store.create_business_task_signal(**SOURCE, dedupe_key=f"s:{i}")
        for i in range(80)
    ]
    signals = tuple(store.get_business_task_signal(id) for id in ids)
    result = _bundle(store, signals, bodies={signal.id: SOURCE["evidence_text"] for signal in signals})
    assert len(result["source_documents"]) == 1
    assert len(result["source_signals"]) == 80
    doc = result["source_documents"][0]
    assert doc["document_id"] == signals[0].source.identity_key
    assert doc["visible_ranges"] == [
        {
            "start": 0,
            "end": len(SOURCE["evidence_text"]),
            "text": SOURCE["evidence_text"],
        }
    ]
    # The source's own fields are written once, on its document.
    assert doc["doc"] == 1
    assert doc["source_ref"] == SOURCE["source_ref"]
    assert doc["author_name"] == "张三"
    assert doc["source_time"] == SOURCE["source_time"]
    for signal, reference in zip(signals, result["source_signals"]):
        assert "evidence_text" not in reference
        assert reference == {
            "id": signal.id, "doc": 1,
            "context_json": signal.context_json, "created_at": signal.created_at,
        }
    assert "source_metrics" not in result


def test_shared_source_bundle_separates_versions_and_provenance(tmp_path):
    store = AutoReplyStore(tmp_path / "versions.sqlite3")
    signals = []
    for i, changes in enumerate(
        (
            {},
            {"evidence_text": "新版本"},
            {"source_type": "memory_provenance"},
            {"author_user_id": "u2"},
        )
    ):
        id = store.create_business_task_signal(
            **(SOURCE | changes), dedupe_key=f"v:{i}"
        )
        signals.append(store.get_business_task_signal(id))
    bundle = _bundle(store, signals)
    assert len(bundle["source_documents"]) == 4
    assert len({r["doc"] for r in bundle["source_signals"]}) == 4
    assert [d["doc"] for d in bundle["source_documents"]] == [1, 2, 3, 4]
    assert [r["doc"] for r in bundle["source_signals"]] == [1, 2, 3, 4]


def test_agent_source_projection_preserves_provenance_without_storage_dedupe_key(tmp_path):
    store = AutoReplyStore(tmp_path / "signal-context.sqlite3")
    dedupe_key = "message:m:1:task-item:" + "a" * 64
    signal_id = store.create_business_task_signal(**SOURCE, dedupe_key=dedupe_key)
    signal = store.get_business_task_signal(signal_id)
    result = _bundle(store, (signal,), bodies={signal.id: SOURCE["evidence_text"]})
    reference = result["source_signals"][0]
    assert "dedupe_key" not in reference
    document = result["source_documents"][0]
    assert document["document_id"] == signal.source.identity_key
    assert reference["doc"] == document["doc"]
    assert "source_ref" not in reference
    assert document["source_ref"] == signal.source_ref
    assert result["source_documents"][0]["source_available"] is True
    assert result["source_documents"][0]["visible_ranges"][0]["text"] == SOURCE["evidence_text"]
    with store._connect() as db:
        assert store.get_business_task_signal_by_dedupe_key(dedupe_key=dedupe_key, _db=db).id == signal_id
    assert store.get_business_task_signal(signal_id).dedupe_key == dedupe_key


def test_the_current_work_item_is_not_a_source_document_of_its_own(tmp_path):
    store = AutoReplyStore(tmp_path / "current.sqlite3")
    id = store.create_business_task_signal(
        source_type="reply_attempt",
        source_ref="m:1",
        source_time="t1",
        conversation_id="c1",
        author_user_id="u1",
        author_name="张三",
        author_kind="human",
        evidence_text="本次原文唯一正文",
        dedupe_key="one",
    )
    signal = store.get_business_task_signal(id)

    result = _bundle(store, (signal,), bodies={signal.id: "本次原文唯一正文"})

    # The Work Item's text travels in the Work Item JSON; the bundle only holds
    # sources some Signal cites, once each, and never a second copy of the item.
    assert "current_work_item" not in result
    assert len(result["source_documents"]) == 1
    assert result["source_documents"][0]["document_id"] == signal.source.identity_key


def test_a_source_with_an_external_locator_is_left_for_the_agent_to_read(tmp_path):
    store = AutoReplyStore(tmp_path / "located.sqlite3")
    id = store.create_business_task_signal(**SOURCE, dedupe_key="located")
    signal = store.get_business_task_signal(id)
    located = replace(
        signal, source=replace(signal.source, provider_locator_json='{"kind":"ai_minutes"}')
    )
    prepared = PreparedSources(RequestSourceReader())

    result = task_source_documents.source_bundle(
        (located,), prepared_sources=prepared,
        citations=(SourceCitation(signal_id=id, source_ref="m:1", source_excerpt="李四负责材料"),),
    )

    (document,) = result["source_documents"]
    assert document == {
        "doc": 1,
        "document_id": located.source.identity_key,
        **{name: SOURCE[name] for name in (
            "source_type", "source_ref", "source_time", "conversation_id",
            "author_user_id", "author_name", "author_kind",
        )},
        "conversation_title": "",
        "source_in_input": False,
        "body_utf8_bytes": located.source.body_utf8_bytes,
        "saved_quotations": ["李四负责材料"],
    }
    # Nothing was read to build it: the service reads a source only when a
    # decision cites it, before it writes.
    assert prepared.readings == {}
    assert document["source_ref"] == "m:1"
    assert result["source_signals"][0]["doc"] == document["doc"]


def test_a_signal_repeats_only_the_source_fields_that_differ_from_its_document(tmp_path):
    store = AutoReplyStore(tmp_path / "override.sqlite3")
    ids = [
        store.create_business_task_signal(
            **SOURCE, conversation_title=title, dedupe_key=f"t:{i}"
        )
        for i, title in enumerate(("项目群", "项目群", "改名后的项目群"))
    ]
    signals = tuple(store.get_business_task_signal(id) for id in ids)

    result = _bundle(store, signals)

    (document,) = result["source_documents"]
    assert document["conversation_title"] == "项目群"
    assert [
        {name: value for name, value in reference.items() if name.startswith("conversation")}
        for reference in result["source_signals"]
    ] == [{}, {}, {"conversation_title": "改名后的项目群"}]
    # Every Signal still resolves to its full original metadata.
    for signal, reference in zip(signals, result["source_signals"]):
        resolved = {**document, **reference}
        assert resolved["conversation_title"] == signal.conversation_title
        assert resolved["source_ref"] == signal.source_ref
        assert resolved["author_name"] == signal.author_name


def test_shared_source_bundle_reports_real_ranges_and_pins_middle_citation(tmp_path):
    store = AutoReplyStore(tmp_path / "ranges.sqlite3")
    body = "开头" + "甲" * 5000 + "王五负责商务和回款。" + "乙" * 5000 + "结尾"
    id = store.create_business_task_signal(
        source_type="meeting",
        source_ref="m:long",
        evidence_text=body,
        dedupe_key="long",
    )
    signal = store.get_business_task_signal(id)
    quote = SourceCitation(
        signal_id=id, source_ref="m:long", source_excerpt="王五负责商务和回款。"
    )
    bundle = _bundle(store, (signal,), bodies={signal.id: body}, citations=(quote,))
    doc = bundle["source_documents"][0]
    assert doc["full_length"] == len(body)
    assert doc["truncated"] is True
    ranges = doc["visible_ranges"]
    assert all(r["text"] == body[r["start"] : r["end"]] for r in ranges)
    assert ranges[0]["start"] == 0 and ranges[-1]["end"] == len(body)
    assert any(quote.source_excerpt in r["text"] for r in ranges)
    assert sum(len(r["text"]) for r in ranges) <= 2048
    assert any(a["end"] < b["start"] for a, b in zip(ranges, ranges[1:]))


def test_shared_source_bundle_locates_decoded_quote_without_joining_json_fields(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "decoded.sqlite3")
    raw = json.dumps(
        {
            "padding": "甲" * 5000,
            "report": {"line": "张三负责\n交付", "next": "李四负责材料"},
        },
        ensure_ascii=True,
    )
    id = store.create_business_task_signal(
        source_type="meeting", source_ref="json:1", evidence_text=raw, dedupe_key="json"
    )
    citation = SourceCitation(
        signal_id=id, source_ref="json:1", source_excerpt="张三负责\n交付"
    )
    doc = _bundle(store,
        (store.get_business_task_signal(id),), bodies={id: raw}, citations=(citation,)
    )["source_documents"][0]
    assert doc["decoded_excerpts"] == [
        {"path": ["report", "line"], "start": 0, "end": 7, "text": "张三负责\n交付"}
    ]
    assert sum(len(span["text"]) for span in (*doc["visible_ranges"], *doc["decoded_excerpts"])) <= 2048
