"""A candidate Task that Derek sets aside from the console, or takes back.

Derek, 2026-09-25: a candidate is an unconfirmed guess, so he can say "this is
not a task". It goes through the ordinary Task semantic update, so it leaves the
same trace as any other change: a persisted signal authored by him (source type
`console`), a `correction` evidence link and a `status_changed` event. Nothing
is deleted and the original discovery evidence stays. Taking it back is the same
command in the other direction.

Only candidates can be set aside. A formal Task has an owner and an exact source
citation, and leaves `open` through its own evidence, not through this button.
Promoting a candidate is not offered here: promotion needs an identified owner
and an owner excerpt from a source, which a click cannot supply.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from app.store import AutoReplyStore
from app.task_attention_projection import BusinessAttentionProjection
from app.task_semantic_models import BusinessActorKind, BusinessTaskStage, BusinessTaskStatus
from app.task_semantic_service import SourceSignal, TaskSemanticService, UpdateBusinessTask

ACTIONS = ("ignore", "restore")


class CandidateActionNotApplicable(Exception):
    """The Task is not in a state where this action means anything."""


def decide_candidate(store: AutoReplyStore, task_id: int, action: str, *, operator_name: str) -> BusinessTaskStatus:
    if action not in ACTIONS:
        raise ValueError(f"unknown candidate action: {action}")
    task = store.get_business_task(task_id)
    if task is None:
        raise LookupError(f"business task {task_id} not found")
    if task.stage is not BusinessTaskStage.CANDIDATE:
        raise CandidateActionNotApplicable("只有候选任务可以忽略或恢复")
    ignoring = action == "ignore"
    if ignoring and task.status not in (BusinessTaskStatus.OPEN, BusinessTaskStatus.WAITING):
        raise CandidateActionNotApplicable("这个候选任务已经处理过了")
    if not ignoring and task.status is not BusinessTaskStatus.CANCELLED:
        raise CandidateActionNotApplicable("这个候选任务没有被忽略")
    target = BusinessTaskStatus.CANCELLED if ignoring else BusinessTaskStatus.OPEN
    verb = "忽略" if ignoring else "恢复"
    # The prior update time makes a repeated click on the same state a replay, and a click after any other change a new fact.
    reference = f"console:task:{task_id}:{action}:{task.updated_at}"
    TaskSemanticService(store).update_task(UpdateBusinessTask(
        task_id=task_id,
        status=target,
        reason=f"{operator_name}在控制台{verb}了这个候选任务。",
        signal=SourceSignal(
            source_type="console",
            source_ref=reference,
            dedupe_key=reference,
            evidence_text=f"{operator_name}在控制台{verb}了候选任务「{task.title}」",
            source_time=datetime.now(timezone.utc).isoformat(),
            author_name=operator_name,
            author_kind=BusinessActorKind.HUMAN,
            context_json=json.dumps({"action": action}),
        ),
    ))
    BusinessAttentionProjection(store).recompute_for_tasks((task_id,))
    return target
