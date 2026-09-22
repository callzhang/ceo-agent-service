from __future__ import annotations

import pytest

from app.task_semantic_models import (
    BusinessTaskStage,
    CommitmentStatus,
    FormalTaskBasis,
)
from app.task_semantic_rules import (
    FormalityEvidence,
    IdentityEvidence,
    resolve_formality,
    resolve_identity,
)


@pytest.mark.parametrize(
    ("basis", "expected_stage", "expected_commitment"),
    [
        (FormalTaskBasis.EXPLICIT_COMMITMENT, BusinessTaskStage.FORMAL, CommitmentStatus.ACCEPTED),
        (
            FormalTaskBasis.EXPLICIT_ASSIGNMENT,
            BusinessTaskStage.FORMAL,
            CommitmentStatus.ASSIGNED_UNACCEPTED,
        ),
        (FormalTaskBasis.EXTERNAL_TODO, BusinessTaskStage.FORMAL, CommitmentStatus.ACCEPTED),
        (
            FormalTaskBasis.MEETING_ACTION_ITEM,
            BusinessTaskStage.FORMAL,
            CommitmentStatus.ASSIGNED_UNACCEPTED,
        ),
        (None, BusinessTaskStage.CANDIDATE, CommitmentStatus.NONE),
    ],
)
def test_promotion_table(basis, expected_stage, expected_commitment):
    resolution = resolve_formality(
        FormalityEvidence(
            basis=basis,
            assigner_is_authorized=True,
            deliverable_is_explicit=True,
            owner_is_explicit=True,
        )
    )

    assert resolution.stage is expected_stage
    assert resolution.commitment_status is expected_commitment
    assert resolution.missing_evidence == ()


@pytest.mark.parametrize(
    "evidence",
    [
        FormalityEvidence(
            basis=FormalTaskBasis.EXPLICIT_ASSIGNMENT,
            assigner_is_authorized=False,
            deliverable_is_explicit=True,
            owner_is_explicit=True,
        ),
        FormalityEvidence(
            basis=FormalTaskBasis.EXPLICIT_ASSIGNMENT,
            assigner_is_authorized=True,
            deliverable_is_explicit=False,
            owner_is_explicit=True,
        ),
        FormalityEvidence(
            basis=FormalTaskBasis.MEETING_ACTION_ITEM,
            assigner_is_authorized=False,
            deliverable_is_explicit=False,
            owner_is_explicit=True,
        ),
    ],
)
def test_unauthorized_assignment_or_implicit_deliverable_is_not_promoted(evidence):
    with pytest.raises(ValueError, match="cannot promote"):
        resolve_formality(evidence)


def test_meeting_action_item_allows_unresolved_owner_and_exposes_missing_owner_evidence():
    resolution = resolve_formality(
        FormalityEvidence(
            basis=FormalTaskBasis.MEETING_ACTION_ITEM,
            assigner_is_authorized=False,
            deliverable_is_explicit=True,
            owner_is_explicit=False,
        )
    )

    assert resolution.stage is BusinessTaskStage.FORMAL
    assert resolution.commitment_status is CommitmentStatus.ASSIGNED_UNACCEPTED
    assert resolution.missing_evidence == ("owner",)


def test_shared_goal_returns_link_not_merge():
    assert resolve_identity(IdentityEvidence(same_context=True)) == "link"


def test_same_external_todo_returns_merge():
    assert resolve_identity(IdentityEvidence(same_external_task_id=True)) == "merge"


def test_uncertain_identity_returns_link():
    assert resolve_identity(IdentityEvidence(same_deliverable=True)) == "link"


def test_only_full_conjunction_of_intrinsic_identity_evidence_merges():
    assert resolve_identity(
        IdentityEvidence(
            same_deliverable=True,
            same_owner=True,
            same_context=True,
            compatible_time_window=True,
        )
    ) == "merge"
    assert resolve_identity(IdentityEvidence(same_owner=True)) == "separate"
