"""Current-fact identity planning for the unactivated adoption inverse.

This module plans identities only. It never copies adoption bodies, mutates the
database, or persists a second handoff/allocator registry. The later materializer
owns writes inside the caller's FK-off transaction.
"""
from __future__ import annotations

import sqlite3


_KINDS = (
    "review_candidates", "candidate_reviews", "candidate_selections",
    "candidate_supplements", "candidate_execution_alias",
)


def _typed_public(value, value_type):
    if value_type == "null":
        if value is not None:
            raise ValueError("invalid null source public identity")
        return None
    if value_type == "integer":
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid integer source public identity") from exc
    if value_type == "text":
        if not isinstance(value, str):
            raise ValueError("invalid text source public identity")
        return value
    if value_type == "blob":
        if not isinstance(value, (bytes, bytearray, memoryview)):
            raise ValueError("invalid blob source public identity")
        return bytes(value)
    raise ValueError("unsupported source public identity type")


def _next_plain_rowids(occupied: set[int], count: int) -> list[int]:
    """Preview old Actions IDs with its actual non-AUTOINCREMENT ROWID rule."""
    if any(type(value) is not int for value in occupied) or type(count) is not int or count < 0:
        raise ValueError("invalid Action rowid request")
    scratch = sqlite3.connect(":memory:")
    try:
        scratch.execute("create table actions(id integer primary key)")
        scratch.executemany("insert into actions(id) values(?)", ((value,) for value in occupied))
        return [int(scratch.execute("insert into actions default values returning id").fetchone()[0])
                for _ in range(count)]
    finally:
        scratch.close()


def _cast_public_integer(value, value_type) -> int:
    if value_type == "null":
        return 0
    if value_type == "blob":
        value = sqlite3.Binary(value)
    with sqlite3.connect(":memory:") as scratch:
        return int(scratch.execute("select cast(? as integer)", (value,)).fetchone()[0])


def _run_sources(db, run_inverse_metadata):
    if type(run_inverse_metadata) is not dict or not {
            "source_objects", "sequence_rows", "run_fk_findings", "run_handoff"}.issubset(run_inverse_metadata):
        raise ValueError("adoption inverse requires actual Run inverse metadata")
    handoff = run_inverse_metadata["run_handoff"]
    if type(handoff) is not dict or set(handoff) != {
            "version", "source_context", "identity_edges", "owner_highwaters", "digest"} or handoff["version"] != 1:
        raise ValueError("invalid Run inverse handoff")
    from app.run_entity_schema import run_handoff_context, run_handoff_digest
    expected_context = run_handoff_context(run_inverse_metadata)
    if handoff["source_context"] != expected_context or handoff["digest"] != run_handoff_digest(
            handoff["source_context"], handoff["identity_edges"], handoff["owner_highwaters"]):
        raise ValueError("Run inverse handoff digest or source context mismatch")
    cursor = db.execute("select id,task_id,role,execution_generation,proposal_revision "
                        "from main.runs where record_type='agent_runs'")
    names = [item[0] for item in cursor.description]
    physical_runs = {int(row[0]): dict(zip(names, row, strict=True)) for row in cursor.fetchall()}
    canonical_to_source = {}
    for edge in handoff["identity_edges"]:
        if type(edge) is not dict or set(edge) != {"source_kind", "canonical_id", "source_physical_id"}:
            raise ValueError("invalid Run inverse identity edge")
        if type(edge["canonical_id"]) is not int or type(edge["source_physical_id"]) is not int:
            raise ValueError("invalid source Run owner edge")
        if edge["source_kind"] != "agent_runs":
            continue
        canonical = edge["canonical_id"]
        physical = edge["source_physical_id"]
        if canonical in canonical_to_source or physical not in physical_runs or canonical == physical:
            raise ValueError("duplicate or missing source Run owner")
        canonical_to_source[canonical] = physical
    return canonical_to_source, physical_runs


def _source_run(canonical_id, canonical_to_source, physical_runs):
    physical = canonical_to_source.get(canonical_id, canonical_id)
    run = physical_runs.get(physical)
    if run is None:
        raise ValueError(f"missing source Run owner: {canonical_id}")
    return physical, run


def _inverse_weak_run_ref(value, canonical_to_source, physical_runs):
    """Translate a current Run ID through its sparse edge or actual source membership."""
    if value is None or value == 0:
        return value
    if type(value) is not int:
        raise ValueError(f"invalid stored Run reference type: {value!r}")
    return _source_run(value, canonical_to_source, physical_runs)[0]


def _map_rows(db, forward_metadata, canonical_to_source, physical_runs):
    rows = db.execute("select source_kind,source_physical_id,source_public_id,source_public_id_type,target_run_id,target_child_id from main.run_adoption_identity_map").fetchall()
    try:
        expected = {(r[0], int(r[1]), int(r[2]), None if r[3] is None else int(r[3]))
                    for r in forward_metadata["identities"]}
    except (IndexError, TypeError, ValueError) as exc:
        raise ValueError("invalid adoption forward identity metadata") from exc
    seen_physical_ids = set()
    actual = set()
    result = {}
    for kind, physical, public, public_type, target_run, target_child in rows:
        if (kind not in _KINDS or type(physical) is not int or type(target_run) is not int
                or (target_child is not None and type(target_child) is not int)):
            raise ValueError("invalid adoption identity-map owner")
        if physical in seen_physical_ids:
            raise ValueError("duplicate adoption physical identity")
        seen_physical_ids.add(physical)
        key = (kind, physical)
        actual.add((kind, physical, int(target_run),
                    None if target_child is None else int(target_child)))
        raw = _typed_public(public, public_type)
        source_run, _ = _source_run(int(target_run), canonical_to_source, physical_runs)
        result[key] = {"source_kind": kind, "physical_id": physical,
                       "public_id": raw, "public_id_type": public_type,
                       "canonical_run_id": int(target_run), "source_run_id": source_run,
                       "target_child_id": target_child}
    if actual != expected:
        raise ValueError("adoption identity map differs from forward metadata")
    return result


def _action_allocator_facts(db, source_metadata):
    captured = source_metadata["source_sequences"]
    current = [list(row) for row in db.execute(
        "select rowid,name,seq from main.sqlite_sequence where name='actions'")]
    return captured, current


def plan_run_adoption_inverse(db: sqlite3.Connection, source_metadata: dict,
                              forward_metadata: dict, *, run_inverse_metadata: dict) -> dict:
    """Resolve current native owners and plan old Action identities, without writes.

    Call after Task2B then Run3B inverse inside a caller-owned FK-off
    transaction. Returned records contain identity and allocator facts only;
    candidate/review/provider bodies remain owned by native tables.
    """
    if not db.in_transaction or db.execute("pragma foreign_keys").fetchone()[0]:
        raise ValueError("adoption inverse planning requires caller-owned FK-off transaction")
    required = {"version", "action_catalog", "receipt_catalog", "observer_catalog",
                "identity_catalog", "receipt_columns", "source_sequences",
                "selected_counts", "source_fk_findings"}
    if type(source_metadata) is not dict or set(source_metadata) != required or source_metadata["version"] != 1:
        raise ValueError("unsupported adoption source metadata")
    if type(forward_metadata) is not dict or forward_metadata.get("version") != 1 or type(forward_metadata.get("identities")) is not list:
        raise ValueError("unsupported adoption forward metadata")
    canonical_to_source, physical_runs = _run_sources(db, run_inverse_metadata)
    identities = _map_rows(db, forward_metadata, canonical_to_source, physical_runs)

    # A map entry is current only while its exact native owner still exists.
    current = set()
    for run_id, stage, predecessor, *_ in db.execute(
            "select consumer_run_id,stage_index,predecessor_review_id,candidate_digest,candidate_json,invalidated_at,invalidation_reason,created_at from main.run_adopted_results"):
        current.add(("review_candidates", int(run_id), None))
    for row_id, consumer, audit in db.execute(
            "select id,consumer_run_id,audit_run_id from main.run_adopted_reviews"):
        current.add(("candidate_reviews", int(audit), int(row_id)))
    for row_id, consumer, audit in db.execute(
            "select id,consumer_run_id,audit_run_id from main.run_adopted_selections"):
        current.add(("candidate_selections", int(consumer), int(row_id)))
    for row_id, consumer in db.execute("select id,consumer_run_id from main.run_adopted_supplements"):
        current.add(("candidate_supplements", int(consumer), int(row_id)))
    for consumer, *_ in db.execute("select consumer_run_id from main.run_execution_progress"):
        current.add(("candidate_execution_alias", int(consumer), None))

    retained_ids = {int(row[0]) for row in db.execute("select id from main.actions")}
    retired = []
    mapped_by_owner = {}
    for key, item in identities.items():
        kind, physical = key
        owner_key = (kind, item["canonical_run_id"], item["target_child_id"])
        if owner_key not in current:
            retired.append(item)
            continue
        if owner_key in mapped_by_owner:
            raise ValueError("ambiguous adoption identity-map owner")
        mapped_by_owner[owner_key] = item

    plans = []
    for kind, rows in (
        ("review_candidates", [(int(row[0]), None) for row in db.execute("select consumer_run_id from main.run_adopted_results")]),
        ("candidate_reviews", [(int(row[0]), int(row[1])) for row in db.execute("select id,audit_run_id from main.run_adopted_reviews")]),
        ("candidate_selections", [(int(row[0]), int(row[1])) for row in db.execute("select id,consumer_run_id from main.run_adopted_selections")]),
        ("candidate_supplements", [(int(row[0]), int(row[1])) for row in db.execute("select id,consumer_run_id from main.run_adopted_supplements")]),
        ("candidate_execution_alias", [(int(row[0]), None) for row in db.execute("select consumer_run_id from main.run_execution_progress")]),
    ):
        for child, run in rows:
            if kind == "review_candidates":
                consumer = child
                source_run, run_fact = _source_run(consumer, canonical_to_source, physical_runs)
                if run_fact["role"] != "consumer":
                    raise ValueError("adopted Candidate owner is not a Consumer Run")
                owner_key = (kind, consumer, None)
                public_id = None
                public_type = None
            elif kind == "candidate_reviews":
                review_id, audit_canonical = child, run
                source_audit, audit_fact = _source_run(audit_canonical, canonical_to_source, physical_runs)
                native = db.execute("select consumer_run_id,audit_run_id from main.run_adopted_reviews where id=?", (review_id,)).fetchone()
                if native is None or int(native[1]) != audit_canonical or audit_fact["role"] != "audit":
                    raise ValueError("adopted Review owner does not match its Audit Run")
                source_consumer, consumer_fact = _source_run(int(native[0]), canonical_to_source, physical_runs)
                if consumer_fact["role"] != "consumer" or int(consumer_fact["task_id"]) != int(audit_fact["task_id"]) or consumer_fact["execution_generation"] != audit_fact["execution_generation"] or int(consumer_fact["proposal_revision"]) != int(audit_fact["proposal_revision"]):
                    raise ValueError("Review Consumer/Audit Run ownership differs")
                owner_key = (kind, audit_canonical, review_id)
                public_id, public_type = str(review_id), "text"
            elif kind == "candidate_selections":
                child_id, consumer = child, run
                source_consumer, consumer_fact = _source_run(consumer, canonical_to_source, physical_runs)
                if consumer_fact["role"] != "consumer":
                    raise ValueError("adopted Selection owner is not a Consumer Run")
                owner_key = (kind, consumer, child_id)
                public_id, public_type = str(child_id), "text"
            elif kind == "candidate_supplements":
                child_id, consumer = child, run
                source_consumer, consumer_fact = _source_run(consumer, canonical_to_source, physical_runs)
                if consumer_fact["role"] != "consumer":
                    raise ValueError("adopted Supplement owner is not a Consumer Run")
                owner_key = (kind, consumer, child_id)
                public_id, public_type = str(child_id), "text"
            else:
                consumer = child
                source_consumer, consumer_fact = _source_run(consumer, canonical_to_source, physical_runs)
                if consumer_fact["role"] != "consumer":
                    raise ValueError("adopted execution alias owner is not a Consumer Run")
                owner_key = (kind, consumer, None)
                public_id = None
                public_type = None
            old = mapped_by_owner.get(owner_key)
            plans.append({"source_kind": kind, "native_owner": [child, run],
                          "canonical_run_id": consumer if kind != "candidate_reviews" else run,
                          "source_run_id": source_audit if kind == "candidate_reviews" else source_consumer if kind != "review_candidates" else source_run,
                          "mapped_physical_id": old["physical_id"] if old else None,
                          "physical_id": old["physical_id"] if old else None,
                          "public_id": old["public_id"] if old else public_id,
                          "public_id_type": old["public_id_type"] if old else public_type,
                          "new": old is None, "reassigned": False})

    # Public rowid namespaces are per source kind. Reviews, selections and
    # supplements retain their real native child IDs; candidate/progress
    # aliases use the source table's ordinary SQLite rowid allocator.
    active = [item for item in identities.values()
              if (item["source_kind"], item["canonical_run_id"], item["target_child_id"]) in mapped_by_owner]
    for kind in ("review_candidates", "candidate_execution_alias"):
        new_public = [item for item in plans if item["new"] and item["source_kind"] == kind]
        occupied_public = {_cast_public_integer(item["public_id"], item["public_id_type"])
                           for item in active if item["source_kind"] == kind}
        for item, public_id in zip(new_public,
                                   _next_plain_rowids(occupied_public, len(new_public)), strict=True):
            item["public_id"] = str(public_id)
            item["public_id_type"] = "text"
    public_owners = set()
    for item in plans:
        public_key = (item["source_kind"],
                      _cast_public_integer(item["public_id"], item["public_id_type"]))
        if public_key in public_owners:
            raise ValueError("duplicate typed adoption public identity")
        public_owners.add(public_key)
    for item in plans:
        if item["physical_id"] in retained_ids:
            item["physical_id"] = None
            item["reassigned"] = True
    allocations = [item for item in plans if item["physical_id"] is None]
    reserved_ids = retained_ids | {int(item["physical_id"]) for item in plans
                                    if item["physical_id"] is not None}
    assigned = _next_plain_rowids(reserved_ids, len(allocations))
    for item, physical in zip(allocations, assigned, strict=True):
        item["physical_id"] = physical
    captured_sequences, current_sequences = _action_allocator_facts(db, source_metadata)
    return {"version": 1, "adoptions": plans,
            "source_run_physical_by_canonical": canonical_to_source,
            "source_sequences": [list(row) for row in captured_sequences],
            "current_sequences": [list(row) for row in current_sequences],
            "planned_sequences": [list(row) for row in captured_sequences],
            "retired_identity_count": len(retired),
            "allocator_reserved_ids": sorted(reserved_ids),
            "reassigned_identity_count": sum(item["reassigned"] for item in plans)}


def _table_stored_columns(sql, table="actions"):
    scratch = sqlite3.connect(":memory:")
    try:
        scratch.execute(sql)
        return tuple(row[1] for row in scratch.execute(f'pragma table_xinfo("{table}")')
                     if row[6] == 0)
    finally:
        scratch.close()


def _task_inverse_owners(task_inverse_metadata):
    if type(task_inverse_metadata) is not dict:
        raise ValueError("invalid Task inverse identity handoff")
    from app import task_entity_schema
    identity = task_inverse_metadata.get("inverse_identity")
    mapped = task_entity_schema._validate_inverse_identity(identity)
    context = task_inverse_metadata.get("source_context")
    if (type(context) is not dict or set(context) != {
            "version", "handoff_required", "handoff_sha256"}
            or context["version"] != 1
            or context["handoff_required"] != bool(identity["owners"])
            or context["handoff_sha256"] != task_entity_schema._inverse_identity_digest(identity)):
        raise ValueError("Task inverse identity handoff digest mismatch")
    return mapped


def _task_source_physical(db, inverse_owners, source_kind, canonical_id):
    """Resolve a canonical Task ID only through its typed inverse owner."""
    if type(canonical_id) is not int or canonical_id <= 0:
        raise ValueError(f"invalid positive Task reference: {canonical_id!r}")
    mapped = {target: source for (kind, source), target in inverse_owners.items()
              if kind == source_kind}
    physical = mapped.get(canonical_id, canonical_id)
    row = db.execute(
        "select 1 from main.tasks where id=? and record_type=?",
        (physical, source_kind)).fetchone()
    if row is None:
        raise ValueError(f"missing typed source Task owner: {source_kind}:{physical}")
    return physical


def _feedback_source_public(db, inverse_owners, kind, canonical_id):
    physical = _task_source_physical(db, inverse_owners, kind, canonical_id)
    column = kind + "__id"
    columns = {row[1] for row in db.execute("pragma main.table_xinfo(tasks)")}
    if column not in columns:
        raise ValueError(f"source Task public identity column is absent: {column}")
    row = db.execute(f'select "{column}" from main.tasks where id=?', (physical,)).fetchone()
    if row is None or row[0] is None:
        raise ValueError(f"missing source Task public identity: {kind}:{physical}")
    return row[0]


def retained_action_source_rows(db: sqlite3.Connection, source_metadata: dict, *,
                                task_inverse_metadata: dict,
                                run_inverse_metadata: dict) -> list[dict]:
    """Translate current retained Actions rows to old-source stored columns.

    This is a pure row translator for the caller-owned FK-off inverse path. It
    reads only current native rows and the actual Task/Run inverse handoffs;
    source-only retired Action columns are reconstructed as NULL.
    """
    if not db.in_transaction or db.execute("pragma foreign_keys").fetchone()[0]:
        raise ValueError("retained Action translation requires caller-owned FK-off transaction")
    if type(source_metadata) is not dict or type(source_metadata.get("action_catalog")) is not list:
        raise ValueError("retained Action translation requires source Action catalog")
    from app.current_action_schema import current_action_objects
    from app.task_run_entity_assembly import _action_objects
    source_objects = current_action_objects()
    source_catalog = sorted((item["type"], item["name"], item["sql"])
                            for item in source_objects)
    if source_catalog != sorted(tuple(item) for item in source_metadata["action_catalog"]):
        raise ValueError("unsupported source Action catalog")
    target_objects = _action_objects()
    scratch = sqlite3.connect(":memory:")
    try:
        for name, sql in target_objects.items():
            if sql.lstrip().lower().startswith("create table"):
                scratch.execute(sql)
        for name, sql in target_objects.items():
            if not sql.lstrip().lower().startswith("create table"):
                scratch.execute(sql)
        target_catalog = sorted(tuple(row) for row in scratch.execute(
            "select type,name,sql from sqlite_schema where tbl_name='actions' "
            "and type in ('table','index','trigger') and name not like 'sqlite_%'"))
    finally:
        scratch.close()
    actual_target_catalog = sorted(tuple(row) for row in db.execute(
        "select type,name,sql from main.sqlite_schema where tbl_name='actions' "
        "and type in ('table','index','trigger') and name not like 'sqlite_%'"))
    if actual_target_catalog != target_catalog:
        raise ValueError("unsupported current native Action catalog")
    source_sql = next(sql for name, sql in target_objects.items()
                      if name == "actions")
    source_actions_sql = next(item["sql"] for item in source_objects if item["name"] == "actions")
    source_columns = _table_stored_columns(source_actions_sql)
    target_columns = _table_stored_columns(source_sql)
    retired_columns = set(source_columns) - set(target_columns)
    run_map, physical_runs = _run_sources(db, run_inverse_metadata)
    task_owners = _task_inverse_owners(task_inverse_metadata)
    result = []
    cursor = db.execute('select * from main.actions order by id')
    native_columns = tuple(item[0] for item in cursor.description)
    for values in cursor.fetchall():
        native = dict(zip(native_columns, values, strict=True))
        row = {column: native[column] if column in native and column not in retired_columns
               else None for column in source_columns}
        for field in ("agent_run_id",):
            if field in native and field in row:
                value = native[field]
                if value is None:
                    row[field] = None
                elif type(value) is not int:
                    raise ValueError(f"invalid stored Run reference type: {field}")
                else:
                    row[field] = _inverse_weak_run_ref(value, run_map, physical_runs)
        for field, kind in (("td_reply_task_id", "reply_tasks"),
                            ("td_meeting_job_id", "meeting_alignment_jobs"),
                            ("td_claim_job_id", "meeting_alignment_jobs")):
            if field in native and field in row and native[field] is not None:
                if type(native[field]) is not int:
                    raise ValueError(f"invalid stored Task reference type: {field}")
                row[field] = _task_source_physical(
                    db, task_owners, kind, native[field])
        for field, kind in (("fb_decision_id", "feedback_iteration_decisions"),
                            ("fb_round_id", "feedback_processing_rounds")):
            if field in native and field in row and native[field] is not None:
                if type(native[field]) is not int:
                    raise ValueError(f"invalid stored Feedback reference type: {field}")
                row[field] = _feedback_source_public(
                    db, task_owners, kind, native[field])
        if "action_data" in native and "action_data" in row and native["action_data"] is not None:
            data = native["action_data"]
            value, json_type = db.execute(
                "select json_extract(?, '$.agent_run_id'), json_type(?, '$.agent_run_id')",
                (data, data)).fetchone()
            if json_type not in (None, "null", "integer"):
                raise ValueError("Action JSON agent_run_id is not an integer reference")
            if json_type == "integer" and value != 0:
                value = _source_run(int(value), run_map, physical_runs)[0]
                data = db.execute("select jsonb_set(?, '$.agent_run_id', jsonb(?))",
                                  (data, str(value))).fetchone()[0]
            row["action_data"] = data
        result.append(row)
    return result


def adopted_action_source_rows(db: sqlite3.Connection, source_metadata: dict,
                               forward_metadata: dict, *, run_inverse_metadata: dict,
                               identity_plan: dict | None = None) -> list[dict]:
    """Build source-shaped Action rows from current adopted native facts.

    This is an in-memory half of the unactivated inverse. It reads current
    native adoption/progress rows and uses the shared identity planner; it does
    not write Actions, sequence state, receipts or observers.
    """
    if not db.in_transaction or db.execute("pragma foreign_keys").fetchone()[0]:
        raise ValueError("adopted Action row building requires caller-owned FK-off transaction")
    from app import action_storage
    from app.current_action_schema import current_action_payloads
    from app.execution_owner_definitions import EXECUTION_FIELDS
    from app.run_adoption_definitions import validate_field_map

    validate_field_map()
    plan = identity_plan or plan_run_adoption_inverse(
        db, source_metadata, forward_metadata,
        run_inverse_metadata=run_inverse_metadata)
    if type(plan) is not dict or plan.get("version") != 1 or type(plan.get("adoptions")) is not list:
        raise ValueError("invalid adoption inverse identity plan")
    payloads = current_action_payloads()
    layouts = payloads.layouts
    source_sql = next(item[2] for item in source_metadata["action_catalog"]
                      if item[0] == "table" and item[1] == "actions")
    source_columns = _table_stored_columns(source_sql)
    run_map, physical_runs = _run_sources(db, run_inverse_metadata)
    plan_by_owner = {}
    for item in plan["adoptions"]:
        key = (item["source_kind"], tuple(item["native_owner"]))
        if key in plan_by_owner:
            raise ValueError("duplicate adopted Action plan owner")
        plan_by_owner[key] = item

    candidate_physical = {}
    review_physical = {}
    selection_physical = {}
    selection_physical_by_id = {}
    # Resolve predecessor and execution references only after every identity
    # has been planned, so forward references never depend on row order.
    adopted_reviews = db.execute(
        "select id,consumer_run_id,audit_run_id,candidate_digest,decision,result_json,created_at from main.run_adopted_reviews order by id").fetchall()
    for row in adopted_reviews:
        native_id, consumer, audit, *_ = row
        item = plan_by_owner[("candidate_reviews", (int(native_id), int(audit)))]
        review_physical[(int(consumer), int(audit))] = int(item["physical_id"])
    adopted_selections = db.execute(
        "select id,consumer_run_id,audit_run_id,option_key,status,created_at from main.run_adopted_selections order by id").fetchall()
    selection_owner_by_id = {}
    for row in adopted_selections:
        native_id, consumer, audit, *_ = row
        item = plan_by_owner[("candidate_selections", (int(native_id), int(consumer)))]
        selection_physical[(int(consumer), int(audit))] = int(item["physical_id"])
        selection_physical_by_id[int(native_id)] = int(item["physical_id"])
        selection_owner_by_id[int(native_id)] = (int(consumer), int(audit))
    candidate_physical = {
        int(item["canonical_run_id"]): int(item["physical_id"])
        for item in plan["adoptions"] if item["source_kind"] == "review_candidates"
    }

    result = []

    def make_row(kind, plan_item, payload_values, refs):
        layout = layouts[kind]
        packed = layout.pack(payload_values, scalar_codec=action_storage._encoded_native_sqlite_value)
        payload = db.execute("select jsonb(?)", (packed,)).fetchone()[0]
        row = dict.fromkeys(source_columns)
        row.update({"id": int(plan_item["physical_id"]), "_kind": payloads.record_types.code(kind),
                    "legacy_key": plan_item["public_id"], "payload_json": payload})
        for name, value in refs.items():
            if name in row:
                row[name] = value
        return row

    progress_columns = ("consumer_run_id", "approved_audit_run_id", "selection_id",
                        "status", "lease_owner", "lease_expires_at", "result_json",
                        "created_at", "updated_at")
    progress_rows = {int(row[0]): dict(zip(progress_columns, row, strict=True))
        for row in db.execute("select consumer_run_id,approved_audit_run_id,selection_id,status,lease_owner,lease_expires_at,result_json,created_at,updated_at from main.run_execution_progress")}

    for row in db.execute("select consumer_run_id,stage_index,predecessor_review_id,candidate_digest,candidate_json,invalidated_at,invalidation_reason,created_at from main.run_adopted_results"):
        consumer, stage, predecessor, digest, body, invalidated, reason, created = row
        consumer = int(consumer)
        item = plan_by_owner[("review_candidates", (consumer, None))]
        _, run = _source_run(consumer, run_map, physical_runs)
        values = {"execution_generation": run["execution_generation"], "stage_index": stage,
                  "proposal_revision": run["proposal_revision"], "candidate_digest": digest,
                  "candidate_json": body, "invalidated_at": invalidated,
                  "invalidation_reason": reason, "created_at": created}
        refs = {"task_id": run["task_id"], "consumer_run_id": item["source_run_id"],
                "predecessor_review_id": None}
        if predecessor is not None:
            pred = db.execute("select consumer_run_id,audit_run_id from main.run_adopted_reviews where id=?", (predecessor,)).fetchone()
            if pred is None or (int(pred[0]), int(pred[1])) not in review_physical:
                raise ValueError("unresolved adopted predecessor Review reference")
            refs["predecessor_review_id"] = review_physical[(int(pred[0]), int(pred[1]))]
        progress = progress_rows.get(consumer)
        if progress is not None:
            approved = int(progress["approved_audit_run_id"])
            if (consumer, approved) not in review_physical:
                raise ValueError("unresolved adopted execution Review reference")
            selection_ref = None
            if progress["selection_id"] is not None:
                selection_id = int(progress["selection_id"])
                if selection_owner_by_id.get(selection_id) != (consumer, approved):
                    raise ValueError("adopted execution Selection does not match its Consumer and approved Review")
                selection_ref = selection_physical_by_id.get(selection_id)
                if selection_ref is None:
                    raise ValueError("unresolved adopted execution Selection reference")
            alias = plan_by_owner[("candidate_execution_alias", (consumer, None))]
            values.update({"execution_public_id": _cast_public_integer(
                alias["public_id"], alias["public_id_type"]),
                "execution_status": progress["status"], "execution_lease_owner": progress["lease_owner"],
                "execution_lease_expires_at": progress["lease_expires_at"],
                "execution_result_json": progress["result_json"],
                "execution_created_at": progress["created_at"],
                "execution_updated_at": progress["updated_at"]})
            refs["execution_review_id"] = review_physical[(consumer, approved)]
            refs["execution_selection_id"] = selection_ref
        else:
            values.update(dict.fromkeys(EXECUTION_FIELDS, None))
            refs.update({"execution_review_id": None, "execution_selection_id": None})
        result.append(make_row("review_candidates", item, values, refs))

    for row in adopted_reviews:
        native_id, consumer, audit, digest, decision, body, created = row
        item = plan_by_owner[("candidate_reviews", (int(native_id), int(audit)))]
        if int(consumer) not in candidate_physical:
            raise ValueError("unresolved adopted Review Candidate reference")
        result.append(make_row("candidate_reviews", item,
            {"decision": decision, "candidate_digest": digest, "result_json": body, "created_at": created},
            {"candidate_id": candidate_physical[int(consumer)], "audit_run_id": item["source_run_id"]}))
    for row in adopted_selections:
        native_id, consumer, audit, option, status, created = row
        item = plan_by_owner[("candidate_selections", (int(native_id), int(consumer)))]
        review_id = review_physical.get((int(consumer), int(audit)))
        if review_id is None:
            raise ValueError("unresolved adopted Selection Review reference")
        result.append(make_row("candidate_selections", item,
            {"option_key": option, "status": status, "created_at": created},
            {"candidate_id": candidate_physical[int(consumer)], "review_id": review_id}))
    for row in db.execute("select id,consumer_run_id,instruction,created_at from main.run_adopted_supplements order by id"):
        native_id, consumer, instruction, created = row
        item = plan_by_owner[("candidate_supplements", (int(native_id), int(consumer)))]
        result.append(make_row("candidate_supplements", item,
            {"instruction": instruction, "created_at": created},
            {"candidate_id": candidate_physical[int(consumer)]}))
    for consumer in progress_rows:
        item = plan_by_owner[("candidate_execution_alias", (consumer, None))]
        result.append(make_row("candidate_execution_alias", item, {},
            {"candidate_id": candidate_physical[consumer]}))
    if any(set(row) != set(source_columns) for row in result):
        raise ValueError("adopted Action row differs from source stored columns")
    return result


def restore_run_adoptions(db: sqlite3.Connection, source_metadata: dict,
                          forward_metadata: dict, *, task_metadata: dict,
                          task_inverse_metadata: dict,
                          run_inverse_metadata: dict) -> dict:
    """Rebuild source Actions and its current receipt/observer catalogs.

    The caller must have run Task inverse then Run inverse in the same
    FK-off transaction. This routine owns no commit and keeps only facts
    represented by current native rows.
    """
    if not db.in_transaction or db.execute("pragma foreign_keys").fetchone()[0]:
        raise ValueError("adoption inverse materialization requires caller-owned FK-off transaction")
    plan = plan_run_adoption_inverse(db, source_metadata, forward_metadata,
                                     run_inverse_metadata=run_inverse_metadata)
    retained = retained_action_source_rows(
        db, source_metadata, task_inverse_metadata=task_inverse_metadata,
        run_inverse_metadata=run_inverse_metadata)
    adopted = adopted_action_source_rows(
        db, source_metadata, forward_metadata,
        run_inverse_metadata=run_inverse_metadata, identity_plan=plan)
    action_cursor = db.execute("select * from main.actions order by id")
    current_action_columns = tuple(item[0] for item in action_cursor.description)
    native_actions = [dict(zip(current_action_columns, row, strict=True))
                      for row in action_cursor.fetchall()]
    physical_map = {int(row["id"]): int(row["id"]) for row in native_actions
                    if row["record_type"] not in {"review_candidates", "candidate_reviews",
                    "candidate_selections", "candidate_supplements", "candidate_execution_alias"}}

    # Current receipts carry canonical Run references; source execution_id is
    # the source public alias. Preserve every other stored SQLite value.
    receipt_catalog = source_metadata["receipt_catalog"]
    source_receipt_sql = next(row[2] for row in receipt_catalog
                              if row[0] == "table" and row[1] == "action_receipts")
    receipt_columns = _table_stored_columns(source_receipt_sql, "action_receipts")
    native_receipt_columns = tuple(row[1] for row in db.execute(
        "pragma main.table_info(action_receipts)"))
    run_map, physical_runs = _run_sources(db, run_inverse_metadata)
    alias_by_run = {}
    for item in plan["adoptions"]:
        if item["source_kind"] == "candidate_execution_alias":
            alias_by_run[int(item["canonical_run_id"])] = _cast_public_integer(
                item["public_id"], item["public_id_type"])
    receipts = []
    provider_lineage = []
    for values in db.execute("select * from main.action_receipts order by id"):
        native = dict(zip(native_receipt_columns, values, strict=True))
        row = {column: native.get(column) for column in receipt_columns}
        row["action_id"] = physical_map[int(native["action_id"])]
        execution_run = native.get("execution_run_id")
        if execution_run is None:
            row["execution_id"] = None
        else:
            try:
                row["execution_id"] = alias_by_run[int(execution_run)]
            except KeyError as exc:
                raise ValueError("receipt execution Run has no current source alias") from exc
        row["agent_run_id"] = _inverse_weak_run_ref(
            native.get("agent_run_id"), run_map, physical_runs)
        if row.get("provider_receipt_id") is not None:
            provider_lineage.append((int(row["id"]), row["provider_receipt_id"]))
        row["provider_receipt_id"] = None
        receipts.append(row)
    observer_columns = tuple(row[1] for row in db.execute(
        "pragma main.table_info(action_observers)"))
    observers = []
    task_owners = _task_inverse_owners(task_inverse_metadata)
    for values in db.execute("select * from main.action_observers order by action_id,agent_run_id"):
        row = dict(zip(observer_columns, values, strict=True))
        row["action_id"] = physical_map[int(row["action_id"])]
        row["agent_run_id"] = _inverse_weak_run_ref(
            row["agent_run_id"], run_map, physical_runs)
        if row["task_id"] != 0:
            row["task_id"] = _task_source_physical(
                db, task_owners, "reply_tasks", row["task_id"])
        observers.append({column: row.get(column) for column in _table_stored_columns(
            next(item[2] for item in source_metadata["observer_catalog"]
                 if item[0] == "table" and item[1] == "action_observers"), "action_observers")})
    identity_sql = next(row[2] for row in source_metadata["identity_catalog"]
                        if row[0] == "table" and row[1] == "action_entity_identity_map")
    identity_columns = _table_stored_columns(identity_sql, "action_entity_identity_map")
    current_identity_columns = tuple(row[1] for row in db.execute(
        "pragma main.table_info(action_entity_identity_map)"))
    identity_rows = []
    for values in db.execute("select * from main.action_entity_identity_map"):
        row = dict(zip(current_identity_columns, values, strict=True))
        if row.get("action_id") is not None:
            row["action_id"] = physical_map[int(row["action_id"])]
        identity_rows.append({column: row.get(column) for column in identity_columns})

    all_catalog = ([("actions", source_metadata["action_catalog"]),
                    ("action_receipts", source_metadata["receipt_catalog"]),
                    ("action_observers", source_metadata["observer_catalog"]),
                    ("action_entity_identity_map", source_metadata["identity_catalog"])])
    current_sequences = [tuple(row) for row in db.execute(
        "select rowid,name,seq from main.sqlite_sequence")]
    for table, _ in all_catalog:
        db.execute(f'drop table main."{table}"')
    for table, catalog in all_catalog:
        for kind in ("table",):
            for object_type, name, sql in catalog:
                if object_type == kind:
                    db.execute(sql)
    def insert_rows(table, rows):
        for row in rows:
            columns = tuple(row)
            db.execute(f'insert into main."{table}" '
                       f'({",".join(chr(34)+c+chr(34) for c in columns)}) '
                       f'values ({",".join("?" for _ in columns)})',
                       tuple(row[column] for column in columns))
    insert_rows("actions", sorted(retained + adopted, key=lambda row: row["id"]))
    insert_rows("action_receipts", receipts)
    insert_rows("action_observers", observers)
    insert_rows("action_entity_identity_map", identity_rows)
    for table, catalog in all_catalog:
        for object_type, _name, sql in catalog:
            if object_type != "table":
                db.execute(sql)
    for receipt_id, provider_id in provider_lineage:
        db.execute("update main.action_receipts set provider_receipt_id=? where id=?",
                   (provider_id, receipt_id))
    for table in ("run_execution_progress", "run_adopted_selections",
                  "run_adopted_supplements", "run_adopted_reviews",
                  "run_adopted_results", "run_adoption_identity_map"):
        db.execute(f'drop table main."{table}"')
    # Preserve current sequence rows, since source Actions and receipts use
    # ordinary integer primary keys and must not acquire fabricated waters.
    db.execute("delete from main.sqlite_sequence where name in "
               "('actions','action_receipts','action_observers','action_entity_identity_map')")
    for rowid, name, seq in current_sequences:
        if name not in {"actions", "action_receipts", "action_observers", "action_entity_identity_map"}:
            continue
        db.execute("insert into main.sqlite_sequence(rowid,name,seq) values(?,?,?)",
                   (rowid, name, seq))
    return {"version": 1, "action_count": len(retained) + len(adopted),
            "receipt_count": len(receipts), "observer_count": len(observers),
            "retained_count": len(retained), "adopted_count": len(adopted),
            "reassigned_identity_count": plan["reassigned_identity_count"]}
