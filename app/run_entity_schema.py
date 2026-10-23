"""Task3B migration-only forward transfer from the selected current Run schema."""
from __future__ import annotations

import sqlite3

from app import logical_run_current_schema, run_entity_definitions, task20_definitions
from app.runs11_definitions import RUN11_NEW_KINDS

_STAGE = "_run_entity_forward_rows"
_SOURCE_SEQUENCE_NAMES = (
    "runs", "runs:published", *("runs:" + kind for kind in RUN11_NEW_KINDS),
)
_PROFESSIONAL_TASK_KINDS = {
    "task_agent_runs": "work_summary_inputs",
    "meeting_alignment_runs": "meeting_alignment_jobs",
    "okr_review_runs": "okr_review_requests",
}
_PROFESSIONAL = frozenset(_PROFESSIONAL_TASK_KINDS)
_TARGETS = run_entity_definitions.run_entity_objects()
_OWNED_TABLES = tuple(name for name, sql in _TARGETS.items()
                      if sql.lstrip().lower().startswith("create table "))
_SOURCE_TO_OWNER = {
    "agent_runs": "runs", "agent_runtime_attempts": "runtime_attempts",
    "agent_run_state_events": "run_failures", "scheduled_task_runs": "schedule_occurrences",
    "workbench_events": "workbench_events", "codex_session_search_index": "codex_session_search_index",
    "email_classifier_runtime_samples": "email_classifier_runtime_samples",
    "setup_wizard_events": "setup_wizard_events",
}


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _objects(db: sqlite3.Connection, table: str) -> list[dict]:
    from app.run_storage import _schema_objects
    return _schema_objects(db, table)


def _require_transaction(db: sqlite3.Connection) -> None:
    if not db.in_transaction or db.execute("pragma foreign_keys").fetchone()[0]:
        raise ValueError("Run entity conversion requires caller-owned FK-off transaction")


def _sequence_rows(db: sqlite3.Connection) -> list[tuple]:
    placeholders = ",".join("?" for _ in _SOURCE_SEQUENCE_NAMES)
    return [tuple(row) for row in db.execute(
        f"select rowid,name,seq from main.sqlite_sequence where name in ({placeholders}) order by rowid",
        _SOURCE_SEQUENCE_NAMES)]


def _run_fk_findings(db: sqlite3.Connection) -> tuple[tuple, ...]:
    # Only records the actual pre-existing Run edges. It is content-free and
    # lets a composed migration preserve an orphan without inventing its Task.
    findings = []
    for row in db.execute("pragma main.foreign_key_check(runs)"):
        findings.append(tuple(row))
    return tuple(findings)


def capture_run_entities(db: sqlite3.Connection) -> dict:
    """Capture exact current declarations, allocator rows and Run FK findings."""
    _require_transaction(db)
    expected = logical_run_current_schema.current_run11_objects()
    if not logical_run_current_schema.current_run11_schema_is_current(db):
        raise ValueError("unsupported selected-current Run source declaration")
    return {"source_objects": expected, "sequence_rows": _sequence_rows(db),
            "run_fk_findings": _run_fk_findings(db)}


def _validate_source(db: sqlite3.Connection, metadata: dict) -> None:
    _require_transaction(db)
    if not isinstance(metadata, dict) or set(metadata) != {
            "source_objects", "sequence_rows", "run_fk_findings"}:
        raise ValueError("unsupported Run entity capture metadata")
    expected = logical_run_current_schema.current_run11_objects()
    if metadata["source_objects"] != expected:
        raise ValueError("Run source metadata does not match selected-current oracle")
    if not logical_run_current_schema.current_run11_schema_is_current(db):
        raise ValueError("Run source is not the exact selected-current catalog")
    if metadata["sequence_rows"] != _sequence_rows(db):
        raise ValueError("Run source allocators changed after capture")
    if metadata["run_fk_findings"] != _run_fk_findings(db):
        raise ValueError("Run source FK findings changed after capture")
    run_entity_definitions.validate_run_entity_contract()


def _source_rows(db: sqlite3.Connection):
    columns = tuple(row[1] for row in db.execute("pragma main.table_info(runs)"))
    for values in db.execute("select * from temp." + _quote(_STAGE) + " order by id"):
        row = dict(zip(columns, values, strict=True))
        source_kind = row["record_type"]
        if source_kind not in run_entity_definitions.SOURCE_FIELDS:
            raise ValueError(f"unknown current Run source kind: {source_kind!r}")
        yield source_kind, row


def _insert(db: sqlite3.Connection, table: str, row: dict) -> None:
    if not row:
        raise ValueError(f"empty Run owner row for {table}")
    fields = tuple(row)
    db.execute(f"insert into main.{_quote(table)} ({','.join(map(_quote, fields))}) "
               f"values ({','.join('?' for _ in fields)})", tuple(row[field] for field in fields))


def _task_kind(source_kind: str) -> str:
    from app.task_run_entity_definitions import _TASK_TABLES
    return _TASK_TABLES.get(source_kind)


def _task_forward_edges(db: sqlite3.Connection, task_metadata: dict) -> dict:
    required = {"counts", "identity_rows", "identity_edges", "owner_highwaters",
                "raw_key_exceptions", "reference_exceptions", "task_sequence", "source_objects"}
    if type(task_metadata) is not dict or set(task_metadata) != required:
        raise ValueError("Run conversion requires actual Task2B forward metadata")
    kinds = set(task20_definitions.TASK20_COLUMNS)
    if (type(task_metadata["identity_edges"]) is not list
            or type(task_metadata["owner_highwaters"]) is not dict
            or set(task_metadata["owner_highwaters"]) != kinds
            or type(task_metadata["counts"]) is not dict
            or set(task_metadata["counts"]) != kinds):
        raise ValueError("invalid Task2B sparse identity metadata")
    if (type(task_metadata["identity_rows"]) is not int
            or task_metadata["identity_rows"] != len(task_metadata["identity_edges"])
            or any(type(value) is not int or value < 0
                   for value in (*task_metadata["counts"].values(),
                                 *task_metadata["owner_highwaters"].values()))
            or type(task_metadata["raw_key_exceptions"]) is not list
            or type(task_metadata["reference_exceptions"]) is not list
            or type(task_metadata["source_objects"]) is not list):
        raise ValueError("invalid Task2B forward metadata values")
    actual = {
        (row[0], int(row[1]), row[2], row[3], int(row[4]))
        for row in db.execute(
            "select source_kind,physical_id,public_key,public_key_type,target_id "
            "from main.task_run_identity_map")
    }
    expected = set()
    for edge in task_metadata["identity_edges"]:
        if type(edge) is not dict or set(edge) != {
                "source_kind", "physical_id", "public_key", "target_id"}:
            raise ValueError("invalid Task2B identity edge")
        kind, physical_id, public_key, target_id = (
            edge["source_kind"], edge["physical_id"], edge["public_key"], edge["target_id"])
        if (kind not in kinds or type(physical_id) is not int or type(target_id) is not int):
            raise ValueError("invalid Task2B identity edge")
        from app.task_entity_schema import _stored_public_key
        from app.task_entity_migration import _typed_key
        expected.add((kind, physical_id, _stored_public_key(public_key),
                      _typed_key(public_key)[0], target_id))
    if len(expected) != len(task_metadata["identity_edges"]) or expected != actual:
        raise ValueError("Task2B sparse identity edges do not match native owners")
    return {(kind, physical_id): target_id for kind, physical_id, _, _, target_id in expected}


def _task_physical_owner(db: sqlite3.Connection, task_edges: dict,
                         source_kind: str, physical_id: int) -> int:
    expected_kind = _task_kind(source_kind)
    if expected_kind is None:
        raise ValueError(f"unsupported physical Task source kind: {source_kind!r}")
    target_id = task_edges.get((source_kind, physical_id), physical_id)
    row = db.execute("select kind from main.tasks where id=?", (target_id,)).fetchone()
    if row is None or row[0] != expected_kind:
        raise ValueError(f"physical Task owner kind mismatch for {source_kind}:{physical_id}")
    return target_id


def _task_public_owner(db: sqlite3.Connection, source_kind: str, public_key):
    expected_kind = _task_kind(source_kind)
    if expected_kind is None:
        raise ValueError(f"unsupported public Task source kind: {source_kind!r}")
    from app.task_entity_schema import resolve_source_public_owner
    target_id = resolve_source_public_owner(db, source_kind=source_kind, public_key=public_key)
    if target_id is None:
        raise ValueError(f"unresolved typed {source_kind} identity: {public_key!r}")
    row = db.execute("select kind from main.tasks where id=?", (target_id,)).fetchone()
    if row is None or row[0] != expected_kind:
        raise ValueError(f"typed Task owner kind mismatch for {source_kind}:{public_key!r}")
    return target_id


def _run_public_maps(rows):
    by_physical = {}
    by_namespace_public = {}
    for kind, row in rows:
        if kind != "agent_runs":
            continue
        physical = row["id"]
        by_physical[physical] = physical
        namespace = row.get("source_type") or "agent_runs"
        public = row["legacy_key"]
        # Source legacy keys use SQLite CAST(public AS INTEGER) lookup
        # semantics, but the map namespace is still source_type-qualified.
        if public is not None:
            try:
                cast_key = int(public)
            except (TypeError, ValueError):
                continue
            by_namespace_public[(namespace, "int", cast_key)] = physical
    return by_physical, by_namespace_public


def _workload_key(db, row, task_edges, run_physical, run_public):
    kind, key = row["workload_kind"], row["workload_key"]
    parts = key.split(":")
    if kind == "agent_run":
        return str(run_physical[int(key)])
    if kind == "task":
        suffix = parts[1:]
        if not suffix or suffix[0].startswith("decision_repair."):
            physical = run_public[("task_agent_runs", "int", int(parts[0]))]
            return str(physical) + ((":" + ":".join(suffix)) if suffix else "")
        # memory_backfill Project IDs and deadline_backfill Todo IDs remain in
        # their business identity domain.
        return key
    if kind == "meeting":
        return str(run_public[("meeting_alignment_runs", "int", int(key))])
    if kind == "structured":
        return str(_task_public_owner(db, "okr_review_requests", int(key)))
    if kind == "weekly_okr" and len(parts) == 5:
        parts[3] = str(_task_public_owner(db, "weekly_okr_analysis_jobs", int(parts[3])))
        return ":".join(parts)
    if kind == "email_classification":
        return str(_task_public_owner(db, "email_agent_classification_tasks", key))
    if kind == "workbench":
        return str(_task_public_owner(db, "workbench_turns", key))
    if kind == "memory" and key.startswith("wechat_memory_import_job:"):
        source_key = int(key.rsplit(":", 1)[1])
        return "wechat_memory_import_job:" + str(
            _task_public_owner(db, "wechat_memory_import_jobs", source_key))
    return key


def _source_reference(db, kind: str, source: dict, field: str, task_edges: dict,
                      run_physical: dict, run_public: dict):
    value = source[field]
    if value is None:
        return None
    if kind == "codex_session_search_index" and field == "source_id" \
            and source["source_type"] == "meeting_alignment":
        return str(run_public[("meeting_alignment_runs", "int", int(value))])
    if kind == "scheduled_task_runs" and field == "execution_id" \
            and source["execution_kind"] == "reply_task":
        return str(_task_public_owner(db, "reply_tasks", int(value)))
    if kind == "agent_runtime_attempts" and field == "workload_key":
        return _workload_key(db, source, task_edges, run_physical, run_public)
    return value


def _captured_task_orphans(db: sqlite3.Connection, rows, findings) -> set[tuple]:
    task_fk_ids = {int(row[0]) for row in db.execute(
        "pragma main.foreign_key_list(runs)") if row[2] == "tasks" and row[3] == "task_id"}
    edges = {(table, rowid, parent, fk_id) for table, rowid, parent, fk_id in findings}
    missing = set()
    for _, row in rows:
        value = row["task_id"]
        if value is None:
            continue
        for fk_id in task_fk_ids:
            if ("runs", row["id"], "tasks", fk_id) in edges:
                missing.add((row["id"], "task_id", value))
    return missing


def _declared_name(sql: str) -> str:
    tokens = sql.strip().split()
    prefix = [token.upper() for token in tokens]
    if prefix[:2] in (["CREATE", "TABLE"], ["CREATE", "TRIGGER"]):
        return tokens[2].strip('"`[]')
    if prefix[:3] == ["CREATE", "UNIQUE", "INDEX"]:
        return tokens[3].strip('"`[]')
    if prefix[:2] == ["CREATE", "INDEX"]:
        return tokens[2].strip('"`[]')
    raise ValueError("unsupported Run target declaration")


def migrate_run_entities(db: sqlite3.Connection, source_metadata: dict, *,
                         task_metadata: dict) -> dict:
    """Forward all eight source families into their declared native owners.

    ``task_metadata`` is the result of Task2B's forward migration in this same
    database/transaction. Sparse public/physical identity edges are verified
    against the native identity map; identity fallback is checked against the
    exact native Task kind before a reference is accepted.
    """
    _validate_source(db, source_metadata)
    task_edges = _task_forward_edges(db, task_metadata)
    if db.execute("select 1 from temp.sqlite_schema where name=?", (_STAGE,)).fetchone():
        raise ValueError("Run migration staging table already exists")
    # runs is the sole same-name source/target table; all other owner names are
    # required to be unoccupied before any source object is changed.
    for name in _OWNED_TABLES:
        if name != "runs" and db.execute(
                "select 1 from main.sqlite_schema where name=? collate nocase", (name,)).fetchone():
            raise ValueError(f"Run entity target name already occupied: {name}")
    source_names = set(source_metadata["source_objects"])
    for sql in _TARGETS.values():
        name = _declared_name(sql)
        found = db.execute("select tbl_name from main.sqlite_schema where name=? collate nocase",
                           (name,)).fetchone()
        if found and name not in source_names:
            raise ValueError(f"Run entity target object name already occupied: {name}")
    db.execute(f"create temp table {_quote(_STAGE)} as select * from main.runs")
    rows = list(_source_rows(db))
    run_physical, run_public = _run_public_maps(rows)
    kinds_by_id = {row["id"]: kind for kind, row in rows}
    orphan_refs = _captured_task_orphans(db, rows, source_metadata["run_fk_findings"])
    # Resolve all references before writing target rows. A missing old Task FK
    # is retained only when it was already a captured source finding.
    for kind, row in rows:
        if kind in {"agent_runs", "workbench_events"} and row["task_id"] is not None:
            source_kind = ("workbench_turns" if kind == "workbench_events" else
                           _PROFESSIONAL_TASK_KINDS.get(row.get("source_type"), "reply_tasks"))
            if (row["id"], "task_id", row["task_id"]) not in orphan_refs:
                _task_physical_owner(db, task_edges, source_kind, row["task_id"])
        if kind == "agent_runtime_attempts":
            parent = row["parent_id"]
            if parent is not None and parent not in kinds_by_id:
                raise ValueError(f"unresolved physical Run parent: {parent!r}")
            _workload_key(db, row, task_edges, run_physical, run_public)
        for field in ("source_id", "execution_id"):
            if field in row and row[field] is not None:
                _source_reference(db, kind, row, field, task_edges, run_physical, run_public)
    field_map = {(entry["source_kind"], entry["source_field"]): entry
                 for entry in run_entity_definitions.run_entity_field_map()}
    db.execute("drop table main.runs")
    for name, sql in _TARGETS.items():
        if sql.lstrip().lower().startswith("create table "):
            db.execute(sql)
    target_columns = {name: {r[1] for r in db.execute(f"pragma main.table_info({_quote(name)})")}
                      for name in _OWNED_TABLES}
    for kind, source in rows:
        owner = _SOURCE_TO_OWNER[kind]
        fields = {}
        for source_field, value in source.items():
            mapping = field_map[(kind, source_field)]
            destination, target_field = mapping["target_table"], mapping["target_field"]
            if destination is None:
                if value is not None:
                    raise ValueError(f"non-NULL structurally retired Run field: {kind}.{source_field}")
                continue
            if destination == owner and target_field in target_columns.get(owner, set()):
                fields[target_field] = value
        if kind == "agent_runs":
            fields["id"] = source["id"]
            fields.pop("source_type", None)
            fields.pop("job_id", None)
            fields.pop("projection_json", None)
            if source["task_id"] is not None:
                task_kind = (_PROFESSIONAL_TASK_KINDS.get(source.get("source_type"), "reply_tasks"))
                fields["task_id"] = (source["task_id"] if
                                     (source["id"], "task_id", source["task_id"]) in orphan_refs else
                                     _task_physical_owner(db, task_edges, task_kind, source["task_id"]))
            _insert(db, "runs", fields)
            source_type = source["source_type"]
            if source_type in _PROFESSIONAL:
                _insert(db, "run_provenance", {"run_id": source["id"], "source_type": source_type,
                                                "job_id": source["job_id"]})
                if source["projection_json"] is not None:
                    _insert(db, "run_projections", {"run_id": source["id"],
                                                     "projection_json": source["projection_json"]})
        else:
            for name in ("source_id", "execution_id", "workload_key"):
                if name in fields:
                    fields[name] = _source_reference(
                        db, kind, source, name, task_edges, run_physical, run_public)
            fields["id"] = source["id"]
            if kind == "agent_runtime_attempts":
                fields["parent_id"] = source["parent_id"]
            elif kind == "agent_run_state_events":
                fields["run_id"] = source["parent_id"]
            elif kind == "workbench_events":
                if source["task_id"] is not None:
                    fields["task_id"] = _task_physical_owner(
                        db, task_edges, "workbench_turns", source["task_id"])
            _insert(db, owner, fields)
        public = source["legacy_key"]
        public_type = "null" if public is None else {
            int: "integer", str: "text", bytes: "blob", float: "text",
        }.get(type(public), "text")
        public_key = public if isinstance(public, bytes) else None if public is None else str(public)
        try:
            public_order = int(public) if public is not None else None
        except (TypeError, ValueError):
            public_order = None
        _insert(db, "run_entity_identity_map", {
            "source_kind": kind, "physical_id": source["id"], "public_key": public_key,
            "public_key_type": public_type, "public_key_order": public_order,
            "target_id": source["id"], "allocator_highwater": None,
        })
    for name, sql in _TARGETS.items():
        if not sql.lstrip().lower().startswith("create table "):
            db.execute(sql)
    # Preserve every source-owned allocator fact (including names for counters
    # whose retained sample rows no longer reach the old highwater). Source
    # names are restored after target inserts so those inserts cannot lower
    # or otherwise rewrite the captured rowid/name/seq facts.
    for source_sequence in source_metadata["sequence_rows"]:
        db.execute("delete from main.sqlite_sequence where name=?", (source_sequence[1],))
        db.execute("insert into main.sqlite_sequence(rowid,name,seq) values(?,?,?)",
                   source_sequence)
    expected_orphans = {(physical, value) for physical, field, value in orphan_refs
                        if field == "task_id"}
    actual_orphans = {(run_id, task_id) for run_id, task_id in db.execute(
        "select r.id,r.task_id from main.runs r where r.task_id is not null "
        "and not exists(select 1 from main.tasks t where t.id=r.task_id)")}
    if actual_orphans != expected_orphans:
        raise ValueError("Run forward changed captured missing physical Task edges")
    db.execute(f"drop table temp.{_quote(_STAGE)}")
    return {"source_objects": source_metadata["source_objects"],
            "sequence_rows": source_metadata["sequence_rows"],
            "run_fk_findings": source_metadata["run_fk_findings"]}
