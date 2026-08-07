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
from app.dingtalk_models import CodexAction
from app.wechat.models import WechatAccount, WechatMessage
from app.wechat.prompt import build_wechat_turn_prompt


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
        ).isoformat()

    def run_once(self, limit: int = 50) -> int:
        processed = 0
        for task in self.store.claim_reply_tasks(limit, channel="wechat"):
            self.process(task)
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
        prompt = build_wechat_turn_prompt(trigger, context)
        self.store.mark_wechat_read_only_decision_started(
            task.id,
            expected_execution_generation=task.execution_generation,
        )
        decision = self.runner.decide(prompt, None)

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
                task_status="pending" if retryable else "failed",
                available_at=self._retry_available_at() if retryable else "",
            )
