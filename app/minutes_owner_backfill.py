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
under a source reference of its own (the digest covers the action items, the
transcript excerpts and the meeting summary), so running this twice queues nothing
the second time.
"""
from __future__ import annotations
from app.provenance_storage import signal_projection
from app.business_definitions import BUSINESS_PROJECTIONS
from app.action_business_definitions import business_action_projection

from dataclasses import dataclass, field
from typing import Any

from app.minutes_todo_context import minutes_full_summary, todo_transcript_excerpts
from app.store import AutoReplyStore, WORK_SUMMARY_INPUT_SQL
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
# attempt (2026-09-25); 2 followed the sentence split and the per-item owner check; 3 the citation
# rules (extracts allowed, earlier evidence citable); 4 added the meeting's own DingTalk summary
# (Derek 2026-09-28: a transcript-window miss is not proof the source has no owner — read the
# summary too before concluding that).
BACKFILL_REVISION = 4


@dataclass
class OwnerBackfillResult:
    dry_run: bool
    inspected: int = 0
    queued: int = 0
    decisions: list[dict[str, Any]] = field(default_factory=list)


def ownerless_candidate_meetings(store: AutoReplyStore) -> list[tuple[str, list[int]]]:
    """Exact meeting IDs and ownerless candidate Task IDs, newest first."""
    with store._connect() as db:
        rows = db.execute(
            f'select s.id, s.source_ref, t.id\n               from ({BUSINESS_PROJECTIONS["task"]}) t\n               join ({business_action_projection("business_task_evidence")}) e on e.task_id = t.id and e.evidence_role = \'discovery\'\n               join ({signal_projection()}) s on s.id = e.signal_id\n               where t.stage = \'candidate\' and t.status in (\'open\', \'waiting\')\n                 and t.owner_name = \'\' and t.owner_user_id = \'\' and s.source_type = \'ai_minutes\'\n               order by s.id desc'
        ).fetchall()
    meetings: dict[str, list[int]] = {}
    for _signal_id, source_ref, task_id in rows:
        minutes_id = source_ref.split("#", 1)[0]
        meetings.setdefault(minutes_id, []).append(int(task_id))
    return [(minutes_id, sorted(set(ids))) for minutes_id, ids in meetings.items()]


def backfill_minutes_owners(
    store: AutoReplyStore, dws, *, dry_run: bool = True, limit: int | None = None
) -> OwnerBackfillResult:
    result = OwnerBackfillResult(dry_run=dry_run)
    for minutes_id, task_ids in ownerless_candidate_meetings(store)[:limit]:
        result.inspected += 1
        decision: dict[str, Any] = {"minutes_id": minutes_id, "task_ids": task_ids}
        result.decisions.append(decision)
        info = dws.get_minutes_info(minutes_id)
        record = info.get("result") if isinstance(info, dict) else None
        if not isinstance(record, dict) or _minutes_id(record) != minutes_id:
            decision["outcome"] = "skipped: the current meeting record does not match its ID"
            continue
        todos_payload = dws.get_minutes_todos(minutes_id)
        actions = _minutes_todo_actions(todos_payload)
        if not actions:
            decision["outcome"] = "skipped: the meeting has no action items now"
            continue
        excerpts = todo_transcript_excerpts(todos_payload, dws.get_all_minutes_transcription(minutes_id)["paragraphs"])
        summary = minutes_full_summary(dws.get_minutes_summary(minutes_id))
        decision["located_items"] = len(excerpts)
        decision["has_summary"] = bool(summary)
        if not excerpts and not summary:
            decision["outcome"] = "skipped: no action item can be located in the transcript, and no meeting summary either"
            continue
        canonical = _canonical_minutes_todos_payload(
            minutes=record, todos_payload=todos_payload, transcript_excerpts=excerpts, meeting_summary=summary,
        )
        digest = _actions_digest([BACKFILL_REVISION, actions, excerpts, summary])
        item = minutes_work_item(record, minutes_id=minutes_id, digest=digest, canonical=canonical)
        decision["source_ref"] = item.source.ref
        if dry_run:
            decision["outcome"] = "would queue"
            continue
        with store._connect() as db:
            already = db.execute(
                f"select 1 from ({WORK_SUMMARY_INPUT_SQL}) where source_type = ? and source_ref = ?",
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
