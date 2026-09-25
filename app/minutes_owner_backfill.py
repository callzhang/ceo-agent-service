"""Give meetings that were queued before their owners were readable a second read.

Derek, 2026-09-25: the scan hands the Task Agent the conversation around each
action item (`app/minutes_todo_context.py`), but a meeting it already queued is
never read again — its action-item digest has not changed. Every candidate Task
found through such a meeting still has no owner. This queues each of those
meetings once more, with the conversation attached, so the Task Agent can update
the existing candidates through its ordinary path (a new source signal, an
`update_fields` transition, an owner excerpt from the transcript).

Which meetings: those with an open candidate Task that has no owner and was
discovered from AI minutes. What is queued is the same Work Item the scan builds,
under a source reference of its own (the digest covers the action items and their
excerpts), so running this twice queues nothing the second time.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.minutes_todo_context import todo_transcript_excerpts
from app.store import AutoReplyStore
from app.task_scanners import (
    _actions_digest,
    _canonical_minutes_todos_payload,
    _minutes_id,
    _minutes_todo_actions,
    minutes_work_item,
)


# Bump when what the Agent is asked to do with the excerpts changes (their format, the prompt's
# owner rules, the checks a decision passes), so meetings whose earlier attempt ended without an
# owner are read again instead of being reported as already queued. Revision 1 was the first
# attempt (2026-09-25); 2 followed the sentence split and the per-item owner check; 3 the citation rules (extracts allowed, earlier evidence citable).
BACKFILL_REVISION = 3


@dataclass
class OwnerBackfillResult:
    dry_run: bool
    inspected: int = 0
    queued: int = 0
    decisions: list[dict[str, Any]] = field(default_factory=list)


def ownerless_candidate_meetings(store: AutoReplyStore) -> list[tuple[str, dict[str, Any], list[int]]]:
    """(minutes id, the meeting record its Tasks were found through, ownerless open candidate Task ids), newest first."""
    with store._connect() as db:
        rows = db.execute(
            """select s.id, s.source_ref, s.evidence_text, t.id
               from business_tasks t
               join business_task_evidence e on e.task_id = t.id and e.evidence_role = 'discovery'
               join business_task_signals s on s.id = e.signal_id
               where t.stage = 'candidate' and t.status in ('open', 'waiting')
                 and t.owner_name = '' and t.owner_user_id = '' and s.source_type = 'ai_minutes'
               order by s.id desc"""
        ).fetchall()
    meetings: dict[str, tuple[dict[str, Any], list[int]]] = {}
    for _signal_id, source_ref, evidence_text, task_id in rows:
        minutes_id = source_ref.split("#", 1)[0]
        if minutes_id not in meetings:
            # The newest signal of a meeting carries the record the scan saw.
            record = json.loads(evidence_text).get("meeting")
            meetings[minutes_id] = (record if isinstance(record, dict) else {}, [])
        meetings[minutes_id][1].append(int(task_id))
    return [(minutes_id, record, sorted(ids)) for minutes_id, (record, ids) in meetings.items()]


def backfill_minutes_owners(
    store: AutoReplyStore, dws, *, dry_run: bool = True, limit: int | None = None
) -> OwnerBackfillResult:
    result = OwnerBackfillResult(dry_run=dry_run)
    for minutes_id, record, task_ids in ownerless_candidate_meetings(store)[:limit]:
        result.inspected += 1
        decision: dict[str, Any] = {"minutes_id": minutes_id, "task_ids": task_ids}
        result.decisions.append(decision)
        if not record or _minutes_id(record) != minutes_id:
            decision["outcome"] = "skipped: the meeting record is not in its stored signal"
            continue
        todos_payload = dws.get_minutes_todos(minutes_id)
        actions = _minutes_todo_actions(todos_payload)
        if not actions:
            decision["outcome"] = "skipped: the meeting has no action items now"
            continue
        excerpts = todo_transcript_excerpts(todos_payload, dws.get_all_minutes_transcription(minutes_id)["paragraphs"])
        decision["located_items"] = len(excerpts)
        if not excerpts:
            decision["outcome"] = "skipped: no action item can be located in the transcript"
            continue
        canonical = _canonical_minutes_todos_payload(
            minutes=record, todos_payload=todos_payload, transcript_excerpts=excerpts
        )
        digest = _actions_digest([BACKFILL_REVISION, actions, excerpts])
        item = minutes_work_item(record, minutes_id=minutes_id, digest=digest, canonical=canonical)
        decision["source_ref"] = item.source.ref
        if dry_run:
            decision["outcome"] = "would queue"
            continue
        with store._connect() as db:
            already = db.execute(
                "select 1 from work_summary_inputs where source_type = ? and source_ref = ?",
                (item.source.type.value, item.source.ref),
            ).fetchone() is not None
        if already:
            decision["outcome"] = "already queued"
            continue
        store.enqueue_work_summary_input(
            source_type=item.source.type.value,
            source_ref=item.source.ref,
            payload_json=item.model_dump_json(),
        )
        decision["outcome"] = "queued"
        result.queued += 1
    return result
