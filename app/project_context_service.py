"""Transactional persistence for source-backed official Project context."""

from __future__ import annotations

import json
import sqlite3

from app.store import AutoReplyStore
from app.task_semantic_models import ProjectContext, SourceCitation


def context_changed(previous_json: str | None, current_json: str) -> bool:
    if previous_json is None:
        return True
    return json.loads(previous_json) != json.loads(current_json)


class ProjectContextService:
    def __init__(self, store: AutoReplyStore) -> None:
        self.store = store

    @staticmethod
    def _citations(context: ProjectContext) -> tuple[SourceCitation, ...]:
        values: list[SourceCitation] = []
        if context.overall_owner is not None:
            values.extend(context.overall_owner.evidence)
        for responsibility in context.responsibilities:
            values.extend(responsibility.evidence)
        for fact in context.facts:
            values.extend(fact.evidence)
        return tuple(values)

    @staticmethod
    def _require_project(project_id: int, db: sqlite3.Connection) -> None:
        if db.execute("select 1 from business_projects where id=?", (project_id,)).fetchone() is None:
            raise ValueError(f"official project {project_id} does not exist")

    @staticmethod
    def _require_signal(signal_id: int, db: sqlite3.Connection) -> sqlite3.Row:
        row = db.execute(
            "select signal.source_ref, document.body as evidence_text "
            "from business_task_signals signal "
            "join business_source_documents document on document.id=signal.source_document_id "
            "where signal.id=?", (signal_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"evidence signal {signal_id} does not exist")
        return row

    def apply(
        self, *, project_id: int, context: ProjectContext | None,
        signal_ids: tuple[int, ...] | list[int], db: sqlite3.Connection,
    ) -> int | None:
        """Use the caller's transaction; never commits or creates external work."""
        self._require_project(project_id, db)
        ids = tuple(signal_ids)
        if len(ids) != len(set(ids)):
            raise ValueError("project evidence signal IDs must be unique")
        signal_rows = {signal_id: self._require_signal(signal_id, db) for signal_id in ids}
        if context is not None:
            for citation in self._citations(context):
                if citation.signal_id is None:
                    raise ValueError("project citation must resolve to an actual signal ID")
                row = self._require_signal(citation.signal_id, db)
                if citation.signal_id not in signal_rows:
                    raise ValueError("project citation signal must be included in project evidence")
                if citation.source_ref != row["source_ref"] or citation.source_excerpt not in row["evidence_text"]:
                    raise ValueError("project citation must faithfully match its source")
        for signal_id in ids:
            db.execute(
                "insert into business_project_evidence (project_id, signal_id) values (?, ?) "
                "on conflict(project_id, signal_id) do nothing", (project_id, signal_id),
            )
        if context is None:
            return None
        current_json = json.dumps(
            context.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        previous = db.execute(
            "select context_json from business_project_context_revisions "
            "where project_id=? order by id desc limit 1", (project_id,)
        ).fetchone()
        if not context_changed(str(previous["context_json"]) if previous else None, current_json):
            return None
        evidence_json = json.dumps(sorted(ids), separators=(",", ":"))
        return int(db.execute(
            "insert into business_project_context_revisions (project_id, context_json, evidence_json) "
            "values (?, ?, ?)", (project_id, current_json, evidence_json),
        ).lastrowid)
