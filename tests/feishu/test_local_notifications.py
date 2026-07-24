import asyncio
import subprocess
import threading
from datetime import datetime, timedelta, timezone

from app.dingtalk_models import CodexAction, CodexDecision
from app.feishu.consumer import FeishuReplyConsumer
from app.feishu.local_notifications import FeishuLocalNotificationWorker
from app.feishu.models import FeishuInboundMessage
from app.store import AutoReplyStore
from tests.feishu.fakes import FakeRunner


APP_ID = "cli_test"


def _trigger(**updates):
    base = FeishuInboundMessage(
        event_id="evt_handoff_1",
        app_id=APP_ID,
        message_id="om_handoff_1",
        chat_id="oc_handoff",
        chat_type="group",
        chat_title="Operations",
        thread_id="omt_handoff",
        sender_open_id="ou_requester",
        sender_name="Alex",
        message_type="text",
        mentioned_bot=True,
        body_text="需要人工判断",
        event_create_time="2026-07-22T03:20:00+00:00",
        received_at="2026-07-22T03:20:01+00:00",
    )
    return base.model_copy(update=updates)


def _queue_handoff(store, *, targets=()):
    store.record_feishu_event(
        _trigger(), eligibility_status="eligible", store_body=True
    )
    FeishuReplyConsumer(
        store,
        FakeRunner(CodexDecision(action=CodexAction.HANDOFF_TO_HUMAN)),
        app_id=APP_ID,
        handoff_enabled=bool(targets),
        handoff_open_ids=tuple(targets),
    ).run_once(1)
    [notification] = store.list_feishu_local_notifications(app_id=APP_ID)
    return notification, store.list_feishu_message_actions(app_id=APP_ID)


def _claim_action(store, action):
    store.approve_feishu_message_action(
        action.id,
        app_id=APP_ID,
        approved_by="test-reviewer",
        expected_approval_hash=action.approval_hash,
    )
    claimed = store.claim_feishu_message_action(
        action.id,
        app_id=APP_ID,
        kinds=("handoff_notify",),
        send_mode="confirm",
    )
    assert claimed is not None
    return claimed


def _finish_action(store, action, status):
    claimed = _claim_action(store, action)
    fields = {}
    if status == "sent":
        fields["remote_id"] = "om_remote_receipt"
    elif status == "failed":
        fields.update(
            error_code="permission_denied",
            error="feishu_action_failed:permission_denied",
        )
    elif status == "result_unknown":
        fields.update(
            error_code="send_timeout",
            error="feishu_action_failed:send_timeout",
        )
    return store.transition_feishu_message_action(
        claimed.id,
        from_statuses=("sending",),
        to_status=status,
        app_id=APP_ID,
        expected_lease_token=claimed.lease_token,
        **fields,
    )


def test_gate_closed_fallback_is_sent_once_with_durable_receipt(tmp_path):
    store = AutoReplyStore(tmp_path / "feishu.sqlite3")
    notification, actions = _queue_handoff(store)
    calls = []
    worker = FeishuLocalNotificationWorker(
        store,
        app_id=APP_ID,
        notifier=lambda **payload: calls.append(payload),
    )

    assert asyncio.run(worker.process_once()) == 1
    assert asyncio.run(worker.process_once()) == 0

    assert len(calls) == 1
    assert calls[0]["url"] is None
    assert actions == []
    [current] = store.list_feishu_local_notifications(app_id=APP_ID)
    assert current.id == notification.id
    assert current.status == "sent"
    assert current.attempts == 1
    assert current.sent_at
    event_types = {
        event.event_type
        for event in store.list_feishu_audit_events(
            app_id=APP_ID,
            entity_type="local_notification",
            entity_id=current.id,
        )
    }
    assert {"created", "claimed", "sent"} <= event_types


def test_remote_handoff_success_cancels_local_fallback(tmp_path):
    store = AutoReplyStore(tmp_path / "feishu.sqlite3")
    _, [action] = _queue_handoff(store, targets=("ou_human",))
    _finish_action(store, action, "sent")
    calls = []
    worker = FeishuLocalNotificationWorker(
        store,
        app_id=APP_ID,
        notifier=lambda **payload: calls.append(payload),
    )

    assert asyncio.run(worker.process_once()) == 0

    assert calls == []
    [current] = store.list_feishu_local_notifications(app_id=APP_ID)
    assert current.status == "cancelled"
    assert current.error_code == "remote_sent"


def test_local_fallback_waits_until_every_remote_action_definitely_fails(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "feishu.sqlite3")
    _, actions = _queue_handoff(
        store, targets=("ou_human_a", "ou_human_b")
    )
    calls = []
    worker = FeishuLocalNotificationWorker(
        store,
        app_id=APP_ID,
        notifier=lambda **payload: calls.append(payload),
    )

    _finish_action(store, actions[0], "failed")
    assert asyncio.run(worker.process_once()) == 0
    [waiting] = store.list_feishu_local_notifications(app_id=APP_ID)
    assert waiting.status == "waiting_remote"
    assert calls == []

    store.reject_feishu_message_action(
        actions[1].id,
        app_id=APP_ID,
        rejected_by="test-reviewer",
    )
    assert asyncio.run(worker.process_once()) == 1
    assert len(calls) == 1
    [sent] = store.list_feishu_local_notifications(app_id=APP_ID)
    assert sent.status == "sent"


def test_result_unknown_never_enables_local_fallback(tmp_path):
    store = AutoReplyStore(tmp_path / "feishu.sqlite3")
    _, [action] = _queue_handoff(store, targets=("ou_human",))
    _finish_action(store, action, "result_unknown")
    calls = []
    worker = FeishuLocalNotificationWorker(
        store,
        app_id=APP_ID,
        notifier=lambda **payload: calls.append(payload),
    )

    assert asyncio.run(worker.process_once()) == 0

    assert calls == []
    [current] = store.list_feishu_local_notifications(app_id=APP_ID)
    assert current.status == "waiting_remote"


def test_untyped_sink_error_is_unknown_and_never_replayed_or_leaked(tmp_path):
    store = AutoReplyStore(tmp_path / "feishu.sqlite3")
    _queue_handoff(store)
    def fail(**_payload):
        raise RuntimeError("secret-token-must-not-be-recorded")

    worker = FeishuLocalNotificationWorker(
        store,
        app_id=APP_ID,
        notifier=fail,
        max_attempts=2,
    )

    assert asyncio.run(worker.process_once()) == 1
    assert asyncio.run(worker.process_once()) == 0
    [unknown] = store.list_feishu_local_notifications(app_id=APP_ID)
    assert unknown.status == "result_unknown"
    assert unknown.error_code == "unknown"
    assert unknown.attempts == 1
    assert "secret-token-must-not-be-recorded" not in repr(unknown)
    audits = store.list_feishu_audit_events(
        app_id=APP_ID, entity_type="local_notification"
    )
    assert "secret-token-must-not-be-recorded" not in repr(audits)


def test_nonzero_offline_sink_exit_is_unknown_and_never_replayed(
    tmp_path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "feishu.sqlite3")
    _queue_handoff(store)
    commands = []
    monkeypatch.setattr("app.notification.shutil.which", lambda _name: None)
    monkeypatch.setattr(
        "app.notification.subprocess.run",
        lambda command, **kwargs: commands.append((command, kwargs))
        or type("Completed", (), {"returncode": 1})(),
    )
    worker = FeishuLocalNotificationWorker(
        store,
        app_id=APP_ID,
        max_attempts=2,
    )

    assert asyncio.run(worker.process_once()) == 1
    assert asyncio.run(worker.process_once()) == 0
    [unknown] = store.list_feishu_local_notifications(app_id=APP_ID)
    assert unknown.status == "result_unknown"
    assert unknown.error_code == "unknown"
    assert len(commands) == 1
    assert all(call[1]["timeout"] == 10.0 for call in commands)


def test_offline_sink_timeout_is_unknown_and_never_replayed(
    tmp_path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "feishu.sqlite3")
    _queue_handoff(store)
    calls = []
    monkeypatch.setattr(
        "app.notification.shutil.which",
        lambda _name: "/opt/homebrew/bin/terminal-notifier",
    )

    def timeout(command, **kwargs):
        calls.append((command, kwargs))
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr("app.notification.subprocess.run", timeout)
    worker = FeishuLocalNotificationWorker(store, app_id=APP_ID)

    assert asyncio.run(worker.process_once()) == 1
    assert asyncio.run(worker.process_once()) == 0
    [unknown] = store.list_feishu_local_notifications(app_id=APP_ID)
    assert unknown.status == "result_unknown"
    assert unknown.error_code == "send_timeout"
    assert len(calls) == 1


def test_proven_process_not_started_retries_with_a_bound(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "feishu.sqlite3")
    _queue_handoff(store)
    clock = [datetime(2026, 7, 23, 0, 0, tzinfo=timezone.utc)]
    calls = []
    monkeypatch.setattr("app.notification.shutil.which", lambda _name: None)

    def not_started(*_args, **_kwargs):
        calls.append(True)
        raise FileNotFoundError("payload-free spawn failure")

    monkeypatch.setattr("app.notification.subprocess.run", not_started)
    worker = FeishuLocalNotificationWorker(
        store,
        app_id=APP_ID,
        max_attempts=2,
        now=lambda: clock[0],
    )

    assert asyncio.run(worker.process_once()) == 1
    [retry] = store.list_feishu_local_notifications(app_id=APP_ID)
    assert retry.status == "retry"
    assert retry.error_code == "local_notification_not_started"
    assert retry.mutation_started_at == ""

    clock[0] += timedelta(seconds=10)
    assert asyncio.run(worker.process_once()) == 1
    [failed] = store.list_feishu_local_notifications(app_id=APP_ID)
    assert failed.status == "failed"
    assert failed.attempts == 2
    assert len(calls) == 2


def test_cancellation_waits_for_bounded_sink_and_persists_receipt(tmp_path):
    store = AutoReplyStore(tmp_path / "feishu.sqlite3")
    _queue_handoff(store)
    started = threading.Event()
    release = threading.Event()

    def notify(**_payload):
        started.set()
        assert release.wait(timeout=1)

    worker = FeishuLocalNotificationWorker(
        store, app_id=APP_ID, notifier=notify
    )

    async def scenario():
        task = asyncio.create_task(worker.process_once())
        while not started.is_set():
            await asyncio.sleep(0)
        task.cancel()
        release.set()
        try:
            await task
        except asyncio.CancelledError:
            return
        raise AssertionError("worker cancellation must propagate")

    asyncio.run(scenario())

    [current] = store.list_feishu_local_notifications(app_id=APP_ID)
    assert current.status == "sent"
    assert current.sent_at


def test_stale_unfenced_claim_is_recovered_revalidated_and_audited(tmp_path):
    store = AutoReplyStore(tmp_path / "feishu.sqlite3")
    _queue_handoff(store)
    old = datetime(2026, 7, 23, 0, 0, tzinfo=timezone.utc)
    [claimed] = store.claim_feishu_local_notifications(
        1, app_id=APP_ID, now=old.isoformat()
    )
    calls = []
    worker = FeishuLocalNotificationWorker(
        store,
        app_id=APP_ID,
        notifier=lambda **payload: calls.append(payload),
        lease_stale_seconds=60,
        now=lambda: old + timedelta(seconds=61),
    )

    assert claimed.status == "sending"
    assert asyncio.run(worker.process_once()) == 1
    assert len(calls) == 1
    event_types = {
        event.event_type
        for event in store.list_feishu_audit_events(
            app_id=APP_ID, entity_type="local_notification"
        )
    }
    assert "stale_claim_recovered" in event_types


def test_stale_fenced_mutation_becomes_unknown_without_replay(tmp_path):
    store = AutoReplyStore(tmp_path / "feishu.sqlite3")
    _queue_handoff(store)
    old = datetime(2026, 7, 23, 0, 0, tzinfo=timezone.utc)
    [claimed] = store.claim_feishu_local_notifications(
        1, app_id=APP_ID, now=old.isoformat()
    )
    fenced = store.begin_feishu_local_notification_mutation(
        claimed.id,
        app_id=APP_ID,
        lease_token=claimed.lease_token,
        now=old.isoformat(),
    )
    calls = []
    worker = FeishuLocalNotificationWorker(
        store,
        app_id=APP_ID,
        notifier=lambda **payload: calls.append(payload),
        lease_stale_seconds=60,
        now=lambda: old + timedelta(seconds=61),
    )

    assert fenced is not None and fenced.mutation_started_at
    assert asyncio.run(worker.process_once()) == 0
    assert calls == []
    [unknown] = store.list_feishu_local_notifications(app_id=APP_ID)
    assert unknown.status == "result_unknown"
    assert unknown.error_code == "unknown"
    event_types = {
        event.event_type
        for event in store.list_feishu_audit_events(
            app_id=APP_ID, entity_type="local_notification"
        )
    }
    assert "stale_mutation_result_unknown" in event_types


def test_superseded_fallback_is_cancelled_and_never_sent(tmp_path):
    store = AutoReplyStore(tmp_path / "feishu.sqlite3")
    _queue_handoff(store)
    store.record_feishu_event(
        _trigger(
            event_id="evt_handoff_2",
            message_id="om_handoff_2",
            body_text="newer request",
            event_create_time="2026-07-22T03:21:00+00:00",
            received_at="2026-07-22T03:21:01+00:00",
        ),
        eligibility_status="eligible",
        store_body=True,
    )
    calls = []
    worker = FeishuLocalNotificationWorker(
        store,
        app_id=APP_ID,
        notifier=lambda **payload: calls.append(payload),
    )

    assert asyncio.run(worker.process_once()) == 0

    assert calls == []
    [current] = store.list_feishu_local_notifications(app_id=APP_ID)
    assert current.status == "cancelled"
    assert current.error_code == "superseded"


def test_newer_trigger_after_claim_but_before_mutation_fence_cancels_effect(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "feishu.sqlite3")
    _queue_handoff(store)
    [claimed] = store.claim_feishu_local_notifications(1, app_id=APP_ID)
    store.record_feishu_event(
        _trigger(
            event_id="evt_handoff_2",
            message_id="om_handoff_2",
            body_text="newer request",
            event_create_time="2026-07-22T03:21:00+00:00",
            received_at="2026-07-22T03:21:01+00:00",
        ),
        eligibility_status="eligible",
        store_body=True,
    )
    calls = []
    worker = FeishuLocalNotificationWorker(
        store,
        app_id=APP_ID,
        notifier=lambda **payload: calls.append(payload),
    )

    assert asyncio.run(worker.send_claimed(claimed)) == "cancelled"

    assert calls == []
    [current] = store.list_feishu_local_notifications(app_id=APP_ID)
    assert current.status == "cancelled"
    assert current.mutation_started_at == ""


def test_newer_trigger_after_mutation_fence_cannot_relabel_started_effect(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "feishu.sqlite3")
    _queue_handoff(store)
    calls = []

    def notify(**payload):
        calls.append(payload)
        store.record_feishu_event(
            _trigger(
                event_id="evt_handoff_2",
                message_id="om_handoff_2",
                body_text="newer request",
                event_create_time="2026-07-22T03:21:00+00:00",
                received_at="2026-07-22T03:21:01+00:00",
            ),
            eligibility_status="eligible",
            store_body=True,
        )

    worker = FeishuLocalNotificationWorker(
        store, app_id=APP_ID, notifier=notify
    )

    assert asyncio.run(worker.process_once()) == 1

    assert len(calls) == 1
    [current] = store.list_feishu_local_notifications(app_id=APP_ID)
    assert current.status == "sent"
    assert current.mutation_started_at


def test_event_retention_preserves_nonterminal_local_fallback(tmp_path):
    store = AutoReplyStore(tmp_path / "feishu.sqlite3")
    _queue_handoff(store)
    [event] = store.list_feishu_events(APP_ID)

    assert (
        store.purge_feishu_events_before("2099-01-01T00:00:00+00:00")
        == 0
    )
    assert store.get_feishu_event(event.id) is not None

    worker = FeishuLocalNotificationWorker(
        store, app_id=APP_ID, notifier=lambda **_payload: None
    )
    assert asyncio.run(worker.process_once()) == 1
    assert (
        store.purge_feishu_events_before("2099-01-01T00:00:00+00:00")
        == 1
    )
    assert store.get_feishu_event(event.id) is None


def test_event_retention_preserves_unknown_local_effect_evidence(tmp_path):
    store = AutoReplyStore(tmp_path / "feishu.sqlite3")
    _queue_handoff(store)

    def uncertain(**_payload):
        raise RuntimeError("opaque detail")

    worker = FeishuLocalNotificationWorker(
        store, app_id=APP_ID, notifier=uncertain
    )
    assert asyncio.run(worker.process_once()) == 1
    [notification] = store.list_feishu_local_notifications(app_id=APP_ID)
    assert notification.status == "result_unknown"

    assert (
        store.purge_feishu_events_before("2099-01-01T00:00:00+00:00")
        == 0
    )
    assert store.list_feishu_events(APP_ID)
