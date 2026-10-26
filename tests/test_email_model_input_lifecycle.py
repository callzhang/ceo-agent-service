"""Proven terminal model inputs are references, with unchanged business use."""
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from app.email_classifier_model import email_message_to_text, CpuTfidfLogisticClassifier
from app.email_body_source import locate_original
from tests.test_email_body_source import (
    body_store as body_store, current13 as current13, _provider_message,
)


def _reader(message):
    return SimpleNamespace(read=lambda *args: message,
        read_many=lambda account, identities: {identity: message for identity in identities})


def _model_fixture(store, *, included=None, owner=True):
    message = _provider_message("complete body")
    text = email_message_to_text(message)
    with store._connect() as db:
        db.execute("update emails set class_model_text=?,class_classification_source=?,"
                   "class_category='work',class_confirmed_category='work'", (text, 'user' if owner else 'model'))
        db.execute("update emails set class_included_in_model_id=?", (included,))
    return message, text


def test_model_input_native_proof_precedes_provider_read(tmp_path, monkeypatch):
    from app import native_trajectory as native
    text = email_message_to_text(_provider_message("complete body"))
    path = tmp_path / "synthetic-native.jsonl"
    path.write_text(json.dumps({"content": json.dumps({"features": text})}) + "\n")
    monkeypatch.setattr(native, 'find_codex_session_path', lambda *args, **kwargs: path)
    ref = locate_original(text, attempts=[{'runtime_kind': 'codex_cli', 'session_id': 'test',
        'transcript_start': 0, 'transcript_end': 1}],
        read_message=lambda: pytest.fail('native original already proved'),
        representations=(CpuTfidfLogisticClassifier.FEATURE_VERSION,))
    assert ref['kind'] == 'native'
    assert native.read_native_source_body(ref['native_provenance']) == text
    assert text not in json.dumps(ref)


@pytest.mark.parametrize('included', [None, 'current-frozen-model'])
def test_terminal_inputs_retire_without_changing_facts_or_training_inclusion(body_store, included):
    store = body_store
    message, text = _model_fixture(store, included=included)
    with store._connect() as db:
        before = dict(db.execute('select * from emails').fetchone())
    pending = store.count_unincluded_training_examples()
    examples, stamp = store.owner_labelled_examples()
    calls = []
    def read(*args):
        calls.append(args)
        return message
    assert store.compact_email_inputs(101, source_reader=SimpleNamespace(read=read))
    with store._connect() as db:
        after = dict(db.execute('select * from emails').fetchone())
    assert after['class_model_text'] == after['message_normalized_text'] == ''
    reference = json.loads(after['message_source_reference_json'])['model_input']
    assert reference['sha256'] == sha256(text.encode()).hexdigest()
    assert reference['utf8_bytes'] == len(text.encode())
    assert reference['representation'] == CpuTfidfLogisticClassifier.FEATURE_VERSION
    assert after | {key: before[key] for key in (
        'class_model_text', 'message_normalized_text', 'message_source_reference_json', 'message_preview')} == before
    assert after['message_preview'] == 'complete body'
    assert store.count_unincluded_training_examples() == pending
    assert store.owner_labelled_examples(source_reader=_reader(message)) == (examples, stamp)
    assert len(calls) == 1
    assert not store.compact_email_inputs(101, source_reader=_reader(message))


@pytest.mark.parametrize('change', ['unavailable', 'different_version', 'status_changed'])
def test_unproved_or_active_model_input_stays_available(body_store, change):
    store = body_store
    message, text = _model_fixture(store)
    assert store.compact_email_message_body(101, source_reader=_reader(message))
    def read(*args):
        if change == 'unavailable':
            raise LookupError('exact source unavailable')
        if change == 'status_changed':
            with store._connect() as db:
                db.execute("update emails set class_status='pending_feedback'")
            return message
        return _provider_message('different original')
    assert not store.compact_email_model_input(101, source_reader=SimpleNamespace(read=read))
    with store._connect() as db:
        assert db.execute('select class_model_text from emails').fetchone()[0] == text


def test_retired_model_input_remains_available_after_future_owner_label(body_store):
    store = body_store
    message, text = _model_fixture(store, owner=False)
    assert store.compact_email_inputs(101, source_reader=_reader(message))
    assert store.owner_labelled_examples()[0] == []
    with store._connect() as db:
        db.execute("update emails set class_classification_source='user'")
    examples, stamp = store.owner_labelled_examples(source_reader=_reader(message))
    assert [item['text'] for item in examples] == [text]
    assert store.count_unincluded_training_examples() == 1
    with pytest.raises(ValueError, match='original'):
        store.owner_labelled_examples(source_reader=_reader(_provider_message('changed')))


def test_actual_new_model_input_clears_only_obsolete_model_reference(body_store):
    from app.email_canonical_storage import update_email_classification

    message, _ = _model_fixture(body_store, included='current-model')
    assert body_store.compact_email_inputs(101, source_reader=_reader(message))
    with body_store._connect() as db:
        root = json.loads(db.execute('select message_source_reference_json from emails').fetchone()[0])
        assert update_email_classification(db, 101, {'model_text': 'actual new input'}) == 1
        row = db.execute('select class_model_text,class_included_in_model_id,message_source_reference_json from emails').fetchone()
    assert row[0] == 'actual new input'
    assert row[1] is None  # This is a real input change, unlike retirement.
    assert json.loads(row[2]) == {k: v for k, v in root.items() if k != 'model_input'}


def test_nontext_model_inputs_are_not_reinterpreted_or_retired(body_store):
    message, _ = _model_fixture(body_store)
    assert body_store.compact_email_message_body(101, source_reader=_reader(message))
    with body_store._connect() as db:
        db.execute('update emails set class_model_text=?', (b'\xff\x00',))
    assert not body_store.compact_email_model_input(101,
        source_reader=SimpleNamespace(read=lambda *args: pytest.fail('nontext original must remain exact')))
    with body_store._connect() as db:
        assert db.execute('select class_model_text from emails').fetchone()[0] == b'\xff\x00'


def test_repeating_compacted_empty_storage_value_preserves_its_only_reference(body_store):
    from app.email_canonical_storage import update_email_classification

    message, text = _model_fixture(body_store)
    assert body_store.compact_email_inputs(101, source_reader=_reader(message))
    with body_store._connect() as db:
        before = db.execute('select message_source_reference_json from emails').fetchone()[0]
        assert update_email_classification(db, 101, {'model_text': ''}) == 1
        assert db.execute('select message_source_reference_json from emails').fetchone()[0] == before
    assert body_store.owner_labelled_examples(source_reader=_reader(message))[0][0]['text'] == text


def test_similar_owner_ranking_and_prompt_examples_survive_input_retirement(body_store, monkeypatch):
    from app.email_similar_examples import similar_owner_examples, clear_cache

    message, _ = _model_fixture(body_store)
    args = dict(stable_message_identity='other-message', sender='class-sender',
                subject='class-subject', text='complete body', allowed_category_keys=('work',))
    clear_cache()
    before = similar_owner_examples(body_store, **args)
    assert before and before[0]['category'] == 'work'
    assert body_store.compact_email_inputs(101, source_reader=_reader(message))
    clear_cache()
    monkeypatch.setattr('app.email_source_reader.build_email_source_reader', lambda *args, **kwargs: _reader(message))
    assert similar_owner_examples(body_store, **args) == before
    clear_cache()


def test_equal_body_with_changed_recipient_features_cannot_retire_model_input(body_store):
    message, text = _model_fixture(body_store)
    assert body_store.compact_email_message_body(101, source_reader=_reader(message))
    changed_headers = message | {'toRecipients': [{'email': 'other@example.test'}]}
    assert changed_headers['textBody'] == message['textBody']
    assert not body_store.compact_email_model_input(101, source_reader=_reader(changed_headers))
    with body_store._connect() as db:
        assert db.execute('select class_model_text from emails').fetchone()[0] == text


def test_model_input_can_be_proven_independently_of_unproved_body_cache(body_store):
    message = _provider_message('accurate provider body different from old cached body')
    text = email_message_to_text(message)
    with body_store._connect() as db:
        db.execute("update emails set class_model_text=?,class_classification_source='user',"
                   "class_category='work',class_confirmed_category='work'", (text,))
    assert body_store.compact_email_inputs(101, source_reader=_reader(message))
    with body_store._connect() as db:
        row = db.execute('select message_normalized_text,class_model_text,message_source_reference_json from emails').fetchone()
    assert tuple(row[:2]) == ('complete body', '')
    reference = json.loads(row[2])
    assert set(reference) == {'model_input'}
    assert body_store.owner_labelled_examples(source_reader=_reader(message))[0][0]['text'] == text


def test_real_body_update_keeps_model_reference_and_removes_only_obsolete_body_proof(body_store):
    from app.email_canonical_storage import update_email_message

    message, text = _model_fixture(body_store)
    assert body_store.compact_email_inputs(101, source_reader=_reader(message))
    with body_store._connect() as db:
        identity = db.execute('select message_id from emails').fetchone()[0]
        before = json.loads(db.execute('select message_source_reference_json from emails').fetchone()[0])
        assert update_email_message(db, identity, {'normalized_text': 'actual new body'}) == 1
        row = db.execute('select message_normalized_text,message_source_reference_json from emails').fetchone()
    assert row[0] == 'actual new body'
    assert json.loads(row[1]) == {'model_input': before['model_input']}
    assert body_store.owner_labelled_examples(source_reader=_reader(message))[0][0]['text'] == text


def test_owner_relabel_same_second_invalidates_example_stamp(body_store):
    store = body_store
    _model_fixture(store)
    before = store.owner_labelled_stamp()
    with store._connect() as db:
        db.execute("update emails set class_confirmed_category='legal'")
    assert store.owner_labelled_stamp() != before


def test_owner_relabel_during_source_read_same_second_is_not_returned(body_store):
    store = body_store
    message, _ = _model_fixture(store)
    assert store.compact_email_inputs(101, source_reader=_reader(message))

    def read_many(account, identities):
        with store._connect() as db:
            db.execute("update emails set class_confirmed_category='legal'")
        return {identity: message for identity in identities}

    with pytest.raises(ValueError, match='labels changed during input read'):
        store.owner_labelled_examples(source_reader=SimpleNamespace(read_many=read_many))
