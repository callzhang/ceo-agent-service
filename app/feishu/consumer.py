"""Claim Feishu tasks, run Codex, and prepare audited deliveries.

This module has no reference to a channel client or send API.  The consumer's
strong invariant is that it can only create ``ready_to_send`` rows; a separate
delivery worker owns every outbound side effect.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.dingtalk_models import CodexAction
from app.feishu.delivery import delivery_idempotency_key
from app.feishu.models import FeishuInboundMessage
from app.feishu.prompt import build_feishu_turn_prompt
from app.history import safe_observability_error
from app.leak_check import contains_forbidden_leak


class UnsafeFeishuReply(ValueError):
    pass


def _action_value(action: Any) -> str:
    return str(getattr(action, "value", action))


def _sensitivity_value(decision: Any) -> str:
    value = getattr(decision, "sensitivity_kind", "general")
    return str(getattr(value, "value", value) or "general")


def _contains_forbidden_side_effect(decision: Any) -> bool:
    calendar = getattr(decision, "calendar_response_status", "")
    calendar_value = str(getattr(calendar, "value", calendar) or "").lower()
    return bool(
        getattr(decision, "system_actions", None)
        or getattr(decision, "ding_self", False)
        or calendar_value not in {"", "none"}
    )


def _default_leak_check(text: str) -> str:
    if contains_forbidden_leak(text):
        raise UnsafeFeishuReply("reply_failed_leak_check")
    return text


class FeishuReplyConsumer:
    """Channel-isolated Codex consumer that never possesses a sender."""

    def __init__(
        self,
        store,
        runner,
        *,
        context_limit: int = 20,
        leak_check: Callable[[str], str] | None = None,
    ):
        if context_limit <= 0:
            raise ValueError("context_limit must be positive")
        if getattr(runner, "tool_mode", None) != "none":
            raise ValueError(
                "Feishu consumer requires hard Codex tool isolation"
            )
        self.store = store
        self.runner = runner
        self.context_limit = context_limit
        self.leak_check = leak_check or _default_leak_check

    def run_once(self, limit: int = 50) -> int:
        if limit <= 0:
            return 0
        processed = 0
        for task in self.store.claim_reply_tasks(limit, channel="feishu"):
            self.process(task)
            processed += 1
        return processed

    @staticmethod
    def _trigger(task) -> FeishuInboundMessage:
        raw = str(getattr(task, "trigger_message_json", "") or "")
        if not raw or raw == "{}":
            raise ValueError("missing normalized Feishu trigger")
        return FeishuInboundMessage.model_validate_json(raw)

    def _record_attempt(
        self, task, trigger, decision, *, audited_draft: str
    ) -> int:
        return self.store.record_reply_attempt(
            conversation_id=trigger.chat_id,
            conversation_title=(
                trigger.chat_title
                or getattr(task, "conversation_title", "")
                or "Feishu conversation"
            ),
            trigger_message_id=trigger.message_id,
            trigger_sender=trigger.sender_name or trigger.sender_open_id,
            trigger_text=trigger.body_text,
            action=_action_value(decision.action),
            sensitivity_kind=_sensitivity_value(decision),
            codex_reason=safe_observability_error(
                getattr(decision, "reason", "") or ""
            ),
            draft_reply_text=audited_draft,
            audit_summary=safe_observability_error(
                getattr(decision, "audit_summary", "") or ""
            ),
            send_status="pending",
            channel="feishu",
        )

    def process(self, task) -> None:
        try:
            trigger = self._trigger(task)
        except Exception:
            self.store.fail_reply_task(task.id, "invalid_feishu_trigger")
            return

        try:
            context = self.store.list_feishu_context(
                trigger.chat_id,
                limit=self.context_limit,
                app_id=trigger.app_id,
            )
            prompt = build_feishu_turn_prompt(
                trigger, context, context_limit=self.context_limit
            )
            decision = self.runner.decide(prompt, None)
        except Exception as exc:
            self.store.fail_reply_task(
                task.id, f"feishu_decision_failed:{type(exc).__name__}"
            )
            return

        action = _action_value(decision.action)
        checked_text = ""
        leak_failed = False
        raw_draft = str(getattr(decision, "reply_text", "") or "").strip()
        if action in {
            CodexAction.SEND_REPLY.value,
            CodexAction.ASK_CLARIFYING_QUESTION.value,
        }:
            try:
                checked_text = self.leak_check(raw_draft).strip()
                # An injected formatter may redact, but may never bypass the
                # channel's mandatory final leak check.
                if contains_forbidden_leak(checked_text):
                    raise UnsafeFeishuReply("reply_failed_leak_check")
            except Exception:
                leak_failed = True
        audited_draft = (
            "[redacted unsafe draft]"
            if contains_forbidden_leak(raw_draft) or leak_failed
            else (checked_text if checked_text else raw_draft)
        )
        try:
            attempt_id = self._record_attempt(
                task, trigger, decision, audited_draft=audited_draft
            )
        except Exception:
            # No outbound row without a durable audit attempt.
            self.store.fail_reply_task(task.id, "feishu_attempt_audit_failed")
            return

        if action in {
            CodexAction.SEND_REPLY.value,
            CodexAction.ASK_CLARIFYING_QUESTION.value,
        }:
            if _contains_forbidden_side_effect(decision):
                self.store.fail_reply_task(
                    task.id, "external_system_actions_rejected"
                )
                return
            if leak_failed:
                self.store.fail_reply_task(task.id, "reply_failed_leak_check")
                return
            text = checked_text
            if not text:
                self.store.fail_reply_task(task.id, "empty_feishu_reply")
                return
            try:
                self.store.create_feishu_delivery(
                    reply_task_id=task.id,
                    attempt_id=attempt_id,
                    app_id=trigger.app_id,
                    chat_id=trigger.chat_id,
                    reply_to_message_id=trigger.message_id,
                    reply_in_thread=bool(
                        trigger.thread_id or trigger.chat_type == "topic"
                    ),
                    reply_text=text,
                    idempotency_key=delivery_idempotency_key(
                        app_id=trigger.app_id,
                        reply_task_id=task.id,
                        trigger_message_id=trigger.message_id,
                    ),
                )
            except Exception as exc:
                self.store.fail_reply_task(
                    task.id, f"feishu_delivery_create_failed:{type(exc).__name__}"
                )
                return
            self.store.complete_reply_task(task.id)
            return

        if action in {
            CodexAction.NO_REPLY.value,
            CodexAction.HANDOFF_TO_HUMAN.value,
        }:
            self.store.complete_reply_task(task.id)
            return
        self.store.fail_reply_task(
            task.id,
            "unexpected_feishu_decision",
        )
