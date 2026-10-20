"""Read-only repair planning for historical task project metadata."""

from __future__ import annotations
from app.task20_definitions import task20_projection
from app.logical_run_storage import logical_run_projection

from app.business_definitions import BUSINESS_PROJECTIONS
from app.action_followup_sync_definitions import SYNC_ACTION_PROJECTIONS

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.work_summary_payload import decode_work_summary_payload

TASK_LOGICAL_RUN_SQL = logical_run_projection("task_agent_runs")
WORK_SUMMARY_INPUT_SQL = task20_projection('work_summary_inputs')


def _project_sql(db: sqlite3.Connection) -> str:
    return BUSINESS_PROJECTIONS["work_project"]


def _todo_sql(db: sqlite3.Connection) -> str:
    return BUSINESS_PROJECTIONS["work_todo"]


RESTORABLE_FIELDS = (
    "title",
    "goal",
    "background",
    "owner_user_id",
    "owner_name",
    "tags_json",
    "related_people_json",
    "facts_json",
    "source_conversations_json",
)
MODEL_TO_STORAGE_FIELD = {
    "tags": "tags_json",
    "related_people": "related_people_json",
    "facts": "facts_json",
    "source_conversations": "source_conversations_json",
}
STORAGE_TO_MODEL_FIELD = {
    storage: model for model, storage in MODEL_TO_STORAGE_FIELD.items()
}


@dataclass(frozen=True)
class FieldRestoration:
    project_id: int
    field: str
    expected: str
    replacement: str
    evidence_kind: str
    evidence_ref: str
    evidence_at: str


@dataclass(frozen=True)
class ArchiveDecision:
    project_id: int
    expected_status: str
    reason: str
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class RepairManifest:
    version: int
    manifest_id: str
    database_fingerprint: str
    created_at: str
    restorations: tuple[FieldRestoration, ...]
    archives: tuple[ArchiveDecision, ...]
    unresolved_project_ids: tuple[int, ...]


def build_repair_manifest(
    db_path: str | Path,
    historical_db_path: str | Path | None = None,
) -> RepairManifest:
    current_path = Path(db_path)
    with _connect(current_path) as db:
        fingerprint = _database_fingerprint(db)
        projects = db.execute(f"select * from ({_project_sql(db)}) order by id").fetchall()
        agent_refs = _latest_agent_refs(db)
        archives = tuple(_archive_candidates(db, projects))
    agent_values = _latest_agent_values(agent_refs)

    historical_rows: dict[int, sqlite3.Row] = {}
    if historical_db_path is not None:
        with _connect(Path(historical_db_path)) as historical_db:
            historical_rows = {
                int(row["id"]): row
                for row in historical_db.execute(
                    f"select * from ({_project_sql(historical_db)}) order by id"
                )
            }

    restorations: list[FieldRestoration] = []
    unresolved: list[int] = []
    for project in projects:
        project_id = int(project["id"])
        for field in RESTORABLE_FIELDS:
            current_value = str(project[field] or "")
            if not _is_blank_storage_value(field, current_value):
                continue
            evidence = agent_values.get((project_id, field))
            if evidence is None:
                historical = historical_rows.get(project_id)
                historical_value = "" if historical is None else str(historical[field] or "")
                if not _is_blank_storage_value(field, historical_value):
                    evidence = (
                        historical_value,
                        "historical_snapshot",
                        str(Path(historical_db_path).resolve()),
                        str(historical["updated_at"] or historical["last_activity_at"]),
                    )
            if evidence is None:
                if field == "title":
                    unresolved.append(project_id)
                continue
            replacement, evidence_kind, evidence_ref, evidence_at = evidence
            restorations.append(
                FieldRestoration(
                    project_id=project_id,
                    field=field,
                    expected=current_value,
                    replacement=replacement,
                    evidence_kind=evidence_kind,
                    evidence_ref=evidence_ref,
                    evidence_at=evidence_at,
                )
            )

    payload = {
        "version": 1,
        "database_fingerprint": fingerprint,
        "restorations": [asdict(item) for item in restorations],
        "archives": [asdict(item) for item in archives],
        "unresolved_project_ids": sorted(set(unresolved)),
    }
    manifest_id = hashlib.sha256(_canonical_json(payload).encode()).hexdigest()[:20]
    return RepairManifest(
        version=1,
        manifest_id=manifest_id,
        database_fingerprint=fingerprint,
        created_at=datetime.now(timezone.utc).isoformat(),
        restorations=tuple(restorations),
        archives=archives,
        unresolved_project_ids=tuple(sorted(set(unresolved))),
    )


def write_manifest(manifest: RepairManifest, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(asdict(manifest), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )






def _latest_agent_refs(db: sqlite3.Connection) -> list[tuple[int, str, object]]:
    from app.native_standalone import latest_task_ref

    rows = db.execute(
        f"select id, created_at from ({TASK_LOGICAL_RUN_SQL}) "
        "where status='completed' order by id desc"
    ).fetchall()
    return [
        (int(row["id"]), str(row["created_at"] or ""), latest_task_ref(db, int(row["id"])))
        for row in rows
    ]


def _latest_agent_values(
    refs: list[tuple[int, str, object]],
) -> dict[tuple[int, str], tuple[str, str, str, str]]:
    from app.native_standalone import task_project_value

    values: dict[tuple[int, str], tuple[str, str, str, str]] = {}
    for run_id, created_at, ref in refs:
        decision = task_project_value(ref)
        if decision is None:
            continue
        project = decision.get("project") if isinstance(decision, dict) else None
        if not isinstance(project, dict):
            continue
        project_id = project.get("id")
        if isinstance(project_id, bool) or not isinstance(project_id, int):
            continue
        for storage_field in RESTORABLE_FIELDS:
            key = (project_id, storage_field)
            if key in values:
                continue
            model_field = STORAGE_TO_MODEL_FIELD.get(storage_field, storage_field)
            if model_field not in project:
                continue
            serialized = _storage_text(storage_field, project[model_field])
            if _is_blank_storage_value(storage_field, serialized):
                continue
            values[key] = (
                serialized,
                "task_agent_run",
                f"task_agent_runs:{run_id}",
                created_at,
            )
    return values


def _archive_candidates(
    db: sqlite3.Connection,
    projects: list[sqlite3.Row],
) -> list[ArchiveDecision]:
    candidates: list[ArchiveDecision] = []
    for project in projects:
        project_id = int(project["id"])
        if str(project["status"]) not in {"active", "waiting"}:
            continue
        if db.execute(
            f"select 1 from ({_todo_sql(db)}) where project_id=? limit 1", (project_id,)
        ).fetchone():
            continue
        if db.execute(
            f"select 1 from ({SYNC_ACTION_PROJECTIONS['follow_up_drafts']}) where project_id=? limit 1", (project_id,)
        ).fetchone():
            continue
        updates = db.execute(
            f"select source_type, source_ref from ({SYNC_ACTION_PROJECTIONS['work_updates']}) "
            "where project_id=? and source_type!='repair_task_projects' order by id",
            (project_id,),
        ).fetchall()
        if not updates or any(row["source_type"] != "local_file" for row in updates):
            continue
        project_created = _parse_datetime(str(project["created_at"] or ""))
        if project_created is None:
            continue
        evidence_refs: list[str] = []
        all_historical = True
        for update in updates:
            input_row = db.execute(
                f"select source_created_at, payload_json from ({WORK_SUMMARY_INPUT_SQL}) "
                "where source_type='local_file' and source_ref=? limit 1",
                (update["source_ref"],),
            ).fetchone()
            source_created = _source_created_at(input_row)
            if source_created is None or (project_created - source_created).days < 14:
                all_historical = False
                break
            evidence_refs.append(str(update["source_ref"]))
        if all_historical:
            candidates.append(
                ArchiveDecision(
                    project_id=project_id,
                    expected_status=str(project["status"]),
                    reason="historical_local_file_only",
                    evidence_refs=tuple(evidence_refs),
                )
            )
    return candidates


def _source_created_at(row: sqlite3.Row | None) -> datetime | None:
    if row is None:
        return None
    recorded = _parse_datetime(str(row["source_created_at"] or ""))
    if recorded is not None:
        return recorded
    try:
        payload = json.loads(decode_work_summary_payload(row["payload_json"] or "{}"))
        value = payload.get("source", {}).get("created_at", "")
    except (AttributeError, TypeError, json.JSONDecodeError):
        return None
    return _parse_datetime(str(value))


def _parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _storage_text(field: str, value: Any) -> str:
    if field.endswith("_json"):
        return _canonical_json(value)
    return str(value or "")


def _is_blank_storage_value(field: str, value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return True
    if field.endswith("_json"):
        try:
            return json.loads(stripped) in (None, [], {})
        except json.JSONDecodeError:
            return False
    return False


def _database_fingerprint(db: sqlite3.Connection) -> str:
    schema_version = db.execute("pragma user_version").fetchone()[0]
    projects = [
        [int(row["id"]), str(row["created_at"])]
        for row in db.execute(f"select id, created_at from ({_project_sql(db)}) order by id")
    ]
    related_state = {
        "todos": db.execute(f"select count(*), coalesce(max(id), 0) from ({_todo_sql(db)})").fetchone(),
            "follow_ups": db.execute(
            f"select count(*), coalesce(max(id), 0) from ({SYNC_ACTION_PROJECTIONS['follow_up_drafts']})"
        ).fetchone(),
        "source_inputs": db.execute(
            f"select count(*), coalesce(max(id), 0) from ({WORK_SUMMARY_INPUT_SQL})"
        ).fetchone(),
        "business_updates": db.execute(
            f"select count(*), coalesce(max(id), 0) from ({SYNC_ACTION_PROJECTIONS['work_updates']}) "
            "where source_type!='repair_task_projects'"
        ).fetchone(),
    }
    payload = {
        "schema_version": schema_version,
        "projects": projects,
        "related_state": {
            name: [int(row[0]), int(row[1])]
            for name, row in related_state.items()
        },
    }
    return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _connect(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    return db
