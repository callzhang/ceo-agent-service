"""Operator-scoped, reversible deferral of one existing reply task.

This module deliberately uses native sqlite3. Merely importing or previewing it
never constructs AutoReplyStore and cannot initialize or migrate a database.
"""

from __future__ import annotations
from app.task_lifecycle_storage import claim_projection
from app.task8_storage import register_task8_functions
from app.store import (NATIVE_REPLY_TASK_CURRENT_SQL, NATIVE_AGENT_RUN_SQL, NATIVE_REPLY_BUSINESS_KEY_SQL, NATIVE_REVIEW_CANDIDATE_SQL, NATIVE_CANDIDATE_SELECTION_SQL, NATIVE_CANDIDATE_EXECUTION_SQL)
from app.deferral_receipt_storage import receipt_projection, find_receipt, insert_receipt, update_receipt

import argparse
import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.action_storage import (
    NATIVE_CANDIDATE_ACTION_ATTEMPT_SQL,
    NATIVE_EXTERNAL_ACTION_RESULT_SQL,
    AGENT_EFFECT_INTENT_SQL,
    AGENT_EXECUTION_RECEIPT_SQL,
    SENT_REPLY_SQL,
)


REPLY_TASK_SQL = NATIVE_REPLY_TASK_CURRENT_SQL
RUNTIME_ATTEMPT_SQL = "select id,parent_id as agent_run_id,workload_kind,workload_key,status,first_effect_started_at,lease_owner,lease_expires_at from runtime_attempts"
REVIEW_CANDIDATE_SQL = NATIVE_REVIEW_CANDIDATE_SQL
CANDIDATE_EXECUTION_SQL = NATIVE_CANDIDATE_EXECUTION_SQL
CANDIDATE_SELECTION_SQL = NATIVE_CANDIDATE_SELECTION_SQL
CANDIDATE_ACTION_ATTEMPT_SQL = NATIVE_CANDIDATE_ACTION_ATTEMPT_SQL
EXTERNAL_ACTION_RESULT_SQL = NATIVE_EXTERNAL_ACTION_RESULT_SQL
AGENT_RUN_SQL = NATIVE_AGENT_RUN_SQL




def _time(now: datetime | None) -> datetime:
    value = now or datetime.now(UTC)
    if value.tzinfo is None:
        raise ValueError('time must include a timezone')
    return value.astimezone(UTC).replace(microsecond=0)


def _stamp(value: datetime) -> str:
    return value.strftime('%Y-%m-%d %H:%M:%S')


def _lease_active(value: str, now: datetime) -> bool:
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return True  # An unparseable nonempty owner lease is not proof of expiry.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC) > now


@contextmanager
def _connect(path: Path | str, *, readonly: bool):
    resolved = Path(path).expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise ValueError('database is not a file')
    if readonly:
        db = sqlite3.connect(resolved.as_uri() + '?mode=ro', uri=True, timeout=5)
        db.execute('pragma query_only=on')
    else:
        db = sqlite3.connect(resolved, timeout=5, isolation_level=None)
        register_task8_functions(db)
    db.row_factory = sqlite3.Row
    try:
        yield db
    finally:
        db.close()


def _fingerprint(run: sqlite3.Row) -> str:
    fields = {name: run[name] for name in (
        'id', 'reply_task_id', 'execution_generation', 'role', 'proposal_revision',
        'turn_attempt', 'status', 'structured_error_json',
    )}
    fields['final_result_json'] = ''
    return hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _legacy_event_may_have_effect(event_json: str) -> bool:
    try:
        event = json.loads(event_json)
    except (TypeError, ValueError):
        return True
    if not isinstance(event, dict):
        return True
    item = event.get('item')
    metadata = item.get('metadata') if isinstance(item, dict) else None
    return isinstance(metadata, dict) and metadata.get('effect') in ('effectful', 'unreviewed')


def _snapshot(
    db: sqlite3.Connection, task_id: int, now: datetime, *,
    ignored_receipt_id: int | None = None,
) -> dict[str, Any]:
    task = db.execute(f'select * from ({REPLY_TASK_SQL}) where id=?', (task_id,)).fetchone()
    if task is None:
        raise ValueError('reply task does not exist')
    run = db.execute(f"""select * from ({AGENT_RUN_SQL}) as agent_runs where reply_task_id=?
        order by id desc limit 1""", (task_id,)).fetchone()
    blocked: list[str] = []
    active = db.execute('select id from (' + receipt_projection() + ") where task_id=? and status='paused'", (task_id,)).fetchall()
    if any(row['id'] != ignored_receipt_id for row in active):
        blocked.append('already_paused')
    current = db.execute(f"select reply_task_id from ({NATIVE_REPLY_BUSINESS_KEY_SQL}) where business_object_key=?",
                         (task['business_object_key'],)).fetchone()
    if current is None or current[0] != task_id:
        blocked.append('business_object_binding_changed')
    if task['status'] not in ('pending', 'processing'):
        blocked.append('task_not_reclaimable')
    if run is None or (run['role'], run['status'], run['execution_generation']) != (
        'consumer', 'failed', task['execution_generation'],
    ):
        blocked.append('latest_run_not_failed_consumer')
    if db.execute(f"select 1 from ({AGENT_RUN_SQL}) as agent_runs where reply_task_id=? and status in ('pending','running') limit 1", (task_id,)).fetchone():
        blocked.append('active_agent_run')
    role_leases = db.execute(f"""select lease_expires_at from ({AGENT_RUN_SQL}) as agent_runs where reply_task_id=?
        and lease_owner<>''""", (task_id,)).fetchall()
    if any(_lease_active(row['lease_expires_at'], now) for row in role_leases):
        blocked.append('active_role_lease')
    if db.execute(f"""select 1 from ({RUNTIME_ATTEMPT_SQL}) a
        join ({AGENT_RUN_SQL}) r on r.id=a.agent_run_id
        where r.reply_task_id=? and a.status in ('starting','running') limit 1""", (task_id,)).fetchone():
        blocked.append('active_native_runtime')
    if db.execute(f"""select 1 from ({RUNTIME_ATTEMPT_SQL}) a
        join ({AGENT_RUN_SQL}) r on r.id=a.agent_run_id join ({REPLY_TASK_SQL}) t on t.id=r.reply_task_id
        where t.business_object_key=? and a.first_effect_started_at<>'' limit 1""",
        (task['business_object_key'],)).fetchone():
        blocked.append('runtime_effect_started')
    claims = db.execute('select * from (' + claim_projection() + ") where source_id=? and adapter_name in ('reply','scheduled_execution')", (str(task_id),)).fetchall()
    for claim in claims:
        if claim['owner'] and (_lease_active(claim['lease_expires_at'], now)
                               or _pid_alive(claim['owner_pid'])):
            blocked.append('active_dispatcher_owner')
    if task['status'] == 'processing' and not claims:
        blocked.append('unproven_processing_owner')
    if db.execute(f"""select 1 from ({REVIEW_CANDIDATE_SQL}) c join ({REPLY_TASK_SQL}) t on t.id=c.task_id
        where t.business_object_key=? and c.execution_generation=t.execution_generation limit 1""",
        (task['business_object_key'],)).fetchone():
        blocked.append('current_candidate_or_selection')
    if db.execute(f"""select 1 from ({CANDIDATE_EXECUTION_SQL}) e join ({REVIEW_CANDIDATE_SQL}) c on c.id=e.candidate_id
        join ({REPLY_TASK_SQL}) t on t.id=c.task_id where t.business_object_key=? limit 1""",
        (task['business_object_key'],)).fetchone():
        blocked.append('candidate_execution')
    if db.execute(f'select 1 from ({EXTERNAL_ACTION_RESULT_SQL}) where business_object_key=? limit 1',
                  (task['business_object_key'],)).fetchone():
        blocked.append('external_action_result')
    if db.execute(f"""select 1 from ({SENT_REPLY_SQL}) where conversation_id=?
        and trigger_message_id=? limit 1""",
        (task['conversation_id'], task['trigger_message_id'])).fetchone():
        blocked.append('sent_reply_projection')
    from app.native_trajectory import read_run_event_evidence
    for event_run in db.execute("""select runs.* from runs
        join tasks on tasks.id=runs.task_id and tasks.kind='reply'
        join task_business_keys on task_business_keys.task_id=tasks.id
        where task_business_keys.business_key=?""",
        (task['business_object_key'],)).fetchall():
        events, available = read_run_event_evidence(db, event_run)
        if any(_legacy_event_may_have_effect(json.dumps(event)) for event in events):
            blocked.append('tool_effect_or_unknown_effect')
        if not available:
            blocked.append('native_event_source_unavailable')
    if db.execute(f"""select 1 from ({AGENT_EFFECT_INTENT_SQL}) e join ({AGENT_RUN_SQL}) r on r.id=e.agent_run_id
        join ({REPLY_TASK_SQL}) t on t.id=r.reply_task_id
        where t.business_object_key=? limit 1""",
        (task['business_object_key'],)).fetchone():
        blocked.append('prepared_or_dispatched_effect')
    if db.execute(f"""select 1 from ({AGENT_EXECUTION_RECEIPT_SQL}) receipt
        join ({AGENT_RUN_SQL}) r on r.id=receipt.agent_run_id join ({REPLY_TASK_SQL}) t on t.id=r.reply_task_id
        where t.business_object_key=? limit 1""",
        (task['business_object_key'],)).fetchone():
        blocked.append('prior_execution_receipt')
    if db.execute(f"""select 1 from ({CANDIDATE_ACTION_ATTEMPT_SQL}) a
        join ({CANDIDATE_EXECUTION_SQL}) e on e.id=a.execution_id
        join ({REVIEW_CANDIDATE_SQL}) c on c.id=e.candidate_id join ({REPLY_TASK_SQL}) t on t.id=c.task_id
        where t.business_object_key=? limit 1""", (task['business_object_key'],)).fetchone():
        blocked.append('candidate_action_attempt')
    return {
        'task_id': task_id, 'business_object_key': task['business_object_key'],
        'execution_generation': task['execution_generation'], 'input_version': task['input_version'],
        'task_status': task['status'], 'available_at': task['available_at'],
        'latest_failed_run_id': run['id'] if run else None,
        'latest_failed_run_fingerprint': _fingerprint(run) if run else None,
        'blockers': tuple(dict.fromkeys(blocked)), 'eligible': not blocked,
    }


def preview_deferral(db_path: Path | str, task_id: int, *, now: datetime | None = None) -> dict[str, Any]:
    """Inspect one task through a read-only native connection; never create schema."""
    with _connect(db_path, readonly=True) as db:
        return _snapshot(db, task_id, _time(now))


def _verified_backup(backup_path: Path | str, task_id: int, expected: dict[str, Any], now: datetime) -> tuple[str, str]:
    path = Path(backup_path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError('backup is not a file')
    with _connect(path, readonly=True) as backup:
        integrity = backup.execute('pragma integrity_check').fetchone()[0]
        if integrity != 'ok':
            raise ValueError('backup integrity check failed')
        observed = _snapshot(backup, task_id, now)
    for name in ('business_object_key', 'execution_generation', 'input_version',
                 'latest_failed_run_id', 'latest_failed_run_fingerprint'):
        if observed[name] != expected[name]:
            raise ValueError('backup does not contain exact task and failed run')
    return str(path), integrity


def _receipt(db: sqlite3.Connection, receipt_id: int) -> dict[str, Any]:
    return find_receipt(db, receipt_id=receipt_id)


def apply_deferral(
    db_path: Path | str, task_id: int, *, expected_business_object_key: str,
    expected_generation: str,
    expected_input_version: int, expected_run_id: int, expected_run_fingerprint: str,
    backup_path: Path | str, rationale: str, duration_seconds: int,
    request_id: str, now: datetime | None = None,
) -> dict[str, Any]:
    """Atomically defer exactly one failed-run task after verifying a backup."""
    instant = _time(now)
    if not rationale.strip() or not request_id.strip() or not 60 <= duration_seconds <= 86400:
        raise ValueError('rationale, request ID and bounded duration are required')
    if Path(db_path).expanduser().resolve(strict=True) == Path(backup_path).expanduser().resolve(strict=True):
        raise ValueError('backup must be a separate database file')
    expected = dict(business_object_key=expected_business_object_key,
                    execution_generation=expected_generation, input_version=expected_input_version,
                    latest_failed_run_id=expected_run_id,
                    latest_failed_run_fingerprint=expected_run_fingerprint)
    backup_name, backup_integrity = _verified_backup(backup_path, task_id, expected, instant)
    with _connect(db_path, readonly=False) as db:
        db.execute('begin immediate')
        try:
            prior = find_receipt(db, request_id=request_id)
            if prior is not None:
                if (prior['task_id'], prior['business_object_key'], prior['execution_generation'], prior['input_version'],
                    prior['latest_failed_run_id'], prior['latest_failed_run_fingerprint'], prior['backup_path'],
                    prior['rationale'], prior['duration_seconds']) != (
                    task_id, expected_business_object_key, expected_generation, expected_input_version,
                    expected_run_id, expected_run_fingerprint, backup_name, rationale, duration_seconds):
                    raise ValueError('request_id_conflict')
                db.commit()
                return dict(prior)
            snap = _snapshot(db, task_id, instant)
            if not snap['eligible']:
                raise ValueError('deferral blocked: ' + ','.join(snap['blockers']))
            for actual, wanted in (
                (snap['business_object_key'], expected_business_object_key),
                (snap['execution_generation'], expected_generation),
                (snap['input_version'], expected_input_version),
                (snap['latest_failed_run_id'], expected_run_id),
                (snap['latest_failed_run_fingerprint'], expected_run_fingerprint),
            ):
                if actual != wanted:
                    raise ValueError('deferral identity changed')
            until = _stamp(instant + timedelta(seconds=duration_seconds))
            if snap['available_at'] and snap['available_at'] >= until:
                raise ValueError('existing deferral is later')
            db.execute("""update tasks set status='pending',available_at=?,locked_at=null,updated_at=? where id=? and kind='reply' and status in ('pending','processing')""",
                (until, _stamp(instant), task_id))
            receipt_id = insert_receipt(db, dict(
                request_id=request_id, task_id=task_id, business_object_key=snap['business_object_key'],
                execution_generation=expected_generation, input_version=expected_input_version,
                latest_failed_run_id=expected_run_id, latest_failed_run_fingerprint=expected_run_fingerprint,
                original_status=snap['task_status'], original_available_at=snap['available_at'],
                deferred_until=until, duration_seconds=duration_seconds, rationale=rationale,
                backup_path=backup_name, backup_integrity=backup_integrity, status='paused'))
            saved = _receipt(db, receipt_id)
            db.commit()
            return saved
        except Exception:
            db.rollback()
            raise


def resume_deferral(
    db_path: Path | str, task_id: int, *, receipt_id: int, request_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Remove only the schedule delay owned by an exact deferral receipt."""
    instant = _time(now)
    if not request_id.strip():
        raise ValueError('resume request ID is required')
    with _connect(db_path, readonly=False) as db:
        db.execute('begin immediate')
        try:
            row = find_receipt(db, receipt_id=receipt_id)
            if row is None or row['task_id'] != task_id:
                raise ValueError('deferral receipt not found')
            if row['status'] == 'resumed':
                if row['resume_request_id'] != request_id:
                    raise ValueError('resume_request_conflict')
                db.commit()
                return dict(row)
            if row['status'] != 'paused':
                raise ValueError('deferral receipt is not active')
            snap = _snapshot(db, task_id, instant, ignored_receipt_id=receipt_id)
            if not snap['eligible']:
                raise ValueError('resume blocked: ' + ','.join(snap['blockers']))
            if (snap['business_object_key'],snap['execution_generation'],snap['input_version'],
                snap['latest_failed_run_id'],snap['latest_failed_run_fingerprint'],
                snap['task_status'],snap['available_at']) != (
                row['business_object_key'],row['execution_generation'],row['input_version'],
                row['latest_failed_run_id'],row['latest_failed_run_fingerprint'],
                'pending',row['deferred_until']):
                raise ValueError('deferral state changed')
            db.execute("""update tasks set available_at=?,updated_at=? where id=? and kind='reply' and status='pending'""",
                       (row['original_available_at'],_stamp(instant),task_id))
            update_receipt(db, receipt_id, dict(status='resumed', resume_request_id=request_id, resumed_at=_stamp(instant)))
            saved = _receipt(db, receipt_id)
            db.commit()
            return saved
        except Exception:
            db.rollback()
            raise


def main(argv: list[str] | None = None) -> int:
    """Explicit operator command; omitted mode is a read-only preview."""
    parser = argparse.ArgumentParser(description='Preview or defer one existing reply task')
    parser.add_argument('--db', required=True)
    parser.add_argument('--task-id', required=True, type=int)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--apply', action='store_true')
    modes.add_argument('--resume', action='store_true')
    parser.add_argument('--business-object-key')
    parser.add_argument('--generation')
    parser.add_argument('--input-version', type=int)
    parser.add_argument('--run-id', type=int)
    parser.add_argument('--run-fingerprint')
    parser.add_argument('--backup')
    parser.add_argument('--rationale')
    parser.add_argument('--duration-seconds', type=int)
    parser.add_argument('--receipt-id', type=int)
    parser.add_argument('--request-id')
    args = parser.parse_args(argv)
    if args.apply:
        required = ('business_object_key', 'generation', 'input_version', 'run_id',
                    'run_fingerprint', 'backup', 'rationale', 'duration_seconds', 'request_id')
        if any(getattr(args, field) is None for field in required):
            parser.error('--apply requires exact preview identity, backup, rationale, duration and request ID')
        result = apply_deferral(
            args.db, args.task_id, expected_business_object_key=args.business_object_key,
            expected_generation=args.generation, expected_input_version=args.input_version,
            expected_run_id=args.run_id, expected_run_fingerprint=args.run_fingerprint,
            backup_path=args.backup, rationale=args.rationale,
            duration_seconds=args.duration_seconds, request_id=args.request_id,
        )
    elif args.resume:
        if args.receipt_id is None or args.request_id is None:
            parser.error('--resume requires receipt ID and request ID')
        result = resume_deferral(args.db, args.task_id, receipt_id=args.receipt_id,
                                 request_id=args.request_id)
    else:
        result = preview_deferral(args.db, args.task_id)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
