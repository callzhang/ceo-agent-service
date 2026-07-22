import json
from datetime import datetime, timezone

from app.feishu import cli
from app.feishu.actions import build_message_action
from app.feishu.delivery import delivery_idempotency_key
from app.feishu.maintenance import FeishuMaintenanceResult
from app.feishu.models import FeishuInboundMessage, FeishuReplyScope
from app.store import AutoReplyStore


def _message(*, app_id: str = "cli_test", suffix: str = "1") -> FeishuInboundMessage:
    now = datetime.now(timezone.utc).isoformat()
    return FeishuInboundMessage(
        event_id=f"evt-cli-{suffix}",
        app_id=app_id,
        message_id=f"om_cli_{suffix}",
        chat_id=f"oc_cli_{suffix}",
        chat_type="group",
        sender_open_id="ou_cli_1",
        sender_name="Alice",
        message_type="text",
        mentioned_bot=True,
        body_text="请给出建议",
        event_create_time=now,
        received_at=now,
    )


def _seed_delivery(
    db,
    *,
    app_id: str = "cli_test",
    suffix: str = "1",
    reply_text: str = "已审核草稿",
):
    store = AutoReplyStore(db)
    message = _message(app_id=app_id, suffix=suffix)
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
        draft_reply_text=reply_text,
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
        reply_text=reply_text,
        idempotency_key=delivery_idempotency_key(
            app_id=message.app_id,
            reply_task_id=event.reply_task_id,
            trigger_message_id=message.message_id,
        ),
    )


def _seed_sent(db, *message_ids: str):
    delivery = _seed_delivery(db)
    store = AutoReplyStore(db)
    claimed = store.claim_feishu_delivery(delivery.id, app_id=delivery.app_id)
    assert claimed is not None
    store.transition_feishu_delivery(
        delivery.id,
        from_statuses=("sending",),
        to_status="sent",
        app_id=delivery.app_id,
        expected_lease_token=claimed.lease_token,
        feishu_message_id=message_ids[0],
        message_ids=message_ids,
    )
    return store, store.get_feishu_delivery(delivery.id)


def _seed_action(db, *, kind="add_reaction", text=""):
    store, delivery = _seed_sent(db, "om_cli_bot")
    target_message_id = delivery.reply_to_message_id
    target_open_id = ""
    payload = {"emoji_type": "OK"}
    allowlist = ()
    if kind == "recall_message":
        [receipt] = store.list_feishu_delivery_receipts(
            delivery_id=delivery.id
        )
        target_message_id = receipt.message_id
        payload = {}
    elif kind == "handoff_notify":
        target_message_id = ""
        target_open_id = "ou_cli_private_owner"
        payload = {"text": text or "private handoff"}
        allowlist = (target_open_id,)
    action = build_message_action(
        reply_task_id=delivery.reply_task_id,
        attempt_id=delivery.attempt_id,
        app_id=delivery.app_id,
        chat_id=delivery.chat_id,
        action_key=f"cli:{kind}",
        kind=kind,
        target_message_id=target_message_id,
        target_open_id=target_open_id,
        payload=payload,
    )
    return store, store.create_feishu_message_action(
        action,
        handoff_target_allowlist=allowlist,
    )


def _mark_action_unknown(store, action, *, request_log_id="log_unknown"):
    if action.risk == "R4":
        action = store.approve_feishu_message_action(
            action.id,
            app_id=action.app_id,
            approved_by="preflight-reviewer",
            expected_approval_hash=action.approval_hash,
        )
    claimed = store.claim_feishu_message_action(
        action.id,
        app_id=action.app_id,
        kinds=(action.kind,),
        send_mode="auto",
    )
    assert claimed is not None
    return store.transition_feishu_message_action(
        action.id,
        from_statuses=("sending",),
        to_status="result_unknown",
        app_id=action.app_id,
        expected_lease_token=claimed.lease_token,
        request_log_id=request_log_id,
        error_code="unknown",
        error="provider_result_unknown",
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


def test_status_counts_only_the_configured_app(tmp_path, monkeypatch, capsys):
    db = tmp_path / "db.sqlite3"
    _seed_delivery(db, app_id="cli_test", suffix="configured")
    _seed_delivery(db, app_id="cli_other", suffix="other")
    store = AutoReplyStore(db)
    for app_id, target_id in (
        ("cli_test", "oc_configured"),
        ("cli_other", "oc_other"),
    ):
        store.upsert_feishu_reply_scope(
            FeishuReplyScope(
                app_id=app_id,
                target_type="group",
                target_id=target_id,
                display_name=target_id,
                trigger_mode="mention_bot",
            )
        )
    monkeypatch.setattr(cli.config, "feishu_app_id", lambda: "cli_test")
    monkeypatch.setattr(cli.config, "feishu_app_secret", lambda: "")

    assert cli.main(["status", "--db", str(db)]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["scope_counts"] == {"pending": 1, "verified": 0}
    assert payload["delivery_counts"]["ready_to_send"] == 1


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
                "--approval-hash",
                "0" * 64,
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
                "--approval-hash",
                delivery.approval_hash,
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


def test_delivery_cli_is_hash_bound_and_list_is_payload_identity_redacted(
    tmp_path, monkeypatch, capsys
):
    db = tmp_path / "db.sqlite3"
    delivery = _seed_delivery(db)
    monkeypatch.setattr(cli.config, "feishu_live_send_allowed", lambda: True)
    monkeypatch.setattr(cli.config, "feishu_app_id", lambda: "cli_test")

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
                "--approval-hash",
                "0" * 64,
            ]
        )
        == 2
    )
    assert "approval hash changed" in capsys.readouterr().out
    assert AutoReplyStore(db).get_feishu_delivery(delivery.id).approved_at == ""

    assert (
        cli.main(
            [
                "deliveries",
                "list",
                "--db",
                str(db),
                "--app-id",
                "cli_test",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "已审核草稿" not in output
    assert delivery.chat_id not in output
    assert delivery.reply_to_message_id not in output
    assert delivery.idempotency_key not in output
    assert delivery.approval_hash not in output
    assert "approval_hash" not in output
    assert "[redacted:" in output

    assert (
        cli.main(
            [
                "deliveries",
                "list",
                "--db",
                str(db),
                "--app-id",
                "cli_test",
                "--include-preview",
            ]
        )
        == 0
    )
    [review_item] = json.loads(capsys.readouterr().out)
    assert review_item["approval_hash"] == delivery.approval_hash
    assert review_item["approval_preview"]["text"] == "已审核草稿"
    assert review_item["approval_preview"]["target"][
        "message_fingerprint"
    ].startswith("sha256:")
    serialized_review = json.dumps(review_item, ensure_ascii=False)
    assert delivery.chat_id not in serialized_review
    assert delivery.reply_to_message_id not in serialized_review


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
                "--approval-hash",
                delivery.approval_hash,
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


def test_maintenance_runs_media_before_events_with_configured_retention(
    tmp_path, monkeypatch, capsys
):
    from app.feishu import maintenance

    captured = {}

    def fake_purge(store, **kwargs):
        captured.update(kwargs)
        return FeishuMaintenanceResult(
            cutoff="2026-07-01T00:00:00+00:00",
            deleted_events=0,
            batches=1,
            more_may_remain=False,
            media_cutoff="2026-07-15T00:00:00+00:00",
        )

    monkeypatch.setattr(maintenance, "purge_expired_feishu_events", fake_purge)
    monkeypatch.setattr(cli.config, "feishu_event_retention_days", lambda: 30)
    monkeypatch.setattr(cli.config, "feishu_media_retention_days", lambda: 7)

    assert cli.main(
        [
            "maintenance-once",
            "--db",
            str(tmp_path / "db.sqlite3"),
            "--all-apps",
        ]
    ) == 0
    assert captured["retention_days"] == 30
    assert captured["media_retention_days"] == 7
    assert '"media_cutoff"' in capsys.readouterr().out


def test_maintenance_defaults_to_configured_app_and_rejects_ambiguous_scope(
    tmp_path, monkeypatch, capsys
):
    from app.feishu import maintenance

    calls = []

    def fake_purge(store, **kwargs):
        calls.append(kwargs)
        return FeishuMaintenanceResult(
            cutoff="2026-07-01T00:00:00+00:00",
            deleted_events=0,
            batches=1,
            more_may_remain=False,
            media_cutoff="2026-07-15T00:00:00+00:00",
        )

    monkeypatch.setattr(maintenance, "purge_expired_feishu_events", fake_purge)
    monkeypatch.setattr(cli.config, "feishu_app_id", lambda: "cli_test")
    db = str(tmp_path / "db.sqlite3")

    assert cli.main(["maintenance-once", "--db", db]) == 0
    assert calls[-1]["app_id"] == "cli_test"
    capsys.readouterr()

    assert cli.main(
        [
            "maintenance-once",
            "--db",
            db,
            "--all-apps",
            "--app-id",
            "cli_test",
        ]
    ) == 2
    assert "either --all-apps or --app-id" in capsys.readouterr().out
    assert len(calls) == 1


def test_app_scoped_local_commands_fail_closed_without_configured_app(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(cli.config, "feishu_app_id", lambda: "")
    db = str(tmp_path / "db.sqlite3")
    commands = (
        ["maintenance-once", "--db", db],
        ["produce-once", "--db", db],
        ["scopes", "list", "--db", db],
        ["deliveries", "list", "--db", db],
    )

    for command in commands:
        assert cli.main(command) == 2
        assert "App ID is not configured" in capsys.readouterr().out


def test_unknown_reconcile_and_requeue_require_separate_cli_actions(
    tmp_path, monkeypatch, capsys
):
    db = tmp_path / "db.sqlite3"
    delivery = _seed_delivery(db)
    store = AutoReplyStore(db)
    store.approve_feishu_delivery(
        delivery.id,
        app_id="cli_test",
        approved_by="first-reviewer",
        expected_approval_hash=delivery.approval_hash,
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


def test_reconcile_sent_requires_declared_ordered_chunk_plan(
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
    assert "expected chunk count" in capsys.readouterr().out
    assert (
        cli.main(
            [
                *base,
                "--expected-chunks",
                "1",
                "--message-id",
                "om_remote",
            ]
        )
        == 0
    )
    assert store.get_feishu_delivery(delivery.id).status == "sent"


def test_reconcile_verified_partial_prefix_reports_suffix_resume(
    tmp_path, monkeypatch, capsys
):
    db = tmp_path / "db.sqlite3"
    delivery = _seed_delivery(db, reply_text="x" * 5000)
    assert delivery.expected_chunks == 2
    store = AutoReplyStore(db)
    store.claim_feishu_delivery(delivery.id)
    store.mark_feishu_delivery_send_unknown(
        delivery.id,
        error_code="send_timeout",
        error="uncertain second chunk",
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
                "sent",
                "--verified-by",
                "operator",
                "--evidence-kind",
                "message_lookup",
                "--expected-chunks",
                "2",
                "--message-id",
                "om_verified_first",
            ]
        )
        == 0
    )

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "retry"
    assert output["verified_chunks"] == 1
    assert "unverified suffix" in output["next"]


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


def test_receipts_and_actions_list_are_app_scoped_and_sanitized(
    tmp_path, monkeypatch, capsys
):
    db = tmp_path / "db.sqlite3"
    private_text = "PRIVATE-CLI-HANDOFF"
    _, action = _seed_action(db, kind="handoff_notify", text=private_text)
    monkeypatch.setattr(cli.config, "feishu_app_id", lambda: "cli_test")

    assert (
        cli.main(
            [
                "receipts",
                "list",
                "--db",
                str(db),
                "--status",
                "active",
            ]
        )
        == 0
    )
    receipts = capsys.readouterr().out
    assert "om_cli_bot" in receipts
    assert '"status": "active"' in receipts

    assert (
        cli.main(
            [
                "actions",
                "list",
                "--db",
                str(db),
                "--kind",
                "handoff_notify",
            ]
        )
        == 0
    )
    actions = capsys.readouterr().out
    assert "handoff text=[redacted:" in actions
    assert "approval_hash" not in actions
    assert private_text not in actions
    assert "ou_cli_private_owner" not in actions
    assert "payload_json" not in actions
    assert "target_open_id" not in actions

    assert (
        cli.main(
            [
                "actions",
                "list",
                "--db",
                str(db),
                "--kind",
                "handoff_notify",
                "--include-preview",
            ]
        )
        == 0
    )
    [review_item] = json.loads(capsys.readouterr().out)
    assert review_item["approval_hash"] == action.approval_hash
    assert review_item["approval_preview"]["effect"]["text"] == private_text
    serialized_review = json.dumps(review_item, ensure_ascii=False)
    assert "ou_cli_private_owner" not in serialized_review
    assert review_item["approval_preview"]["target"]["fingerprint"].startswith(
        "sha256:"
    )

    monkeypatch.setattr(cli.config, "feishu_app_id", lambda: "other_app")
    assert cli.main(["actions", "list", "--db", str(db)]) == 0
    assert capsys.readouterr().out.strip() == "[]"


def test_action_approve_is_hash_bound_offline_and_pending_runtime(
    tmp_path, monkeypatch, capsys
):
    db = tmp_path / "db.sqlite3"
    store, action = _seed_action(db, kind="recall_message")
    monkeypatch.setattr(cli.config, "feishu_app_id", lambda: "cli_test")
    monkeypatch.setattr(cli.config, "feishu_recall_enabled", lambda: True)
    monkeypatch.setattr(cli.config, "feishu_live_send_allowed", lambda: True)

    def forbidden_client(*_args, **_kwargs):
        raise AssertionError("CLI action review must not build a Feishu client")

    monkeypatch.setattr("app.feishu.client.build_channel", forbidden_client)
    base = [
        "actions",
        "approve",
        "--db",
        str(db),
        "--id",
        str(action.id),
        "--approved-by",
        "human-owner",
        "--approval-hash",
    ]

    assert cli.main([*base, "0" * 64]) == 2
    assert "approval hash changed" in capsys.readouterr().out
    assert store.get_feishu_message_action(action.id).approved_at == ""

    assert cli.main([*base, action.approval_hash]) == 0
    output = capsys.readouterr().out
    saved = store.get_feishu_message_action(action.id)
    assert saved.approved_by == "human-owner"
    assert saved.status == "ready"
    assert "approved_pending_runtime" in output
    assert '"network": "not_checked"' in output
    assert '"send": "not_attempted"' in output


def test_action_cli_approval_checks_gate_but_rejection_remains_available(
    tmp_path, monkeypatch, capsys
):
    db = tmp_path / "db.sqlite3"
    store, action = _seed_action(db)
    approve = [
        "actions",
        "approve",
        "--db",
        str(db),
        "--id",
        str(action.id),
        "--approved-by",
        "reviewer",
        "--approval-hash",
        action.approval_hash,
    ]
    reject = [
        "actions",
        "reject",
        "--db",
        str(db),
        "--id",
        str(action.id),
        "--rejected-by",
        "reviewer",
    ]
    monkeypatch.setattr(cli.config, "feishu_live_send_allowed", lambda: True)
    monkeypatch.setattr(cli.config, "feishu_reaction_enabled", lambda: False)
    monkeypatch.setattr(cli.config, "feishu_app_id", lambda: "cli_test")

    assert cli.main(approve) == 2
    assert "kind gate is closed" in capsys.readouterr().out
    monkeypatch.setattr(cli.config, "feishu_app_id", lambda: "other_app")
    assert cli.main(reject) == 2
    assert "does not match runtime" in capsys.readouterr().out

    monkeypatch.setattr(cli.config, "feishu_app_id", lambda: "cli_test")
    assert cli.main(reject) == 0
    output = capsys.readouterr().out
    assert '"status": "rejected"' in output
    assert '"send": "not_attempted"' in output
    assert store.get_feishu_message_action(action.id).status == "rejected"


def test_action_cli_reconcile_uses_kind_specific_ids_and_never_networks(
    tmp_path, monkeypatch, capsys
):
    db = tmp_path / "reaction.sqlite3"
    store, action = _seed_action(db)
    action = _mark_action_unknown(store, action)
    monkeypatch.setattr(cli.config, "feishu_app_id", lambda: "cli_test")
    monkeypatch.setattr(cli.config, "feishu_reaction_enabled", lambda: False)
    monkeypatch.setattr(cli.config, "feishu_live_send_allowed", lambda: False)

    def forbidden_client(*_args, **_kwargs):
        raise AssertionError("action reconciliation must not build a client")

    monkeypatch.setattr("app.feishu.client.build_channel", forbidden_client)
    base = [
        "actions",
        "reconcile",
        "--db",
        str(db),
        "--id",
        str(action.id),
        "--outcome",
        "applied",
        "--verified-by",
        "operator",
        "--evidence-kind",
        "message_lookup",
    ]
    assert cli.main([*base, "--message-id", "om_wrong_kind"]) == 2
    assert "requires --reaction-id" in capsys.readouterr().out

    assert cli.main([*base, "--reaction-id", "omr_verified"]) == 0
    output = capsys.readouterr().out
    assert '"status": "sent"' in output
    assert '"network": "not_checked"' in output
    assert '"send": "not_attempted"' in output
    assert "omr_verified" not in output
    assert store.get_feishu_message_action(action.id).remote_id == "omr_verified"


def test_action_cli_verified_not_applied_requires_separate_requeue(
    tmp_path, monkeypatch, capsys
):
    db = tmp_path / "recall.sqlite3"
    store, action = _seed_action(db, kind="recall_message")
    action = _mark_action_unknown(store, action)
    monkeypatch.setattr(cli.config, "feishu_app_id", lambda: "cli_test")
    monkeypatch.setattr(cli.config, "feishu_recall_enabled", lambda: False)
    monkeypatch.setattr(cli.config, "feishu_live_send_allowed", lambda: False)

    reconcile = [
        "actions",
        "reconcile",
        "--db",
        str(db),
        "--id",
        str(action.id),
        "--outcome",
        "not-applied",
        "--verified-by",
        "operator",
        "--evidence-kind",
        "feishu_ui",
    ]
    assert cli.main(reconcile) == 0
    output = capsys.readouterr().out
    assert '"status": "failed"' in output
    saved = store.get_feishu_message_action(action.id)
    assert saved.error_code == "verified_not_applied"

    requeue = [
        "actions",
        "requeue",
        "--db",
        str(db),
        "--id",
        str(action.id),
        "--verified-by",
        "operator",
        "--evidence-kind",
        "admin_audit",
    ]
    assert cli.main(requeue) == 0
    output = capsys.readouterr().out
    assert '"status": "retry"' in output
    assert '"approved": false' in output
    saved = store.get_feishu_message_action(action.id)
    assert saved.status == "retry"
    assert saved.approved_at == ""
