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
