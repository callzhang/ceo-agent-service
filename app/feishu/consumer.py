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
from app.leak_check import contains_forbidden_leak


class UnsafeFeishuReply(ValueError):
    pass


MIN_PROCESSING_STALE_SECONDS = 30 * 60


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
        try:
            runner_timeout = int(getattr(runner, "timeout_seconds", 0) or 0)
        except (TypeError, ValueError):
            runner_timeout = 0
        self.processing_stale_seconds = max(
            MIN_PROCESSING_STALE_SECONDS,
            # CodexDecisionRunner may consume two full executor windows while
            # repairing malformed JSON, plus bounded session-grace reads.
            # Reclaiming after only one timeout can duplicate model execution.
            (runner_timeout * 2) + (3 * 60),
        )

    def run_once(self, limit: int = 50) -> int:
        if limit <= 0:
            return 0
        # A standalone Feishu consumer must recover its own crash residue; the
        # main DingTalk worker is not guaranteed to be running.  Scope the
        # reset so one channel cannot steal another channel's task.
        self.store.reset_stale_processing_reply_tasks(
            self.processing_stale_seconds, channel="feishu"
        )
        processed = 0
        for _ in range(limit):
            claimed = self.store.claim_reply_tasks(1, channel="feishu")
            if not claimed:
                break
            task = claimed[0]
            try:
                self.process(task)
            except Exception as exc:
                # Keep one corrupt task or a stale-lease CAS miss from
                # terminating the long-running consumer component.
                self._fail_claimed_task(
                    task,
                    f"feishu_consumer_failed:{type(exc).__name__}",
                )
            processed += 1
        return processed

    def _fail_claimed_task(self, task, error: str) -> bool:
        return self.store.fail_processing_reply_task(
            task.id,
            error,
            channel="feishu",
            lease_token=task.lease_token,
        )

    @staticmethod
    def _trigger(task) -> FeishuInboundMessage:
        raw = str(getattr(task, "trigger_message_json", "") or "")
        if not raw or raw == "{}":
            raise ValueError("missing normalized Feishu trigger")
        return FeishuInboundMessage.model_validate_json(raw)

    def process(self, task) -> None:
        try:
            trigger = self._trigger(task)
        except Exception:
            self._fail_claimed_task(task, "invalid_feishu_trigger")
            return

        try:
            if self.store.recover_feishu_reply_task(
                task.id, lease_token=task.lease_token
            ):
                return
        except Exception:
            self._fail_claimed_task(task, "feishu_recovery_failed")
            return

        try:
            context = self.store.list_feishu_context(
                trigger.chat_id,
                limit=self.context_limit,
                app_id=trigger.app_id,
                thread_id=trigger.thread_id,
                before_event_id=trigger.event_id,
            )
            prompt = build_feishu_turn_prompt(
                trigger, context, context_limit=self.context_limit
            )
            decision = self.runner.decide(prompt, None)
        except Exception as exc:
            self._fail_claimed_task(
                task, f"feishu_decision_failed:{type(exc).__name__}"
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
        finalize_common = {
            "lease_token": task.lease_token,
            "action": action,
            "sensitivity_kind": _sensitivity_value(decision),
            "codex_reason": str(getattr(decision, "reason", "") or ""),
            "draft_reply_text": audited_draft,
            "audit_summary": str(
                getattr(decision, "audit_summary", "") or ""
            ),
        }

        if action in {
            CodexAction.SEND_REPLY.value,
            CodexAction.ASK_CLARIFYING_QUESTION.value,
        }:
            if _contains_forbidden_side_effect(decision):
                self.store.finalize_feishu_reply_task(
                    task.id,
                    **finalize_common,
                    task_status="failed",
                    send_status="failed",
                    error="external_system_actions_rejected",
                )
                return
            if leak_failed:
                self.store.finalize_feishu_reply_task(
                    task.id,
                    **finalize_common,
                    task_status="failed",
                    send_status="failed",
                    error="reply_failed_leak_check",
                )
                return
            text = checked_text
            if not text:
                self.store.finalize_feishu_reply_task(
                    task.id,
                    **finalize_common,
                    task_status="failed",
                    send_status="failed",
                    error="empty_feishu_reply",
                )
                return
            self.store.finalize_feishu_reply_task(
                task.id,
                **{**finalize_common, "draft_reply_text": text},
                task_status="done",
                send_status="pending",
                delivery_app_id=trigger.app_id,
                delivery_chat_id=trigger.chat_id,
                reply_to_message_id=trigger.message_id,
                reply_in_thread=bool(
                    trigger.thread_id or trigger.chat_type == "topic"
                ),
                idempotency_key=delivery_idempotency_key(
                    app_id=trigger.app_id,
                    reply_task_id=task.id,
                    trigger_message_id=trigger.message_id,
                ),
            )
            return

        if action in {
            CodexAction.NO_REPLY.value,
            CodexAction.HANDOFF_TO_HUMAN.value,
        }:
            self.store.finalize_feishu_reply_task(
                task.id,
                **finalize_common,
                task_status="done",
                send_status="skipped",
            )
            return
        self.store.finalize_feishu_reply_task(
            task.id,
            **finalize_common,
            task_status="failed",
            send_status="failed",
            error="unexpected_feishu_decision",
        )
