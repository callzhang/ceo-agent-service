from datetime import datetime, timedelta, timezone

import pytest

from app.codex_failure import CODEX_PROVIDER_AUTH_FAILED
from app.dingtalk_models import CodexAction, CodexDecision
from app.store import AgentRunLeaseLostError, AutoReplyStore
from app.wechat.consumer import WechatReplyConsumer, WechatTaskProcessingError
from app.wechat.models import WechatAccount


class FakeCodexRunner:
    def __init__(self):
        self.decision = None
        self.prompts: list[str] = []

    def decide(self, prompt, session_id, image_paths=None, *, run_id=None):
        self.prompts.append(prompt)
        assert run_id is not None
        return self.decision


@pytest.fixture
def store(tmp_path):
    return AutoReplyStore(tmp_path / "w.sqlite3")


@pytest.fixture
def account():
    return WechatAccount(account_id="acct-1", display_name="derek", self_user_id="self-1",
                         account_dir="/a", db_dir="/a/db_storage", app_version="4.1.10")


@pytest.fixture
def fake_codex():
    return FakeCodexRunner()


@pytest.fixture
def consumer(store, fake_codex, account):
    store.enqueue_reply_task(
        channel="wechat", conversation_id="u9", conversation_title="Alex",
        single_chat=True, trigger_message_id="m1",
        trigger_create_time="2026-07-17T10:00:00", trigger_sender="Alex",
        trigger_text="下午能给结论吗",
    )
    return WechatReplyConsumer(store, fake_codex, reader=None, account=account)


def test_send_reply_creates_ready_delivery(fake_codex, consumer, store):
    fake_codex.decision = CodexDecision(
        action=CodexAction.SEND_REPLY, reply_text="收到，我下午给你结论。",
        reason="明确承诺", audit_summary="明确承诺",
    )
    assert consumer.run_once(limit=1) == 1
    delivery = store.get_wechat_delivery_for_task(1)
    assert delivery is not None
    assert delivery.status == "ready_to_send"
    assert delivery.reply_text == "收到，我下午给你结论。"
    assert delivery.evidence["trigger_text"] == "下午能给结论吗"
    attempt = store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "pending"
    assert "memory_recall" in fake_codex.prompts[0]


def test_no_reply_completes_without_delivery(fake_codex, consumer, store):
    fake_codex.decision = CodexDecision(action=CodexAction.NO_REPLY, audit_summary="无需回复")
    assert consumer.run_once(limit=1) == 1
    assert store.get_wechat_delivery_for_task(1) is None
    attempt = store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "skipped"
    assert attempt.send_error == "no_reply"


def test_dingtalk_system_actions_rejected(fake_codex, consumer, store):
    fake_codex.decision = CodexDecision(
        action=CodexAction.SEND_REPLY, reply_text="x",
        system_actions=[{"tool": "dws"}], audit_summary="s",
    )
    assert consumer.run_once(limit=1) == 1
    assert store.get_wechat_delivery_for_task(1) is None
    attempt = store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert attempt.send_error == "dingtalk_only_system_actions_rejected"


def test_reply_transport_action_creates_ready_wechat_delivery(
    fake_codex, consumer, store
):
    fake_codex.decision = CodexDecision(
        action=CodexAction.SEND_REPLY,
        reply_text="我也去餐厅吃饭。",
        system_actions=[
            {
                "type": "send_dingtalk_reply",
                "reply_text_ref": "user_response.text",
            }
        ],
        audit_summary="基于同一对话上下文澄清晚饭安排。",
    )

    assert consumer.run_once(limit=1) == 1

    delivery = store.get_wechat_delivery_for_task(1)
    assert delivery is not None
    assert delivery.status == "ready_to_send"
    attempt = store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "pending"


def test_stop_with_error_records_failed_attempt(fake_codex, consumer, store):
    fake_codex.decision = CodexDecision(
        action=CodexAction.STOP_WITH_ERROR,
        reason="missing_wechat_context",
        audit_summary="缺上下文",
    )

    assert consumer.run_once(limit=1) == 1

    attempt = store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert attempt.send_error == "missing_wechat_context"


def test_runtime_failure_fails_agent_run_without_completing_business_stop(
    consumer, store
):
    from app.agent_runtime_contracts import RuntimeFailureClass
    from app.agent_runtime_router import RoutedCodexExecutionError

    secret = "provider-secret-detail"

    class RuntimeFailingRunner:
        def decide(self, *_args, **_kwargs):
            raise RoutedCodexExecutionError(
                "runtime_route_unavailable",
                secret,
                failure_class=RuntimeFailureClass.AUTHENTICATION,
                failure_code="codex_provider_auth_failed",
            )

    consumer.runner = RuntimeFailingRunner()

    with pytest.raises(WechatTaskProcessingError):
        consumer.run_once(limit=1)

    [run] = store.list_agent_runs_for_task_generation(1, "initial")
    assert run.status == "failed"
    error = __import__("json").loads(run.structured_error_json)
    assert error == {
        "code": "runtime_route_unavailable",
        "failure_class": "authentication",
        "failure_code": "codex_provider_auth_failed",
    }
    assert secret not in run.structured_error_json
    assert run.final_result_json == ""
    task = store.get_reply_task(1)
    assert task is not None
    assert task.status == "failed"
    assert task.recovery_code == CODEX_PROVIDER_AUTH_FAILED
    assert task.locked_at is None
    attempt = store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "runtime_failure"
    assert attempt.send_error == CODEX_PROVIDER_AUTH_FAILED


def test_retryable_runtime_transport_failure_requeues_and_unlocks_task(store, account):
    from app.agent_runtime_contracts import RuntimeFailureClass
    from app.agent_runtime_router import RoutedCodexExecutionError

    store.enqueue_reply_task(
        channel="wechat",
        conversation_id="u9",
        conversation_title="Alex",
        single_chat=True,
        trigger_message_id="m1",
        trigger_create_time="2026-07-17T10:00:00",
        trigger_sender="Alex",
        trigger_text="下午能给结论吗",
    )

    class RuntimeFailingRunner:
        def decide(self, *_args, **_kwargs):
            raise RoutedCodexExecutionError(
                "runtime_route_unavailable",
                "unsafe provider detail",
                failure_class=RuntimeFailureClass.TRANSPORT,
                failure_code="codex_transport_disconnected",
                retryable_external_dependency=True,
            )

    now = datetime(2026, 8, 8, 8, 0, tzinfo=timezone.utc)
    consumer = WechatReplyConsumer(
        store,
        RuntimeFailingRunner(),
        reader=None,
        account=account,
        retry_delay=timedelta(minutes=5),
        now_provider=lambda: now,
    )

    with pytest.raises(WechatTaskProcessingError):
        consumer.run_once(limit=1)

    task = store.get_reply_task(1)
    assert task is not None
    assert task.status == "pending"
    assert task.available_at == "2026-08-08 08:05:00"
    assert task.locked_at is None
    assert task.error == "codex_transport_disconnected"
    [run] = store.list_agent_runs_for_task_generation(1, "initial")
    assert run.status == "failed"
    assert "unsafe provider detail" not in run.structured_error_json


def test_external_dependency_failure_defers_wechat_task_for_retry(
    fake_codex, store, account
):
    store.enqueue_reply_task(
        channel="wechat", conversation_id="u9", conversation_title="Alex",
        single_chat=True, trigger_message_id="m1",
        trigger_create_time="2026-07-17T10:00:00", trigger_sender="Alex",
        trigger_text="下午能给结论吗",
    )
    now = datetime(2026, 8, 8, 8, 0, tzinfo=timezone.utc)
    consumer = WechatReplyConsumer(
        store,
        fake_codex,
        reader=None,
        account=account,
        max_task_attempts=3,
        retry_delay=timedelta(minutes=5),
        now_provider=lambda: now,
    )
    fake_codex.decision = CodexDecision(
        action=CodexAction.STOP_WITH_ERROR,
        reason="provider_unavailable",
        audit_summary="外部推理服务暂不可用",
        external_dependency_failed=True,
    )

    assert consumer.run_once(limit=1) == 1

    task = store.get_reply_task(1)
    assert task is not None
    assert task.status == "pending"
    assert task.attempts == 1
    assert task.available_at == "2026-08-08 08:05:00"
    assert task.error == "provider_unavailable"
    attempt = store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert attempt.send_error == "provider_unavailable"
    assert store.claim_reply_tasks(
        1,
        now="2026-08-08 08:05:01",
        channel="wechat",
    )[0].id == task.id


def test_auth_failure_records_recoverable_login_state(fake_codex, consumer, store):
    fake_codex.decision = CodexDecision(
        action=CodexAction.STOP_WITH_ERROR,
        reason="native authentication unavailable",
        audit_summary="native authentication unavailable",
        external_dependency_failed=True,
        failure_code=CODEX_PROVIDER_AUTH_FAILED,
    )

    assert consumer.run_once(limit=1) == 1

    task = store.get_reply_task(1)
    assert task is not None
    assert task.status == "failed"
    assert task.recovery_code == CODEX_PROVIDER_AUTH_FAILED
    assert task.available_at == ""


def test_auth_recovery_reprocesses_original_wechat_message(
    fake_codex, consumer, store, monkeypatch
):
    fake_codex.decision = CodexDecision(
        action=CodexAction.STOP_WITH_ERROR,
        reason="native authentication unavailable",
        audit_summary="native authentication unavailable",
        external_dependency_failed=True,
        failure_code=CODEX_PROVIDER_AUTH_FAILED,
    )
    assert consumer.run_once(limit=1) == 1

    monkeypatch.setattr(
        "app.wechat.consumer.recover_native_codex_auth_failures",
        lambda current_store, *, channel: current_store.recover_failed_native_codex_auth_tasks(
            channel=channel,
            reason="codex_auth_recovered",
        ),
    )
    fake_codex.decision = CodexDecision(
        action=CodexAction.NO_REPLY,
        audit_summary="感谢消息无需再回复。",
    )

    assert consumer.run_once(limit=1) == 1
    task = store.get_reply_task(1)
    assert task is not None
    assert task.status == "done"
    assert task.attempts == 1
    assert task.error == ""


def test_consumer_marks_read_only_decision_phase_before_calling_codex(
    consumer, store
):
    observed_phases: list[str] = []

    class PhaseInspectingRunner:
        def decide(self, *_args, **_kwargs):
            task = store.get_reply_task(1)
            assert task is not None
            observed_phases.append(task.error)
            return CodexDecision(
                action=CodexAction.NO_REPLY,
                audit_summary="无需回复。",
            )

    consumer.runner = PhaseInspectingRunner()

    assert consumer.run_once(limit=1) == 1
    assert observed_phases == ["wechat_read_only_decision_running"]


def test_consumer_passes_current_processing_time_to_prompt(
    fake_codex, store, account
):
    store.enqueue_reply_task(
        channel="wechat", conversation_id="u9", conversation_title="Alex",
        single_chat=True, trigger_message_id="m1",
        trigger_create_time="2026-07-17T10:00:00+08:00", trigger_sender="Alex",
        trigger_text="早点来公司",
    )
    now = datetime(2026, 7, 18, 9, 0, tzinfo=timezone(timedelta(hours=8)))
    consumer = WechatReplyConsumer(
        store,
        fake_codex,
        reader=None,
        account=account,
        now_provider=lambda: now,
    )
    fake_codex.decision = CodexDecision(
        action=CodexAction.NO_REPLY,
        audit_summary="消息已过时，无需补发。",
    )

    assert consumer.run_once(limit=1) == 1

    assert "2026-07-18T09:00:00+08:00" in fake_codex.prompts[0]


def test_corrected_generation_replaces_unsent_wechat_delivery(
    fake_codex, consumer, store
):
    fake_codex.decision = CodexDecision(
        action=CodexAction.SEND_REPLY,
        reply_text="旧回复",
        reason="first",
        audit_summary="first",
    )
    assert consumer.run_once(limit=1) == 1
    first = store.get_reply_task(1)
    assert first is not None

    with store._connect() as db:
        db.execute(
            "update reply_tasks set status='pending', execution_generation='corrected' "
            "where id=1"
        )
    fake_codex.decision = CodexDecision(
        action=CodexAction.SEND_REPLY,
        reply_text="修正版回复",
        reason="corrected",
        audit_summary="corrected",
    )

    assert consumer.run_once(limit=1) == 1

    delivery = store.get_wechat_delivery_for_task(1)
    assert delivery is not None
    assert delivery.reply_text == "修正版回复"
    assert delivery.execution_generation == "corrected"


def test_stale_wechat_worker_cannot_persist_attempt_or_delivery(
    fake_codex, consumer, store
):
    [claimed] = store.claim_reply_tasks(1, channel="wechat")

    class RotatingRunner:
        def decide(self, *_args, **_kwargs):
            store.rotate_reply_task_execution_generation(claimed.id)
            return CodexDecision(
                action=CodexAction.SEND_REPLY,
                reply_text="旧 worker 回复",
                reason="stale",
                audit_summary="stale",
            )

    stale_consumer = WechatReplyConsumer(
        store, RotatingRunner(), reader=None, account=consumer.account
    )

    with pytest.raises(AgentRunLeaseLostError):
        stale_consumer.process(claimed)

    assert store.get_wechat_delivery_for_task(claimed.id) is None
    assert store.get_latest_reply_attempt_for_trigger("u9", "m1") is None


def test_consumer_error_keeps_trigger_identity(fake_codex, consumer, store):
    class FailingRunner:
        def decide(self, *_args, **_kwargs):
            raise RuntimeError("decision failed")

    consumer.runner = FailingRunner()

    with pytest.raises(WechatTaskProcessingError) as caught:
        consumer.run_once(limit=1)

    assert caught.value.conversation_id == "u9"
    assert caught.value.trigger_message_id == "m1"
    assert str(caught.value) == "decision failed"
    task = store.get_reply_task(1)
    assert task is not None
    assert task.status == "pending"
    assert task.error == "wechat_decision_failed"
