"""Caller-owned current Task20 to native Task/domain forward migration."""
from __future__ import annotations

import json
import hashlib
import sqlite3
from pathlib import Path
from collections import defaultdict
from uuid import UUID, uuid4

from app import task20_definitions, task_run_entity_definitions
from app.action_storage import _encoded_native_sqlite_value
from app.task_entity_storage import _decode_scalar
from app.task_lifecycle_current_schema import task_lifecycle_objects
from app.task_lifecycle_definitions import (
    LIFECYCLE_FIELDS, SOURCE_FIELDS, scalar as lifecycle_scalar,
    task_lifecycle_physical_field_map, task_physical_identity_field_map,
)
from app.task_entity_migration import (
    _typed_key, owner_indexes, restore_feedback_round_pointer,
    restore_feedback_scope_receipt, translate_feedback_round_pointer,
    translate_feedback_scope_receipt, translate_feedback_workbench_refs,
)
from app.task8_storage import register_task8_functions

_STAGE_PREFIX = "_task2b_"
_DOMAIN_TARGETS = task_run_entity_definitions._DOMAIN_TARGETS
_TASK_TABLES = task_run_entity_definitions._TASK_TABLES


def _stored_public_key(value: object):
    """Keep BLOB identity bytes as BLOB in the TEXT-affinity map column."""
    kind, normalized = _typed_key(value)
    return normalized if kind == "blob" else None if kind == "null" else str(normalized)


def _identity_ranges(rows: dict[str, list[dict]]) -> dict[str, list[list[int]]]:
    """Compress exact source physical membership without retaining Task facts."""
    result = {}
    for kind, items in rows.items():
        ordered = sorted({int(row["_physical_id"]) for row in items})
        ranges = []
        for value in ordered:
            if ranges and value == ranges[-1][1] + 1:
                ranges[-1][1] = value
            else:
                ranges.append([value, value])
        result[kind] = ranges
    return result


def _identity_member(ranges: dict[str, list[list[int]]], kind: str, value: int) -> bool:
    return any(start <= value <= end for start, end in ranges.get(kind, ()))


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
    remapped_native = db.execute(
        "select 1 from task_run_identity_map where source_kind=? and target_id=? limit 1",
        (source_kind, public_id),
    ).fetchone()
    if remapped_native is not None:
        # The numeric value is a canonical ID owned by another public key;
        # it cannot also be inferred as the missing source public identity.
        return None
    row = db.execute("select id from tasks where id=? and kind=?", (public_id, expected_kind)).fetchone()
    return None if row is None else int(row[0])


_DISPATCHER_TASK_KINDS = {
    "reply": "reply_tasks", "scheduled_execution": "reply_tasks",
    "meeting": "meeting_alignment_jobs", "work_summary": "work_summary_inputs",
    "okr_review": "okr_review_requests",
}


def _dispatcher_claim_state_plan(db, translate, native_owner_exists):
    """Validate passive Task references and return planned reference rewrites."""
    if db.execute("select 1 from main.sqlite_schema where type='table' and name='state'").fetchone() is None:
        return []
    rows = [tuple(row) for row in db.execute(
        "select cast(legacy_key as integer),adapter_name,source_id from main.state "
        "where record_type='dispatcher_claim_leases' order by cast(legacy_key as integer)")]
    planned = []
    for rowid, adapter, source_id in rows:
        if adapter not in _DISPATCHER_TASK_KINDS:
            continue
        target = translate(adapter, source_id)
        if target is None:
            try:
                numeric_source = int(source_id)
            except (TypeError, ValueError):
                continue
            if str(numeric_source) != str(source_id):
                continue
            if native_owner_exists(adapter, numeric_source):
                raise ValueError("unresolved Task-backed State reference collides with a native owner")
            continue
        if type(target) is not int or target <= 0:
            raise ValueError("Task-backed dispatcher State reference has no canonical owner")
        planned.append((int(rowid), adapter, str(source_id), str(target)))
    proposed = {int(rowid): str(source_id) for rowid, _adapter, source_id in rows}
    row_adapters = {int(rowid): adapter for rowid, adapter, _source_id in rows}
    for rowid, adapter, old, new in planned:
        proposed[rowid] = new
    final = {}
    for rowid, source_id in proposed.items():
        adapter = row_adapters[rowid]
        key = (adapter, source_id)
        previous = final.setdefault(key, rowid)
        if previous != rowid:
            raise ValueError("Task-backed dispatcher State references collide after translation")
    return [(rowid, adapter, old, new) for rowid, adapter, old, new in planned if old != new]


def _translate_dispatcher_claim_state(db, translate):
    """Rekey only Task-backed passive State references, retaining State row IDs."""
    def native_owner_exists(adapter, task_id):
        expected_kind = _task_kind(_DISPATCHER_TASK_KINDS[adapter])
        return db.execute("select 1 from main.tasks where id=? and kind=?",
                          (task_id, expected_kind)).fetchone() is not None

    changed = _dispatcher_claim_state_plan(db, translate, native_owner_exists)
    from app.state_storage import update_state
    # The State primary key is (adapter_name,source_id); stage permutations so
    # valid swaps do not depend on row order. These sentinels exist only inside
    # the caller's migration transaction and are never a persisted alias.
    staged = []
    for rowid, adapter, old, new in changed:
        sentinel = "task-ref-stage:" + uuid4().hex
        if update_state(db, "dispatcher_claim_leases", rowid,
                        {"source_id": sentinel}, where_sql="adapter_name=? and source_id=?",
                        parameters=(adapter, old)) != 1:
            raise ValueError("Task-backed dispatcher State changed during translation")
        staged.append((rowid, adapter, sentinel, new))
    for rowid, adapter, sentinel, new in staged:
        if update_state(db, "dispatcher_claim_leases", rowid,
                        {"source_id": new}, where_sql="adapter_name=? and source_id=?",
                        parameters=(adapter, sentinel)) != 1:
            raise ValueError("Task-backed dispatcher State staging row disappeared")
    return len(changed)


def migrate_task_entities(
    db: sqlite3.Connection, metadata: dict, *, source_context: dict | None = None,
    inverse_identity: dict | None = None,
) -> dict:
    """Replace current Task projections with native Task/domain facts.

    The caller retains transaction and foreign-key-check ownership because
    Runs/Actions and task-backed State are composed by the later cutover.
    """
    _transaction(db)
    _validate_metadata(db, metadata)
    handoff = _validate_inverse_identity(inverse_identity)
    if source_context is not None:
        if (not isinstance(source_context, dict)
                or set(source_context) != {"version", "handoff_required", "handoff_sha256"}
                or source_context["version"] != 1
                or type(source_context["handoff_required"]) is not bool
                or not isinstance(source_context["handoff_sha256"], str)):
            raise ValueError("unsupported restored-source Task context")
        if source_context["handoff_required"] and inverse_identity is None:
            raise ValueError("restored-source Task migration requires its inverse identity handoff")
        digest = _inverse_identity_digest(inverse_identity or {"version": 1, "owners": []})
        if digest != source_context["handoff_sha256"]:
            raise ValueError("restored-source Task identity handoff does not match its context")
    staged = _stage(db)
    field_map = task_run_entity_definitions.task_run_field_map()
    logical = defaultdict(list)
    for item in field_map:
        if item["mapping_layer"] == "logical_projection":
            logical[item["source_kind"]].append(item)

    all_rows = {kind: list(_source_rows(db, kind, stage)) for kind, stage in staged.items()}
    attachment_identity_handoff = handoff["workbench_attachment_identities"]
    attachment_file_handoff = handoff["workbench_attachment_files"]
    attachments_by_physical = {
        int(row["_physical_id"]): row for row in all_rows["workbench_attachments"]
    }
    if set(attachment_identity_handoff) - set(attachments_by_physical):
        raise ValueError("Workbench attachment identity handoff names a missing source row")
    for source_id, identity in attachment_identity_handoff.items():
        row = attachments_by_physical[source_id]
        if (row["id"] != identity["source_public_id"]
                or handoff["owners"].get(("workbench_attachments", source_id), source_id)
                != identity["canonical_id"]):
            raise ValueError("Workbench attachment identity handoff differs from source row")
    artifact_identity_handoff = handoff["workbench_artifact_identities"]
    artifacts_by_physical = {
        int(row["_physical_id"]): row for row in all_rows["workbench_artifacts"]
    }
    if set(artifact_identity_handoff) - set(artifacts_by_physical):
        raise ValueError("Workbench artifact identity handoff names a missing source row")
    for source_id, identity in artifact_identity_handoff.items():
        row = artifacts_by_physical[source_id]
        if (row["id"] != identity["source_public_id"]
                or handoff["owners"].get(("workbench_artifacts", source_id), source_id)
                != identity["canonical_id"]):
            raise ValueError("Workbench artifact identity handoff differs from source row")
    for source_id, file_handoff in attachment_file_handoff.items():
        if attachments_by_physical[source_id]["storage_path"] != file_handoff["source_path"]:
            raise ValueError("Workbench attachment source locator differs from handoff")
    meeting_rows = all_rows["meeting_alignment_jobs"]
    meeting_key_edges = handoff["meeting_business_keys"]
    reply_key_rowids = {int(row["_physical_id"]) for row in all_rows["business_object_tasks"]}
    if reply_key_rowids & set(meeting_key_edges.values()):
        raise ValueError("Meeting delivery-key row ID collides with a source Reply business key")
    source_owner_kind_by_physical = {
        int(row["_physical_id"]): kind
        for kind, rows in all_rows.items() for row in rows
    }
    for source_id in meeting_key_edges:
        actual_kind = source_owner_kind_by_physical.get(source_id)
        if actual_kind is not None and actual_kind != "meeting_alignment_jobs":
            raise ValueError("Meeting delivery-key handoff points to a different source owner kind")
    # A missing source Meeting is a retired owner after the old-code inverse
    # interval. Its edge is intentionally not replayed; current owners alone
    # drive reforward, while the inverse still reserved its row ID in the old
    # shared Tasks allocator.
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
            physical_id = int(row["_physical_id"])
            target_id = handoff["owners"].get((kind, physical_id), physical_id)
            if target_id != _target_id(db, target, target_id):
                raise ValueError("inverse identity handoff does not match the native owner")
            target_ids[(kind, int(row["_physical_id"]))] = target_id
            id_field = "id" if "id" in row else "task_id" if kind == "email_agent_classification_tasks" else None
            if id_field:
                public_rows.append({"source_kind": kind, "physical_id": int(row["_physical_id"]),
                                    "public_key": row[id_field], "target_id": target_id})
    indexes = owner_indexes(public_rows)

    def planned_claim_owner(adapter, source_id):
        try:
            public_id = int(source_id)
        except (TypeError, ValueError):
            return None
        if str(public_id) != str(source_id):
            return None
        return indexes["public"].get(
            (_DISPATCHER_TASK_KINDS[adapter], _typed_key(public_id)))

    planned_native_owners = {
        (kind, target_ids[(kind, int(row["_physical_id"]))])
        for kind, rows in all_rows.items() if kind in _TASK_TABLES for row in rows
    }
    # Reject unknown IDs that would numerically name a differently keyed
    # native owner before the source Tasks declaration is replaced.
    _dispatcher_claim_state_plan(
        db, planned_claim_owner,
        lambda adapter, task_id: (_DISPATCHER_TASK_KINDS[adapter], task_id)
        in planned_native_owners,
    )

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
            values = {"id": target_ids[(kind, int(row["_physical_id"]))], "kind": _task_kind(kind)}
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
                    values["id"] = target_ids[(kind, int(row["_physical_id"]))]
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
                    if (kind == "workbench_attachments" and source_field == "storage_path"
                            and int(row["_physical_id"]) in attachment_file_handoff):
                        value = attachment_file_handoff[int(row["_physical_id"])]["native_path"]
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
                    if kind in {"feedback_processing_items", "feedback_processing_rounds",
                                "feedback_iteration_decisions"} and source_field in {
                        "workbench_task_id", "workbench_turn_id",
                    }:
                        ref_markers = []
                        task_ref, turn_ref = translate_feedback_workbench_refs(
                            task_ref=row.get("workbench_task_id"),
                            turn_ref=row.get("workbench_turn_id"),
                            task_indexes=indexes, turn_indexes=indexes,
                            unresolved=ref_markers,
                        )
                        value = task_ref if source_field == "workbench_task_id" else turn_ref
                        for marker in ref_markers:
                            if marker["field"] == source_field:
                                reference_exceptions.append({"source_kind": kind,
                                    "physical_id": row["_physical_id"], **marker})
                    if kind == "feedback_processing_items" and source_field == "current_round_id":
                        ref_markers = []
                        value = translate_feedback_round_pointer(value, indexes, ref_markers)
                        for marker in ref_markers:
                            reference_exceptions.append({"source_kind": kind,
                                "physical_id": row["_physical_id"], **marker})
                    if kind == "feedback_processing_rounds" and source_field == "scope_receipt_json":
                        ref_markers = []
                        value = translate_feedback_scope_receipt(value, indexes,
                                                                unresolved=ref_markers)
                        for marker in ref_markers:
                            reference_exceptions.append({"source_kind": kind,
                                "physical_id": row["_physical_id"], **marker})
                    values[dest] = value
                if target == "task_inputs":
                    values["id"] = target_ids[(kind, int(row["_physical_id"]))]
                    values["task_id"] = target_ids[(kind, int(row["_physical_id"]))] if kind == "reply_tasks" else values.get("task_id")
                if target in {"workbench_turns", "feedback_processing_items", "email_classification_inputs"}:
                    values["task_id"] = target_ids[(kind, int(row["_physical_id"]))]
                if target == "task_results":
                    values["task_id"] = target_ids[(kind, int(row["_physical_id"]))]
                    values["result_kind"] = _result_kind(kind)
                if target == "work_summary_inputs":
                    values["task_id"] = target_ids[(kind, int(row["_physical_id"]))]
                if target == "feedback_processing_items" and values.get("batch_id") is not None:
                    pass
                _insert(db, target, values)

    # Store only identity edges that cannot be reconstructed from native IDs.
    for identity in public_rows:
        if (_typed_key(identity["public_key"]) == ("integer", identity["target_id"])
                and identity["physical_id"] == identity["target_id"]):
            continue
        db.execute("insert into task_run_identity_map(source_kind,physical_id,public_key,public_key_type,target_id) values(?,?,?,?,?)",
            (identity["source_kind"], identity["physical_id"],
             _stored_public_key(identity["public_key"]),
             _typed_key(identity["public_key"])[0], identity["target_id"]))
        identity_records.append(identity)

    # Hidden source ROWID owners have no public key, but a new native row may
    # have a canonical ID that differs from its restored source physical ID.
    # Retain only those sparse physical/canonical edges so the next inverse
    # can recover the same source ROWID and its raw-key exception.
    for kind, rows in all_rows.items():
        for row in rows:
            if "id" in row or (kind == "email_agent_classification_tasks" and "task_id" in row):
                continue
            physical_id = int(row["_physical_id"])
            target_id = target_ids[(kind, physical_id)]
            if physical_id == target_id:
                continue
            identity = {"source_kind": kind, "physical_id": physical_id,
                        "public_key": None, "target_id": target_id}
            db.execute(
                "insert into task_run_identity_map(source_kind,physical_id,public_key,public_key_type,target_id) "
                "values(?,?,NULL,'null',?)", (kind, physical_id, target_id),
            )
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
            db.execute(f"update tasks set {assignments} where id=?",
                       (*updates.values(), target_ids[("reply_tasks", int(row["_physical_id"]))]))

    # Meeting delivery identity is an exact Task-owned association. The key
    # text is rederived from source facts; only its native row ID crosses an
    # inverse/forward boundary.
    for row in meeting_rows:
        physical_id = int(row["_physical_id"])
        meeting_task_id = target_ids[("meeting_alignment_jobs", physical_id)]
        delivery_key = f"meeting-alignment:{row['id']}:{row['meeting_id']}"
        key_row_id = meeting_key_edges.get(physical_id)
        if key_row_id is None:
            db.execute("insert into task_business_keys(task_id,business_key) values(?,?)",
                       (meeting_task_id, delivery_key))
        else:
            db.execute("insert into task_business_keys(id,task_id,business_key) values(?,?,?)",
                       (key_row_id, meeting_task_id, delivery_key))

    # Install exact target indexes and triggers only after bulk data insertion.
    for name, sql in objects.items():
        if name in target_tables or name.startswith(("idx_", "trg_")):
            if not sql.lstrip().lower().startswith("create table"):
                db.execute(sql)
    attachment_sequence = _sequence(db, "workbench_attachments")
    attachment_water = attachment_sequence[1] if attachment_sequence else 0
    if inverse_identity is not None:
        attachment_water = max(
            attachment_water, handoff["workbench_attachment_highwater"]
        )
    # Keep the captured Tasks sequence rowid stable while restoring it; target
    # AUTOINCREMENT owners may have allocated sqlite_sequence rowids meanwhile.
    db.execute("delete from main.sqlite_sequence where name='workbench_attachments'")
    db.execute("delete from main.sqlite_sequence where name='tasks'")
    if metadata["task_sequence"] is not None:
        rowid, water = metadata["task_sequence"]
        db.execute("insert into main.sqlite_sequence(rowid,name,seq) values(?, 'tasks', ?)",
                   (rowid, water))
    if attachment_water:
        db.execute(
            "insert into main.sqlite_sequence(name,seq) values('workbench_attachments',?)",
            (attachment_water,),
        )
    for stage in staged.values():
        db.execute("drop table temp." + _quote(stage))
    _translate_dispatcher_claim_state(
        db, lambda adapter, source_id: resolve_dispatcher_claim_source_id(
            db, adapter_name=adapter, source_id=source_id,
        ),
    )
    return {
        "counts": {kind: len(rows) for kind, rows in all_rows.items()},
        "identity_rows": len(identity_records),
        "identity_edges": identity_records,
        "source_id_ranges": _identity_ranges(all_rows),
        "owner_highwaters": {kind: max((int(row["_physical_id"]) for row in rows), default=0)
                             for kind, rows in all_rows.items()},
        "raw_key_exceptions": raw_key_exceptions,
        "reference_exceptions": reference_exceptions,
        "task_sequence": metadata["task_sequence"],
        "source_objects": source_objects,
    }


def _validate_inverse_identity(value: dict | None) -> dict:
    """Validate the content-free identity handoff returned by the inverse."""
    if value is None:
        return {"owners": {}, "meeting_business_keys": {},
                "workbench_parent_identities": {},
                "workbench_attachment_identities": {},
                "workbench_artifact_identities": {},
                "workbench_attachment_files": {},
                "workbench_attachment_highwater": 0}
    if (not isinstance(value, dict) or not {"version", "owners"}.issubset(value)
            or not set(value).issubset({"version", "owners", "meeting_business_keys",
                                        "workbench_parent_identities",
                                        "workbench_attachment_identities",
                                        "workbench_artifact_identities",
                                        "workbench_attachment_files",
                                        "workbench_attachment_highwater"})
            or value["version"] != 1):
        raise ValueError("unsupported inverse Task identity handoff")
    if not isinstance(value["owners"], list):
        raise ValueError("invalid inverse Task identity handoff owners")
    attachment_highwater = value.get("workbench_attachment_highwater", 0)
    if type(attachment_highwater) is not int or attachment_highwater < 0:
        raise ValueError("invalid Workbench attachment allocation highwater")
    result = {}
    canonical = set()
    for row in value["owners"]:
        if (not isinstance(row, dict) or set(row) != {"source_kind", "source_physical_id", "canonical_id"}
                or row["source_kind"] not in task20_definitions.TASK20_COLUMNS
                or type(row["source_physical_id"]) is not int or type(row["canonical_id"]) is not int):
            raise ValueError("invalid inverse Task identity handoff owner")
        key = (row["source_kind"], row["source_physical_id"])
        target = row["canonical_id"]
        if key in result or (row["source_kind"], target) in canonical:
            raise ValueError("duplicate inverse Task identity handoff owner")
        result[key] = target
        canonical.add((row["source_kind"], target))
    key_edges = {}
    key_ids = set()
    raw_key_edges = value.get("meeting_business_keys", [])
    if not isinstance(raw_key_edges, list):
        raise ValueError("invalid Meeting delivery-key identity handoff")
    for row in raw_key_edges:
        if (not isinstance(row, dict) or set(row) != {
                "source_meeting_physical_id", "task_business_key_row_id"}
                or type(row["source_meeting_physical_id"]) is not int
                or type(row["task_business_key_row_id"]) is not int
                or row["source_meeting_physical_id"] < 1
                or row["task_business_key_row_id"] < 1):
            raise ValueError("invalid Meeting delivery-key identity handoff")
        source_id, key_id = row["source_meeting_physical_id"], row["task_business_key_row_id"]
        if source_id in key_edges or key_id in key_ids:
            raise ValueError("duplicate Meeting delivery-key identity handoff")
        key_edges[source_id] = key_id
        key_ids.add(key_id)
    parent_identities = {}
    for row in value.get("workbench_parent_identities", []):
        if (not isinstance(row, dict) or set(row) != {
                "source_physical_id", "canonical_id", "source_public_id"}
                or type(row["source_physical_id"]) is not int
                or type(row["canonical_id"]) is not int
                or not isinstance(row["source_public_id"], str)
                or str(UUID(row["source_public_id"])) != row["source_public_id"]):
            raise ValueError("invalid Workbench parent identity handoff")
        key = row["source_physical_id"]
        if key in parent_identities:
            raise ValueError("duplicate Workbench parent identity handoff")
        parent_identities[key] = row
    attachment_identities = {}
    attachment_public_ids = set()
    for row in value.get("workbench_attachment_identities", []):
        if (not isinstance(row, dict) or set(row) != {
                    "source_physical_id", "canonical_id", "source_public_id",
                    "is_new_native"}
                or type(row["source_physical_id"]) is not int
                or type(row["canonical_id"]) is not int
                or not isinstance(row["source_public_id"], str)
                or type(row["is_new_native"]) is not bool):
            raise ValueError("invalid Workbench attachment identity handoff")
        if (row["is_new_native"] and
                str(UUID(row["source_public_id"])) != row["source_public_id"]):
            raise ValueError("invalid Workbench attachment UUID identity")
        if row["source_physical_id"] in attachment_identities or row["source_public_id"] in attachment_public_ids:
            raise ValueError("duplicate Workbench attachment identity handoff")
        attachment_identities[row["source_physical_id"]] = row
        attachment_public_ids.add(row["source_public_id"])
    artifact_identities = {}
    artifact_public_ids = set()
    for row in value.get("workbench_artifact_identities", []):
        if (not isinstance(row, dict) or set(row) != {
                    "source_physical_id", "canonical_id", "source_public_id", "is_new_native"}
                or type(row["source_physical_id"]) is not int
                or type(row["canonical_id"]) is not int
                or type(row["is_new_native"]) is not bool):
            raise ValueError("invalid Workbench artifact identity handoff")
        try:
            source_public_id = _decode_scalar(row["source_public_id"])
            if source_public_id is not None and type(source_public_id) not in (str, int, float, bytes):
                raise ValueError
            if row["is_new_native"] and (
                    not isinstance(source_public_id, str)
                    or str(UUID(source_public_id)) != source_public_id):
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid Workbench artifact public identity") from exc
        if (row["source_physical_id"] in artifact_identities
                or source_public_id in artifact_public_ids):
            raise ValueError("duplicate Workbench artifact identity handoff")
        identity = {**row, "source_public_id": source_public_id}
        artifact_identities[row["source_physical_id"]] = identity
        artifact_public_ids.add(source_public_id)
    files = {}
    for row in value.get("workbench_attachment_files", []):
        if (not isinstance(row, dict) or set(row) != {
                "source_physical_id", "canonical_id", "parent_canonical_id",
                "source_parent_physical_id", "source_parent_public_id",
                "source_path", "native_path",
                "source_public_id", "content_sha256", "size_bytes"}
                or type(row["source_physical_id"]) is not int
                or type(row["canonical_id"]) is not int
                or type(row["parent_canonical_id"]) is not int
                or type(row["source_parent_physical_id"]) is not int
                or not isinstance(row["source_parent_public_id"], str)
                or str(UUID(row["source_parent_public_id"])) != row["source_parent_public_id"]
                or not isinstance(row["source_public_id"], str)
                or str(UUID(row["source_public_id"])) != row["source_public_id"]
                or Path(row["source_path"]).name != row["source_public_id"]
                or Path(row["source_path"]).parent.name != row["source_parent_public_id"]
                or not all(isinstance(row[key], str) and row[key] for key in
                           ("source_path", "native_path", "content_sha256"))
                or type(row["size_bytes"]) is not int or row["size_bytes"] < 0):
            raise ValueError("invalid Workbench attachment file handoff")
        key = row["source_physical_id"]
        if key in files or key not in attachment_identities:
            raise ValueError(
                "unmatched Workbench attachment file handoff: "
                f"source={key}, identities={sorted(attachment_identities)}"
            )
        if (row["canonical_id"] != attachment_identities[key]["canonical_id"]
                or row["source_public_id"] != attachment_identities[key]["source_public_id"]
                or row["source_parent_physical_id"] not in parent_identities
                or row["parent_canonical_id"] != parent_identities[
                    row["source_parent_physical_id"]
                ]["canonical_id"]
                or row["source_parent_public_id"] != parent_identities[
                    row["source_parent_physical_id"]
                ]["source_public_id"]
                or len(row["content_sha256"]) != 64):
            raise ValueError("WorkBench attachment file identity mismatch")
        files[key] = row
    return {"owners": result, "meeting_business_keys": key_edges,
            "workbench_parent_identities": parent_identities,
            "workbench_attachment_identities": attachment_identities,
            "workbench_artifact_identities": artifact_identities,
            "workbench_attachment_files": files,
            "workbench_attachment_highwater": attachment_highwater}


def _inverse_identity_digest(value: dict) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _restore_raw_physical_edges(
        physical_target: dict, raw_exceptions: list[dict],
        native_targets: set[tuple[str, int]]) -> dict:
    """Translate source physical exceptions back through native owner IDs."""
    result = dict(physical_target)
    target_by_source = {}
    for (kind, target_id), source_id in result.items():
        key = (kind, source_id)
        previous = target_by_source.setdefault(key, target_id)
        if previous != target_id:
            raise ValueError("ambiguous inverse Task physical identity edge")
    for row in raw_exceptions:
        kind, source_id = row["source_kind"], row["physical_id"]
        target_id = target_by_source.get((kind, source_id))
        if target_id is None:
            # Only an already indexed identity edge proves the source ID is
            # also the native target ID; never infer it from the raw key.
            if ((kind, source_id) not in native_targets
                    or result.get((kind, source_id)) not in (None, source_id)):
                raise ValueError("raw Task identity exception has no inverse owner edge")
            target_id = source_id
        existing = result.get((kind, target_id))
        if existing is not None and existing != source_id:
            raise ValueError("raw Task identity exception conflicts with inverse owner edge")
        result.setdefault((kind, target_id), source_id)
    return result


def _inverse_indexes(db: sqlite3.Connection, forward_metadata: dict) -> tuple[dict, dict, dict]:
    """Rebuild the typed owner index from native owners and sparse exceptions."""
    edges = forward_metadata.get("identity_edges")
    raw_exceptions = forward_metadata.get("raw_key_exceptions")
    owner_highwaters = forward_metadata.get("owner_highwaters")
    source_id_ranges = forward_metadata.get("source_id_ranges")
    if (not isinstance(edges, list) or not isinstance(raw_exceptions, list)
            or not isinstance(owner_highwaters, dict) or not isinstance(source_id_ranges, dict)
            or set(source_id_ranges) != set(task20_definitions.TASK20_COLUMNS)):
        raise ValueError("Task inverse requires sparse identity metadata")
    for kind, ranges in source_id_ranges.items():
        if type(ranges) is not list:
            raise ValueError("invalid Task source identity ranges")
        previous = -1
        for pair in ranges:
            if (type(pair) is not list or len(pair) != 2
                    or any(type(value) is not int or value < 1 for value in pair)
                    or pair[0] > pair[1] or pair[0] <= previous + 1):
                raise ValueError(f"invalid Task source identity ranges: {kind}")
            previous = pair[1]
    exceptional = {}
    for row in edges:
        if (not isinstance(row, dict) or set(row) != {
                "source_kind", "physical_id", "public_key", "target_id"}):
            raise ValueError("invalid Task identity edge")
        exceptional[(row["source_kind"], row["target_id"])] = row
    physical_by_target = {
        (row[0], int(row[1])): int(row[2])
        for row in db.execute(
            "select source_kind,target_id,physical_id from main.task_run_identity_map")
    }
    physical_by_target.update({(kind, target_id): row["physical_id"]
                               for (kind, target_id), row in exceptional.items()})
    target_by_physical = {(kind, physical_id): target_id
                          for (kind, target_id), physical_id in physical_by_target.items()}
    raw_by_target = {}
    for row in raw_exceptions:
        physical_id = row["physical_id"]
        target_id = target_by_physical.get((row["source_kind"], physical_id), physical_id)
        raw_by_target[(row["source_kind"], target_id)] = row
    public_rows = []
    for kind in task20_definitions.TASK20_COLUMNS:
        table = _target_table(kind)
        columns = {row[1] for row in db.execute(f"pragma main.table_info({_quote(table)})")}
        if table == "tasks":
            owner_ids = [int(row[0]) for row in db.execute(
                "select id from main.tasks where kind=?", (_TASK_TABLES.get(kind, "domain"),))]
        elif kind == "business_object_tasks":
            owner_ids = [int(row[0]) for row in db.execute(
                "select business.id from main.task_business_keys business "
                "join main.tasks task on task.id=business.task_id "
                "where task.kind='reply' order by business.id")]
        else:
            key_column = "id" if "id" in columns else "task_id"
            owner_ids = [int(row[0]) for row in db.execute(
                f"select {_quote(key_column)} from main.{_quote(table)}")]
        has_public = any(name in {field[0] for field in task20_definitions.TASK20_COLUMNS[kind]}
                         for name in ("id", "task_id"))
        for target_id in owner_ids:
            edge = exceptional.get((kind, target_id))
            raw = raw_by_target.get((kind, target_id))
            if edge is None and raw is None and not _identity_member(
                    source_id_ranges, kind, target_id):
                continue
            physical_id = (edge["physical_id"] if edge is not None else
                           raw["physical_id"] if raw is not None else
                           physical_by_target.get((kind, target_id), target_id))
            if has_public:
                public_rows.append({"source_kind": kind, "physical_id": physical_id,
                    "public_key": edge["public_key"] if edge is not None and edge["public_key"] is not None
                                 else target_id,
                    "target_id": target_id})
    public = {}
    for row in public_rows:
        if not isinstance(row, dict) or set(row) != {"source_kind", "physical_id", "public_key", "target_id"}:
            raise ValueError("invalid Task public owner metadata")
        key = (row["source_kind"], _typed_key(row["public_key"]))
        if key in public:
            raise ValueError("ambiguous Task public owner metadata")
        public[key] = row
    public_identity = {(row["source_kind"], row["physical_id"]): row["public_key"] for row in public_rows}
    physical_by_target.update({(row["source_kind"], row["target_id"]): row["physical_id"]
                               for row in public_rows})
    edges = {(row["source_kind"], row["physical_id"]): row["public_key"]
             for row in forward_metadata.get("identity_edges", []) if row["public_key"] is not None}
    public_target = {(row["source_kind"], row["target_id"]): row["public_key"] for row in public_rows}
    public_index = {(row["source_kind"], _typed_key(row["public_key"])): row["target_id"]
                    for row in public_rows}
    return public, {}, {"public_by_source": public_identity, "physical_target": physical_by_target,
                              "public_target": public_target,
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
    required = {"source_objects", "task_sequence", "identity_edges", "owner_highwaters",
                "raw_key_exceptions", "reference_exceptions"}
    if not required.issubset(forward_metadata):
        raise ValueError("incomplete Task inverse metadata")
    if source_metadata["source_objects"] != forward_metadata["source_objects"]:
        raise ValueError("Task source declaration metadata differs from forward capture")
    _, _, identities = _inverse_indexes(db, forward_metadata)
    attachment_sequence = _sequence(db, "workbench_attachments")
    attachment_rows_highwater = db.execute(
        "select coalesce(max(id),0) from main.workbench_attachments"
    ).fetchone()[0]
    attachment_highwater = max(
        int(attachment_rows_highwater),
        attachment_sequence[1] if attachment_sequence else 0,
    )
    unsupported_key_owner = db.execute(
        "select task.kind from main.task_business_keys business "
        "join main.tasks task on task.id=business.task_id "
        "where task.kind not in ('reply','meeting_alignment') limit 1"
    ).fetchone()
    if unsupported_key_owner is not None:
        raise ValueError("TaskBusinessKey owner kind has no current-source inverse")
    meeting_key_timestamp = db.execute(
        "select business.id from main.task_business_keys business "
        "join main.tasks task on task.id=business.task_id "
        "where task.kind='meeting_alignment' "
        "and (business.created_at is not null or business.updated_at is not null) limit 1"
    ).fetchone()
    if meeting_key_timestamp is not None:
        raise ValueError(
            "Meeting TaskBusinessKey timestamps have no current-source inverse"
        )
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
        elif kind == "business_object_tasks":
            rows = db.execute(
                "select business.id from main.task_business_keys business "
                "join main.tasks task on task.id=business.task_id "
                "where task.kind='reply' order by business.id").fetchall()
        else:
            columns = {row[1] for row in db.execute(f"pragma main.table_info({_quote(table)})")}
            key_column = "id" if "id" in columns else "task_id" if "task_id" in columns else None
            if key_column is None:
                raise ValueError(f"native owner has no reconstructible identity: {kind}")
            rows = db.execute(f"select {_quote(key_column)} from main.{_quote(table)} order by {_quote(key_column)}").fetchall()
        for (target_id,) in rows:
            physical_rows.append({"source_kind": kind, "physical_id": int(target_id), "target_id": int(target_id)})
    old_physical = _restore_raw_physical_edges(
        identities["physical_target"], forward_metadata["raw_key_exceptions"],
        {(row["source_kind"], row["target_id"]) for row in physical_rows},
    )
    old_public = identities["public_target"]
    source_key_values = defaultdict(list)
    for (kind, _), value in old_public.items():
        source_key_values[kind].append(value)
    for item in forward_metadata["raw_key_exceptions"]:
        source_key_values[item["source_kind"]].append(item["value"])
    source_key_next = {}
    from app.task20_definitions import TASK12_AUTOINCREMENT_KINDS
    for kind, values in source_key_values.items():
        numeric = [int(value) for value in values if type(value) is int or
                   isinstance(value, str) and value.isdecimal()]
        sequence_row = _sequence(db, "tasks:" + kind) if kind in TASK12_AUTOINCREMENT_KINDS else None
        water = max([*numeric, sequence_row[1] if sequence_row else 0], default=0)
        source_key_next[kind] = water + 1
    reserved_physical = set(old_physical.values())
    reserved_physical.update(
        row["physical_id"] for row in forward_metadata["raw_key_exceptions"]
    )
    meeting_key_rows = [tuple(row) for row in db.execute(
        "select business.id,business.task_id from main.task_business_keys business "
        "join main.tasks task on task.id=business.task_id "
        "where task.kind='meeting_alignment' order by business.id")]
    meeting_key_reservation_water = max((int(row[0]) for row in meeting_key_rows), default=0)
    allocation_water = max(
        source_metadata["task_sequence"][1] if source_metadata["task_sequence"] else 0,
        max(reserved_physical, default=0),
        meeting_key_reservation_water,
    )
    native_sequence = _sequence(db, "tasks")
    original_water = allocation_water
    source_physical_by_native = {}
    next_physical = allocation_water + 1
    allocated_above_water = False
    for identity in physical_rows:
        kind, canonical_id = identity["source_kind"], identity["target_id"]
        original = old_physical.get((kind, canonical_id))
        if original is None:
            candidate = canonical_id
            if candidate <= original_water or candidate in reserved_physical:
                while next_physical in reserved_physical:
                    next_physical += 1
                candidate = next_physical
                next_physical += 1
                allocated_above_water = True
            original = candidate
            reserved_physical.add(original)
            allocation_water = max(allocation_water, original)
        source_physical_by_native[(kind, canonical_id)] = original

    source_public_by_native = {}
    attachment_public_ids = {
        str(value) for (kind, _), value in old_public.items()
        if kind == "workbench_attachments" and isinstance(value, str)
    }
    artifact_public_ids = {
        str(value) for (kind, _), value in old_public.items()
        if kind == "workbench_artifacts" and isinstance(value, str)
    }
    for identity in physical_rows:
        kind, canonical_id = identity["source_kind"], identity["target_id"]
        if (kind, canonical_id) in old_public:
            public_key = old_public[(kind, canonical_id)]
        else:
            source_identity_fields = {field[0] for field in task20_definitions.TASK20_COLUMNS[kind]}
            identity_field = "id" if "id" in source_identity_fields else (
                "task_id" if "task_id" in source_identity_fields else None
            )
            if identity_field is None:
                continue
            if kind == "email_agent_classification_tasks":
                row = db.execute(
                    "select stable_message_identity from main.email_classification_inputs where task_id=?",
                    (canonical_id,),
                ).fetchone()
                if row is None or not isinstance(row[0], str) or not row[0].strip():
                    raise ValueError(
                        f"native Email Task lacks stable message identity: {canonical_id}"
                    )
                public_key = "email-classification:" + hashlib.sha256(
                    row[0].encode("utf-8")
                ).hexdigest()
            else:
                if kind in {"workbench_attachments", "workbench_artifacts"}:
                    public_key = str(uuid4())
                    used_ids = attachment_public_ids if kind == "workbench_attachments" else artifact_public_ids
                    while public_key in used_ids:
                        public_key = str(uuid4())
                    used_ids.add(public_key)
                else:
                    public_key = (str(uuid4()) if kind in {"workbench_tasks", "workbench_turns"}
                              else source_physical_by_native[(kind, canonical_id)]
                              if kind == "reply_tasks" else canonical_id)
        source_public_by_native[(kind, canonical_id)] = public_key

    _translate_dispatcher_claim_state(
        db, lambda adapter, _source_id: source_public_by_native.get(
            (_DISPATCHER_TASK_KINDS[adapter], int(_source_id))
        ) if str(_source_id).isdecimal() else None,
    )

    # Current native Workbench owners may have been created after the original
    # source capture. Their inverse public keys are generated above, so include
    # those exact keys in the reverse reference index used by Feedback rows.
    inverse_public_index = dict(identities["public_index"])
    for (kind, canonical_id), public_key in source_public_by_native.items():
        if kind in {"workbench_tasks", "workbench_turns"}:
            inverse_public_index[(kind, _typed_key(public_key))] = canonical_id

    source_rows = []
    for identity in physical_rows:
        kind, physical_id, target_id = identity["source_kind"], identity["physical_id"], identity["target_id"]
        public_key = source_public_by_native.get((kind, target_id))
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
                        value = source_public_by_native.get(
                            (parent_kind, value),
                            identities["public_target"].get((parent_kind, value), value),
                        )
            if kind in {"feedback_processing_items", "feedback_processing_rounds",
                        "feedback_iteration_decisions"} and field in {
                    "workbench_task_id", "workbench_turn_id"}:
                raw_marker = reference_exceptions.get((kind, physical_id, field))
                marker = None if raw_marker is None else {
                    key: raw_marker[key] for key in ("field", "value", "type")
                }
                if marker is not None and value != marker["value"]:
                    marker = None
                value = restore_feedback_workbench_ref(field=field, current_value=value,
                    indexes={"public": inverse_public_index}, unresolved_marker=marker)
            if kind == "feedback_processing_items" and field == "current_round_id":
                raw_marker = reference_exceptions.get((kind, physical_id, field))
                marker = None if raw_marker is None else {
                    key: raw_marker[key] for key in ("field", "value", "type")
                }
                if marker is not None and value != marker["value"]:
                    marker = None
                value = restore_feedback_round_pointer(
                    value, {"public": identities["public_index"]}, marker)
            if kind == "feedback_processing_rounds" and field == "scope_receipt_json":
                prefix = "scope_receipt_json."
                receipt_markers = {
                    name[len(prefix):]: item
                    for (owner_kind, owner_id, name), item in reference_exceptions.items()
                    if owner_kind == kind and owner_id == physical_id
                    and name.startswith(prefix)
                }
                value = restore_feedback_scope_receipt(
                    value, {"public": inverse_public_index}, unresolved=receipt_markers)
            if dest and dest.startswith("parameters_json.$."):
                values[field] = value
            elif field != "final_text":
                values[field] = value
        if kind == "email_agent_classification_tasks" and (kind, target_id) not in old_public:
            stable_identity = values.get("stable_message_identity")
            if not isinstance(stable_identity, str) or not stable_identity.strip():
                raise ValueError(
                    f"native Email Task lacks stable message identity: {target_id}"
                )
            values["task_id"] = "email-classification:" + hashlib.sha256(
                stable_identity.encode("utf-8")
            ).hexdigest()
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
                value = None if row is None else row[0]
                values["__" + field] = (None if value is None else
                    source_physical_by_native.get(("reply_task_inputs", int(value)), value))
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
                parent_kind = {
                    "reply_task_inputs": "reply_tasks", "business_object_tasks": "reply_tasks",
                    "workbench_turns": "workbench_tasks", "workbench_artifacts": "workbench_turns",
                    "workbench_attachments": "workbench_tasks",
                }[kind]
                parent_id = source_physical_by_native.get((parent_kind, int(row[0])))
                if parent_id is None:
                    raise ValueError(f"unresolved native {kind} parent identity")
        elif kind == "feedback_iteration_decisions":
            row = db.execute(
                "select batch.id from main.feedback_iteration_decisions decision "
                "join main.feedback_processing_batches batch using(batch_id) where decision.id=?",
                (target_id,),
            ).fetchone()
            parent_id = None if row is None else int(row[0])
        source_physical_id = source_physical_by_native[(kind, target_id)]
        source_rows.append((kind, source_physical_id, target_id, public_key, values, parent_id))

    meeting_ids = {target_id: values.get("meeting_id")
                   for kind, _, target_id, _, values, _ in source_rows
                   if kind == "meeting_alignment_jobs"}
    key_by_task = defaultdict(list)
    for key_id, task_id in meeting_key_rows:
        key_by_task[int(task_id)].append(int(key_id))
    if set(key_by_task) != set(meeting_ids):
        raise ValueError("Meeting Task must own exactly one delivery identity association")
    for task_id, meeting_id in meeting_ids.items():
        key_ids = key_by_task[task_id]
        public_id = source_public_by_native[("meeting_alignment_jobs", task_id)]
        key_row = db.execute("select business_key from main.task_business_keys where id=?",
                             (key_ids[0],)).fetchone() if len(key_ids) == 1 else None
        expected_key = f"meeting-alignment:{public_id}:{meeting_id}"
        if key_row is None or key_row[0] != expected_key:
            raise ValueError("Meeting delivery identity does not match its source facts")

    physical_owners = {}
    for kind, physical_id, target_id, _, _, _ in source_rows:
        if physical_id in physical_owners:
            raise ValueError(
                f"inverse source physical ID {physical_id} is claimed by "
                f"{physical_owners[physical_id][0]} target {physical_owners[physical_id][1]} "
                f"and {kind} target {target_id}")
        physical_owners[physical_id] = (kind, target_id)

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
            if public_key is None:
                raw_value = source_key_next.get(kind, 1)
                source_key_next[kind] = raw_value + 1
            elif kind == "email_agent_classification_tasks" and (kind, target_id) not in old_public:
                raw_value = target_id
            elif kind == "workbench_artifacts":
                raw_value = source_key_next.get(kind, 1)
                source_key_next[kind] = raw_value + 1
            elif kind == "workbench_attachments":
                raw_value = physical_id
            else:
                raw_value = public_key
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
    if sequence is not None or native_sequence is not None:
        rowid = sequence[0] if sequence is not None else native_sequence[0]
        water = max(
            sequence[1] if sequence is not None else 0,
            native_sequence[1] if native_sequence is not None else 0,
            allocation_water if allocated_above_water else 0,
            meeting_key_reservation_water,
        )
        db.execute("insert into main.sqlite_sequence(rowid,name,seq) values(?, 'tasks', ?)",
                   (rowid, water))
    meeting_business_keys = []
    for row in meeting_key_rows:
        source_meeting_physical_id = source_physical_by_native.get(
            ("meeting_alignment_jobs", int(row[1])))
        if source_meeting_physical_id is None:
            raise ValueError("Meeting delivery-key owner has no inverse Task identity")
        meeting_business_keys.append({
            "source_meeting_physical_id": source_meeting_physical_id,
            "task_business_key_row_id": int(row[0]),
        })
    attachment_identities = []
    artifact_identities = []
    parent_target_by_source = {
        source_id: native_id for (kind, native_id), source_id in source_physical_by_native.items()
        if kind == "workbench_tasks"
    }
    for kind, physical_id, target_id, public_key, _, parent_physical in source_rows:
        if kind == "workbench_artifacts":
            artifact_identities.append({
                "source_physical_id": physical_id,
                "canonical_id": target_id,
                "source_public_id": _encoded_native_sqlite_value(public_key),
                "is_new_native": (kind, target_id) not in old_public,
            })
        if kind != "workbench_attachments" or (kind, target_id) in old_public:
            continue
        parent_target = parent_target_by_source.get(parent_physical)
        if parent_target is None:
            raise ValueError("restored Workbench attachment parent identity is missing")
        attachment_identities.append({
            "source_physical_id": physical_id,
            "canonical_id": target_id,
            "source_public_id": public_key,
            "is_new_native": (kind, target_id) not in old_public,
        })
    inverse_identity = {
        "version": 1,
        "owners": [
            {"source_kind": kind, "source_physical_id": source_id, "canonical_id": target_id}
            for (kind, target_id), source_id in source_physical_by_native.items()
            if source_id != target_id
        ],
        "meeting_business_keys": meeting_business_keys,
        "workbench_attachment_highwater": attachment_highwater,
    }
    if attachment_identities:
        inverse_identity["workbench_attachment_identities"] = attachment_identities
    if artifact_identities:
        inverse_identity["workbench_artifact_identities"] = artifact_identities
    if any(kind == "workbench_attachments" for kind, *_ in source_rows):
        parent_identities = []
        for kind, _, target_id, _, _, _ in source_rows:
            if kind != "workbench_tasks":
                continue
            public_id = source_public_by_native[("workbench_tasks", target_id)]
            if not isinstance(public_id, str):
                continue
            try:
                if str(UUID(public_id)) != public_id:
                    continue
            except ValueError:
                continue
            parent_identities.append({
                "source_physical_id": source_physical_by_native[("workbench_tasks", target_id)],
                "canonical_id": target_id,
                "source_public_id": public_id,
            })
        if parent_identities:
            inverse_identity["workbench_parent_identities"] = parent_identities
    source_context = {
        "version": 1,
        "handoff_required": bool(inverse_identity["owners"] or meeting_business_keys
                                  or attachment_identities),
        "handoff_sha256": _inverse_identity_digest(inverse_identity),
    }
    return {
        "rows": len(source_rows),
        "source_objects": forward_metadata["source_objects"],
        "sparse_identity_metadata": {
            "identity_edges": forward_metadata["identity_edges"],
            "raw_key_exceptions": forward_metadata["raw_key_exceptions"],
            "reference_exceptions": forward_metadata["reference_exceptions"],
        },
        "inverse_identity": inverse_identity,
        "source_context": source_context,
    }
