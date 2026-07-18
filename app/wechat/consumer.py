"""WeChat reply consumer: claim channel-isolated tasks, decide with the existing
Codex runner, and prepare a fail-closed delivery.

The consumer never sends. For send_reply / ask_clarifying_question it leak-checks
the text and records exactly one ``wechat_deliveries`` row in ``ready_to_send``;
actual delivery is the sender's job (Task 10). DingTalk-only system actions are
rejected as a failed decision rather than executed.
"""
from __future__ import annotations

from typing import Callable

from app.dingtalk_models import CodexAction
from app.wechat.models import WechatAccount, WechatMessage
from app.wechat.prompt import build_wechat_turn_prompt


class WechatReplyConsumer:
    def __init__(self, store, runner, reader, account: WechatAccount, *,
                 leak_check: Callable[[str], str] | None = None):
        self.store = store
        self.runner = runner
        self.reader = reader
        self.account = account
        self.leak_check = leak_check

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
        prompt = build_wechat_turn_prompt(trigger, context)
        decision = self.runner.decide(prompt, None)

        if decision.action in (CodexAction.SEND_REPLY, CodexAction.ASK_CLARIFYING_QUESTION):
            if getattr(decision, "system_actions", None):
                self.store.fail_reply_task(task.id, "dingtalk_only_system_actions_rejected")
                return
            text = decision.reply_text or ""
            if self.leak_check is not None:
                text = self.leak_check(text)
            self.store.create_wechat_delivery(
                reply_task_id=task.id,
                account_id=self.account.account_id,
                target_type="direct" if task.single_chat else "group",
                target_id=trigger.conversation_id,
                conversation_id=trigger.conversation_id,
                reply_text=text,
                evidence={"reason": decision.reason, "audit_summary": decision.audit_summary},
            )
            self.store.complete_reply_task(task.id)
        elif decision.action in (CodexAction.NO_REPLY, CodexAction.HANDOFF_TO_HUMAN):
            self.store.complete_reply_task(task.id)
        else:  # STOP_WITH_ERROR (and anything unexpected) -> bounded retry via fail
            self.store.fail_reply_task(task.id, decision.reason or "stop_with_error")
