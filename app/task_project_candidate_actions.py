"""Explicit console actions for turning a project candidate into a Project."""

from __future__ import annotations

from app.store import AutoReplyStore
from app.task_business_resolution import BusinessResolutionService
from app.task_semantic_models import BusinessAnchorType, BusinessRelationStatus


class ProjectCandidateActionNotApplicable(Exception):
    """The candidate is not in a state where the requested action is valid."""


def confirm_project_candidate(
    store: AutoReplyStore,
    candidate_id: int,
    *,
    project_id: int | None = None,
) -> int:
    """Confirm a candidate as a new official Project or merge it into one.

    The source signal is selected from the candidate's own cluster, never
    fabricated by the console. Repeating the same confirmation is idempotent;
    a different target Project is rejected.
    """
    resolver = BusinessResolutionService(store)
    with store.business_task_transaction() as db:
        candidate = store.get_business_project_candidate_in_transaction(
            candidate_id=candidate_id, _db=db
        )
        if candidate is None:
            raise LookupError(f"business project candidate {candidate_id} not found")
        if candidate.status is BusinessRelationStatus.CONFIRMED:
            if project_id is not None and candidate.confirmed_project_id != project_id:
                raise ProjectCandidateActionNotApplicable("这个候选项目已经确认到另一个正式项目")
            assert candidate.confirmed_project_id is not None
            return candidate.confirmed_project_id
        if candidate.status is not BusinessRelationStatus.PROPOSED:
            raise ProjectCandidateActionNotApplicable("只有待确认的项目线索可以确认")
        signal_row = db.execute(
            """
            select evidence.signal_id
            from business_work_cluster_tasks as membership
            join business_task_evidence as evidence
              on evidence.task_id = membership.task_id
            where membership.cluster_id=?
            order by evidence.created_at, evidence.signal_id
            limit 1
            """,
            (candidate.cluster_id,),
        ).fetchone()
        if signal_row is None:
            raise ValueError("项目候选缺少可引用的来源证据")
        signal_id = int(signal_row["signal_id"])
        if project_id is None:
            anchor_id = resolver.register_anchor(
                anchor_type=BusinessAnchorType.PROJECT,
                anchor_ref=f"console:project-candidate:{candidate_id}",
                title=candidate.title,
                _db=db,
            )
            project_id = resolver.register_official_project(
                anchor_id=anchor_id,
                registry_source=f"console_confirmed_candidate:{candidate_id}",
                _db=db,
            )
        resolver.confirm_project_candidate(
            candidate_id=candidate_id,
            project_id=project_id,
            evidence_signal_id=signal_id,
            _db=db,
        )
        return project_id
