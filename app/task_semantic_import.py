"""Plan and apply source-checked imports of formal legacy business records.

Unproven, small, or unrelated legacy rows remain in the existing history store;
they are not converted into candidates or default-attention signals. Existing
links make retries idempotent. Imported semantic truth is never inferred from
an old Project title or an Agent-created DingTalk TODO mirror.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from app.store import AutoReplyStore
from app.task_semantic_models import BusinessActorKind, FormalTaskBasis
from app.task_semantic_rules import FormalityEvidence
from app.task_semantic_service import (
    RecordFormalTask,
    SourceSignal,
    TaskSemanticService,
)


MANIFEST_VERSION = 2
LEGACY_TABLES = ("work_projects", "work_todos", "work_updates")
LegacyKind = Literal["work_projects", "work_todos", "work_updates"]
Disposition = Literal["formal_task", "official_project_match", "history_only"]


@dataclass(frozen=True)
class LegacyImportItem:
    legacy_kind: LegacyKind
    legacy_row_id: int
    legacy_project_id: int
    legacy_todo_ids: tuple[int, ...]
    disposition: Disposition
    source_digest: str
    evidence_refs: tuple[str, ...]
    semantic_task: dict[str, object] | None
    official_project_registry_key: str
    reason: str


@dataclass(frozen=True)
class TaskSemanticImportManifest:
    version: int
    manifest_id: str
    database_fingerprint: str
    created_at: str
    items: tuple[LegacyImportItem, ...]


@dataclass(frozen=True)
class ImportApplyResult:
    created: int
    skipped: int
    formal_tasks: int
    official_project_matches: int
    history_only: int


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _connect(path: str | Path, *, read_only: bool) -> sqlite3.Connection:
    location = Path(path).resolve()
    mode = "ro" if read_only else "rw"
    db = sqlite3.connect(location.as_uri() + f"?mode={mode}", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("pragma foreign_keys=on")
    if read_only:
        db.execute("pragma query_only=on")
    return db


def _rows(db: sqlite3.Connection, table: str) -> list[dict[str, object]]:
    if table not in (*LEGACY_TABLES, "business_anchors", "business_projects"):
        raise ValueError("unsupported import source table")
    if table in {"business_anchors", "business_projects"} and not _table_exists(db, table):
        return []
    return [dict(row) for row in db.execute(f"select * from {table} order by id")]


def _table_exists(db: sqlite3.Connection, table: str) -> bool:
    return db.execute(
        "select 1 from sqlite_master where type='table' and name=?", (table,)
    ).fetchone() is not None


def _fingerprint(path: str | Path, db: sqlite3.Connection) -> str:
    # Target semantic rows are deliberately absent: applying the first batch
    # must not invalidate the same manifest for the next batch or retry.
    source = {table: _rows(db, table) for table in LEGACY_TABLES}
    registry = {
        table: _rows(db, table) for table in ("business_anchors", "business_projects")
    }
    source_inputs = [dict(row) for row in db.execute(
        "select source_type, source_ref, payload_json from work_summary_inputs "
        "order by source_type, source_ref"
    )]
    return _digest({"path": str(Path(path).resolve()), "source": source,
                    "source_inputs": source_inputs, "registry": registry})


def _json_object(value: object) -> dict[str, object]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _source_backed_task(
    todo: dict[str, object], updates: dict[int, dict[str, object]],
    source_inputs: dict[tuple[str, str], tuple[dict[str, object], str]],
) -> tuple[dict[str, object], tuple[str, ...]] | None:
    update = updates.get(int(todo["created_from_update_id"] or 0))
    if update is None or int(update["project_id"]) != int(todo["project_id"]):
        return None
    evidence = _json_object(update["changes_json"]).get("formal_task_evidence")
    if not isinstance(evidence, dict):
        return None
    try:
        basis = FormalTaskBasis(str(evidence["formal_basis"]))
        author_kind = BusinessActorKind(str(evidence["author_kind"]))
    except (KeyError, ValueError):
        return None
    source_type = str(update["source_type"] or "")
    source_ref = str(update["source_ref"] or "")
    source_record = source_inputs.get((source_type, source_ref))
    if source_record is None:
        return None
    source_input, input_digest = source_record
    source = source_input.get("source")
    context = source_input.get("context")
    if not isinstance(source, dict) or not isinstance(context, dict):
        return None
    if source.get("type") != source_type or source.get("ref") != source_ref:
        return None
    excerpt = str(evidence.get("source_excerpt") or "")
    owner_excerpt = str(evidence.get("owner_excerpt") or "")
    title = str(todo["title"] or "").strip()
    owner_id = str(todo["owner_user_id"] or "").strip()
    owner_name = str(todo["owner_name"] or "").strip()
    if not all((source_type, source_ref, excerpt, owner_excerpt, title, owner_id or owner_name)):
        return None
    if (
        evidence.get("source_type") != source_type
        or evidence.get("source_ref") != source_ref
        or evidence.get("owner_user_id") != owner_id
        or evidence.get("owner_name") != owner_name
        or excerpt not in str(update["summary"] or "")
        or excerpt not in str(source_input.get("summary") or "")
        or owner_excerpt not in excerpt
        or title not in excerpt
        or (owner_name and owner_name not in owner_excerpt)
        or (not owner_name and owner_id not in owner_excerpt)
        or evidence.get("deliverable_is_explicit") is not True
        or evidence.get("owner_is_explicit") is not True
    ):
        return None
    author_id = str(evidence.get("author_user_id") or "")
    owner_identity = context.get("owner_identity")
    if not isinstance(owner_identity, dict) or owner_identity.get("user_id") != owner_id or owner_identity.get("name") != owner_name:
        return None
    if context.get("sender_user_id") != author_id or context.get("sender") != evidence.get("author_name"):
        return None
    if basis is FormalTaskBasis.EXPLICIT_ASSIGNMENT:
        if (author_kind is not BusinessActorKind.HUMAN
                or evidence.get("assigner_is_authorized") is not True
                or context.get("assignment_authorized") is not True):
            return None
    elif basis is FormalTaskBasis.EXPLICIT_COMMITMENT:
        if author_kind is not BusinessActorKind.HUMAN or not owner_id or author_id != owner_id:
            return None
    elif basis is FormalTaskBasis.EXTERNAL_TODO:
        if source_type not in {"todo_completion_check", "todo_completion_evidence_candidate"} or not str(context.get("external_task_id") or "").strip():
            return None
    elif basis is FormalTaskBasis.MEETING_ACTION_ITEM:
        if (source_type != "ai_minutes" or context.get("source_conversation_kind") != "minutes"
                or "#todos-sha256=" not in source_ref):
            return None
    return (
        {
            "title": title,
            "description": str(todo["description"] or ""),
            "formal_basis": basis.value,
            "owner_user_id": owner_id,
            "owner_name": owner_name,
            "owner_excerpt": owner_excerpt,
            "source_type": source_type,
            "source_ref": source_ref,
            "source_excerpt": excerpt,
            "author_kind": author_kind.value,
            "author_user_id": author_id,
            "author_name": str(evidence.get("author_name") or ""),
            "assigner_is_authorized": evidence.get("assigner_is_authorized") is True,
            "source_time": str(update["created_at"] or ""),
            "source_update_id": int(update["id"]),
            "source_update_digest": _digest(update),
            "source_input_digest": input_digest,
        },
        (f"{source_type}:{source_ref}",),
    )


def _plan_items(db: sqlite3.Connection) -> tuple[LegacyImportItem, ...]:
    projects = _rows(db, "work_projects")
    todos = _rows(db, "work_todos")
    updates = _rows(db, "work_updates")
    by_update = {int(row["id"]): row for row in updates}
    source_inputs = {
        (str(row["source_type"]), str(row["source_ref"])): (
            _json_object(row["payload_json"]), _digest(dict(row))
        )
        for row in db.execute(
            "select source_type, source_ref, payload_json from work_summary_inputs"
        )
    }
    todos_by_project: dict[int, list[int]] = {}
    for todo in todos:
        todos_by_project.setdefault(int(todo["project_id"]), []).append(int(todo["id"]))
    official = {
        str(row["anchor_ref"]): int(row["project_id"])
        for row in db.execute(
            "select anchor.anchor_ref, project.id as project_id "
            "from business_projects as project "
            "join business_anchors as anchor on anchor.id=project.canonical_anchor_id "
            "where anchor.anchor_type='project' and anchor.active=1"
        )
    } if _table_exists(db, "business_projects") and _table_exists(db, "business_anchors") else {}
    items: list[LegacyImportItem] = []
    for project in projects:
        project_id = int(project["id"])
        key = str(_json_object(project["memory_context_json"]).get("official_project_registry_key") or "")
        matched = bool(key and key in official)
        items.append(LegacyImportItem(
            legacy_kind="work_projects", legacy_row_id=project_id,
            legacy_project_id=project_id,
            legacy_todo_ids=tuple(todos_by_project.get(project_id, ())),
            disposition="official_project_match" if matched else "history_only",
            source_digest=_digest(project), evidence_refs=(f"work_projects:{project_id}",),
            semantic_task=None, official_project_registry_key=key if matched else "",
            reason="exact registered Project key" if matched else "legacy Project has no confirmed registry key",
        ))
    for todo in todos:
        todo_id = int(todo["id"])
        formal = _source_backed_task(todo, by_update, source_inputs)
        task, refs = formal if formal else (None, (f"work_todos:{todo_id}",))
        items.append(LegacyImportItem(
            legacy_kind="work_todos", legacy_row_id=todo_id,
            legacy_project_id=int(todo["project_id"]), legacy_todo_ids=(todo_id,),
            disposition="formal_task" if formal else "history_only",
            source_digest=_digest(todo), evidence_refs=refs,
            semantic_task=task, official_project_registry_key="",
            reason=("source-backed formal Task basis" if formal else
                    "no confirmed formal Task; retain only in legacy history"),
        ))
    for update in updates:
        update_id = int(update["id"])
        items.append(LegacyImportItem(
            legacy_kind="work_updates", legacy_row_id=update_id,
            legacy_project_id=int(update["project_id"]), legacy_todo_ids=(),
            disposition="history_only", source_digest=_digest(update),
            evidence_refs=(f"{update['source_type']}:{update['source_ref']}",),
            semantic_task=None, official_project_registry_key="",
            reason="legacy update retained only in history",
        ))
    recency = {
        ("work_projects", int(row["id"])): str(
            row.get("last_activity_at") or row.get("updated_at") or row.get("created_at") or ""
        ) for row in projects
    }
    recency.update({
        ("work_todos", int(row["id"])): str(row.get("updated_at") or row.get("created_at") or "")
        for row in todos
    })
    recency.update({
        ("work_updates", int(row["id"])): str(row.get("created_at") or "")
        for row in updates
    })
    rank = {"work_projects": 2, "work_todos": 1, "work_updates": 0}
    items.sort(
        key=lambda item: (
            recency[(item.legacy_kind, item.legacy_row_id)],
            rank[item.legacy_kind], item.legacy_row_id,
        ),
        reverse=True,
    )
    return tuple(items)


def _manifest_id(version: int, fingerprint: str, items: tuple[LegacyImportItem, ...]) -> str:
    return _digest({
        "version": version, "database_fingerprint": fingerprint,
        "items": [asdict(item) for item in items],
    })


def build_task_semantic_import_manifest(
    db_path: str | Path, *, limit: int | None = None
) -> TaskSemanticImportManifest:
    if limit is not None and limit < 1:
        raise ValueError("import plan limit must be positive")
    with _connect(db_path, read_only=True) as db:
        db.execute("begin")
        fingerprint = _fingerprint(db_path, db)
        items = _plan_items(db)
    if limit is not None:
        items = items[:limit]
    return TaskSemanticImportManifest(
        version=MANIFEST_VERSION,
        manifest_id=_manifest_id(MANIFEST_VERSION, fingerprint, items),
        database_fingerprint=fingerprint,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        items=items,
    )


def write_task_semantic_import_manifest(
    manifest: TaskSemanticImportManifest, path: str | Path
) -> None:
    Path(path).write_text(json.dumps(asdict(manifest), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_task_semantic_import_manifest(path: str | Path) -> TaskSemanticImportManifest:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != {
        "version", "manifest_id", "database_fingerprint", "created_at", "items"
    } or not isinstance(payload["items"], list):
        raise ValueError("invalid task semantic import manifest")
    items = tuple(LegacyImportItem(
        **{**item, "legacy_todo_ids": tuple(item["legacy_todo_ids"]),
           "evidence_refs": tuple(item["evidence_refs"])}
    ) for item in payload["items"])
    return TaskSemanticImportManifest(
        version=payload["version"], manifest_id=payload["manifest_id"],
        database_fingerprint=payload["database_fingerprint"],
        created_at=payload["created_at"], items=items,
    )


def _check_manifest(db_path: str | Path, manifest: TaskSemanticImportManifest) -> None:
    if manifest.version != MANIFEST_VERSION:
        raise ValueError("unsupported task semantic import manifest version")
    if manifest.manifest_id != _manifest_id(
        manifest.version, manifest.database_fingerprint, manifest.items
    ):
        raise ValueError("task semantic import manifest checksum mismatch")
    with _connect(db_path, read_only=True) as db:
        db.execute("begin")
        if _fingerprint(db_path, db) != manifest.database_fingerprint:
            raise ValueError("task semantic import database fingerprint mismatch")
        expected = _plan_items(db)
    if expected[:len(manifest.items)] != manifest.items:
        raise ValueError("task semantic import manifest source digest or classification mismatch")


def _link_column(kind: LegacyKind) -> str:
    if kind not in LEGACY_TABLES:
        raise ValueError("unsupported legacy kind")
    return {"work_projects": "work_project_id", "work_todos": "work_todo_id",
            "work_updates": "work_update_id"}[kind]


def _signal_for_item(item: LegacyImportItem, row: sqlite3.Row) -> SourceSignal:
    details = item.semantic_task or {}
    source_type = str(details.get("source_type") or f"legacy_{item.legacy_kind}")
    source_ref = str(details.get("source_ref") or f"{item.legacy_kind}:{item.legacy_row_id}")
    evidence_text = str(details.get("source_excerpt") or row["title"] if "title" in row.keys() else row["summary"])
    if not evidence_text.strip():
        evidence_text = _canonical(dict(row))
    owner_identity = {
        "user_id": str(details.get("owner_user_id") or ""),
        "name": str(details.get("owner_name") or ""),
    }
    return SourceSignal(
        source_type=source_type, source_ref=source_ref, evidence_text=evidence_text,
        dedupe_key=f"legacy:{item.legacy_kind}:{item.legacy_row_id}:{item.source_digest}",
        source_time=str(details.get("source_time") or row["created_at"] or ""),
        author_user_id=str(details.get("author_user_id") or ""),
        author_name=str(details.get("author_name") or ""),
        author_kind=BusinessActorKind(str(details.get("author_kind") or "unknown")),
        context_json=_canonical({"legacy_row": dict(row), "owner_identity": owner_identity}),
    )


def _check_supporting_source(
    db: sqlite3.Connection, item: LegacyImportItem
) -> None:
    details = item.semantic_task
    if item.disposition != "formal_task" or details is None:
        return
    update = db.execute(
        "select * from work_updates where id=?", (details["source_update_id"],)
    ).fetchone()
    source_input = db.execute(
        "select source_type, source_ref, payload_json from work_summary_inputs "
        "where source_type=? and source_ref=?",
        (details["source_type"], details["source_ref"]),
    ).fetchone()
    if (
        update is None
        or source_input is None
        or int(update["project_id"]) != item.legacy_project_id
        or _digest(dict(update)) != details["source_update_digest"]
        or _digest(dict(source_input)) != details["source_input_digest"]
    ):
        raise ValueError("task semantic import supporting source digest mismatch")


def _existing_link_matches(
    db: sqlite3.Connection, item: LegacyImportItem, existing: sqlite3.Row
) -> bool:
    if item.disposition == "official_project_match":
        match = db.execute(
            "select project.id from business_projects as project "
            "join business_anchors as anchor on anchor.id=project.canonical_anchor_id "
            "where anchor.anchor_type='project' and anchor.active=1 and anchor.anchor_ref=?",
            (item.official_project_registry_key,),
        ).fetchone()
        return match is not None and existing["project_id"] == match["id"]
    if item.disposition == "history_only":
        return False
    details = item.semantic_task
    if details is None or existing["task_id"] is None:
        return False
    task = db.execute(
        "select stage, formal_basis, title from business_tasks where id=?",
        (existing["task_id"],),
    ).fetchone()
    if task is None or task["title"] != details["title"]:
        return False
    expected_stage = "formal"
    if task["stage"] != expected_stage or task["formal_basis"] != details["formal_basis"]:
        return False
    evidence = db.execute(
        "select 1 from business_task_evidence as evidence "
        "join business_task_signals as signal on signal.id=evidence.signal_id "
        "where evidence.task_id=? and signal.dedupe_key=? limit 1",
        (existing["task_id"],
         f"legacy:{item.legacy_kind}:{item.legacy_row_id}:{item.source_digest}"),
    ).fetchone()
    return evidence is not None


def apply_task_semantic_import_manifest(
    db_path: str | Path, manifest: TaskSemanticImportManifest,
    *, limit: int | None = None,
) -> ImportApplyResult:
    if limit is not None and limit < 1:
        raise ValueError("import apply limit must be positive")
    _check_manifest(db_path, manifest)  # All source/manifest checks precede any write.
    store = AutoReplyStore(db_path)
    service = TaskSemanticService(store)
    created = skipped = formal = matches = history_only = 0
    for item in manifest.items[:limit]:
        column = _link_column(item.legacy_kind)
        with store.business_task_transaction() as db:
            current = db.execute(
                f"select * from {item.legacy_kind} where id=?", (item.legacy_row_id,)
            ).fetchone()
            if current is None or _digest(dict(current)) != item.source_digest:
                raise ValueError("task semantic import source digest mismatch")
            _check_supporting_source(db, item)
            existing = db.execute(
                f"select * from business_legacy_links where {column}=?",
                (item.legacy_row_id,),
            ).fetchone()
            if existing is not None:
                if not _existing_link_matches(db, item, existing):
                    raise ValueError("task semantic import conflicting legacy link")
                skipped += 1
                continue
            if item.disposition == "history_only":
                # Legacy history is already served by the explicit history
                # route. Do not create a semantic signal/task for it.
                history_only += 1
                continue
            endpoint: str
            endpoint_id: int
            if item.disposition == "official_project_match":
                row = db.execute(
                    "select project.id from business_projects as project "
                    "join business_anchors as anchor on anchor.id=project.canonical_anchor_id "
                    "where anchor.anchor_type='project' and anchor.active=1 and anchor.anchor_ref=?",
                    (item.official_project_registry_key,),
                ).fetchone()
                if row is None:
                    raise ValueError("official Project registry match disappeared")
                endpoint, endpoint_id = "project_id", int(row["id"])
                matches += 1
            elif item.disposition == "formal_task":
                details = item.semantic_task
                if details is None:
                    raise ValueError("formal import requires Task evidence")
                signal = _signal_for_item(item, current)
                result = service.record_formal_task(RecordFormalTask(
                    title=str(details["title"]), description=str(details["description"]),
                    signal=signal,
                    formality=FormalityEvidence(
                        basis=FormalTaskBasis(str(details["formal_basis"])),
                        assigner_is_authorized=details["assigner_is_authorized"] is True,
                        deliverable_is_explicit=True, owner_is_explicit=True,
                    ),
                    owner_user_id=str(details["owner_user_id"]),
                    owner_name=str(details["owner_name"]),
                    owner_evidence_json=_canonical({
                        "source_ref": signal.source_ref, "excerpt": details["owner_excerpt"]
                    }),
                ), _db=db)
                endpoint, endpoint_id = "task_id", result.task_id
                formal += 1
            else:
                raise ValueError("unsupported import disposition")
            db.execute(
                f"insert into business_legacy_links ({endpoint}, {column}) values (?, ?)",
                (endpoint_id, item.legacy_row_id),
            )
            created += 1
    return ImportApplyResult(
        created=created, skipped=skipped, formal_tasks=formal,
        official_project_matches=matches, history_only=history_only,
    )
