"""Offline-first CLI for the optional official Feishu Bot channel.

Commands that can connect to Feishu or send a message are named explicitly and
never run as part of ``status``, ``setup``, or ``doctor`` without their live
flags.  Credential values are deliberately represented only as booleans.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, is_dataclass
from getpass import getpass
import json
from pathlib import Path
from typing import Any

from app import config
from app.store import AutoReplyStore


def _json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    elif is_dataclass(value):
        value = asdict(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _store(args: argparse.Namespace) -> AutoReplyStore:
    return AutoReplyStore(Path(args.db).expanduser())


def _configured_app_id(args: argparse.Namespace) -> str:
    app_id = str(getattr(args, "app_id", "") or config.feishu_app_id()).strip()
    if not app_id:
        raise ValueError("Feishu App ID is not configured")
    return app_id


def _client_config(args: argparse.Namespace):
    from app.feishu.client import FeishuClientConfig

    app_secret = config.feishu_app_secret()
    if not app_secret:
        raise ValueError("Feishu App Secret is not configured")
    return FeishuClientConfig(
        app_id=_configured_app_id(args),
        app_secret=app_secret,
        security_mode=config.feishu_security_mode(),
    )


def cmd_status(args: argparse.Namespace) -> int:
    from app.feishu.setup import dependency_status

    dependencies = dependency_status()
    store = _store(args)
    payload = {
        "enabled": config.feishu_enabled(),
        "sender_enabled": config.feishu_sender_enabled(),
        "send_mode": config.feishu_send_mode(),
        "security_mode": config.feishu_security_mode(),
        "app_id": "configured" if config.feishu_app_id() else "missing",
        "app_secret": "configured" if config.feishu_app_secret() else "missing",
        "dependencies": asdict(dependencies),
        "scope_counts": {
            "pending": len(
                store.list_feishu_reply_scopes(binding_status="pending")
            ),
            "verified": len(
                store.list_feishu_reply_scopes(binding_status="verified")
            ),
        },
        "delivery_counts": {
            status: len(store.list_feishu_deliveries(status=status))
            for status in (
                "ready_to_send",
                "sending",
                "sent",
                "retry",
                "send_unknown",
                "failed",
                "rejected",
            )
        },
        "network": "not_checked",
        "send": "not_checked",
    }
    print(_json(payload))
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    """Print the least-privilege manifest; never create/update an app."""
    from app.feishu.setup import registration_manifest, save_app_secret

    if args.save_secret:
        save_app_secret(getpass("Feishu App Secret (stored in Keychain): "))
    if args.save_app_id:
        app_id = str(args.app_id or "").strip()
        if not app_id:
            print("--save-app-id requires --app-id")
            return 2
        config.write_env_values({"CEO_FEISHU_APP_ID": app_id})
    print(
        _json(
            {
                "mode": "offline_manifest_only",
                "manifest": registration_manifest(),
                "app_id": "configured" if config.feishu_app_id() else "missing",
                "app_secret": (
                    "configured" if config.feishu_app_secret() else "missing"
                ),
                "next": "Complete app/admin approval manually; then run doctor --verify-live only when authorized.",
            }
        )
    )
    return 0


async def _verify_live(args: argparse.Namespace) -> bool:
    from app.feishu.client import build_channel
    from app.feishu.setup import verify_live_connection

    client = build_channel(_client_config(args))
    return await verify_live_connection(client, timeout=args.timeout)


def cmd_doctor(args: argparse.Namespace) -> int:
    from app.feishu.setup import doctor

    app_id = str(args.app_id or config.feishu_app_id()).strip()
    secret = config.feishu_app_secret()
    result = doctor(app_id=app_id, app_secret=secret)
    payload = asdict(result)
    if args.verify_live:
        try:
            payload["live_verified"] = asyncio.run(_verify_live(args))
            payload["checks"]["network"] = "connected"
        except Exception as exc:
            payload["live_verified"] = False
            payload["checks"]["network"] = f"failed:{type(exc).__name__}"
    print(_json(payload))
    ready = result.status == "ready_for_explicit_live_check"
    return 0 if ready and (not args.verify_live or payload["live_verified"]) else 1


async def _receive_for(args: argparse.Namespace):
    from app.feishu.listener import FeishuIngressListener
    from app.feishu.producer import FeishuReplyProducer

    store = _store(args)
    producer = FeishuReplyProducer(
        store,
        app_id=_configured_app_id(args),
        stale_event_seconds=config.feishu_stale_event_seconds(),
    )
    listener = FeishuIngressListener(
        producer,
        _client_config(args),
        enabled=True,
    )
    task = asyncio.create_task(
        listener.run(ready_timeout=min(float(args.timeout), 30.0))
    )
    await asyncio.sleep(0)
    try:
        await listener.wait_ready(timeout=min(float(args.timeout), 30.0))
        await asyncio.sleep(float(args.timeout))
    finally:
        await listener.stop()
        await asyncio.gather(task, return_exceptions=True)
    return listener.health


def cmd_receive_test(args: argparse.Namespace) -> int:
    try:
        health = asyncio.run(_receive_for(args))
    except Exception as exc:
        print(_json({"status": "failed", "error_kind": type(exc).__name__}))
        return 1
    print(_json(health))
    return 0


def cmd_produce_once(args: argparse.Namespace) -> int:
    store = _store(args)
    app_id = str(args.app_id or config.feishu_app_id()).strip()
    events = store.list_feishu_events(
        app_id=app_id,
        eligibility_status="eligible",
        unqueued_only=True,
        limit=args.limit,
    )
    attached = 0
    for event in events:
        attached += int(
            store.attach_feishu_event_reply_task(event.id).reply_task_id > 0
        )
    print(_json({"eligible_unqueued": len(events), "enqueued": attached}))
    return 0


def cmd_consume_once(args: argparse.Namespace) -> int:
    from app.feishu.service import build_decision_runner, run_consume_once

    runner = build_decision_runner(workspace=config.workspace_path())
    processed = run_consume_once(_store(args), runner, limit=args.limit)
    print(_json({"processed": processed, "sent": 0}))
    return 0


def cmd_maintenance_once(args: argparse.Namespace) -> int:
    from app.feishu.maintenance import purge_expired_feishu_events

    result = purge_expired_feishu_events(
        _store(args),
        retention_days=config.feishu_event_retention_days(),
        app_id=str(args.app_id or "").strip(),
        batch_limit=args.batch_limit,
        max_batches=args.max_batches,
    )
    print(_json(result))
    return 1 if result.more_may_remain else 0


def cmd_scopes_list(args: argparse.Namespace) -> int:
    rows = _store(args).list_feishu_reply_scopes(
        app_id=str(args.app_id or config.feishu_app_id()).strip(),
        target_type=args.target_type,
        binding_status=args.status,
    )
    print(_json([row.model_dump() for row in rows]))
    return 0


def _review_scope(args: argparse.Namespace, *, approved: bool) -> int:
    row = _store(args).review_feishu_reply_scope(
        _configured_app_id(args),
        args.target_type,
        args.target_id,
        approved=approved,
        approved_by=args.approved_by,
    )
    print(_json(row))
    return 0


def cmd_scope_approve(args: argparse.Namespace) -> int:
    return _review_scope(args, approved=True)


def cmd_scope_disable(args: argparse.Namespace) -> int:
    return _review_scope(args, approved=False)


def cmd_deliveries_list(args: argparse.Namespace) -> int:
    rows = _store(args).list_feishu_deliveries(
        status=args.status,
        app_id=str(args.app_id or config.feishu_app_id()).strip(),
        limit=args.limit,
    )
    payload = []
    for row in rows:
        item = row.model_dump()
        if not args.include_text:
            item["reply_text"] = f"[redacted:{len(row.reply_text)} chars]"
        payload.append(item)
    print(_json(payload))
    return 0


def cmd_audit_events_list(args: argparse.Namespace) -> int:
    if args.all_apps and str(args.app_id or "").strip():
        raise ValueError("use either --all-apps or --app-id")
    selected_app_id = "" if args.all_apps else _configured_app_id(args)
    rows = _store(args).list_feishu_audit_events(
        app_id=selected_app_id,
        entity_type=args.entity_type,
        entity_id=args.entity_id,
        before_id=args.before_id,
        limit=args.limit,
    )
    print(_json([row.model_dump() for row in rows]))
    return 0


def cmd_delivery_approve(args: argparse.Namespace) -> int:
    """Record approval locally; the single service runtime performs the send."""
    if not config.feishu_live_send_allowed():
        print(_json({"status": "blocked", "reason": "outbound_gates_closed"}))
        return 2
    app_id = str(config.feishu_app_id() or "").strip()
    if not app_id:
        raise ValueError("Feishu App ID is not configured")
    delivery = _store(args).approve_feishu_delivery(
        args.id,
        app_id=app_id,
        approved_by=args.approved_by,
    )
    print(
        _json(
            {
                "id": delivery.id,
                "status": "approved_pending_runtime",
                "approved_by": delivery.approved_by,
                "network": "not_checked",
                "send": "not_attempted",
                "next": "Use the running local /feishu/review runtime; this CLI never opens a second WebSocket.",
            }
        )
    )
    return 0


def cmd_delivery_reject(args: argparse.Namespace) -> int:
    app_id = str(config.feishu_app_id() or "").strip()
    if not app_id:
        raise ValueError("Feishu App ID is not configured")
    _store(args).reject_feishu_delivery(
        args.id,
        app_id=app_id,
        rejected_by=args.rejected_by,
    )
    print(_json({"id": args.id, "status": "rejected"}))
    return 0


def cmd_delivery_reconcile(args: argparse.Namespace) -> int:
    app_id = _configured_app_id(args)
    delivery = _store(args).reconcile_feishu_delivery_unknown(
        args.id,
        app_id=app_id,
        outcome=args.outcome,
        verified_by=args.verified_by,
        evidence_kind=args.evidence_kind,
        feishu_message_id=args.feishu_message_id,
        request_log_id=args.request_log_id,
    )
    print(
        _json(
            {
                "id": delivery.id,
                "status": delivery.status,
                "feishu_message_id": delivery.feishu_message_id,
                "next": (
                    "none"
                    if delivery.status == "sent"
                    else "Use deliveries requeue only after independently confirming non-delivery."
                ),
            }
        )
    )
    return 0


def cmd_delivery_requeue(args: argparse.Namespace) -> int:
    app_id = _configured_app_id(args)
    delivery = _store(args).requeue_feishu_delivery_after_verification(
        args.id,
        app_id=app_id,
        verified_by=args.verified_by,
        evidence_kind=args.evidence_kind,
        available_at=args.available_at,
    )
    print(
        _json(
            {
                "id": delivery.id,
                "status": delivery.status,
                "approved": bool(delivery.approved_at),
                "next": "A fresh approval is required in confirm mode.",
            }
        )
    )
    return 0


def _add_db(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", default=str(config.worker_db_path()))


def _add_app_id(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--app-id", default="")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ceo-agent feishu")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="local sanitized status; no network")
    _add_db(status)
    status.set_defaults(func=cmd_status)

    setup = sub.add_parser("setup", help="print the offline least-privilege manifest")
    _add_app_id(setup)
    setup.add_argument("--save-app-id", action="store_true")
    setup.add_argument("--save-secret", action="store_true")
    setup.set_defaults(func=cmd_setup)

    doctor = sub.add_parser("doctor", help="local checks; live check is opt-in")
    _add_app_id(doctor)
    doctor.add_argument("--verify-live", action="store_true")
    doctor.add_argument("--timeout", type=float, default=30.0)
    doctor.set_defaults(func=cmd_doctor)

    for name in ("discover", "receive-test"):
        receive = sub.add_parser(name, help="explicit receive-only live check")
        _add_db(receive)
        _add_app_id(receive)
        receive.add_argument("--timeout", type=float, default=60.0)
        receive.set_defaults(func=cmd_receive_test)

    produce = sub.add_parser("produce-once", help="enqueue stored eligible events")
    _add_db(produce)
    _add_app_id(produce)
    produce.add_argument("--limit", type=int, default=50)
    produce.set_defaults(func=cmd_produce_once)

    consume = sub.add_parser("consume-once", help="prepare drafts; never send")
    _add_db(consume)
    consume.add_argument("--limit", type=int, default=50)
    consume.set_defaults(func=cmd_consume_once)

    maintenance = sub.add_parser(
        "maintenance-once", help="bounded local retention cleanup; no network"
    )
    _add_db(maintenance)
    _add_app_id(maintenance)
    maintenance.add_argument("--batch-limit", type=int, default=500)
    maintenance.add_argument("--max-batches", type=int, default=20)
    maintenance.set_defaults(func=cmd_maintenance_once)

    audit_events = sub.add_parser(
        "audit-events", help="list append-only Feishu state evidence"
    )
    _add_db(audit_events)
    _add_app_id(audit_events)
    audit_events.add_argument("--all-apps", action="store_true")
    audit_events.add_argument("--entity-type", default="")
    audit_events.add_argument("--entity-id", default="")
    audit_events.add_argument("--before-id", type=int, default=0)
    audit_events.add_argument("--limit", type=int, default=100)
    audit_events.set_defaults(func=cmd_audit_events_list)

    scopes = sub.add_parser("scopes")
    scope_sub = scopes.add_subparsers(dest="scope_command", required=True)
    scope_list = scope_sub.add_parser("list")
    _add_db(scope_list)
    _add_app_id(scope_list)
    scope_list.add_argument(
        "--target-type", choices=("", "direct_sender", "group"), default=""
    )
    scope_list.add_argument(
        "--status", choices=("", "pending", "verified", "disabled"), default=""
    )
    scope_list.set_defaults(func=cmd_scopes_list)
    for name, func in (("approve", cmd_scope_approve), ("disable", cmd_scope_disable)):
        review = scope_sub.add_parser(name)
        _add_db(review)
        _add_app_id(review)
        review.add_argument(
            "--target-type", choices=("direct_sender", "group"), required=True
        )
        review.add_argument("--target-id", required=True)
        review.add_argument("--approved-by", required=True)
        review.set_defaults(func=func)

    deliveries = sub.add_parser("deliveries")
    delivery_sub = deliveries.add_subparsers(
        dest="delivery_command", required=True
    )
    delivery_list = delivery_sub.add_parser("list")
    _add_db(delivery_list)
    _add_app_id(delivery_list)
    delivery_list.add_argument("--status", default="")
    delivery_list.add_argument("--limit", type=int, default=100)
    delivery_list.add_argument("--include-text", action="store_true")
    delivery_list.set_defaults(func=cmd_deliveries_list)
    approve = delivery_sub.add_parser("approve")
    _add_db(approve)
    approve.add_argument("--id", type=int, required=True)
    approve.add_argument("--approved-by", required=True)
    approve.set_defaults(func=cmd_delivery_approve)
    reject = delivery_sub.add_parser("reject")
    _add_db(reject)
    reject.add_argument("--id", type=int, required=True)
    reject.add_argument("--rejected-by", required=True)
    reject.set_defaults(func=cmd_delivery_reject)
    reconcile = delivery_sub.add_parser("reconcile")
    _add_db(reconcile)
    _add_app_id(reconcile)
    reconcile.add_argument("--id", type=int, required=True)
    reconcile.add_argument(
        "--outcome", choices=("sent", "not-sent"), required=True
    )
    reconcile.add_argument("--verified-by", required=True)
    reconcile.add_argument(
        "--evidence-kind",
        choices=("feishu_ui", "message_lookup", "admin_audit"),
        required=True,
    )
    reconcile.add_argument("--feishu-message-id", default="")
    reconcile.add_argument("--request-log-id", default="")
    reconcile.set_defaults(func=cmd_delivery_reconcile)
    requeue = delivery_sub.add_parser("requeue")
    _add_db(requeue)
    _add_app_id(requeue)
    requeue.add_argument("--id", type=int, required=True)
    requeue.add_argument("--verified-by", required=True)
    requeue.add_argument(
        "--evidence-kind",
        choices=("feishu_ui", "message_lookup", "admin_audit"),
        required=True,
    )
    requeue.add_argument("--available-at", default="")
    requeue.set_defaults(func=cmd_delivery_requeue)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (ValueError, PermissionError) as exc:
        print(_json({"status": "blocked", "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
