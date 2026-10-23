"""Task3C2A current Action adoption and receipt forward transfer.

Capture is performed against the selected current source inside the caller's
FK-off transaction. The converter runs after Task2B and Run3B and is deliberately
not wired into a constructor or runtime path.
"""
from __future__ import annotations

import sqlite3

from app import run_adoption_definitions as definitions
from app import task_run_entity_assembly

_CAPTURE = "_run_adoption_source_receipts"
_OBSERVER_CAPTURE = "_run_adoption_source_observers"
_ACTION_CAPTURE = "_run_adoption_source_actions"
_ALL_ACTION_CAPTURE = "_run_adoption_all_source_actions"
_SOURCE_KINDS = {
    "review_candidates", "candidate_reviews", "candidate_selections",
    "candidate_supplements", "candidate_execution_alias",
}
_SOURCE_TYPES = {int: "integer", str: "text", bytes: "blob"}


def _require_transaction(db):
    if not db.in_transaction or db.execute("pragma foreign_keys").fetchone()[0]:
        raise ValueError("Run adoption conversion requires caller-owned FK-off transaction")


def _catalog(db, table):
    return [tuple(row) for row in db.execute(
        "select type,name,sql from main.sqlite_schema where tbl_name=? "
        "and type in ('table','index','trigger') and name not like 'sqlite_%' order by type,name",
        (table,))]


def _sequence_rows(db):
    names = ("actions", "action_receipts", "action_observers", "action_entity_identity_map")
    return [tuple(row) for row in db.execute(
        "select rowid,name,seq from main.sqlite_sequence where name in (?,?,?,?) order by rowid", names)]


def capture_run_adoptions(db: sqlite3.Connection) -> dict:
    """Capture current declarations/allocators and transaction-local receipt rows."""
    _require_transaction(db)
    if any(db.execute("select 1 from temp.sqlite_schema where name=?", (name,)).fetchone()
           for name in (_CAPTURE, _OBSERVER_CAPTURE, _ACTION_CAPTURE, _ALL_ACTION_CAPTURE)):
        raise ValueError("adoption capture already exists")
    action_catalog = _catalog(db, "actions")
    from app.current_action_schema import current_action_objects, _action_entity_objects
    expected_actions = sorted((row["type"], row["name"], row["sql"])
                              for row in current_action_objects())
    if action_catalog != expected_actions:
        raise ValueError("unsupported current Action declaration")
    receipts = _catalog(db, "action_receipts")
    observers = _catalog(db, "action_observers")
    identity = _catalog(db, "action_entity_identity_map")
    expected_children = sorted((row["type"], row["name"], row["sql"])
                               for row in _action_entity_objects())
    if sorted(receipts + observers + identity) != expected_children:
        raise ValueError("unsupported current Action child declaration")
    receipt_columns = tuple(row[1] for row in db.execute("pragma main.table_info(action_receipts)"))
    if "execution_id" not in receipt_columns:
        raise ValueError("current Action receipt execution owner is not selected source")
    db.execute(f'create temp table "{_CAPTURE}" as select * from main.action_receipts')
    db.execute(f'create temp table "{_OBSERVER_CAPTURE}" as select * from main.action_observers')
    db.execute(f'create temp table "{_ACTION_CAPTURE}" as select * from main.actions where record_type in '
        "('review_candidates','candidate_reviews','candidate_selections','candidate_supplements',"
        "'candidate_execution_alias')")
    db.execute(f'create temp table "{_ALL_ACTION_CAPTURE}" as select * from main.actions')
    selected = tuple((kind, count) for kind, count in db.execute(
        "select record_type,count(*) from main.actions where record_type in "
        "('review_candidates','candidate_reviews','candidate_selections','candidate_supplements',"
        "'candidate_execution_alias') group by record_type order by record_type"))
    source_sequences = _sequence_rows(db)
    original_fk = tuple(tuple(row) for row in db.execute("pragma main.foreign_key_check(actions)"))
    return {"version": 1, "action_catalog": [list(row) for row in action_catalog],
            "receipt_catalog": [list(row) for row in receipts],
            "observer_catalog": [list(row) for row in observers],
            "identity_catalog": [list(row) for row in identity],
            "receipt_columns": list(receipt_columns),
            "source_sequences": [list(row) for row in source_sequences],
            "selected_counts": [list(row) for row in selected],
            "source_fk_findings": [list(row) for row in original_fk]}


def _identity_maps(db, task_metadata, run_metadata):
    # Verify we were given the actual converter outputs, then use their native
    # identity maps (including sparse non-identity remaps) as the only resolver.
    from app.run_entity_schema import _run_identity_rows
    from app.run_entity_schema import _task_forward_edges
    tasks = _task_forward_edges(db, task_metadata)
    runs = _run_identity_rows(db)
    expected_edges = {(row["source_kind"], row["physical_id"]): row["target_id"]
                      for row in runs.values()}
    actual_edges = {(row["source_kind"], row["physical_id"]): row["target_id"]
                    for row in run_metadata.get("identity_edges", [])}
    if actual_edges != expected_edges:
        raise ValueError("Run3B metadata differs from its native Run identity map")
    physical_runs = {physical: target for (kind, physical), target in expected_edges.items()
                     if kind == "agent_runs"}
    from app.task_entity_schema import _inverse_indexes
    _, _, task_indexes = _inverse_indexes(db, task_metadata)
    return tasks, physical_runs, task_indexes["public_index"]


def _task_physical_ref(db, task_metadata, task_map, source_kind, physical_id):
    if type(physical_id) is not int or not any(
            start <= physical_id <= end
            for start, end in task_metadata["source_id_ranges"][source_kind]):
        raise ValueError(f"unresolved typed source Task owner: {source_kind}:{physical_id!r}")
    target_id = task_map.get((source_kind, physical_id), physical_id)
    from app.run_entity_schema import _task_kind
    row = db.execute("select kind from main.tasks where id=?", (target_id,)).fetchone()
    if row is None or row[0] != _task_kind(source_kind):
        raise ValueError(f"typed source Task owner kind mismatch: {source_kind}:{physical_id!r}")
    return target_id


def _run_id(maps, source_id, *, role=None):
    if source_id is None:
        return None
    if source_id not in maps:
        raise ValueError(f"unresolved or ambiguous source Run reference: {source_id!r}")
    return maps[source_id]


def _translate_weak_run_ref(run_map, value):
    """Translate real physical Run references, preserving source null/zero sentinels."""
    if value is None or value == 0:
        return value
    return _run_id(run_map, value)


def _selection_branch(candidate_json, option_key):
    from app.model_action_schema import candidate_option
    return candidate_option(candidate_json, option_key)


def _feedback_public_ref(db, task_public_index, source_kind, target_table, value):
    if value is None:
        return None
    from app.task_entity_migration import _typed_key
    target_id = task_public_index.get((source_kind, _typed_key(value)))
    if target_id is not None and db.execute(
            f'select 1 from main."{target_table}" where id=?', (target_id,)).fetchone():
        return target_id
    raise ValueError(f"unresolved {source_kind} public identity")


def _insert(db, table, values):
    columns = tuple(values)
    db.execute(f'insert into main."{table}" ({",".join(chr(34)+c+chr(34) for c in columns)}) '
               f'values ({",".join("?" for _ in columns)})', tuple(values[c] for c in columns))


def _restore_sequence_rows(db, source_sequences, names):
    for name in names:
        db.execute("delete from main.sqlite_sequence where name=?", (name,))
        source = next((row for row in source_sequences if row[1] == name), None)
        if source is not None:
            db.execute("insert into main.sqlite_sequence(rowid,name,seq) values(?,?,?)", source)


def _rebuild_actions(db, task_metadata, task_public_index, task_map, run_map, source_sequences):
    """Rebuild the actual target Action table and omit retired source kinds."""
    objects = task_run_entity_assembly._action_objects()
    target_sql = objects["actions"]
    source_columns = tuple(row[1] for row in db.execute(
        f'pragma temp.table_info("{_ALL_ACTION_CAPTURE}")'))
    scratch = sqlite3.connect(":memory:")
    scratch.execute(target_sql)
    target_columns = tuple(row[1] for row in scratch.execute("pragma table_info(actions)"))
    scratch.close()
    # Save retained rows in TEMP before replacing the source table. Generated
    # columns are excluded by pragma_table_xinfo(hidden=0).
    retained = [dict(zip(source_columns, row, strict=True)) for row in db.execute(
        f'select * from temp."{_ALL_ACTION_CAPTURE}" where record_type not in '
        "('review_candidates','candidate_reviews','candidate_selections','candidate_supplements',"
        "'candidate_executions','candidate_execution_alias','candidate_action_attempts') order by id")]
    stored_source_columns = set(row[1] for row in db.execute(
        "pragma main.table_xinfo(actions)") if row[6] == 0)
    stored_target_columns = set(target_columns)
    retired_columns = stored_source_columns - stored_target_columns
    populated_retired = sorted(
        column for column in retired_columns
        if any(row[column] is not None for row in retained))
    if populated_retired:
        raise ValueError("retained source Action columns have no assembled target representation: "
                         + ",".join(populated_retired))
    db.execute("drop table main.actions")
    db.execute(target_sql)
    columns = [name for name in target_columns if name in source_columns]
    for row in retained:
        values = {name: row[name] for name in columns}
        for field in ("agent_run_id", "audit_run_id", "consumer_run_id"):
            if field in values:
                values[field] = _translate_weak_run_ref(run_map, values[field])
        for field, kind, table in (
            ("fb_decision_id", "feedback_iteration_decisions", "feedback_iteration_decisions"),
            ("fb_round_id", "feedback_processing_rounds", "feedback_processing_rounds"),
        ):
            if field in values:
                values[field] = _feedback_public_ref(db, task_public_index, kind, table, values[field])
        for field, source_kind in (
            ("td_reply_task_id", "reply_tasks"),
            ("td_meeting_job_id", "meeting_alignment_jobs"),
            ("td_claim_job_id", "meeting_alignment_jobs"),
        ):
            if field in values and values[field] is not None:
                values[field] = _task_physical_ref(db, task_metadata, task_map, source_kind, values[field])
        if "action_data" in values and values["action_data"] is not None:
            data = values["action_data"]
            ref, ref_type = db.execute(
                "select json_extract(?, '$.agent_run_id'), "
                "json_type(?, '$.agent_run_id')", (data, data)).fetchone()
            if ref_type not in (None, "null"):
                translated = _translate_weak_run_ref(run_map, ref)
                values["action_data"] = db.execute(
                    "select jsonb_set(?, '$.agent_run_id', ?)",
                    (data, translated)).fetchone()[0]
        _insert(db, "actions", values)
    for name, sql in objects.items():
        if name != "actions" and sql.lstrip().lower().startswith(("create index", "create unique index")):
            db.execute(sql)
    for name, sql in objects.items():
        if name != "actions" and sql.lstrip().lower().startswith("create trigger"):
            db.execute(sql)
    _restore_sequence_rows(db, source_sequences, ("actions",))


def _source_actions(db):
    columns = tuple(row[1] for row in db.execute(f'pragma temp.table_info("{_ACTION_CAPTURE}")'))
    expected = set(definitions.adoption_source_fields())
    rows = {}
    for values in db.execute(f'select * from temp."{_ACTION_CAPTURE}" where record_type in '
            "('review_candidates','candidate_reviews','candidate_selections','candidate_supplements',"
            "'candidate_execution_alias') order by id"):
        row = dict(zip(columns, values, strict=True))
        kind = row["record_type"]
        if kind not in expected:
            raise ValueError(f"unknown adopted Action record type: {kind!r}")
        rows.setdefault(kind, []).append(row)
    return rows


def migrate_run_adoptions(db: sqlite3.Connection, source_metadata: dict, *,
                          task_metadata: dict, run_metadata: dict) -> dict:
    """Move actual source facts to assembled Run owners; caller keeps transaction."""
    _require_transaction(db)
    required = {"version", "action_catalog", "receipt_catalog", "observer_catalog",
                "identity_catalog", "receipt_columns", "source_sequences", "selected_counts",
                "source_fk_findings"}
    if type(source_metadata) is not dict or set(source_metadata) != required or source_metadata["version"] != 1:
        raise ValueError("unsupported adoption source metadata")
    if any(not db.execute("select 1 from temp.sqlite_schema where name=?", (name,)).fetchone()
           for name in (_CAPTURE, _OBSERVER_CAPTURE, _ACTION_CAPTURE, _ALL_ACTION_CAPTURE)):
        raise ValueError("missing pre-conversion Action capture")
    for table, key in (("actions", "action_catalog"), ("action_receipts", "receipt_catalog"),
                       ("action_observers", "observer_catalog"),
                       ("action_entity_identity_map", "identity_catalog")):
        if _catalog(db, table) != [tuple(row) for row in source_metadata[key]]:
            raise ValueError(f"current source {table} catalog changed after capture")
    target_objects = task_run_entity_assembly.task_run_entity_assembly_objects()
    for table in (*definitions.TABLES.keys(), "action_receipts"):
        if table not in target_objects and not db.execute(
                "select 1 from main.sqlite_schema where name=?", (table,)).fetchone():
            raise ValueError(f"assembled target lacks adopted owner {table}")
    task_map, run_map, task_public_index = _identity_maps(db, task_metadata, run_metadata)
    for name in definitions.TABLES:
        if db.execute("select 1 from main.sqlite_schema where name=?", (name,)).fetchone():
            raise ValueError(f"adoption target name already occupied: {name}")
    for name, sql in definitions.TABLES.items():
        db.execute(sql)
    for name, sql in definitions.INDEXES.items():
        db.execute(sql)
    for name, sql in definitions.TRIGGERS.items():
        db.execute(sql)
    # Run3B rebuilds Actions, while receipts remain a source table. Replace
    # that physical table from the assembled target declaration after its
    # transaction-local rows have been captured.
    if db.execute("select 1 from main.sqlite_schema where name='action_receipts'").fetchone():
        db.execute("drop table main.action_receipts")
    receipt_objects = definitions.receipt_schema_objects()
    for name, sql in receipt_objects.items():
        if name == "action_observers" or name == "trg_action_observer_owner_insert":
            continue
        if sql.lstrip().lower().startswith("create table"):
            db.execute(sql)
    for name, sql in receipt_objects.items():
        if name == "action_observers" or name == "trg_action_observer_owner_insert":
            continue
        if not sql.lstrip().lower().startswith("create table"):
            db.execute(sql)
    source = _source_actions(db)
    candidates = {r["id"]: r for r in source.get("review_candidates", [])}
    reviews = {r["id"]: r for r in source.get("candidate_reviews", [])}
    review_owner = {}
    for review in reviews.values():
        candidate = candidates.get(review["candidate_id"])
        if candidate is None:
            raise ValueError("review has unresolved candidate owner")
        consumer = _run_id(run_map, review["audit_run_id"])
        parent = _run_id(run_map, candidate["consumer_run_id"])
        if consumer is None or parent is None or review["candidate_digest"] != candidate["candidate_digest"]:
            raise ValueError("review and candidate ownership/digest mismatch")
        review_owner[review["id"]] = (consumer, parent)
    candidate_by_run = {}
    progress_by_public = {}
    for candidate in candidates.values():
        consumer = _run_id(run_map, candidate["consumer_run_id"])
        run = db.execute("select task_id,execution_generation,proposal_revision,role from main.runs where id=?", (consumer,)).fetchone()
        candidate_task_id = _task_physical_ref(db, task_metadata, task_map,
                                               "reply_tasks", candidate["task_id"])
        if run is None or run[3] != "consumer" or (candidate_task_id, candidate["execution_generation"], candidate["proposal_revision"]) != tuple(run[:3]):
            raise ValueError("candidate facts differ from native Consumer Run")
        candidate_by_run[consumer] = candidate
        if candidate.get("execution_public_id") is not None:
            public = int(candidate["execution_public_id"])
            if public in progress_by_public:
                raise ValueError("duplicate candidate execution public identity")
            progress_by_public[public] = (candidate, consumer)
    # All semantic references are resolved before target writes.
    predecessor = {}
    for candidate in candidates.values():
        old = candidate.get("predecessor_review_id")
        if old is not None:
            if old not in review_owner:
                raise ValueError("unresolved candidate predecessor Review physical identity")
            predecessor[candidate["id"]] = int(reviews[old]["legacy_key"])
    selections = source.get("candidate_selections", [])
    supplements = source.get("candidate_supplements", [])
    aliases = source.get("candidate_execution_alias", [])
    selection_by_physical = {row["id"]: row for row in selections}
    alias_pairs = {(row["candidate_id"], int(row["execution_binding_public_id"])) for row in aliases}
    expected_pairs = {(candidate["id"], public) for public, (candidate, _) in progress_by_public.items()}
    if alias_pairs != expected_pairs or len(alias_pairs) != len(aliases):
        raise ValueError("candidate execution alias/progress pairs differ")
    # Preserve reviews' actual source public protocol ID as native child id.
    for candidate in candidates.values():
        consumer = _run_id(run_map, candidate["consumer_run_id"])
        _insert(db, "run_adopted_results", {
            "consumer_run_id": consumer, "stage_index": candidate["stage_index"],
            # The target trigger requires the Review child to exist first;
            # the caller transaction keeps this intermediate NULL invisible.
            "predecessor_review_id": None,
            "candidate_digest": candidate["candidate_digest"], "candidate_json": candidate["candidate_json"],
            "invalidated_at": candidate["invalidated_at"], "invalidation_reason": candidate["invalidation_reason"],
            "created_at": candidate["created_at"]})
    for review in reviews.values():
        consumer, parent = review_owner[review["id"]]
        public_id = int(review["legacy_key"])
        _insert(db, "run_adopted_reviews", {"id": public_id, "audit_run_id": consumer,
            "consumer_run_id": parent, "candidate_digest": review["candidate_digest"],
            "decision": review["decision"], "result_json": review["result_json"], "created_at": review["created_at"]})
    for candidate in candidates.values():
        if candidate["id"] in predecessor:
            consumer = _run_id(run_map, candidate["consumer_run_id"])
            db.execute("update main.run_adopted_results set predecessor_review_id=? where consumer_run_id=?",
                       (predecessor[candidate["id"]], consumer))
    for selection in selections:
        candidate = candidates.get(selection["candidate_id"])
        review = reviews.get(selection["review_id"])
        if candidate is None or review is None or review["candidate_id"] != candidate["id"]:
            raise ValueError("selection has unresolved candidate/Review binding")
        _selection_branch(candidate["candidate_json"], selection["option_key"])
        consumer = _run_id(run_map, candidate["consumer_run_id"])
        audit_run = _run_id(run_map, review["audit_run_id"])
        _insert(db, "run_adopted_selections", {"id": int(selection["legacy_key"]),
            "consumer_run_id": consumer, "audit_run_id": audit_run,
            "option_key": selection["option_key"], "status": selection["status"],
            "created_at": selection["created_at"]})
    for supplement in supplements:
        candidate = candidates.get(supplement["candidate_id"])
        if candidate is None:
            raise ValueError("supplement has unresolved candidate")
        consumer = _run_id(run_map, candidate["consumer_run_id"])
        child = int(supplement["legacy_key"])
        _insert(db, "run_adopted_supplements", {"id": child, "consumer_run_id": consumer,
            "instruction": supplement["instruction"], "created_at": supplement["created_at"]})
    for public, (candidate, consumer) in progress_by_public.items():
        old_review = candidate["execution_review_id"]
        if old_review not in review_owner:
            raise ValueError("progress has unresolved Review physical identity")
        approved_audit = review_owner[old_review][0]
        selected = candidate["execution_selection_id"]
        selection_id = None
        if selected is not None:
            selection = selection_by_physical.get(selected)
            if selection is None:
                raise ValueError("progress has unresolved selection physical identity")
            review = reviews.get(selection["review_id"])
            if review is None:
                raise ValueError("selected branch has unresolved Review identity")
            selection_id = int(selection["legacy_key"])
        _insert(db, "run_execution_progress", {"consumer_run_id": consumer,
            "approved_audit_run_id": approved_audit, "selection_id": selection_id,
            "status": candidate["execution_status"], "lease_owner": candidate["execution_lease_owner"],
            "lease_expires_at": candidate["execution_lease_expires_at"],
            "result_json": candidate["execution_result_json"], "created_at": candidate["execution_created_at"],
            "updated_at": candidate["execution_updated_at"]})
    # Migrate receipts from the transaction-local original snapshot, resolving
    # execution_id through progress; keep each row/Action/provider lineage intact.
    columns = tuple(source_metadata["receipt_columns"])
    if "execution_id" not in columns:
        raise ValueError("captured receipt columns omit source execution identity")
    rows = list(db.execute(f'select * from temp."{_CAPTURE}"'))
    current_cols = tuple(row[1] for row in db.execute("pragma main.table_info(action_receipts)"))
    if "execution_run_id" not in current_cols:
        raise ValueError("assembled receipt owner does not use execution_run_id")
    receipt_lineage = []
    for values in rows:
        row = dict(zip(columns, values, strict=True))
        provider_receipt_id = row.get("provider_receipt_id")
        if provider_receipt_id is not None:
            receipt_lineage.append((int(row["id"]), provider_receipt_id))
            row["provider_receipt_id"] = None
        old_execution = row.pop("execution_id")
        old_agent = row.get("agent_run_id")
        row["agent_run_id"] = _translate_weak_run_ref(run_map, old_agent)
        if old_execution is not None:
            if int(old_execution) not in progress_by_public:
                raise ValueError("receipt execution public identity has no progress owner")
            row["execution_run_id"] = progress_by_public[int(old_execution)][1]
        else:
            row["execution_run_id"] = None
        _insert(db, "action_receipts", row)
    for receipt_id, provider_id in receipt_lineage:
        db.execute("update main.action_receipts set provider_receipt_id=? where id=?",
                   (provider_id, receipt_id))
    observer_columns = tuple(row[1] for row in db.execute(
        f'pragma temp.table_info("{_OBSERVER_CAPTURE}")'))
    db.execute("delete from main.action_observers")
    for values in db.execute(f'select * from temp."{_OBSERVER_CAPTURE}"'):
        observer = dict(zip(observer_columns, values, strict=True))
        observer["agent_run_id"] = _translate_weak_run_ref(run_map, observer["agent_run_id"])
        if observer["task_id"] != 0:
            observer["task_id"] = _task_physical_ref(db, task_metadata, task_map,
                                                     "reply_tasks", observer["task_id"])
        _insert(db, "action_observers", observer)
    identities = []
    for kind, rows_for_kind in source.items():
        for row in rows_for_kind:
            candidate = row if kind == "review_candidates" else candidates.get(row.get("candidate_id"))
            if candidate is None:
                raise ValueError("adoption identity has no candidate owner")
            target_run = _run_id(run_map, row.get("audit_run_id") if kind == "candidate_reviews" else candidate["consumer_run_id"])
            child = int(row["legacy_key"]) if kind in {
                "candidate_reviews", "candidate_selections", "candidate_supplements"} else None
            public = row.get("legacy_key")
            if public is None:
                typ, stored_public = "null", None
            elif type(public) is bytes:
                typ, stored_public = "blob", public
            elif type(public) is str:
                typ, stored_public = "text", public
            elif type(public) is int:
                typ, stored_public = "integer", str(public)
            else:
                raise ValueError("unsupported source public identity storage type")
            db.execute("insert into main.run_adoption_identity_map(source_kind,source_physical_id,source_public_id,source_public_id_type,target_run_id,target_child_id) values(?,?,?,?,?,?)",
                (kind, row["id"], stored_public, typ, target_run, child))
            identities.append((kind, row["id"], target_run, child))
    _rebuild_actions(db, task_metadata, task_public_index, task_map, run_map,
                     source_metadata["source_sequences"])
    _restore_sequence_rows(db, source_metadata["source_sequences"],
                           ("action_receipts", "action_observers", "action_entity_identity_map"))
    db.execute(f'drop table temp."{_CAPTURE}"')
    db.execute(f'drop table temp."{_OBSERVER_CAPTURE}"')
    db.execute(f'drop table temp."{_ACTION_CAPTURE}"')
    db.execute(f'drop table temp."{_ALL_ACTION_CAPTURE}"')
    return {"version": 1, "identity_rows": len(identities), "identities": [list(r) for r in identities],
            "receipt_count": len(rows), "source_sequences": source_metadata["source_sequences"],
            "selected_counts": source_metadata["selected_counts"], "source_fk_findings": source_metadata["source_fk_findings"]}
