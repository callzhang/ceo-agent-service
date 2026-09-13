"""Auditable, idempotent repair planning for task project metadata."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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


@dataclass(frozen=True)
class ApplyResult:
    changed_fields: int
    archived_projects: int
    skipped_fields: int
    skipped_archives: int
    audit_updates: int


def build_repair_manifest(
    db_path: str | Path,
    historical_db_path: str | Path | None = None,
) -> RepairManifest:
    current_path = Path(db_path)
    with _connect(current_path) as db:
        fingerprint = _database_fingerprint(db)
        projects = db.execute(
            "select * from work_projects order by id"
        ).fetchall()
        agent_values = _latest_agent_values(db)
        archives = tuple(_archive_candidates(db, projects))

    historical_rows: dict[int, sqlite3.Row] = {}
    if historical_db_path is not None:
        with _connect(Path(historical_db_path)) as historical_db:
            historical_rows = {
                int(row["id"]): row
                for row in historical_db.execute(
                    "select * from work_projects order by id"
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


def read_manifest(path: str | Path) -> RepairManifest:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return RepairManifest(
        version=int(payload["version"]),
        manifest_id=str(payload["manifest_id"]),
        database_fingerprint=str(payload["database_fingerprint"]),
        created_at=str(payload["created_at"]),
        restorations=tuple(FieldRestoration(**item) for item in payload["restorations"]),
        archives=tuple(
            ArchiveDecision(
                **{**item, "evidence_refs": tuple(item["evidence_refs"])}
            )
            for item in payload["archives"]
        ),
        unresolved_project_ids=tuple(payload.get("unresolved_project_ids", ())),
    )


def apply_manifest(
    db_path: str | Path,
    manifest: RepairManifest,
    *,
    archive_limit: int | None = None,
) -> ApplyResult:
    changed_fields = 0
    archived_projects = 0
    skipped_fields = 0
    skipped_archives = 0
    changed_by_project: dict[int, list[dict[str, str]]] = {}
    path = Path(db_path)
    with _connect(path) as db:
        db.execute("begin immediate")
        try:
            if manifest.version != 1:
                raise RuntimeError(f"unsupported repair manifest version: {manifest.version}")
            if _database_fingerprint(db) != manifest.database_fingerprint:
                raise RuntimeError("repair manifest database fingerprint mismatch")
            for restoration in manifest.restorations:
                if restoration.field not in RESTORABLE_FIELDS:
                    raise RuntimeError(
                        f"unsupported repair field: {restoration.field}"
                    )
                row = db.execute(
                    f"select {restoration.field} from work_projects where id=?",
                    (restoration.project_id,),
                ).fetchone()
                if row is None:
                    raise RuntimeError(f"project {restoration.project_id} no longer exists")
                current = str(row[restoration.field] or "")
                if current == restoration.replacement:
                    skipped_fields += 1
                    continue
                if current != restoration.expected:
                    raise RuntimeError(
                        f"project {restoration.project_id} field {restoration.field} "
                        "changed since manifest planning"
                    )
                db.execute(
                    f"update work_projects set {restoration.field}=?, "
                    "updated_at=current_timestamp where id=?",
                    (restoration.replacement, restoration.project_id),
                )
                changed_fields += 1
                changed_by_project.setdefault(restoration.project_id, []).append(
                    {
                        "field": restoration.field,
                        "evidence_kind": restoration.evidence_kind,
                        "evidence_ref": restoration.evidence_ref,
                    }
                )

            archive_decisions = manifest.archives
            if archive_limit is not None:
                archive_decisions = archive_decisions[: max(0, archive_limit)]
            for archive in archive_decisions:
                row = db.execute(
                    "select status from work_projects where id=?",
                    (archive.project_id,),
                ).fetchone()
                if row is None:
                    raise RuntimeError(f"project {archive.project_id} no longer exists")
                current_status = str(row["status"] or "")
                if current_status == "archived":
                    skipped_archives += 1
                    continue
                if current_status != archive.expected_status:
                    raise RuntimeError(
                        f"project {archive.project_id} status changed since manifest planning"
                    )
                db.execute(
                    "update work_projects set status='archived', "
                    "updated_at=current_timestamp where id=?",
                    (archive.project_id,),
                )
                archived_projects += 1
                changed_by_project.setdefault(archive.project_id, []).append(
                    {
                        "field": "status",
                        "evidence_kind": archive.reason,
                        "evidence_ref": ";".join(archive.evidence_refs),
                    }
                )

            for project_id, changes in changed_by_project.items():
                db.execute(
                    """
                    insert into work_updates(
                        project_id, source_type, source_ref, summary,
                        changes_json, merge_reason, confidence
                    ) values(?, 'repair_task_projects', ?, ?, ?, ?, 1.0)
                    """,
                    (
                        project_id,
                        f"manifest:{manifest.manifest_id}",
                        "Restored or archived by reviewed task-project repair manifest.",
                        _canonical_json({"changes": changes}),
                        "auditable_repair",
                    ),
                )
            db.commit()
        except Exception:
            db.rollback()
            raise

    return ApplyResult(
        changed_fields=changed_fields,
        archived_projects=archived_projects,
        skipped_fields=skipped_fields,
        skipped_archives=skipped_archives,
        audit_updates=len(changed_by_project),
    )


def _latest_agent_values(
    db: sqlite3.Connection,
) -> dict[tuple[int, str], tuple[str, str, str, str]]:
    values: dict[tuple[int, str], tuple[str, str, str, str]] = {}
    rows = db.execute(
        "select id, decision_json, created_at from task_agent_runs "
        "where status='completed' order by id desc"
    )
    for row in rows:
        try:
            decision = json.loads(row["decision_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
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
                f"task_agent_runs:{row['id']}",
                str(row["created_at"] or ""),
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
            "select 1 from work_todos where project_id=? limit 1", (project_id,)
        ).fetchone():
            continue
        if db.execute(
            "select 1 from follow_up_drafts where project_id=? limit 1", (project_id,)
        ).fetchone():
            continue
        updates = db.execute(
            "select source_type, source_ref from work_updates "
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
                "select payload_json from work_summary_inputs "
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
    try:
        payload = json.loads(row["payload_json"] or "{}")
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
        for row in db.execute("select id, created_at from work_projects order by id")
    ]
    related_state = {
        "todos": db.execute("select count(*), coalesce(max(id), 0) from work_todos").fetchone(),
        "follow_ups": db.execute(
            "select count(*), coalesce(max(id), 0) from follow_up_drafts"
        ).fetchone(),
        "source_inputs": db.execute(
            "select count(*), coalesce(max(id), 0) from work_summary_inputs"
        ).fetchone(),
        "business_updates": db.execute(
            "select count(*), coalesce(max(id), 0) from work_updates "
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
