"""Evidence-backed grouping, anchor, Project, and relevance commands."""

from __future__ import annotations

from datetime import datetime, timezone
import json

from app.store import AutoReplyStore
from app.task_semantic_models import (
    BusinessAnchor,
    BusinessAnchorType,
    BusinessEvidenceRole,
    BusinessProject,
    BusinessRelationStatus,
    BusinessRelationType,
    BusinessRelevance,
    BusinessTask,
    BusinessTaskEventType,
)


class BusinessResolutionService:
    """Apply explicit business-resolution decisions in atomic transactions."""

    def __init__(self, store: AutoReplyStore) -> None:
        self.store = store

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _snapshot(task: BusinessTask) -> str:
        return json.dumps(
            task.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _require_nonblank(value: str, *, field: str) -> str:
        if not value.strip():
            raise ValueError(f"{field} must not be blank")
        return value

    @staticmethod
    def _require_task(task: BusinessTask | None, task_id: int) -> BusinessTask:
        if task is None:
            raise ValueError(f"business task {task_id} does not exist")
        return task

    @staticmethod
    def _require_anchor(anchor: BusinessAnchor | None, anchor_id: int) -> BusinessAnchor:
        if anchor is None:
            raise ValueError(f"business anchor {anchor_id} does not exist")
        return anchor

    def _require_evidence_signal(self, *, signal_id: int, db) -> None:
        row = db.execute(
            "select 1 from business_task_signals where id=?", (signal_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"evidence signal {signal_id} does not exist")

    def create_cluster(self, *, title: str, task_ids: list[int]) -> int:
        if not task_ids:
            raise ValueError("cluster requires at least one task")
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("cluster task IDs must be unique")
        with self.store.business_task_transaction() as db:
            for task_id in task_ids:
                self._require_task(
                    self.store.get_business_task_in_transaction(task_id=task_id, _db=db),
                    task_id,
                )
            cluster_id = self.store.create_business_work_cluster_in_transaction(
                title=title, _db=db
            )
            for task_id in task_ids:
                self.store.add_business_work_cluster_task_in_transaction(
                    cluster_id=cluster_id, task_id=task_id, _db=db
                )
            return cluster_id

    def add_relation(
        self,
        *,
        from_task_id: int,
        to_task_id: int,
        relation_type: BusinessRelationType | str,
        evidence_signal_id: int,
        status: BusinessRelationStatus | str = BusinessRelationStatus.PROPOSED,
        reason: str = "",
    ) -> int:
        with self.store.business_task_transaction() as db:
            self._require_task(
                self.store.get_business_task_in_transaction(task_id=from_task_id, _db=db),
                from_task_id,
            )
            self._require_task(
                self.store.get_business_task_in_transaction(task_id=to_task_id, _db=db),
                to_task_id,
            )
            self._require_evidence_signal(signal_id=evidence_signal_id, db=db)
            return self.store.create_business_task_relation_in_transaction(
                from_task_id=from_task_id,
                to_task_id=to_task_id,
                relation_type=relation_type,
                status=status,
                supporting_signal_id=evidence_signal_id,
                reason=reason,
                _db=db,
            )

    def register_anchor(
        self,
        *,
        anchor_type: BusinessAnchorType | str,
        anchor_ref: str,
        title: str,
        active: bool = True,
    ) -> int:
        with self.store.business_task_transaction() as db:
            existing = self.store.get_business_anchor_by_identity_in_transaction(
                anchor_type=anchor_type, anchor_ref=anchor_ref, _db=db
            )
            if existing is not None:
                if existing.title != title or existing.active is not active:
                    raise ValueError("registered anchor identity has different attributes")
                return existing.id
            return self.store.create_business_anchor_in_transaction(
                anchor_type=anchor_type,
                anchor_ref=anchor_ref,
                title=title,
                active=active,
                _db=db,
            )

    def register_official_project(
        self, *, anchor_id: int, registry_source: str
    ) -> int:
        self._require_nonblank(registry_source, field="canonical registry source")
        with self.store.business_task_transaction() as db:
            anchor = self._require_anchor(
                self.store.get_business_anchor_in_transaction(anchor_id=anchor_id, _db=db),
                anchor_id,
            )
            if anchor.anchor_type is not BusinessAnchorType.PROJECT:
                raise ValueError("official Project requires a project anchor")
            if not anchor.active:
                raise ValueError("official Project requires an active project anchor")
            existing_row = db.execute(
                "select * from business_projects where canonical_anchor_id=?", (anchor_id,)
            ).fetchone()
            if existing_row is not None:
                return BusinessProject.model_validate(dict(existing_row)).id
            return self.store.create_business_project_in_transaction(
                canonical_anchor_id=anchor.id, title=anchor.title, _db=db
            )

    def propose_anchor_match(
        self,
        *,
        task_id: int,
        anchor_id: int,
        evidence_signal_id: int,
        reason: str = "",
    ) -> int:
        with self.store.business_task_transaction() as db:
            self._require_task(
                self.store.get_business_task_in_transaction(task_id=task_id, _db=db),
                task_id,
            )
            self._require_anchor(
                self.store.get_business_anchor_in_transaction(anchor_id=anchor_id, _db=db),
                anchor_id,
            )
            self._require_evidence_signal(signal_id=evidence_signal_id, db=db)
            existing = self.store.get_business_task_anchor_link_in_transaction(
                task_id=task_id, anchor_id=anchor_id, _db=db
            )
            if (
                existing is not None
                and existing.status is BusinessRelationStatus.CONFIRMED
            ):
                raise ValueError("proposal cannot replace a confirmed anchor match")
            link_id = self.store.create_business_task_anchor_link_in_transaction(
                task_id=task_id,
                anchor_id=anchor_id,
                status=BusinessRelationStatus.PROPOSED,
                active=True,
                evidence_signal_id=evidence_signal_id,
                reason=reason,
                _db=db,
            )
            return link_id

    def confirm_anchor_match(
        self,
        *,
        task_id: int,
        anchor_id: int,
        evidence_signal_id: int,
        reason: str = "Business relevance confirmed from registered anchor evidence.",
        relevance: BusinessRelevance | str = BusinessRelevance.RELEVANT,
    ) -> int:
        selected_relevance = BusinessRelevance(relevance)
        if selected_relevance is BusinessRelevance.UNKNOWN:
            raise ValueError("anchor confirmation must decide relevant or not_relevant")
        with self.store.business_task_transaction() as db:
            task = self._require_task(
                self.store.get_business_task_in_transaction(task_id=task_id, _db=db),
                task_id,
            )
            self._require_anchor(
                self.store.get_business_anchor_in_transaction(anchor_id=anchor_id, _db=db),
                anchor_id,
            )
            self._require_evidence_signal(signal_id=evidence_signal_id, db=db)
            self.store.create_business_task_anchor_link_in_transaction(
                task_id=task_id,
                anchor_id=anchor_id,
                status=BusinessRelationStatus.CONFIRMED,
                active=selected_relevance is BusinessRelevance.RELEVANT,
                evidence_signal_id=evidence_signal_id,
                reason=reason,
                _db=db,
            )
            self.store.link_business_task_evidence_in_transaction(
                task_id=task_id,
                signal_id=evidence_signal_id,
                evidence_role=BusinessEvidenceRole.RELEVANCE,
                _db=db,
            )
            derived_relevance = self.store.derive_business_task_relevance_in_transaction(
                task_id=task_id, _db=db
            )
            if derived_relevance is task.business_relevance:
                return task_id
            timestamp = self._timestamp()
            updated = task.model_copy(
                update={
                    "business_relevance": derived_relevance,
                    "updated_at": timestamp,
                    "last_activity_at": timestamp,
                }
            )
            self.store.update_business_task_in_transaction(task=updated, _db=db)
            self.store.append_business_task_event(
                task_id=task_id,
                event_type=BusinessTaskEventType.RELEVANCE_CHANGED,
                signal_id=evidence_signal_id,
                before_json=self._snapshot(task),
                after_json=self._snapshot(updated),
                reason=reason,
                _db=db,
            )
            return task_id

    def propose_project(
        self, *, cluster_id: int, reason: str, title: str | None = None
    ) -> int:
        with self.store.business_task_transaction() as db:
            cluster_row = db.execute(
                "select title from business_work_clusters where id=?", (cluster_id,)
            ).fetchone()
            if cluster_row is None:
                raise ValueError(f"business work cluster {cluster_id} does not exist")
            return self.store.create_business_project_candidate_in_transaction(
                cluster_id=cluster_id,
                title=cluster_row["title"] if title is None else title,
                reason=reason,
                _db=db,
            )

    def confirm_project_candidate(
        self,
        *,
        candidate_id: int,
        project_id: int | None = None,
        anchor_id: int | None = None,
        evidence_signal_id: int | None = None,
    ) -> int:
        if evidence_signal_id is None:
            raise ValueError("project candidate confirmation requires an evidence signal")
        if (project_id is None) == (anchor_id is None):
            raise ValueError("confirmation requires exactly one Project or project anchor")
        with self.store.business_task_transaction() as db:
            candidate = self.store.get_business_project_candidate_in_transaction(
                candidate_id=candidate_id, _db=db
            )
            if candidate is None:
                raise ValueError(f"business project candidate {candidate_id} does not exist")
            self._require_evidence_signal(signal_id=evidence_signal_id, db=db)
            resolved_project_id: int
            if project_id is not None:
                if self.store.get_business_project_in_transaction(
                    project_id=project_id, _db=db
                ) is None:
                    raise ValueError(f"official business project {project_id} does not exist")
                resolved_project_id = project_id
            else:
                assert anchor_id is not None
                anchor = self._require_anchor(
                    self.store.get_business_anchor_in_transaction(
                        anchor_id=anchor_id, _db=db
                    ),
                    anchor_id,
                )
                if anchor.anchor_type is not BusinessAnchorType.PROJECT or not anchor.active:
                    raise ValueError(
                        "explicit Project confirmation requires an active project anchor"
                    )
                existing_row = db.execute(
                    "select id from business_projects where canonical_anchor_id=?",
                    (anchor_id,),
                ).fetchone()
                resolved_project_id = (
                    int(existing_row["id"])
                    if existing_row is not None
                    else self.store.create_business_project_in_transaction(
                        canonical_anchor_id=anchor_id, title=anchor.title, _db=db
                    )
                )
            self.store.confirm_business_project_candidate_in_transaction(
                candidate_id=candidate_id,
                project_id=resolved_project_id,
                confirmation_signal_id=evidence_signal_id,
                _db=db,
            )
            return candidate_id
