"""Publish only already verified input references, never a copy's business state."""
from contextlib import closing
from pathlib import Path
import sqlite3

import pytest

from tests.test_email_body_source import body_store as body_store, _provider_message
from tests.test_email_model_input_lifecycle import _model_fixture, _reader


def _proved_copy(store):
    from app.email_store import EmailStore
    message, _ = _model_fixture(store, included='old-model')
    with store._connect() as db:
        db.execute("update emails set message_folder='Archive',"
                   "uidvalidity=11,uid=21,class_rfc_message_id='<message>',"
                   "message_rfc_message_id='<message>',class_thread_id='',message_thread_identity=''")
    path = store.path.with_name('verified-copy.sqlite3')
    with closing(sqlite3.connect(store.path)) as source, closing(sqlite3.connect(path)) as target:
        source.backup(target)
    copy = EmailStore(path)
    assert copy.compact_email_inputs(101, source_reader=_reader(message))
    return path


def _row(store):
    with store._connect() as db:
        return dict(db.execute('select * from emails').fetchone())


def test_preview_and_apply_keep_live_facts_inclusion_and_repeat_idempotently(body_store, monkeypatch):
    from scripts.retire_verified_email_inputs import retire_verified_email_inputs
    from app import email_body_source
    source = _proved_copy(body_store)
    with body_store._connect() as db:
        db.execute("update emails set message_subject='new business result',class_category='legal'")
        db.execute("update emails set class_included_in_model_id='current-model'")
    before = _row(body_store)
    monkeypatch.setattr(email_body_source, 'locate_original', lambda *a, **k: pytest.fail('proof already exists'))
    preview = retire_verified_email_inputs(source, body_store.path)
    assert preview['body_matches'] == preview['model_matches'] == 1
    assert _row(body_store) == before
    applied = retire_verified_email_inputs(source, body_store.path, apply=True)
    assert applied['body_retired'] == applied['model_inputs_retired'] == 1
    after = _row(body_store)
    assert after['message_normalized_text'] == after['class_model_text'] == ''
    assert after['message_preview'] == 'complete body'
    assert after | {k: before[k] for k in ('message_normalized_text','class_model_text','message_source_reference_json','message_preview')} == before
    again = retire_verified_email_inputs(source, body_store.path, apply=True)
    assert again['changed_rows'] == 0
    assert _row(body_store) == after


@pytest.mark.parametrize('change', ['body', 'model', 'model_blob', 'active', 'identity'])
def test_new_or_unfinished_live_inputs_are_never_overwritten(body_store, change):
    from scripts.retire_verified_email_inputs import retire_verified_email_inputs
    source = _proved_copy(body_store)
    with body_store._connect() as db:
        column = {'body':'message_normalized_text','model':'class_model_text',
                  'model_blob':'class_model_text','active':'class_status','identity':'stable_message_identity'}[change]
        value = 'pending_feedback' if change == 'active' else b'live blob' if change == 'model_blob' else 'new live value'
        db.execute(f'update emails set {column}=?', (value,))
    before = _row(body_store)
    report = retire_verified_email_inputs(source, body_store.path, apply=True)
    after = _row(body_store)
    assert report['body_retired'] == (1 if change in ('model', 'model_blob') else 0)
    assert report['model_inputs_retired'] == (1 if change == 'body' else 0)
    permitted = {'message_source_reference_json'}
    if change == 'body':
        permitted.add('class_model_text')
    if change in ('model', 'model_blob'):
        permitted.update(('message_normalized_text', 'message_preview'))
        assert after['message_preview'] == 'complete body'
    assert after | {k:before[k] for k in permitted} == before


def test_source_copy_is_read_only_and_missing_target_is_not_created(body_store):
    from scripts.retire_verified_email_inputs import retire_verified_email_inputs
    source = _proved_copy(body_store)
    before = source.read_bytes()
    retire_verified_email_inputs(source, body_store.path, apply=True)
    assert source.read_bytes() == before
    missing = Path(body_store.path).with_name('missing.sqlite3')
    with pytest.raises(FileNotFoundError):
        retire_verified_email_inputs(source, missing, apply=True)
    assert not missing.exists()


def test_existing_body_reference_is_preserved_while_model_reference_is_published(body_store):
    import json
    from scripts.retire_verified_email_inputs import retire_verified_email_inputs
    source = _proved_copy(body_store)
    message = _provider_message('complete body')
    message['uid'] = 99
    assert body_store.compact_email_message_body(101, source_reader=_reader(message))
    before = json.loads(_row(body_store)['message_source_reference_json'])
    report = retire_verified_email_inputs(source, body_store.path, apply=True)
    after = json.loads(_row(body_store)['message_source_reference_json'])
    assert report['body_retired'] == 0 and report['model_inputs_retired'] == 1
    assert {k:v for k,v in after.items() if k != 'model_input'} == before


def test_exact_native_metadata_can_be_published_without_rereading_originals(body_store, tmp_path, monkeypatch):
    import json
    from app import native_trajectory as native
    from app.email_body_source import locate_original
    from scripts.retire_verified_email_inputs import retire_verified_email_inputs
    source = _proved_copy(body_store)
    before = _row(body_store)
    path = tmp_path / 'native.jsonl'
    path.write_text(json.dumps({'content':json.dumps({'body':before['message_normalized_text'],
                                'features':before['class_model_text']})})+'\n')
    monkeypatch.setattr(native, 'find_codex_session_path', lambda *a, **k:path)
    attempt = dict(runtime_kind='codex_cli',session_id='test',transcript_start=0,transcript_end=1)
    def proof(text):
        return locate_original(text, attempts=[attempt], read_message=lambda:pytest.fail('native proof exists'))
    reference = proof(before['message_normalized_text']) | {'model_input':proof(before['class_model_text'])}
    with closing(sqlite3.connect(source)) as db:
        db.execute('update emails set message_source_reference_json=?',(json.dumps(reference),))
        db.commit()
    monkeypatch.setattr(native, 'read_native_source_body', lambda *a, **k:pytest.fail('native proof already verified'))
    report = retire_verified_email_inputs(source, body_store.path, apply=True)
    assert report['body_retired'] == report['model_inputs_retired'] == 1
    assert json.loads(_row(body_store)['message_source_reference_json']) == reference
