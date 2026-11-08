"""Transactional error-to-Workbench associations in existing service State."""

import json

from app.state_definitions import state_projection
from app.state_storage import insert_state, state_json_field, upsert_state

PREFIX = 'automatic_repair:'
TERMINAL = frozenset({'pr_created', 'no_code_change', 'blocked', 'failed', 'stopped'})


def repair_key(source: str, source_id: int) -> str:
    return f'{PREFIX}{source}:{source_id}'


def enqueue_in_transaction(db, source: str, source_id: int) -> None:
    insert_state(db, 'service_state', {
        'key': repair_key(source, source_id),
        'value': json.dumps({'source': source, 'source_id': source_id, 'status': 'queued',
                             'task_id': '', 'turn_ids': [], 'pr_url': '', 'summary': ''}),
    }, ignore=True)


def enqueue_repair(store, source: str, source_id: int) -> None:
    with store._immediate_write_transaction() as db:
        enqueue_in_transaction(db, source, source_id)


def get_repair(store, source: str, source_id: int) -> dict | None:
    value = store.get_service_state(repair_key(source, source_id))
    return json.loads(value) if value else None


def list_repairs(store) -> list[dict]:
    with store._connect() as db:
        rows = db.execute(
            f"select value from ({state_projection('service_state')}) where key like ? order by key",
            (PREFIX + '%',),
        ).fetchall()
    return [json.loads(row['value']) for row in rows]


def save_repair(store, entry: dict) -> None:
    store.set_service_state(repair_key(entry['source'], entry['source_id']), json.dumps(entry))


def ensure_conversation(store, entry: dict) -> dict:
    """Commit the Workbench parent and its source association together."""
    with store._immediate_write_transaction() as db:
        key = repair_key(entry['source'], entry['source_id'])
        row = db.execute(
            f"select value from ({state_projection('service_state')}) where key=?", (key,),
        ).fetchone()
        current = json.loads(row['value'])
        if current['task_id']:
            return current
        task = db.execute(
            "insert into workbench_tasks(title,runtime_kind) values(?,?) returning id",
            (f"自动修复 {current['source']} #{current['source_id']}", 'codex'),
        ).fetchone()
        current['task_id'] = str(task[0])
        upsert_state(db, 'service_state', {'key': key, 'value': json.dumps(current)},
                     {'value': state_json_field('value'), 'updated_at': 'json_quote(current_timestamp)'})
        return current
