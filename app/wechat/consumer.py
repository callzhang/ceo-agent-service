"""WeChat reply consumer: claim channel-isolated tasks, decide with the existing
Codex runner, and prepare a fail-closed delivery.

The consumer never sends. For send_reply / ask_clarifying_question it leak-checks
the text and records exactly one ``wechat_deliveries`` row in ``ready_to_send``;
actual delivery is the sender's job (Task 10). DingTalk-only system actions are
rejected as a failed decision rather than executed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

from pydantic import ValidationError

from app.agent_envelope import SendDingTalkReplyAction
from app.codex_failure import CODEX_PROVIDER_AUTH_FAILED
from app.codex_runner import recover_native_codex_auth_failures
from app.dingtalk_models import CodexAction
from app.store import AgentRole
from app.wechat.models import WechatAccount, WechatMessage
from app.wechat.prompt import build_wechat_turn_prompt


class WechatTaskProcessingError(RuntimeError):
    def __init__(
        self,
        conversation_id: str,
        trigger_message_id: str,
        detail: str,
    ) -> None:
        super().__init__(detail)
        self.conversation_id = conversation_id
        self.trigger_message_id = trigger_message_id


def _is_reply_transport_action(action: object) -> bool:
    """Whether an action only materializes the already-decided chat response."""
    try:
        SendDingTalkReplyAction.model_validate(action)
    except ValidationError:
        return False
    return True


class WechatReplyConsumer:
    def __init__(self, store, runner, reader, account: WechatAccount, *,
                 leak_check: Callable[[str], str] | None = None,
                 max_task_attempts: int = 3,
                 retry_delay: timedelta = timedelta(minutes=1),
                 now_provider: Callable[[], datetime] | None = None):
        if max_task_attempts <= 0:
            raise ValueError("max_task_attempts must be positive")
        if retry_delay.total_seconds() < 0:
            raise ValueError("retry_delay must not be negative")
        self.store = store
        self.runner = runner
        self.reader = reader
        self.account = account
        self.leak_check = leak_check
        self.max_task_attempts = max_task_attempts
        self.retry_delay = retry_delay
        self.now_provider = now_provider or (lambda: datetime.now().astimezone())

    def _retry_available_at(self) -> str:
        return (
            self.now_provider().astimezone(timezone.utc) + self.retry_delay
        ).strftime("%Y-%m-%d %H:%M:%S")

    def run_once(self, limit: int = 50) -> int:
        recover_native_codex_auth_failures(self.store, channel="wechat")
        processed = 0
        for task in self.store.claim_reply_tasks(limit, channel="wechat"):
            try:
                self.process(task)
            except OSError:
                raise
            except Exception as exc:
                raise WechatTaskProcessingError(
                    task.conversation_id,
                    task.trigger_message_id,
                    str(exc),
                ) from exc
            processed += 1
        return processed

    def _trigger_message(self, task) -> WechatMessage:
        raw = task.trigger_message_json
        if raw and raw != "{}":
            try:
                return WechatMessage.model_validate_json(raw)
            except Exception:
                pass
        return WechatMessage(
            account_id=self.account.account_id,
            conversation_id=task.conversation_id,
            message_id=task.trigger_message_id,
            sender_id="",
            sender_display_name=task.trigger_sender,
            conversation_type="direct" if task.single_chat else "group",
            direction="inbound",
            sent_at=task.trigger_create_time,
            kind="text",
            text=task.trigger_text,
            source_version=self.account.app_version,
        )

    def process(self, task) -> None:
        trigger = self._trigger_message(task)
        context: list[WechatMessage] = []
        if self.reader is not None:
            try:
                context = self.reader.read_messages(
                    self.account, conversation_id=trigger.conversation_id,
                    conversation_type=trigger.conversation_type, limit=20,
                )
            except Exception:
                context = []
        try:
            from app.wechat.article import enrich_context
            context = enrich_context(context)
        except Exception:
            pass
        prompt = build_wechat_turn_prompt(
            trigger,
            context,
            current_time=self.now_provider().isoformat(),
        )
        self.store.mark_wechat_read_only_decision_started(
            task.id,
            expected_execution_generation=task.execution_generation,
        )
        turn_attempt = max(task.attempts - 1, 0)
        run_owner = (
            f"wechat-decision:{task.id}:{task.execution_generation}:{turn_attempt}"
        )
        run_claim = self.store.claim_agent_run(
            task.id,
            task.execution_generation,
            role=AgentRole.CONSUMER,
            proposal_revision=0,
            turn_attempt=turn_attempt,
            parent_agent_run_id=None,
            operation_id="",
            owner=run_owner,
            lease_seconds=max(1800, int(self.retry_delay.total_seconds()) + 60),
        )
        if not run_claim.claimed:
            # A duplicate worker must not turn a recoverable task into a
            # permanent processing row.  Keep ownership with a live run; if
            # the persisted run is already terminal, requeue this generation
            # so the normal worker path can derive the next state.
            if run_claim.run.status == "running":
                return
            self.store.requeue_reply_task(
                task.id,
                "wechat_decision_agent_run_already_terminal",
                expected_execution_generation=task.execution_generation,
                available_at=self._retry_available_at(),
            )
            return
        try:
            decision = self.runner.decide(prompt, None, run_id=run_claim.run.id)
        except Exception as exc:
            from app.agent_runtime_router import RoutedCodexExecutionError

            if isinstance(exc, RoutedCodexExecutionError):
                structured_error = {
                    "code": exc.code,
                    "failure_class": (
                        exc.failure_class.value if exc.failure_class is not None else ""
                    ),
                    "failure_code": exc.failure_code,
                }
            else:
                structured_error = {
                    "code": "wechat_decision_failed",
                    "detail": str(exc)[:500],
                }
            self.store.fail_agent_run(
                run_claim.run.id,
                structured_error,
                owner=run_owner,
            )
            if isinstance(exc, RoutedCodexExecutionError):
                failure_code = exc.failure_code or exc.code
                retryable = (
                    exc.retryable_external_dependency
                    and failure_code != CODEX_PROVIDER_AUTH_FAILED
                    and task.attempts < self.max_task_attempts
                )
                self.store.finalize_wechat_reply_task(
                    task_id=task.id,
                    expected_execution_generation=task.execution_generation,
                    action="runtime_failure",
                    sensitivity_kind="normal",
                    codex_reason=failure_code,
                    draft_reply_text="",
                    audit_summary=failure_code,
                    send_status="failed",
                    send_error=failure_code,
                    recovery_code=(
                        CODEX_PROVIDER_AUTH_FAILED
                        if failure_code == CODEX_PROVIDER_AUTH_FAILED
                        else ""
                    ),
                    task_status="pending" if retryable else "failed",
                    available_at=self._retry_available_at() if retryable else "",
                )
            else:
                retryable = task.attempts < self.max_task_attempts
                self.store.finalize_wechat_reply_task(
                    task_id=task.id,
                    expected_execution_generation=task.execution_generation,
                    action="decision_failure",
                    sensitivity_kind="normal",
                    codex_reason="wechat_decision_failed",
                    draft_reply_text="",
                    audit_summary="wechat_decision_failed",
                    send_status="failed",
                    send_error="wechat_decision_failed",
                    task_status="pending" if retryable else "failed",
                    available_at=self._retry_available_at() if retryable else "",
                )
            raise
        self.store.complete_agent_run(
            run_claim.run.id,
            decision.model_dump(mode="json"),
            owner=run_owner,
        )

        if decision.action in (CodexAction.SEND_REPLY, CodexAction.ASK_CLARIFYING_QUESTION):
            unsupported_actions = [
                action
                for action in decision.system_actions
                if not _is_reply_transport_action(action)
            ]
            if unsupported_actions:
                self.store.finalize_wechat_reply_task(
                    task_id=task.id,
                    expected_execution_generation=task.execution_generation,
                    action=getattr(decision.action, "value", str(decision.action)),
                    sensitivity_kind=getattr(decision, "sensitivity_kind", "") or "normal",
                    codex_reason=decision.reason or "",
                    draft_reply_text=decision.reply_text or "",
                    audit_summary=getattr(decision, "audit_summary", "") or "",
                    send_status="failed",
                    send_error="dingtalk_only_system_actions_rejected",
                    task_status="failed",
                )
                return
            text = decision.reply_text or ""
            if self.leak_check is not None:
                text = self.leak_check(text)
            self.store.finalize_wechat_reply_task(
                task_id=task.id,
                expected_execution_generation=task.execution_generation,
                action=getattr(decision.action, "value", str(decision.action)),
                sensitivity_kind=getattr(decision, "sensitivity_kind", "") or "normal",
                codex_reason=decision.reason or "",
                draft_reply_text=decision.reply_text or "",
                audit_summary=getattr(decision, "audit_summary", "") or "",
                send_status="pending",
                account_id=self.account.account_id,
                target_type="direct" if task.single_chat else "group",
                target_id=trigger.conversation_id,
                conversation_id=trigger.conversation_id,
                reply_text=text,
                evidence={
                    "reason": decision.reason,
                    "audit_summary": decision.audit_summary,
                    "trigger_text": trigger.text,
                },
            )
        elif decision.action in (CodexAction.NO_REPLY, CodexAction.HANDOFF_TO_HUMAN):
            self.store.finalize_wechat_reply_task(
                task_id=task.id,
                expected_execution_generation=task.execution_generation,
                action=getattr(decision.action, "value", str(decision.action)),
                sensitivity_kind=getattr(decision, "sensitivity_kind", "") or "normal",
                codex_reason=decision.reason or "",
                draft_reply_text=decision.reply_text or "",
                audit_summary=getattr(decision, "audit_summary", "") or "",
                send_status="skipped",
                send_error=getattr(decision.action, "value", str(decision.action)),
            )
        else:  # STOP_WITH_ERROR (and anything unexpected)
            retryable = (
                bool(getattr(decision, "external_dependency_failed", False))
                and decision.failure_code != CODEX_PROVIDER_AUTH_FAILED
                and task.attempts < self.max_task_attempts
            )
            self.store.finalize_wechat_reply_task(
                task_id=task.id,
                expected_execution_generation=task.execution_generation,
                action=getattr(decision.action, "value", str(decision.action)),
                sensitivity_kind=getattr(decision, "sensitivity_kind", "") or "normal",
                codex_reason=decision.reason or "",
                draft_reply_text=decision.reply_text or "",
                audit_summary=getattr(decision, "audit_summary", "") or "",
                send_status="failed",
                send_error=decision.reason or "stop_with_error",
                recovery_code=(
                    CODEX_PROVIDER_AUTH_FAILED
                    if decision.failure_code == CODEX_PROVIDER_AUTH_FAILED
                    else ""
                ),
                task_status="pending" if retryable else "failed",
                available_at=self._retry_available_at() if retryable else "",
            )
