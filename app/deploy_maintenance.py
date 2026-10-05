"""Explicit maintenance for authorized, effect-free interrupted reply work."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import signal
from pathlib import Path
import sqlite3
import subprocess
import time

from app.database_backup import create_database_backup
from app.reply_task_deferral import _legacy_event_may_have_effect
from app.repository_updater import UpgradePreconditionError


@dataclass(frozen=True)
class MaintenanceSnapshot:
    processes: tuple[int, ...]
    task_bindings: tuple[tuple[int, str, str, int], ...]
    owners: tuple[int, ...] = ()


def check_maintenance(database_path: Path, task_ids: tuple[int, ...]) -> MaintenanceSnapshot:
    """Read all in-flight work; only the explicitly named reply tasks may run."""
    with sqlite3.connect(database_path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        processing = {row[0] for row in db.execute("select id from reply_tasks where status='processing'")}
        if not processing.issubset(set(task_ids)):
            raise UpgradePreconditionError('maintenance has unapproved processing reply tasks')
        for query in (
            "select 1 from work_summary_inputs where status='processing' limit 1",
            "select 1 from meeting_alignment_jobs where status in ('processing','sending','summarizing') limit 1",
            "select 1 from email_actions where status not in ('pending','done','skipped','failed','needs_human') limit 1",
            "select 1 from scheduled_task_runs where dispatch_status='pending' and lease_owner<>'' "
            "and julianday(lease_expires_at)>julianday('now') limit 1",
            "select 1 from agent_runtime_attempts where status in ('starting','running') "
            "and (agent_run_id is null or agent_run_id not in "
            "(select id from agent_runs where reply_task_id in (" + ','.join('?' for _ in task_ids) + "))) limit 1",
        ):
            args = task_ids if '?' in query else ()
            if db.execute(query, args).fetchone():
                raise UpgradePreconditionError('maintenance has other in-flight work')
        bindings = []
        for task_id in task_ids:
            task = db.execute('select * from reply_tasks where id=?', (task_id,)).fetchone()
            if task is None or task['status'] not in ('pending','processing'):
                raise UpgradePreconditionError('maintenance task is not recoverable')
            latest = db.execute('select role,status from agent_runs where reply_task_id=? order by id desc limit 1', (task_id,)).fetchone()
            if latest is None or latest['role'] != 'consumer' or latest['status'] not in ('running','failed'):
                raise UpgradePreconditionError('maintenance needs a recoverable Consumer run')
            bindings.append((task_id, task['business_object_key'], task['execution_generation'], task['input_version']))
            checks = (
                ('review_candidates', 'task_id'),
                ('external_action_results', 'business_object_key'),
            )
            for table, column in checks:
                value = task_id if column == 'task_id' else task['business_object_key']
                if db.execute(f'select 1 from {table} where {column}=? limit 1', (value,)).fetchone():
                    raise UpgradePreconditionError('maintenance task has prepared or completed action')
            for table in ('agent_effect_intents', 'agent_execution_receipts'):
                if db.execute(f'select 1 from {table} e join agent_runs r on r.id=e.agent_run_id '
                              'where r.reply_task_id=? limit 1', (task_id,)).fetchone():
                    raise UpgradePreconditionError('maintenance task has effect identity')
            if db.execute('select 1 from sent_replies where conversation_id=? and trigger_message_id=? limit 1',
                          (task['conversation_id'], task['trigger_message_id'])).fetchone():
                raise UpgradePreconditionError('maintenance task has delivery receipt')
            if db.execute('select 1 from agent_runtime_attempts a join agent_runs r on r.id=a.agent_run_id '
                          "where r.reply_task_id=? and a.first_effect_started_at<>'' limit 1", (task_id,)).fetchone():
                raise UpgradePreconditionError('maintenance task has started effect')
            events = db.execute('select e.effect_kind,e.event_json from agent_run_events e '
                                'join agent_runs r on r.id=e.agent_run_id where r.reply_task_id=?', (task_id,))
            for effect, raw in events:
                event = json.loads(raw)
                item = event.get('item', {})
                # Native protocol and message events cannot execute provider tools.
                if (effect in ('effectful', 'unreviewed') or _legacy_event_may_have_effect(raw)
                    or item.get('type') not in (None, 'agent_message', 'reasoning', 'error')
                    or event.get('type') not in ('thread.started', 'turn.started', 'turn.completed',
                                                 'turn.failed', 'item.started', 'item.completed', 'error')):
                    raise UpgradePreconditionError('maintenance task has tool or unknown event')
        owners = {row[0] for row in db.execute(
            "select distinct owner_pid from dispatcher_claim_leases where terminal_at='' and owner_pid>0 "
            "and julianday(lease_expires_at)>julianday('now')")}
    lines = subprocess.run(['ps', '-axo', 'pid=,ppid='], check=True, capture_output=True, text=True).stdout.splitlines()
    parents = [(int(pid), int(parent)) for pid, parent in (line.split() for line in lines)]
    descendants = set(owners)
    while True:
        expanded = descendants | {pid for pid, parent in parents if parent in descendants}
        if expanded == descendants:
            break
        descendants = expanded
    return MaintenanceSnapshot(tuple(sorted(descendants)), tuple(bindings), tuple(sorted(owners)))


def _process_table() -> dict[int, tuple[int, int, str]]:
    result = subprocess.run(['ps', '-axo', 'pid=,ppid=,pgid=,stat='], check=True,
                            capture_output=True, text=True)
    return {int(pid): (int(parent), int(group), state)
            for pid, parent, group, state in (line.split() for line in result.stdout.splitlines())}


def stop_for_maintenance(snapshot: MaintenanceSnapshot, stop) -> MaintenanceSnapshot:
    """Freeze admissions and descendants, then stop and terminate the frozen tree.

    A frozen native process cannot spawn another child during termination. This
    is an explicit interruption of already verified effect-free preparation.
    """
    frozen = set()
    groups = set()
    own_group = os.getpgrp()
    owned = set(snapshot.processes) | set(snapshot.owners)
    deadline = time.monotonic() + 30
    try:
        while True:
            table = _process_table()
            discovered = owned | {pid for pid, (parent, group, _) in table.items()
                                  if parent in owned or group in groups}
            newly_frozen = False
            for pid in sorted(discovered - frozen, key=lambda pid: pid not in snapshot.owners):
                if pid not in table or table[pid][2].startswith('Z'):
                    continue
                try:
                    os.kill(pid, signal.SIGSTOP)
                except ProcessLookupError:
                    continue
                frozen.add(pid)
                newly_frozen = True
                group = table[pid][1]
                if group == pid and group != own_group:
                    groups.add(group)
            stopped = {pid for pid, (_, _, state) in table.items() if state.startswith(('T', 'Z'))}
            if not newly_frozen and discovered == owned and discovered.issubset(stopped | (owned - set(table))):
                break
            owned = discovered
            if time.monotonic() >= deadline:
                raise UpgradePreconditionError('maintenance could not freeze owned process tree')
    except BaseException:
        stop()
        raise
    stop()
    deadline = time.monotonic() + 30
    # Frozen survivors cannot execute a TERM handler or create a late child.
    table = _process_table()
    survivors = {pid for pid, (parent, group, state) in table.items()
                 if not state.startswith('Z') and (pid in frozen or parent in frozen or group in groups)}
    for pid in survivors:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            continue
    while True:
        table = _process_table()
        active = {pid for pid, (parent, group, state) in table.items()
                  if not state.startswith('Z') and (pid in frozen or parent in frozen or group in groups)}
        if not active:
            break
        if time.monotonic() >= deadline:
            raise UpgradePreconditionError('maintenance owned process survived stop')
        time.sleep(0.2)
    return MaintenanceSnapshot((), snapshot.task_bindings, ())


def prepare_maintenance(database_path: Path, task_ids: tuple[int, ...], operation_id: str,
                        snapshot: MaintenanceSnapshot) -> None:
    """After stopping admissions, recheck effects, verify backup, use restart recovery."""
    deadline = time.monotonic() + 30
    for pid in snapshot.processes:
        while True:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            if time.monotonic() >= deadline:
                raise UpgradePreconditionError('maintenance process is still alive')
            time.sleep(0.2)
    current = check_maintenance(database_path, task_ids)
    if current.task_bindings != snapshot.task_bindings:
        raise UpgradePreconditionError('maintenance task identity changed during stop')
    backup = create_database_backup(database_path, database_path.parent / 'maintenance-backups' / f'{operation_id}.sqlite3')
    from app.store import AutoReplyStore
    store = AutoReplyStore(database_path)
    recovered = store.recover_interrupted_agent_runs_after_service_restart()
    store.recover_stale_runtime_attempts(stale_after_seconds=1)
    receipt = {'operation_id': operation_id, 'authorized_task_ids': list(task_ids),
               'recovered_task_ids': [task.id for task in recovered], 'verified_backup': str(backup)}
    store.set_service_state('deployment_maintenance_receipt', json.dumps(receipt, sort_keys=True))
