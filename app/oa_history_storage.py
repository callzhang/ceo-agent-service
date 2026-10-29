"""Explicit historical OA fact normalization and current fact access."""
import json

from app.action_entity_storage import append_action_receipt


def _quality_projection(db, raw):
    if not raw or not db.execute('select json_valid(?)', (raw,)).fetchone()[0]:
        return '{"success":null,"taskStatus":""}'
    return db.execute("select json_object('success',json_extract(?,'$.success'),'taskStatus',coalesce(json_extract(?,'$.taskStatus'),''))", (raw, raw)).fetchone()[0]


def _insert_fact(db, *, attempt_id, receipt_id, raw, process_id, task_id, operation, status, outcome):
    body = db.execute('select action_id,provider_result_json from action_receipts where id=?', (receipt_id,)).fetchone()
    if body is None or body[1] != raw:
        raise ValueError('OA fact receipt body does not match')
    ref = db.execute('select r.receipt_id from reply_attempt_receipt_refs r join actions a on a.id=r.attempt_id where a.record_type=\'reply_attempts\' and cast(a.legacy_key as integer)=?', (attempt_id,)).fetchone()
    if ref is None or ref[0] != receipt_id:
        raise ValueError('OA fact requires the exact Attempt receipt reference')
    target = json.dumps({'process_instance_id': process_id, 'task_id': task_id}, ensure_ascii=False, separators=(',', ':'))
    existing = db.execute('''select id from action_receipts where action_id=? and provider_receipt_id=?
        and provider_result_json is null and agent_run_id is null and execution_run_id is null
        and action_index is null and provider_result_id is null and operation is ? and target is ?
        and status is ? and outcome_json is ?''', (body[0], receipt_id, operation, target, status, outcome)).fetchone()
    if existing is None:
        append_action_receipt(db, body[0], values={'provider_receipt_id': receipt_id, 'operation': operation, 'target': target, 'status': status, 'outcome_json': outcome})
        return 1
    return 0


def record_current_oa_facts(db, *, attempt_id, receipt_id, expected_body, process_instance_id, task_id):
    return _insert_fact(db, attempt_id=attempt_id, receipt_id=receipt_id, raw=expected_body, process_id=process_instance_id, task_id=task_id, operation=None, status=None, outcome=_quality_projection(db, expected_body))


def list_recorded_oa_results(db, attempt_ids):
    ids = list(dict.fromkeys(attempt_ids))
    result = []
    for start in range(0, len(ids), 400):
        batch = ids[start:start + 400]
        placeholders = ','.join('?' for _ in batch)
        cursor = db.execute(f'''select cast(a.legacy_key as integer) as attempt_id, metadata.operation, metadata.target, metadata.status, metadata.outcome_json
            from actions a join reply_attempt_receipt_refs ref on ref.attempt_id=a.id
            join action_receipts body on body.id=ref.receipt_id
            join action_receipts metadata on metadata.action_id=body.action_id and metadata.provider_receipt_id=body.id
            where a.record_type='reply_attempts' and cast(a.legacy_key as integer) in ({placeholders})
              and body.provider_result_json is not null and metadata.provider_result_json is null
              and metadata.agent_run_id is null and metadata.execution_run_id is null and metadata.action_index is null
              and metadata.provider_result_id is null
              and case when json_valid(metadata.outcome_json) then
                    json_type(metadata.outcome_json)='object'
                    and json_type(metadata.outcome_json, '$.success') is not null
                    and json_type(metadata.outcome_json, '$.taskStatus') is not null
                  else 0 end''', batch)
        names = [d[0] for d in cursor.description]
        result.extend(dict(zip(names, row)) for row in cursor.fetchall())
    return result


