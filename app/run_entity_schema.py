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
                "source_id_ranges", "raw_key_exceptions", "reference_exceptions",
                "task_sequence", "source_objects"}
    if type(task_metadata) is not dict or set(task_metadata) != required:
        raise ValueError("Run conversion requires actual Task2B forward metadata")
    kinds = set(task20_definitions.TASK20_COLUMNS)
    if (type(task_metadata["identity_edges"]) is not list
            or type(task_metadata["owner_highwaters"]) is not dict
            or set(task_metadata["owner_highwaters"]) != kinds
            or type(task_metadata["source_id_ranges"]) is not dict
            or set(task_metadata["source_id_ranges"]) != kinds
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
    for kind, ranges in task_metadata["source_id_ranges"].items():
        if type(ranges) is not list:
            raise ValueError("invalid Task2B source identity ranges")
        previous = -1
        for pair in ranges:
            if (type(pair) is not list or len(pair) != 2
                    or any(type(value) is not int or value < 1 for value in pair)
                    or pair[0] > pair[1] or pair[0] <= previous + 1):
                raise ValueError(f"invalid Task2B source identity ranges: {kind}")
            previous = pair[1]
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


def _run_public_maps(db: sqlite3.Connection, rows):
    by_physical = {}
    by_namespace_public = {}
    public_order_by_physical = {}
    # Match logical_run_storage's actual source identity expression. In
    # particular, Python int() rejects values (such as '201suffix') that
    # SQLite CAST intentionally resolves to the integer public key 201.
    cast_rows = db.execute(
        "select id,record_type,coalesce(source_type,'agent_runs'),cast(legacy_key as integer) "
        f"from temp.{_quote(_STAGE)} where legacy_key is not null"
    )
    for physical, kind, namespace, cast_key in cast_rows:
        public_order_by_physical[physical] = cast_key
        if kind == "agent_runs":
            by_namespace_public[(namespace, "int", cast_key)] = physical
    for kind, row in rows:
        if kind != "agent_runs":
            continue
        physical = row["id"]
        by_physical[physical] = physical
    return by_physical, by_namespace_public, public_order_by_physical


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
    run_physical, run_public, run_public_order = _run_public_maps(db, rows)
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
        public_order = run_public_order.get(source["id"])
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
    identity_edges = [dict(zip(("source_kind", "physical_id", "public_key", "public_key_type",
                                "public_key_order", "target_id"), row, strict=True))
                      for row in db.execute(
                          "select source_kind,physical_id,public_key,public_key_type,public_key_order,target_id "
                          "from main.run_entity_identity_map order by source_kind,physical_id")]
    return {"source_objects": source_metadata["source_objects"],
            "sequence_rows": source_metadata["sequence_rows"],
            "run_fk_findings": source_metadata["run_fk_findings"],
            "identity_edges": identity_edges}


def _run_identity_rows(db: sqlite3.Connection) -> dict:
    rows = [tuple(row) for row in db.execute(
        "select source_kind,physical_id,public_key,public_key_type,public_key_order,target_id "
        "from main.run_entity_identity_map order by source_kind,physical_id")]
    expected_kinds = set(run_entity_definitions.SOURCE_FIELDS)
    if any(row[0] not in expected_kinds for row in rows):
        raise ValueError("unsupported new Run identity; B3 allocation/handoff is required")
    return {(row[0], int(row[1])): {
        "source_kind": row[0], "physical_id": int(row[1]), "public_key": row[2],
        "public_key_type": row[3], "public_key_order": row[4], "target_id": int(row[5]),
    } for row in rows}


def _inverse_task_ids(db: sqlite3.Connection, task_metadata: dict) -> tuple[dict, dict]:
    task_edges = _task_forward_edges(db, task_metadata)
    source_to_target = dict(task_edges)
    target_to_source = {(kind, target): physical
                        for (kind, physical), target in task_edges.items()}
    return source_to_target, target_to_source


def _task_source_physical(db: sqlite3.Connection, target_to_source: dict,
                          source_kind: str, target_id, *, allow_captured_orphan: bool = False):
    if target_id is None:
        return None
    physical = target_to_source.get((source_kind, int(target_id)), int(target_id))
    expected_kind = _task_kind(source_kind)
    row = db.execute("select kind from main.tasks where id=?", (target_id,)).fetchone()
    if row is None or row[0] != expected_kind:
        if allow_captured_orphan and row is None:
            return int(target_id)
        raise ValueError(f"native Task reference has wrong or missing owner: {source_kind}:{target_id}")
    return physical


def _task_public_from_target(db: sqlite3.Connection, target_to_source: dict,
                             source_kind: str, target_id):
    if target_id is None:
        return None
    _task_source_physical(db, target_to_source, source_kind, target_id)
    edge = db.execute(
        "select public_key,public_key_type from main.task_run_identity_map "
        "where source_kind=? and target_id=?", (source_kind, target_id)).fetchone()
    if edge is None:
        return str(target_id)
    public, typ = edge
    if typ == "integer":
        return str(target_id if public is None else public)
    if typ == "blob":
        return str(db.execute("select cast(? as integer)", (public,)).fetchone()[0])
    return str(public)


def _inverse_workload_key(db: sqlite3.Connection, value: str, task_targets: dict,
                          run_ids: dict, workload_kind: str) -> str:
    parts = value.split(":")
    kind = workload_kind
    if kind == "agent_run":
        return value
    if kind == "task":
        suffix = parts[1:]
        if suffix and not suffix[0].startswith("decision_repair."):
            return value
        run = run_ids.get(int(parts[0]))
        if run is None or run[0] != "task_agent_runs":
            raise ValueError(f"unresolved native Task Run workload identity: {value!r}")
        return str(run[1]) + ((":" + ":".join(suffix)) if suffix else "")
    if kind == "meeting":
        run = run_ids.get(int(value))
        if run is None or run[0] != "meeting_alignment_runs":
            raise ValueError(f"unresolved native Meeting Run workload identity: {value!r}")
        return str(run[1])
    if kind in {"structured", "email_classification", "workbench"}:
        source_kind = {"structured": "okr_review_requests",
                       "email_classification": "email_agent_classification_tasks",
                       "workbench": "workbench_turns"}[kind]
        return _task_public_from_target(db, task_targets, source_kind, value)
    if kind == "weekly_okr" and len(parts) == 5:
        parts[3] = _task_public_from_target(db, task_targets,
                                            "weekly_okr_analysis_jobs", parts[3])
        return ":".join(parts)
    if kind == "memory" and len(parts) > 1 and parts[0] == "wechat_memory_import_job":
        task = _task_public_from_target(db, task_targets, "wechat_memory_import_jobs", parts[-1])
        parts[-1] = task
        return ":".join(parts)
    return value


def restore_run_entities(db: sqlite3.Connection, source_metadata: dict,
                         forward_metadata: dict, *, task_metadata: dict) -> dict:
    """Restore the selected-current Run source from current native owner facts.

    Caller owns an FK-off transaction. Compose before ``restore_task_entities``:
    this inverse resolves current Run references while native Tasks still exist.
    Only identities recorded by this forward are supported; new native Run rows
    require the separate B3 allocator and portable-handoff contract.
    """
    _require_transaction(db)
    if not isinstance(source_metadata, dict) or set(source_metadata) != {
            "source_objects", "sequence_rows", "run_fk_findings"}:
        raise ValueError("unsupported Run inverse capture metadata")
    required = {"source_objects", "sequence_rows", "run_fk_findings"}
    if (not isinstance(forward_metadata, dict)
            or not required.union({"identity_edges"}).issubset(forward_metadata)):
        raise ValueError("incomplete Run forward metadata")
    if any(source_metadata[key] != forward_metadata[key] for key in required):
        raise ValueError("Run inverse metadata differs from forward capture")
    if source_metadata["source_objects"] != logical_run_current_schema.current_run11_objects():
        raise ValueError("unsupported selected-current Run source declaration")
    identities = _run_identity_rows(db)
    expected_identity_edges = {
        (row["source_kind"], int(row["physical_id"]), row["public_key"],
         row["public_key_type"], row["public_key_order"], int(row["target_id"]))
        for row in forward_metadata["identity_edges"]
    }
    actual_identity_edges = {
        (row["source_kind"], row["physical_id"], row["public_key"],
         row["public_key_type"], row["public_key_order"], row["target_id"])
        for row in identities.values()
    }
    if len(expected_identity_edges) != len(forward_metadata["identity_edges"]) \
            or actual_identity_edges != expected_identity_edges:
        raise ValueError("Run inverse identities differ from the reviewed forward identity map")
    _, task_target_to_source = _inverse_task_ids(db, task_metadata)
    source_kinds = set(run_entity_definitions.SOURCE_FIELDS)
    by_kind = {kind: [] for kind in source_kinds}
    for (kind, physical), identity in identities.items():
        by_kind[kind].append(identity)
    expected_target = {(kind, row["target_id"]) for kind, rows in by_kind.items() for row in rows}
    field_map = {(row["source_kind"], row["source_field"]): row
                 for row in run_entity_definitions.run_entity_field_map()}
    from app.task_run_entity_definitions import task_run_entity_objects
    target_declarations = {**task_run_entity_objects(), **_TARGETS}
    from app.configs_storage import create_configs_schema
    from app.schedules_storage import create_schedules_schema
    with sqlite3.connect(":memory:") as expected_db:
        declarations = list(target_declarations.values())
        for sql in declarations:
            if sql.lstrip().lower().startswith("create table "):
                expected_db.execute(sql)
        create_schedules_schema(expected_db)
        create_configs_schema(expected_db)
        for sql in declarations:
            if not sql.lstrip().lower().startswith("create table "):
                expected_db.execute(sql)
        for table in _OWNED_TABLES:
            if _objects(db, table) != _objects(expected_db, table):
                raise ValueError(f"Run native target declaration changed: {table}")
    captured_orphan_ids = {
        int(rowid) for table, rowid, parent, _ in source_metadata["run_fk_findings"]
        if table == "runs" and parent == "tasks"
    }
    for table in _OWNED_TABLES:
        if not db.execute("select 1 from main.sqlite_schema where name=?", (table,)).fetchone():
            raise ValueError(f"missing current native Run owner: {table}")
    target_ids = {}
    for table in _OWNED_TABLES:
        if table == "run_entity_identity_map":
            continue
        columns = {info[1] for info in db.execute(f"pragma main.table_info({_quote(table)})")}
        key_column = "id" if "id" in columns else "run_id" if "run_id" in columns else None
        if key_column is None:
            raise ValueError(f"native Run owner has no identity column: {table}")
        target_ids[table] = {int(row[0]) for row in db.execute(
            f"select {_quote(key_column)} from main.{_quote(table)}")}
    actual_ids = set()
    for kind, rows in by_kind.items():
        owner = _SOURCE_TO_OWNER[kind]
        for identity in rows:
            target = identity["target_id"]
            if target not in target_ids[owner]:
                raise ValueError(f"missing native Run identity: {kind}:{target}")
            actual_ids.add((kind, target))
    for kind, owner in _SOURCE_TO_OWNER.items():
        actual = target_ids[owner]
        expected = {target for source, target in expected_target if source == kind}
        if actual != expected:
            raise ValueError(f"unsupported new or missing native Run identities in {owner}")

    staged = []
    run_identity_by_target = {row["target_id"]: row for row in identities.values()
                              if row["source_kind"] == "agent_runs"}
    run_source_type = {}
    for target, provenance in db.execute(
            "select run_id,source_type from main.run_provenance"):
        run_source_type[int(target)] = provenance
    run_public_by_physical = {
        row["physical_id"]: (run_source_type.get(row["target_id"]), row["public_key_order"])
        for row in run_identity_by_target.values()
    }
    run_source_public_by_target = {
        row["target_id"]: (run_source_type.get(row["target_id"]), row["public_key_order"])
        for row in run_identity_by_target.values()
    }
    native_rows = {}
    for table in _OWNED_TABLES:
        if table == "run_entity_identity_map":
            continue
        columns = [info[1] for info in db.execute(f"pragma main.table_info({_quote(table)})")]
        key_column = "id" if "id" in columns else "run_id"
        key_index = columns.index(key_column)
        native_rows[table] = {
            int(values[key_index]): dict(zip(columns, values, strict=True))
            for values in db.execute(f"select * from main.{_quote(table)}")
        }
    for kind, rows in by_kind.items():
        owner = _SOURCE_TO_OWNER[kind]
        for identity in rows:
            target = identity["target_id"]
            source = {row["source_field"]: None for row in field_map.values()
                      if row["source_kind"] == kind}
            source["id"] = identity["physical_id"]
            source["record_type"] = kind
            typ, public = identity["public_key_type"], identity["public_key"]
            source["legacy_key"] = (None if typ == "null" else bytes(public) if typ == "blob"
                                     else int(public) if typ == "integer" else public)
            for (source_kind, source_field), mapping in field_map.items():
                if source_kind != kind or mapping["target_table"] is None:
                    continue
                table, target_field = mapping["target_table"], mapping["target_field"]
                if table == "run_entity_identity_map" or target_field in {"id", "source_kind"}:
                    continue
                key = target
                native = native_rows[table].get(key)
                if native is not None:
                    source[source_field] = native.get(target_field)
            if kind == "agent_runs":
                provenance = db.execute("select source_type,job_id from main.run_provenance where run_id=?",
                                        (target,)).fetchone()
                if provenance:
                    source["source_type"], source["job_id"] = provenance
                projection = db.execute("select projection_json from main.run_projections where run_id=?",
                                        (target,)).fetchone()
                if projection:
                    source["projection_json"] = projection[0]
                if source["task_id"] is not None:
                    task_kind = _PROFESSIONAL_TASK_KINDS.get(source["source_type"], "reply_tasks")
                    source["task_id"] = _task_source_physical(db, task_target_to_source,
                        task_kind, source["task_id"], allow_captured_orphan=(
                            source["source_type"] == "meeting_alignment_runs"
                            and identity["physical_id"] in captured_orphan_ids))
            elif kind == "agent_runtime_attempts":
                source["workload_key"] = _inverse_workload_key(
                    db, source["workload_key"], task_target_to_source,
                    run_source_public_by_target, source["workload_kind"])
            elif kind == "workbench_events" and source["task_id"] is not None:
                source["task_id"] = _task_source_physical(
                    db, task_target_to_source, "workbench_turns", source["task_id"])
            elif kind == "scheduled_task_runs" and source["execution_kind"] == "reply_task":
                source["execution_id"] = _task_public_from_target(
                    db, task_target_to_source, "reply_tasks", source["execution_id"])
            elif kind == "codex_session_search_index" and source["source_type"] == "meeting_alignment":
                search_identity = run_public_by_physical.get(int(source["source_id"]))
                if search_identity is None or search_identity[0] != "meeting_alignment_runs":
                    raise ValueError("unresolved Meeting Run search identity")
                source["source_id"] = search_identity[1]
            staged.append((kind, identity["physical_id"], source))

    # Every source slot is explicit: structural NULLs are restored from the
    # selected source CHECK contract, while every mapped field came from the
    # current native row above.
    for kind, _, source in staged:
        for field, value in source.items():
            mapping = field_map[(kind, field)]
            if mapping["target_table"] is None and value is not None:
                raise ValueError(f"native facts cannot populate structural Run slot {kind}.{field}")
    for name, sql in _TARGETS.items():
        if sql.lstrip().lower().startswith(("create index", "create unique index", "create trigger")):
            db.execute(f"drop {('trigger' if 'trigger' in sql.lower() else 'index')} "
                       f"if exists main.{_quote(_declared_name(sql))}")
    for table in reversed(_OWNED_TABLES):
        db.execute(f"drop table main.{_quote(table)}")
    for name, sql in source_metadata["source_objects"].items():
        if name == "runs":
            db.execute(sql)
    source_columns = tuple(row[1] for row in db.execute("pragma main.table_info(runs)"))
    for kind, _, source in staged:
        fields = {name: source[name] for name in source_columns}
        columns = ",".join(_quote(name) for name in fields)
        values = ",".join("?" for _ in fields)
        try:
            db.execute(f"insert into main.runs ({columns}) values ({values})", tuple(fields.values()))
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"restored source row rejected for {kind}:{source['id']}: {exc}") from exc
    for name, sql in source_metadata["source_objects"].items():
        if name != "runs":
            db.execute(sql)
    for name in _SOURCE_SEQUENCE_NAMES:
        db.execute("delete from main.sqlite_sequence where name=?", (name,))
    for row in source_metadata["sequence_rows"]:
        db.execute("insert into main.sqlite_sequence(rowid,name,seq) values(?,?,?)", row)
    if not logical_run_current_schema.current_run11_schema_is_current(db):
        raise ValueError("restored Run declaration differs from selected-current source")
    return {"rows": len(staged), "source_objects": source_metadata["source_objects"],
            "sequence_rows": source_metadata["sequence_rows"],
            "run_fk_findings": source_metadata["run_fk_findings"]}
