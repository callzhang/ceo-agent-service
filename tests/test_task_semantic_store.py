from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pytest

from app.store import AutoReplyStore


NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


def test_schema_creates_task_first_business_tables(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "semantic.sqlite3")

    with sqlite3.connect(store.path) as db:
        tables = {
            row[0]
            for row in db.execute("select name from sqlite_master where type='table'")
        }

    assert {
        "business_task_signals",
        "business_tasks",
        "business_task_evidence",
        "business_task_events",
        "business_task_relations",
        "business_work_clusters",
        "business_work_cluster_tasks",
        "business_anchors",
        "business_task_anchor_links",
        "business_projects",
        "business_project_candidates",
        "business_attention_items",
        "business_attention_tasks",
        "business_attention_events",
        "business_legacy_links",
    } <= tables


def test_formal_task_can_be_retrieved_without_a_project_link(tmp_path: Path) -> None:
    from app.task_semantic_models import (
        BusinessCommitmentStatus,
        BusinessEvidenceKind,
        BusinessFormalBasis,
        BusinessRelevance,
        BusinessSignalKind,
        BusinessSignalSource,
        BusinessTaskStage,
        BusinessTaskStatus,
    )

    store = AutoReplyStore(tmp_path / "semantic.sqlite3")
    signal = store.create_business_task_signal(
        source=BusinessSignalSource.DINGTALK_MESSAGE,
        source_ref="message-123",
        kind=BusinessSignalKind.ASSIGNMENT,
        summary="Derek assigned the weekly customer review.",
        observed_at=NOW,
    )
    task = store.create_business_task(
        title="Complete the weekly customer review",
        stage=BusinessTaskStage.FORMAL,
        status=BusinessTaskStatus.OPEN,
        commitment_status=BusinessCommitmentStatus.ASSIGNED_UNACCEPTED,
        formal_basis=BusinessFormalBasis.EXPLICIT_ASSIGNMENT,
        business_relevance=BusinessRelevance.HIGH,
        source_signal_id=signal.id,
        now=NOW,
    )
    evidence = store.link_business_task_evidence(
        task_id=task.id,
        kind=BusinessEvidenceKind.ASSIGNMENT,
        source_signal_id=signal.id,
        summary="Assignment recorded in message-123.",
        observed_at=NOW,
    )

    assert store.get_business_task(task.id) == task
    assert store.list_business_tasks() == (task,)
    assert store.list_business_task_signals() == (signal,)
    assert store.list_business_task_evidence(task.id) == (evidence,)
    assert task.project_id is None


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"stage": "formal"}, "formal_basis"),
        (
            {
                "stage": "candidate",
                "formal_basis": "explicit_assignment",
            },
            "candidate",
        ),
        ({"status": "merged"}, "merged_into_task_id"),
        (
            {
                "status": "open",
                "merged_into_task_id": 7,
            },
            "only merged",
        ),
    ],
)
def test_task_model_enforces_stage_and_merge_invariants(
    kwargs: dict[str, object], message: str
) -> None:
    from pydantic import ValidationError

    from app.task_semantic_models import (
        BusinessCommitmentStatus,
        BusinessRelevance,
        BusinessTask,
        BusinessTaskStage,
        BusinessTaskStatus,
    )

    values: dict[str, object] = {
        "id": 1,
        "title": "Review launch metrics",
        "stage": BusinessTaskStage.CANDIDATE,
        "status": BusinessTaskStatus.OPEN,
        "commitment_status": BusinessCommitmentStatus.UNCOMMITTED,
        "formal_basis": None,
        "business_relevance": BusinessRelevance.MEDIUM,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(kwargs)

    with pytest.raises(ValidationError, match=message):
        BusinessTask(**values)


def test_sqlite_enforces_task_stage_merge_and_foreign_key_invariants(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "semantic.sqlite3")

    with store._connect() as db, pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            insert into business_tasks (
                title, stage, status, commitment_status, formal_basis,
                business_relevance, created_at, updated_at
            ) values ('Unfounded formal task', 'formal', 'open', 'uncommitted',
                      null, 'low', '2026-09-22T12:00:00+00:00',
                      '2026-09-22T12:00:00+00:00')
            """
        )

    with store._connect() as db, pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            insert into business_tasks (
                title, stage, status, commitment_status, formal_basis,
                business_relevance, created_at, updated_at
            ) values ('Invalid candidate', 'candidate', 'open', 'uncommitted',
                      'explicit_assignment', 'low', '2026-09-22T12:00:00+00:00',
                      '2026-09-22T12:00:00+00:00')
            """
        )

    with store._connect() as db, pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            insert into business_tasks (
                title, stage, status, commitment_status, formal_basis,
                business_relevance, created_at, updated_at
            ) values ('Missing merge target', 'candidate', 'merged', 'uncommitted',
                      null, 'low', '2026-09-22T12:00:00+00:00',
                      '2026-09-22T12:00:00+00:00')
            """
        )

    with store._connect() as db, pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            insert into business_task_evidence (
                task_id, kind, summary, observed_at
            ) values (404, 'assignment', 'Missing task', '2026-09-22T12:00:00+00:00')
            """
        )
