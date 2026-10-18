"""Actual Store Signal methods against the complete final physical declaration."""

from dataclasses import replace
from datetime import datetime, timezone
import sqlite3
import json

import pytest

from tests.test_provenance_vector_storage import db as db, binding as binding
from app.store import AutoReplyStore
from app.business_source_reader import SourceVersionBinding
from app.task_semantic_models import BusinessTaskSignal


def signal_input(binding):
    return dict(source_type=binding.source_type, source_ref=binding.source_ref,
                source_time=binding.source_time, conversation_id=binding.conversation_id,
                author_user_id=binding.author_user_id, author_name=binding.author_name,
                author_kind=binding.author_kind, evidence_text="source α")


def test_transient_signal_has_no_retired_document_alias(binding):
    signal = BusinessTaskSignal(id=1, **signal_input(binding), dedupe_key="one", created_at="clock")
    assert not hasattr(signal, "source_document_id")
    assert SourceVersionBinding.from_signal(signal).identity_key == binding.identity_key


def test_store_current_signal_write_and_point_reads_need_no_original_table(db, binding):
    # This covers actual transaction methods, not the pending constructor gate.
    store = AutoReplyStore.__new__(AutoReplyStore)
    db.row_factory = sqlite3.Row
    now = datetime(2026, 10, 18, tzinfo=timezone.utc)
    first = store.create_business_task_signal_in_transaction(**signal_input(binding),
        source_binding=binding, conversation_title="first", context_json='{"fact":1}',
        dedupe_key="one", now=now, _db=db)
    second = store.create_business_task_signal_in_transaction(**signal_input(binding),
        source_binding=binding, conversation_title="second", dedupe_key="two", now=now, _db=db)
    assert (first, second) == (1, 2)
    fact = store.get_business_task_signal_in_transaction(signal_id=first, _db=db)
    assert fact.source == binding
    assert not hasattr(fact, "source_document_id") and not hasattr(fact, "evidence_text")
    assert fact.conversation_title == "first" and fact.context_json == '{"fact":1}'
    assert store.get_business_task_signal_by_dedupe_key(dedupe_key="one", _db=db) == fact
    assert store.get_business_task_signal_in_transaction(signal_id=second, _db=db).source == binding
    assert db.execute("select count(*) from provenance").fetchone()[0] == 1
    assert not db.execute("select 1 from sqlite_schema where name='business_source_documents'").fetchone()
    assert db.execute("pragma foreign_key_check").fetchall() == []


def test_store_rejects_changed_source_binding_before_insertion(db, binding):
    store = AutoReplyStore.__new__(AutoReplyStore)
    with pytest.raises(ValueError, match="source binding"):
        store.create_business_task_signal_in_transaction(**signal_input(binding),
            source_binding=replace(binding, author_user_id="other"), dedupe_key="one", _db=db)
    assert db.execute("select count(*) from actions").fetchone()[0] == 0


def test_semantic_service_carries_exact_binding_and_deduplicates(db, binding):
    from app.task_semantic_service import SourceSignal, TaskSemanticService
    store = AutoReplyStore.__new__(AutoReplyStore)
    db.row_factory = sqlite3.Row
    service = TaskSemanticService(store)
    signal = SourceSignal(**signal_input(binding), dedupe_key="semantic", source_binding=binding)
    now = datetime(2026, 10, 18, tzinfo=timezone.utc)
    first = service._signal_id_or_create(signal=signal, db=db, now=now)
    assert service._signal_id_or_create(signal=signal, db=db, now=now) == first
    assert store.get_business_task_signal_in_transaction(signal_id=first, _db=db).source == binding


def test_store_public_signal_reads_and_console_reconstruction(db, tmp_path, monkeypatch):
    from app.business_source_reader import AvailableSource
    from app.business_storage import insert_business
    from app.action_business_storage import insert_business_action
    from tests.test_business_console_source_reader import facts, BODY, REF
    binding, original, event = facts()
    insert_business(db, "task", dict(id=event.task_id, title="Task", stage="candidate"))
    db.commit()
    path = tmp_path / "final-source-component.sqlite3"
    with sqlite3.connect(path) as target:
        db.backup(target)
    with monkeypatch.context() as patch:
        # Current constructor selection is still a separate integration gate.
        patch.setattr(AutoReplyStore, "_ensure_initialized", lambda self: None)
        store = AutoReplyStore(path)
    public_id = store.create_business_task_signal(
        **{field: getattr(binding, field) for field in (
            "source_type", "source_ref", "source_time", "conversation_id",
            "author_user_id", "author_name", "author_kind")}, evidence_text=BODY,
        dedupe_key=REF, context_json=original.context_json)
    with store.business_task_transaction() as current:
        insert_business_action(current, "business_task_evidence", dict(
            task_id=event.task_id, signal_id=public_id, evidence_role="resolution"))
        store.append_business_task_event(task_id=event.task_id, event_type=event.event_type,
            signal_id=public_id, before_json=event.before_json, after_json=event.after_json,
            reason=event.reason, _db=current)
    fact = store.get_business_task_signal(public_id)
    assert fact.source == binding
    assert store.list_business_task_signals() == (fact,)
    assert store.get_business_task_signal_for_task_source_ref(task_id=event.task_id, source_ref=REF) == fact
    reading = store.business_source_reader().resolve(binding)
    assert isinstance(reading, AvailableSource) and reading.body == BODY
    with store._connect() as current:
        for row in current.execute("select json(payload_json) from actions"):
            assert BODY not in json.dumps(json.loads(row[0]), ensure_ascii=False)
