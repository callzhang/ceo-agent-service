import sqlite3

import pytest

from app import logical_run_current_schema, run_entity_definitions, runs11_current_schema
from app.run_entity_schema import capture_run_entities, migrate_run_entities, restore_run_entities

_TARGET_NAMES = set(run_entity_definitions.run_entity_objects())
_TARGET_TABLES = {name for name, sql in run_entity_definitions.run_entity_objects().items()
                  if sql.lstrip().lower().startswith("create table ")}


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


def _run_source(*, orphan_task_id=None, include_task_source_metadata=False):
    from app.task_entity_schema import capture_task_entities, migrate_task_entities
    from tests.test_task_entity_schema import _source as task_source

    db = task_source()
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
        migrate_run_entities(db, run_metadata, task_metadata=task_metadata)

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


def test_existing_identity_inverse_restores_all_eight_current_source_kinds():
    db, task_metadata = _run_source()
    try:
        source_objects = logical_run_current_schema.current_run11_objects()
        source_rows = tuple(db.execute("select * from runs order by id"))
        run_metadata = capture_run_entities(db)
        forward_metadata = migrate_run_entities(db, run_metadata, task_metadata=task_metadata)
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
        refoward = migrate_run_entities(db, run_metadata, task_metadata=task_metadata)
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
        forward_metadata = migrate_run_entities(db, run_metadata, task_metadata=task_metadata)
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
        forward_metadata = migrate_run_entities(db, run_metadata, task_metadata=task_metadata)
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
        forward_metadata = migrate_run_entities(db, run_metadata, task_metadata=task_metadata)
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
        forward_metadata = migrate_run_entities(db, run_metadata, task_metadata=task_metadata)
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
        run_forward = migrate_run_entities(db, run_source_metadata, task_metadata=task_forward)
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
        migrate_run_entities(db, run_source_metadata, task_metadata=new_task_forward)
        assert tuple(db.execute("select status,error from runs where id=1101").fetchone()) == (
            "failed", "updated by predecessor Run writer")
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
