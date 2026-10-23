import sqlite3

import pytest

from app import logical_run_current_schema, run_entity_definitions, runs11_current_schema
from app.run_entity_schema import capture_run_entities, migrate_run_entities

_TARGET_NAMES = set(run_entity_definitions.run_entity_objects())


def _create(objects, db):
    for sql in objects.values():
        db.execute(sql)


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
            migrate_run_entities(db, metadata, task_metadata=task_metadata)
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


def _run_source(*, orphan_task_id=None):
    from app.task_entity_schema import capture_task_entities, migrate_task_entities
    from tests.test_task_entity_schema import _source as task_source

    db = task_source()
    db.execute("pragma foreign_keys=off")
    db.execute("begin immediate")
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
        ("task", "201:decision_repair.2"), ("agent_run", "1001"), ("meeting", "201"),
        ("structured", "222"), ("weekly_okr", "week:manager:digest"),
        ("weekly_okr", "week:manager:digest:223:owner"),
        ("email_classification", "classification-225"),
        ("workbench", "wb-turn-a"), ("memory", "wechat_memory_import_job:232"),
        ("memory", "meeting_memory_write_event:opaque:3"),
        ("task", "301:memory_backfill"), ("task", "302:deadline_backfill.4"),
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
            "id": 1400, "legacy_key": "1400", "task_id": orphan_task_id,
            "execution_generation": "consumer-anomaly", "role": "consumer",
            "proposal_revision": 0, "turn_attempt": 0, "operation_id": "anomaly-op",
            "status": "completed",
            "session_id": "anomaly-session", "transcript_start": 0, "transcript_end": 0,
            "structured_error_json": "{}", "lease_owner": "", "lease_expires_at": "",
            "started_at": "start", "completed_at": "done", "created_at": "created",
            "updated_at": "updated",
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
    return db, task_forward


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
        result = migrate_run_entities(db, metadata, task_metadata=task_metadata)
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
        assert runtime_keys["agent_run:1202"] == "1001"
        assert runtime_keys["meeting:1203"] == "1102"
        assert runtime_keys["structured:1204"] == "222"
        assert runtime_keys["weekly_okr:1206"] == "week:manager:digest:223:owner"
        assert runtime_keys["email_classification:1207"] == "225"
        assert runtime_keys["workbench:1208"] == "203"
        assert runtime_keys["memory:1209"] == "wechat_memory_import_job:232"
        assert runtime_keys["memory:1210"] == "meeting_memory_write_event:opaque:3"
        assert runtime_keys["task:1211"] == "301:memory_backfill"
        assert runtime_keys["task:1212"] == "302:deadline_backfill.4"
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
                        "select source_kind,public_key,target_id from run_entity_identity_map "
                        "where source_kind=? and physical_id=?", (kind, physical_id),
                    ).fetchone()
                    expected = {"source_kind": kind, "public_key":
                                (None if value is None else str(value)), "target_id": physical_id}
                    assert identity is not None
                    assert identity[("source_kind", "public_key", "target_id").index(target_field)] == expected[target_field]
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
                    assert (stored[1] if stored else None) == value, (kind, field, value, stored)
                    assert (stored[0] if stored else "null") == _sqlite_type(value), (
                        kind, field, _sqlite_type(value), stored)
    finally:
        db.close()


def test_forward_is_rollbackable_by_caller_and_preserves_all_source_allocators():
    db, task_metadata = _run_source()
    try:
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
        migrate_run_entities(db, captured, task_metadata=task_metadata)
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
            migrate_run_entities(db, run_metadata, task_metadata=task_metadata)
        assert tuple(db.execute("select id,record_type,task_id from runs order by id")) == before
        assert db.execute("select 1 from sqlite_schema where name='run_provenance'").fetchone() is None
    finally:
        db.close()


def test_forward_preserves_a_captured_missing_physical_task_edge_without_fabrication():
    db, task_metadata = _run_source(orphan_task_id=999)
    try:
        metadata = capture_run_entities(db)
        assert ("runs", 1400, "tasks", 1) in metadata["run_fk_findings"]
        migrate_run_entities(db, metadata, task_metadata=task_metadata)
        assert db.execute("select task_id from runs where id=1400").fetchone()[0] == 999
        assert not db.execute("select 1 from tasks where id=999").fetchone()
        assert tuple(db.execute("pragma foreign_key_check(runs)")) == (("runs", 1400, "tasks", 1),)
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
