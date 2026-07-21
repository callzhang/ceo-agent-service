from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import Barrier, Event, Lock
from zoneinfo import ZoneInfo

import pytest

from app.dingtalk_models import DingTalkConversation, DingTalkMessage
from app.memory_connector_auth import MemoryConnectorAuthorizationRequired
from app.memory_connector_client import MemoryWriteResult
from app.store import AutoReplyStore
from app.universal_consumer import UniversalConsumerOutcome, UniversalConsumerResult
from app.universal_context import build_universal_context
from app.universal_executor import build_universal_action_execution
from app.universal_plan import (
    PlannedAction,
    PlannedActionKind,
    UniversalAudit,
    UniversalPlan,
)
from app.universal_validator import DependencyStatus
from app.worker import (
    DingTalkAutoReplyWorker,
    UniversalDependencyAuthorizationError,
)


class RecordingPlanner:
    def __init__(self, plan: UniversalPlan) -> None:
        self.plan_result = plan
        self.calls = []
        self.last_session_id = "universal-session-1"

    def plan(self, context, session_id=None):
        self.calls.append((context, session_id))
        return self.plan_result.model_copy(deep=True)


class FakeDws:
    def __init__(self, trigger: DingTalkMessage, *, ready: bool = True) -> None:
        self.trigger = trigger
        self.ready = ready
        self.auth_status_calls = 0
        self.auth_login_starts = 0
        self.sent_replies: list[tuple[str, str, str]] = []

    def auth_status(self):
        self.auth_status_calls += 1
        return {
            "authenticated": self.ready,
            "token_valid": self.ready,
            "refresh_token_valid": self.ready,
        }

    def list_messages_by_ids(self, message_ids):
        assert message_ids == [self.trigger.open_message_id]
        return [self.trigger]

    def read_recent_messages(self, conversation):
        return [self.trigger]

    def read_unread_messages(self, conversation):
        return [self.trigger]

    def start_auth_login(self):
        self.auth_login_starts += 1
        raise AssertionError("worker must not start dws auth login")

    def send_reply_to_trigger(self, conversation, trigger, text, **kwargs):
        self.sent_replies.append(
            (conversation.open_conversation_id, trigger.open_message_id, text)
        )
        return {"success": True, "messageId": "sent-1"}


class FakeLegacyCodex:
    timeout_seconds = 901
    idle_timeout_seconds = 900

    class Runner:
        workspace = Path("/tmp/universal-worker-workspace")
        codex_bin = "codex-native"

    runner = Runner()


class FailingMemoryClient:
    def __init__(self) -> None:
        self.ready_calls = 0

    def ensure_ready_sync(self):
        self.ready_calls += 1
        raise MemoryConnectorAuthorizationRequired("login required")


class RecordingMemoryClient:
    def __init__(self) -> None:
        self.ready_calls = 0
        self.write_calls = []

    def ensure_ready_sync(self):
        self.ready_calls += 1

    def memory_write_sync(self, **kwargs):
        self.write_calls.append(kwargs)
        return MemoryWriteResult(
            episode_uuid="episode-1",
            processing_status="completed",
            duplicate=False,
        )


def fixed_now() -> datetime:
    return datetime(2026, 7, 21, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def trigger_message(*, raw_payload=None, content="请处理") -> DingTalkMessage:
    return DingTalkMessage(
        open_conversation_id="cid-1",
        open_message_id="msg-1",
        conversation_title="测试群",
        single_chat=False,
        sender_name="宇航",
        sender_open_dingtalk_id="sender-open-1",
        sender_user_id="sender-user-1",
        create_time="2026-07-21 09:55:00",
        content=content,
        raw_payload=raw_payload or {},
    )


def conversation() -> DingTalkConversation:
    return DingTalkConversation(
        open_conversation_id="cid-1",
        title="测试群",
        single_chat=False,
        unread_point=1,
    )


def stored_task(worker, task_id):
    return next(task for task in worker.store.list_reply_tasks() if task.id == task_id)


def no_reply_plan(*, dependencies=("dws",)) -> UniversalPlan:
    return UniversalPlan(
        task_kind="reply",
        reason="无需回复",
        dependencies=list(dependencies),
        actions=[
            PlannedAction(
                kind=PlannedActionKind.NO_REPLY,
                reason="仅记录",
                target={"conversation_id": "cid-1", "trigger_message_id": "msg-1"},
                payload={},
            )
        ],
        audit=UniversalAudit(summary="上下文足够", confidence=0.9),
    )


def reply_then_memory_plan(*, dependencies=("dws", "memory")) -> UniversalPlan:
    return UniversalPlan(
        task_kind="reply_and_memory",
        reason="回复后记录稳定决策",
        dependencies=list(dependencies),
        actions=[
            PlannedAction(
                kind=PlannedActionKind.SEND_REPLY,
                reason="回复",
                sensitivity_kind="general",
                target={"conversation_id": "cid-1", "trigger_message_id": "msg-1"},
                payload={"text": "已处理"},
            ),
            PlannedAction(
                kind=PlannedActionKind.MEMORY_WRITE,
                reason="记录稳定决策",
                payload={"data": "Derek 决定采用通用 consumer 架构。", "type": "text"},
            ),
        ],
        audit=UniversalAudit(summary="需要两步完成", confidence=0.9),
    )


def reply_plan_without_target() -> UniversalPlan:
    return UniversalPlan(
        task_kind="reply",
        reason="回复当前触发消息",
        dependencies=["dws"],
        actions=[
            PlannedAction(
                kind=PlannedActionKind.SEND_REPLY,
                reason="回复",
                sensitivity_kind="general",
                payload={"text": "已按当前消息处理"},
            )
        ],
        audit=UniversalAudit(summary="上下文足够", confidence=0.9),
    )


def make_worker(
    tmp_path,
    monkeypatch,
    *,
    trigger=None,
    planner=None,
    dws_ready=True,
    memory_client=None,
    dry_run=False,
):
    monkeypatch.setattr("app.worker.send_macos_notification", lambda **_: None)
    trigger = trigger or trigger_message()
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.set_current_user_id("principal-user-1")
    worker = DingTalkAutoReplyWorker(
        store=store,
        dws=FakeDws(trigger, ready=dws_ready),
        codex=FakeLegacyCodex(),
        dry_run=dry_run,
        now_provider=fixed_now,
        memory_client=memory_client,
        universal_planner=planner or RecordingPlanner(no_reply_plan()),
    )
    return worker, trigger


def enqueue(worker, trigger, *, force_new_decision=False, generation="initial", oa_url=""):
    worker.store.upsert_conversation("cid-1", "测试群", False, None)
    assert worker.store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="测试群",
        single_chat=False,
        trigger_message_id=trigger.open_message_id,
        trigger_create_time=trigger.create_time,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        trigger_message_json=trigger.model_dump_json(),
        force_new_decision=force_new_decision,
        execution_generation=generation,
        oa_url=oa_url,
    )
    return worker.store.list_reply_tasks(limit=1)[0]


def test_explicit_flag_off_preserves_legacy_route(tmp_path, monkeypatch):
    monkeypatch.setenv("CEO_UNIVERSAL_CONSUMER", "0")
    planner = RecordingPlanner(no_reply_plan())
    worker, trigger = make_worker(tmp_path, monkeypatch, planner=planner)
    task = enqueue(worker, trigger)
    legacy_calls = []
    monkeypatch.setattr(worker, "_process_batch", lambda *args, **kwargs: legacy_calls.append((args, kwargs)))

    assert worker._process_queued_task(
        conversation(), task
    ) is True

    assert len(legacy_calls) == 1
    assert planner.calls == []


def test_universal_route_is_default(tmp_path, monkeypatch):
    monkeypatch.delenv("CEO_UNIVERSAL_CONSUMER", raising=False)
    planner = RecordingPlanner(no_reply_plan())
    worker, trigger = make_worker(tmp_path, monkeypatch, planner=planner)
    task = enqueue(worker, trigger)
    legacy_calls = []
    monkeypatch.setattr(
        worker,
        "_process_batch",
        lambda *args, **kwargs: legacy_calls.append((args, kwargs)),
    )

    assert worker._process_queued_task(conversation(), task) is True

    assert len(planner.calls) == 1
    assert legacy_calls == []


def test_dws_auth_blocks_before_planner_without_starting_login(tmp_path, monkeypatch):
    monkeypatch.setenv("CEO_UNIVERSAL_CONSUMER", "1")
    planner = RecordingPlanner(no_reply_plan())
    worker, trigger = make_worker(
        tmp_path, monkeypatch, planner=planner, dws_ready=False
    )
    task = enqueue(worker, trigger)

    assert worker.consume_once(max_tasks=1) == 0

    stored = stored_task(worker, task.id)
    assert stored.status == "pending"
    assert stored.error == "dws_authorization_required"
    assert planner.calls == []
    assert worker.dws.auth_login_starts == 0


def test_memory_unavailable_does_not_block_memory_unrelated_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("CEO_UNIVERSAL_CONSUMER", "1")
    planner = RecordingPlanner(no_reply_plan())
    memory = FailingMemoryClient()
    worker, trigger = make_worker(
        tmp_path, monkeypatch, planner=planner, memory_client=memory
    )
    enqueue(worker, trigger)

    assert worker.consume_once(max_tasks=1) == 1
    assert len(planner.calls) == 1
    assert memory.ready_calls == 0


def test_memory_dependency_is_deferred_without_authorization_side_effects(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CEO_UNIVERSAL_CONSUMER", "1")
    planner = RecordingPlanner(reply_then_memory_plan(dependencies=("dws",)))
    memory = FailingMemoryClient()
    worker, trigger = make_worker(
        tmp_path, monkeypatch, planner=planner, memory_client=memory
    )
    task = enqueue(worker, trigger)

    assert worker.consume_once(max_tasks=1) == 0

    stored = stored_task(worker, task.id)
    assert stored.status == "pending"
    assert stored.error == "memory_authorization_required"
    assert len(planner.calls) == 1
    assert memory.ready_calls == 1
    assert worker.dws.auth_login_starts == 0

    with pytest.raises(
        UniversalDependencyAuthorizationError,
        match="memory_authorization_required",
    ):
        worker._process_queued_task(conversation(), stored)

    assert len(planner.calls) == 1
    assert memory.ready_calls == 2


def test_universal_reply_plan_missing_target_uses_current_trigger(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CEO_UNIVERSAL_CONSUMER", "1")
    planner = RecordingPlanner(reply_plan_without_target())
    worker, trigger = make_worker(tmp_path, monkeypatch, planner=planner)
    task = enqueue(worker, trigger)

    assert worker.consume_once(max_tasks=1) == 1

    stored = stored_task(worker, task.id)
    assert stored.status == "done"
    assert len(worker.dws.sent_replies) == 1
    sent_conversation_id, sent_message_id, sent_text = worker.dws.sent_replies[0]
    assert (sent_conversation_id, sent_message_id) == ("cid-1", "msg-1")
    assert "已按当前消息处理" in sent_text
    plan_execution = worker.store.load_universal_plan_execution(
        build_universal_context(
            conversation=conversation(),
            trigger=trigger,
            context_messages=[trigger],
            task_id=task.id,
            force_new_decision=False,
            dry_run=False,
            execution_generation=task.execution_generation,
        )
    )
    assert plan_execution is not None
    assert plan_execution.plan.actions[0].target == {
        "conversation_id": "cid-1",
        "trigger_message_id": "msg-1",
    }


def test_worker_builds_trusted_context_and_force_generation(tmp_path, monkeypatch):
    monkeypatch.setenv("CEO_UNIVERSAL_CONSUMER", "1")
    raw_payload = {
        "mail": {"mailbox": "hr@example.com", "messageId": 42, "subject": "候选人"},
        "calendar": {"eventId": 84, "selfResponseStatus": "tentative"},
        "oa": {"processInstanceId": 126, "taskId": 168},
    }
    document_url = "https://alidocs.dingtalk.com/i/nodes/node-1"
    trigger = trigger_message(raw_payload=raw_payload, content=f"请处理 {document_url}")
    planner = RecordingPlanner(no_reply_plan())
    worker, _ = make_worker(tmp_path, monkeypatch, trigger=trigger, planner=planner)
    enqueue(
        worker,
        trigger,
        force_new_decision=True,
        generation="generation-2",
        oa_url="https://oa.dingtalk.com/process?processInstanceId=126&taskId=168",
    )

    assert worker.consume_once(max_tasks=1) == 1

    context = planner.calls[0][0]
    assert context.execution_generation == "generation-2"
    assert context.force_new_decision is True
    assert context.trusted_oa_process_instance_id == "126"
    assert context.trusted_oa_task_id == "168"
    assert context.trusted_mail_message_id == "42"
    assert context.trusted_calendar_event_id == "84"
    assert context.trusted_document_url == document_url
    assert context.context_messages[-1].raw_payload_json
    assert planner.calls[0][1] is None


def test_worker_reuses_existing_native_codex_session(tmp_path, monkeypatch):
    monkeypatch.setenv("CEO_UNIVERSAL_CONSUMER", "1")
    planner = RecordingPlanner(no_reply_plan())
    planner.last_session_id = None
    worker, trigger = make_worker(tmp_path, monkeypatch, planner=planner)
    enqueue(worker, trigger)
    worker.store.upsert_conversation("cid-1", "测试群", False, "existing-session")

    assert worker.consume_once(max_tasks=1) == 1

    assert planner.calls[0][1] == "existing-session"


def test_universal_stale_codex_resume_clears_session_and_retries_fresh(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("CEO_UNIVERSAL_CONSUMER", "1")

    class StaleThenFreshPlanner(RecordingPlanner):
        def __init__(self):
            super().__init__(no_reply_plan())
            self.last_session_id = None

        def plan(self, context, session_id=None):
            self.calls.append((context, session_id))
            if session_id == "existing-session":
                self.last_session_id = session_id
                raise RuntimeError(
                    "thread/resume failed: no rollout found for thread id "
                    "019f3bc6"
                )
            self.last_session_id = "fresh-session"
            return self.plan_result.model_copy(deep=True)

    planner = StaleThenFreshPlanner()
    worker, trigger = make_worker(tmp_path, monkeypatch, planner=planner)
    enqueue(worker, trigger)
    worker.store.upsert_conversation("cid-1", "测试群", False, "existing-session")

    assert worker.consume_once(max_tasks=1) == 1

    assert [session_id for _, session_id in planner.calls] == [
        "existing-session",
        "existing-session",
        None,
    ]
    assert worker.store.get_codex_session_id("cid-1") == "fresh-session"


def test_planner_exception_persists_new_native_codex_session(tmp_path, monkeypatch):
    monkeypatch.setenv("CEO_UNIVERSAL_CONSUMER", "1")

    class FailingPlanner(RecordingPlanner):
        def plan(self, context, session_id=None):
            self.calls.append((context, session_id))
            self.last_session_id = "session-created-before-error"
            raise ValueError("planner parse failed")

    planner = FailingPlanner(no_reply_plan())
    planner.last_session_id = None
    worker, trigger = make_worker(tmp_path, monkeypatch, planner=planner)
    task = enqueue(worker, trigger)

    with pytest.raises(ValueError, match="planner parse failed"):
        worker._process_queued_task(conversation(), task)

    assert worker.store.get_codex_session_id("cid-1") == (
        "session-created-before-error"
    )


def test_default_universal_planner_uses_native_codex_runner_and_15_minute_floor(
    tmp_path, monkeypatch
):
    captured = {}

    class CapturingUniversalPlanner:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("app.worker.UniversalPlanner", CapturingUniversalPlanner)
    trigger = trigger_message()
    worker = DingTalkAutoReplyWorker(
        store=AutoReplyStore(tmp_path / "worker.sqlite3"),
        dws=FakeDws(trigger),
        codex=FakeLegacyCodex(),
        now_provider=fixed_now,
    )

    planner = worker._universal_planner()

    assert isinstance(planner, CapturingUniversalPlanner)
    assert captured == {
        "workspace": Path("/tmp/universal-worker-workspace"),
        "codex_bin": "codex-native",
        "timeout_seconds": 901,
        "idle_timeout_seconds": 900,
    }


def test_active_plan_resumes_memory_after_reply_was_sent(tmp_path, monkeypatch):
    monkeypatch.setenv("CEO_UNIVERSAL_CONSUMER", "1")
    planner = RecordingPlanner(reply_then_memory_plan())
    memory = RecordingMemoryClient()
    worker, trigger = make_worker(
        tmp_path, monkeypatch, planner=planner, memory_client=memory
    )
    task = enqueue(worker, trigger)
    context = build_universal_context(
        conversation=conversation(),
        trigger=trigger,
        context_messages=[trigger],
        task_id=task.id,
        force_new_decision=False,
        dry_run=False,
        execution_generation=task.execution_generation,
    )
    plan_execution = worker.store.create_universal_plan_execution(
        context, reply_then_memory_plan()
    )
    reply_execution = build_universal_action_execution(
        context, plan_execution, plan_execution.plan.actions[0], 0
    )
    assert worker.store.claim_universal_action_execution(reply_execution).value == "not_started"
    worker.store.complete_universal_action_execution(reply_execution, result_json="{}")
    worker.store.record_sent_reply("cid-1", "msg-1", "已处理")

    assert worker.consume_once(max_tasks=1) == 1

    assert planner.calls == []
    assert len(memory.write_calls) == 1
    assert stored_task(worker, task.id).status == "done"


def test_dry_run_is_deferred_without_authorization_label(tmp_path, monkeypatch):
    monkeypatch.setenv("CEO_UNIVERSAL_CONSUMER", "1")
    worker, trigger = make_worker(tmp_path, monkeypatch, dry_run=True)
    task = enqueue(worker, trigger)

    assert worker.consume_once(max_tasks=1) == 0

    stored = stored_task(worker, task.id)
    assert stored.status == "pending"
    assert stored.error == "dry_run"
    assert all(error.kind != "reply_task_authorization" for error in worker.store.list_errors())


def test_unknown_action_fails_persistently_without_replanning(tmp_path, monkeypatch):
    monkeypatch.setenv("CEO_UNIVERSAL_CONSUMER", "1")
    planner = RecordingPlanner(no_reply_plan())
    worker, trigger = make_worker(tmp_path, monkeypatch, planner=planner)
    task = enqueue(worker, trigger)
    context = build_universal_context(
        conversation=conversation(),
        trigger=trigger,
        context_messages=[trigger],
        task_id=task.id,
        force_new_decision=False,
        dry_run=False,
        execution_generation=task.execution_generation,
    )
    plan_execution = worker.store.create_universal_plan_execution(context, no_reply_plan())
    execution = build_universal_action_execution(
        context, plan_execution, plan_execution.plan.actions[0], 0
    )
    worker.store.claim_universal_action_execution(execution)
    worker.store.mark_universal_action_execution_unknown(execution, "receipt ambiguous")

    assert worker.consume_once(max_tasks=1) == 0
    assert stored_task(worker, task.id).status == "failed"
    assert planner.calls == []
    assert worker.consume_once(max_tasks=1) == 0


@pytest.mark.parametrize(
    ("outcome", "completed", "expected_status"),
    [
        (UniversalConsumerOutcome.COMPLETED, True, "done"),
        (UniversalConsumerOutcome.DUPLICATE, True, "done"),
        (UniversalConsumerOutcome.ACTION_FAILED, False, "pending"),
        (UniversalConsumerOutcome.VALIDATION_BLOCKED, False, "pending"),
        (UniversalConsumerOutcome.NONTERMINAL_BLOCKED, False, "pending"),
    ],
)
def test_universal_outcome_mapping(
    tmp_path, monkeypatch, outcome, completed, expected_status
):
    monkeypatch.setenv("CEO_UNIVERSAL_CONSUMER", "1")
    worker, trigger = make_worker(tmp_path, monkeypatch)
    task = enqueue(worker, trigger)

    class FakeOrchestrator:
        def process(self, context):
            return UniversalConsumerResult(
                completed=completed,
                reason=f"reason:{outcome.value}",
                executed_actions=(),
                outcome=outcome,
            )

    monkeypatch.setattr(worker, "_universal_consumer", lambda: FakeOrchestrator())
    worker.consume_once(max_tasks=1)

    assert stored_task(worker, task.id).status == expected_status


def test_concurrent_workers_execute_one_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("CEO_UNIVERSAL_CONSUMER", "1")
    trigger = trigger_message()
    planner = RecordingPlanner(no_reply_plan())
    worker1, _ = make_worker(tmp_path, monkeypatch, trigger=trigger, planner=planner)
    enqueue(worker1, trigger)
    worker2 = DingTalkAutoReplyWorker(
        store=AutoReplyStore(tmp_path / "worker.sqlite3"),
        dws=FakeDws(trigger),
        codex=FakeLegacyCodex(),
        now_provider=fixed_now,
        universal_planner=planner,
    )
    barrier = Barrier(2)
    original_claim = worker1.store.claim_reply_tasks
    lock = Lock()

    def synchronized_claim(*args, **kwargs):
        barrier.wait()
        with lock:
            return original_claim(*args, **kwargs)

    worker1.store.claim_reply_tasks = synchronized_claim
    worker2.store.claim_reply_tasks = synchronized_claim

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda worker: worker.consume_once(max_tasks=1), (worker1, worker2)))

    assert sorted(results) == [0, 1]
    assert len(planner.calls) == 1


def test_same_conversation_tasks_cannot_plan_concurrently(tmp_path, monkeypatch):
    monkeypatch.setenv("CEO_UNIVERSAL_CONSUMER", "1")
    first_started = Event()
    release_first = Event()

    class BlockingPlanner(RecordingPlanner):
        def plan(self, context, session_id=None):
            self.calls.append((context, session_id))
            first_started.set()
            assert release_first.wait(timeout=5)
            return self.plan_result.model_copy(deep=True)

    first_trigger = trigger_message()
    second_trigger = trigger_message(content="请处理第二个任务").model_copy(
        update={"open_message_id": "msg-2"}
    )
    planner = BlockingPlanner(no_reply_plan())
    worker1, _ = make_worker(
        tmp_path,
        monkeypatch,
        trigger=first_trigger,
        planner=planner,
    )
    first_task = enqueue(worker1, first_trigger)
    second_task = enqueue(worker1, second_trigger)
    worker2 = DingTalkAutoReplyWorker(
        store=AutoReplyStore(tmp_path / "worker.sqlite3"),
        dws=FakeDws(second_trigger),
        codex=FakeLegacyCodex(),
        now_provider=fixed_now,
        universal_planner=planner,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(
            worker1._process_universal_queued_task,
            conversation(),
            first_task,
            first_trigger,
            [first_trigger],
        )
        assert first_started.wait(timeout=5)
        second_future = pool.submit(
            worker2._process_universal_queued_task,
            conversation(),
            second_task,
            second_trigger,
            [second_trigger],
        )
        with pytest.raises(RuntimeError, match="codex session locked"):
            second_future.result(timeout=5)
        release_first.set()
        assert first_future.result(timeout=5) is True

    assert len(planner.calls) == 1


def test_flag_off_keeps_legacy_leak_check_order(tmp_path, monkeypatch):
    monkeypatch.setenv("CEO_UNIVERSAL_CONSUMER", "0")
    worker, trigger = make_worker(tmp_path, monkeypatch)
    updates = []
    delivered = []
    regenerated = []
    original_update = worker.store.update_reply_attempt

    def recording_update(attempt_id, **kwargs):
        updates.append(kwargs.copy())
        return original_update(attempt_id, **kwargs)

    monkeypatch.setattr(worker.store, "update_reply_attempt", recording_update)
    monkeypatch.setattr("app.worker.feedback_spike_vercel_base_url", lambda: "")
    monkeypatch.setattr(
        "app.worker.contains_forbidden_leak",
        lambda text: "unsafe-leak" in text,
    )
    monkeypatch.setattr(
        worker,
        "_regenerate_reply_after_leak_check",
        lambda *, blocked_reply_text: (
            regenerated.append(blocked_reply_text) or "clean reply"
        ),
    )
    monkeypatch.setattr(worker, "_notify", lambda **kwargs: None)
    monkeypatch.setattr(
        worker,
        "_deliver_trigger_reply",
        lambda **kwargs: delivered.append(kwargs) or True,
    )

    worker._deliver_final_reply(
        conversation=conversation(),
        trigger=trigger,
        new_messages=[trigger],
        attempt_id=999,
        final_reply_text="unsafe-leak",
        at_users=[],
        at_open_dingtalk_ids=[],
        at_open_dingtalk_names=[],
        direct_user_id=None,
        direct_open_dingtalk_id=None,
    )

    assert updates[0]["final_reply_text"] == "unsafe-leak"
    assert regenerated == ["unsafe-leak"]
    assert len(delivered) == 1
    assert "clean reply" in delivered[0]["reply_text"]
