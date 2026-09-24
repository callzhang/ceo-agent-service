"""Pure evidence rules for Task-first promotion and identity decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.task_semantic_models import (
    BusinessTaskStage,
    CommitmentStatus,
    FormalTaskBasis,
)


@dataclass(frozen=True)
class FormalityEvidence:
    basis: FormalTaskBasis | None
    assigner_is_authorized: bool
    deliverable_is_explicit: bool
    owner_is_explicit: bool


@dataclass(frozen=True)
class FormalityResolution:
    stage: BusinessTaskStage
    commitment_status: CommitmentStatus
    missing_evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class IdentityEvidence:
    same_external_task_id: bool = False
    explicit_source_reference: bool = False
    same_deliverable: bool = False
    same_owner: bool = False
    same_context: bool = False
    compatible_time_window: bool = False


def resolve_formality(evidence: FormalityEvidence) -> FormalityResolution:
    """Resolve only structured authority and deliverable evidence.

    A candidate is the absence of a formal basis. A basis with insufficient
    evidence is not a candidate fallback: the caller must retain or repair the
    source evidence rather than silently promoting it.
    """
    if evidence.basis is None:
        return FormalityResolution(
            stage=BusinessTaskStage.CANDIDATE,
            commitment_status=CommitmentStatus.NONE,
        )
    if not evidence.deliverable_is_explicit:
        raise ValueError("cannot promote an implicit deliverable")
    if not evidence.owner_is_explicit:
        raise ValueError("cannot promote a task without an explicit owner")
    if (
        evidence.basis is FormalTaskBasis.EXPLICIT_ASSIGNMENT
        and not evidence.assigner_is_authorized
    ):
        raise ValueError("cannot promote an unauthorized assignment")

    match evidence.basis:
        case FormalTaskBasis.EXPLICIT_COMMITMENT:
            commitment_status = CommitmentStatus.ACCEPTED
        case FormalTaskBasis.EXPLICIT_ASSIGNMENT | FormalTaskBasis.EXTERNAL_TODO | FormalTaskBasis.MEETING_ACTION_ITEM:
            commitment_status = CommitmentStatus.ASSIGNED_UNACCEPTED
        case _:
            raise AssertionError(f"unhandled formal task basis: {evidence.basis}")

    return FormalityResolution(
        stage=BusinessTaskStage.FORMAL,
        commitment_status=commitment_status,
        missing_evidence=(),
    )


def resolve_identity(evidence: IdentityEvidence) -> Literal["merge", "link", "separate"]:
    if evidence.same_external_task_id or evidence.explicit_source_reference:
        return "merge"
    if (
        evidence.same_deliverable
        and evidence.same_owner
        and evidence.same_context
        and evidence.compatible_time_window
    ):
        return "merge"
    if evidence.same_deliverable or evidence.same_context:
        return "link"
    return "separate"
