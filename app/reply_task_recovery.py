"""Explicit task-bound recovery of stalled, effect-free reply processing."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.store import AutoReplyStore, NATIVE_REPLY_TASK_CURRENT_SQL
from app.current_publication_sources import publication_receipt_sources, publication_delivery_sources
from app.reply_task_deferral import _lease_active, _pid_alive
from app.task_lifecycle_storage import claim_projection


def recover_stalled_reply_task(store, task_id: int, *, expected_execution_generation: str, now=None):
    from app.worker import STALE_PROCESSING_TASK_SECONDS

    if task_id <= 0 or not expected_execution_generation.strip():
        raise ValueError("task and current execution generation are required")
    with store._agent_run_write_transaction(now) as (db, (now_value, now_text)):
        task = db.execute(f"select * from ({NATIVE_REPLY_TASK_CURRENT_SQL}) where id=?", (task_id,)).fetchone()
        if (task is None or task["status"] != "processing"
                or task["execution_generation"] != expected_execution_generation):
            raise ValueError("task is not processing in the specified generation")
        claims = db.execute("select * from (" + claim_projection() + ") where source_id=? and adapter_name in ('reply','scheduled_execution')", (str(task_id),)).fetchall()
        if not claims:
            raise ValueError("processing task has no dispatcher ownership history")
        if any(claim["owner"] and (_lease_active(claim["lease_expires_at"], now_value)
                                  or _pid_alive(claim["owner_pid"] or 0)) for claim in claims):
            raise ValueError("dispatcher still owns this task")
        leases = db.execute("""select lease_expires_at from runs where task_id=? and execution_generation=?
            and status='running' union all select a.lease_expires_at from runtime_attempts a
            join runs r on r.id=a.parent_id where r.task_id=? and r.execution_generation=?
            and a.status in ('starting','running')""",
            (task_id, expected_execution_generation, task_id, expected_execution_generation)).fetchall()
        if any(_lease_active(row["lease_expires_at"], now_value) for row in leases):
            raise ValueError("Agent run or native runtime lease is active")
        age = f"-{STALE_PROCESSING_TASK_SECONDS} seconds"
        if db.execute("select datetime(?) is null or datetime(?)>datetime(?,?)",
                      (task["locked_at"], task["locked_at"], now_text, age)).fetchone()[0]:
            raise ValueError("task has not reached the existing stalled threshold")
        runs = db.execute("select id from runs where task_id=? and execution_generation=?",
                          (task_id, expected_execution_generation)).fetchall()
        if not runs:
            raise ValueError("task has no Agent run to recover")
        if db.execute("""select 1 from runs where task_id=? and execution_generation=?
            and (datetime(updated_at) is null or datetime(updated_at)>datetime(?,?)) limit 1""",
            (task_id, expected_execution_generation, now_text, age)).fetchone():
            raise ValueError("Agent run has recent progress")
        if db.execute("""select 1 from runtime_attempts a join runs r on r.id=a.parent_id
            where r.task_id=? and r.execution_generation=? and a.status in ('starting','running')
            and (datetime(a.updated_at) is null or datetime(a.updated_at)>datetime(?,?)) limit 1""",
            (task_id, expected_execution_generation, now_text, age)).fetchone():
            raise ValueError("native runtime has recent progress")
        receipts = publication_receipt_sources(db)
        deliveries = publication_delivery_sources(db)
        if db.execute(f"select 1 from ({receipts['external_action_results']}) where business_object_key=? limit 1",
                      (task["business_object_key"],)).fetchone():
            raise ValueError("task has an external action result")
        for source in (receipts["agent_effect_intents"], receipts["agent_execution_receipts"]):
            if db.execute(f"select 1 from ({source}) e join runs r on r.id=e.agent_run_id where r.task_id in (select task_id from task_business_keys where business_key=?) limit 1",
                          (task["business_object_key"],)).fetchone():
                raise ValueError("task has a prepared or dispatched external action")
        if db.execute("""select 1 from actions a where a.record_type='action'
            and (a.action_business_object_key=? or a.action_agent_run_id in
                 (select r.id from runs r join task_business_keys k on k.task_id=r.task_id where k.business_key=?)) limit 1""", (task["business_object_key"], task["business_object_key"])).fetchone():
            raise ValueError("task has a prepared or dispatched external action")
        from app.action_storage import NATIVE_CANDIDATE_ACTION_ATTEMPT_SQL
        if db.execute(f"""select 1 from ({NATIVE_CANDIDATE_ACTION_ATTEMPT_SQL}) a
            join runs r on r.id=a.execution_id join task_business_keys k on k.task_id=r.task_id
            where k.business_key=? limit 1""", (task["business_object_key"],)).fetchone():
            raise ValueError("task has a system action attempt")
        if db.execute(f"select 1 from ({deliveries['sent_replies']}) where conversation_id=? and trigger_message_id=? limit 1",
                      (task["conversation_id"], task["trigger_message_id"])).fetchone():
            raise ValueError("task has a delivery receipt")
        if db.execute("""select 1 from runtime_attempts a join runs r on r.id=a.parent_id
            join task_business_keys k on k.task_id=r.task_id
            where k.business_key=? and a.first_effect_started_at<>'' limit 1""", (task["business_object_key"],)).fetchone():
            raise ValueError("task has started an external action")
        store._requeue_reply_task_in_connection(db, task_id, "stale_agent_turn_recovery",
            expected_execution_generation=expected_execution_generation, available_at="", now_text=now_text)
        db.execute("""update runtime_attempts set status='failed', failure_class='process',
            failure_code='stale_agent_turn_recovery', failover_permitted=1,
            lease_owner='',lease_expires_at='',finished_at=?,updated_at=?
            where status in ('starting','running') and parent_id in
            (select id from runs where task_id=? and execution_generation=?)""",
            (now_text, now_text, task_id, expected_execution_generation))
    return store.get_reply_task(task_id)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--task-id", required=True, type=int)
    parser.add_argument("--execution-generation", required=True)
    parser.add_argument("--apply", action="store_true", required=True)
    args = parser.parse_args(argv)
    task = recover_stalled_reply_task(AutoReplyStore(args.db), args.task_id,
                                     expected_execution_generation=args.execution_generation)
    print(json.dumps({"task_id": task.id, "status": task.status,
                      "execution_generation": task.execution_generation}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
