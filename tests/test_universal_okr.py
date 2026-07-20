from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.store import AutoReplyStore
from app.universal_context import UniversalContextMessage, UniversalTaskContext
from app.universal_executor import (
    UniversalActionExecution,
    UniversalActionExecutionState,
    build_universal_action_execution,
)
from app.universal_plan import (
    PlannedAction,
    PlannedActionKind,
    UniversalAudit,
    UniversalPlan,
)
from app.worker import DingTalkAutoReplyWorker, ReplyDeliveryError


class FakeCodex:
    pass


class RecordingDws:
    def __init__(self) -> None:
        self.sent_replies: list[tuple[str, str, str, dict]] = []

    def send_reply_to_trigger(
        self,
        conversation,
        trigger,
        text: str,
        **kwargs,
    ) -> dict:
        self.sent_replies.append(
            (
                conversation.open_conversation_id,
                trigger.open_message_id,
                text,
                kwargs,
            )
        )
        return {"success": True, "messageId": "okr-ack-1"}


class RecordingOkrSource:
    def __init__(self, *, result: dict | None = None, error: Exception | None = None):
        self.result = result if result is not None else {"objectives": []}
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def fetch_user_okr(self, *, user_id: str, period_label: str) -> dict:
        self.calls.append((user_id, period_label))
        if self.error is not None:
            raise self.error
        return self.result


def fixed_now() -> datetime:
    return datetime(2026, 7, 21, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def make_execution(
    store: AutoReplyStore,
    *,
    target_conversation_id: str = "cid-okr",
    target_trigger_message_id: str = "msg-okr",
) -> UniversalActionExecution:
    assert store.enqueue_reply_task(
        conversation_id="cid-okr",
        conversation_title="OKR review",
        single_chat=True,
        trigger_message_id="msg-okr",
        trigger_create_time="2026-07-21 09:55:00",
        trigger_sender="宇航",
        trigger_text="请审核我的 Q2 OKR",
    )
    task = store.claim_reply_tasks(limit=1)[0]
    context = UniversalTaskContext(
        task_id=task.id,
        conversation_id="cid-okr",
        conversation_title="OKR review",
        single_chat=True,
        trigger_message_id="msg-okr",
        trigger_create_time="2026-07-21 09:55:00",
        trigger_sender="宇航",
        trigger_text="请审核我的 Q2 OKR",
        context_messages=(
            UniversalContextMessage(
                sender_name="宇航",
                open_message_id="msg-okr",
                content="请审核我的 Q2 OKR",
                sender_open_dingtalk_id="open-yuhang",
                sender_user_id="user-yuhang",
                message_type="text",
                create_time="2026-07-21 09:55:00",
            ),
        ),
        required_dependencies=("dws",),
        force_new_decision=False,
        dry_run=False,
    )
    action = PlannedAction(
        kind=PlannedActionKind.QUEUE_OKR_REVIEW,
        reason="读取本人 OKR 后进入审核流程",
        target={
            "conversation_id": target_conversation_id,
            "trigger_message_id": target_trigger_message_id,
        },
        payload={},
    )
    plan = UniversalPlan(
        task_kind="okr_review",
        reason="发信人请求审核本人的 OKR",
        dependencies=["dws"],
        actions=[action],
        audit=UniversalAudit(summary="OKR review request", confidence=0.98),
    )
    plan_execution = store.create_universal_plan_execution(context, plan)
    return build_universal_action_execution(
        context,
        plan_execution,
        plan_execution.plan.actions[0],
        0,
    )


def make_worker(
    store: AutoReplyStore,
    dws: RecordingDws,
    source: RecordingOkrSource,
) -> DingTalkAutoReplyWorker:
    worker = DingTalkAutoReplyWorker(
        store=store,
        dws=dws,
        codex=FakeCodex(),
        send_attempts=1,
        now_provider=fixed_now,
    )
    worker.okr_live_source = source
    return worker


def test_execute_universal_okr_review_fetches_queues_acks_and_completes(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "okr.sqlite3")
    store.set_current_user_id("principal-user")
    execution = make_execution(store)
    dws = RecordingDws()
    payload = {"objectives": [{"title": "Ship Q2", "progress": 0.8}]}
    source = RecordingOkrSource(result=payload)
    worker = make_worker(store, dws, source)

    assert worker.execute_universal_okr_review(execution) is True

    assert source.calls == [("user-yuhang", "2026 Q2")]
    requests = store.claim_okr_review_requests(1)
    assert len(requests) == 1
    assert requests[0].trigger_message_id == "msg-okr"
    assert json.loads(requests[0].okr_source_json) == payload
    assert len(dws.sent_replies) == 1
    assert "已受理 2026 Q2 OKR 审核请求" in dws.sent_replies[0][2]
    attempt = store.get_latest_reply_attempt_for_trigger("cid-okr", "msg-okr")
    assert attempt is not None
    assert attempt.action == "queue_okr_review"
    assert attempt.send_status == "sent"
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.SUCCEEDED
    )
    assert store.has_seen("msg-okr") is True


def test_execute_universal_okr_review_blocks_untrusted_target(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "okr.sqlite3")
    execution = make_execution(store, target_conversation_id="cid-spoofed")
    dws = RecordingDws()
    source = RecordingOkrSource()
    worker = make_worker(store, dws, source)

    assert worker.execute_universal_okr_review(execution) is True

    assert source.calls == []
    assert dws.sent_replies == []
    assert store.claim_okr_review_requests(1) == []
    attempt = store.get_latest_reply_attempt_for_trigger("cid-okr", "msg-okr")
    assert attempt is not None
    assert attempt.send_status == "blocked"
    assert attempt.send_error == "untrusted_okr_review_target"


def test_execute_universal_okr_review_transient_source_failure_is_retryable(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "okr.sqlite3")
    execution = make_execution(store)
    dws = RecordingDws()
    source = RecordingOkrSource(error=RuntimeError("temporary OKR source outage"))
    worker = make_worker(store, dws, source)

    with pytest.raises(ReplyDeliveryError, match="temporary OKR source outage"):
        worker.execute_universal_okr_review(execution)

    assert source.calls == [("user-yuhang", "2026 Q2")]
    assert dws.sent_replies == []
    assert store.claim_okr_review_requests(1) == []
    attempt = store.get_latest_reply_attempt_for_trigger("cid-okr", "msg-okr")
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )
    assert store.has_seen("msg-okr") is False

    source.error = None
    assert worker.execute_universal_okr_review(execution) is True

    assert source.calls == [
        ("user-yuhang", "2026 Q2"),
        ("user-yuhang", "2026 Q2"),
    ]
    assert len(store.claim_okr_review_requests(1)) == 1
    retry_attempt = store.get_latest_reply_attempt_for_trigger("cid-okr", "msg-okr")
    assert retry_attempt is not None
    assert retry_attempt.action == "queue_okr_review"
    assert retry_attempt.send_status == "sent"
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.SUCCEEDED
    )
    assert store.has_seen("msg-okr") is True


def test_execute_universal_okr_review_external_login_block_is_terminal(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "okr.sqlite3")
    execution = make_execution(store)
    dws = RecordingDws()
    source = RecordingOkrSource(
        error=RuntimeError(
            "Dingteam OKR live source failed: Dingteam OKR API error 103: 未登录"
        )
    )
    worker = make_worker(store, dws, source)

    assert worker.execute_universal_okr_review(execution) is True

    attempt = store.get_latest_reply_attempt_for_trigger("cid-okr", "msg-okr")
    assert attempt is not None
    assert attempt.send_status == "blocked"
    assert attempt.send_error.startswith("blocked_unrecoverable_external_auth:")
    assert dws.sent_replies == []
    assert store.claim_okr_review_requests(1) == []
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.SUCCEEDED
    )
    assert store.has_seen("msg-okr") is True


def test_execute_universal_okr_review_repeat_does_not_repeat_external_actions(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "okr.sqlite3")
    store.set_current_user_id("principal-user")
    execution = make_execution(store)
    dws = RecordingDws()
    source = RecordingOkrSource()
    worker = make_worker(store, dws, source)

    assert worker.execute_universal_okr_review(execution) is True
    assert worker.execute_universal_okr_review(execution) is True

    assert source.calls == [("user-yuhang", "2026 Q2")]
    assert len(dws.sent_replies) == 1
    assert len(store.claim_okr_review_requests(10)) == 1
