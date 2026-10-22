"""The paired release consumes only exact, already verified source coordinates."""
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from app.business_source_reader import SourceVersionBinding


def signal():
    return SimpleNamespace(source_type="message", source_ref="original:1", source_time="",
        conversation_id="", author_user_id="", author_name="", author_kind="human",
        evidence_text="exact original α")


def test_authorized_retirement_retains_binding_without_body_or_locator():
    from app.final_source_migration import bind_verified_source
    source = signal()
    expected = SourceVersionBinding.from_signal(source)
    actual = bind_verified_source(source, proof=None,
        retirement={"identity_key": expected.identity_key, "utf8_bytes": expected.body_utf8_bytes})
    assert actual == expected
    assert source.evidence_text not in str(asdict(actual))


def test_unknown_original_is_not_silently_retired():
    from app.final_source_migration import bind_verified_source
    with pytest.raises(ValueError, match="verified source"):
        bind_verified_source(signal(), proof=None, retirement=None)


def test_changed_authorized_original_is_rejected():
    from app.final_source_migration import bind_verified_source
    expected = SourceVersionBinding.from_signal(signal())
    with pytest.raises(ValueError, match="retirement"):
        bind_verified_source(signal(), proof=None,
            retirement={"identity_key": expected.identity_key, "utf8_bytes": 1})


def test_native_coordinates_are_selected_without_copying_original():
    from app.final_source_migration import bind_verified_source, NATIVE_FIELDS
    source = signal()
    binding = SourceVersionBinding.from_signal(source)
    native = dict.fromkeys(NATIVE_FIELDS, "coordinate")
    native.update(status="currently_readable_exact_native_match")
    proof = dict(identity_key=binding.identity_key, source_body_sha256=binding.body_sha256,
        source_body_utf8_bytes=binding.body_utf8_bytes, native_axis=native, upstream_axis={})
    actual = bind_verified_source(source, proof=proof, retirement=None)
    import json
    assert json.loads(actual.native_provenance_json) == {key: native[key] for key in NATIVE_FIELDS}
    assert actual.provider_locator_json is None
    assert source.evidence_text not in str(asdict(actual))
    proof["source_body_sha256"] = "changed"
    with pytest.raises(ValueError, match="version"):
        bind_verified_source(source, proof=proof, retirement=None)


def test_inverse_reads_original_from_explicit_existing_backup(tmp_path):
    import sqlite3
    from app.final_source_migration import read_recovery_source_body
    from app.business_source_reader import RequestSourceReader
    binding = SourceVersionBinding.from_signal(signal())
    backup = tmp_path / "existing-backup.sqlite3"
    with sqlite3.connect(backup) as db:
        db.execute("create table business_source_documents(identity_key text,body text)")
        db.execute("insert into business_source_documents values(?,?)", (binding.identity_key, signal().evidence_text))
    assert read_recovery_source_body(binding, backup_path=backup, reader=RequestSourceReader()) == signal().evidence_text
    with sqlite3.connect(backup) as db:
        db.execute("update business_source_documents set body='changed'")
    with pytest.raises(ValueError, match="differs"):
        read_recovery_source_body(binding, backup_path=backup, reader=RequestSourceReader())


def test_inverse_unavailable_original_is_not_fabricated():
    from app.final_source_migration import read_recovery_source_body
    from app.business_source_reader import RequestSourceReader
    with pytest.raises(ValueError, match="unavailable"):
        read_recovery_source_body(SourceVersionBinding.from_signal(signal()),
            backup_path=None, reader=RequestSourceReader())


def test_new_source_version_uses_its_actual_task_generation_native_input(tmp_path, monkeypatch):
    import json
    import sqlite3
    from app import native_trajectory as native
    from app.final_source_migration import locate_task_source_binding

    source = signal()
    transcript = tmp_path / 'native.jsonl'
    transcript.write_text(json.dumps({'payload': {'text': json.dumps({'source': source.evidence_text}, ensure_ascii=False)}}) + '\n')
    monkeypatch.setattr(native, 'find_codex_session_path', lambda session: transcript if session == 'actual-session' else None)
    with sqlite3.connect(':memory:') as db:
        db.row_factory = sqlite3.Row
        db.execute('create table tasks(record_type text,legacy_key text,source_type text,source_ref text)')
        db.execute('create table runs(record_type text,legacy_key text,summary_input_id integer,workload_kind text,workload_key text,runtime_kind text,session_id text,transcript_start integer,transcript_end integer,status text)')
        db.execute('insert into tasks values(?,?,?,?)', ('work_summary_inputs', '100', source.source_type, source.source_ref))
        db.execute("insert into runs(record_type,legacy_key,summary_input_id) values('task_agent_runs','200',100)")
        db.execute("insert into runs values('agent_runtime_attempts','300',null,'task','200','codex_cli','actual-session',0,1,'completed')")
        binding = locate_task_source_binding(db, source)
        assert binding.matches_body(source.evidence_text)
        locator = json.loads(binding.native_provenance_json)
        assert native.read_native_source_body(locator) == source.evidence_text
        assert source.evidence_text not in binding.native_provenance_json
        db.execute("update tasks set source_ref='unrelated-source'")
        assert locate_task_source_binding(db, source) is None


def test_missing_native_original_does_not_authorize_a_new_retirement(tmp_path, monkeypatch):
    import sqlite3
    from app import native_trajectory as native
    from app.final_source_migration import locate_task_source_binding

    monkeypatch.setattr(native, 'find_codex_session_path', lambda session: None)
    with sqlite3.connect(':memory:') as db:
        db.row_factory = sqlite3.Row
        db.execute('create table tasks(record_type text,legacy_key text,source_type text,source_ref text)')
        db.execute('create table runs(record_type text,legacy_key text,summary_input_id integer,workload_kind text,workload_key text,runtime_kind text,session_id text,transcript_start integer,transcript_end integer,status text)')
        db.execute('insert into tasks values(?,?,?,?)', ('work_summary_inputs', '100', signal().source_type, signal().source_ref))
        db.execute("insert into runs(record_type,legacy_key,summary_input_id) values('task_agent_runs','200',100)")
        db.execute("insert into runs values('agent_runtime_attempts','300',null,'task','200','codex_cli','missing-session',0,1,'completed')")
        assert locate_task_source_binding(db, signal()) is None
