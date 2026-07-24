from datetime import datetime, timezone

from app.feishu import cli
from app.feishu.delivery import delivery_idempotency_key
from app.feishu.models import FeishuInboundMessage, FeishuReplyScope
from app.store import AutoReplyStore


def _message() -> FeishuInboundMessage:
    now = datetime.now(timezone.utc).isoformat()
    return FeishuInboundMessage(
        event_id="evt-cli-1",
        app_id="cli_test",
        message_id="om_cli_1",
        chat_id="oc_cli_1",
        chat_type="group",
        sender_open_id="ou_cli_1",
        sender_name="Alice",
        message_type="text",
        mentioned_bot=True,
        body_text="请给出建议",
        event_create_time=now,
        received_at=now,
    )


def _seed_delivery(db):
    store = AutoReplyStore(db)
    message = _message()
    event = store.record_feishu_event(
        message,
        eligibility_status="eligible",
        store_body=True,
        enqueue_eligible=True,
    )
    task = next(
        row
        for row in store.list_reply_tasks(channel="feishu")
        if row.id == event.reply_task_id
    )
    attempt_id = store.record_reply_attempt(
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=message.message_id,
        trigger_sender=message.sender_name,
        trigger_text=message.body_text,
        action="send_reply",
        sensitivity_kind="general",
        draft_reply_text="已审核草稿",
        send_status="pending",
        channel="feishu",
    )
    return store.create_feishu_delivery(
        reply_task_id=event.reply_task_id,
        attempt_id=attempt_id,
        app_id=message.app_id,
        chat_id=message.chat_id,
        reply_to_message_id=message.message_id,
        reply_in_thread=False,
        reply_text="已审核草稿",
        idempotency_key=delivery_idempotency_key(
            app_id=message.app_id,
            reply_task_id=event.reply_task_id,
            trigger_message_id=message.message_id,
        ),
    )


def test_status_is_local_and_never_prints_secret(tmp_path, monkeypatch, capsys):
    leaked = "super-secret-value"
    monkeypatch.setattr(cli.config, "feishu_app_id", lambda: "cli_test")
    monkeypatch.setattr(cli.config, "feishu_app_secret", lambda: leaked)

    assert cli.main(["status", "--db", str(tmp_path / "db.sqlite3")]) == 0

    output = capsys.readouterr().out
    assert leaked not in output
    assert '"app_id": "configured"' in output
    assert '"app_secret": "configured"' in output
    assert '"network": "not_checked"' in output
    assert '"send": "not_checked"' in output


def test_setup_only_prints_manifest_by_default(monkeypatch, capsys):
    monkeypatch.setattr(cli.config, "feishu_app_id", lambda: "")
    monkeypatch.setattr(cli.config, "feishu_app_secret", lambda: "")

    assert cli.main(["setup"]) == 0

    output = capsys.readouterr().out
    assert '"mode": "offline_manifest_only"' in output
    assert "im:message.p2p_msg:readonly" in output
    assert "im:message.group_at_msg:readonly" in output
    assert "im:message:send_as_bot" in output
    assert "im.message.receive_v1" in output


def test_delivery_approve_with_closed_gates_never_builds_client(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(cli.config, "feishu_live_send_allowed", lambda: False)

    assert (
        cli.main(
            [
                "deliveries",
                "approve",
                "--db",
                str(tmp_path / "db.sqlite3"),
                "--id",
                "1",
                "--approved-by",
                "operator",
            ]
        )
        == 2
    )

    assert "outbound_gates_closed" in capsys.readouterr().out


def test_delivery_approve_is_durable_and_never_builds_second_client(
    tmp_path, monkeypatch, capsys
):
    db = tmp_path / "db.sqlite3"
    delivery = _seed_delivery(db)
    monkeypatch.setattr(cli.config, "feishu_live_send_allowed", lambda: True)
    monkeypatch.setattr(cli.config, "feishu_app_id", lambda: "cli_test")

    def forbidden_client(*_args, **_kwargs):
        raise AssertionError("CLI must not build a second Feishu client")

    monkeypatch.setattr("app.feishu.client.build_channel", forbidden_client)

    assert (
        cli.main(
            [
                "deliveries",
                "approve",
                "--db",
                str(db),
                "--id",
                str(delivery.id),
                "--approved-by",
                "operator",
            ]
        )
        == 0
    )

    saved = AutoReplyStore(db).get_feishu_delivery(delivery.id)
    assert saved.status == "ready_to_send"
    assert saved.approved_at
    assert saved.approved_by == "operator"
    output = capsys.readouterr().out
    assert "approved_pending_runtime" in output
    assert '"network": "not_checked"' in output
    assert '"send": "not_attempted"' in output


def test_delivery_approve_rejects_configured_app_mismatch(
    tmp_path, monkeypatch, capsys
):
    db = tmp_path / "db.sqlite3"
    delivery = _seed_delivery(db)
    monkeypatch.setattr(cli.config, "feishu_live_send_allowed", lambda: True)
    monkeypatch.setattr(cli.config, "feishu_app_id", lambda: "cli_other")

    assert (
        cli.main(
            [
                "deliveries",
                "approve",
                "--db",
                str(db),
                "--id",
                str(delivery.id),
                "--approved-by",
                "operator",
            ]
        )
        == 2
    )

    assert "does not match runtime" in capsys.readouterr().out
    assert AutoReplyStore(db).get_feishu_delivery(delivery.id).approved_at == ""


def test_delivery_reject_is_local_and_configured_app_bound(
    tmp_path, monkeypatch, capsys
):
    db = tmp_path / "db.sqlite3"
    delivery = _seed_delivery(db)
    monkeypatch.setattr(cli.config, "feishu_app_id", lambda: "cli_other")

    assert (
        cli.main(
            [
                "deliveries",
                "reject",
                "--db",
                str(db),
                "--id",
                str(delivery.id),
                "--rejected-by",
                "operator",
            ]
        )
        == 2
    )
    assert AutoReplyStore(db).get_feishu_delivery(delivery.id).status == "ready_to_send"
    assert "does not match runtime" in capsys.readouterr().out

    monkeypatch.setattr(cli.config, "feishu_app_id", lambda: "cli_test")
    assert (
        cli.main(
            [
                "deliveries",
                "reject",
                "--db",
                str(db),
                "--id",
                str(delivery.id),
                "--rejected-by",
                "operator",
            ]
        )
        == 0
    )
    assert AutoReplyStore(db).get_feishu_delivery(delivery.id).status == "rejected"


def test_scope_approval_is_explicit_and_audited(tmp_path, monkeypatch, capsys):
    db = tmp_path / "db.sqlite3"
    store = AutoReplyStore(db)
    store.upsert_feishu_reply_scope(
        FeishuReplyScope(
            app_id="cli_test",
            target_type="group",
            target_id="oc_cli_1",
            display_name="Test group",
            trigger_mode="mention_bot",
        )
    )
    monkeypatch.setattr(cli.config, "feishu_app_id", lambda: "cli_test")

    assert (
        cli.main(
            [
                "scopes",
                "approve",
                "--db",
                str(db),
                "--target-type",
                "group",
                "--target-id",
                "oc_cli_1",
                "--approved-by",
                "local-operator",
            ]
        )
        == 0
    )

    reviewed = store.get_feishu_reply_scope("cli_test", "group", "oc_cli_1")
    assert reviewed is not None
    assert reviewed.enabled is True
    assert reviewed.binding_status == "verified"
    assert reviewed.approved_by == "local-operator"
    assert '"binding_status": "verified"' in capsys.readouterr().out


def test_produce_once_only_attaches_stored_eligible_events(tmp_path, capsys):
    db = tmp_path / "db.sqlite3"
    store = AutoReplyStore(db)
    record = store.record_feishu_event(
        _message(),
        eligibility_status="eligible",
        store_body=True,
        enqueue_eligible=False,
    )
    assert record.reply_task_id == 0

    assert (
        cli.main(
            [
                "produce-once",
                "--db",
                str(db),
                "--app-id",
                "cli_test",
            ]
        )
        == 0
    )

    stored = store.get_feishu_event(record.id)
    assert stored is not None
    assert stored.reply_task_id > 0
    [task] = store.list_reply_tasks(channel="feishu")
    assert task.id == stored.reply_task_id
    assert '"enqueued": 1' in capsys.readouterr().out


def test_unknown_reconcile_and_requeue_require_separate_cli_actions(
    tmp_path, monkeypatch, capsys
):
    db = tmp_path / "db.sqlite3"
    delivery = _seed_delivery(db)
    store = AutoReplyStore(db)
    store.approve_feishu_delivery(
        delivery.id, app_id="cli_test", approved_by="first-reviewer"
    )
    store.claim_feishu_delivery(delivery.id, approved_only=True)
    store.mark_feishu_delivery_send_unknown(
        delivery.id,
        error_code="send_timeout",
        error="uncertain",
    )
    monkeypatch.setattr(cli.config, "feishu_app_id", lambda: "cli_test")

    assert (
        cli.main(
            [
                "deliveries",
                "reconcile",
                "--db",
                str(db),
                "--id",
                str(delivery.id),
                "--outcome",
                "not-sent",
                "--verified-by",
                "operator",
                "--evidence-kind",
                "message_lookup",
            ]
        )
        == 0
    )
    assert store.get_feishu_delivery(delivery.id).status == "failed"
    capsys.readouterr()

    assert (
        cli.main(
            [
                "deliveries",
                "requeue",
                "--db",
                str(db),
                "--id",
                str(delivery.id),
                "--verified-by",
                "operator",
                "--evidence-kind",
                "admin_audit",
            ]
        )
        == 0
    )
    saved = store.get_feishu_delivery(delivery.id)
    assert saved.status == "retry"
    assert saved.approved_at == ""
    assert '"approved": false' in capsys.readouterr().out


def test_reconcile_sent_requires_verified_remote_message_id(
    tmp_path, monkeypatch, capsys
):
    db = tmp_path / "db.sqlite3"
    delivery = _seed_delivery(db)
    store = AutoReplyStore(db)
    store.claim_feishu_delivery(delivery.id)
    store.mark_feishu_delivery_send_unknown(
        delivery.id,
        error_code="send_timeout",
        error="uncertain",
    )
    monkeypatch.setattr(cli.config, "feishu_app_id", lambda: "cli_test")
    base = [
        "deliveries",
        "reconcile",
        "--db",
        str(db),
        "--id",
        str(delivery.id),
        "--outcome",
        "sent",
        "--verified-by",
        "operator",
        "--evidence-kind",
        "feishu_ui",
    ]

    assert cli.main(base) == 2
    assert "requires Feishu message ID" in capsys.readouterr().out
    assert cli.main([*base, "--feishu-message-id", "om_remote"]) == 0
    assert store.get_feishu_delivery(delivery.id).status == "sent"


def test_reconcile_not_sent_preserves_request_log_id(
    tmp_path, monkeypatch
):
    db = tmp_path / "db.sqlite3"
    delivery = _seed_delivery(db)
    store = AutoReplyStore(db)
    store.claim_feishu_delivery(delivery.id)
    store.mark_feishu_delivery_send_unknown(
        delivery.id,
        error_code="send_timeout",
        error="uncertain",
    )
    monkeypatch.setattr(cli.config, "feishu_app_id", lambda: "cli_test")

    assert (
        cli.main(
            [
                "deliveries",
                "reconcile",
                "--db",
                str(db),
                "--id",
                str(delivery.id),
                "--outcome",
                "not-sent",
                "--verified-by",
                "operator",
                "--evidence-kind",
                "message_lookup",
                "--request-log-id",
                "log-not-sent",
            ]
        )
        == 0
    )
    assert store.get_feishu_delivery(delivery.id).request_log_id == "log-not-sent"


def test_audit_events_requires_explicit_cross_app_or_configured_app(
    tmp_path, monkeypatch, capsys
):
    db = tmp_path / "db.sqlite3"
    _seed_delivery(db)
    monkeypatch.setattr(cli.config, "feishu_app_id", lambda: "")

    assert cli.main(["audit-events", "--db", str(db)]) == 2
    assert "App ID is not configured" in capsys.readouterr().out
    assert (
        cli.main(["audit-events", "--db", str(db), "--all-apps"])
        == 0
    )
    assert "attempt_recorded" in capsys.readouterr().out

    assert (
        cli.main(
            [
                "audit-events",
                "--db",
                str(db),
                "--all-apps",
                "--app-id",
                "cli_test",
            ]
        )
        == 2
    )
    assert "either --all-apps or --app-id" in capsys.readouterr().out
