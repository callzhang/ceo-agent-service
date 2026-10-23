import sqlite3
import json
import re

import pytest

from app import logical_run_current_schema, run_entity_definitions, runs11_current_schema
from app.run_entity_schema import capture_run_entities, migrate_run_entities, restore_run_entities
from app.run_entity_schema import _relocate_run_owner_sequence_rowids

_TARGET_NAMES = set(run_entity_definitions.run_entity_objects())
_TARGET_TABLES = {name for name, sql in run_entity_definitions.run_entity_objects().items()
                  if sql.lstrip().lower().startswith("create table ")}


def _create(objects, db):
    for sql in objects.values():
        db.execute(sql)



def _run_handoff(db, source_metadata, *, identity_edges=(), owner_highwaters=None):
    from app.run_entity_schema import run_handoff_context, run_handoff_digest

    owners = ("runs", "runtime_attempts", "run_failures", "schedule_occurrences",
              "workbench_events", "codex_session_search_index",
              "email_classifier_runtime_samples", "setup_wizard_events")
    highwaters = ({owner: None for owner in owners} if owner_highwaters is None
                  else owner_highwaters)
    edges = sorted(identity_edges, key=lambda edge: (edge["source_kind"], edge["source_physical_id"]))
    context = run_handoff_context(source_metadata)
    return {"version": 3, "source_context": context, "identity_edges": edges,
            "runtime_attempt_parent_edges": [], "runtime_attempt_orphan_workload_edges": [],
            "owner_highwaters": highwaters,
            "digest": run_handoff_digest(context, edges, [], [], highwaters)}


def test_capture_requires_exact_selected_current_run_oracle():
    db = sqlite3.connect(":memory:")
    db.execute("pragma foreign_keys=off")
    db.execute("begin")
    _create(logical_run_current_schema.current_run11_objects(), db)
    assert logical_run_current_schema.current_run11_schema_is_current(db)
    assert not runs11_current_schema.current_run11_schema_is_current(db)
    metadata = capture_run_entities(db)
    assert metadata["source_objects"] == logical_run_current_schema.current_run11_objects()


def test_capture_rejects_historical_runs11_factory():
    db = sqlite3.connect(":memory:")
    db.execute("pragma foreign_keys=off")
    db.execute("begin")
    from app.runs11_schema_release import _target_objects
    _create(_target_objects(), db)
    assert runs11_current_schema.current_run11_schema_is_current(db)
    assert not logical_run_current_schema.current_run11_schema_is_current(db)
    with pytest.raises(ValueError, match="selected-current"):
        capture_run_entities(db)


def test_forward_requires_caller_transaction():
    db = sqlite3.connect(":memory:")
    with pytest.raises(ValueError, match="caller-owned"):
        capture_run_entities(db)


def test_forward_source_catalog_change_rejects_before_mutation():
    db, task_metadata = _run_source()
    try:
        metadata = capture_run_entities(db)
        db.execute("drop index idx_agent_runs_status")
        original = tuple(db.execute("select id,record_type from runs order by id"))
        with pytest.raises(ValueError, match="selected-current"):
            migrate_run_entities(db, metadata, task_metadata=task_metadata, run_handoff=_run_handoff(db, metadata))
        assert tuple(db.execute("select id,record_type from runs order by id")) == original
        assert db.execute("select 1 from sqlite_schema where name='runs'").fetchone()
    finally:
        db.close()


def _target_prerequisites(db):
    from app.configs_storage import create_configs_schema
    from app.schedules_storage import create_schedules_schema

    create_schedules_schema(db)
    create_configs_schema(db)
    db.execute(
        "insert into schedules(id,name,prompt,cron_expression,timezone,runtime_id,enabled) "
        "values(55,'fixture','prompt','* * * * *','UTC','codex',1)"
    )


def _source_row(db, kind, values):
    row = {"record_type": kind, **values}
    columns = tuple(row)
    db.execute("insert into runs(" + ",".join(columns) + ") values(" +
               ",".join("?" for _ in columns) + ")", tuple(row[c] for c in columns))


def test_run_owner_allocator_composes_with_captured_source_sequence_rowids():
    db = sqlite3.connect(":memory:")
    source_names = ("tasks:published", "tasks")
    for name in source_names:
        db.execute(f'create table "{name}"(id integer primary key autoincrement)')
        db.execute(f'insert into "{name}"(id) values(2)')
    db.execute('update sqlite_sequence set seq=3 where name="tasks"')
    db.execute("insert into sqlite_sequence(rowid,name,seq) values(3,'runs:published',2)")
    db.execute('create table "runs"(id integer primary key autoincrement)')
    db.execute('insert into "runs"(id) values(2)')
    db.execute("insert into sqlite_sequence(rowid,name,seq) values(5,'runs:reserved',44)")
    source_rows = tuple(db.execute(
        "select rowid,name,seq from sqlite_sequence order by rowid"))
    assert source_rows == ((1, "tasks:published", 2), (2, "tasks", 3),
                           (3, "runs:published", 2), (4, "runs", 2),
                           (5, "runs:reserved", 44))

    # Run conversion drops the source table, then creates the native owner;
    # its first sequence row reuses captured source ROWID 4.
    db.execute('drop table "runs"')
    db.execute("delete from sqlite_sequence where name='runs:reserved'")
    db.execute("create table workbench_events(id integer primary key autoincrement)")
    db.execute("insert into workbench_events(id) values(2)")
    assert db.execute(
        "select rowid,name,seq from sqlite_sequence where name='workbench_events'"
    ).fetchone() == (4, "workbench_events", 2)

    run_source_rows = tuple(row for row in source_rows if row[1].startswith("runs"))
    _relocate_run_owner_sequence_rowids(db, run_source_rows, {"workbench_events"})
    for row in run_source_rows:
        db.execute("delete from sqlite_sequence where name=?", (row[1],))
        db.execute("insert into sqlite_sequence(rowid,name,seq) values(?,?,?)", row)
    assert tuple(db.execute(
        "select rowid,name,seq from sqlite_sequence where name in "
        "('tasks:published','tasks','runs:published','runs','runs:reserved') order by rowid")) == source_rows
    assert db.execute(
        "select rowid,name,seq from sqlite_sequence where name='workbench_events'"
    ).fetchone() == (6, "workbench_events", 2)


def _run_source(*, orphan_task_id=None, include_task_source_metadata=False, workbench_artifact_public_id=None):
    from app.task_entity_schema import capture_task_entities, migrate_task_entities
    from tests.test_task_entity_schema import _source as task_source

    db = task_source()
    if workbench_artifact_public_id is not None:
        from app.task_lifecycle_storage import source_assignments
        from app.storage_payload import TASK_RECORD_TYPES
        physical = db.execute(
            "select id from tasks where _kind=?",
            (TASK_RECORD_TYPES.code("workbench_artifacts"),),
        ).fetchone()[0]
        values = source_assignments(
            "workbench_artifacts", {"id": workbench_artifact_public_id}, {},
            payload_source="tasks.payload_json", source_alias="tasks",
        )
        db.execute(
            f"update tasks set {values.sql} where _kind=? and id=?",
            (*values.parameters, TASK_RECORD_TYPES.code("workbench_artifacts"), physical),
        )
        db.commit()
    db.execute("pragma foreign_keys=off")
    db.execute("begin immediate")
    task_source_metadata = capture_task_entities(db) if include_task_source_metadata else None
    task_metadata = capture_task_entities(db)
    task_forward = migrate_task_entities(db, task_metadata)
    db.commit()
    db.execute("begin immediate")
    _target_prerequisites(db)
    objects = logical_run_current_schema.current_run11_objects()
    db.execute(objects["runs"])
    _source_row(db, "agent_runs", {
        "id": 1001, "legacy_key": "1001", "task_id": 201,
        "execution_generation": "consumer-g", "role": "consumer", "proposal_revision": 0,
        "turn_attempt": 0, "operation_id": "consumer-op", "status": "completed",
        "session_id": "consumer-session", "transcript_start": 0, "transcript_end": 0,
        "structured_error_json": "{}", "lease_owner": "", "lease_expires_at": "",
        "started_at": "start", "completed_at": "done", "created_at": "created",
        "updated_at": "updated",
    })
    _source_row(db, "agent_runs", {
        "id": 1002, "legacy_key": "1002", "task_id": 201,
        "execution_generation": "audit-g", "role": "audit", "proposal_revision": 0,
        "turn_attempt": 0, "operation_id": "audit-op", "status": "completed",
        "session_id": "audit-session", "transcript_start": 0, "transcript_end": 0,
        "structured_error_json": "{}", "lease_owner": "", "lease_expires_at": "",
        "started_at": "start", "completed_at": "done", "created_at": "created",
        "updated_at": "updated",
    })
    for physical, source_type, task_id in (
        (1101, "task_agent_runs", 220), (1102, "meeting_alignment_runs", 221),
        (1103, "okr_review_runs", 222),
    ):
        values = {"id": physical, "record_type": "agent_runs", "source_type": source_type,
                  "legacy_key": "201", "task_id": task_id, "session_id": f"session-{physical}",
                  "created_at": "created"}
        if source_type == "task_agent_runs":
            values.update(status="completed", error=b"error\x00\xff", projection_json=' {"x":1} ',
                          completed_at="done", updated_at="updated")
        elif source_type == "meeting_alignment_runs":
            values.update(status="no_action", error="", transcript_start=2, transcript_end=8,
                          completed_at="done", updated_at="updated")
        else:
            values.update(transcript_start=0, transcript_end=0)
        _source_row(db, "agent_runs", values)
    workload_facts = (
        ("task", "201:decision_repair.2"), ("task", "201"),
        ("agent_run", "1001"), ("meeting", "201"),
        ("structured", "222"), ("weekly_okr", "week:manager:digest"),
        ("weekly_okr", "week:manager:digest:223:owner"),
        ("email_classification", "classification-225"),
        ("workbench", "wb-turn-a"), ("memory", "wechat_memory_import_job:232"),
        ("memory", "meeting_memory_write_event:opaque:3"),
        ("task", "301:memory_backfill"), ("task", "302:deadline_backfill.4"),
        ("task", "201:decision_repair.3"),
    )
    for index, (workload_kind, workload_key) in enumerate(workload_facts, start=1201):
        _source_row(db, "agent_runtime_attempts", {
            "id": index, "legacy_key": str(index), "parent_id": 1001 if workload_kind == "agent_run" else None,
            "workload_kind": workload_kind, "workload_key": workload_key, "attempt_number": 1,
            "route_name": f"r-{index}", "runtime_kind": "codex_cli", "credential_mode": "local_oauth",
            "model": "m", "session_mode": "fresh", "source_session_id": "",
            "attempt_purpose": "normal", "validation_retry_policy_id": "",
            "validation_result_schema_id": "", "session_id": "s", "transcript_reference": "t",
            "transcript_start": 0, "transcript_end": 0, "status": "completed",
            "failure_class": "", "failure_code": "", "failover_permitted": 0,
            "lease_owner": "", "lease_expires_at": "", "first_effect_started_at": "",
            "result_schema_id": "schema", "started_at": "start", "finished_at": "finish",
            "created_at": "created", "updated_at": "updated",
        })
    _source_row(db, "agent_run_state_events", {
        "id": 1302, "legacy_key": "1302", "parent_id": 1001,
        "phase": "server", "structured_error_json": b"{\x00}", "created_at": "created",
    })
    _source_row(db, "scheduled_task_runs", {
        "id": 1303, "legacy_key": "1303", "scheduled_task_id": 55,
        "trigger_kind": "manual", "scheduled_for": "2026-10-22T00:00:00Z",
        "dispatch_status": "pending", "skip_or_error_reason": "", "event_id": "event-1",
        "execution_kind": "reply_task", "execution_id": "201", "dispatched_at": None,
        "first_scheduled_for": "2026-10-22T00:00:00Z", "occurrence_count": 1,
        "result_summary": "", "snapshot_id": None, "lease_owner": "",
        "lease_expires_at": None, "created_at": "created",
    })
    _source_row(db, "workbench_events", {
        "id": 1304, "legacy_key": "1304", "task_id": 203, "sequence": 1,
        "event_type": "started", "payload_json": b"{\x00}", "created_at": "created",
    })
    _source_row(db, "codex_session_search_index", {
        "id": 1305, "legacy_key": "1305", "session_id": "index-session",
        "source_type": "meeting_alignment", "source_id": "201", "title": "title",
        "created_at": "created", "updated_at": "updated",
    })
    _source_row(db, "scheduled_task_runs", {
        "id": 1308, "legacy_key": "1308", "scheduled_task_id": 55,
        "trigger_kind": "manual", "scheduled_for": "2026-10-22T00:01:00Z",
        "dispatch_status": "pending", "skip_or_error_reason": "", "event_id": "event-opaque",
        "execution_kind": "service_command", "execution_id": "201",
        "first_scheduled_for": "2026-10-22T00:01:00Z", "occurrence_count": 1,
        "result_summary": "", "lease_owner": "",
        "created_at": "created",
    })
    _source_row(db, "codex_session_search_index", {
        "id": 1309, "legacy_key": "1309", "session_id": "index-opaque",
        "source_type": "service_command", "source_id": "201", "title": "opaque title",
        "created_at": "created", "updated_at": "updated",
    })
    _source_row(db, "email_classifier_runtime_samples", {
        "id": 1306, "legacy_key": "1306", "model_id": "model", "outcome": "success",
        "fallback_code": "", "cache_hit": 1, "runtime_warm": 0, "queue_ms": 1.25,
        "http_ms": 2.5, "embedding_ms": 3.75, "head_ms": 4.5, "total_ms": 12.0,
        "created_at": "created",
    })
    _source_row(db, "setup_wizard_events", {
        "id": 1307, "legacy_key": "1307", "step_id": "step", "action_id": "action",
        "status": "completed", "summary": "summary", "evidence_json": "{}",
        "stdout_excerpt": "out", "stderr_excerpt": "err", "created_at": "created",
        "finished_at": "finished",
    })
    if orphan_task_id is not None:
        _source_row(db, "agent_runs", {
            "id": 1400, "legacy_key": "2298", "task_id": orphan_task_id,
            "source_type": "meeting_alignment_runs", "job_id": 4908,
            "status": "no_action", "session_id": "anomaly-session",
            "transcript_start": 0, "transcript_end": 0, "error": "",
            "completed_at": "done", "created_at": "created", "updated_at": "updated",
        })
    for name, sql in objects.items():
        if name != "runs":
            db.execute(sql)
    db.execute("create table runs_other(id integer primary key autoincrement)")
    db.execute("insert into runs_other(id) values(77)")
    db.execute("update sqlite_sequence set seq=8000 where name='runs'")
    db.execute("insert into sqlite_sequence(name,seq) values('runs:published',8000)")
    db.commit()
    db.execute("begin")
    return ((db, task_forward, task_source_metadata) if include_task_source_metadata
            else (db, task_forward))



def test_artifact_created_uuid_reference_survives_native_forward_and_inverse():
    from uuid import uuid4
    from app.task_entity_schema import (
        capture_task_entities, migrate_task_entities, restore_task_entities,
    )

    artifact_public_id = str(uuid4())
    db, task_forward, task_source_metadata = _run_source(
        include_task_source_metadata=True,
        workbench_artifact_public_id=artifact_public_id,
    )
    try:
        db.execute(
            "update runs set event_type='artifact_created', payload_json=? where record_type='workbench_events' and id=1304",
            (json.dumps({"artifact_id": artifact_public_id, "retained": {"x": 1}}),),
        )
        source_metadata = capture_run_entities(db)
        source_sequences = tuple(source_metadata["sequence_rows"])
        assert source_sequences == ((10, "runs", 8000), (12, "runs:published", 8000))
        unrelated_sequences = tuple(db.execute(
            "select rowid,name,seq from sqlite_sequence where name='runs_other'"))
        owner_highwaters = {owner: None for owner in (
            "runs", "runtime_attempts", "run_failures", "schedule_occurrences",
            "workbench_events", "codex_session_search_index",
            "email_classifier_runtime_samples", "setup_wizard_events")}
        owner_highwaters["workbench_events"] = 1304
        forward = migrate_run_entities(
            db, source_metadata, task_metadata=task_forward,
            run_handoff=_run_handoff(db, source_metadata, owner_highwaters=owner_highwaters),
        )
        assert tuple(db.execute(
            "select rowid,name,seq from sqlite_sequence where name in ('runs','runs:published') "
            "order by rowid")) == source_sequences
        assert tuple(db.execute(
            "select rowid,name,seq from sqlite_sequence where name='runs_other'")) == unrelated_sequences
        event_sequence_row = db.execute(
            "select rowid,name,seq from sqlite_sequence where name='workbench_events'"
        ).fetchone()
        assert event_sequence_row[0] > max(row[0] for row in source_sequences)
        event = db.execute("select task_id,payload_json from workbench_events where sequence=1").fetchone()
        next_event_id = db.execute(
            "insert into workbench_events(task_id,sequence,event_type,payload_json,created_at) "
            "values(?,2,'allocator_probe','{}','now') returning id", (event[0],),
        ).fetchone()[0]
        assert next_event_id > 1304
        db.execute("delete from workbench_events where id=?", (next_event_id,))
        owner_highwaters["workbench_events"] = next_event_id
        artifact_id = json.loads(event[1])["artifact_id"]
        artifact = db.execute("select workbench_turn_id from workbench_artifacts where id=?", (int(artifact_id),)).fetchone()
        assert int(artifact_id) > 0
        assert artifact[0] == event[0]
        assert json.loads(event[1]) == {"artifact_id": artifact_id, "retained": {"x": 1}}

        task_inverse = restore_task_entities(db, task_source_metadata, task_forward)
        restore_run_entities(
            db, source_metadata, forward, task_metadata=task_forward,
            task_inverse_metadata=task_inverse,
        )
        restored = db.execute(
            "select payload_json from runs where record_type='workbench_events' and id=1304"
        ).fetchone()[0]
        assert json.loads(restored) == {"artifact_id": artifact_public_id, "retained": {"x": 1}}
        assert tuple(db.execute(
            "select rowid,name,seq from sqlite_sequence where name='runs_other'")) == unrelated_sequences
        db.commit()

        # Repeat the actual handoff twice: UUID source identity and unrelated
        # payload facts must remain stable while the physical native ID stays
        # canonical across cycles.
        for _ in range(2):
            db.execute("begin immediate")
            next_task_source = capture_task_entities(db)
            next_task_forward = migrate_task_entities(
                db, next_task_source, source_context=task_inverse["source_context"],
                inverse_identity=task_inverse["inverse_identity"],
            )
            next_run_source = capture_run_entities(db)
            next_run_forward = migrate_run_entities(
                db, next_run_source, task_metadata=next_task_forward,
                run_handoff=_run_handoff(db, next_run_source,
                                         owner_highwaters=owner_highwaters),
            )
            assert tuple(db.execute(
                "select rowid,name,seq from sqlite_sequence where name='runs_other'")) == unrelated_sequences
            assert db.execute(
                "select seq from sqlite_sequence where name='workbench_events'"
            ).fetchone()[0] >= owner_highwaters["workbench_events"]
            native_event = db.execute(
                "select task_id,payload_json from workbench_events where sequence=1"
            ).fetchone()
            native_artifact_id = json.loads(native_event[1])["artifact_id"]
            native_artifact = db.execute(
                "select workbench_turn_id from workbench_artifacts where id=?",
                (int(native_artifact_id),),
            ).fetchone()
            assert native_artifact_id == artifact_id
            assert native_artifact[0] == native_event[0]
            assert json.loads(native_event[1]) == {
                "artifact_id": artifact_id, "retained": {"x": 1},
            }
            task_inverse = restore_task_entities(db, next_task_source, next_task_forward)
            restore_run_entities(
                db, next_run_source, next_run_forward, task_metadata=next_task_forward,
                task_inverse_metadata=task_inverse,
            )
            restored_payload = db.execute(
                "select payload_json from runs where record_type='workbench_events' and id=1304"
            ).fetchone()[0]
            assert json.loads(restored_payload) == {
                "artifact_id": artifact_public_id, "retained": {"x": 1},
            }
            db.commit()
    finally:
        db.close()

def test_forward_migrates_all_eight_current_source_kinds_and_typed_facts():
    db, task_metadata = _run_source()
    try:
        source_columns = tuple(row[1] for row in db.execute("pragma table_info(runs)"))
        source_rows = [dict(zip(source_columns, row, strict=True))
                       for row in db.execute("select * from runs order by id")]
        before = {name: tuple(db.execute("select * from runs where record_type=? order by id", (name,)))
                  for name in {r[0] for r in db.execute("select distinct record_type from runs")}}
        unrelated_sequence = tuple(db.execute(
            "select rowid,name,seq from sqlite_sequence where name='runs_other'"))
        metadata = capture_run_entities(db)
        result = migrate_run_entities(db, metadata, task_metadata=task_metadata, run_handoff=_run_handoff(db, metadata))
        assert tuple(db.execute(
            "select rowid,name,seq from sqlite_sequence where name='runs_other'")) == unrelated_sequence
        assert set(before) == set(run_entity_definitions.SOURCE_FIELDS)
        assert result["sequence_rows"] == metadata["sequence_rows"]
        assert db.execute("select count(*) from runs").fetchone()[0] == 5
        assert tuple(db.execute("select source_type,job_id from run_provenance order by run_id")) == (
            ("task_agent_runs", None), ("meeting_alignment_runs", None), ("okr_review_runs", None))
        assert tuple(db.execute("select id,task_id from runs order by id")) == (
            (1001, 201), (1002, 201), (1101, 220), (1102, 221), (1103, 222))
        assert db.execute("select projection_json from run_projections where run_id=1101").fetchone()[0] == ' {"x":1} '
        assert tuple(db.execute("select typeof(error),error from runs where id=1101")) == (
            ("blob", b"error\x00\xff"),)
        assert db.execute("select typeof(structured_error_json) from run_failures").fetchone()[0] == "blob"
        assert db.execute("select typeof(payload_json) from workbench_events").fetchone()[0] == "blob"
        assert db.execute("select task_id from workbench_events").fetchone()[0] == 203
        assert db.execute("select execution_id from schedule_occurrences").fetchone()[0] == "201"
        assert db.execute("select source_id from codex_session_search_index").fetchone()[0] == "1102"
        assert db.execute("select execution_id from schedule_occurrences where id=1308").fetchone()[0] == "201"
        assert db.execute("select source_id from codex_session_search_index where id=1309").fetchone()[0] == "201"
        assert db.execute("select typeof(total_ms),total_ms from email_classifier_runtime_samples").fetchone() == ("real", 12.0)
        runtime_keys = dict(db.execute("select workload_kind||':'||id,workload_key from runtime_attempts"))
        assert runtime_keys["task:1201"] == "1101:decision_repair.2"
        assert runtime_keys["task:1202"] == "1101"
        assert runtime_keys["agent_run:1203"] == "1001"
        assert runtime_keys["meeting:1204"] == "1102"
        assert runtime_keys["structured:1205"] == "222"
        assert runtime_keys["weekly_okr:1206"] == "week:manager:digest"
        assert runtime_keys["weekly_okr:1207"] == "week:manager:digest:223:owner"
        assert runtime_keys["email_classification:1208"] == "225"
        assert runtime_keys["workbench:1209"] == "203"
        assert runtime_keys["memory:1210"] == "wechat_memory_import_job:232"
        assert runtime_keys["memory:1211"] == "meeting_memory_write_event:opaque:3"
        assert runtime_keys["task:1212"] == "301:memory_backfill"
        assert runtime_keys["task:1213"] == "302:deadline_backfill.4"
        assert runtime_keys["task:1214"] == "1101:decision_repair.3"
        assert dict(db.execute("select id,parent_id from runtime_attempts where id in (1201,1202,1204,1214)")) == {
            1201: 1101, 1202: 1101, 1204: 1102, 1214: 1101}
        assert tuple(db.execute("select workload_key,attempt_number,parent_id from runtime_attempts "
                                "where id in (1201,1214) order by id")) == (
            ("1101:decision_repair.2", 1, 1101), ("1101:decision_repair.3", 1, 1101))
        assert db.execute("select parent_id from runtime_attempts where id=1211").fetchone()[0] is None
        assert db.execute("select parent_id from runtime_attempts where id=1205").fetchone()[0] is None
        assert db.execute("select seq from sqlite_sequence where name='runs'").fetchone()[0] == 8000
        assert db.execute("select seq from sqlite_sequence where name='runs:published'").fetchone()[0] == 8000
        assert set(_TARGET_NAMES) <= {row[0] for row in db.execute(
            "select name from sqlite_schema where name not like 'sqlite_%'")}
        mappings = {(item["source_kind"], item["source_field"]): item
                    for item in run_entity_definitions.run_entity_field_map()}
        for source in source_rows:
            kind = source["record_type"]
            physical_id = source["id"]
            for field in source_columns:
                mapping = mappings[(kind, field)]
                table, target_field = mapping["target_table"], mapping["target_field"]
                value = source[field]
                if table is None:
                    assert value is None, (kind, field, value)
                elif table == "run_entity_identity_map":
                    identity = db.execute(
                        "select source_kind,public_key,target_id,public_key_order "
                        "from run_entity_identity_map "
                        "where source_kind=? and physical_id=?", (kind, physical_id),
                    ).fetchone()
                    expected = {"source_kind": kind, "public_key":
                                (None if value is None else str(value)), "target_id": physical_id}
                    assert identity is not None
                    assert identity[("source_kind", "public_key", "target_id").index(target_field)] == expected[target_field]
                    if field == "legacy_key":
                        cast_order = (db.execute("select cast(? as integer)", (value,)).fetchone()[0]
                                      if value is not None else None)
                        assert identity[3] == cast_order
                else:
                    key_field = "run_id" if table in {"run_provenance", "run_projections"} else "id"
                    key_value = physical_id
                    stored = db.execute(
                        f"select typeof({_q(target_field)}),{_q(target_field)} from {_q(table)} "
                        f"where {_q(key_field)}=?",
                        (key_value,),
                    ).fetchone()
                    if field == "task_id" and kind in {"agent_runs", "workbench_events"} and value is not None:
                        pass
                    if field == "source_id" and kind == "codex_session_search_index" \
                            and source["source_type"] == "meeting_alignment":
                        value = "1102"
                    if field == "execution_id" and kind == "scheduled_task_runs" \
                            and source["execution_kind"] == "reply_task":
                        value = "201"
                    if field == "workload_key" and kind == "agent_runtime_attempts":
                        value = {"task:201:decision_repair.2": "1101:decision_repair.2",
                                 "task:201": "1101", "task:201:decision_repair.3": "1101:decision_repair.3",
                                 "agent_run:1001": "1001", "meeting:201": "1102",
                                 "structured:222": "222", "weekly_okr:week:manager:digest:223:owner":
                                     "week:manager:digest:223:owner",
                                 "weekly_okr:week:manager:digest": "week:manager:digest",
                                 "email_classification:classification-225": "225",
                                 "workbench:wb-turn-a": "203",
                                 "memory:wechat_memory_import_job:232": "wechat_memory_import_job:232",
                                 "memory:meeting_memory_write_event:opaque:3": "meeting_memory_write_event:opaque:3",
                                 "task:301:memory_backfill": "301:memory_backfill",
                                 "task:302:deadline_backfill.4": "302:deadline_backfill.4"}[
                                     f"{source['workload_kind']}:{source['workload_key']}"]
                    if field == "parent_id" and kind == "agent_runtime_attempts":
                        if source["workload_kind"] == "task" and source["workload_key"] in {
                                "201", "201:decision_repair.2", "201:decision_repair.3"}:
                            value = 1101
                        elif source["workload_kind"] == "meeting":
                            value = 1102
                    assert (stored[1] if stored else None) == value, (kind, field, value, stored)
                    assert (stored[0] if stored else "null") == _sqlite_type(value), (
                        kind, field, _sqlite_type(value), stored)
    finally:
        db.close()


def test_portable_handoff_remaps_all_eight_typed_run_owners_and_references():
    db, task_metadata = _run_source()
    try:
        db.execute("update runs set parent_id=1001 where id=1002")
        source = capture_run_entities(db)
        # Same canonical integer across different owners is valid. Within the
        # runs owner, every source Run receives a distinct canonical identity.
        edge_targets = {
            ("agent_runs", 1001): 9101, ("agent_runs", 1002): 9102,
            ("agent_runs", 1101): 9111, ("agent_runs", 1102): 9112,
            ("agent_runs", 1103): 9113, ("agent_runtime_attempts", 1203): 9101,
            ("agent_run_state_events", 1302): 9402,
            ("scheduled_task_runs", 1303): 9503,
            ("workbench_events", 1304): 9604,
            ("codex_session_search_index", 1305): 9705,
            ("email_classifier_runtime_samples", 1306): 9806,
            ("setup_wizard_events", 1307): 9907,
        }
        edges = [dict(source_kind=kind, source_physical_id=physical, canonical_id=canonical)
                 for (kind, physical), canonical in edge_targets.items()]
        highwaters = {owner: None for owner in _run_handoff(db, source)["owner_highwaters"]}
        handoff = _run_handoff(db, source, identity_edges=edges, owner_highwaters=highwaters)
        handoff = json.loads(json.dumps(handoff))
        migrate_run_entities(db, source, task_metadata=task_metadata, run_handoff=handoff)
        assert tuple(db.execute("select id,task_id,parent_id from runs order by id")) == (
            (9101, 201, None), (9102, 201, 9101), (9111, 220, None),
            (9112, 221, None), (9113, 222, None))
        assert tuple(db.execute("select run_id,source_type from run_provenance order by run_id")) == (
            (9111, "task_agent_runs"), (9112, "meeting_alignment_runs"),
            (9113, "okr_review_runs"))
        assert db.execute("select run_id from run_projections").fetchone()[0] == 9111
        assert db.execute("select parent_id,workload_key from runtime_attempts where id=9101").fetchone() == (
            9101, "9101")
        assert db.execute("select workload_key from runtime_attempts where id=1201").fetchone()[0] == (
            "9111:decision_repair.2")
        assert db.execute("select parent_id from runtime_attempts where id=1201").fetchone()[0] == 9111
        assert db.execute("select run_id from run_failures").fetchone()[0] == 9101
        assert db.execute("select id,execution_id from schedule_occurrences "
                           "where execution_kind='reply_task'").fetchone() == (9503, "201")
        assert db.execute("select id,task_id from workbench_events").fetchone() == (9604, 203)
        assert db.execute("select id,source_id from codex_session_search_index").fetchone() == (
            9705, "9112")
        assert db.execute("select id from email_classifier_runtime_samples").fetchone()[0] == 9806
        assert db.execute("select id from setup_wizard_events").fetchone()[0] == 9907
        identity = db.execute(
            "select physical_id,typeof(public_key),public_key,target_id from run_entity_identity_map "
            "where source_kind='agent_runs' and physical_id=1101").fetchone()
        assert identity == (1101, "text", "201", 9111)
    finally:
        db.close()


def test_meeting_audit_runtime_key_and_parent_survive_two_inverse_reforward_cycles():
    db, task_metadata = _run_source()
    try:
        db.execute(
            "update runs set workload_key='201:audit' "
            "where record_type='agent_runtime_attempts' and id=1204"
        )
        with pytest.raises(sqlite3.IntegrityError, match="logical agent run"):
            db.execute(
                "update runs set parent_id=1102 where record_type='agent_runtime_attempts' and id=1204"
            )
        source = capture_run_entities(db)
        handoff = _run_handoff(db, source, identity_edges=[
            dict(source_kind="agent_runs", source_physical_id=1102, canonical_id=9112),
        ])
        forward = migrate_run_entities(
            db, source, task_metadata=task_metadata,
            run_handoff=json.loads(json.dumps(handoff)),
        )

        assert db.execute(
            "select workload_key,parent_id from runtime_attempts where id=1204"
        ).fetchone() == ("9112:audit", 9112)

        for _ in range(2):
            inverse = restore_run_entities(
                db, source, forward, task_metadata=task_metadata,
            )
            assert db.execute(
                "select workload_key,parent_id from runs where record_type='agent_runtime_attempts' and id=1204"
            ).fetchone() == ("201:audit", None)
            source = capture_run_entities(db)
            forward = migrate_run_entities(
                db, source, task_metadata=task_metadata,
                run_handoff=json.loads(json.dumps(inverse["run_handoff"])),
            )
            assert db.execute(
                "select workload_key,parent_id from runtime_attempts where id=1204"
            ).fetchone() == ("9112:audit", 9112)
    finally:
        db.close()


@pytest.mark.parametrize("workload_key", ["201:planner", "201:audit:extra", "201:"])
def test_meeting_audit_runtime_key_rejects_unsupported_suffix_before_forward(workload_key):
    db, task_metadata = _run_source()
    try:
        source = capture_run_entities(db)
        db.execute(
            "update runs set workload_key=? where record_type='agent_runtime_attempts' and id=1204",
            (workload_key,),
        )
        with pytest.raises(ValueError, match="Meeting RuntimeAttempt workload key"):
            migrate_run_entities(
                db, source, task_metadata=task_metadata,
                run_handoff=_run_handoff(db, source),
            )
    finally:
        db.close()


def test_inverse_meeting_runtime_key_rejects_unsupported_suffix():
    db, task_metadata = _run_source()
    try:
        source = capture_run_entities(db)
        db.execute(
            "update runs set workload_key='201:audit' "
            "where record_type='agent_runtime_attempts' and id=1204"
        )
        forward = migrate_run_entities(
            db, source, task_metadata=task_metadata,
            run_handoff=_run_handoff(db, source, identity_edges=[
                dict(source_kind="agent_runs", source_physical_id=1102, canonical_id=9112),
            ]),
        )
        db.execute("update runtime_attempts set workload_key='9112:other' where id=1204")
        with pytest.raises(ValueError, match="malformed Meeting RuntimeAttempt workload key"):
            restore_run_entities(db, source, forward, task_metadata=task_metadata)
    finally:
        db.close()


def test_professional_attempt_owner_survives_two_json_inverse_reforward_cycles():
    db, task_metadata = _run_source()
    try:
        source = capture_run_entities(db)
        edges = [
            dict(source_kind="agent_runs", source_physical_id=1101, canonical_id=9111),
            dict(source_kind="agent_runs", source_physical_id=1102, canonical_id=9112),
        ]
        highwaters = {owner: None for owner in _run_handoff(db, source)["owner_highwaters"]}
        handoff = json.loads(json.dumps(_run_handoff(
            db, source, identity_edges=edges, owner_highwaters=highwaters)))
        forward = migrate_run_entities(db, source, task_metadata=task_metadata, run_handoff=handoff)
        for _ in range(2):
            result = restore_run_entities(db, source, forward, task_metadata=task_metadata)
            assert tuple(db.execute(
                "select id,parent_id,workload_key from runs where record_type='agent_runtime_attempts' "
                "and id in (1201,1202,1204,1214) order by id")) == (
                    (1201, None, "201:decision_repair.2"),
                    (1202, None, "201"), (1204, None, "201"),
                    (1214, None, "201:decision_repair.3"))
            source = capture_run_entities(db)
            handoff = json.loads(json.dumps(result["run_handoff"]))
            forward = migrate_run_entities(db, source, task_metadata=task_metadata,
                                           run_handoff=handoff)
            assert tuple(db.execute(
                "select id,parent_id from runtime_attempts where id in (1201,1202,1204,1214) order by id")) == (
                    (1201, 9111), (1202, 9111), (1204, 9112), (1214, 9111))
            assert db.execute("select parent_id from runtime_attempts where id=1211").fetchone()[0] is None
            assert db.execute("select parent_id from runtime_attempts where id=1205").fetchone()[0] is None
    finally:
        db.close()


def test_structured_okr_attempt_parent_survives_two_json_inverse_reforward_cycles():
    db, task_metadata = _run_source()
    try:
        source = capture_run_entities(db)
        edges = [dict(source_kind="agent_runs", source_physical_id=1103, canonical_id=9113),
                 dict(source_kind="agent_runtime_attempts", source_physical_id=1205,
                      canonical_id=9113)]
        handoff = _run_handoff(db, source, identity_edges=edges)
        handoff["runtime_attempt_parent_edges"] = [
            {"attempt_source_physical_id": 1205, "run_source_physical_id": 1103}]
        from app.run_entity_schema import run_handoff_digest
        handoff["digest"] = run_handoff_digest(
            handoff["source_context"], handoff["identity_edges"],
            handoff["runtime_attempt_parent_edges"],
            handoff["runtime_attempt_orphan_workload_edges"], handoff["owner_highwaters"])
        forward = migrate_run_entities(db, source, task_metadata=task_metadata,
                                       run_handoff=json.loads(json.dumps(handoff)))
        assert db.execute("select id,parent_id from runtime_attempts where id=9113").fetchone() == (9113, 9113)
        for _ in range(2):
            result = restore_run_entities(db, source, forward, task_metadata=task_metadata)
            assert db.execute("select parent_id from runs where id=1205").fetchone()[0] is None
            assert result["run_handoff"]["owner_highwaters"]["runtime_attempts"] >= 9113
            ids = [row[0] for row in db.execute("select id from runs")]
            assert len(ids) == len(set(ids))
            assert result["run_handoff"]["runtime_attempt_parent_edges"] == [
                {"attempt_source_physical_id": 1205, "run_source_physical_id": 1103}]
            source = capture_run_entities(db)
            forward = migrate_run_entities(db, source, task_metadata=task_metadata,
                run_handoff=json.loads(json.dumps(result["run_handoff"])))
            assert db.execute("select id,parent_id from runtime_attempts where id=9113").fetchone() == (9113, 9113)
    finally:
        db.close()


def test_new_structured_okr_run_and_attempt_parent_edges_allocate_before_inverse():
    from tests.test_run_entity_definitions import insert_attempt, valid_attempt
    from app.task_entity_schema import capture_task_entities, migrate_task_entities, restore_task_entities

    db, task_forward, task_source_metadata = _run_source(include_task_source_metadata=True)
    try:
        run_source_metadata = capture_run_entities(db)
        handoff = _run_handoff(db, run_source_metadata, identity_edges=[
            dict(source_kind="agent_runs", source_physical_id=1103, canonical_id=9113)])
        run_forward = migrate_run_entities(db, run_source_metadata, task_metadata=task_forward,
                                           run_handoff=handoff)
        # Existing mapped Run + newly inserted attempt exercises one mixed edge.
        db.execute("update runtime_attempts set parent_id=9113 where id=1205")
        insert_attempt(db, valid_attempt(parent_id=9113, workload_kind="structured",
            workload_key="222", attempt=2, status="completed"))
        mapped_run_attempt = db.execute("select max(id) from runtime_attempts").fetchone()[0]
        # Both the Run and its attempt are new native facts with no identity-map row.
        db.execute("insert into runs(id,task_id,session_id,created_at,transcript_start,transcript_end) "
                   "values(1800,222,'session-new-okr','created',0,0)")
        db.execute("insert into run_provenance values(1800,'okr_review_runs',NULL)")
        insert_attempt(db, valid_attempt(parent_id=1800, workload_kind="structured",
            workload_key="222", attempt=3, status="completed"))
        new_run_attempt = db.execute("select max(id) from runtime_attempts").fetchone()[0]

        def owner_facts():
            tables = [name for name in _TARGET_TABLES if name != "run_entity_identity_map"]
            return {table: sorted((tuple(row) for row in db.execute(f'select * from "{table}"')),
                                 key=repr) for table in tables}

        expected = owner_facts()
        stable_sequences = None
        for cycle in (1, 2):
            task_inverse = restore_task_entities(db, task_source_metadata, task_forward)
            result = restore_run_entities(db, run_source_metadata, run_forward,
                                          task_metadata=task_forward,
                                          task_inverse_metadata=task_inverse)
            handoff = json.loads(json.dumps(result["run_handoff"]))
            edges = handoff["runtime_attempt_parent_edges"]
            assert len(edges) == 3
            run_edges = {edge["canonical_id"]: edge["source_physical_id"]
                         for edge in handoff["identity_edges"]
                         if edge["source_kind"] == "agent_runs"}
            attempt_edges = {edge["canonical_id"]: edge["source_physical_id"]
                             for edge in handoff["identity_edges"]
                             if edge["source_kind"] == "agent_runtime_attempts"}
            assert edges == sorted([
                {"attempt_source_physical_id": 1205,
                 "run_source_physical_id": run_edges[9113]},
                {"attempt_source_physical_id": attempt_edges[mapped_run_attempt],
                 "run_source_physical_id": run_edges[9113]},
                {"attempt_source_physical_id": attempt_edges[new_run_attempt],
                 "run_source_physical_id": run_edges[1800]},
            ], key=lambda edge: (edge["attempt_source_physical_id"],
                                 edge["run_source_physical_id"]))
            allocated_source_ids = [edge["source_physical_id"]
                                    for edge in handoff["identity_edges"]]
            assert len(allocated_source_ids) == len(set(allocated_source_ids))
            edge_source_ids = tuple(edge["attempt_source_physical_id"] for edge in edges)
            assert tuple(db.execute("select parent_id from runs where id in (?,?,?) order by id",
                                    edge_source_ids)) == ((None,), (None,), (None,))
            parentless_source = 1206
            assert db.execute("select parent_id,workload_key from runs where id=?",
                              (parentless_source,)).fetchone() == (None, "week:manager:digest")

            task_source_metadata = capture_task_entities(db)
            run_source_metadata = capture_run_entities(db)
            task_forward = migrate_task_entities(db, task_source_metadata,
                source_context=task_inverse["source_context"],
                inverse_identity=json.loads(json.dumps(task_inverse["inverse_identity"])))
            run_forward = migrate_run_entities(db, run_source_metadata, task_metadata=task_forward,
                run_handoff=handoff)
            assert owner_facts() == expected
            after_sequences = tuple(tuple(row) for row in db.execute(
                "select rowid,name,seq from sqlite_sequence order by rowid"))
            if stable_sequences is not None:
                assert after_sequences == stable_sequences
            stable_sequences = after_sequences
            assert db.execute("select parent_id from runtime_attempts where id=1205").fetchone() == (9113,)
    finally:
        db.close()


def test_new_structured_parent_wrong_request_task_refuses_without_mutation():
    from tests.test_run_entity_definitions import insert_attempt, valid_attempt
    from app.task_entity_schema import restore_task_entities

    db, task_forward, task_source_metadata = _run_source(include_task_source_metadata=True)
    try:
        source = capture_run_entities(db)
        forward = migrate_run_entities(db, source, task_metadata=task_forward,
                                       run_handoff=_run_handoff(db, source))
        db.execute("insert into runs(id,task_id,session_id,created_at,transcript_start,transcript_end) "
                   "values(1800,222,'session-new-okr','created',0,0)")
        db.execute("insert into run_provenance values(1800,'okr_review_runs',NULL)")
        insert_attempt(db, valid_attempt(parent_id=1800, workload_kind="structured",
            workload_key="222", attempt=2, status="completed"))
        attempt_id = db.execute("select max(id) from runtime_attempts").fetchone()[0]
        task_inverse = restore_task_entities(db, task_source_metadata, task_forward)
        db.execute("update runtime_attempts set workload_key='221' where id=?", (attempt_id,))
        before = {table: sorted((tuple(row) for row in db.execute(f'select * from "{table}"')),
                                key=repr) for table in _TARGET_TABLES}
        sequences = tuple(tuple(row) for row in db.execute(
            "select rowid,name,seq from sqlite_sequence order by rowid"))
        with pytest.raises(ValueError, match="Task reference|structured RuntimeAttempt"):
            restore_run_entities(db, source, forward, task_metadata=task_forward,
                                 task_inverse_metadata=task_inverse)
        after = {table: sorted((tuple(row) for row in db.execute(f'select * from "{table}"')),
                               key=repr) for table in _TARGET_TABLES}
        assert after == before
        assert tuple(tuple(row) for row in db.execute(
            "select rowid,name,seq from sqlite_sequence order by rowid")) == sequences
    finally:
        db.close()


@pytest.mark.parametrize("mutation", ["duplicate", "dangling_attempt", "wrong_parent_kind",
                                      "wrong_provenance", "digest"])
def test_invalid_structured_parent_handoff_rejects_before_source_replacement(mutation):
    db, task_metadata = _run_source()
    try:
        source = capture_run_entities(db)
        handoff = _run_handoff(db, source)
        edge = {"attempt_source_physical_id": 1205, "run_source_physical_id": 1103}
        handoff["runtime_attempt_parent_edges"] = [edge]
        if mutation == "duplicate":
            handoff["runtime_attempt_parent_edges"].append(edge.copy())
        elif mutation == "dangling_attempt":
            handoff["runtime_attempt_parent_edges"][0]["attempt_source_physical_id"] = 99999
        elif mutation == "wrong_parent_kind":
            handoff["runtime_attempt_parent_edges"][0]["run_source_physical_id"] = 1201
        elif mutation == "wrong_provenance":
            handoff["runtime_attempt_parent_edges"][0]["run_source_physical_id"] = 1101
        from app.run_entity_schema import run_handoff_digest
        if mutation != "digest":
            handoff["digest"] = run_handoff_digest(
                handoff["source_context"], handoff["identity_edges"],
                handoff["runtime_attempt_parent_edges"],
            handoff["runtime_attempt_orphan_workload_edges"], handoff["owner_highwaters"])
        before = tuple(db.execute("select id,record_type from runs order by id"))
        with pytest.raises(ValueError):
            migrate_run_entities(db, source, task_metadata=task_metadata, run_handoff=handoff)
        assert tuple(db.execute("select id,record_type from runs order by id")) == before
        assert db.execute("select 1 from sqlite_schema where name='runs'").fetchone()
        assert not db.execute("select 1 from sqlite_schema where name='runtime_attempts'").fetchone()
    finally:
        db.close()


@pytest.mark.parametrize("attempt_id,parent_id", [(1201, None), (1201, 1102), (1205, 1101)])
def test_inverse_rejects_native_attempt_owner_that_source_cannot_represent(attempt_id, parent_id):
    db, task_metadata = _run_source()
    try:
        source = capture_run_entities(db)
        forward = migrate_run_entities(db, source, task_metadata=task_metadata,
                                       run_handoff=_run_handoff(db, source))
        db.execute("update runtime_attempts set parent_id=? where id=?", (parent_id, attempt_id))
        with pytest.raises(ValueError, match="cannot be represented"):
            restore_run_entities(db, source, forward, task_metadata=task_metadata)
        assert db.execute("select parent_id from runtime_attempts where id=?", (attempt_id,)).fetchone()[0] == parent_id
        assert db.execute("select 1 from sqlite_schema where type='table' and name='runtime_attempts'").fetchone()
    finally:
        db.close()


def test_new_native_task_run_attempt_gets_source_backed_owner_on_reforward():
    from app.task_entity_schema import capture_task_entities, migrate_task_entities, restore_task_entities
    from tests.test_run_entity_definitions import insert_attempt, valid_attempt

    db, task_forward, task_source_metadata = _run_source(include_task_source_metadata=True)
    try:
        source = capture_run_entities(db)
        forward = migrate_run_entities(db, source, task_metadata=task_forward,
                                       run_handoff=_run_handoff(db, source))
        db.execute("insert into runs(id,task_id,status,session_id,error,completed_at,created_at,updated_at) "
                   "values(1800,220,'completed','','','now','now','now')")
        db.execute("insert into run_provenance values(1800,'task_agent_runs',NULL)")
        db.execute("insert into run_projections values(1800,'{}')")
        insert_attempt(db, valid_attempt(parent_id=1800, workload_kind="task",
                                         workload_key="1800", status="completed"))
        attempt_id = db.execute("select max(id) from runtime_attempts").fetchone()[0]

        task_inverse = restore_task_entities(db, task_source_metadata, task_forward)
        run_inverse = restore_run_entities(db, source, forward, task_metadata=task_forward,
                                           task_inverse_metadata=task_inverse)
        edges = run_inverse["run_handoff"]["identity_edges"]
        source_run_id = next(edge["source_physical_id"] for edge in edges
                             if edge["source_kind"] == "agent_runs" and edge["canonical_id"] == 1800)
        source_attempt_id = next(edge["source_physical_id"] for edge in edges
                                 if edge["source_kind"] == "agent_runtime_attempts"
                                 and edge["canonical_id"] == attempt_id)
        assert db.execute("select source_type,legacy_key from runs where id=?",
                          (source_run_id,)).fetchone() == ("task_agent_runs", str(source_run_id))
        assert db.execute("select parent_id,workload_key from runs where id=?",
                          (source_attempt_id,)).fetchone() == (None, str(source_run_id))

        new_task_capture = capture_task_entities(db)
        new_task_forward = migrate_task_entities(db, new_task_capture)
        new_run_source = capture_run_entities(db)
        migrate_run_entities(db, new_run_source, task_metadata=new_task_forward,
                             run_handoff=json.loads(json.dumps(run_inverse["run_handoff"])))
        assert db.execute("select parent_id,workload_key from runtime_attempts where id=?",
                          (attempt_id,)).fetchone() == (1800, "1800")
    finally:
        db.close()


def _closed_offline_email_task_attempt_roundtrip(
        tmp_path, *, predecessor_reader=None, preserve_database=False):

    from app.email_store import EmailStore
    from app.email_task_adapter import EmailClassificationTaskAdapter
    from app.run_entity_storage import create_runtime_attempt
    from app.task_entity_schema import (
        capture_task_entities, migrate_task_entities, restore_task_entities,
    )

    db, task_forward, task_source_metadata = _run_source(include_task_source_metadata=True)
    source = capture_run_entities(db)
    forward = migrate_run_entities(
        db, source, task_metadata=task_forward, run_handoff=_run_handoff(db, source)
    )
    db.commit()
    path = tmp_path / "task2d6-offline-inverse.sqlite3"
    try:
        path.unlink(missing_ok=True)
        disk = sqlite3.connect(path)
        db.backup(disk)
        disk.close()
        db.close()

        email_store = EmailStore.__new__(EmailStore)
        email_store.path = path
        adapter = EmailClassificationTaskAdapter(email_store)
        canonical_id = adapter.open_offline_task(
            task_id="email-classification:" + "a" * 64, owner="email-training-labeler"
        )
        with email_store._connect() as native:
            attempt_values = {
                "parent_id": None, "workload_kind": "email_classification",
                "workload_key": canonical_id, "attempt_number": 1,
                "route_name": "codex_oauth", "runtime_kind": "codex_cli",
                "credential_mode": "oauth", "model": "gpt-6-luna",
                "session_mode": "fresh", "source_session_id": "",
                "attempt_purpose": "normal", "validation_retry_policy_id": "",
                "validation_result_schema_id": "", "session_id": "session-offline",
                "transcript_reference": "trace-offline", "transcript_start": 0,
                "transcript_end": 1, "status": "completed", "failure_class": "",
                "failure_code": "", "failover_permitted": 0, "lease_owner": "",
                "lease_expires_at": "", "first_effect_started_at": "",
                "result_schema_id": "", "started_at": "start", "finished_at": "finish",
                "created_at": "created", "updated_at": "updated",
            }
            attempt = create_runtime_attempt(native, values=attempt_values)
            second_attempt_values = {**attempt_values, "attempt_number": 2,
                                     "session_id": "session-offline-2",
                                     "transcript_reference": "trace-offline-2"}
            second_attempt = create_runtime_attempt(native, values=second_attempt_values)
            attempt_facts = tuple(native.execute(
                "select * from runtime_attempts where id=?", (attempt["id"],)).fetchone())
            second_attempt_facts = tuple(native.execute(
                "select * from runtime_attempts where id=?", (second_attempt["id"],)).fetchone())
        adapter.close_offline_task(canonical_id)

        with sqlite3.connect(path) as native:
            native.row_factory = sqlite3.Row
            native.execute("pragma foreign_keys=off")
            native.execute("begin immediate")
            task_inverse = restore_task_entities(native, task_source_metadata, task_forward)
            run_inverse = restore_run_entities(
                native, source, forward, task_metadata=task_forward,
                task_inverse_metadata=task_inverse,
            )
            assert native.execute(
                "select count(*) from tasks where record_type='email_agent_classification_tasks' "
                "and legacy_key=?", (str(canonical_id),)
            ).fetchone()[0] == 0
            task_water = native.execute(
                "select seq from sqlite_sequence where name='tasks'"
            ).fetchone()
            assert task_water is not None and task_water[0] >= int(canonical_id)
            portable_handoff = json.loads(json.dumps(run_inverse["run_handoff"]))
            orphan_edges = portable_handoff["runtime_attempt_orphan_workload_edges"]
            assert len(orphan_edges) == 2
            assert len({edge["source_operation_key"] for edge in orphan_edges}) == 1
            assert all(edge["native_workload_key"] == str(canonical_id) for edge in orphan_edges)
            assert re.fullmatch(r"email-classification:[0-9a-f]{64}",
                                orphan_edges[0]["source_operation_key"])
            native.commit()
            predecessor_source_before_read = tuple(native.execute(
                "select * from runs order by id"))
            if predecessor_reader is None:
                old_attempts = native.execute(
                    "select cast(legacy_key as integer) as id,parent_id as agent_run_id,status,session_id "
                    "from runs where record_type='agent_runtime_attempts' and workload_key=? "
                    "order by cast(attempt_number as integer)",
                    (orphan_edges[0]["source_operation_key"],),
                ).fetchall()
                assert [row["id"] for row in old_attempts] == [
                    edge["attempt_source_physical_id"] for edge in orphan_edges]
                assert all(row["agent_run_id"] is None and row["status"] == "completed"
                           for row in old_attempts)
                assert [row["session_id"] for row in old_attempts] == [
                    "session-offline", "session-offline-2"]
            else:
                old_read = predecessor_reader(path, orphan_edges[0]["source_operation_key"])
                old_attempts = old_read["attempts"]
                assert old_read["parent_runnable"] is False
                assert [row["id"] for row in old_attempts] == [
                    edge["attempt_source_physical_id"] for edge in orphan_edges]
                assert all(row["agent_run_id"] is None and row["status"] == "completed"
                           for row in old_attempts)
                assert [row["session_id"] for row in old_attempts] == [
                    "session-offline", "session-offline-2"]
            assert tuple(native.execute("select * from runs order by id")) == \
                predecessor_source_before_read
            native.execute("begin immediate")
            fresh_task_capture = capture_task_entities(native)
            fresh_task_forward = migrate_task_entities(
                native, fresh_task_capture,
                source_context=task_inverse["source_context"],
                inverse_identity=json.loads(json.dumps(task_inverse["inverse_identity"])),
            )
            fresh_run_capture = capture_run_entities(native)
            fresh_run_forward = migrate_run_entities(
                native, fresh_run_capture, task_metadata=fresh_task_forward,
                run_handoff=portable_handoff,
            )
            persisted = native.execute(
                "select workload_key,workload_kind,status,parent_id,route_name,session_id,"
                "transcript_reference,transcript_start,transcript_end,failure_code,started_at,finished_at "
                "from runtime_attempts where id=?", (attempt["id"],)
            ).fetchone()
            assert tuple(persisted) == (
                str(canonical_id), "email_classification", "completed", None,
                "codex_oauth", "session-offline", "trace-offline", 0, 1, "", "start", "finish",
            )
            persisted_second = native.execute(
                "select workload_key,workload_kind,status,parent_id,route_name,session_id,"
                "transcript_reference,transcript_start,transcript_end,failure_code,started_at,finished_at "
                "from runtime_attempts where id=?", (second_attempt["id"],)
            ).fetchone()
            assert tuple(persisted_second) == (
                str(canonical_id), "email_classification", "completed", None,
                "codex_oauth", "session-offline-2", "trace-offline-2", 0, 1, "", "start", "finish",
            )
            task_inverse_2 = restore_task_entities(
                native, fresh_task_capture, fresh_task_forward)
            run_inverse_2 = restore_run_entities(
                native, fresh_run_capture, fresh_run_forward,
                task_metadata=fresh_task_forward,
                task_inverse_metadata=task_inverse_2,
            )
            handoff_2 = json.loads(json.dumps(run_inverse_2["run_handoff"]))
            assert handoff_2["runtime_attempt_orphan_workload_edges"] == \
                portable_handoff["runtime_attempt_orphan_workload_edges"]
            assert handoff_2["runtime_attempt_orphan_workload_edges"][0]["native_workload_key"] == \
                str(canonical_id)
            assert {edge["source_operation_key"] for edge in
                    handoff_2["runtime_attempt_orphan_workload_edges"]} == {
                orphan_edges[0]["source_operation_key"]}
            native.commit()
            if predecessor_reader is None:
                old_attempts_2 = native.execute(
                    "select cast(legacy_key as integer) as id,parent_id as agent_run_id,status "
                    "from runs where record_type='agent_runtime_attempts' and workload_key=? "
                    "order by cast(attempt_number as integer)",
                    (orphan_edges[0]["source_operation_key"],),
                ).fetchall()
                assert [row["id"] for row in old_attempts_2] == [
                    edge["attempt_source_physical_id"]
                    for edge in handoff_2["runtime_attempt_orphan_workload_edges"]]
                assert all(row["agent_run_id"] is None and row["status"] == "completed"
                           for row in old_attempts_2)
            else:
                old_read_2 = predecessor_reader(path, orphan_edges[0]["source_operation_key"])
                old_attempts_2 = old_read_2["attempts"]
                assert old_read_2["parent_runnable"] is False
                assert [row["id"] for row in old_attempts_2] == [
                    edge["attempt_source_physical_id"]
                    for edge in handoff_2["runtime_attempt_orphan_workload_edges"]]
                assert all(row["agent_run_id"] is None and row["status"] == "completed"
                           for row in old_attempts_2)
            native.execute("begin immediate")
            task_capture_2 = capture_task_entities(native)
            task_forward_2 = migrate_task_entities(
                native, task_capture_2,
                source_context=task_inverse_2["source_context"],
                inverse_identity=json.loads(json.dumps(task_inverse_2["inverse_identity"])),
            )
            run_capture_2 = capture_run_entities(native)
            migrate_run_entities(
                native, run_capture_2, task_metadata=task_forward_2,
                run_handoff=handoff_2,
            )
            persisted_again = native.execute(
                "select workload_key,workload_kind,status,parent_id,route_name,session_id,"
                "transcript_reference,transcript_start,transcript_end,failure_code,started_at,finished_at "
                "from runtime_attempts where id=?", (attempt["id"],)
            ).fetchone()
            assert tuple(persisted_again) == tuple(persisted)
            persisted_second_again = native.execute(
                "select workload_key,workload_kind,status,parent_id,route_name,session_id,"
                "transcript_reference,transcript_start,transcript_end,failure_code,started_at,finished_at "
                "from runtime_attempts where id=?", (second_attempt["id"],)
            ).fetchone()
            assert tuple(persisted_second_again) == tuple(persisted_second)
            assert tuple(native.execute(
                "select * from runtime_attempts where id=?", (attempt["id"],)).fetchone()) == \
                attempt_facts
            assert tuple(native.execute(
                "select * from runtime_attempts where id=?", (second_attempt["id"],)).fetchone()) == \
                second_attempt_facts
    finally:
        try:
            db.close()
        except Exception:
            pass
        if not preserve_database:
            path.unlink(missing_ok=True)


def test_closed_offline_email_task_attempt_survives_current_task_run_inverse(tmp_path):
    _closed_offline_email_task_attempt_roundtrip(tmp_path)


def test_run_inverse_keeps_new_live_email_task_owner_without_task_inverse():
    from app.task_entity_storage import create_task

    db, task_metadata = _run_source()
    try:
        db.row_factory = sqlite3.Row
        now = "now"
        task = create_task(db, values={
            "kind": "email_classification", "status": "pending", "available_at": now,
            "attempts": 0, "error": "", "owner_pid": None, "generation": 0,
            "lease_owner": "", "lease_expires_at": "", "created_at": now,
            "updated_at": now,
        })
        source = capture_run_entities(db)
        forward = migrate_run_entities(
            db, source, task_metadata=task_metadata, run_handoff=_run_handoff(db, source)
        )
        email_attempt = db.execute(
            "select id from runtime_attempts where workload_kind='email_classification'"
        ).fetchone()
        db.execute(
            "update runtime_attempts set workload_key=? where id=?",
            (str(task["id"]), email_attempt[0]),
        )
        inverse = restore_run_entities(
            db, source, forward, task_metadata=task_metadata
        )
        attempt = db.execute(
            "select workload_key from runs where record_type='agent_runtime_attempts' "
            "and id=?", (email_attempt[0],)
        ).fetchone()
        assert tuple(attempt) == (str(task["id"]),)
        assert inverse["run_handoff"]["runtime_attempt_orphan_workload_edges"] == []
    finally:
        db.close()


@pytest.mark.parametrize("mutation, message", [
    ("malformed", "malformed orphan"),
    ("duplicate", "duplicate orphan"),
    ("wrongkind", "wrong-kind live Task"),
    ("sourcecollision", "source key collides"),
    ("wrongparent", "edge differs from source attempt"),
    ("wrongworkload", "edge differs from source attempt"),
    ("digest", "handoff digest"),
])
def test_email_orphan_handoff_rejections_precede_run_schema_replacement(mutation, message):
    import hashlib
    from app.run_entity_schema import run_handoff_digest

    db, task_metadata = _run_source()
    try:
        attempt_id = db.execute(
            "select id from runs where record_type='agent_runtime_attempts' "
            "and workload_kind='email_classification' limit 1"
        ).fetchone()[0]
        native_key = "416922"
        source_key = "email-classification:" + hashlib.sha256(
            json.dumps({"kind": "email_classification", "native_workload_key": native_key},
                       sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        db.execute("update runs set workload_key=? where id=?", (source_key, attempt_id))
        source = capture_run_entities(db)
        if mutation == "wrongparent":
            db.execute("pragma ignore_check_constraints=on")
            db.execute("update runs set parent_id=1001 where id=?", (attempt_id,))
            source = capture_run_entities(db)
        elif mutation == "wrongworkload":
            db.execute("update runs set workload_kind='email_unsubscribe_page' where id=?",
                       (attempt_id,))
            source = capture_run_entities(db)
        handoff = _run_handoff(db, source)
        edge = {"attempt_source_physical_id": attempt_id,
                "workload_kind": "email_classification",
                "source_operation_key": source_key,
                "native_workload_key": native_key}
        handoff["runtime_attempt_orphan_workload_edges"] = [edge]
        if mutation == "malformed":
            edge["native_workload_key"] = "0416922"
        elif mutation == "duplicate":
            handoff["runtime_attempt_orphan_workload_edges"].append(edge.copy())
        elif mutation == "wrongkind":
            edge["native_workload_key"] = "201"  # live reply Task
        elif mutation == "sourcecollision":
            db.execute("update task_run_identity_map set public_key=?,public_key_type='text' "
                       "where source_kind='email_agent_classification_tasks'",
                       (source_key,))
            for identity in task_metadata["identity_edges"]:
                if identity["source_kind"] == "email_agent_classification_tasks":
                    identity["public_key"] = source_key
        elif mutation == "digest":
            handoff["digest"] = "0" * 64
        if mutation != "digest":
            handoff["digest"] = run_handoff_digest(
                handoff["source_context"], handoff["identity_edges"],
                handoff["runtime_attempt_parent_edges"],
                handoff["runtime_attempt_orphan_workload_edges"],
                handoff["owner_highwaters"],
            )
        before_rows = tuple(db.execute("select * from runs order by id"))
        with pytest.raises(ValueError, match=message):
            migrate_run_entities(db, source, task_metadata=task_metadata, run_handoff=handoff)
        assert tuple(db.execute("select * from runs order by id")) == before_rows
        assert logical_run_current_schema.current_run11_schema_is_current(db)
    finally:
        db.close()


@pytest.mark.parametrize("mutation, message", [
    ("missing", "portable B3 identity handoff"),
    ("digest", "digest mismatch"),
    ("context", "mismatched portable Run handoff"),
    ("unknown", "unknown or malformed"),
    ("duplicate", "duplicate portable"),
    ("collision", "canonical Run ID collision"),
    ("dangling", "dangling portable"),
])
def test_portable_handoff_rejections_precede_source_replacement(mutation, message):
    db, task_metadata = _run_source()
    try:
        source = capture_run_entities(db)
        original = tuple(db.execute("select * from runs order by id"))
        handoff = _run_handoff(db, source)
        if mutation == "missing":
            handoff = None
        elif mutation == "digest":
            handoff["digest"] = "0" * 64
        elif mutation == "context":
            handoff["source_context"] = "f" * 64
            from app.run_entity_schema import run_handoff_digest
            handoff["digest"] = run_handoff_digest(
                handoff["source_context"], handoff["identity_edges"],
                handoff["runtime_attempt_parent_edges"],
            handoff["runtime_attempt_orphan_workload_edges"], handoff["owner_highwaters"])
        elif mutation == "unknown":
            handoff["identity_edges"] = [dict(source_kind="unknown", source_physical_id=1,
                                                canonical_id=2)]
            from app.run_entity_schema import run_handoff_digest
            handoff["digest"] = run_handoff_digest(
                handoff["source_context"], handoff["identity_edges"],
                handoff["runtime_attempt_parent_edges"],
            handoff["runtime_attempt_orphan_workload_edges"], handoff["owner_highwaters"])
        elif mutation == "duplicate":
            edge = dict(source_kind="agent_runs", source_physical_id=1001, canonical_id=9101)
            handoff["identity_edges"] = [edge, edge.copy()]
            from app.run_entity_schema import run_handoff_digest
            handoff["digest"] = run_handoff_digest(
                handoff["source_context"], handoff["identity_edges"],
                handoff["runtime_attempt_parent_edges"],
            handoff["runtime_attempt_orphan_workload_edges"], handoff["owner_highwaters"])
        elif mutation == "collision":
            handoff["identity_edges"] = [
                dict(source_kind="agent_runs", source_physical_id=1001, canonical_id=9101),
                dict(source_kind="agent_runs", source_physical_id=1002, canonical_id=9101)]
            from app.run_entity_schema import run_handoff_digest
            handoff["digest"] = run_handoff_digest(
                handoff["source_context"], handoff["identity_edges"],
                handoff["runtime_attempt_parent_edges"],
            handoff["runtime_attempt_orphan_workload_edges"], handoff["owner_highwaters"])
        else:
            handoff["identity_edges"] = [dict(source_kind="agent_runs", source_physical_id=9999,
                                                canonical_id=9101)]
            from app.run_entity_schema import run_handoff_digest
            handoff["digest"] = run_handoff_digest(
                handoff["source_context"], handoff["identity_edges"],
                handoff["runtime_attempt_parent_edges"],
            handoff["runtime_attempt_orphan_workload_edges"], handoff["owner_highwaters"])
        with pytest.raises(ValueError, match=message):
            migrate_run_entities(db, source, task_metadata=task_metadata, run_handoff=handoff)
        assert tuple(db.execute("select * from runs order by id")) == original
        assert logical_run_current_schema.current_run11_schema_is_current(db)
    finally:
        db.close()


@pytest.mark.parametrize("highwater, next_id", [(0, 1), (12000, 12001)])
def test_empty_native_run_owner_retains_captured_highwater_after_migration(highwater, next_id):
    db, task_metadata = _run_source()
    try:
        db.execute("delete from runs where record_type='setup_wizard_events'")
        source = capture_run_entities(db)
        highwaters = {owner: None for owner in _run_handoff(db, source)["owner_highwaters"]}
        highwaters["setup_wizard_events"] = highwater
        migrate_run_entities(db, source, task_metadata=task_metadata,
                             run_handoff=_run_handoff(db, source, owner_highwaters=highwaters))
        assert db.execute("select count(*) from setup_wizard_events").fetchone()[0] == 0
        assert db.execute("select seq from sqlite_sequence where name='setup_wizard_events'").fetchone()[0] == highwater
        db.execute("insert into setup_wizard_events(step_id,action_id,status,summary,evidence_json,"
                   "stdout_excerpt,stderr_excerpt,created_at,finished_at) "
                   "values('step','action','completed','summary','{}','out','err','created','finished')")
        assert db.execute("select id from setup_wizard_events").fetchone()[0] == next_id
    finally:
        db.close()


def test_handoff_survives_real_old_source_insert_with_fresh_capture():
    from app.task_entity_schema import capture_task_entities, migrate_task_entities, restore_task_entities

    db, task_metadata, task_source_metadata = _run_source(include_task_source_metadata=True)
    try:
        restore_task_entities(db, task_source_metadata, task_metadata)
        before_write = capture_run_entities(db)
        edges = [dict(source_kind="agent_runs", source_physical_id=1001, canonical_id=9101)]
        handoff = _run_handoff(db, before_write, identity_edges=edges)
        _source_row(db, "agent_runs", {
            "id": 1500, "legacy_key": "1500", "task_id": 201,
            "execution_generation": "consumer-new", "role": "consumer", "proposal_revision": 0,
            "turn_attempt": 0, "operation_id": "consumer-new-op", "status": "completed",
            "session_id": "new-session", "transcript_start": 0, "transcript_end": 0,
            "structured_error_json": "{}", "lease_owner": "", "lease_expires_at": "",
            "started_at": "start", "completed_at": "done", "created_at": "created",
            "updated_at": "updated",
        })
        fresh_task_capture = capture_task_entities(db)
        fresh_task_metadata = migrate_task_entities(db, fresh_task_capture)
        fresh_source = capture_run_entities(db)
        assert handoff["source_context"] == _run_handoff(db, fresh_source)["source_context"]
        migrate_run_entities(db, fresh_source, task_metadata=fresh_task_metadata,
                             run_handoff=json.loads(json.dumps(handoff)))
        assert db.execute("select id,status from runs where id in (9101,1500) order by id").fetchall() == [
            (1500, "completed"), (9101, "completed")]
        assert db.execute(
            "select physical_id,target_id from run_entity_identity_map "
            "where source_kind='agent_runs' and physical_id in (1001,1500) order by physical_id"
        ).fetchall() == [(1001, 9101), (1500, 1500)]
    finally:
        db.close()


@pytest.mark.parametrize("public_key", ["201suffix", "201.0", b"201"])
def test_forward_uses_source_sqlite_cast_for_professional_public_identity(public_key):
    db, task_metadata = _run_source()
    try:
        db.execute("update runs set legacy_key=? where id=1101", (public_key,))
        stored_type, stored_key = db.execute(
            "select typeof(legacy_key),legacy_key from runs where id=1101").fetchone()
        assert stored_key == public_key
        assert stored_type == ("blob" if isinstance(public_key, bytes) else "text")
        # Exercise the selected source's actual public lookup expression and
        # ensure it identifies the intended physical Run before conversion.
        source_run = db.execute(
            "select id,cast(legacy_key as integer) from runs where record_type='agent_runs' "
            "and source_type='task_agent_runs' and cast(legacy_key as integer)=201"
        ).fetchone()
        assert source_run == (1101, 201)

        run_metadata = capture_run_entities(db)
        migrate_run_entities(db, run_metadata, task_metadata=task_metadata, run_handoff=_run_handoff(db, run_metadata))

        identity = db.execute(
            "select typeof(public_key),public_key,public_key_type,public_key_order "
            "from run_entity_identity_map where source_kind='agent_runs' and physical_id=1101"
        ).fetchone()
        assert identity == (stored_type, stored_key,
                            "blob" if stored_type == "blob" else "text", 201)
        assert db.execute(
            "select workload_key from runtime_attempts where workload_kind='task' "
            "and id=1201").fetchone()[0] == "1101:decision_repair.2"
    finally:
        db.close()


def test_forward_is_rollbackable_by_caller_and_preserves_all_source_allocators():
    db, task_metadata = _run_source()
    try:
        db.execute("insert into sqlite_sequence(name,seq) values('runs:codex_session_search_index',776)")
        source_objects = logical_run_current_schema.current_run11_objects()
        before_rows = tuple(db.execute("select * from runs order by id"))
        db.execute("insert into sqlite_sequence(name,seq) values('runs:codex_session_search_index',776)")
        db.execute("insert into sqlite_sequence(name,seq) values('runs:email_classifier_runtime_samples',6863)")
        db.execute("insert into sqlite_sequence(name,seq) values('runs:setup_wizard_events',22)")
        db.commit()
        db.execute("begin")
        unrelated = tuple(db.execute(
            "select rowid,name,seq from sqlite_sequence where name='runs_other'"))
        captured = capture_run_entities(db)
        assert {row[1:] for row in captured["sequence_rows"]} == {
            ("runs", 8000), ("runs:published", 8000),
            ("runs:codex_session_search_index", 776),
            ("runs:email_classifier_runtime_samples", 6863),
            ("runs:setup_wizard_events", 22),
        }
        assert all(row[1] != "runs_other" for row in captured["sequence_rows"])
        migrate_run_entities(db, captured, task_metadata=task_metadata, run_handoff=_run_handoff(db, captured))
        assert db.execute("select count(*) from run_provenance").fetchone()[0] == 3
        db.rollback()
        assert tuple(db.execute("select * from runs order by id")) == before_rows
        assert logical_run_current_schema.current_run11_schema_is_current(db)
        assert tuple(db.execute(
            "select rowid,name,seq from sqlite_sequence where name in "
            "('runs','runs:published','runs:codex_session_search_index',"
            "'runs:email_classifier_runtime_samples','runs:setup_wizard_events') order by rowid")) \
            == tuple(captured["sequence_rows"])
        assert tuple(db.execute(
            "select rowid,name,seq from sqlite_sequence where name='runs_other'")) == unrelated
        assert set(source_objects) == set(logical_run_current_schema.current_run11_objects())
    finally:
        db.close()


def test_wrong_existing_native_task_kind_mapping_is_rejected_before_run_writes():
    db, task_metadata = _run_source()
    try:
        run_metadata = capture_run_entities(db)
        wrong = next(edge for edge in task_metadata["identity_edges"]
                     if edge["source_kind"] == "email_agent_classification_tasks")
        wrong["target_id"] = 220  # existing Work Summary Task, not the Email Task
        db.execute(
            "update task_run_identity_map set target_id=220 "
            "where source_kind='email_agent_classification_tasks' and physical_id=?",
            (wrong["physical_id"],),
        )
        assert db.execute("select kind from tasks where id=220").fetchone()[0] == "work_summary"
        before = tuple(db.execute("select id,record_type,task_id from runs order by id"))
        with pytest.raises(ValueError, match="typed Task owner kind mismatch"):
            migrate_run_entities(db, run_metadata, task_metadata=task_metadata, run_handoff=_run_handoff(db, run_metadata))
        assert tuple(db.execute("select id,record_type,task_id from runs order by id")) == before
        assert db.execute("select 1 from sqlite_schema where name='run_provenance'").fetchone() is None
    finally:
        db.close()


def test_forward_preserves_a_captured_missing_physical_task_edge_without_fabrication():
    db, task_metadata = _run_source(orphan_task_id=999)
    try:
        metadata = capture_run_entities(db)
        assert ("runs", 1400, "tasks", 1) in metadata["run_fk_findings"]
        handoff = _run_handoff(
            db, metadata,
            identity_edges=[dict(source_kind="agent_runs", source_physical_id=1400,
                                 canonical_id=9140)])
        migrate_run_entities(db, metadata, task_metadata=task_metadata, run_handoff=handoff)
        assert db.execute("select task_id from runs where id=9140").fetchone()[0] == 999
        assert not db.execute("select 1 from tasks where id=999").fetchone()
        assert tuple(db.execute("pragma foreign_key_check(runs)")) == (("runs", 9140, "tasks", 1),)
    finally:
        db.close()


def test_existing_identity_inverse_restores_all_eight_current_source_kinds():
    db, task_metadata = _run_source()
    try:
        source_objects = logical_run_current_schema.current_run11_objects()
        source_rows = tuple(db.execute("select * from runs order by id"))
        run_metadata = capture_run_entities(db)
        forward_metadata = migrate_run_entities(db, run_metadata, task_metadata=task_metadata, run_handoff=_run_handoff(db, run_metadata))
        # Current native owner values are the inverse source of truth.
        db.execute("update runs set status='failed' where id=1001")
        db.execute("update runs set session_id='edited-session',transcript_start=3,"
                   "transcript_end=9 where id=1001")
        db.execute("update runs set error=? where id=1101", (b"edited\x00error",))
        db.execute("update run_projections set projection_json=? where run_id=1101",
                   (' { "edited": true } ',))
        db.execute("update runtime_attempts set session_id='attempt-session',transcript_start=4,"
                   "transcript_end=10 where id=1201")
        result = restore_run_entities(db, run_metadata, forward_metadata,
                                      task_metadata=task_metadata)
        assert result["rows"] == len(source_rows)
        assert logical_run_current_schema.current_run11_schema_is_current(db)
        expected_rows = []
        columns = tuple(row[1] for row in db.execute("pragma table_info(runs)"))
        for row in source_rows:
            values = list(row)
            if values[columns.index("id")] == 1001:
                values[columns.index("status")] = "failed"
                values[columns.index("session_id")] = "edited-session"
                values[columns.index("transcript_start")] = 3
                values[columns.index("transcript_end")] = 9
            if values[columns.index("id")] == 1101:
                values[columns.index("error")] = b"edited\x00error"
                values[columns.index("projection_json")] = ' { "edited": true } '
            if values[columns.index("id")] == 1201:
                values[columns.index("session_id")] = "attempt-session"
                values[columns.index("transcript_start")] = 4
                values[columns.index("transcript_end")] = 10
            expected_rows.append(tuple(values))
        assert tuple(db.execute("select * from runs order by id")) == tuple(expected_rows)
        assert db.execute("select status from runs where id=1001").fetchone() == ("failed",)
        assert db.execute("select session_id,transcript_start,transcript_end from runs where id=1001").fetchone() == (
            "edited-session", 3, 9)
        assert db.execute("select projection_json from runs where id=1101").fetchone() == (
            ' { "edited": true } ',)
        assert db.execute("select typeof(error),error from runs where id=1101").fetchone() == (
            "blob", b"edited\x00error")
        assert tuple(db.execute(
            "select rowid,name,seq from sqlite_sequence where name in "
            "('runs','runs:published','runs:codex_session_search_index') order by rowid")) == tuple(
                run_metadata["sequence_rows"])
        actual_objects = {row[0]: row[1] for row in db.execute(
            "select name,sql from sqlite_schema where name in (" +
            ",".join("?" for _ in source_objects) + ")", tuple(source_objects))}
        assert actual_objects == source_objects
        db.execute("update runs set status='completed' where id=1001")
        refoward = migrate_run_entities(db, run_metadata, task_metadata=task_metadata, run_handoff=_run_handoff(db, run_metadata))
        assert refoward["source_objects"] == source_objects
        assert db.execute("select status from runs where id=1001").fetchone() == ("completed",)
        assert db.execute("select session_id,transcript_start,transcript_end from runs where id=1001").fetchone() == (
            "edited-session", 3, 9)
        assert db.execute("select typeof(error),error from runs where id=1101").fetchone() == (
            "blob", b"edited\x00error")
        assert db.execute("select projection_json from run_projections where run_id=1101").fetchone() == (
            ' { "edited": true } ',)
        assert tuple(db.execute(
            "select rowid,name,seq from sqlite_sequence where name in "
            "('runs','runs:published','runs:codex_session_search_index') order by rowid")) == tuple(
                run_metadata["sequence_rows"])
        assert db.execute("select 1 from sqlite_schema where name='run_entity_identity_map'").fetchone()
    finally:
        db.close()


def test_existing_identity_inverse_rejects_new_native_run_identity_without_mutation():
    db, task_metadata = _run_source()
    try:
        run_metadata = capture_run_entities(db)
        forward_metadata = migrate_run_entities(db, run_metadata, task_metadata=task_metadata, run_handoff=_run_handoff(db, run_metadata))
        before = tuple(db.execute("select * from runs order by id"))
        db.execute("insert into runs(id,created_at) values(90000,'new native')")
        db.execute("insert into run_entity_identity_map(source_kind,physical_id,public_key,public_key_type,target_id) "
                   "values('agent_runs',90000,'90000','text',90000)")
        with pytest.raises(ValueError, match="Run inverse identities differ"):
            restore_run_entities(db, run_metadata, forward_metadata, task_metadata=task_metadata)
        assert tuple(db.execute("select * from runs where id<90000 order by id")) == before
        assert db.execute("select 1 from sqlite_schema where name='run_entity_identity_map'").fetchone()
    finally:
        db.close()


def test_existing_identity_inverse_rejects_native_owned_catalog_drift_before_retirement():
    db, task_metadata = _run_source()
    try:
        run_metadata = capture_run_entities(db)
        forward_metadata = migrate_run_entities(db, run_metadata, task_metadata=task_metadata, run_handoff=_run_handoff(db, run_metadata))
        before = tuple(db.execute("select * from runs order by id"))
        db.execute("create index idx_unexpected_run_inverse on runs(status)")
        with pytest.raises(ValueError, match="Run native target declaration changed: runs"):
            restore_run_entities(db, run_metadata, forward_metadata, task_metadata=task_metadata)
        assert tuple(db.execute("select * from runs order by id")) == before
        assert db.execute("select 1 from sqlite_schema where name='run_entity_identity_map'").fetchone()
        assert db.execute("select 1 from sqlite_schema where name='idx_unexpected_run_inverse'").fetchone()
    finally:
        db.close()


def test_inverse_caller_rollback_preserves_native_facts_and_unrelated_schema_allocators():
    db, task_metadata = _run_source()
    try:
        db.execute("create table unrelated_run_inverse(id integer primary key autoincrement,value blob)")
        db.execute("create index unrelated_run_inverse_value on unrelated_run_inverse(value)")
        db.execute("insert into unrelated_run_inverse(value) values(x'00ff')")
        run_metadata = capture_run_entities(db)
        forward_metadata = migrate_run_entities(db, run_metadata, task_metadata=task_metadata, run_handoff=_run_handoff(db, run_metadata))
        db.commit()
        native_catalog = tuple(db.execute(
            "select type,name,tbl_name,sql from sqlite_schema where name not like 'sqlite_%' order by name"))
        native_sequences = tuple(db.execute(
            "select rowid,name,seq from sqlite_sequence order by rowid"))
        native_rows = {table: tuple(db.execute(f"select * from \"{table}\" order by 1"))
                       for table in _TARGET_TABLES if db.execute(
                           "select 1 from sqlite_schema where name=?", (table,)).fetchone()}
        db.execute("begin immediate")
        restore_run_entities(db, run_metadata, forward_metadata, task_metadata=task_metadata)
        db.rollback()
        assert tuple(db.execute(
            "select type,name,tbl_name,sql from sqlite_schema where name not like 'sqlite_%' order by name")) == native_catalog
        assert tuple(db.execute("select rowid,name,seq from sqlite_sequence order by rowid")) == native_sequences
        assert {table: tuple(db.execute(f"select * from \"{table}\" order by 1"))
                for table in native_rows} == native_rows
        assert db.execute("select value from unrelated_run_inverse").fetchone() == (b"\x00\xff",)
    finally:
        db.close()


def test_existing_identity_inverse_keeps_only_captured_missing_task_reference():
    db, task_metadata = _run_source(orphan_task_id=999)
    try:
        run_metadata = capture_run_entities(db)
        forward_metadata = migrate_run_entities(db, run_metadata, task_metadata=task_metadata, run_handoff=_run_handoff(db, run_metadata))
        assert ("runs", 1400, "tasks", 1) in run_metadata["run_fk_findings"]
        restore_run_entities(db, run_metadata, forward_metadata, task_metadata=task_metadata)
        assert db.execute("select task_id from runs where id=1400").fetchone() == (999,)
        assert db.execute("select 1 from tasks where id=999").fetchone() is None
        assert tuple(db.execute("pragma foreign_key_check(runs)")) == (("runs", 1400, "tasks", 1),)
    finally:
        db.close()


def test_run_then_task_inverse_allows_source_getter_and_writer_before_immediate_reforward():
    from app.task_entity_schema import capture_task_entities, migrate_task_entities, restore_task_entities
    from app.logical_run_storage import finish_logical_run, logical_run_projection

    db, task_forward, task_source_metadata = _run_source(include_task_source_metadata=True)
    try:
        db.execute("update runs set status='running' where id=1101")
        run_source_metadata = capture_run_entities(db)
        run_forward = migrate_run_entities(db, run_source_metadata, task_metadata=task_forward, run_handoff=_run_handoff(db, run_source_metadata))
        restore_run_entities(db, run_source_metadata, run_forward, task_metadata=task_forward)
        restore_task_entities(db, task_source_metadata, task_forward)

        db.row_factory = sqlite3.Row
        task_run_sql = logical_run_projection("task_agent_runs")
        before_cursor = db.execute(f"select * from ({task_run_sql}) where id=201")
        before_row = before_cursor.fetchone()
        before = dict(zip((column[0] for column in before_cursor.description), before_row, strict=True))
        assert before is not None
        assert before["status"] == "running"
        finish_logical_run(db, "task_agent_runs", 201, status="failed",
                           error="updated by predecessor Run writer")
        after_cursor = db.execute(f"select * from ({task_run_sql}) where id=201")
        after_row = after_cursor.fetchone()
        after = dict(zip((column[0] for column in after_cursor.description), after_row, strict=True))
        assert after["status"] == "failed"
        assert after["error"] == "updated by predecessor Run writer"

        new_task_capture = capture_task_entities(db)
        new_task_forward = migrate_task_entities(db, new_task_capture)
        migrate_run_entities(db, run_source_metadata, task_metadata=new_task_forward, run_handoff=_run_handoff(db, run_source_metadata))
        assert tuple(db.execute("select status,error from runs where id=1101").fetchone()) == (
            "failed", "updated by predecessor Run writer")
    finally:
        db.close()


def test_task_first_run_inverse_emits_json_handoff_from_restored_task_source():
    import json
    from app.task_entity_schema import restore_task_entities

    db, task_forward, task_source_metadata = _run_source(include_task_source_metadata=True)
    try:
        run_source_metadata = capture_run_entities(db)
        run_forward = migrate_run_entities(
            db, run_source_metadata, task_metadata=task_forward,
            run_handoff=_run_handoff(db, run_source_metadata))
        db.execute(
            "insert into setup_wizard_events(id,step_id,action_id,status,summary,evidence_json,"
            "stdout_excerpt,stderr_excerpt,created_at,finished_at) "
            "values(10999,'new-step','new-action','completed','new native fact','{}','','',"
            "'2026-10-22T00:00:00Z','2026-10-22T00:00:01Z')")
        task_inverse = restore_task_entities(db, task_source_metadata, task_forward)
        result = restore_run_entities(
            db, run_source_metadata, run_forward, task_metadata=task_forward,
            task_inverse_metadata=task_inverse)
        assert not any(row[1] == "runs:setup_wizard_events"
                       for row in run_source_metadata["sequence_rows"])
        created_sequence = next(row for row in result["sequence_rows"]
                                if row[1] == "runs:setup_wizard_events")
        assert created_sequence[0] is not None
        assert tuple(db.execute("select rowid,name,seq from sqlite_sequence "
                                "where name='runs:setup_wizard_events'").fetchone()) == created_sequence
        handoff = json.loads(json.dumps(result["run_handoff"]))
        assert handoff["version"] == 3
        assert handoff["runtime_attempt_parent_edges"] == []
        assert handoff["source_context"]
        assert set(handoff["owner_highwaters"]) == {
            "runs", "runtime_attempts", "run_failures", "schedule_occurrences",
            "workbench_events", "codex_session_search_index",
            "email_classifier_runtime_samples", "setup_wizard_events",
        }
        new_row = db.execute(
            "select id,legacy_key,step_id from runs where record_type='setup_wizard_events' "
            "and step_id='new-step'").fetchone()
        assert new_row is not None
        assert new_row[0] > 8000
        previous_setup_key = db.execute(
            "select max(cast(legacy_key as integer)) from runs "
            "where record_type='setup_wizard_events' and step_id!='new-step'").fetchone()[0]
        assert new_row[1] == str(previous_setup_key + 1)
        assert new_row[2] == "new-step"
        assert any(edge["source_kind"] == "setup_wizard_events"
                   and edge["canonical_id"] == 10999 for edge in handoff["identity_edges"])
        assert logical_run_current_schema.current_run11_schema_is_current(db)
    finally:
        db.close()


def test_run_inverse_rejects_unknown_current_task_identity_metadata_before_mutation():
    from app.task_entity_schema import restore_task_entities, _inverse_identity_digest

    db, task_forward, task_source_metadata = _run_source(include_task_source_metadata=True)
    try:
        run_source_metadata = capture_run_entities(db)
        run_forward = migrate_run_entities(
            db, run_source_metadata, task_metadata=task_forward,
            run_handoff=_run_handoff(db, run_source_metadata))
        task_inverse = restore_task_entities(db, task_source_metadata, task_forward)
        task_inverse["inverse_identity"]["unrecognized_owner_data"] = []
        task_inverse["source_context"]["handoff_sha256"] = _inverse_identity_digest(
            task_inverse["inverse_identity"])
        native_rows = {table: tuple(tuple(row) for row in db.execute(
            f'select * from "{table}" order by 1')) for table in _TARGET_TABLES}
        native_catalog = tuple(db.execute(
            "select type,name,tbl_name,sql from sqlite_schema where name not like 'sqlite_%' "
            "order by name"))
        with pytest.raises(ValueError, match="unsupported inverse Task identity handoff"):
            restore_run_entities(db, run_source_metadata, run_forward,
                                 task_metadata=task_forward,
                                 task_inverse_metadata=task_inverse)
        assert {table: tuple(tuple(row) for row in db.execute(
            f'select * from "{table}" order by 1')) for table in _TARGET_TABLES} == native_rows
        assert tuple(db.execute(
            "select type,name,tbl_name,sql from sqlite_schema where name not like 'sqlite_%' "
            "order by name")) == native_catalog
    finally:
        db.close()


def test_run_inverse_preserves_sparse_task_owner_identity_edges():
    from app.task_entity_schema import restore_task_entities, _inverse_identity_digest

    db, task_forward, task_source_metadata = _run_source(include_task_source_metadata=True)
    try:
        run_source_metadata = capture_run_entities(db)
        run_forward = migrate_run_entities(
            db, run_source_metadata, task_metadata=task_forward,
            run_handoff=_run_handoff(db, run_source_metadata))
        task_inverse = restore_task_entities(db, task_source_metadata, task_forward)
        task_inverse["inverse_identity"]["owners"].append({
            "source_kind": "reply_tasks", "source_physical_id": 987654,
            "canonical_id": 987654,
        })
        task_inverse["source_context"]["handoff_sha256"] = _inverse_identity_digest(
            task_inverse["inverse_identity"])
        with pytest.raises(ValueError, match="duplicate or redundant Task2B inverse identity edge"):
            restore_run_entities(db, run_source_metadata, run_forward,
                                 task_metadata=task_forward,
                                 task_inverse_metadata=task_inverse)
    finally:
        db.close()


def test_task_first_new_run_facts_survive_old_write_and_two_json_cycles():
    import hashlib
    import struct
    from app.task_entity_schema import capture_task_entities, migrate_task_entities, restore_task_entities
    from app.task20_definitions import task20_projection
    from app.task_entity_storage import create_task, append_task_input, bind_claimed_input, bind_current_input
    from app.run_entity_storage import create_run, set_run_provenance, set_run_projection
    from app.logical_run_storage import create_logical_run, logical_run_projection

    db, task_forward, task_source_metadata = _run_source(include_task_source_metadata=True)
    owners = ("runs", "runtime_attempts", "run_failures", "schedule_occurrences",
              "workbench_events", "codex_session_search_index",
              "email_classifier_runtime_samples", "setup_wizard_events")

    def clone(table, changes):
        row = dict(db.execute(f'select * from "{table}" limit 1').fetchone())
        row.update(changes)
        columns = tuple(row)
        db.execute(f'insert into "{table}" (' + ",".join(f'"{name}"' for name in columns)
                   + ") values(" + ",".join("?" for _ in columns) + ")",
                   tuple(row[name] for name in columns))

    def fingerprints(excluded_run=None):
        output = {}
        for table in (*owners, "run_provenance", "run_projections", "tasks", "task_inputs",
                      "task_business_keys", "task_results"):
            sql = f'select * from "{table}"'
            args = ()
            if excluded_run is not None and table in ("runs", "run_provenance", "run_projections"):
                sql += " where " + ("id" if table == "runs" else "run_id") + "<>?"
                args = (excluded_run,)
            rows = db.execute(sql, args).fetchall()
            digest_rows = []
            for row in rows:
                digest = hashlib.sha256()
                for value in row:
                    if value is None:
                        tag, raw = b"n", b""
                    elif isinstance(value, bytes):
                        tag, raw = b"b", value
                    elif isinstance(value, str):
                        tag, raw = b"t", value.encode()
                    elif isinstance(value, int):
                        tag, raw = b"i", str(value).encode()
                    elif isinstance(value, float):
                        tag, raw = b"r", struct.pack(">d", value)
                    else:
                        raise TypeError(type(value))
                    digest.update(tag + str(len(raw)).encode() + b":" + raw)
                digest_rows.append(digest.digest())
            output[table] = (len(rows), hashlib.sha256(b"".join(sorted(digest_rows))).hexdigest())
        return output

    try:
        run_source_metadata = capture_run_entities(db)
        run_forward = migrate_run_entities(
            db, run_source_metadata, task_metadata=task_forward,
            run_handoff=_run_handoff(db, run_source_metadata))
        db.row_factory = sqlite3.Row
        now = "2026-10-22T00:00:00Z"
        native_task = create_task(db, values={"kind": "reply", "status": "pending",
            "available_at": now, "attempts": 0, "error": "", "execution_generation": "initial",
            "claimed_input_version": 0, "created_at": now, "updated_at": now}, parameters={
                "force_new_decision": 0, "manual_rerun_attempt_id": 0,
                "manual_rerun_revision_key": "", "recovery_code": ""})
        task_inputs = []
        for index in (1, 2):
            task_input, created = append_task_input(db, task_id=native_task["id"], values={
                "channel": "dingtalk", "conversation_id": "synthetic-roundtrip",
                "conversation_title": "synthetic", "single_chat": 1,
                "trigger_message_id": f"synthetic-input-{index}", "trigger_create_time": now,
                "trigger_sender": "synthetic", "trigger_text": f"synthetic input {index}",
                "oa_url": "", "business_object_key": "synthetic-roundtrip",
                "input_revision_key": "same-revision", "created_at": now})
            assert created
            task_inputs.append(task_input)
        assert bind_claimed_input(db, task_id=native_task["id"], input_id=task_inputs[0]["id"],
                                  claimed_input_version=1)
        assert bind_current_input(db, task_id=native_task["id"], input_id=task_inputs[0]["id"],
                                  expected_input_version=None, input_version=1)
        assert bind_current_input(db, task_id=native_task["id"], input_id=task_inputs[1]["id"],
                                  expected_input_version=1, input_version=2)
        email_task = create_task(db, values={"kind": "email_classification", "status": "pending",
            "available_at": now, "attempts": 0, "error": "", "owner_pid": None, "generation": 0,
            "lease_owner": "", "lease_expires_at": "", "created_at": now, "updated_at": now})
        email_stable_identity = "synthetic:account:folder:uidvalidity:uid"
        db.execute("insert into email_classification_inputs(task_id,channel,stable_message_identity,input_json) "
                   "values(?,?,?,?)", (email_task["id"], "email", email_stable_identity, "{}"))
        db.execute("insert into task_results(task_id,result_kind,result_json) values(?,?,?)",
                   (email_task["id"], "email_classification", "{}"))
        db.execute("insert into workbench_tasks(title,runtime_kind,provider_session_ref) values(?,?,?)",
                   ("Synthetic Workbench", "codex", "opaque:synthetic-session"))
        workbench_session = db.execute("select id from workbench_tasks where title='Synthetic Workbench'").fetchone()[0]
        workbench_turn = create_task(db, values={"kind": "workbench_turn", "status": "queued",
            "created_at": now, "updated_at": now, "lease_owner": "", "lease_expires_at": ""})
        db.execute("insert into workbench_turns(task_id,workbench_task_id,client_request_id,task_sequence,"
            "user_text,stop_requested,error_code,error_detail,resume_context,execution_run_id,"
            "runtime_quiesced_run_id,started_at,completed_at) values(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (workbench_turn["id"], workbench_session, "synthetic-client-request", 1,
             "synthetic workbench input", 0, "", "", "", "", "", "", ""))
        consumer = create_run(db, values={"task_id": native_task["id"],
            "execution_generation": "initial", "role": "consumer", "proposal_revision": 1,
            "turn_attempt": 1, "operation_id": "synthetic-consumer-op", "status": "completed",
            "session_id": "synthetic-consumer", "transcript_start": 2, "transcript_end": 8,
            "structured_error_json": b"typed-error", "lease_owner": "synthetic",
            "lease_expires_at": now, "started_at": now, "completed_at": now,
            "created_at": now, "updated_at": now})
        audit = create_run(db, values={"task_id": native_task["id"], "parent_id": consumer["id"],
            "execution_generation": "initial", "role": "audit", "proposal_revision": 1,
            "turn_attempt": 1, "operation_id": "synthetic-audit-op", "status": "completed",
            "session_id": "synthetic-audit", "transcript_start": 9, "transcript_end": 12,
            "structured_error_json": "{}", "lease_owner": "synthetic", "lease_expires_at": now,
            "started_at": now, "completed_at": now, "created_at": now, "updated_at": now})
        assert audit["id"] != consumer["id"]
        task_agent_task = db.execute("select id from tasks where kind='work_summary' limit 1").fetchone()[0]
        professional = create_run(db, values={"task_id": task_agent_task, "status": "completed",
            "session_id": "synthetic-professional", "error": "", "completed_at": now,
            "updated_at": now, "created_at": now})
        set_run_provenance(db, run_id=professional["id"], source_type="task_agent_runs")
        set_run_projection(db, run_id=professional["id"], projection_json=b'{"synthetic":true}')
        common = consumer["id"]
        clone("runtime_attempts", {"id": common, "parent_id": common, "workload_kind": "agent_run",
            "workload_key": str(common), "attempt_number": 1, "route_name": "synthetic",
            "session_mode": "fresh", "source_session_id": "", "session_id": "synthetic-call",
            "status": "completed"})
        clone("runtime_attempts", {"id": common + 1, "parent_id": common, "workload_kind": "agent_run",
            "workload_key": str(common), "attempt_number": 2, "route_name": "synthetic",
            "session_mode": "resume", "source_session_id": "synthetic-call",
            "session_id": "synthetic-call-resume", "status": "completed"})
        clone("runtime_attempts", {"id": common + 2, "parent_id": None,
            "workload_kind": "email_classification", "workload_key": str(email_task["id"]),
            "attempt_number": 1, "route_name": "synthetic", "session_mode": "fresh",
            "source_session_id": "", "session_id": "synthetic-email-call", "status": "completed"})
        clone("runtime_attempts", {"id": common + 3, "parent_id": None,
            "workload_kind": "workbench", "workload_key": str(workbench_turn["id"]),
            "attempt_number": 1, "route_name": "synthetic", "session_mode": "fresh",
            "source_session_id": "", "session_id": "synthetic-workbench-call", "status": "completed"})
        clone("run_failures", {"id": common, "run_id": common, "phase": "synthetic-failure",
            "structured_error_json": '{"synthetic":true}'})
        clone("schedule_occurrences", {"id": common, "event_id": "synthetic-event",
            "trigger_kind": "manual", "dispatch_status": "dispatched", "execution_kind": "reply_task",
            "execution_id": str(native_task["id"])})
        clone("workbench_events", {"id": common, "task_id": workbench_turn["id"],
            "sequence": 1, "event_type": "synthetic", "payload_json": "{}"})
        meeting = db.execute("select run_id from run_provenance where source_type='meeting_alignment_runs' "
                             "and job_id is null limit 1").fetchone()[0]
        clone("codex_session_search_index", {"id": common, "session_id": "synthetic-search",
            "source_type": "meeting_alignment", "source_id": str(meeting), "title": "synthetic"})
        clone("email_classifier_runtime_samples", {"id": common, "model_id": "synthetic-no-provider-call"})
        clone("setup_wizard_events", {"id": common, "step_id": "synthetic", "action_id": "synthetic",
            "summary": "synthetic"})
        cursor = db.execute("insert into setup_wizard_events(step_id,action_id,status) "
                            "values('insert-delete','synthetic','completed')")
        deleted_setup_id = int(cursor.lastrowid)
        db.execute("delete from setup_wizard_events where id=?", (deleted_setup_id,))
        deleted_setup_water = db.execute("select seq from sqlite_sequence where name='setup_wizard_events'").fetchone()[0]
        assert deleted_setup_water >= deleted_setup_id
        for owner in owners:
            value = db.execute("select seq from sqlite_sequence where name=?", (owner,)).fetchone()[0]
            db.execute("update sqlite_sequence set seq=? where name=?", (value + 20, owner))
        expected = fingerprints()
        expected_water = dict(db.execute("select name,seq from sqlite_sequence where name in (" +
                                        ",".join("?" for _ in owners) + ")", owners))

        old_run = None
        for cycle in (1, 2):
            task_inverse = restore_task_entities(db, task_source_metadata, task_forward)
            run_inverse = restore_run_entities(db, run_source_metadata, run_forward,
                task_metadata=task_forward, task_inverse_metadata=task_inverse)
            serialized_run_handoff = json.loads(json.dumps(run_inverse["run_handoff"]))
            assert len(serialized_run_handoff["identity_edges"]) >= 8
            if cycle == 1:
                from hashlib import sha256
                from app.store import AutoReplyStore
                email_public = "email-classification:" + sha256(email_stable_identity.encode()).hexdigest()
                run_owner_ids = {(row["source_kind"], row["canonical_id"]): row["source_physical_id"]
                                 for row in serialized_run_handoff["identity_edges"]}
                email_attempt_id = run_owner_ids[("agent_runtime_attempts", common + 2)]
                assert db.execute("select workload_key from runs where record_type='agent_runtime_attempts' "
                                  "and id=?", (email_attempt_id,)).fetchone()[0] == email_public
                assert re.fullmatch(r"email-classification:[0-9a-f]{64}", email_public)
                turn_public = db.execute("select id from (" + task20_projection("workbench_turns")
                    + ") where client_request_id='synthetic-client-request'").fetchone()[0]
                from uuid import UUID
                UUID(turn_public)
                workbench_attempt_id = run_owner_ids[("agent_runtime_attempts", common + 3)]
                assert db.execute("select workload_key from runs where record_type='agent_runtime_attempts' "
                                  "and id=?", (workbench_attempt_id,)).fetchone()[0] == turn_public
                assert AutoReplyStore._validate_runtime_operation_workload(
                    "workbench", str(workbench_turn["id"])) == (
                        "workbench", str(workbench_turn["id"]))
            if cycle == 1:
                parent_public = db.execute("select cast(legacy_key as integer) from tasks "
                    "where record_type='work_summary_inputs' limit 1").fetchone()
                old_run = create_logical_run(db, "task_agent_runs", parent_public[0], status="completed",
                    codex_session_id="genuine-predecessor-write")
                assert old_run > max(deleted_setup_water, *expected_water.values())
                assert db.execute("select legacy_key from runs where id=?", (old_run,)).fetchone()[0] == str(old_run)
                assert db.execute("select id from (" + logical_run_projection("task_agent_runs") +
                    ") where common_run_id=?", (old_run,)).fetchone()[0] == old_run
            task_source_metadata = capture_task_entities(db)
            run_source_metadata = capture_run_entities(db)
            task_forward = migrate_task_entities(db, task_source_metadata,
                source_context=task_inverse["source_context"],
                inverse_identity=json.loads(json.dumps(task_inverse["inverse_identity"])))
            run_forward = migrate_run_entities(db, run_source_metadata, task_metadata=task_forward,
                run_handoff=serialized_run_handoff)
            assert fingerprints(old_run) == expected
            for owner, highwater in expected_water.items():
                actual = db.execute("select seq from sqlite_sequence where name=?", (owner,)).fetchone()[0]
                assert actual >= highwater
        assert not logical_run_current_schema.current_run11_schema_is_current(db)
        assert db.execute("select 1 from sqlite_schema where type='table' and name='runtime_attempts'").fetchone()
    finally:
        db.close()


def test_inverse_retires_deleted_mapped_occurrence_and_cache_without_resurrection():
    import json
    from app.task_entity_schema import capture_task_entities, migrate_task_entities, restore_task_entities

    db, task_forward, task_source_metadata = _run_source(
        orphan_task_id=987654, include_task_source_metadata=True)
    owners = ("runs", "runtime_attempts", "run_failures", "schedule_occurrences",
              "workbench_events", "codex_session_search_index",
              "email_classifier_runtime_samples", "setup_wizard_events")

    def owner_rows():
        return {table: tuple(tuple(row) for row in db.execute(f'select * from "{table}" order by 1'))
                for table in owners}

    def inverse_once(run_source_metadata, run_forward):
        task_inverse = restore_task_entities(db, task_source_metadata, task_forward)
        run_inverse = restore_run_entities(db, run_source_metadata, run_forward,
            task_metadata=task_forward, task_inverse_metadata=task_inverse)
        return task_inverse, run_inverse

    try:
        original_source_rows = {
            kind: tuple(db.execute("select * from runs where record_type=? order by id", (kind,)))
            for kind in run_entity_definitions.SOURCE_FIELDS
        }
        run_source_metadata = capture_run_entities(db)
        run_forward = migrate_run_entities(db, run_source_metadata, task_metadata=task_forward,
            run_handoff=_run_handoff(db, run_source_metadata))
        edge_by_source = {(row["source_kind"], row["physical_id"]): row["target_id"]
                          for row in run_forward["identity_edges"]}
        occurrence_canonical = edge_by_source[("scheduled_task_runs", 1303)]
        cache_canonical = edge_by_source[("codex_session_search_index", 1305)]
        native_state = owner_rows()
        native_catalog = tuple(tuple(row) for row in db.execute(
            "select type,name,sql from sqlite_schema where name in (" +
            ",".join("?" for _ in owners) + ") order by name", owners))
        owner_waters = {name: db.execute("select seq from sqlite_sequence where name=?", (name,)).fetchone()
                        for name in owners}
        db.execute("savepoint retired_inverse_rollback")
        db.execute("delete from schedule_occurrences where id=?", (occurrence_canonical,))
        db.execute("delete from codex_session_search_index where id=?", (cache_canonical,))

        task_inverse, run_inverse = inverse_once(run_source_metadata, run_forward)
        restored_rows = {kind: tuple(db.execute("select * from runs where record_type=? order by id", (kind,)))
                         for kind in run_entity_definitions.SOURCE_FIELDS}
        for kind, before_rows in original_source_rows.items():
            expected = tuple(row for row in before_rows if not (
                kind == "scheduled_task_runs" and row[0] == 1303
                or kind == "codex_session_search_index" and row[0] == 1305))
            assert restored_rows[kind] == expected
        assert not any(edge["source_kind"] == "scheduled_task_runs" and edge["source_physical_id"] == 1303
                       for edge in run_inverse["run_handoff"]["identity_edges"])
        assert not any(edge["source_kind"] == "codex_session_search_index" and edge["source_physical_id"] == 1305
                       for edge in run_inverse["run_handoff"]["identity_edges"])
        assert tuple(db.execute("pragma foreign_key_check")) == (("runs", 1400, "tasks", 1),)
        for owner, before_water in owner_waters.items():
            assert run_inverse["run_handoff"]["owner_highwaters"][owner] == (
                None if before_water is None else before_water[0])

        fresh_task_metadata = capture_task_entities(db)
        fresh_run_metadata = capture_run_entities(db)
        portable_run_handoff = json.loads(json.dumps(run_inverse["run_handoff"]))
        task_source_metadata = fresh_task_metadata
        task_forward = migrate_task_entities(db, fresh_task_metadata,
            source_context=task_inverse["source_context"],
            inverse_identity=json.loads(json.dumps(task_inverse["inverse_identity"])))
        run_forward = migrate_run_entities(db, fresh_run_metadata, task_metadata=task_forward,
            run_handoff=portable_run_handoff)
        assert db.execute("select 1 from schedule_occurrences where id=?", (occurrence_canonical,)).fetchone() is None
        assert db.execute("select 1 from codex_session_search_index where id=?", (cache_canonical,)).fetchone() is None
        assert tuple(db.execute("pragma foreign_key_check")) == (("runs", 1400, "tasks", 1),)

        task_inverse, run_inverse = inverse_once(fresh_run_metadata, run_forward)
        for kind, source_id in (("scheduled_task_runs", 1303), ("codex_session_search_index", 1305)):
            assert db.execute("select 1 from runs where record_type=? and id=?", (kind, source_id)).fetchone() is None
        assert run_inverse["run_handoff"]["owner_highwaters"]["schedule_occurrences"] >= (
            owner_waters["schedule_occurrences"][0] or 0)
        assert run_inverse["run_handoff"]["owner_highwaters"]["codex_session_search_index"] >= (
            owner_waters["codex_session_search_index"][0] or 0)
        repeated_task_metadata = capture_task_entities(db)
        repeated_run_metadata = capture_run_entities(db)
        task_forward = migrate_task_entities(db, repeated_task_metadata,
            source_context=task_inverse["source_context"],
            inverse_identity=json.loads(json.dumps(task_inverse["inverse_identity"])))
        run_forward = migrate_run_entities(db, repeated_run_metadata, task_metadata=task_forward,
            run_handoff=json.loads(json.dumps(run_inverse["run_handoff"])))
        assert db.execute("select 1 from schedule_occurrences where id=?", (occurrence_canonical,)).fetchone() is None
        assert db.execute("select 1 from codex_session_search_index where id=?", (cache_canonical,)).fetchone() is None
        assert tuple(db.execute("pragma foreign_key_check")) == (("runs", 1400, "tasks", 1),)
        db.execute("rollback to retired_inverse_rollback")
        db.execute("release retired_inverse_rollback")
        assert owner_rows() == native_state
        restored_catalog = tuple(tuple(row) for row in db.execute(
            "select type,name,sql from sqlite_schema where name in (" +
            ",".join("?" for _ in owners) + ") order by name", owners))
        assert restored_catalog == native_catalog
        for owner, before_water in owner_waters.items():
            after_water = db.execute("select seq from sqlite_sequence where name=?", (owner,)).fetchone()
            assert after_water == before_water
    finally:
        db.close()


@pytest.mark.parametrize("workload_key", [
    "201:decision_repair", "201:decision_repair.not-a-number", "201:decision_repair.0",
    "201:decision_repair.01", "201:decision_repair.1:extra",
])
def test_forward_rejects_malformed_decision_repair_task_keys_before_schema_mutation(workload_key):
    db, task_metadata = _run_source()
    try:
        source_metadata = capture_run_entities(db)
        db.execute("update runs set workload_key=? where record_type='agent_runtime_attempts' "
                   "and workload_kind='task' and workload_key='201:decision_repair.2'", (workload_key,))
        source_rows = tuple(db.execute("select id,workload_kind,workload_key from runs "
                                       "where record_type='agent_runtime_attempts' order by id"))
        with pytest.raises(ValueError, match="decision_repair"):
            migrate_run_entities(db, source_metadata, task_metadata=task_metadata,
                                 run_handoff=_run_handoff(db, source_metadata))
        assert tuple(db.execute("select id,workload_kind,workload_key from runs "
                                "where record_type='agent_runtime_attempts' order by id")) == source_rows
        assert db.execute("select 1 from sqlite_schema where type='table' and name='runs'").fetchone()
        assert not db.execute("select 1 from sqlite_schema where type='table' "
                              "and name='runtime_attempts'").fetchone()
        assert not db.execute("select 1 from temp.sqlite_schema where name='_run_entity_forward_rows'").fetchone()
    finally:
        db.close()


@pytest.mark.parametrize("workload_key", [
    "201:decision_repair", "201:decision_repair.not-a-number", "201:decision_repair.0",
    "201:decision_repair.01", "201:decision_repair.1:extra",
])
def test_inverse_rejects_malformed_decision_repair_task_keys_before_schema_mutation(workload_key):
    db, task_metadata = _run_source()
    try:
        source_metadata = capture_run_entities(db)
        forward = migrate_run_entities(db, source_metadata, task_metadata=task_metadata,
                                       run_handoff=_run_handoff(db, source_metadata))
        db.execute("update runtime_attempts set workload_key=? where workload_kind='task' "
                   "and workload_key like '%decision_repair.2'", (workload_key,))
        native_rows = tuple(db.execute("select id,workload_kind,workload_key,parent_id "
                                       "from runtime_attempts order by id"))
        with pytest.raises(ValueError, match="decision_repair"):
            restore_run_entities(db, source_metadata, forward, task_metadata=task_metadata)
        assert tuple(db.execute("select id,workload_kind,workload_key,parent_id "
                                "from runtime_attempts order by id")) == native_rows
        assert db.execute("select 1 from sqlite_schema where type='table' "
                          "and name='runtime_attempts'").fetchone()
    finally:
        db.close()


def test_forward_maps_canonical_first_decision_repair_round_to_task_run():
    db, task_metadata = _run_source()
    try:
        source_metadata = capture_run_entities(db)
        db.execute("update runs set workload_key='201:decision_repair.1' "
                   "where record_type='agent_runtime_attempts' and workload_kind='task' "
                   "and workload_key='201:decision_repair.2'")
        migrate_run_entities(db, source_metadata, task_metadata=task_metadata,
                             run_handoff=_run_handoff(db, source_metadata))
        assert db.execute("select parent_id from runtime_attempts "
                          "where workload_kind='task' and workload_key='1101:decision_repair.1'").fetchone() == (1101,)
        assert db.execute("select 1 from runtime_attempts where workload_kind='task' "
                          "and workload_key in ('301:memory_backfill','302:deadline_backfill.4')").fetchone()
    finally:
        db.close()


def _q(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def _sqlite_type(value):
    if value is None:
        return "null"
    if isinstance(value, bytes):
        return "blob"
    if isinstance(value, float):
        return "real"
    if isinstance(value, int):
        return "integer"
    return "text"
