"""Proven terminal model inputs are references, with unchanged business use."""
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from app.email_classifier_model import email_message_to_text, CpuTfidfLogisticClassifier
from app.email_body_source import locate_original
from tests.test_email_body_source import (
    body_store as body_store, current13 as current13, _provider_message, _retired_canonical,
)
from tests.test_email_canonical_storage import _db
from app.email_canonical_storage import restore_email_canonical_schema


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
        'class_model_text', 'message_normalized_text', 'message_source_reference_json')} == before
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


def test_inverse_restores_model_input_without_rewinding_current_business_facts(tmp_path, current13):
    db = _db(tmp_path, current13)
    body, reference, metadata = _retired_canonical(db)
    text = 'exact historical redacted model input'
    model_ref = {'kind': 'native', 'sha256': sha256(text.encode()).hexdigest(),
                 'utf8_bytes': len(text.encode()), 'native_provenance': {}}
    db.execute("update emails set class_model_text='',class_subject='current result',"
               "message_source_reference_json=?", (json.dumps(reference | {'model_input': model_ref}),))
    db.execute("update emails set class_included_in_model_id='current-model'")
    restore_email_canonical_schema(db, metadata, read_body=lambda row, ref: body,
                                  read_model_input=lambda row, ref: text)
    assert db.execute('select model_text,subject,included_in_model_id from email_classifications').fetchone() == (
        text, 'current result', 'current-model')
    assert db.execute('pragma quick_check').fetchone() == ('ok',)
    db.close()


def test_inverse_rejects_missing_model_input_before_any_mutation(tmp_path, current13):
    db = _db(tmp_path, current13)
    body, reference, metadata = _retired_canonical(db)
    db.execute("update emails set class_model_text='',message_source_reference_json=?",
               (json.dumps(reference | {'model_input': {'sha256': sha256(b'model').hexdigest(), 'utf8_bytes': 5}}),))
    db.commit()
    before = tuple(db.iterdump())
    with pytest.raises(ValueError, match='model input'):
        restore_email_canonical_schema(db, metadata, read_body=lambda row, ref: body)
    assert tuple(db.iterdump()) == before
    db.close()


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


@pytest.mark.parametrize('backup_matches', [True, False])
def test_recovery_models_use_exact_backup_bytes_or_own_new_source(tmp_path, current13, backup_matches):
    import sqlite3
    from app.email_body_source import prepare_recovery_model_inputs

    db = _db(tmp_path, current13)
    body, reference, metadata = _retired_canonical(db)
    message = _provider_message(body)
    text = email_message_to_text(message)
    model_ref = locate_original(text, attempts=[], read_message=lambda: message,
        representations=(CpuTfidfLogisticClassifier.FEATURE_VERSION,))
    db.execute("update emails set class_model_text='',message_source_reference_json=?",
               (json.dumps(reference | {'model_input': model_ref}),))
    db.commit()
    backup = tmp_path / 'old-source.sqlite3'
    with sqlite3.connect(backup) as old:
        old.execute('create table email_classifications(account_id text,stable_message_identity text,model_text text)')
        old.execute('insert into email_classifications values(?,?,?)',
                    ('account', 'stable-1', text if backup_matches else 'previous input'))
    reads = []
    def read(account, identity):
        reads.append((account, identity))
        return message
    prepared = prepare_recovery_model_inputs(tmp_path / 'email.sqlite3', backup_path=backup,
                                             read_message=read)
    assert prepared == {('account', 'stable-1'): text}
    assert reads == ([] if backup_matches else [('account', 'stable-1')])
    assert db.execute('select class_model_text from emails').fetchone() == ('',)
    db.close()


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


def test_model_only_reference_inverse_keeps_current_body_and_inclusion(tmp_path, current13):
    db = _db(tmp_path, current13)
    _body, _reference, metadata = _retired_canonical(db)
    text = 'proven original features'
    model_ref = {'kind': 'native', 'sha256': sha256(text.encode()).hexdigest(), 'utf8_bytes': len(text.encode())}
    db.execute("update emails set class_model_text='',message_normalized_text='current cached body',"
               "message_source_reference_json=?", (json.dumps({'model_input': model_ref}),))
    db.execute("update emails set class_included_in_model_id='current-frozen-model'")
    restore_email_canonical_schema(db, metadata, read_model_input=lambda row, ref: text)
    assert db.execute('select normalized_text from email_messages').fetchone() == ('current cached body',)
    assert db.execute('select model_text,included_in_model_id from email_classifications').fetchone() == (
        text, 'current-frozen-model')
    db.close()


def test_inverse_never_replaces_a_current_nonempty_model_input_with_old_reference(tmp_path, current13):
    db = _db(tmp_path, current13)
    body, reference, metadata = _retired_canonical(db)
    old = 'old features'
    model_ref = {'kind': 'native', 'sha256': sha256(old.encode()).hexdigest(), 'utf8_bytes': len(old.encode())}
    db.execute("update emails set class_model_text='current new input',message_source_reference_json=?",
               (json.dumps(reference | {'model_input': model_ref}),))
    restore_email_canonical_schema(db, metadata, read_body=lambda row, ref: body,
        read_model_input=lambda *args: pytest.fail('current input must not be rewound'))
    assert db.execute('select model_text from email_classifications').fetchone() == ('current new input',)
    db.close()


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
