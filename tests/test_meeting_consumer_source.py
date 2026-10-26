import json

import pytest

from app import native_trajectory as native
from app.agent_cron.commands import ServiceCommandConsumerContext, ServiceCommandSkillMaterial
from app.meeting_consumer_source import compact_meeting_consumer


@pytest.fixture
def consumer_source(tmp_path, monkeypatch):
    context = ServiceCommandConsumerContext(
        scheduled_task_id=7, scheduled_task_run_id=12, prompt="会议任务🙂",
        skill_names=("meeting",), skill_protocol="read selected frozen Skill",
        skill_protocol_source="generated_discovery",
        skill_materials=(ServiceCommandSkillMaterial(name="meeting", content="完整 Skill\n"),),
    )
    source = json.dumps({"meeting_id": "meeting-1", "minutes_info": {"id": "meeting-1"},
                         "scheduled_consumer": context.to_payload()}, ensure_ascii=False)
    path = tmp_path / "native.jsonl"
    path.write_text(json.dumps({"content": context.prompt + "\n" +
                              context.materialized_skill_protocol()}, ensure_ascii=False) + "\n")
    monkeypatch.setattr(native, "find_codex_session_path", lambda *a, **kw: path)
    attempt = dict(runtime_kind="codex_cli", session_id="session-1", transcript_start=0,
                   transcript_end=1)
    return context, source, attempt, path


def test_complete_native_fields_become_references_without_changing_business_source(consumer_source):
    context, source, attempt, path = consumer_source
    before = path.read_bytes()
    compact = json.loads(compact_meeting_consumer(source, [attempt]))
    assert "scheduled_consumer" not in compact
    assert compact["minutes_info"] == {"id": "meeting-1"}
    assert compact["meeting_id"] == "meeting-1"
    ref = compact["scheduled_consumer_reference"]
    assert ref["scheduled_task_run_id"] == 12
    assert ref["skill_protocol"] == context.skill_protocol
    assert native.read_native_source_body(ref["prompt"], literal_encoding_mode="plain_utf8") == context.prompt
    assert native.read_native_source_body(ref["skill_materials"][0]["native_provenance"],
                                         literal_encoding_mode="plain_utf8") == context.skill_materials[0].content
    assert context.prompt not in json.dumps(compact, ensure_ascii=False)
    assert context.skill_materials[0].content not in json.dumps(compact, ensure_ascii=False)
    assert path.read_bytes() == before
    assert compact_meeting_consumer(json.dumps(compact), [attempt]) is None


def test_missing_or_partial_native_source_keeps_original(consumer_source):
    _, source, attempt, path = consumer_source
    assert compact_meeting_consumer(source, []) is None
    path.write_text(json.dumps({"content": "会议任务🙂"}) + "\n")
    assert compact_meeting_consumer(source, [attempt]) is None


def test_inline_protocol_also_requires_a_complete_native_match(consumer_source):
    context, source, attempt, path = consumer_source
    payload = json.loads(source)
    del payload["scheduled_consumer"]["skill_materials"]
    payload["scheduled_consumer"]["skill_protocol_source"] = "explicit_custom"
    payload["scheduled_consumer"]["skill_protocol"] = "inline complete protocol"
    path.write_text(json.dumps({"content": context.prompt + "\ninline complete protocol"},
                               ensure_ascii=False) + "\n")
    ref = json.loads(compact_meeting_consumer(json.dumps(payload), [attempt]))["scheduled_consumer_reference"]
    assert native.read_native_source_body(ref["skill_protocol"], literal_encoding_mode="plain_utf8") == "inline complete protocol"


@pytest.fixture
def meeting_store(consumer_source, tmp_path):
    from app.store import AutoReplyStore
    from tests.support.native_run_rows import insert_runtime_attempt_row

    _, source, attempt, _ = consumer_source
    store = AutoReplyStore(tmp_path / "meeting.sqlite3")
    job_id = store.upsert_meeting_alignment_job(
        meeting_id="meeting-1", title="Meeting", source_json=source,
        participants_json="[]", ended_at="2026-10-18T00:00:00Z",
        eligible_at="2026-10-18T00:00:00Z", status="processing",
    )
    run_id = store.create_professional_run(
        job_id, session_id=attempt["session_id"], status="ready_to_send",
        transcript_start=0, transcript_end=1,
    )
    with store._connect() as db:
        db.execute("begin immediate")
        insert_runtime_attempt_row(db, {
            "parent_id": run_id, "workload_kind": "meeting", "workload_key": str(run_id),
            "attempt_number": 1, "route_name": "route", "runtime_kind": "codex_cli",
            "credential_mode": "local_oauth", "model": "model", "status": "completed",
            "session_id": attempt["session_id"], "transcript_start": 0, "transcript_end": 1,
            "lease_owner": "", "lease_expires_at": "",
        })
    return store, job_id


@pytest.mark.parametrize("status", ["processing", "retry", "ready_to_send", "no_action", "failed"])
def test_only_nonreplayable_terminal_jobs_can_retire_inputs(meeting_store, status):
    store, job_id = meeting_store
    store.update_meeting_alignment_job(job_id, status=status)
    before = store.get_meeting_alignment_job(job_id)
    assert not store.compact_meeting_consumer_input(job_id)
    assert store.get_meeting_alignment_job(job_id) == before


def test_terminal_write_compacts_native_inputs_and_preserves_business_result(meeting_store):
    store, job_id = meeting_store
    store.update_meeting_alignment_job(job_id, status="sent", final_message="business result",
                                       send_result_json='{"receipt":"provider-1"}')
    job = store.get_meeting_alignment_job(job_id)
    assert "scheduled_consumer" not in json.loads(job.source_json)
    assert job.final_message == "business result"
    assert json.loads(job.send_result_json) == {"receipt": "provider-1"}
    assert not store.compact_meeting_consumer_input(job_id)
    assert store.rerun_meeting_alignment_jobs([job_id]) == []


def test_native_missing_terminal_write_retains_original_and_receipt(meeting_store, monkeypatch):
    store, job_id = meeting_store
    original = store.get_meeting_alignment_job(job_id).source_json
    monkeypatch.setattr(native, "find_codex_session_path", lambda *a, **kw: None)
    store.update_meeting_alignment_job(job_id, status="skipped", send_result_json='{"result":"skipped"}')
    assert store.get_meeting_alignment_job(job_id).source_json == original
    assert json.loads(store.get_meeting_alignment_job(job_id).send_result_json) == {"result": "skipped"}


def test_source_changed_during_native_read_is_not_overwritten(meeting_store, monkeypatch):
    import app.meeting_consumer_source as sources

    store, job_id = meeting_store
    # Set the terminal state without invoking the normal post-commit compactor.
    from tests.support.native_task_fields import set_native_task_fields
    with store._connect() as db:
        set_native_task_fields(db, "meeting_alignment_jobs", job_id, {"status": "sent"})
    prepare = sources.compact_meeting_consumer
    newer_source = '{"meeting_id":"meeting-1","current_fact":true}'

    def changed(source, attempts, **kwargs):
        compact = prepare(source, attempts, **kwargs)
        store.update_meeting_alignment_job(job_id, source_json=newer_source)
        return compact

    monkeypatch.setattr(sources, "compact_meeting_consumer", changed)
    assert not store.compact_meeting_consumer_input(job_id)
    assert store.get_meeting_alignment_job(job_id).source_json == newer_source


@pytest.fixture
def saved_consumer_source():
    from types import SimpleNamespace
    from hashlib import sha256
    from app.agent_cron.models import ScheduledTaskSkillRef

    content = "existing managed Skill original\n"
    revision = SimpleNamespace(id=142, skill_id=9, content=content,
                               sha256=sha256(content.encode()).hexdigest())
    protocol = f"## Managed Skill: meeting\nrevision_id: 142\nsha256: {revision.sha256}\n\n{content}"
    context = ServiceCommandConsumerContext(
        scheduled_task_id=7, scheduled_task_run_id=12, prompt="saved prompt",
        skill_names=("meeting",), skill_protocol=protocol,
    )
    snapshot = SimpleNamespace(task_id=7, prompt=context.prompt, skill_refs=(
        ScheduledTaskSkillRef(skill_source="managed", skill_name="meeting", position=0,
                              managed_skill_id=9, managed_revision_id=142),
    ))
    store = SimpleNamespace(
        get_scheduled_task_run=lambda run_id: SimpleNamespace(snapshot=snapshot) if run_id == 12 else None,
        get_managed_skill_revision=lambda revision_id: revision if revision_id == 142 else None,
        get_managed_skill=lambda skill_id: SimpleNamespace(name="meeting") if skill_id == 9 else None,
    )
    return context, store, revision


def test_exact_saved_source_replaces_inline_original_only_after_native_lookup(saved_consumer_source):
    from app.meeting_consumer_source import scheduled_consumer_source_fields

    context, store, _ = saved_consumer_source
    source = json.dumps({"scheduled_consumer": context.to_payload(), "meeting_id": "meeting-1"})
    compact = compact_meeting_consumer(
        source, [], source_reader=lambda c: scheduled_consumer_source_fields(store, c),
    )
    ref = json.loads(compact)["scheduled_consumer_reference"]
    assert ref["prompt"]["scheduled_task_run_id"] == 12
    assert ref["skill_protocol"]["skills"][0]["revision_id"] == 142
    assert context.skill_protocol not in json.dumps(ref)


def test_changed_saved_source_cannot_retire_an_old_version(saved_consumer_source):
    from app.meeting_consumer_source import scheduled_consumer_source_fields
    from hashlib import sha256

    context, store, revision = saved_consumer_source
    revision.content = "different valid original"
    revision.sha256 = sha256(revision.content.encode()).hexdigest()
    assert scheduled_consumer_source_fields(store, context) is None


def test_native_match_does_not_read_upstream_configuration(consumer_source):
    _, source, attempt, _ = consumer_source

    def forbidden(context):
        raise AssertionError("native source should be preferred")

    assert compact_meeting_consumer(source, [attempt], source_reader=forbidden) is not None


def test_explicit_migration_batch_compacts_only_terminal_jobs(meeting_store):
    from tests.support.native_task_fields import set_native_task_fields

    store, job_id = meeting_store
    with store._connect() as db:
        set_native_task_fields(db, "meeting_alignment_jobs", job_id, {"status": "sent"})
    assert store.compact_terminal_meeting_consumer_inputs() == {"examined": 1, "compacted": 1}
    assert store.compact_terminal_meeting_consumer_inputs() == {"examined": 1, "compacted": 0}


def test_material_reference_keeps_source_hash_separate_from_rendered_content_hash(saved_consumer_source):
    from app.meeting_consumer_source import scheduled_consumer_source_fields
    from dataclasses import replace
    from hashlib import sha256

    context, store, revision = saved_consumer_source
    material_context = replace(context, skill_protocol="small discovery",
        skill_materials=(ServiceCommandSkillMaterial(name="meeting", content=context.skill_protocol),))
    fields = scheduled_consumer_source_fields(store, material_context)
    material = fields["skill_materials"][0]
    assert material["source"]["sha256"] == revision.sha256
    assert material["content_sha256"] == sha256(context.skill_protocol.encode()).hexdigest()
    assert material["content_sha256"] != revision.sha256


def test_snapshot_prompt_change_retains_original(saved_consumer_source):
    from app.meeting_consumer_source import scheduled_consumer_source_fields

    context, store, _ = saved_consumer_source
    store.get_scheduled_task_run(12).snapshot.prompt = "changed snapshot"
    assert scheduled_consumer_source_fields(store, context) is None


@pytest.mark.parametrize("error", [PermissionError("source denied"), UnicodeError("invalid source text")])
def test_unreadable_native_file_keeps_terminal_input_and_receipt(meeting_store, monkeypatch, error):
    import app.meeting_consumer_source as sources

    store, job_id = meeting_store
    original = store.get_meeting_alignment_job(job_id).source_json

    def unreadable(*args, **kwargs):
        raise error

    monkeypatch.setattr(sources, "locate_native_source_body", unreadable)
    store.update_meeting_alignment_job(job_id, status="sent", final_message="business result",
                                       send_result_json='{"receipt":"provider-1"}')
    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "sent"
    assert job.source_json == original
    assert job.final_message == "business result"
    assert json.loads(job.send_result_json) == {"receipt": "provider-1"}


def test_unreadable_operation_directory_keeps_original(saved_consumer_source):
    from dataclasses import replace
    from types import SimpleNamespace
    from app.agent_cron.models import ScheduledTaskSkillRef
    from app.meeting_consumer_source import scheduled_consumer_source_fields

    context, store, _ = saved_consumer_source
    context = replace(context, skill_names=("operation",))
    store.get_scheduled_task_run(12).snapshot.skill_refs = (
        ScheduledTaskSkillRef(skill_source="operation", skill_name="operation", position=0),
    )

    def unreadable():
        raise PermissionError("operation source directory denied")

    assert scheduled_consumer_source_fields(store, context,
        skill_files=SimpleNamespace(list_skills=unreadable)) is None


def test_configuration_database_error_is_not_treated_as_missing_source(saved_consumer_source):
    import sqlite3
    from app.meeting_consumer_source import scheduled_consumer_source_fields

    context, store, _ = saved_consumer_source

    def database_error(revision_id):
        raise sqlite3.OperationalError("configuration read failed")

    store.get_managed_skill_revision = database_error
    with pytest.raises(sqlite3.OperationalError, match="configuration read failed"):
        scheduled_consumer_source_fields(store, context)
