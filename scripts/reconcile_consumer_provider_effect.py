#!/usr/bin/env python
"""Record a delivery a proposal turn performed, and close the task it belongs to.

Run this when ``app.agent_effect_guard`` has reported
``consumer_unreviewed_provider_effect``.  Until the delivery is in the ledger,
the idempotency check has nothing to find and the next attempt sends the same
message to the same person a second time, so this is the step that has to come
before any retry decision.

    scripts/reconcile_consumer_provider_effect.py --run-id 14017
    scripts/reconcile_consumer_provider_effect.py --run-id 14017 --apply

Without ``--apply`` it only prints what it would record.  With
``--confirmation-json`` it attaches a read-only provider readback (for DingTalk,
the ``dws chat message query-send-status`` answer) so the durable message id is
stored with the delivery rather than only the asynchronous task id.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.provider_effect_reconcile import (  # noqa: E402
    UnreconcilableProviderEffect,
    plan_provider_effect_reconciliation,
)
from app.store import AutoReplyStore  # noqa: E402


def _default_db() -> Path:
    configured = os.environ.get("CEO_WORKER_DB", "").strip()
    if configured:
        return Path(configured)
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "ceo-agent-service"
        / "auto-reply.sqlite3"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--db", type=Path, default=_default_db())
    parser.add_argument("--confirmation-json", type=Path, default=None)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the ledger row and close the task; otherwise print only",
    )
    args = parser.parse_args(argv)

    store = AutoReplyStore(args.db)
    run = store.get_agent_run(args.run_id)
    if run is None:
        print(f"agent run {args.run_id} does not exist", file=sys.stderr)
        return 2
    if run.status != "completed":
        print(
            f"agent run {args.run_id} is {run.status}; only a completed run "
            "carries a result that can be attributed",
            file=sys.stderr,
        )
        return 2
    task = store.get_reply_task(run.reply_task_id)
    if task is None:
        print(f"reply task {run.reply_task_id} does not exist", file=sys.stderr)
        return 2

    confirmation = None
    if args.confirmation_json is not None:
        confirmation = json.loads(args.confirmation_json.read_text())

    try:
        plan = plan_provider_effect_reconciliation(
            agent_run_id=run.id,
            reply_task_id=task.id,
            business_object_key=task.business_object_key,
            final_result_json=run.final_result_json,
            tool_events=run.tool_events,
            confirmation=confirmation,
        )
    except UnreconcilableProviderEffect as error:
        print(f"cannot reconcile run {args.run_id}: {error}", file=sys.stderr)
        return 3

    print(f"run {run.id} ({run.role}) on task {task.id} [{task.status}]")
    print(f"  receipts            {', '.join(plan.receipts)}")
    print(f"  action_identity     {plan.action_identity}")
    print(f"  operation           {plan.operation}")
    print(f"  target              {json.dumps(plan.target_identifiers, ensure_ascii=False)}")
    print(f"  external_action_key {plan.external_action_key}")
    print(f"  reply_text          {len(plan.reply_text)} chars")
    if not args.apply:
        print("dry run; pass --apply to record it")
        return 0

    sent = store.record_completed_agent_message_delivery(
        agent_run_id=plan.agent_run_id,
        external_action_key=plan.external_action_key,
        business_object_key=plan.business_object_key,
        action_identity=plan.action_identity,
        operation=plan.operation,
        target_identifiers=plan.target_identifiers,
        conversation_id=task.conversation_id,
        trigger_message_id=task.trigger_message_id,
        reply_text=plan.reply_text,
        provider_result=plan.provider_result,
    )
    print(f"recorded sent_reply {sent.id}")
    if task.status == "failed":
        closed = store.resolve_reconciled_failed_reply_task(
            task.id, external_action_key=plan.external_action_key
        )
        print(f"task {task.id} closed as done: {closed}")
    else:
        print(f"task {task.id} left at {task.status}; only a failed task is closed here")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
