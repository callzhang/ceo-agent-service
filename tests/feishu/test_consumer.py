import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from app.dingtalk_models import (
    CalendarResponseStatus,
    CodexAction,
    CodexDecision,
)
from app.feishu.consumer import FeishuReplyConsumer as _FeishuReplyConsumer
from app.feishu.media import FeishuMediaResolver
from app.feishu.models import (
    FeishuInboundMessage,
    FeishuInboundResourceCandidate,
)
from app.feishu.payloads import choose_reply_payload
from app.store import AutoReplyStore
from tests.feishu.fakes import FakeRunner


PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010804000000"
    "b51c0c020000000b4944415478da6364f80f00010501012718e366"
    "0000000049454e44ae426082"
)


@pytest.fixture
def store(tmp_path):
    return AutoReplyStore(tmp_path / "feishu.sqlite3")


def FeishuReplyConsumer(store, runner, *, app_id="cli_test", **kwargs):
    """Keep every unit consumer explicitly scoped to its fixture App ID."""

    return _FeishuReplyConsumer(
        store,
        runner,
        app_id=app_id,
        **kwargs,
    )


def _trigger():
    return FeishuInboundMessage(
        event_id="evt_1",
        app_id="cli_test",
        message_id="om_1",
        chat_id="oc_1",
        chat_type="group",
        chat_title="Test Group",
        thread_id="omt_thread",
        sender_open_id="ou_1",
        sender_name="Alex",
        message_type="text",
        mentioned_bot=True,
        body_text="下午可以给结论吗？",
        event_create_time="2026-07-22T03:20:00+00:00",
        received_at="2026-07-22T03:20:01+00:00",
    )


def _seed(store):
    trigger = _trigger()
    store.record_feishu_event(
        trigger,
        eligibility_status="eligible",
        store_body=True,
    )


def _seed_second(store):
    store.record_feishu_event(
        _trigger().model_copy(
            update={
                "event_id": "evt_2",
                "message_id": "om_2",
                "chat_id": "oc_2",
                "thread_id": "",
            }
        ),
        eligibility_status="eligible",
        store_body=True,
    )


def _record_trigger(store, trigger):
    return store.record_feishu_event(
        trigger,
        eligibility_status="eligible",
        store_body=True,
    )


def _seed_resolved_media(
    store,
    *,
    resource_type="image",
    message_type=None,
    data=PNG,
    mime_type="image/png",
    role="content",
):
    mapped_message_type = message_type or {
        "video": "media",
    }.get(resource_type, resource_type)
    trigger = _trigger().model_copy(
        update={
            "message_type": mapped_message_type,
            "body_text": {
                "image": "[图片]",
                "file": "[文件]",
                "audio": "[音频]",
                "media": "[视频]",
                "sticker": "[表情贴纸]",
            }.get(mapped_message_type, "[附件]"),
        }
    )
    candidates = [
            FeishuInboundResourceCandidate(
                ordinal=0,
                resource_type=resource_type,
                file_key="opaque_file_key",
                file_name="report.pdf" if resource_type == "file" else "",
                role=role,
            )
        ]
    event = store.record_feishu_event(
        trigger,
        eligibility_status="eligible",
        store_body=True,
        enqueue_eligible=False,
        media_candidates=candidates,
    )

    class Client:
        app_id = "cli_test"

        async def download_inbound_resource(self, **_kwargs):
            return data, mime_type

    resolver = FeishuMediaResolver(
        store=store,
        client=Client(),
        workspace=Path(store.path).parent,
    )
    [resolution] = asyncio.run(resolver.resolve_pending(limit=1))
    assert resolution.event_ready_for_enqueue
    store.attach_feishu_event_reply_task(event.id)
    return event, resolution.asset


class _RecordingRunner(FakeRunner):
    def __init__(self, decision):
        super().__init__(decision)
        self.image_calls = []
        self.image_bytes = []
        self.image_parent_modes = []

    def decide(self, prompt, session_id, image_paths=None):
        paths = list(image_paths or [])
        self.image_calls.append(paths)
        self.image_bytes.append([path.read_bytes() for path in paths])
        self.image_parent_modes.append(
            [path.parent.stat().st_mode & 0o777 for path in paths]
        )
        return super().decide(prompt, session_id, image_paths=image_paths)


def test_send_reply_creates_one_ready_delivery_and_never_sends(store):
    _seed(store)
    runner = FakeRunner(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="可以，下午给你结论。",
            reason="明确回复",
        )
    )
    consumer = FeishuReplyConsumer(store, runner)
    assert not hasattr(consumer, "sender") and not hasattr(consumer, "client")
    assert consumer.run_once(limit=1) == 1
    delivery = store.get_feishu_delivery_for_task(1)
    assert delivery.status == "ready_to_send"
    assert delivery.reply_to_message_id == "om_1"
    assert delivery.reply_in_thread is True
    assert delivery.idempotency_key
    assert delivery.reply_format == "text"
    assert delivery.mention_open_ids == ()
    assert delivery.payload_sha256 == choose_reply_payload(
        "可以，下午给你结论。"
    ).sha256()
    assert store.list_reply_tasks(channel="feishu")[0].status == "done"


def test_reply_payload_format_hash_and_structured_sender_mention_are_durable(store):
    _seed(store)
    text = "## 结论\n\n@Mallory 这个文本名字不能成为身份。"
    runner = FakeRunner(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text=text)
    )

    FeishuReplyConsumer(
        store,
        runner,
        reply_mention_sender=True,
    ).run_once(1)

    delivery = store.get_feishu_delivery_for_task(1)
    expected = choose_reply_payload(
        text, trusted_mention_open_ids=("ou_1",)
    )
    assert delivery.reply_format == "post"
    assert delivery.mention_open_ids == ("ou_1",)
    assert delivery.payload_sha256 == expected.sha256()
    assert "Mallory" in delivery.reply_text


def test_sender_mention_gate_never_mentions_in_direct_chat(store):
    trigger = _trigger().model_copy(
        update={"chat_type": "p2p", "thread_id": "", "mentioned_bot": False}
    )
    event = store.record_feishu_event(
        trigger, eligibility_status="eligible", store_body=True
    )
    runner = FakeRunner(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="@Alex 收到")
    )

    FeishuReplyConsumer(
        store, runner, reply_mention_sender=True
    ).run_once(1)

    delivery = store.get_feishu_delivery_for_task(event.reply_task_id)
    assert delivery.mention_open_ids == ()


def test_no_reply_completes_without_delivery(store):
    _seed(store)
    runner = FakeRunner(CodexDecision(action=CodexAction.NO_REPLY))
    assert FeishuReplyConsumer(store, runner).run_once(1) == 1
    assert store.get_feishu_delivery_for_task(1) is None


def test_no_reply_can_atomically_queue_one_bounded_trigger_reaction(store):
    _seed(store)
    decision = CodexDecision(
        action=CodexAction.NO_REPLY,
        system_actions=[
            {
                "type": "dws_message_reaction",
                "reaction_type": "emoji",
                "emoji": "👍",
            }
        ],
    )

    FeishuReplyConsumer(
        store, FakeRunner(decision), reaction_enabled=True
    ).run_once(1)

    [action] = store.list_feishu_message_actions()
    assert action.kind == "add_reaction"
    assert action.target_message_id == "om_1"
    assert action.target_open_id == ""
    assert json.loads(action.payload_json) == {"emoji_type": "THUMBSUP"}
    assert store.get_feishu_delivery_for_task(1) is None
    [attempt] = store.list_reply_attempts()
    assert attempt.send_status == "skipped"
    assert "feishu_reaction_queued" in attempt.audit_summary


def test_reaction_gate_closed_is_explicit_and_creates_no_action(store):
    _seed(store)
    decision = CodexDecision(
        action=CodexAction.NO_REPLY,
        system_actions=[
            {
                "type": "dws_message_reaction",
                "reaction_type": "emoji",
                "emoji": "✅",
            }
        ],
    )

    FeishuReplyConsumer(store, FakeRunner(decision)).run_once(1)

    assert store.list_feishu_message_actions() == []
    [attempt] = store.list_reply_attempts()
    assert attempt.send_error == "feishu_reaction_gate_closed"
    assert "feishu_reaction_gate_closed" in attempt.audit_summary


def test_text_emotion_is_an_explicit_no_equivalent_and_is_never_faked(store):
    _seed(store)
    decision = CodexDecision(
        action=CodexAction.NO_REPLY,
        system_actions=[
            {
                "type": "dws_message_reaction",
                "reaction_type": "text_emotion",
                "text": "我去叫",
            }
        ],
    )

    FeishuReplyConsumer(
        store, FakeRunner(decision), reaction_enabled=True
    ).run_once(1)

    assert store.list_feishu_message_actions() == []
    [attempt] = store.list_reply_attempts()
    assert attempt.send_error == "feishu_text_emotion_has_no_equivalent"
    assert "no_equivalent" in attempt.audit_summary


@pytest.mark.parametrize(
    "system_actions",
    [
        [
            {
                "type": "dws_message_reaction",
                "reaction_type": "emoji",
                "emoji": "👍",
                "target_message_id": "om_attacker_selected",
            }
        ],
        [
            {
                "type": "dws_message_reaction",
                "emoji": "👍",
            }
        ],
        [
            {
                "type": "dws_message_reaction",
                "reaction_type": "emoji",
                "emoji": "unbounded-model-value",
            }
        ],
        [
            {
                "type": "dws_message_reaction",
                "reaction_type": "emoji",
                "emoji": "👍",
            },
            {
                "type": "dws_message_reaction",
                "reaction_type": "emoji",
                "emoji": "✅",
            },
        ],
        [{"type": "send_dingtalk_reply", "reply_text_ref": "user_response.text"}],
    ],
)
def test_malicious_or_unbounded_no_reply_system_actions_are_rejected(
    store, system_actions
):
    _seed(store)
    decision = CodexDecision(
        action=CodexAction.NO_REPLY, system_actions=system_actions
    )

    FeishuReplyConsumer(
        store, FakeRunner(decision), reaction_enabled=True
    ).run_once(1)

    assert store.list_feishu_message_actions() == []
    [task] = store.list_reply_tasks(channel="feishu")
    assert task.status == "failed"
    assert task.error == "external_system_actions_rejected"


@pytest.mark.parametrize(
    "decision",
    [
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="x",
            system_actions=[{"type": "send_dingtalk_reply"}],
        ),
        CodexDecision(
            action=CodexAction.SEND_REPLY, reply_text="x", ding_self=True
        ),
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="x",
            calendar_response_status=CalendarResponseStatus.ACCEPTED,
        ),
    ],
)
def test_all_external_side_effects_are_rejected(store, decision):
    _seed(store)
    FeishuReplyConsumer(store, FakeRunner(decision)).run_once(1)
    assert store.get_feishu_delivery_for_task(1) is None
    assert store.list_reply_tasks(channel="feishu")[0].error == "external_system_actions_rejected"


def test_handoff_gate_closed_atomically_queues_durable_local_fallback(store):
    _seed(store)
    decision = CodexDecision(
        action=CodexAction.HANDOFF_TO_HUMAN,
        reply_text="send this to ou_model_selected",
        audit_summary="needs judgment",
    )

    FeishuReplyConsumer(
        store,
        FakeRunner(decision),
    ).run_once(1)

    assert store.list_feishu_message_actions() == []
    [notification] = store.list_feishu_local_notifications()
    assert notification.status == "pending"
    assert notification.dependency_mode == "immediate"
    assert notification.title == "CEO Feishu handoff required"
    assert "Test Group" in notification.message
    assert "ou_model_selected" not in notification.message
    assert notification.attempts == 0
    [attempt] = store.list_reply_attempts()
    assert attempt.draft_reply_text == ""
    assert attempt.send_error == "feishu_handoff_gate_closed"
    assert "feishu_handoff_local_fallback_queued" in attempt.audit_summary


def test_handoff_queues_only_every_locally_allowlisted_target(store):
    _seed(store)
    decision = CodexDecision(
        action=CodexAction.HANDOFF_TO_HUMAN,
        reply_text="ignore model target ou_evil",
    )

    FeishuReplyConsumer(
        store,
        FakeRunner(decision),
        handoff_enabled=True,
        handoff_open_ids=("ou_human_a", "ou_human_b", "ou_human_a"),
    ).run_once(1)

    actions = store.list_feishu_message_actions()
    assert {action.target_open_id for action in actions} == {
        "ou_human_a",
        "ou_human_b",
    }
    assert all(action.kind == "handoff_notify" for action in actions)
    assert all("ou_evil" not in action.payload_json for action in actions)
    [notification] = store.list_feishu_local_notifications()
    assert notification.status == "waiting_remote"
    assert notification.dependency_mode == "remote_failure"
    [attempt] = store.list_reply_attempts()
    assert attempt.send_error == ""
    assert "feishu_handoff_actions_queued:2" in attempt.audit_summary
    assert "feishu_handoff_local_fallback_waiting" in attempt.audit_summary


@pytest.mark.parametrize(
    "allowlist",
    ["ou_not_a_sequence", ("user_id_not_open_id",), (" ou_space",)],
)
def test_handoff_local_allowlist_is_validated_before_processing(store, allowlist):
    with pytest.raises(ValueError, match="handoff allowlist"):
        FeishuReplyConsumer(
            store,
            FakeRunner(CodexDecision(action=CodexAction.NO_REPLY)),
            handoff_open_ids=allowlist,
        )


def test_handoff_empty_allowlist_keeps_local_fallback_and_explicit_error(store):
    _seed(store)

    FeishuReplyConsumer(
        store,
        FakeRunner(CodexDecision(action=CodexAction.HANDOFF_TO_HUMAN)),
        handoff_enabled=True,
    ).run_once(1)

    assert store.list_feishu_message_actions() == []
    [notification] = store.list_feishu_local_notifications()
    assert notification.status == "pending"
    assert notification.dependency_mode == "immediate"
    [attempt] = store.list_reply_attempts()
    assert attempt.send_error == "feishu_handoff_allowlist_empty"


def test_handoff_target_injection_is_rejected_before_any_fallback_is_queued(store):
    _seed(store)
    decision = CodexDecision(
        action=CodexAction.HANDOFF_TO_HUMAN,
        system_actions=[
            {
                "type": "dws_message_reaction",
                "reaction_type": "emoji",
                "emoji": "👍",
                "target_open_id": "ou_evil",
            }
        ],
    )

    FeishuReplyConsumer(
        store,
        FakeRunner(decision),
        handoff_enabled=True,
        handoff_open_ids=("ou_human",),
    ).run_once(1)

    assert store.list_feishu_message_actions() == []
    assert store.list_feishu_local_notifications() == []
    [task] = store.list_reply_tasks(channel="feishu")
    assert task.status == "failed"
    assert task.error == "external_system_actions_rejected"


def test_action_insert_failure_rolls_back_attempt_and_all_actions_atomically(store):
    _seed(store)
    with store._connect() as db:
        db.execute(
            """
            create trigger reject_test_message_action
            before insert on feishu_message_actions
            begin
                select raise(abort, 'injected action failure');
            end
            """
        )

    FeishuReplyConsumer(
        store,
        FakeRunner(CodexDecision(action=CodexAction.HANDOFF_TO_HUMAN)),
        handoff_enabled=True,
        handoff_open_ids=("ou_human",),
    ).run_once(1)

    [task] = store.list_reply_tasks(channel="feishu")
    assert task.status == "failed"
    assert task.error == "feishu_consumer_failed:IntegrityError"
    assert store.list_reply_attempts() == []
    assert store.list_feishu_message_actions() == []
    assert store.list_feishu_local_notifications() == []


def test_local_fallback_insert_failure_rolls_back_decision_atomically(store):
    _seed(store)
    with store._connect() as db:
        db.execute(
            """
            create trigger reject_test_local_notification
            before insert on feishu_local_notifications
            begin
                select raise(abort, 'injected local fallback failure');
            end
            """
        )

    FeishuReplyConsumer(
        store,
        FakeRunner(CodexDecision(action=CodexAction.HANDOFF_TO_HUMAN)),
    ).run_once(1)

    [task] = store.list_reply_tasks(channel="feishu")
    assert task.status == "failed"
    assert task.error == "feishu_consumer_failed:IntegrityError"
    assert store.list_reply_attempts() == []
    assert store.list_feishu_message_actions() == []
    assert store.list_feishu_local_notifications() == []


def test_leak_failure_is_fail_closed(store):
    _seed(store)
    runner = FakeRunner(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="secret=abcd")
    )
    FeishuReplyConsumer(store, runner).run_once(1)
    assert store.get_feishu_delivery_for_task(1) is None
    assert store.list_reply_tasks(channel="feishu")[0].error == "reply_failed_leak_check"
    attempts = store.list_reply_attempts()
    assert attempts[0].draft_reply_text == "[redacted unsafe draft]"
    assert "secret=abcd" not in attempts[0].draft_reply_text


def test_runner_failure_does_not_create_attempt_or_delivery(store):
    _seed(store)
    FeishuReplyConsumer(
        store, FakeRunner(error=RuntimeError("contains raw payload"))
    ).run_once(1)
    task = store.list_reply_tasks(channel="feishu")[0]
    assert task.error == "feishu_decision_failed:RuntimeError"
    assert store.get_feishu_delivery_for_task(1) is None


def test_consumer_rejects_runner_without_hard_tool_isolation(store):
    class UnsafeRunner:
        def decide(self, prompt, session_id):
            raise AssertionError("must not be reached")

    with pytest.raises(ValueError, match="tool isolation"):
        FeishuReplyConsumer(store, UnsafeRunner())


@pytest.mark.parametrize("lookback_seconds", [0, 30 * 86400 + 1])
def test_consumer_rejects_invalid_context_lookback(store, lookback_seconds):
    with pytest.raises(ValueError, match="context_lookback_seconds"):
        FeishuReplyConsumer(
            store,
            FakeRunner(),
            context_lookback_seconds=lookback_seconds,
        )


@pytest.mark.parametrize("context_limit", [0, 101])
def test_consumer_rejects_invalid_context_limit(store, context_limit):
    with pytest.raises(ValueError, match="context_limit must be between 1 and 100"):
        FeishuReplyConsumer(
            store,
            FakeRunner(),
            context_limit=context_limit,
        )


def test_consumer_threads_context_lookback_to_store(store, monkeypatch):
    _seed(store)
    calls = []
    original = store.list_feishu_context

    def capture(*args, **kwargs):
        calls.append(kwargs.get("lookback_seconds"))
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "list_feishu_context", capture)
    runner = FakeRunner(CodexDecision(action=CodexAction.NO_REPLY))

    assert FeishuReplyConsumer(
        store,
        runner,
        context_lookback_seconds=321,
    ).run_once(1) == 1
    assert calls == [321]


def test_consumer_claims_and_finishes_one_task_at_a_time(store):
    _seed(store)
    _seed_second(store)
    tasks = store.list_reply_tasks(channel="feishu")
    first_id, second_id = sorted(task.id for task in tasks)
    consumer = FeishuReplyConsumer(store, FakeRunner())

    def finish(task):
        current = {row.id: row for row in store.list_reply_tasks(channel="feishu")}
        if task.id == first_id:
            assert current[second_id].status == "pending"
        assert store.complete_processing_reply_task(
            task.id, channel="feishu"
        )

    consumer.process = finish

    assert consumer.run_once(limit=2) == 2
    assert all(
        task.status == "done"
        for task in store.list_reply_tasks(channel="feishu")
    )


def test_same_reference_root_keeps_only_latest_pending_trigger(store):
    first = _trigger().model_copy(
        update={"thread_id": "", "root_message_id": "om_root"}
    )
    second = first.model_copy(
        update={
            "event_id": "evt_2",
            "message_id": "om_2",
            "event_create_time": "2026-07-22T03:20:02+00:00",
            "received_at": "2026-07-22T03:20:03+00:00",
        }
    )
    _record_trigger(store, first)
    latest = _record_trigger(store, second)

    tasks = {task.trigger_message_id: task for task in store.list_reply_tasks(channel="feishu")}
    assert tasks["om_1"].status == "done"
    assert tasks["om_1"].error == "superseded_by_newer_feishu_trigger"
    assert tasks["om_2"].status == "pending"
    runner = FakeRunner(CodexDecision(action=CodexAction.NO_REPLY))

    assert FeishuReplyConsumer(store, runner).run_once(limit=2) == 1
    assert len(runner.prompts) == 1
    assert store.reply_task_is_done(latest.reply_task_id)
    assert len(store.list_reply_attempts()) == 1


def test_different_reference_roots_are_never_superseded(store):
    first = _trigger().model_copy(
        update={"thread_id": "", "root_message_id": "om_root_a"}
    )
    second = first.model_copy(
        update={
            "event_id": "evt_2",
            "message_id": "om_2",
            "root_message_id": "om_root_b",
            "event_create_time": "2026-07-22T03:20:02+00:00",
            "received_at": "2026-07-22T03:20:03+00:00",
        }
    )
    _record_trigger(store, first)
    _record_trigger(store, second)
    runner = FakeRunner(CodexDecision(action=CodexAction.NO_REPLY))

    assert FeishuReplyConsumer(store, runner).run_once(limit=2) == 2
    assert len(runner.prompts) == 2
    assert len(store.list_reply_attempts()) == 2


def test_same_root_supersession_never_crosses_app_identity(store):
    first = _trigger().model_copy(
        update={"thread_id": "omt_shared", "root_message_id": "om_root"}
    )
    other = first.model_copy(
        update={
            "app_id": "cli_other",
            "event_id": "evt_other",
            "message_id": "om_other",
            "event_create_time": "2026-07-22T03:20:02+00:00",
            "received_at": "2026-07-22T03:20:03+00:00",
        }
    )
    _record_trigger(store, first)
    _record_trigger(store, other)
    runner = FakeRunner(CodexDecision(action=CodexAction.NO_REPLY))

    assert FeishuReplyConsumer(store, runner, app_id="cli_test").run_once(2) == 1
    tasks = {task.trigger_message_id: task for task in store.list_reply_tasks(channel="feishu")}
    assert tasks["om_1"].status == "done"
    assert tasks["om_other"].status == "pending"


def test_new_same_root_trigger_during_model_run_blocks_stale_finalization(store):
    first = _trigger().model_copy(
        update={"thread_id": "", "root_message_id": "om_root"}
    )
    _record_trigger(store, first)

    class RacingRunner(FakeRunner):
        def decide(self, prompt, session_id, image_paths=None):
            result = super().decide(prompt, session_id, image_paths=image_paths)
            _record_trigger(
                store,
                first.model_copy(
                    update={
                        "event_id": "evt_2",
                        "message_id": "om_2",
                        "event_create_time": "2026-07-22T03:20:02+00:00",
                        "received_at": "2026-07-22T03:20:03+00:00",
                    }
                ),
            )
            return result

    runner = RacingRunner(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="stale reply")
    )

    assert FeishuReplyConsumer(store, runner).run_once(limit=1) == 1
    tasks = {task.trigger_message_id: task for task in store.list_reply_tasks(channel="feishu")}
    assert tasks["om_1"].status == "done"
    assert tasks["om_1"].error == "superseded_by_newer_feishu_trigger"
    assert tasks["om_2"].status == "pending"
    assert len(runner.prompts) == 1
    assert store.list_reply_attempts() == []
    assert store.list_feishu_deliveries() == []


def test_standalone_consumer_reclaims_only_stale_feishu_task(store):
    _seed(store)
    [claimed] = store.claim_reply_tasks(1, channel="feishu")
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update reply_tasks set locked_at=datetime('now', '-31 minutes') where id=?",
            (claimed.id,),
        )
    runner = FakeRunner(CodexDecision(action=CodexAction.NO_REPLY))

    assert FeishuReplyConsumer(store, runner).run_once(limit=1) == 1

    [task] = store.list_reply_tasks(channel="feishu")
    assert task.status == "done"
    assert task.attempts == 2
    assert len(runner.prompts) == 1


def test_consumer_does_not_reclaim_during_json_repair_window(store):
    _seed(store)
    [claimed] = store.claim_reply_tasks(1, channel="feishu")
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update reply_tasks set locked_at=datetime('now', '-31 minutes') where id=?",
            (claimed.id,),
        )
    runner = FakeRunner(CodexDecision(action=CodexAction.NO_REPLY))
    runner.timeout_seconds = 1200

    assert FeishuReplyConsumer(store, runner).run_once(limit=1) == 0

    [task] = store.list_reply_tasks(channel="feishu")
    assert task.status == "processing"
    assert task.attempts == 1
    assert runner.prompts == []


def test_unexpected_task_failure_does_not_stop_later_task(store):
    _seed(store)
    _seed_second(store)
    first_id, second_id = sorted(
        task.id for task in store.list_reply_tasks(channel="feishu")
    )
    consumer = FeishuReplyConsumer(store, FakeRunner())

    def fail_first(task):
        if task.id == first_id:
            raise RuntimeError("raw secret must not be persisted")
        assert store.complete_processing_reply_task(
            task.id, channel="feishu"
        )

    consumer.process = fail_first

    assert consumer.run_once(limit=2) == 2
    tasks = {row.id: row for row in store.list_reply_tasks(channel="feishu")}
    assert tasks[first_id].status == "failed"
    assert tasks[first_id].error == "feishu_consumer_failed:RuntimeError"
    assert tasks[second_id].status == "done"


def test_atomic_finalize_rolls_back_attempt_if_delivery_insert_fails(store):
    _seed(store)
    with store._connect() as db:
        db.execute(
            """
            create trigger reject_test_delivery
            before insert on feishu_deliveries
            begin
                select raise(abort, 'injected delivery failure');
            end
            """
        )
    runner = FakeRunner(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="reply")
    )

    assert FeishuReplyConsumer(store, runner).run_once(limit=1) == 1

    [task] = store.list_reply_tasks(channel="feishu")
    assert task.status == "failed"
    assert task.error == "feishu_consumer_failed:IntegrityError"
    assert store.list_reply_attempts() == []
    assert store.list_feishu_deliveries() == []
    assert len(runner.prompts) == 1


def test_idempotency_key_is_stable_for_same_identity(store):
    from app.feishu.delivery import delivery_idempotency_key

    first = delivery_idempotency_key(
        app_id="cli_test", reply_task_id=1, trigger_message_id="om_1"
    )
    second = delivery_idempotency_key(
        app_id="cli_test", reply_task_id=1, trigger_message_id="om_1"
    )
    assert first == second


def test_verified_current_event_image_is_passed_to_runner(store):
    _event, asset = _seed_resolved_media(store)
    runner = _RecordingRunner(CodexDecision(action=CodexAction.NO_REPLY))
    consumer = FeishuReplyConsumer(
        store,
        runner,
        media_enabled=True,
        media_workspace=Path(store.path).parent,
    )

    assert consumer.run_once(1) == 1

    assert len(runner.image_calls) == 1
    assert len(runner.image_calls[0]) == 1
    image_path = runner.image_calls[0][0]
    assert not image_path.exists()
    assert image_path.name == f"00-{asset.sha256}.png"
    assert runner.image_bytes == [[PNG]]
    assert runner.image_parent_modes == [[0o700]]
    assert "图片附件已安全验证" in runner.prompts[0]
    assert "opaque_file_key" not in runner.prompts[0]
    assert str(image_path) not in runner.prompts[0]


def test_runner_reads_private_snapshot_if_retention_leaf_is_replaced(store):
    _event, asset = _seed_resolved_media(store)
    retained = Path(store.path).parent / asset.relative_path
    outside = Path(store.path).parent / "outside-sensitive.png"
    outside.write_bytes(b"not-the-verified-image")

    class ReplacingRunner(FakeRunner):
        def __init__(self):
            super().__init__(CodexDecision(action=CodexAction.NO_REPLY))
            self.snapshot_path = None
            self.snapshot_bytes = b""

        def decide(self, prompt, session_id, image_paths=None):
            [snapshot] = list(image_paths or [])
            self.snapshot_path = snapshot
            retained.unlink()
            retained.symlink_to(outside)
            self.snapshot_bytes = snapshot.read_bytes()
            return super().decide(
                prompt, session_id, image_paths=image_paths
            )

    runner = ReplacingRunner()
    FeishuReplyConsumer(
        store,
        runner,
        media_enabled=True,
        media_workspace=Path(store.path).parent,
    ).run_once(1)

    assert runner.snapshot_bytes == PNG
    assert runner.snapshot_path is not None
    assert not runner.snapshot_path.exists()
    assert retained.is_symlink()
    assert outside.read_bytes() == b"not-the-verified-image"


def test_tampered_ready_image_is_not_exposed_and_is_explicitly_unavailable(store):
    _event, asset = _seed_resolved_media(store)
    image_path = Path(store.path).parent / asset.relative_path
    image_path.write_bytes(PNG + b"tampered")
    runner = _RecordingRunner(CodexDecision(action=CodexAction.NO_REPLY))

    FeishuReplyConsumer(
        store,
        runner,
        media_enabled=True,
        media_workspace=Path(store.path).parent,
    ).run_once(1)

    assert runner.image_calls == [[]]
    assert "附件不可用；不可猜测" in runner.prompts[0]
    assert asset.relative_path not in runner.prompts[0]


def test_runner_without_image_contract_fails_closed(store):
    _seed_resolved_media(store)

    class LegacyRunner:
        tool_mode = "none"

        def decide(self, prompt, session_id):
            del prompt, session_id
            return CodexDecision(action=CodexAction.NO_REPLY)

    FeishuReplyConsumer(
        store,
        LegacyRunner(),
        media_enabled=True,
        media_workspace=Path(store.path).parent,
    ).run_once(1)

    [task] = store.list_reply_tasks(channel="feishu")
    assert task.status == "failed"
    assert task.error == "feishu_decision_failed:TypeError"


@pytest.mark.parametrize(
    ("resource_type", "data", "mime_type", "expected"),
    [
        ("file", b"%PDF-1.7\nbody", "application/pdf", "文件附件已接收"),
        ("audio", b"ID3audio", "audio/mpeg", "音频附件已接收"),
        (
            "video",
            b"\x00\x00\x00\x14ftypisom",
            "video/mp4",
            "视频附件已接收",
        ),
    ],
)
def test_non_image_media_is_summary_only(
    store, resource_type, data, mime_type, expected
):
    _event, asset = _seed_resolved_media(
        store,
        resource_type=resource_type,
        data=data,
        mime_type=mime_type,
    )
    runner = _RecordingRunner(CodexDecision(action=CodexAction.NO_REPLY))

    FeishuReplyConsumer(
        store,
        runner,
        media_enabled=True,
        media_workspace=Path(store.path).parent,
    ).run_once(1)

    assert runner.image_calls == [[]]
    assert expected in runner.prompts[0]
    assert "不可猜测" in runner.prompts[0]
    assert "opaque_file_key" not in runner.prompts[0]
    assert asset.relative_path not in runner.prompts[0]


def test_rejected_attachment_is_explicitly_unavailable_without_key_or_path(store):
    _event, asset = _seed_resolved_media(
        store,
        resource_type="file",
        data=b"\x00\x01invalid",
        mime_type="application/octet-stream",
    )
    assert asset.status == "rejected"
    runner = _RecordingRunner(CodexDecision(action=CodexAction.NO_REPLY))

    FeishuReplyConsumer(
        store,
        runner,
        media_enabled=True,
        media_workspace=Path(store.path).parent,
    ).run_once(1)

    assert "附件不可用；不可猜测" in runner.prompts[0]
    assert "opaque_file_key" not in runner.prompts[0]
    assert ".ceo-agent/feishu-media" not in runner.prompts[0]


def test_media_trigger_without_assets_is_explicitly_unavailable(store):
    trigger = _trigger().model_copy(
        update={"message_type": "image", "body_text": "[图片]"}
    )
    store.record_feishu_event(
        trigger, eligibility_status="eligible", store_body=True
    )
    runner = _RecordingRunner(CodexDecision(action=CodexAction.NO_REPLY))

    FeishuReplyConsumer(
        store,
        runner,
        media_enabled=True,
        media_workspace=Path(store.path).parent,
    ).run_once(1)

    assert runner.image_calls == [[]]
    assert "附件不可用；不可猜测" in runner.prompts[0]


def test_sticker_is_never_exposed_as_a_ready_image(store):
    _seed_resolved_media(store, resource_type="sticker")
    runner = _RecordingRunner(CodexDecision(action=CodexAction.NO_REPLY))

    FeishuReplyConsumer(
        store,
        runner,
        media_enabled=True,
        media_workspace=Path(store.path).parent,
    ).run_once(1)

    assert runner.image_calls == [[]]
    assert "附件不可用；不可猜测" in runner.prompts[0]
