"""Caller-owned current Task20 to native Task/domain forward migration."""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict

from app import task20_definitions, task_run_entity_definitions
from app.task_lifecycle_current_schema import task_lifecycle_objects
from app.task_lifecycle_definitions import (
    LIFECYCLE_FIELDS, SOURCE_FIELDS, scalar as lifecycle_scalar,
    task_lifecycle_physical_field_map, task_physical_identity_field_map,
)
from app.task_entity_migration import (
    _typed_key, owner_indexes, translate_feedback_workbench_refs,
)
from app.task8_storage import register_task8_functions

_STAGE_PREFIX = "_task2b_"
_DOMAIN_TARGETS = task_run_entity_definitions._DOMAIN_TARGETS
_TASK_TABLES = task_run_entity_definitions._TASK_TABLES


def _stored_public_key(value: object):
    """Keep BLOB identity bytes as BLOB in the TEXT-affinity map column."""
    kind, normalized = _typed_key(value)
    return normalized if kind == "blob" else None if kind == "null" else str(normalized)


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _objects(db: sqlite3.Connection, table: str) -> list[dict]:
    from app.run_storage import _schema_objects
    return _schema_objects(db, table)


def _sequence(db: sqlite3.Connection, name: str) -> tuple[int, int] | None:
    row = db.execute("select rowid,seq from main.sqlite_sequence where name=?", (name,)).fetchone()
    return None if row is None else (int(row[0]), int(row[1]))


def _transaction(db: sqlite3.Connection) -> None:
    if not db.in_transaction or db.execute("pragma foreign_keys").fetchone()[0]:
        raise ValueError("Task entity conversion requires caller-owned FK-off transaction")


def capture_task_entities(db: sqlite3.Connection) -> dict:
    """Capture declarations and allocator state; never capture source bodies."""
    _transaction(db)
    register_task8_functions(db)
    expected = task_lifecycle_objects()
    if _objects(db, "tasks") != expected:
        raise ValueError("unsupported current Task source declaration")
    external_fks = []
    table_names = [row[0] for row in db.execute(
        "select name from main.sqlite_schema where type='table' and name not like 'sqlite_%' and name<>'tasks'")]
    for table in table_names:
        for fk in db.execute(f"pragma main.foreign_key_list({_quote(table)})"):
            if fk[2] == "tasks":
                external_fks.append((table, *tuple(fk)))
    external_triggers = sorted(
        (row[0], row[1], row[2]) for row in db.execute(
            "select name,tbl_name,sql from main.sqlite_schema where type='trigger' "
            "and tbl_name<>'tasks' and lower(sql) like '%tasks%'"
        )
    )
    return {
        "source_tasks": expected,
        "task_sequence": _sequence(db, "tasks"),
        "external_fks": sorted(external_fks),
        "external_triggers": external_triggers,
        "source_objects": sorted(
            (row[0], row[1], row[2]) for row in db.execute(
                "select type,name,sql from main.sqlite_schema where tbl_name='tasks' "
                "and type in ('table','index','trigger') and name not like 'sqlite_%'"
            )
        ),
    }


def _validate_metadata(db: sqlite3.Connection, metadata: dict) -> None:
    if not isinstance(metadata, dict) or set(metadata) != {
        "source_tasks", "task_sequence", "source_objects", "external_fks", "external_triggers",
    }:
        raise ValueError("unsupported Task entity source metadata")
    _transaction(db)
    actual_objects = _objects(db, "tasks")
    actual_sequence = _sequence(db, "tasks")
    actual_source_objects = sorted(
        (row[0], row[1], row[2]) for row in db.execute(
            "select type,name,sql from main.sqlite_schema where tbl_name='tasks' "
            "and type in ('table','index','trigger') and name not like 'sqlite_%'"
        )
    )
    if actual_objects != metadata["source_tasks"]:
        raise ValueError("current Task source declaration or allocator changed")
    current_external = capture_task_entities(db)
    if (actual_objects != metadata["source_tasks"]
            or actual_sequence != metadata["task_sequence"]
            or actual_source_objects != metadata["source_objects"]
            or current_external["external_fks"] != metadata["external_fks"]
            or current_external["external_triggers"] != metadata["external_triggers"]):
        raise ValueError("current Task source declaration or allocator changed")
    task_run_entity_definitions.validate_task_run_field_map()
    lifecycle_rows = task_lifecycle_physical_field_map()
    if len(lifecycle_rows) != len(task20_definitions.TASK20_COLUMNS) * len(LIFECYCLE_FIELDS):
        raise ValueError("Task physical lifecycle field coverage is incomplete")
    if {(row["source_kind"], row["source_physical_field"])
            for row in lifecycle_rows} != {
            (kind, field) for kind in task20_definitions.TASK20_COLUMNS for field in LIFECYCLE_FIELDS}:
        raise ValueError("Task physical lifecycle field coverage changed")
    for row in lifecycle_rows:
        task_owned = row["source_kind"] in SOURCE_FIELDS
        if task_owned != (row["owner_scope"] == "native_task"):
            raise ValueError("Task physical lifecycle owner scope changed")
        if task_owned:
            if (row["target_table"], row["target_physical_field"]) != (
                    "tasks", row["source_physical_field"]):
                raise ValueError("Task physical lifecycle target changed")
        elif row["target_table"] is not None or row["retirement_reason"] is None:
            raise ValueError("auxiliary Task lifecycle scalar must remain unowned")
    identity_rows = task_physical_identity_field_map()
    if len(identity_rows) != len(task20_definitions.TASK20_COLUMNS) or {
            (row["source_kind"], row["source_physical_field"]) for row in identity_rows
    } != {(kind, "legacy_key") for kind in task20_definitions.TASK20_COLUMNS}:
        raise ValueError("Task physical identity field coverage is incomplete")
    for row in identity_rows:
        hidden_rowid = row["identity_semantics"] == "hidden_task12_rowid"
        if hidden_rowid != (row["logical_identity_field"] is None):
            raise ValueError("Task physical identity semantics changed")
        if (row["inverse_metadata"] == "raw_key_exception") != hidden_rowid:
            raise ValueError("Task physical identity metadata policy changed")


def _stage(db: sqlite3.Connection) -> dict[str, str]:
    staged = {}
    lifecycle_columns = ",".join(
        "(select " + lifecycle_scalar(field, "source") + " from main.tasks source "
        "where source.id=projected._physical_id) as " + _quote("_lifecycle_" + field)
        for field in LIFECYCLE_FIELDS
    )
    for kind in task20_definitions.TASK20_COLUMNS:
        name = _STAGE_PREFIX + kind
        if db.execute("select 1 from temp.sqlite_schema where name=?", (name,)).fetchone():
            raise ValueError("Task entity staging collision")
        projection = task20_definitions.task20_projection(kind)
        identity = ("tasks.id as _physical_id,tasks.legacy_key as _raw_key,"
                    "typeof(tasks.legacy_key) as _raw_key_type,")
        if projection.startswith("select * from(select "):
            projection = projection.replace("select * from(select ",
                                            "select * from(select " + identity, 1)
        else:
            projection = projection.replace("select ", "select " + identity, 1)
        db.execute("create temp table " + _quote(name) + " as select projected.*," + lifecycle_columns
                   + " from (" + projection + ") projected")
        staged[kind] = name
    return staged


def _source_rows(db: sqlite3.Connection, table: str, stage: str):
    cursor = db.execute("select * from temp." + _quote(stage))
    names = [item[0] for item in cursor.description]
    for row in cursor:
        yield dict(zip(names, row, strict=True))


def _task_kind(source_kind: str) -> str:
    return {"reply_tasks": "reply"}.get(
        source_kind, _TASK_TABLES.get(source_kind, "domain"),
    )


def _target_table(source_kind: str) -> str:
    if source_kind in _TASK_TABLES:
        return "tasks"
    return _DOMAIN_TARGETS[source_kind]


def _result_kind(source_kind: str) -> str:
    return {
        "meeting_alignment_jobs": "meeting_alignment",
        "email_agent_classification_tasks": "email_classification",
        "weekly_okr_analysis_jobs": "okr_analysis",
        "wechat_memory_import_jobs": "wechat_import",
    }[source_kind]


def _target_id(db: sqlite3.Connection, target_table: str, physical_id: int) -> int:
    columns = {row[1] for row in db.execute(f"pragma table_info({_quote(target_table)})")}
    if target_table == "tasks":
        return physical_id
    if "task_id" in columns and target_table in {
        "workbench_turns", "feedback_processing_items", "email_classification_inputs",
    }:
        return physical_id
    if "id" in columns:
        return physical_id
    return physical_id


def _add_identity(db, source_kind, row, target_id, identity_rows):
    public_field = "id" if "id" in row else (
        "task_id" if source_kind == "email_agent_classification_tasks" else None
    )
    if public_field is None:
        return
    public_key = row[public_field]
    if public_key == target_id and _typed_key(public_key)[0] == "integer":
        return
    identity_rows.append({
        "source_kind": source_kind, "physical_id": row["_physical_id"],
        "public_key": public_key, "target_id": target_id,
    })


def _insert(db, table: str, values: dict) -> None:
    if not values:
        raise ValueError(f"empty Task target row for {table}")
    fields = tuple(values)
    db.execute(
        f"insert into main.{_quote(table)} ({','.join(map(_quote, fields))}) "
        f"values ({','.join('?' for _ in fields)})", tuple(values[field] for field in fields),
    )


def resolve_source_public_owner(
    db: sqlite3.Connection, *, source_kind: str, public_key: object,
) -> int | None:
    """Resolve an old typed public owner key to its canonical target ID.

    Composed Run/Action and State migration uses this boundary rather than
    guessing that a numeric-looking source key is a native physical ID.
    """
    if source_kind not in task20_definitions.TASK20_COLUMNS:
        return None
    matches = db.execute(
        "select target_id from task_run_identity_map where source_kind=? "
        "and public_key=? and public_key_type=?",
        (source_kind, _stored_public_key(public_key), _typed_key(public_key)[0]),
    ).fetchall()
    if len(matches) > 1:
        raise ValueError("ambiguous typed Task source public identity")
    if matches:
        return int(matches[0][0])
    if type(public_key) is not int:
        return None
    table = _target_table(source_kind)
    columns = {row[1] for row in db.execute(f"pragma table_info({_quote(table)})")}
    key_column = "id" if "id" in columns else "task_id" if "task_id" in columns else None
    if key_column is None:
        return None
    row = db.execute(
        f"select { _quote(key_column) } from main.{_quote(table)} where { _quote(key_column) }=?",
        (public_key,),
    ).fetchone()
    return None if row is None else int(row[0])


def resolve_dispatcher_claim_source_id(
    db: sqlite3.Connection, *, adapter_name: str, source_id: str,
) -> int | None:
    """Resolve Task-backed State history to its canonical Task physical ID.

    The State row remains caller-owned. Unknown or stale history returns None
    so the composed cutover can retain it without manufacturing a Task.
    """
    source_kind = {
        "reply": "reply_tasks", "scheduled_execution": "reply_tasks",
        "meeting": "meeting_alignment_jobs", "work_summary": "work_summary_inputs",
        "okr_review": "okr_review_requests",
    }.get(adapter_name)
    if source_kind is None or not isinstance(source_id, str):
        return None
    try:
        public_id = int(source_id)
    except ValueError:
        return None
    if str(public_id) != source_id:
        return None
    mapped = db.execute(
        "select target_id from task_run_identity_map where source_kind=? "
        "and public_key=? and public_key_type='integer'",
        (source_kind, source_id),
    ).fetchall()
    if len(mapped) > 1:
        raise ValueError("ambiguous Task-backed State source identity")
    if mapped:
        return int(mapped[0][0])
    expected_kind = _task_kind(source_kind)
    row = db.execute("select id from tasks where id=? and kind=?", (public_id, expected_kind)).fetchone()
    return None if row is None else int(row[0])


def migrate_task_entities(db: sqlite3.Connection, metadata: dict) -> dict:
    """Replace current Task projections with native Task/domain facts.

    The caller retains transaction and foreign-key-check ownership because
    Runs/Actions and task-backed State are composed by the later cutover.
    """
    _transaction(db)
    _validate_metadata(db, metadata)
    staged = _stage(db)
    field_map = task_run_entity_definitions.task_run_field_map()
    logical = defaultdict(list)
    for item in field_map:
        if item["mapping_layer"] == "logical_projection":
            logical[item["source_kind"]].append(item)

    all_rows = {kind: list(_source_rows(db, kind, stage)) for kind, stage in staged.items()}
    input_fields = ("channel", "conversation_id", "conversation_title", "single_chat",
                    "trigger_message_id", "trigger_create_time", "trigger_sender",
                    "oa_url", "business_object_key")
    children_by_public = {
        _typed_key(row["id"]): row for row in all_rows["reply_task_inputs"]
    }
    for reply in all_rows["reply_tasks"]:
        current_physical = reply["current_input_id"]
        if current_physical is None:
            if any(reply[field] is not None for field in input_fields):
                raise ValueError("reply Task input metadata has no current native input owner")
            continue
        current = children_by_public.get(_typed_key(current_physical))
        if current is None:
            raise ValueError("reply Task current input has no source revision owner")
        if any(reply[field] != current[field] for field in input_fields):
            raise ValueError("reply Task metadata differs from its current input owner")
    public_rows = []
    target_ids = {}
    for kind, rows in all_rows.items():
        target = _target_table(kind)
        for row in rows:
            target_id = _target_id(db, target, int(row["_physical_id"]))
            target_ids[(kind, int(row["_physical_id"]))] = target_id
            id_field = "id" if "id" in row else "task_id" if kind == "email_agent_classification_tasks" else None
            if id_field:
                public_rows.append({"source_kind": kind, "physical_id": int(row["_physical_id"]),
                                    "public_key": row[id_field], "target_id": target_id})
    indexes = owner_indexes(public_rows)

    objects = task_run_entity_definitions.task_run_entity_objects()
    target_tables = {"tasks", "task_inputs", "task_business_keys", "workbench_tasks",
        "workbench_turns", "task_results", "email_classification_inputs", "work_summary_inputs",
        "feedback_processing_items", "feedback_processing_batches", "feedback_processing_rounds",
        "feedback_iteration_decisions", "oa_notification_events", "okr_review_items",
        "workbench_artifacts", "workbench_attachments", "email_historical_candidates",
        "task_run_identity_map"}
    existing = {row[0] for row in db.execute(
        "select name from main.sqlite_schema where type='table' and name not like 'sqlite_%'")}
    collisions = existing & (target_tables - {"tasks"})
    if collisions:
        raise ValueError("Task target owners already exist: " + ", ".join(sorted(collisions)))

    # Stage all source facts before dropping the old virtual-column container.
    source_objects = metadata["source_objects"]
    db.execute("drop table main.tasks")
    table_sql = {name: sql for name, sql in objects.items()
                 if name in target_tables and sql.lstrip().lower().startswith("create table")}
    for name, sql in table_sql.items():
        db.execute(sql)

    # Install the nine executable Task kinds first; result and detail owners
    # follow in a second pass so every target Task parent already exists.
    identity_records = []
    reference_exceptions = []
    raw_key_exceptions = []
    for kind, rows in all_rows.items():
        for row in rows:
            public_field = "id" if "id" in row else (
                "task_id" if kind == "email_agent_classification_tasks" else None
            )
            raw = row["_raw_key"]
            raw_type = row["_raw_key_type"]
            if public_field is None:
                # These Task12 families expose natural logical keys, while the
                # physical legacy_key retains their hidden source ROWID.
                # That identity cannot be reconstructed from a business key.
                raw_key_exceptions.append({"source_kind": kind,
                    "physical_id": row["_physical_id"], "value": raw, "type": raw_type})
                continue
            public = row[public_field]
            expected_raw = (str(public) if raw_type == "text" and public is not None
                            else public if raw_type in {"integer", "blob"} else None)
            if raw_type not in {"null", "text", "integer", "blob"} or raw != expected_raw:
                raw_key_exceptions.append({"source_kind": kind,
                    "physical_id": row["_physical_id"], "value": raw, "type": raw_type})
    for kind, rows in all_rows.items():
        if kind not in _TASK_TABLES:
            continue
        for row in rows:
            values = {"id": int(row["_physical_id"]), "kind": _task_kind(kind)}
            parameters = {}
            for item in logical[kind]:
                dest = item["target_field_or_expression"]
                target = item["target_table"]
                source_field = item["source_field"]
                lifecycle_field = SOURCE_FIELDS.get(kind, {}).get(source_field)
                value = (row["_lifecycle_" + lifecycle_field] if lifecycle_field is not None
                         else row[source_field])
                if target != "tasks" or source_field == "id":
                    continue
                if dest and dest.startswith("parameters_json.$."):
                    parameters[dest.partition("$.")[2]] = value
                elif dest and "." not in dest and item["retirement_reason"] is None:
                    values[dest] = value
            task_columns = {item[1] for item in db.execute("pragma table_info(tasks)")}
            for lifecycle_field in LIFECYCLE_FIELDS:
                if lifecycle_field in task_columns:
                    values[lifecycle_field] = row["_lifecycle_" + lifecycle_field]
            for source_field, lifecycle_field in SOURCE_FIELDS.get(kind, {}).items():
                target_field = {"owner": "lease_owner", "attempt_count": "attempts"}.get(
                    source_field, lifecycle_field,
                )
                if target_field in task_columns:
                    values[target_field] = row["_lifecycle_" + lifecycle_field]
            from app.task_entity_storage import _parameters_blob
            values["parameters_json"] = _parameters_blob(db, parameters)
            _insert(db, "tasks", values)

    # Insert remaining typed owners from the frozen field crosswalk.
    for kind, rows in all_rows.items():
        owner_targets = {item["target_table"] for item in logical[kind]
                         if item["target_table"] not in {None, "tasks", "task_run_identity_map"}
                         and item["retirement_reason"] is None}
        if kind == "reply_tasks":
            # Reply metadata is an input read projection. The writable native
            # TaskInput owner is the explicit revision row, which carries the
            # required trigger_text and full-key identity.
            owner_targets.discard("task_inputs")
        for target in sorted(owner_targets):
            for row in rows:
                values = {}
                columns = {item[1] for item in db.execute(f"pragma table_info({_quote(target)})")}
                if "id" in columns:
                    values["id"] = int(row["_physical_id"])
                for item in logical[kind]:
                    dest, owner = item["target_field_or_expression"], item["target_table"]
                    source_field = item["source_field"]
                    if owner != target or not dest:
                        continue
                    if dest.startswith("parameters_json.$.") or dest == "public_key" or dest not in columns:
                        continue
                    lifecycle_field = SOURCE_FIELDS.get(kind, {}).get(source_field)
                    value = (row["_lifecycle_" + lifecycle_field] if lifecycle_field is not None
                             else row[source_field])
                    if (kind, source_field) in {
                        ("reply_task_inputs", "reply_task_id"), ("business_object_tasks", "reply_task_id"),
                        ("okr_review_items", "request_id"), ("workbench_turns", "task_id"),
                        ("workbench_artifacts", "turn_id"), ("workbench_attachments", "task_id"),
                    }:
                        parent_kind = {
                            "reply_task_inputs": "reply_tasks", "business_object_tasks": "reply_tasks",
                            "okr_review_items": "okr_review_requests", "workbench_turns": "workbench_tasks",
                            "workbench_artifacts": "workbench_turns", "workbench_attachments": "workbench_tasks",
                        }[kind]
                        index_kind = "physical" if kind in {
                            "workbench_artifacts", "workbench_attachments",
                        } else "public"
                        identity = ((parent_kind, int(value)) if index_kind == "physical"
                                    else (parent_kind, _typed_key(value)))
                        value = indexes[index_kind].get(identity)
                        if value is None:
                            raise ValueError(f"unresolved {kind}.{source_field} owner")
                        dest = {"reply_task_id": "task_id", "request_id": "request_id",
                                "task_id": "workbench_task_id", "turn_id": "workbench_turn_id"}.get(source_field, dest)
                    if kind in {"feedback_processing_items", "feedback_processing_rounds"} and source_field in {
                        "workbench_task_id", "workbench_turn_id",
                    }:
                        ref_markers = []
                        task_ref, turn_ref = translate_feedback_workbench_refs(
                            task_ref=(row.get("workbench_task_id") or ""),
                            turn_ref=(row.get("workbench_turn_id") or ""),
                            task_indexes=indexes, turn_indexes=indexes,
                            unresolved=ref_markers,
                        )
                        value = task_ref if source_field == "workbench_task_id" else turn_ref
                        for marker in ref_markers:
                            if marker["field"] == source_field:
                                reference_exceptions.append({"source_kind": kind,
                                    "physical_id": row["_physical_id"], **marker})
                    values[dest] = value
                if target == "task_inputs":
                    values["id"] = int(row["_physical_id"])
                    values["task_id"] = int(row["_physical_id"]) if kind == "reply_tasks" else values.get("task_id")
                if target in {"workbench_turns", "feedback_processing_items", "email_classification_inputs"}:
                    values["task_id"] = int(row["_physical_id"])
                if target == "task_results":
                    values["task_id"] = int(row["_physical_id"])
                    values["result_kind"] = _result_kind(kind)
                if target == "work_summary_inputs":
                    values["task_id"] = int(row["_physical_id"])
                if target == "feedback_processing_items" and values.get("batch_id") is not None:
                    pass
                _insert(db, target, values)

    # Store only identity edges that cannot be reconstructed from native IDs.
    for identity in public_rows:
        if _typed_key(identity["public_key"]) == ("integer", identity["target_id"]):
            continue
        db.execute("insert into task_run_identity_map(source_kind,physical_id,public_key,public_key_type,target_id) values(?,?,?,?,?)",
            (identity["source_kind"], identity["physical_id"],
             _stored_public_key(identity["public_key"]),
             _typed_key(identity["public_key"])[0], identity["target_id"]))
        identity_records.append(identity)

    # Rebind Task current/claimed input public IDs after all input identities exist.
    for row in all_rows["reply_tasks"]:
        updates = {}
        for field in ("current_input_id", "claimed_input_id"):
            public_id = row[field]
            if public_id is not None:
                mapped = indexes["public"].get(("reply_task_inputs", _typed_key(public_id)))
                if mapped is None:
                    raise ValueError(f"unresolved reply_tasks.{field} input identity")
                updates[field] = mapped
        if updates:
            assignments = ",".join(_quote(key) + "=?" for key in updates)
            db.execute(f"update tasks set {assignments} where id=?", (*updates.values(), row["_physical_id"]))

    # Install exact target indexes and triggers only after bulk data insertion.
    for name, sql in objects.items():
        if name in target_tables or name.startswith(("idx_", "trg_")):
            if not sql.lstrip().lower().startswith("create table"):
                db.execute(sql)
    db.execute("delete from main.sqlite_sequence where name='tasks'")
    if metadata["task_sequence"] is not None:
        rowid, water = metadata["task_sequence"]
        db.execute("insert into main.sqlite_sequence(rowid,name,seq) values(?, 'tasks', ?)",
                   (rowid, water))
    for stage in staged.values():
        db.execute("drop table temp." + _quote(stage))
    return {
        "counts": {kind: len(rows) for kind, rows in all_rows.items()},
        "identity_rows": len(identity_records),
        "identity_edges": identity_records,
        "public_owner_rows": public_rows,
        "raw_key_exceptions": raw_key_exceptions,
        "reference_exceptions": reference_exceptions,
        "task_sequence": metadata["task_sequence"],
        "source_objects": source_objects,
    }


def _inverse_indexes(forward_metadata: dict) -> tuple[dict, dict, dict]:
    public_rows = forward_metadata.get("public_owner_rows")
    if not isinstance(public_rows, list):
        raise ValueError("Task inverse requires exact forward identity metadata")
    public = {}
    for row in public_rows:
        if not isinstance(row, dict) or set(row) != {"source_kind", "physical_id", "public_key", "target_id"}:
            raise ValueError("invalid Task public owner metadata")
        key = (row["source_kind"], _typed_key(row["public_key"]))
        if key in public:
            raise ValueError("ambiguous Task public owner metadata")
        public[key] = row
    public_identity = {(row["source_kind"], row["physical_id"]): row["public_key"] for row in public_rows}
    edges = {(row["source_kind"], row["physical_id"]): row["public_key"]
             for row in forward_metadata.get("identity_edges", [])}
    public_target = {(row["source_kind"], row["target_id"]): row["public_key"] for row in public_rows}
    public_index = {(row["source_kind"], _typed_key(row["public_key"])): row["target_id"]
                    for row in public_rows}
    return public, {}, {"public_by_source": public_identity, "public_target": public_target,
                              "public_index": public_index, "edges": edges}


def _native_value(db: sqlite3.Connection, item: dict, target_id: int):
    table = item["target_table"]
    field = item["target_field_or_expression"]
    if table == "task_run_identity_map" or table is None or not field:
        return None
    if table == "tasks":
        if field.startswith("parameters_json.$."):
            key = field.partition("$.")[2]
            row = db.execute("select json(parameters_json) from main.tasks where id=?",
                             (target_id,)).fetchone()
            if row is None or row[0] is None:
                return None
            encoded = json.loads(row[0])
            if not isinstance(encoded, dict) or key not in encoded:
                return None
            from app.task_entity_storage import _decode_scalar
            return _decode_scalar(encoded[key])
        row = db.execute("select " + _quote(field)
                         + " from main.tasks where id=?", (target_id,)).fetchone()
        return None if row is None else row[0]
    if table == "task_inputs" and item["source_kind"] == "reply_tasks":
        pointer = db.execute("select current_input_id from main.tasks where id=?", (target_id,)).fetchone()
        if pointer is None or pointer[0] is None:
            return None
        row = db.execute(f"select {_quote(field)} from main.task_inputs where id=?", (pointer[0],)).fetchone()
        return None if row is None else row[0]
    columns = {row[1] for row in db.execute(f"pragma main.table_info({_quote(table)})")}
    key_column = "id" if "id" in columns else "task_id" if "task_id" in columns else None
    if key_column is None:
        raise ValueError(f"native owner has no reconstructible identity: {table}")
    row = db.execute(f"select {_quote(field)} from main.{_quote(table)} where {_quote(key_column)}=?",
                     (target_id,)).fetchone()
    return None if row is None else row[0]


def restore_task_entities(db: sqlite3.Connection, source_metadata: dict, forward_metadata: dict) -> dict:
    """Rebuild the exact current lifecycle Tasks declaration from native facts.

    This routine is migration-only. The caller owns an FK-off transaction; it
    never commits, changes transaction mode, or persists historical row bodies.
    """
    _transaction(db)
    if (not isinstance(source_metadata, dict)
            or set(source_metadata) != {"source_tasks", "task_sequence", "source_objects",
                                        "external_fks", "external_triggers"}
            or source_metadata["source_tasks"] != task_lifecycle_objects()):
        raise ValueError("unsupported current Task source declaration metadata")
    if not isinstance(forward_metadata, dict):
        raise ValueError("invalid Task forward metadata")
    required = {"source_objects", "task_sequence", "identity_edges", "public_owner_rows",
                "raw_key_exceptions", "reference_exceptions"}
    if not required.issubset(forward_metadata):
        raise ValueError("incomplete Task inverse metadata")
    if source_metadata["source_objects"] != forward_metadata["source_objects"]:
        raise ValueError("Task source declaration metadata differs from forward capture")
    _, _, identities = _inverse_indexes(forward_metadata)
    from app import task20_definitions
    from app.task_run_entity_definitions import task_run_field_map, task_run_entity_objects
    from app.task_entity_migration import restore_feedback_workbench_ref
    from app.task_lifecycle_storage import source_insert
    from app.task_lifecycle_storage import encoded as encode_lifecycle_value
    from app.storage_payload import TASK_RECORD_TYPES

    expected_target = task_run_entity_objects()
    with sqlite3.connect(":memory:") as expected_db:
        table_sql = [(name, sql) for name, sql in expected_target.items()
                     if sql.lstrip().lower().startswith("create table")]
        other_sql = [(name, sql) for name, sql in expected_target.items()
                     if not sql.lstrip().lower().startswith("create table")]
        for _, sql in table_sql:
            expected_db.execute(sql)
        for _, sql in other_sql:
            expected_db.execute(sql)
        for name, _ in table_sql:
            if _objects(db, name) != _objects(expected_db, name):
                raise ValueError("Task native target declaration changed: " + name)

    maps = defaultdict(list)
    for item in task_run_field_map():
        if item["mapping_layer"] == "logical_projection":
            maps[item["source_kind"]].append(item)
    raw_exceptions = {(r["source_kind"], r["physical_id"]): (r["value"], r["type"])
                      for r in forward_metadata["raw_key_exceptions"]}
    reference_exceptions = {}
    for item in forward_metadata["reference_exceptions"]:
        reference_exceptions[(item["source_kind"], item["physical_id"], item["field"])] = item

    from app.task_run_entity_definitions import _DOMAIN_TARGETS, _TASK_TABLES
    physical_rows = []
    for kind in task20_definitions.TASK20_COLUMNS:
        table = _DOMAIN_TARGETS.get(kind, "tasks")
        if table == "tasks":
            selector = {"reply_tasks": "reply"}.get(kind, _TASK_TABLES.get(kind, "domain"))
            rows = db.execute("select id from main.tasks where kind=? order by id", (selector,)).fetchall()
        else:
            columns = {row[1] for row in db.execute(f"pragma main.table_info({_quote(table)})")}
            key_column = "id" if "id" in columns else "task_id" if "task_id" in columns else None
            if key_column is None:
                raise ValueError(f"native owner has no reconstructible identity: {kind}")
            rows = db.execute(f"select {_quote(key_column)} from main.{_quote(table)} order by {_quote(key_column)}").fetchall()
        for (target_id,) in rows:
            physical_rows.append({"source_kind": kind, "physical_id": int(target_id), "target_id": int(target_id)})
    source_rows = []
    for identity in physical_rows:
        kind, physical_id, target_id = identity["source_kind"], identity["physical_id"], identity["target_id"]
        public_key = identities["public_by_source"].get((kind, physical_id))
        if public_key is None:
            public_key = next((row["public_key"] for row in forward_metadata["public_owner_rows"]
                               if row["source_kind"] == kind and row["physical_id"] == physical_id), None)
        values = {}
        if public_key is not None:
            for identity_field in ("id", "task_id"):
                if identity_field in task20_definitions.TASK20_PAYLOAD_FIELDS[kind]:
                    values[identity_field] = public_key
        for item in maps[kind]:
            field = item["source_field"]
            target = item["target_table"]
            dest = item["target_field_or_expression"]
            if field == "id" or field == "task_id" and target == "task_run_identity_map":
                continue
            if target == "task_run_identity_map" or item["retirement_reason"] is not None:
                continue
            value = _native_value(db, item, target_id)
            if field in {"reply_task_id", "request_id", "task_id", "turn_id"}:
                parent_kind = {"reply_task_id": "reply_tasks", "request_id": "okr_review_requests",
                               "task_id": "workbench_tasks", "turn_id": "workbench_turns"}.get(field)
                if parent_kind and value is not None:
                    if type(value) is int:
                        value = identities["public_target"].get((parent_kind, value), value)
            if kind in {"feedback_processing_items", "feedback_processing_rounds"} and field in {
                    "workbench_task_id", "workbench_turn_id"}:
                raw_marker = reference_exceptions.get((kind, physical_id, field))
                marker = None if raw_marker is None else {
                    key: raw_marker[key] for key in ("field", "value", "type")
                }
                value = restore_feedback_workbench_ref(field=field, current_value=value,
                    indexes={"public": identities["public_index"]}, unresolved_marker=marker)
            if dest and dest.startswith("parameters_json.$."):
                values[field] = value
            elif field != "final_text":
                values[field] = value
        # Dispatch-only scalars are physical Task facts even though they are
        # intentionally outside the older logical field map.
        if kind in _TASK_TABLES:
            for field in LIFECYCLE_FIELDS:
                row = db.execute("select " + _quote(field)
                                 + " from main.tasks where id=?", (target_id,)).fetchone()
                values["__lifecycle_" + field] = None if row is None else row[0]
        if kind == "reply_tasks":
            for field in ("current_input_id", "claimed_input_id"):
                row = db.execute(f"select {_quote(field)} from main.tasks where id=?", (target_id,)).fetchone()
                values["__" + field] = None if row is None else row[0]
        if kind in task20_definitions.TASK20_COLUMNS:
            task_row = db.execute("select id from main.tasks where id=? and kind=?",
                                  (target_id, {"reply_tasks": "reply"}.get(kind, _TASK_TABLES.get(kind, kind)))).fetchone()
            if kind in {"reply_tasks", "work_summary_inputs", "meeting_alignment_jobs",
                        "okr_review_requests", "weekly_okr_analysis_jobs", "workbench_turns",
                        "email_agent_classification_tasks", "wechat_memory_import_jobs"} and task_row is None:
                raise ValueError(f"native Task owner is missing for {kind}:{target_id}")
        parent_id = None
        parent_field = {
            "reply_task_inputs": ("task_inputs", "task_id"),
            "business_object_tasks": ("task_business_keys", "task_id"),
            "workbench_turns": ("workbench_turns", "workbench_task_id"),
            "workbench_artifacts": ("workbench_artifacts", "workbench_turn_id"),
            "workbench_attachments": ("workbench_attachments", "workbench_task_id"),
        }.get(kind)
        if parent_field:
            parent_table, parent_column = parent_field
            parent_columns = {row[1] for row in db.execute(f"pragma main.table_info({_quote(parent_table)})")}
            parent_key = "id" if "id" in parent_columns else "task_id"
            row = db.execute(f"select {_quote(parent_column)} from main.{_quote(parent_table)} "
                             f"where {_quote(parent_key)}=?",
                             (target_id,)).fetchone()
            if row is not None and row[0] is not None:
                parent_id = int(row[0])
        elif kind == "feedback_iteration_decisions":
            row = db.execute(
                "select batch.id from main.feedback_iteration_decisions decision "
                "join main.feedback_processing_batches batch using(batch_id) where decision.id=?",
                (target_id,),
            ).fetchone()
            parent_id = None if row is None else int(row[0])
        source_rows.append((kind, physical_id, target_id, public_key, values, parent_id))

    # Retire exactly the native owner relations created by this converter.
    # Their attached indexes and triggers are removed by SQLite with each
    # owner table; unrelated catalog objects remain caller-owned.
    for name in reversed([name for name, _ in table_sql]):
        db.execute("drop table main." + _quote(name))

    # Restore the source table under its exact captured declaration.
    for typ, name, sql in forward_metadata["source_objects"]:
        if typ == "table":
            db.execute(sql)
    for kind, physical_id, target_id, public_key, values, parent_id in source_rows:
        raw, raw_type = raw_exceptions.get((kind, physical_id), (None, None))
        if (kind, physical_id) not in raw_exceptions:
            raw_value = physical_id if public_key is None else public_key
            raw_type = "text" if isinstance(raw_value, str) else "integer"
            raw = str(raw_value) if raw_type == "text" else raw_value
        source_values = {field: value for field, value in values.items()
                         if field in task20_definitions.TASK20_PAYLOAD_FIELDS[kind]}
        columns, expressions, parameters = source_insert(kind, source_values, {})
        for field in LIFECYCLE_FIELDS:
            if field not in columns:
                columns.append(field)
                value = values.get("__lifecycle_" + field)
                if value is None:
                    expressions.append("NULL")
                else:
                    expressions.append("jsonb(?)")
                    parameters += (encode_lifecycle_value(value),)
        base = ["_kind", "id", "legacy_key", "parent_id"]
        insert_values = ["?", "?", "?", "?", *expressions]
        bound = [TASK_RECORD_TYPES.code(kind), physical_id, raw, parent_id, *parameters]
        if kind == "reply_tasks":
            for field in ("current_input_id", "claimed_input_id"):
                base.append(field)
                insert_values.insert(len(base) - 1, "?")
                bound.insert(len(base) - 1, values["__" + field])
        insert_columns = [*base, *columns]
        db.execute("insert into main.tasks(" + ",".join(map(_quote, insert_columns)) + ") values("
                   + ",".join(insert_values) + ")", bound)
    for typ, name, sql in forward_metadata["source_objects"]:
        if typ != "table":
            db.execute(sql)
    db.execute("delete from main.sqlite_sequence where name='tasks'")
    sequence = source_metadata["task_sequence"]
    if sequence is not None:
        db.execute("insert into main.sqlite_sequence(rowid,name,seq) values(?, 'tasks', ?)", sequence)
    return {
        "rows": len(source_rows),
        "source_objects": forward_metadata["source_objects"],
        "sparse_identity_metadata": {
            "identity_edges": forward_metadata["identity_edges"],
            "raw_key_exceptions": forward_metadata["raw_key_exceptions"],
            "reference_exceptions": forward_metadata["reference_exceptions"],
        },
    }
