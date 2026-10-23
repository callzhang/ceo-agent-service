"""Task3B migration-only forward transfer from the selected current Run schema."""
from __future__ import annotations

import sqlite3
import hashlib
import json
import re

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
_HANDOFF_VERSION = 3


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


def _validate_source(db: sqlite3.Connection, metadata: dict, task_metadata: dict) -> None:
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
    current_findings = set(_run_fk_findings(db))
    expected_findings = set(metadata["run_fk_findings"])
    task_edges = _task_forward_edges(db, task_metadata)
    task_fk_ids = {int(row[0]) for row in db.execute(
        "pragma main.foreign_key_list(runs)") if row[2] == "tasks" and row[3] == "task_id"}
    task_run_kinds = {"task_agent_runs": "work_summary_inputs",
                      "meeting_alignment_runs": "meeting_alignment_jobs",
                      "okr_review_runs": "okr_review_requests"}
    for run_id, record_type, source_type, task_id in db.execute(
            "select id,record_type,source_type,task_id from main.runs "
            "where record_type in ('agent_runs','workbench_events') and task_id is not null"):
        source_kind = ("workbench_turns" if record_type == "workbench_events"
                       else task_run_kinds.get(source_type, "reply_tasks"))
        mapped = task_edges.get((source_kind, int(task_id)))
        if (mapped is not None and mapped != int(task_id)
                and not db.execute("select 1 from main.tasks where id=?", (task_id,)).fetchone()):
            expected_findings.update(("runs", int(run_id), "tasks", fk_id)
                                     for fk_id in task_fk_ids)
    if current_findings != expected_findings:
        raise ValueError("Run source FK findings changed after typed Task migration")
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


def _run_public_maps(db: sqlite3.Connection, rows, owner_maps: dict):
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
            by_namespace_public[(namespace, "int", cast_key)] = _run_target_id(
                owner_maps, kind, physical)
    for kind, row in rows:
        if kind != "agent_runs":
            continue
        physical = row["id"]
        by_physical[physical] = _run_target_id(owner_maps, kind, physical)
    return by_physical, by_namespace_public, public_order_by_physical


def _canonical_json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def run_handoff_context(source_metadata: dict) -> str:
    """Bind a handoff to selected source DDL, not mutable rows or allocators."""
    return hashlib.sha256(_canonical_json(source_metadata["source_objects"])).hexdigest()


def run_handoff_digest(source_context: str, identity_edges: list,
                       runtime_attempt_parent_edges: list,
                       runtime_attempt_orphan_workload_edges: list,
                       owner_highwaters: dict) -> str:
    """Digest the JSON-portable B3 Run identity handoff payload."""
    return hashlib.sha256(_canonical_json({
        "version": _HANDOFF_VERSION, "source_context": source_context,
        "identity_edges": identity_edges,
        "runtime_attempt_parent_edges": runtime_attempt_parent_edges,
        "runtime_attempt_orphan_workload_edges": runtime_attempt_orphan_workload_edges,
        "owner_highwaters": owner_highwaters,
    })).hexdigest()


def _run_owner_maps(rows, handoff: dict, source_metadata: dict, db, task_edges) -> dict:
    owners = tuple(dict.fromkeys(_SOURCE_TO_OWNER.values()))
    if type(handoff) is not dict or set(handoff) != {
            "version", "source_context", "identity_edges", "runtime_attempt_parent_edges",
            "runtime_attempt_orphan_workload_edges",
            "owner_highwaters", "digest"}:
        raise ValueError("Run conversion requires portable B3 identity handoff")
    if (type(handoff["version"]) is not int or handoff["version"] != _HANDOFF_VERSION
            or type(handoff["source_context"]) is not str
            or handoff["source_context"] != run_handoff_context(source_metadata)
            or type(handoff["identity_edges"]) is not list
            or type(handoff["owner_highwaters"]) is not dict
            or set(handoff["owner_highwaters"]) != set(owners)
            or type(handoff["digest"]) is not str):
        raise ValueError("invalid or mismatched portable Run handoff")
    if handoff["digest"] != run_handoff_digest(
            handoff["source_context"], handoff["identity_edges"],
            handoff["runtime_attempt_parent_edges"],
            handoff["runtime_attempt_orphan_workload_edges"], handoff["owner_highwaters"]):
        raise ValueError("portable Run handoff digest mismatch")
    highwaters = handoff["owner_highwaters"]
    if any(value is not None and (type(value) is not int or value < 0)
           for value in highwaters.values()):
        raise ValueError("invalid native Run owner high-water")
    sources = {(kind, row["id"]): row for kind, row in rows}
    edges = {}
    ordered_keys = []
    for edge in handoff["identity_edges"]:
        if type(edge) is not dict or set(edge) != {
                "source_kind", "source_physical_id", "canonical_id"}:
            raise ValueError("invalid portable Run identity edge")
        kind, source_id, canonical_id = (edge["source_kind"], edge["source_physical_id"],
                                         edge["canonical_id"])
        if (type(kind) is not str or kind not in _SOURCE_TO_OWNER
                or type(source_id) is not int or source_id < 1
                or type(canonical_id) is not int or canonical_id < 1
                or source_id == canonical_id):
            raise ValueError("unknown or malformed portable Run identity edge")
        key = (kind, source_id)
        ordered_keys.append(key)
        if key in edges:
            raise ValueError("duplicate portable Run identity edge")
        if key not in sources:
            raise ValueError("dangling portable Run identity edge")
        edges[key] = canonical_id
    if len(edges) != len(handoff["identity_edges"]):
        raise ValueError("duplicate portable Run identity edge")
    if ordered_keys != sorted(ordered_keys):
        raise ValueError("portable Run identity edges are not deterministically sorted")
    maps = {owner: {} for owner in owners}
    targets = {owner: set() for owner in owners}
    for kind, row in rows:
        owner = _SOURCE_TO_OWNER[kind]
        source_id = row["id"]
        target_id = edges.get((kind, source_id), source_id)
        if target_id in targets[owner]:
            raise ValueError(f"canonical Run ID collision in owner {owner}: {target_id}")
        targets[owner].add(target_id)
        maps[owner][(kind, source_id)] = target_id
    parent_edges = handoff["runtime_attempt_parent_edges"]
    if type(parent_edges) is not list:
        raise ValueError("invalid structured RuntimeAttempt parent edges")
    source_rows = {(kind, row["id"]): row for kind, row in rows}
    seen, ordered = set(), []
    validated = {}
    identity_targets = {(kind, source): target for owner in maps.values()
                        for (kind, source), target in owner.items()}
    for edge in parent_edges:
        if type(edge) is not dict or set(edge) != {
                "attempt_source_physical_id", "run_source_physical_id"}:
            raise ValueError("invalid structured RuntimeAttempt parent edge")
        attempt_id, run_id = edge["attempt_source_physical_id"], edge["run_source_physical_id"]
        if type(attempt_id) is not int or attempt_id < 1 or type(run_id) is not int or run_id < 1:
            raise ValueError("invalid structured RuntimeAttempt parent edge")
        key = (attempt_id, run_id)
        ordered.append(key)
        if key in seen:
            raise ValueError("duplicate structured RuntimeAttempt parent edge")
        seen.add(key)
        attempt = source_rows.get(("agent_runtime_attempts", attempt_id))
        parent = source_rows.get(("agent_runs", run_id))
        if (attempt is None or parent is None or attempt["workload_kind"] != "structured"
                or attempt["parent_id"] is not None or parent["source_type"] != "okr_review_runs"):
            raise ValueError("wrong-kind structured RuntimeAttempt parent edge")
        request_task_id = _task_public_owner(
            db, "okr_review_requests", int(attempt["workload_key"]))
        parent_task_id = _task_physical_owner(db, task_edges, "okr_review_requests", parent["task_id"])
        if request_task_id != parent_task_id:
            raise ValueError("structured RuntimeAttempt parent Task mismatch")
        validated[attempt_id] = identity_targets[("agent_runs", run_id)]
    if ordered != sorted(ordered):
        raise ValueError("structured RuntimeAttempt parent edges are not deterministically sorted")
    orphan_edges = handoff["runtime_attempt_orphan_workload_edges"]
    if type(orphan_edges) is not list:
        raise ValueError("invalid orphan RuntimeAttempt workload edges")
    orphan_seen, orphan_order, orphan_validated = set(), [], {}
    for edge in orphan_edges:
        if type(edge) is not dict or set(edge) != {
                "attempt_source_physical_id", "workload_kind", "source_operation_key",
                "native_workload_key"}:
            raise ValueError("invalid orphan RuntimeAttempt workload edge")
        attempt_id, workload_kind = edge["attempt_source_physical_id"], edge["workload_kind"]
        source_key, native_key = edge["source_operation_key"], edge["native_workload_key"]
        if (type(attempt_id) is not int or attempt_id < 1 or workload_kind != "email_classification"
                or type(source_key) is not str
                or not re.fullmatch(r"email-classification:[0-9a-f]{64}", source_key)
                or type(native_key) is not str or not native_key.isdecimal()
                or str(int(native_key)) != native_key or int(native_key) < 1):
            raise ValueError("malformed orphan RuntimeAttempt workload edge")
        key = (attempt_id, workload_kind, source_key, native_key)
        orphan_order.append(key)
        if attempt_id in orphan_seen:
            raise ValueError("duplicate orphan RuntimeAttempt workload edge")
        orphan_seen.add(attempt_id)
        attempt = source_rows.get(("agent_runtime_attempts", attempt_id))
        if (attempt is None or attempt["workload_kind"] != workload_kind
                or str(attempt["workload_key"]) != source_key or attempt["parent_id"] is not None):
            raise ValueError("orphan RuntimeAttempt edge differs from source attempt")
        from app.task_entity_schema import resolve_source_public_owner
        if resolve_source_public_owner(
                db, source_kind="email_agent_classification_tasks",
                public_key=source_key) is not None:
            raise ValueError("orphan RuntimeAttempt source key collides with a live Email Task")
        live_task = db.execute("select kind from main.tasks where id=?", (int(native_key),)).fetchone()
        if live_task is not None:
            if live_task[0] != "email_classification":
                raise ValueError("orphan RuntimeAttempt edge collides with a wrong-kind live Task")
            raise ValueError("orphan RuntimeAttempt edge names a live Email Task")
        orphan_validated[attempt_id] = native_key
    if orphan_order != sorted(orphan_order):
        raise ValueError("orphan RuntimeAttempt workload edges are not deterministically sorted")
    return maps, validated, orphan_validated


def _run_target_id(owner_maps: dict, source_kind: str, source_id: int) -> int:
    return owner_maps[_SOURCE_TO_OWNER[source_kind]][(source_kind, source_id)]


def _restore_owner_highwaters(db: sqlite3.Connection, owner_maps: dict,
                              highwaters: dict) -> None:
    for owner, ids in owner_maps.items():
        maximum = max(ids.values(), default=0)
        captured = highwaters[owner]
        source_global = db.execute(
            "select seq from main.sqlite_sequence where name=?", (owner,)).fetchone()
        # The selected source's global `runs` AUTOINCREMENT row and the native
        # `runs` owner share one SQLite sequence name after conversion. Keep
        # its source value as a floor alongside the independent native water.
        desired = max(maximum, captured or 0, source_global[0] if source_global else 0)
        existing = db.execute("select rowid from main.sqlite_sequence where name=?", (owner,)).fetchone()
        if desired or existing or captured is not None:
            if existing:
                db.execute("update main.sqlite_sequence set seq=? where name=?", (desired, owner))
            else:
                db.execute("insert into main.sqlite_sequence(name,seq) values(?,?)", (owner, desired))


def _task_workload_key_class(key: str) -> str:
    """Classify Task workload identity using the frozen source key grammar."""
    parts = key.split(":")
    task_id = parts[0]
    if not task_id.isdecimal() or int(task_id) <= 0:
        raise ValueError(f"Task workload key must start with a persisted ID: {key!r}")
    if len(parts) == 1:
        return "plain"
    suffix = parts[1]
    if suffix != "decision_repair" and not suffix.startswith("decision_repair."):
        return "business"
    if len(parts) != 2:
        raise ValueError(f"malformed decision_repair Task workload key: {key!r}")
    operation, separator, round_text = suffix.partition(".")
    if (operation != "decision_repair" or not separator or not round_text.isdecimal()
            or int(round_text) <= 0 or str(int(round_text)) != round_text):
        raise ValueError(f"malformed decision_repair Task workload key: {key!r}")
    return "decision_repair"


def _meeting_workload_key_parts(key: str) -> tuple[int, str]:
    """Return the Meeting Run identity and the one supported operation suffix."""
    parts = key.split(":")
    if (len(parts) not in {1, 2} or not parts[0].isdecimal()
            or int(parts[0]) <= 0 or (len(parts) == 2 and parts[1] != "audit")):
        raise ValueError(f"malformed Meeting RuntimeAttempt workload key: {key!r}")
    return int(parts[0]), ":audit" if len(parts) == 2 else ""


def _workload_key(db, row, task_edges, run_physical, run_public):
    kind, key = row["workload_kind"], row["workload_key"]
    parts = key.split(":")
    if kind == "agent_run":
        return str(run_physical[int(key)])
    if kind == "task":
        task_key_class = _task_workload_key_class(key)
        suffix = parts[1:]
        if task_key_class in {"plain", "decision_repair"}:
            physical = run_public[("task_agent_runs", "int", int(parts[0]))]
            return str(physical) + ((":" + ":".join(suffix)) if suffix else "")
        # memory_backfill Project IDs and deadline_backfill Todo IDs remain in
        # their business identity domain.
        return key
    if kind == "meeting":
        source_run_id, suffix = _meeting_workload_key_parts(key)
        return str(run_public[("meeting_alignment_runs", "int", source_run_id)]) + suffix
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


def _professional_attempt_parent(row, run_public):
    """Return the typed professional Run owner proven by this source key."""
    kind, key = row["workload_kind"], row["workload_key"]
    if kind == "task":
        parts = key.split(":")
        if _task_workload_key_class(key) in {"plain", "decision_repair"}:
            return run_public[("task_agent_runs", "int", int(parts[0]))]
    elif kind == "meeting":
        source_run_id, _ = _meeting_workload_key_parts(key)
        return run_public[("meeting_alignment_runs", "int", source_run_id)]
    return None


def _source_reference(db, kind: str, source: dict, field: str, task_edges: dict,
                      run_physical: dict, run_public: dict, orphan_workloads=None):
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
        orphan_key = (orphan_workloads or {}).get(int(source["id"]))
        if orphan_key is not None:
            return orphan_key
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
                         task_metadata: dict, run_handoff: dict) -> dict:
    """Forward all eight source families into their declared native owners.

    ``task_metadata`` is the result of Task2B's forward migration in this same
    database/transaction. Sparse public/physical identity edges are verified
    against the native identity map; identity fallback is checked against the
    exact native Task kind before a reference is accepted.
    """
    _validate_source(db, source_metadata, task_metadata)
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
    # Reject malformed repair-shaped keys before creating even the temporary
    # migration snapshot. The source Store remains the frozen oracle.
    for (workload_key,) in db.execute(
            "select workload_key from main.runs where record_type='agent_runtime_attempts' "
            "and workload_kind='task'"):
        _task_workload_key_class(workload_key)
    db.execute(f"create temp table {_quote(_STAGE)} as select * from main.runs")
    rows = list(_source_rows(db))
    owner_maps, structured_parents, orphan_workloads = _run_owner_maps(
        rows, run_handoff, source_metadata, db, task_edges)
    run_physical, run_public, run_public_order = _run_public_maps(db, rows, owner_maps)
    kinds_by_id = {row["id"]: kind for kind, row in rows}
    rows_by_id = {row["id"]: row for _, row in rows}
    orphan_refs = _captured_task_orphans(db, rows, source_metadata["run_fk_findings"])
    # Resolve all references before writing target rows. A missing old Task FK
    # is retained only when it was already a captured source finding.
    for kind, row in rows:
        if kind in {"agent_runs", "workbench_events"} and row["task_id"] is not None:
            source_kind = ("workbench_turns" if kind == "workbench_events" else
                           _PROFESSIONAL_TASK_KINDS.get(row.get("source_type"), "reply_tasks"))
            if (row["id"], "task_id", row["task_id"]) not in orphan_refs:
                _task_physical_owner(db, task_edges, source_kind, row["task_id"])
        if kind == "agent_runs" and row["parent_id"] is not None:
            parent_kind = kinds_by_id.get(row["parent_id"])
            if parent_kind != "agent_runs":
                raise ValueError(f"unresolved logical Run parent: {row['parent_id']!r}")
            if rows_by_id[row["parent_id"]]["source_type"] is not None:
                raise ValueError(f"logical Run parent is not Consumer/Audit: {row['parent_id']!r}")
        if kind == "agent_runtime_attempts":
            parent = row["parent_id"]
            if parent is not None and parent not in kinds_by_id:
                raise ValueError(f"unresolved physical Run parent: {parent!r}")
            if row["id"] not in orphan_workloads:
                _workload_key(db, row, task_edges, run_physical, run_public)
        for field in ("source_id", "execution_id"):
            if field in row and row[field] is not None:
                _source_reference(db, kind, row, field, task_edges, run_physical, run_public,
                                  orphan_workloads)
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
            fields["id"] = _run_target_id(owner_maps, kind, source["id"])
            if source["parent_id"] is not None:
                parent_kind = kinds_by_id[source["parent_id"]]
                fields["parent_id"] = _run_target_id(owner_maps, parent_kind, source["parent_id"])
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
                canonical_id = fields["id"]
                _insert(db, "run_provenance", {"run_id": canonical_id, "source_type": source_type,
                                                "job_id": source["job_id"]})
                if source["projection_json"] is not None:
                    _insert(db, "run_projections", {"run_id": canonical_id,
                                                     "projection_json": source["projection_json"]})
        else:
            for name in ("source_id", "execution_id", "workload_key"):
                if name in fields:
                    fields[name] = _source_reference(
                        db, kind, source, name, task_edges, run_physical, run_public,
                        orphan_workloads)
            fields["id"] = _run_target_id(owner_maps, kind, source["id"])
            if kind == "agent_runtime_attempts":
                if source["workload_kind"] == "agent_run":
                    fields["parent_id"] = _run_target_id(
                        owner_maps, kinds_by_id[source["parent_id"]], source["parent_id"])
                else:
                    fields["parent_id"] = structured_parents.get(
                        source["id"], _professional_attempt_parent(source, run_public))
            elif kind == "agent_run_state_events":
                fields["run_id"] = _run_target_id(owner_maps, "agent_runs", source["parent_id"])
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
            "target_id": _run_target_id(owner_maps, kind, source["id"]), "allocator_highwater": None,
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
    _restore_owner_highwaters(db, owner_maps, run_handoff["owner_highwaters"])
    expected_orphans = {
        (_run_target_id(owner_maps, kinds_by_id[physical], physical), value)
        for physical, field, value in orphan_refs if field == "task_id"
    }
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


def _inverse_task_ids(db: sqlite3.Connection, task_metadata: dict,
                      task_inverse_metadata: dict | None = None) -> tuple[dict, dict, dict]:
    if task_inverse_metadata is not None:
        if (type(task_inverse_metadata) is not dict
                or set(task_inverse_metadata) != {
                    "rows", "source_objects", "sparse_identity_metadata",
                    "inverse_identity", "source_context",
                }):
            raise ValueError("Run inverse requires actual Task2B inverse metadata")
        inverse = task_inverse_metadata["inverse_identity"]
        context = task_inverse_metadata["source_context"]
        if (type(inverse) is not dict or type(context) is not dict
                or set(context) != {"version", "handoff_required", "handoff_sha256"}
                or context["version"] != 1
                or type(context["handoff_required"]) is not bool
                or not isinstance(context["handoff_sha256"], str)):
            raise ValueError("invalid Task2B inverse identity context")
        from app.task_entity_schema import _inverse_identity_digest, _validate_inverse_identity
        source_physical = _validate_inverse_identity(inverse)["owners"]
        if any(edge["source_physical_id"] == edge["canonical_id"]
               for edge in inverse["owners"]):
            raise ValueError("duplicate or redundant Task2B inverse identity edge")
        if context.get("handoff_sha256") != _inverse_identity_digest(inverse):
            raise ValueError("Task2B inverse identity context digest mismatch")
        source_to_target, target_to_source = {}, {}
        public_by_source = {}
        relevant_kinds = {*_PROFESSIONAL_TASK_KINDS.values(), "reply_tasks",
                          "workbench_turns", "okr_review_requests",
                          "weekly_okr_analysis_jobs", "email_agent_classification_tasks",
                          "wechat_memory_import_jobs"}
        for kind in sorted(relevant_kinds):
            projection = task20_definitions.task20_projection(kind)
            identity = "tasks.id as _physical_id,"
            if projection.startswith("select * from(select "):
                projection = projection.replace("select * from(select ",
                                                "select * from(select " + identity, 1)
            else:
                projection = projection.replace("select ", "select " + identity, 1)
            cursor = db.execute(projection)
            names = [item[0] for item in cursor.description]
            identity_field = "id" if "id" in names else "task_id" if "task_id" in names else None
            if identity_field is None:
                continue
            for values in cursor:
                row = dict(zip(names, values, strict=True))
                raw_public = row[identity_field]
                if raw_public is None:
                    continue
                physical = int(row["_physical_id"])
                canonical = source_physical.get((kind, physical), physical)
                key = (kind, canonical)
                if key in target_to_source:
                    raise ValueError("ambiguous restored Task source identity")
                source_to_target[(kind, physical)] = canonical
                target_to_source[key] = physical
                public_by_source[key] = raw_public
        target_to_source["_restored_source"] = True
        return target_to_source, source_to_target, public_by_source
    task_edges = _task_forward_edges(db, task_metadata)
    source_to_target = dict(task_edges)
    target_to_source = {(kind, target): physical
                        for (kind, physical), target in task_edges.items()}
    return target_to_source, source_to_target, {}


def _task_source_physical(db: sqlite3.Connection, target_to_source: dict,
                          source_kind: str, target_id, *, allow_captured_orphan: bool = False):
    if target_id is None:
        return None
    if target_to_source and target_to_source.get("_restored_source"):
        physical = target_to_source.get((source_kind, int(target_id)))
        if physical is None:
            if allow_captured_orphan:
                return int(target_id)
            raise ValueError(f"native Task reference has wrong or missing owner: {source_kind}:{target_id}")
        return physical
    physical = target_to_source.get((source_kind, int(target_id)), int(target_id))
    expected_kind = _task_kind(source_kind)
    row = db.execute("select kind from main.tasks where id=?", (target_id,)).fetchone()
    if row is None or row[0] != expected_kind:
        if allow_captured_orphan and row is None:
            return int(target_id)
        raise ValueError(f"native Task reference has wrong or missing owner: {source_kind}:{target_id}")
    return physical


def _task_public_from_target(db: sqlite3.Connection, target_to_source: dict,
                             source_kind: str, target_id, public_by_target: dict | None = None):
    if target_id is None:
        return None
    if public_by_target is not None:
        key = (source_kind, int(target_id))
        if key not in public_by_target:
            raise ValueError(f"native Task public identity is unresolved: {source_kind}:{target_id}")
        return str(public_by_target[key])
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
                          run_ids: dict, workload_kind: str,
                          task_public_by_target: dict | None = None,
                          run_physical_by_target: dict | None = None) -> str:
    parts = value.split(":")
    kind = workload_kind
    if kind == "agent_run":
        if run_physical_by_target is not None:
            physical = run_physical_by_target.get(int(value))
            if physical is None:
                raise ValueError(f"unresolved native agent Run workload identity: {value!r}")
            return str(physical)
        return value
    if kind == "task":
        suffix = parts[1:]
        task_key_class = _task_workload_key_class(value)
        if task_key_class == "business":
            return value
        run = run_ids.get(int(parts[0]))
        if run is None or run[0] != "task_agent_runs":
            raise ValueError(f"unresolved native Task Run workload identity: {value!r}")
        return str(run[1]) + ((":" + ":".join(suffix)) if suffix else "")
    if kind == "meeting":
        native_run_id, suffix = _meeting_workload_key_parts(value)
        run = run_ids.get(native_run_id)
        if run is None or run[0] != "meeting_alignment_runs":
            raise ValueError(f"unresolved native Meeting Run workload identity: {value!r}")
        return str(run[1]) + suffix
    if kind in {"structured", "email_classification", "workbench"}:
        source_kind = {"structured": "okr_review_requests",
                       "email_classification": "email_agent_classification_tasks",
                       "workbench": "workbench_turns"}[kind]
        return _task_public_from_target(db, task_targets, source_kind, value,
                                        task_public_by_target)
    if kind == "weekly_okr" and len(parts) == 5:
        parts[3] = _task_public_from_target(db, task_targets,
                                            "weekly_okr_analysis_jobs", parts[3],
                                            task_public_by_target)
        return ":".join(parts)
    if kind == "memory" and len(parts) > 1 and parts[0] == "wechat_memory_import_job":
        task = _task_public_from_target(db, task_targets, "wechat_memory_import_jobs",
                                        parts[-1], task_public_by_target)
        parts[-1] = task
        return ":".join(parts)
    return value


def restore_run_entities(db: sqlite3.Connection, source_metadata: dict,
                         forward_metadata: dict, *, task_metadata: dict,
                         task_inverse_metadata: dict | None = None) -> dict:
    """Restore the selected-current Run source from current native owner facts.

    Caller owns an FK-off transaction. Existing-only mode composes before Task
    inverse; new-fact mode composes after Task inverse and consumes its sparse
    identity handoff plus the exact restored source projections.
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
    current_owner_ids = {}
    for kind, owner in _SOURCE_TO_OWNER.items():
        columns = {info[1] for info in db.execute(f"pragma main.table_info({_quote(owner)})")}
        key_column = "id" if "id" in columns else "run_id"
        current_owner_ids[kind] = {int(row[0]) for row in db.execute(
            f"select {_quote(key_column)} from main.{_quote(owner)}")}
    identities = {key: row for key, row in identities.items()
                  if int(row["target_id"]) in current_owner_ids[row["source_kind"]]}
    task_target_to_source, _, task_public_by_target = _inverse_task_ids(
        db, task_metadata, task_inverse_metadata)
    if (task_inverse_metadata is not None
            and task_inverse_metadata["source_objects"] != task_metadata["source_objects"]):
        raise ValueError("Task inverse result differs from its forward source declaration")
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
    new_native = {}
    for kind, owner in _SOURCE_TO_OWNER.items():
        actual = target_ids[owner]
        expected = {target for source, target in expected_target if source == kind}
        if not expected.issubset(actual):
            raise ValueError(f"missing native Run identities in {owner}")
        added = actual - expected
        if added and task_inverse_metadata is None:
            raise ValueError(f"unsupported new native Run identities in {owner}; Task-first inverse context required")
        new_native[kind] = added

    staged = []
    run_source_type = {}
    for target, provenance in db.execute(
            "select run_id,source_type from main.run_provenance"):
        run_source_type[int(target)] = provenance
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
    pending_structured_parent_edges = []
    pending_orphan_workload_edges = []
    orphan_operation_by_attempt = {}
    task_owner_kinds_by_target = {}
    for identity_key in task_target_to_source:
        if type(identity_key) is tuple:
            source_kind, canonical_id = identity_key
            task_owner_kinds_by_target.setdefault(canonical_id, set()).add(source_kind)
    for attempt_id, attempt in native_rows["runtime_attempts"].items():
        if attempt["workload_kind"] != "email_classification":
            continue
        native_key = str(attempt["workload_key"])
        if (not native_key.isdecimal() or str(int(native_key)) != native_key
                or int(native_key) < 1):
            raise ValueError("invalid native Email Task workload identity")
        restored_source = bool(task_target_to_source.get("_restored_source"))
        if restored_source:
            owner_kinds = task_owner_kinds_by_target.get(int(native_key), set())
            if ("email_agent_classification_tasks", int(native_key)) in task_public_by_target:
                owner_kinds = owner_kinds | {"email_agent_classification_tasks"}
        else:
            # Captured Task maps are sparse: a Task created after Task forward
            # migration is absent from them, but remains a real native owner.
            live_task = db.execute(
                "select kind from main.tasks where id=?", (int(native_key),)
            ).fetchone()
            if live_task is not None:
                if live_task[0] != "email_classification":
                    raise ValueError("Email RuntimeAttempt workload key has a wrong-kind live Task owner")
                owner_kinds = {"email_agent_classification_tasks"}
            else:
                owner_kinds = task_owner_kinds_by_target.get(int(native_key), set())
        if len(owner_kinds) > 1:
            raise ValueError("ambiguous Email RuntimeAttempt Task identity")
        owner_kind = next(iter(owner_kinds), None)
        if owner_kind is not None and owner_kind != "email_agent_classification_tasks":
            raise ValueError("Email RuntimeAttempt workload key has a wrong-kind live Task owner")
        if owner_kind is None:
            if not restored_source:
                raise ValueError("Email RuntimeAttempt Task absence requires the exact Task inverse owner map")
            if attempt["parent_id"] is not None:
                raise ValueError("orphan Email RuntimeAttempt must have a NULL parent")
            digest = hashlib.sha256(_canonical_json({
                "kind": "email_classification", "native_workload_key": native_key,
            })).hexdigest()
            operation_key = "email-classification:" + digest
            orphan_operation_by_attempt[attempt_id] = (native_key, operation_key)
            pending_orphan_workload_edges.append((attempt_id, native_key, operation_key))
    for attempt_id, attempt in native_rows["runtime_attempts"].items():
        if attempt["workload_kind"] == "agent_run":
            continue
        key = attempt["workload_key"]
        expected_parent, expected_source = None, None
        if attempt["workload_kind"] == "task":
            task_key_class = _task_workload_key_class(key)
            if task_key_class in {"plain", "decision_repair"}:
                expected_parent, expected_source = int(key.split(":", 1)[0]), "task_agent_runs"
        elif attempt["workload_kind"] == "meeting":
            expected_parent, expected_source = (
                _meeting_workload_key_parts(key)[0], "meeting_alignment_runs"
            )
        actual_parent = attempt["parent_id"]
        if attempt["workload_kind"] == "structured" and actual_parent is not None:
            request_task = int(attempt["workload_key"])
            _task_source_physical(
                db, task_target_to_source, "okr_review_requests", request_task)
            parent_provenance = run_source_type.get(int(actual_parent))
            parent_row = native_rows["runs"].get(int(actual_parent))
            if (parent_provenance != "okr_review_runs"
                    or parent_row is None or parent_row["task_id"] != request_task):
                raise ValueError("native structured RuntimeAttempt Run owner cannot be represented by the selected source")
            pending_structured_parent_edges.append((attempt_id, int(actual_parent)))
            continue
        if actual_parent != expected_parent or (expected_parent is not None
                and run_source_type.get(expected_parent) != expected_source):
            raise ValueError(
                "native non-Agent RuntimeAttempt Run owner cannot be represented by the selected source"
            )
    owner_highwaters = {}
    for owner in _SOURCE_TO_OWNER.values():
        sequence = db.execute("select seq from main.sqlite_sequence where name=?", (owner,)).fetchone()
        owner_highwaters[owner] = None if sequence is None else int(sequence[0])
    # Allocate globally unique old-source physical IDs for current native rows
    # that did not exist in the source captured by B3B1. Allocation is derived
    # from the actual source global water and identity rows, never from a native
    # owner-local ROWID alone.
    allocated = []
    source_physical = {int(row["physical_id"]) for row in identities.values()}
    source_run_water = max(
        [int(row[2]) for row in source_metadata["sequence_rows"] if row[1] == "runs"]
        + [value or 0 for value in owner_highwaters.values()]
        + [0])
    next_physical = max(source_run_water, max(source_physical, default=0)) + 1
    counters = {row[1]: int(row[2]) for row in source_metadata["sequence_rows"]}
    from app.runs11_definitions import RUN11_NEW_KINDS
    for identity in identities.values():
        sequence_name = ("runs:" + identity["source_kind"]
                        if identity["source_kind"] in RUN11_NEW_KINDS else None)
        if sequence_name is not None and identity["public_key_order"] is not None:
            counters[sequence_name] = max(
                counters.get(sequence_name, 0), int(identity["public_key_order"]))
    for kind in sorted(new_native):
        for target in sorted(new_native[kind]):
            owner = _SOURCE_TO_OWNER[kind]
            native = native_rows[owner][target]
            while next_physical in source_physical:
                next_physical += 1
            physical = next_physical
            next_physical += 1
            source_physical.add(physical)
            public_type, public, public_order = "text", str(physical), physical
            if kind == "agent_runs":
                # create_logical_run omits legacy_key; the selected source's
                # AFTER INSERT trigger fills it from the physical ID.
                public_type, public, public_order = "text", str(physical), physical
            elif kind in RUN11_NEW_KINDS:
                name = "runs:" + kind
                counters[name] = counters.get(name, 0) + 1
                public, public_order = str(counters[name]), counters[name]
            identity = {"source_kind": kind, "physical_id": physical,
                        "public_key": public, "public_key_type": public_type,
                        "public_key_order": public_order, "target_id": target}
            identities[(kind, physical)] = identity
            by_kind[kind].append(identity)
            allocated.append((kind, physical, target))
    run_identity_by_target = {row["target_id"]: row for row in identities.values()
                              if row["source_kind"] == "agent_runs"}
    attempt_identity_by_target = {row["target_id"]: row for row in identities.values()
                                  if row["source_kind"] == "agent_runtime_attempts"}
    run_source_public_by_target = {
        row["target_id"]: (run_source_type.get(row["target_id"]), row["public_key_order"])
        for row in run_identity_by_target.values()
    }
    run_source_physical_by_target = {
        row["target_id"]: int(row["physical_id"])
        for row in run_identity_by_target.values()
    }
    inverse_parent_edges = []
    for attempt_id, parent_id in pending_structured_parent_edges:
        attempt_identity = attempt_identity_by_target.get(attempt_id)
        parent_identity = run_identity_by_target.get(parent_id)
        if attempt_identity is None or parent_identity is None:
            raise ValueError("structured RuntimeAttempt parent has no allocated source identity")
        inverse_parent_edges.append({
            "attempt_source_physical_id": int(attempt_identity["physical_id"]),
            "run_source_physical_id": int(parent_identity["physical_id"]),
        })
    inverse_orphan_workload_edges = []
    for attempt_id, native_key, source_key in pending_orphan_workload_edges:
        attempt_identity = attempt_identity_by_target.get(attempt_id)
        if attempt_identity is None:
            raise ValueError("orphan RuntimeAttempt has no allocated source identity")
        inverse_orphan_workload_edges.append({
            "attempt_source_physical_id": int(attempt_identity["physical_id"]),
            "workload_kind": "email_classification",
            "source_operation_key": source_key,
            "native_workload_key": native_key,
        })
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
                if source["parent_id"] is not None:
                    parent = run_source_physical_by_target.get(int(source["parent_id"]))
                    if parent is None:
                        raise ValueError("unresolved logical Run parent identity")
                    source["parent_id"] = parent
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
                if source["workload_kind"] == "agent_run" and source["parent_id"] is not None:
                    parent = run_source_physical_by_target.get(int(source["parent_id"]))
                    if parent is None:
                        raise ValueError("unresolved runtime attempt parent identity")
                    source["parent_id"] = parent
                elif source["workload_kind"] != "agent_run":
                    source["parent_id"] = None
                if source["workload_kind"] == "email_classification":
                    native_key = str(source["workload_key"])
                    if (not native_key.isdecimal() or str(int(native_key)) != native_key
                            or int(native_key) < 1):
                        raise ValueError("invalid native Email Task workload identity")
                    orphan = orphan_operation_by_attempt.get(target)
                    if orphan is not None:
                        source["workload_key"] = orphan[1]
                    else:
                        source["workload_key"] = _inverse_workload_key(
                            db, source["workload_key"], task_target_to_source,
                            run_source_public_by_target, source["workload_kind"],
                            task_public_by_target or None,
                            run_source_physical_by_target if task_inverse_metadata is not None else None)
                else:
                    source["workload_key"] = _inverse_workload_key(
                        db, source["workload_key"], task_target_to_source,
                        run_source_public_by_target, source["workload_kind"],
                        task_public_by_target or None,
                        run_source_physical_by_target if task_inverse_metadata is not None else None)
            elif kind == "agent_run_state_events":
                parent = run_source_physical_by_target.get(int(source["parent_id"]))
                if parent is None:
                    raise ValueError("unresolved Run failure identity")
                source["parent_id"] = parent
            elif kind == "workbench_events" and source["task_id"] is not None:
                source["task_id"] = _task_source_physical(
                    db, task_target_to_source, "workbench_turns", source["task_id"])
            elif kind == "scheduled_task_runs" and source["execution_kind"] == "reply_task":
                source["execution_id"] = _task_public_from_target(
                    db, task_target_to_source, "reply_tasks", source["execution_id"],
                    task_public_by_target or None)
            elif kind == "codex_session_search_index" and source["source_type"] == "meeting_alignment":
                search_identity = run_source_public_by_target.get(int(source["source_id"]))
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
    restored_sequence_rows = [list(row) for row in source_metadata["sequence_rows"]]
    by_sequence = {row[1]: row for row in restored_sequence_rows}
    required_waters = {
        "runs": max([source_run_water, owner_highwaters["runs"] or 0,
                     *(value or 0 for value in owner_highwaters.values()),
                     *(physical for _kind, physical, _target in allocated)], default=0),
        "runs:published": max(
            [int(row[2]) for row in source_metadata["sequence_rows"]
             if row[1] == "runs:published"]
            + [physical for kind, physical, _target in allocated
               if kind not in RUN11_NEW_KINDS]
            + [0]),
    }
    allocated_kinds = {kind for kind, _physical, _target in allocated}
    for kind in RUN11_NEW_KINDS:
        if kind in allocated_kinds:
            required_waters["runs:" + kind] = counters.get("runs:" + kind, 0)
    for name, water in required_waters.items():
        if name in by_sequence:
            by_sequence[name][2] = max(int(by_sequence[name][2]), int(water))
        elif water > 0:
            restored_sequence_rows.append([None, name, int(water)])
    for name in _SOURCE_SEQUENCE_NAMES:
        db.execute("delete from main.sqlite_sequence where name=?", (name,))
    for rowid, name, water in restored_sequence_rows:
        if rowid is None:
            db.execute("insert into main.sqlite_sequence(name,seq) values(?,?)", (name, water))
        else:
            db.execute("insert into main.sqlite_sequence(rowid,name,seq) values(?,?,?)",
                       (rowid, name, water))
    if not logical_run_current_schema.current_run11_schema_is_current(db):
        raise ValueError("restored Run declaration differs from selected-current source")
    identity_edges = [
        {"source_kind": item["source_kind"],
         "source_physical_id": int(item["physical_id"]),
         "canonical_id": int(item["target_id"])}
        for item in identities.values() if int(item["physical_id"]) != int(item["target_id"])
    ]
    identity_edges.sort(key=lambda edge: (edge["source_kind"], edge["source_physical_id"]))
    run_handoff = {"version": _HANDOFF_VERSION,
                   "source_context": run_handoff_context(source_metadata),
                   "identity_edges": identity_edges,
                   "runtime_attempt_parent_edges": sorted(
                       inverse_parent_edges, key=lambda edge: (
                           edge["attempt_source_physical_id"], edge["run_source_physical_id"])),
                   "runtime_attempt_orphan_workload_edges": sorted(
                       inverse_orphan_workload_edges,
                       key=lambda edge: (edge["attempt_source_physical_id"],
                                         edge["workload_kind"], edge["source_operation_key"],
                                         edge["native_workload_key"])),
                   "owner_highwaters": owner_highwaters}
    run_handoff["digest"] = run_handoff_digest(
        run_handoff["source_context"], identity_edges,
        run_handoff["runtime_attempt_parent_edges"],
        run_handoff["runtime_attempt_orphan_workload_edges"], owner_highwaters)
    return {"rows": len(staged), "source_objects": source_metadata["source_objects"],
        "sequence_rows": _sequence_rows(db),
            "run_fk_findings": source_metadata["run_fk_findings"],
            "run_handoff": run_handoff}
