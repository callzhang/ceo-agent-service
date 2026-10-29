"""Offline projection of adopted OA history from the explicit 28.5 source."""
import sqlite3
import json
from app.action_entity_storage import append_action_receipt, create_action
from app.action_storage import REPLY_ATTEMPT_COMMON_SQL, update_action
from app.reply_receipt_reference import add_reference
from app.oa_history_storage import _insert_fact, list_recorded_oa_results

def load_oa_history_source(db):
    """Load exact Attempts and adopted logical-run results without opening a Store."""
    from app.store import AutoReplyStore, ReplyAttempt
    from app.schema_sources.oa_history_28_5 import _is_approval_attempt
    previous = db.row_factory
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute(f"select * from ({REPLY_ATTEMPT_COMMON_SQL})").fetchall()
        attempts = [attempt for row in rows
                    if _is_approval_attempt(attempt := ReplyAttempt.model_validate(dict(row)))]
        ids = list(dict.fromkeys(a.agent_run_id for a in attempts if a.agent_run_id))
        summaries = {}
        for start in range(0, len(ids), 400):
            batch = ids[start:start + 400]
            placeholders = ','.join('?' for _ in batch)
            runs = db.execute(f'''with terminal_runs as (
                select id as terminal_id, task_id, execution_generation from runs
                where id in ({placeholders}) and role in ('consumer','audit'))
                select terminal_runs.terminal_id, runs.*,
                    case runs.role when 'consumer' then candidate.candidate_json
                    when 'audit' then review.result_json end as adopted_result_json
                from terminal_runs join runs on runs.task_id=terminal_runs.task_id
                    and runs.execution_generation=terminal_runs.execution_generation
                left join run_adopted_results candidate on candidate.consumer_run_id=runs.id
                left join run_adopted_reviews review on review.audit_run_id=runs.id
                order by terminal_runs.terminal_id, runs.proposal_revision,
                    case runs.role when 'consumer' then 0 else 1 end, runs.turn_attempt, runs.id''', batch).fetchall()
            for row in runs:
                summaries.setdefault(row['terminal_id'], []).append(AutoReplyStore._agent_run_from_row(row, db=db, load_events=False))
        return attempts, {a.id: summaries.get(a.agent_run_id, []) for a in attempts}
    finally:
        db.row_factory = previous


def _historical_quality_projection(db, raw):
    if not raw or not db.execute('select json_valid(?)', (raw,)).fetchone()[0]:
        return '{"success":null,"taskStatus":""}'
    sql = "select json_object('success',coalesce(json_extract(?,'$.success'),json_extract(?,'$.result.success'),json_extract(?,'$.dws_action_result.success')),'taskStatus',coalesce(json_extract(?,'$.taskStatus'),json_extract(?,'$.result.taskStatus'),''))"
    return db.execute(sql, (raw,) * 5).fetchone()[0]


def normalize_oa_history(db):
    """Normalize the adopted 28.5 OA evidence in the caller's transaction."""
    from app.schema_sources import oa_history_28_5 as source
    attempts, runs_by_attempt = load_oa_history_source(db)
    counts = {'attempts': len(attempts), 'bodies': 0, 'facts': 0}
    # A reference takes precedence in the source projection. Check the hidden
    # inline value before changing anything so a distinct source body is never lost.
    ids = [attempt.id for attempt in attempts]
    for start in range(0, len(ids), 400):
        batch = ids[start:start + 400]
        placeholders = ','.join('?' for _ in batch)
        conflict = db.execute(f'''select cast(a.legacy_key as integer)
            from actions a join reply_attempt_receipt_refs ref on ref.attempt_id=a.id
            join action_receipts body on body.id=ref.receipt_id
            where a.record_type='reply_attempts' and cast(a.legacy_key as integer) in ({placeholders})
              and a.attempt_oa_action_result_json is not null
              and a.attempt_oa_action_result_json is not ''
              and not (typeof(a.attempt_oa_action_result_json)=typeof(body.provider_result_json)
                       and a.attempt_oa_action_result_json is body.provider_result_json)
            limit 1''', batch).fetchone()
        if conflict is not None:
            raise ValueError(f"OA Attempt {conflict[0]} has conflicting inline and referenced bodies")
    operations = {'approved': 'approve', 'rejected': 'reject', 'returned': 'revert_task', 'commented_pending': 'comment'}
    recorded = list_recorded_oa_results(db, [attempt.id for attempt in attempts])
    recorded_by_attempt = {}
    for fact in recorded:
        recorded_by_attempt.setdefault(fact["attempt_id"], []).append(fact)
    for attempt in attempts:
        # An exact already-projected fact is authoritative. Re-decoding its body
        # as 28.5 would change the meaning of bodies written by current code.
        already_projected = False
        for fact in recorded_by_attempt.get(attempt.id, ()):
            try:
                target = json.loads(fact["target"] or "{}")
                outcome = json.loads(fact["outcome_json"] or "{}")
            except (TypeError, ValueError):
                continue
            if (isinstance(target, dict) and isinstance(outcome, dict)
                and target == {"process_instance_id": attempt.oa_process_instance_id,
                               "task_id": attempt.oa_task_id}
                and "success" in outcome and "taskStatus" in outcome):
                already_projected = True
                break
        if already_projected:
            if attempt.oa_action_result_json:
                update_action(db, 'reply_attempts', attempt.id, {'oa_action_result_json': ''})
            continue
        runs = runs_by_attempt[attempt.id]
        consumers = [r for r in runs if r.role is source.AgentRole.CONSUMER]
        structured = source._confirmed_structured_results(attempt, runs, {r.id:r for r in consumers}, source._consumer_results(consumers))
        results = structured | source._direct_results(attempt)
        raw = attempt.oa_action_result_json
        # Empty commented status remains a workflow fact, not a synthetic receipt.
        if not structured and (not raw or (not raw.strip() and source._normalize(attempt.send_status) == "commented")):
            continue
        if not raw and not results:
            continue
        ref = db.execute("select r.receipt_id from reply_attempt_receipt_refs r join actions a on a.id=r.attempt_id where a.record_type='reply_attempts' and cast(a.legacy_key as integer)=?", (attempt.id,)).fetchone()
        if ref:
            receipt_id = ref[0]
            existing = db.execute('select provider_result_json from action_receipts where id=?', (receipt_id,)).fetchone()
            if existing is None or existing[0] != raw:
                raise ValueError('existing OA reference does not match Attempt body')
        else:
            action = create_action(db, values={'operation': attempt.oa_action or None, 'status': 'done' if structured or (results and raw.strip()) else None, 'created_at': attempt.created_at, 'updated_at': attempt.updated_at})
            receipt_id = append_action_receipt(db, action['id'], values={'provider_result_json': raw})
            add_reference(db, attempt_id=attempt.id, receipt_id=receipt_id, expected_body=raw)
            counts['bodies'] += 1
        if raw:
            update_action(db, 'reply_attempts', attempt.id, {'oa_action_result_json': ''})
        outcome = _historical_quality_projection(db, raw)
        for result in sorted(results, key=str):
            # Empty-body commented is already represented by workflow status.
            if not raw.strip() and result == source.ApprovalHistoryResult.COMMENTED_PENDING and result not in structured:
                continue
            counts['facts'] += _insert_fact(db, attempt_id=attempt.id, receipt_id=receipt_id, raw=raw, process_id=attempt.oa_process_instance_id, task_id=attempt.oa_task_id, operation=operations[str(result)], status='done', outcome=outcome)
        if not results and raw:
            counts['facts'] += _insert_fact(db, attempt_id=attempt.id, receipt_id=receipt_id, raw=raw, process_id=attempt.oa_process_instance_id, task_id=attempt.oa_task_id, operation=None, status=None, outcome=outcome)
    return counts
