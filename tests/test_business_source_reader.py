"""Synthetic exact-source checks for the unselected provenance reader."""

import hashlib
import json
from dataclasses import replace

import pytest

from app import native_trajectory
from app.business_source_reader import (
    AvailableSource,
    BusinessTaskSignalFact,
    PreparedSources,
    RequestSourceReader,
    SourceUnavailableReason,
    SourceVersionBinding,
    UnavailableSource,
)
from app.task_semantic_models import BusinessTaskSignal
from app.task_source_documents import source_document_key


BODY = '{"message":"中文🙂 said hello"}'
SESSION = "d1b35850-a2b0-4899-a869-c53dac5fb481"


def signal(body=BODY, **changes):
    fields = dict(
        id=7, source_type="dingtalk_chat",
        source_ref="chat:one", source_time="2026-10-18T01:02:03Z",
        conversation_id="conversation:one", conversation_title="Original title",
        author_user_id="actor:one", author_name="A", author_kind="human",
        evidence_text=body, context_json="{}", dedupe_key="action:one",
        created_at="2026-10-18T01:02:04Z",
    )
    fields.update(changes)
    return BusinessTaskSignal(**fields)


def test_native_reader_and_prepared_fact_fanout_never_read_inside_writer(native, monkeypatch):
    _, locator = native()
    first = signal()
    second = signal(id=8, dedupe_key="action:two",
                    conversation_title="Another Signal")
    facts = {
        item.id: BusinessTaskSignalFact.from_signal(
            item, binding=SourceVersionBinding.from_signal(item, native_provenance=locator)
        ) for item in (first, second)
    }
    reads = []
    read_native = native_trajectory.read_native_source_body
    def read(locator):
        reads.append(facts[7].source.identity_key)
        return read_native(locator)
    monkeypatch.setattr(native_trajectory, "read_native_source_body", read)

    class FakeStore:
        def business_source_reader(self):
            return RequestSourceReader()

        def get_business_task_signal(self, signal_id):
            return facts.get(signal_id)

        def get_business_task_signal_in_transaction(self, *, signal_id, _db):
            return facts.get(signal_id)

    store = FakeStore()
    prepared = PreparedSources.load(store, facts)
    assert reads == [facts[7].source.identity_key]
    assert prepared.require_body(store, 7, db=object())[1] == BODY
    assert prepared.require_body(store, 8, db=object())[1] == BODY
    assert reads == [facts[7].source.identity_key]
    assert prepared.facts[7].conversation_title != prepared.facts[8].conversation_title
    facts[8] = replace(facts[8], context_json='{"changed":true}')
    with pytest.raises(ValueError, match="changed"):
        prepared.require_body(store, 8, db=object())


def test_missing_locator_is_explicit_and_never_empty_original():
    item = signal()
    binding = SourceVersionBinding.from_signal(item)
    result = RequestSourceReader().resolve(binding)
    assert isinstance(result, UnavailableSource)
    assert result.reason is SourceUnavailableReason.MISSING_LOCATOR


def test_metadata_only_prepared_fact_rejects_a_changed_binding_without_original():
    item = signal()
    fact = BusinessTaskSignalFact.from_signal(
        item, binding=SourceVersionBinding.from_signal(item)
    )
    live = {item.id: fact}

    class FakeStore:
        def get_business_task_signal_in_transaction(self, *, signal_id, _db):
            return live.get(signal_id)

    prepared = PreparedSources(RequestSourceReader())
    prepared.add(fact)
    assert isinstance(prepared.reading_for(fact), UnavailableSource)
    assert prepared.require_fact(FakeStore(), fact.id, db=object()) == fact
    live[item.id] = replace(fact, source=replace(fact.source, source_ref="chat:changed"))
    with pytest.raises(ValueError, match="changed"):
        prepared.require_fact(FakeStore(), fact.id, db=object())


def test_current_exact_body_registration_retains_native_locator_without_reader_io(native):
    _, locator = native()
    item = signal()
    binding = SourceVersionBinding.from_signal(item, native_provenance=locator)
    fact = BusinessTaskSignalFact.from_signal(item, binding=binding)
    prepared = PreparedSources(RequestSourceReader())

    prepared.add_current(fact, BODY)

    assert prepared.require_body(
        type("Store", (), {"get_business_task_signal_in_transaction":
                           lambda self, *, signal_id, _db: fact})(),
        fact.id, db=object()
    )[1] == BODY
    assert prepared.facts[fact.id].source.native_provenance_json == binding.native_provenance_json
    with pytest.raises(ValueError, match="does not match"):
        prepared.add_current(fact, BODY + " altered")


@pytest.fixture
def native(tmp_path, monkeypatch):
    def make(body=BODY):
        literal = json.dumps(body, ensure_ascii=False)
        prefix = "before "
        leaf = prefix + literal + " after"
        raw = json.dumps({"payload": {"text": leaf}}, ensure_ascii=False).encode() + b"\n"
        path = tmp_path / "session.jsonl"
        path.write_bytes(raw)
        monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda session_id: path)
        locator = dict(
            runtime_kind="codex_cli", session_uuid=SESSION,
            line_index_zero_based=0, raw_line_sha256=hashlib.sha256(raw).hexdigest(),
            leaf_path=["payload", "text"],
            substring_start_character_zero_based=len(prefix),
            substring_end_character_exclusive=len(prefix) + len(literal),
            literal_encoding_mode="json_string_literal_utf8",
            decoded_utf8_sha256=hashlib.sha256(body.encode()).hexdigest(),
            decoded_utf8_bytes=len(body.encode()),
        )
        return path, locator
    return make


def test_exact_native_available_with_multibyte_body_and_decoded_quote(native):
    _, locator = native()
    item = signal()
    binding = SourceVersionBinding.from_signal(item, native_provenance=locator)
    result = RequestSourceReader().resolve(
        binding, source_ref=item.source_ref, quote="中文🙂 said hello"
    )
    assert isinstance(result, AvailableSource)
    assert result.body == BODY
    assert result.binding is binding
    assert binding.body_utf8_bytes == len(BODY.encode())
    assert binding.body_sha256 == hashlib.sha256(BODY.encode()).hexdigest()
    assert binding.identity_key == source_document_key(
        source_type=item.source_type, source_ref=item.source_ref,
        source_time=item.source_time, conversation_id=item.conversation_id,
        author_user_id=item.author_user_id, author_name=item.author_name,
        author_kind=item.author_kind, evidence_text=BODY,
    )
    fact = BusinessTaskSignalFact.from_signal(item, binding=binding)
    assert fact.id == item.id and not hasattr(fact, "source_document_id")
    assert fact.conversation_title == item.conversation_title
    assert fact.context_json == item.context_json and fact.dedupe_key == item.dedupe_key
    assert "evidence_text" not in vars(fact)
    with pytest.raises(ValueError, match="does not match"):
        BusinessTaskSignalFact.from_signal(
            item, binding=replace(binding, author_user_id="different actor")
        )


@pytest.mark.parametrize("damage,reason", [
    ("missing", SourceUnavailableReason.SOURCE_MISSING),
    ("changed", SourceUnavailableReason.VERSION_MISMATCH),
    ("invalid", SourceUnavailableReason.INVALID_LOCATOR),
])
def test_missing_changed_or_invalid_native_source(native, damage, reason):
    path, locator = native()
    binding = SourceVersionBinding.from_signal(signal(), native_provenance=locator)
    if damage == "missing":
        path.unlink()
    elif damage == "changed":
        path.write_bytes(path.read_bytes().replace(b"payload", b"changed"))
    else:
        locator["substring_end_character_exclusive"] = 99999
        binding = SourceVersionBinding.from_signal(signal(), native_provenance=locator)
    result = RequestSourceReader().resolve(binding)
    assert isinstance(result, UnavailableSource)
    assert result.reason is reason
    assert not hasattr(result, "body")


def test_binding_body_mismatch_and_identity_fact_changes(native):
    _, locator = native()
    reader = RequestSourceReader()
    assert reader.resolve(SourceVersionBinding.from_signal(signal("wrong"), native_provenance=locator)).reason is SourceUnavailableReason.VERSION_MISMATCH
    for field, value in (
        ("source_ref", "chat:other"), ("source_time", "2026-10-19"),
        ("conversation_id", "conversation:other"), ("author_user_id", "actor:other"),
        ("author_name", "B"), ("author_kind", "agent"),
    ):
        changed = SourceVersionBinding.from_signal(signal(**{field: value}), native_provenance=locator)
        assert changed.identity_key != SourceVersionBinding.from_signal(signal(), native_provenance=locator).identity_key
    good = SourceVersionBinding.from_signal(signal(), native_provenance=locator)
    assert reader.resolve(good, source_ref="chat:other").reason is SourceUnavailableReason.SOURCE_REF_MISMATCH
    assert reader.resolve(good, quote="absent").reason is SourceUnavailableReason.QUOTE_MISSING
    assert isinstance(reader.resolve(good, quote="中文🙂"), AvailableSource)


def test_missing_locator_reference_only_and_unimplemented_provider():
    reader = RequestSourceReader()
    missing = SourceVersionBinding.from_signal(signal())
    assert reader.resolve(missing).reason is SourceUnavailableReason.MISSING_LOCATOR
    cited = SourceVersionBinding.from_signal(signal(source_type="memory_provenance"))
    assert reader.resolve(cited, quote="中文🙂").reason is SourceUnavailableReason.REFERENCE_ONLY
    provider = SourceVersionBinding.from_signal(signal(), provider_locator={"node_id": "exact", "historyVersion": 4})
    assert RequestSourceReader().resolve(provider).reason is SourceUnavailableReason.READER_UNAVAILABLE


def test_incomplete_selected_native_record_is_unavailable(native):
    path, locator = native()
    malformed = b"{broken\n"
    path.write_bytes(malformed)
    locator["raw_line_sha256"] = hashlib.sha256(malformed).hexdigest()
    result = RequestSourceReader().resolve(
        SourceVersionBinding.from_signal(signal(), native_provenance=locator)
    )
    assert result.reason is SourceUnavailableReason.SOURCE_INCOMPLETE


def test_native_helper_distinguishes_missing_and_changed_with_lookup_compatibility(native):
    path, locator = native()
    path.unlink()
    with pytest.raises(native_trajectory.NativeSourceMissing) as missing:
        native_trajectory.read_native_source_body(locator)
    assert isinstance(missing.value, LookupError)

    path, locator = native()
    path.write_bytes(path.read_bytes().replace(b"payload", b"changed"))
    with pytest.raises(native_trajectory.NativeSourceVersionChanged) as changed:
        native_trajectory.read_native_source_body(locator)
    assert isinstance(changed.value, LookupError)

    path, locator = native()
    path.write_bytes(b"")
    with pytest.raises(native_trajectory.NativeSourceMissing) as missing_line:
        native_trajectory.read_native_source_body(locator)
    assert isinstance(missing_line.value, LookupError)

    _, locator = native()
    locator["decoded_utf8_sha256"] = "0" * 64
    with pytest.raises(native_trajectory.NativeSourceVersionChanged) as changed_body:
        native_trajectory.read_native_source_body(locator)
    assert isinstance(changed_body.value, LookupError)


def test_request_map_fanout_and_changed_binding_do_not_reuse(native, monkeypatch):
    _, locator = native()
    calls = 0
    original = native_trajectory.read_native_source_body

    def counted(value):
        nonlocal calls
        calls += 1
        return original(value)

    monkeypatch.setattr(native_trajectory, "read_native_source_body", counted)
    first = signal()
    second = signal(id=8, dedupe_key="action:two", conversation_title="Different title")
    binding = SourceVersionBinding.from_signal(first, native_provenance=locator)
    reader = RequestSourceReader()
    assert isinstance(reader.resolve(binding), AvailableSource)
    assert isinstance(reader.resolve(SourceVersionBinding.from_signal(second, native_provenance=locator)), AvailableSource)
    assert calls == 1
    assert BusinessTaskSignalFact.from_signal(first, binding=binding).id != BusinessTaskSignalFact.from_signal(second, binding=binding).id
    changed = replace(binding, body_sha256="0" * 64)
    assert reader.resolve(changed).reason is SourceUnavailableReason.BINDING_CONFLICT
    assert calls == 1


def test_programming_error_is_not_converted_to_unavailable(native, monkeypatch):
    _, locator = native()
    monkeypatch.setattr(native_trajectory, "read_native_source_body", lambda value: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        RequestSourceReader().resolve(SourceVersionBinding.from_signal(signal(), native_provenance=locator))

    def unknown_lookup(value):
        raise KeyError("unexpected helper bug")

    monkeypatch.setattr(native_trajectory, "read_native_source_body", unknown_lookup)
    with pytest.raises(KeyError, match="unexpected helper bug"):
        RequestSourceReader().resolve(SourceVersionBinding.from_signal(signal(), native_provenance=locator))


def test_native_serialized_locator_wrong_type_is_invalid(native):
    _, locator = native()
    binding = SourceVersionBinding.from_signal(signal(), native_provenance=locator)
    assert RequestSourceReader().resolve(
        replace(binding, native_provenance_json=123)
    ).reason is SourceUnavailableReason.INVALID_LOCATOR
