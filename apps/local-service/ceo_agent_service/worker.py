import json
import re
import urllib.request
import zipfile
from datetime import datetime, timedelta
from io import BytesIO
from urllib.parse import urlsplit, urlunsplit

from pypdf import PdfReader

from ceo_agent_service.codex_decision import append_signature, contains_forbidden_leak
from ceo_agent_service.config import (
    assistant_signature,
    current_user_display_names,
    handoff_ack,
    principal_name,
)
from ceo_agent_service.dws_client import DwsClient, DwsDocumentSearchResult, DwsError
from ceo_agent_service.corpus import (
    MEDIA_OR_LINK_PATTERN,
    CorpusRecord,
    count_information_units,
    retrieve_similar_examples,
)
from ceo_agent_service.dingtalk_models import (
    CodexAction,
    CodexDecision,
    DingTalkConversation,
    DingTalkMessage,
)
from ceo_agent_service.notification import send_macos_notification
from ceo_agent_service.permission import PermissionAction, PermissionGate
from ceo_agent_service.prompt import LinkedDocumentContext, build_turn_prompt
from ceo_agent_service.store import AutoReplyStore, ReplyAttempt


HANDOFF_ACK = handoff_ack()
SPLIT_PERSON_SIGNATURE = assistant_signature()
CURRENT_USER_DISPLAY_NAMES = set(current_user_display_names())
TEXT_MESSAGE_TYPES = {"text"}
RENDERED_NON_TEXT_PREFIXES = (
    "[文件]",
    "[图片]",
    "[视频]",
    "[日程]",
)
DINGTALK_INTERNAL_OR_RENDERED_MEDIA_PATTERN = re.compile(
    r"dingtalk://|https?://[^\s)]*dingtalk\.com|\[(?:文件|图片|视频|日程)\]",
    re.IGNORECASE,
)
DINGTALK_APPROVAL_LINK_PATTERN = re.compile(
    r"aflow\.dingtalk\.com|dinghash(?:=|%3D)approval|swfrom(?:=|%3D)oa",
    re.IGNORECASE,
)
ORDINARY_EXTERNAL_LINK_PATTERN = re.compile(
    r"https?://(?![^\s)]*dingtalk\.com)\S+",
    re.IGNORECASE,
)
SYSTEM_STATUS_NOTIFICATION_PATTERN = re.compile(
    r"""
    ^\s*(?:
        (?:AI\s*)?自动同步(?:完成|成功|失败)(?:[:：]\S.*)?
        |已同步到(?:知识库|文档|项目)(?:[:：]\S.*)
        |(?:文件|文档)[^\n，,。；;？?]{0,40}(?:已上传|已更新|上传完成|更新完成)(?:[:：]\S.*)?
        |已更新文档(?:[:：]\S.*)?
        |(?:项目立项|流程|审批)[^\n，,。；;？?]{0,40}(?:已提交|已通过|被退回|已退回|已撤回|已流转)(?:[:：]\S.*)?
    )\s*$
    """,
    re.VERBOSE,
)
QUESTION_MARK_PATTERN = re.compile(r"[?？]")
FIELD_LINE_PATTERN = re.compile(r"^\s*[^:：\n]{1,60}[:：]\s*\S+")
MENTION_PATTERN = re.compile(r"@[^\s]+(?:\([^)]+\))?")
QUOTE_MENTION_PATTERN = re.compile(
    r"@[^\s@()]+(?:\s+[A-Za-z][^\s@()]*)?(?:\((?:[^()]|\([^()]*\))*\))?"
)
DINGTALK_DOC_URL_PATTERN = re.compile(r"https://alidocs\.dingtalk\.com/i/nodes/[^\s)\]]+")
FILE_MESSAGE_PATTERN = re.compile(r"^\s*\[文件]\s*(?P<name>.+?)\s*$")
QUOTE_WORD_OR_CJK_PATTERN = re.compile(r"[A-Za-z0-9]+(?:[-_'][A-Za-z0-9]+)*|[\u4e00-\u9fff]")
DINGTALK_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
GROUP_CONTEXT_RECOVERY_WINDOW = timedelta(hours=24)
QUOTE_INFORMATION_UNIT_LIMIT = 20
REFERENCED_FILE_CONTEXT_WINDOW = timedelta(minutes=10)
DOWNLOADED_FILE_MAX_BYTES = 50 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 30
PDF_TEXT_PAGE_LIMIT = 30


class DingTalkAutoReplyWorker:
    def __init__(
        self,
        store: AutoReplyStore,
        dws,
        codex,
        dry_run: bool = False,
        style_profile: str = "",
        style_records: list[CorpusRecord] | None = None,
        style_example_limit: int = 2,
        send_attempts: int = 2,
    ):
        self.store = store
        self.dws = dws
        self.codex = codex
        self.dry_run = dry_run
        self.style_profile = style_profile.strip()
        self.style_records = style_records or []
        self.style_example_limit = style_example_limit
        self.send_attempts = send_attempts
        self.permission_gate = PermissionGate(dws)

    def run_once(self, max_batches: int | None = None) -> None:
        processed_batches = 0
        conversations = self.dws.list_unread_conversations(count=50)
        mentioned_messages = self._mentioned_messages_by_conversation(conversations)
        for conversation in conversations:
            self.store.upsert_conversation(
                conversation_id=conversation.open_conversation_id,
                title=conversation.title,
                single_chat=conversation.single_chat,
                codex_session_id=None,
            )
            try:
                context_messages = self.dws.read_recent_messages(conversation)
                unread_messages = self.dws.read_unread_messages(conversation)
            except Exception as exc:
                self.store.record_error(
                    conversation.open_conversation_id,
                    None,
                    "read_messages",
                    str(exc),
                )
                self._notify(
                    title=f"CEO read messages failed: {conversation.title}",
                    message=str(exc)[:120],
                )
                continue
            unseen_context_messages = [
                message
                for message in unread_messages
                if not self.store.has_seen(message.open_message_id)
            ]
            if self.store.is_in_handoff(conversation.open_conversation_id):
                self._handle_active_handoff(conversation, unseen_context_messages)
                continue
            candidate_source_messages = self._candidate_source_messages(
                conversation,
                context_messages,
                unread_messages,
                mentioned_messages.get(conversation.open_conversation_id, []),
            )
            candidates = self._candidate_messages(
                conversation,
                candidate_source_messages,
            )
            new_messages = [
                message
                for message in candidates
                if not self.store.has_seen(message.open_message_id)
            ]
            if not new_messages:
                continue
            new_messages = self._skip_system_or_notification_messages(
                conversation,
                new_messages,
            )
            if not new_messages:
                continue
            prompt_context_messages = self._prompt_context_messages(
                context_messages, unread_messages
            )
            self._process_batch(conversation, new_messages, prompt_context_messages)
            processed_batches += 1
            if max_batches is not None and processed_batches >= max_batches:
                return

    def _mentioned_messages_by_conversation(
        self, conversations: list[DingTalkConversation]
    ) -> dict[str, list[DingTalkMessage]]:
        unread_group_ids = {
            conversation.open_conversation_id
            for conversation in conversations
            if not conversation.single_chat
        }
        if not unread_group_ids:
            return {}
        messages = self.dws.read_mentioned_messages(limit=100)
        grouped: dict[str, list[DingTalkMessage]] = {}
        for message in messages:
            if message.open_conversation_id not in unread_group_ids:
                continue
            grouped.setdefault(message.open_conversation_id, []).append(message)
        return grouped

    def rerun_message(
        self,
        conversation: DingTalkConversation,
        message_id: str,
        *,
        force_new_decision: bool = False,
    ) -> str:
        context_messages = self.dws.read_recent_messages(conversation)
        unread_messages = self.dws.read_unread_messages(conversation)
        prompt_context_messages = self._prompt_context_messages(
            context_messages, unread_messages
        )
        candidates = [
            message
            for message in prompt_context_messages
            if message.open_message_id == message_id
        ]
        if not candidates:
            raise ValueError(
                f"message not found in recent DingTalk context: {message_id}"
            )
        trigger = candidates[-1]
        if self._is_system_or_notification_message(trigger):
            self._record_system_or_notification_skip(conversation, trigger)
            self._mark_seen([trigger])
            return trigger.open_message_id
        self._process_batch(
            conversation,
            [trigger],
            prompt_context_messages,
            ignore_existing_attempt=force_new_decision,
        )
        return trigger.open_message_id

    def _skip_system_or_notification_messages(
        self,
        conversation: DingTalkConversation,
        messages: list[DingTalkMessage],
    ) -> list[DingTalkMessage]:
        remaining = []
        skipped = []
        for message in messages:
            if self._is_system_or_notification_message(message):
                skipped.append(message)
                self._record_system_or_notification_skip(conversation, message)
            else:
                remaining.append(message)
        self._mark_seen(skipped)
        return remaining

    def _record_system_or_notification_skip(
        self,
        conversation: DingTalkConversation,
        message: DingTalkMessage,
    ) -> None:
        existing_attempt = self.store.get_latest_reply_attempt_for_trigger(
            conversation.open_conversation_id,
            message.open_message_id,
        )
        if (
            existing_attempt
            and existing_attempt.action == CodexAction.NO_REPLY.value
            and existing_attempt.send_status == "skipped"
        ):
            return
        attempt_id = self.store.record_reply_attempt(
            conversation_id=conversation.open_conversation_id,
            conversation_title=conversation.title,
            trigger_message_id=message.open_message_id,
            trigger_sender=message.sender_name,
            trigger_text=message.content,
            action=CodexAction.NO_REPLY.value,
            sensitivity_kind="general",
            codex_reason="system_or_notification_message",
            audit_summary="系统类或通知类消息，无需自动回复。",
        )
        self.store.update_reply_attempt(
            attempt_id,
            send_status="skipped",
            send_error="no_reply",
        )

    @staticmethod
    def _is_system_or_notification_message(message: DingTalkMessage) -> bool:
        if message.message_type and message.message_type.lower() not in TEXT_MESSAGE_TYPES:
            return True
        content = message.content.strip()
        if content.startswith(RENDERED_NON_TEXT_PREFIXES):
            return True
        if content.startswith("[dingtalk://"):
            return True
        if DingTalkAutoReplyWorker._is_link_caption_only(content):
            return True
        if DingTalkAutoReplyWorker._is_structured_link_card(content):
            return True
        if DingTalkAutoReplyWorker._is_system_status_notification(content):
            return True
        return False

    @staticmethod
    def _is_system_status_notification(content: str) -> bool:
        if not SYSTEM_STATUS_NOTIFICATION_PATTERN.match(content):
            return False
        if DINGTALK_APPROVAL_LINK_PATTERN.search(content):
            return False
        if ORDINARY_EXTERNAL_LINK_PATTERN.search(content):
            return False
        return not DingTalkAutoReplyWorker._has_question_outside_links(content)

    @staticmethod
    def _is_link_caption_only(content: str) -> bool:
        if not MEDIA_OR_LINK_PATTERN.search(content):
            return False
        if not DINGTALK_INTERNAL_OR_RENDERED_MEDIA_PATTERN.search(content):
            return False
        if DingTalkAutoReplyWorker._has_question_outside_links(content):
            return False
        text_without_links = MEDIA_OR_LINK_PATTERN.sub(" ", content)
        text_without_mentions = MENTION_PATTERN.sub(" ", text_without_links)
        return count_information_units(text_without_mentions) <= 2

    @staticmethod
    def _is_structured_link_card(content: str) -> bool:
        if not MEDIA_OR_LINK_PATTERN.search(content):
            return False
        if not DINGTALK_INTERNAL_OR_RENDERED_MEDIA_PATTERN.search(content):
            return False
        if DINGTALK_APPROVAL_LINK_PATTERN.search(content):
            return False
        if DingTalkAutoReplyWorker._has_question_outside_links(content):
            return False
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        if len(lines) < 4:
            return False
        field_line_count = sum(1 for line in lines if FIELD_LINE_PATTERN.match(line))
        return field_line_count >= 3 and field_line_count / len(lines) >= 0.45

    @staticmethod
    def _has_question_outside_links(content: str) -> bool:
        return bool(QUESTION_MARK_PATTERN.search(MEDIA_OR_LINK_PATTERN.sub(" ", content)))

    def _candidate_messages(
        self,
        conversation: DingTalkConversation,
        messages: list[DingTalkMessage],
    ) -> list[DingTalkMessage]:
        current_user_message_times = [
            message.create_time
            for message in messages
            if self._is_current_user_message_for_candidate_filter(message)
        ]
        latest_current_user_message_time = (
            max(current_user_message_times) if current_user_message_times else None
        )
        if conversation.single_chat:
            eligible_messages = messages
        else:
            eligible_messages = [
                message for message in messages if message.mentions_derek()
            ]
        return [
            message
            for message in eligible_messages
            if not self._is_current_user_message_for_candidate_filter(message)
            and (
                latest_current_user_message_time is None
                or message.create_time > latest_current_user_message_time
            )
        ]

    @staticmethod
    def _candidate_source_messages(
        conversation: DingTalkConversation,
        context_messages: list[DingTalkMessage],
        unread_messages: list[DingTalkMessage],
        mentioned_messages: list[DingTalkMessage] | None = None,
    ) -> list[DingTalkMessage]:
        if conversation.single_chat:
            return unread_messages
        recovery_start_time = DingTalkAutoReplyWorker._group_context_recovery_start_time(
            unread_messages
        )
        unread_message_ids = {
            message.open_message_id
            for message in unread_messages
        }
        result: list[DingTalkMessage] = []
        seen_message_ids: set[str] = set()
        for message in [*context_messages, *unread_messages]:
            if message.open_message_id in seen_message_ids:
                continue
            if message.open_message_id not in unread_message_ids and (
                recovery_start_time is None
                or message.create_time < recovery_start_time
            ):
                continue
            seen_message_ids.add(message.open_message_id)
            result.append(message)
        for message in sorted(mentioned_messages or [], key=lambda item: item.create_time):
            if message.open_message_id in seen_message_ids:
                continue
            seen_message_ids.add(message.open_message_id)
            result.append(message)
        return result

    @staticmethod
    def _group_context_recovery_start_time(
        unread_messages: list[DingTalkMessage],
    ) -> str | None:
        if not unread_messages:
            return None
        earliest_unread_time = min(
            datetime.strptime(message.create_time, DINGTALK_TIME_FORMAT)
            for message in unread_messages
        )
        return (earliest_unread_time - GROUP_CONTEXT_RECOVERY_WINDOW).strftime(
            DINGTALK_TIME_FORMAT
        )

    def _is_current_user_message_for_candidate_filter(
        self, message: DingTalkMessage
    ) -> bool:
        if message.sender_name.strip() in CURRENT_USER_DISPLAY_NAMES:
            return True
        try:
            return self.dws.is_current_user_message(message)
        except Exception:
            return False

    @staticmethod
    def _prompt_context_messages(
        previous_messages: list[DingTalkMessage],
        unread_messages: list[DingTalkMessage],
        previous_limit: int = 20,
    ) -> list[DingTalkMessage]:
        previous_messages = sorted(
            previous_messages,
            key=lambda message: datetime.strptime(
                message.create_time, DINGTALK_TIME_FORMAT
            ),
        )
        result: list[DingTalkMessage] = []
        seen_message_ids: set[str] = set()
        for message in [*previous_messages[-previous_limit:], *unread_messages]:
            if message.open_message_id in seen_message_ids:
                continue
            seen_message_ids.add(message.open_message_id)
            result.append(message)
        return result

    def _handle_active_handoff(
        self,
        conversation: DingTalkConversation,
        unseen_messages: list[DingTalkMessage],
    ) -> None:
        try:
            handoff_create_time = self.store.get_handoff_message_create_time(
                conversation.open_conversation_id
            )
            manual_clear_message = self._manual_handoff_clear_message(
                unseen_messages,
                handoff_create_time=handoff_create_time,
            )
        except Exception as exc:
            trigger_message_id = (
                unseen_messages[-1].open_message_id if unseen_messages else None
            )
            self.store.record_error(
                conversation.open_conversation_id,
                trigger_message_id,
                "handoff_clear",
                str(exc),
            )
            self._notify(
                title=f"CEO handoff clear failed: {conversation.title}",
                message=str(exc)[:120],
            )
            return
        if manual_clear_message is not None:
            if not self.dry_run:
                self.store.clear_handoff(
                    conversation.open_conversation_id,
                    manual_clear_message.open_message_id,
                )
            self._mark_seen(unseen_messages)
            self._notify(
                title=f"CEO handoff cleared: {conversation.title}",
                message=manual_clear_message.content[:120],
            )
            return
        if unseen_messages and not self.dry_run:
            self._notify(
                title=f"CEO 自动回复已暂停: {conversation.title}",
                message=(
                    "该会话已交给本人处理，本次未生成回复。"
                    f"最新消息：{unseen_messages[-1].content[:120]}"
                ),
            )
            self._mark_seen(unseen_messages)

    def _manual_handoff_clear_message(
        self,
        messages: list[DingTalkMessage],
        handoff_create_time: str | None,
    ) -> DingTalkMessage | None:
        for message in messages:
            if not self._message_after_handoff(message, handoff_create_time):
                continue
            if SPLIT_PERSON_SIGNATURE in message.content:
                continue
            if self.dws.is_current_user_message(message):
                return message
        return None

    @staticmethod
    def _message_after_handoff(
        message: DingTalkMessage, handoff_create_time: str | None
    ) -> bool:
        if handoff_create_time is None:
            return False
        return message.create_time > handoff_create_time

    def _process_batch(
        self,
        conversation: DingTalkConversation,
        new_messages: list[DingTalkMessage],
        context_messages: list[DingTalkMessage],
        *,
        ignore_existing_attempt: bool = False,
    ) -> None:
        trigger = new_messages[-1]
        if not ignore_existing_attempt and self._handle_existing_attempt(
            conversation, trigger, new_messages
        ):
            return
        try:
            linked_documents = self._read_linked_documents(new_messages, context_messages)
        except Exception as exc:
            self._record_linked_document_error(conversation, trigger, str(exc))
            return
        session_id = None
        if not ignore_existing_attempt:
            session_id = self.store.get_codex_session_id(conversation.open_conversation_id)
        prompt = self._build_prompt(
            conversation,
            new_messages,
            context_messages,
            include_thread_prompt=session_id is None,
            linked_documents=linked_documents,
        )
        before_session_id = getattr(self.codex, "last_session_id", None)
        decision = self.codex.decide(prompt=prompt, session_id=session_id)
        if self._is_stale_codex_resume(decision, session_id):
            self.store.clear_codex_session(conversation.open_conversation_id)
            session_id = None
            prompt = self._build_prompt(
                conversation,
                new_messages,
                context_messages,
                include_thread_prompt=True,
                linked_documents=linked_documents,
            )
            before_session_id = getattr(self.codex, "last_session_id", None)
            decision = self.codex.decide(prompt=prompt, session_id=None)
        after_session_id = getattr(self.codex, "last_session_id", None)
        self._persist_codex_session_id(
            conversation,
            before_session_id=before_session_id,
            after_session_id=after_session_id,
        )
        attempt_session_id = after_session_id or session_id or ""
        attempt_id = self.store.record_reply_attempt(
            conversation_id=conversation.open_conversation_id,
            conversation_title=conversation.title,
            trigger_message_id=trigger.open_message_id,
            trigger_sender=trigger.sender_name,
            trigger_text=trigger.content,
            action=decision.action.value,
            sensitivity_kind=decision.sensitivity_kind.value,
            codex_reason=decision.reason,
            draft_reply_text=decision.reply_text,
            codex_session_id=attempt_session_id,
            codex_transcript_start_line=getattr(
                self.codex, "last_transcript_start_line", 0
            ),
            codex_transcript_end_line=getattr(
                self.codex, "last_transcript_end_line", 0
            ),
            audit_documents_json=json.dumps(
                decision.audit_documents,
                ensure_ascii=False,
            ),
            audit_tool_events_json=json.dumps(
                getattr(self.codex, "last_audit_tool_events", []),
                ensure_ascii=False,
            ),
            audit_summary=decision.audit_summary,
        )

        if decision.action == CodexAction.NO_REPLY:
            self.store.update_reply_attempt(
                attempt_id,
                send_status="skipped",
                send_error="no_reply",
            )
            self._mark_seen(new_messages)
            return
        if decision.action == CodexAction.STOP_WITH_ERROR:
            self.store.update_reply_attempt(
                attempt_id,
                send_status="failed",
                send_error=decision.reason,
            )
            self.store.record_error(
                conversation.open_conversation_id,
                trigger.open_message_id,
                "codex",
                decision.reason,
            )
            self._notify(
                title=f"CEO agent error: {conversation.title}",
                message=decision.reason[:120],
            )
            return
        if decision.action == CodexAction.HANDOFF_TO_HUMAN:
            handoff_reply_text = self._format_reply_text(trigger, HANDOFF_ACK, [])
            if self.dry_run:
                self.store.update_reply_attempt(
                    attempt_id,
                    final_reply_text=handoff_reply_text,
                    send_status="dry_run",
                )
                self._notify(
                    title=f"CEO handoff: {conversation.title}",
                    message=trigger.content[:120],
                )
                return
            try:
                self._ding_self(
                    self._handoff_ding_text(
                        conversation=conversation,
                        trigger=trigger,
                        context_messages=context_messages,
                    )
                )
                self._send(conversation.open_conversation_id, handoff_reply_text)
            except Exception as exc:
                self.store.update_reply_attempt(
                    attempt_id,
                    final_reply_text=handoff_reply_text,
                    send_status="failed",
                    send_error=str(exc),
                )
                self.store.record_error(
                    conversation.open_conversation_id,
                    trigger.open_message_id,
                    "handoff_delivery",
                    str(exc),
                )
                self._notify(
                    title=f"CEO handoff failed: {conversation.title}",
                    message=str(exc)[:120],
                )
                return
            self.store.enter_handoff(
                conversation.open_conversation_id,
                trigger.open_message_id,
                decision.reason,
                handoff_message_create_time=trigger.create_time,
            )
            self.store.update_reply_attempt(
                attempt_id,
                final_reply_text=handoff_reply_text,
                send_status="sent",
            )
            self._mark_seen(new_messages)
            self._notify(
                title=f"CEO handoff: {conversation.title}",
                message=trigger.content[:120],
            )
            return

        permission = self.permission_gate.evaluate(decision, trigger)
        self.store.update_reply_attempt(
            attempt_id,
            permission_action=permission.action.value,
            permission_reason=permission.reason,
        )
        if permission.action == PermissionAction.ERROR:
            self.store.update_reply_attempt(
                attempt_id,
                send_status="failed",
                send_error=permission.reason,
            )
            self.store.record_error(
                conversation.open_conversation_id,
                trigger.open_message_id,
                "permission",
                permission.reason,
            )
            self._notify(
                title=f"CEO permission error: {conversation.title}",
                message=permission.reason[:120],
            )
            return
        if permission.action == PermissionAction.REPLY:
            self._send_reply(
                conversation=conversation,
                trigger=trigger,
                new_messages=new_messages,
                reply_text=permission.reply_text,
                reason=permission.reason,
                attempt_id=attempt_id,
            )
            return

        self._send_reply(
            conversation=conversation,
            trigger=trigger,
            new_messages=new_messages,
            reply_text=decision.reply_text,
            reason=decision.reason,
            attempt_id=attempt_id,
        )

    def _handle_existing_attempt(
        self,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        new_messages: list[DingTalkMessage],
    ) -> bool:
        attempt = self.store.get_latest_reply_attempt_for_trigger(
            conversation.open_conversation_id,
            trigger.open_message_id,
        )
        if attempt is None:
            return False
        if attempt.send_status in {"sent", "skipped", "blocked"}:
            self._mark_seen(new_messages)
            return True
        if attempt.send_status == "dry_run":
            if self.dry_run:
                return True
            return self._retry_existing_reply_attempt(
                conversation,
                trigger,
                new_messages,
                attempt,
            )
        if attempt.send_status in {"failed", "pending"}:
            return self._retry_existing_reply_attempt(
                conversation,
                trigger,
                new_messages,
                attempt,
            )
        return False

    def _read_linked_documents(
        self,
        new_messages: list[DingTalkMessage],
        context_messages: list[DingTalkMessage],
    ) -> list[LinkedDocumentContext]:
        documents: list[LinkedDocumentContext] = []
        referenced_messages = self._referenced_document_messages(
            new_messages, context_messages
        )
        for url in self._dingtalk_doc_urls(referenced_messages):
            payload = self.dws.read_doc(url)
            title = str(payload.get("title") or "钉钉文档")
            markdown = str(payload.get("markdown") or "")
            if not markdown.strip():
                raise DwsError(f"DingTalk doc read returned empty markdown: {url}")
            documents.append(
                LinkedDocumentContext(
                    url=url,
                    title=title,
                    markdown=markdown,
                )
            )
        for file_name in self._referenced_file_names(new_messages, context_messages):
            document = self._read_referenced_file(file_name)
            if document is not None:
                documents.append(document)
        return documents

    @staticmethod
    def _referenced_document_messages(
        new_messages: list[DingTalkMessage],
        context_messages: list[DingTalkMessage],
    ) -> list[DingTalkMessage]:
        result: list[DingTalkMessage] = []
        seen_message_ids: set[str] = set()
        context_by_message_id = {
            message.open_message_id: message for message in context_messages
        }
        for message in new_messages:
            if message.open_message_id not in seen_message_ids:
                result.append(message)
                seen_message_ids.add(message.open_message_id)
            if (
                message.quoted_message_id
                and message.quoted_message_id in context_by_message_id
                and message.quoted_message_id not in seen_message_ids
            ):
                quoted = context_by_message_id[message.quoted_message_id]
                result.append(quoted)
                seen_message_ids.add(quoted.open_message_id)
        return result

    @classmethod
    def _referenced_file_names(
        cls,
        new_messages: list[DingTalkMessage],
        context_messages: list[DingTalkMessage],
    ) -> list[str]:
        names: list[str] = []
        seen_names: set[str] = set()

        def add_from_text(text: str | None) -> None:
            if not text:
                return
            match = FILE_MESSAGE_PATTERN.match(text.strip())
            if not match:
                return
            file_name = match.group("name").strip()
            if file_name and file_name not in seen_names:
                seen_names.add(file_name)
                names.append(file_name)

        context_by_message_id = {
            message.open_message_id: message for message in context_messages
        }
        trigger = new_messages[-1] if new_messages else None
        for message in new_messages:
            add_from_text(message.content)
            add_from_text(message.quoted_content)
            if message.quoted_message_id and message.quoted_message_id in context_by_message_id:
                add_from_text(context_by_message_id[message.quoted_message_id].content)

        if trigger is None:
            return names

        trigger_time = datetime.strptime(trigger.create_time, DINGTALK_TIME_FORMAT)
        window_start = trigger_time - REFERENCED_FILE_CONTEXT_WINDOW
        for message in context_messages:
            if message.sender_name != trigger.sender_name:
                continue
            try:
                message_time = datetime.strptime(message.create_time, DINGTALK_TIME_FORMAT)
            except ValueError:
                continue
            if window_start <= message_time <= trigger_time:
                add_from_text(message.content)
        return names

    def _read_referenced_file(self, file_name: str) -> LinkedDocumentContext | None:
        matches = self._matching_document_search_results(
            file_name,
            self.dws.search_documents(file_name, page_size=5),
        )
        if not matches:
            return LinkedDocumentContext(
                url="",
                title=file_name,
                markdown="钉钉文件消息已在上下文中出现，但没有搜索到可访问的文件正文。",
            )
        if len(matches) > 1:
            titles = ", ".join(self._document_display_name(match) for match in matches)
            return LinkedDocumentContext(
                url="",
                title=file_name,
                markdown=f"钉钉文件消息已在上下文中出现，但同名可访问文件不唯一：{titles}。",
            )
        match = matches[0]
        if match.content_type.upper() == "ALIDOC" and match.extension.lower() == "adoc":
            payload = self.dws.read_doc(match.node_id)
            markdown = str(payload.get("markdown") or "")
            if markdown.strip():
                return LinkedDocumentContext(
                    url=match.doc_url,
                    title=str(payload.get("title") or self._document_display_name(match)),
                    markdown=markdown,
                )
        payload = self.dws.download_doc(match.node_id)
        markdown = self._downloaded_file_markdown(match, payload)
        if markdown.strip():
            return LinkedDocumentContext(
                url=match.doc_url,
                title=self._document_display_name(match) or file_name,
                markdown=markdown,
            )
        return LinkedDocumentContext(
            url=match.doc_url,
            title=self._document_display_name(match) or file_name,
            markdown=(
                "钉钉普通文件已定位，但正文未能读取。"
                f"node_id: {match.node_id}\n"
                f"extension: {match.extension or 'unknown'}\n"
                f"content_type: {match.content_type or 'unknown'}\n"
                "如果新消息要求对文件内容 comments、审核、总结或判断，不能只凭文件名回复。"
            ),
        )

    def _downloaded_file_markdown(
        self, match: DwsDocumentSearchResult, payload: dict
    ) -> str:
        for key in ("markdown", "text", "content"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value

        resource_url = str(payload.get("resourceUrl") or "")
        if not resource_url:
            return ""
        data = self._download_resource_bytes(resource_url, payload.get("headers"))
        extension = match.extension.lower()
        if extension in {"txt", "md", "markdown", "csv", "json"}:
            return self._decode_text_file(data)
        if extension == "pdf":
            return self._extract_pdf_text(data)
        if extension == "docx":
            return self._extract_docx_text(data)
        return ""

    @staticmethod
    def _download_resource_bytes(url: str, headers: object) -> bytes:
        normalized_headers = headers if isinstance(headers, dict) else {}
        request = urllib.request.Request(
            url,
            headers={str(key): str(value) for key, value in normalized_headers.items()},
        )
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > DOWNLOADED_FILE_MAX_BYTES:
                raise DwsError("dingtalk_file_too_large")
            data = response.read(DOWNLOADED_FILE_MAX_BYTES + 1)
        if len(data) > DOWNLOADED_FILE_MAX_BYTES:
            raise DwsError("dingtalk_file_too_large")
        return data

    @staticmethod
    def _decode_text_file(data: bytes) -> str:
        for encoding in ("utf-8", "utf-8-sig", "gb18030"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="replace")

    @classmethod
    def _extract_pdf_text(cls, data: bytes) -> str:
        reader = PdfReader(BytesIO(data))
        chunks: list[str] = []
        for page_number, page in enumerate(reader.pages[:PDF_TEXT_PAGE_LIMIT], start=1):
            text = (page.extract_text() or "").strip()
            if text:
                chunks.append(f"第 {page_number} 页:\n{text}")
        if len(reader.pages) > PDF_TEXT_PAGE_LIMIT:
            chunks.append(f"[PDF 超过 {PDF_TEXT_PAGE_LIMIT} 页，后续页面未预读]")
        return "\n\n".join(chunks)

    @staticmethod
    def _extract_docx_text(data: bytes) -> str:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", errors="replace")
        text = re.sub(r"<w:tab[^>]*/>", "\t", xml)
        text = re.sub(r"</w:p>", "\n", text)
        text = re.sub(r"<[^>]+>", "", text)
        return text

    @classmethod
    def _matching_document_search_results(
        cls,
        file_name: str,
        results: list[DwsDocumentSearchResult],
    ) -> list[DwsDocumentSearchResult]:
        expected = cls._normalized_document_name(file_name)
        matches = []
        for result in results:
            candidates = {
                cls._normalized_document_name(result.name),
                cls._normalized_document_name(cls._document_display_name(result)),
            }
            if expected in candidates:
                matches.append(result)
        return matches

    @staticmethod
    def _document_display_name(result: DwsDocumentSearchResult) -> str:
        if not result.extension:
            return result.name
        suffix = f".{result.extension.lstrip('.')}"
        if result.name.endswith(suffix):
            return result.name
        return f"{result.name}{suffix}"

    @staticmethod
    def _normalized_document_name(value: str) -> str:
        return " ".join(value.strip().split()).casefold()

    @classmethod
    def _dingtalk_doc_urls(cls, messages: list[DingTalkMessage]) -> list[str]:
        urls: list[str] = []
        seen: set[str] = set()
        for message in messages:
            for text in (message.content, message.quoted_content or ""):
                for match in DINGTALK_DOC_URL_PATTERN.finditer(text):
                    url = cls._canonical_doc_url(match.group(0))
                    if url in seen:
                        continue
                    seen.add(url)
                    urls.append(url)
        return urls

    @staticmethod
    def _canonical_doc_url(url: str) -> str:
        parts = urlsplit(url.rstrip(".,;，。；"))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

    def _record_linked_document_error(
        self,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        error: str,
    ) -> None:
        reason = f"linked_dingtalk_doc_read_failed: {error}"
        attempt_id = self.store.record_reply_attempt(
            conversation_id=conversation.open_conversation_id,
            conversation_title=conversation.title,
            trigger_message_id=trigger.open_message_id,
            trigger_sender=trigger.sender_name,
            trigger_text=trigger.content,
            action=CodexAction.STOP_WITH_ERROR.value,
            sensitivity_kind="general",
            codex_reason=reason,
            audit_summary="新消息包含钉钉文档链接，但读取文档正文失败；按照规则不生成回复。",
        )
        self.store.update_reply_attempt(
            attempt_id,
            send_status="failed",
            send_error=reason,
        )
        self.store.record_error(
            conversation.open_conversation_id,
            trigger.open_message_id,
            "linked_dingtalk_doc_read",
            error,
        )
        self._notify(
            title=f"CEO doc read failed: {conversation.title}",
            message=error[:120],
        )

    def _retry_existing_reply_attempt(
        self,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        new_messages: list[DingTalkMessage],
        attempt: ReplyAttempt,
    ) -> bool:
        if attempt.action not in {
            CodexAction.SEND_REPLY.value,
            CodexAction.ASK_CLARIFYING_QUESTION.value,
        }:
            return False
        if not attempt.final_reply_text.strip():
            return False
        try:
            at_users = self._reply_at_users(trigger)
        except Exception as exc:
            self.store.update_reply_attempt(
                attempt.id,
                send_status="failed",
                send_error=str(exc),
            )
            self.store.record_error(
                conversation.open_conversation_id,
                trigger.open_message_id,
                "reply_at_users",
                str(exc),
            )
            self._notify(
                title=f"CEO reply recipient failed: {conversation.title}",
                message=str(exc)[:120],
            )
            return True
        direct_user_id = at_users[0] if conversation.single_chat and at_users else None
        send_at_users = [] if conversation.single_chat else at_users
        self._deliver_final_reply(
            conversation=conversation,
            trigger=trigger,
            new_messages=new_messages,
            attempt_id=attempt.id,
            final_reply_text=attempt.final_reply_text,
            at_users=send_at_users,
            direct_user_id=direct_user_id,
            direct_open_dingtalk_id=trigger.sender_open_dingtalk_id
            if conversation.single_chat
            else None,
        )
        return True

    def _handoff_ding_text(
        self,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        context_messages: list[DingTalkMessage],
    ) -> str:
        previous_split_reply = self._previous_split_person_reply(context_messages, trigger)
        return (
            f"{conversation.title}\n"
            f"{trigger.sender_name}: {trigger.content[:300]}\n"
            f"previous split-person reply: {previous_split_reply}"
        )

    def _previous_split_person_reply(
        self,
        context_messages: list[DingTalkMessage],
        trigger: DingTalkMessage,
    ) -> str:
        for message in reversed(context_messages):
            if message.open_message_id == trigger.open_message_id:
                continue
            if message.create_time > trigger.create_time:
                continue
            if SPLIT_PERSON_SIGNATURE in message.content:
                return message.content[:300]
        return "none"

    def _send_reply(
        self,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        new_messages: list[DingTalkMessage],
        reply_text: str,
        reason: str,
        attempt_id: int,
    ) -> None:
        if not reply_text.strip():
            self.store.update_reply_attempt(
                attempt_id,
                send_status="blocked",
                send_error=f"empty_reply: {reason}",
            )
            self.store.record_error(
                conversation.open_conversation_id,
                trigger.open_message_id,
                "empty_reply",
                reason,
            )
            self._notify(
                title=f"CEO agent empty reply: {conversation.title}",
                message=reason[:120],
            )
            return
        try:
            at_users = self._reply_at_users(trigger)
        except Exception as exc:
            self.store.update_reply_attempt(
                attempt_id,
                send_status="failed",
                send_error=str(exc),
            )
            self.store.record_error(
                conversation.open_conversation_id,
                trigger.open_message_id,
                "reply_at_users",
                str(exc),
            )
            self._notify(
                title=f"CEO reply recipient failed: {conversation.title}",
                message=str(exc)[:120],
            )
            return
        direct_user_id = at_users[0] if conversation.single_chat and at_users else None
        send_at_users = [] if conversation.single_chat else at_users
        reply_text = append_signature(reply_text)
        reply_text = self._format_reply_text(
            trigger,
            reply_text,
            send_at_users,
        )
        self._deliver_final_reply(
            conversation=conversation,
            trigger=trigger,
            new_messages=new_messages,
            attempt_id=attempt_id,
            final_reply_text=reply_text,
            at_users=send_at_users,
            direct_user_id=direct_user_id,
            direct_open_dingtalk_id=trigger.sender_open_dingtalk_id
            if conversation.single_chat
            else None,
        )

    def _deliver_final_reply(
        self,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        new_messages: list[DingTalkMessage],
        attempt_id: int,
        final_reply_text: str,
        at_users: list[str],
        direct_user_id: str | None,
        direct_open_dingtalk_id: str | None,
    ) -> None:
        reply_text = final_reply_text
        self.store.update_reply_attempt(
            attempt_id,
            final_reply_text=reply_text,
            direct_user_id=direct_user_id or "",
            direct_open_dingtalk_id=direct_open_dingtalk_id or "",
        )
        if contains_forbidden_leak(reply_text):
            self.store.update_reply_attempt(
                attempt_id,
                send_status="blocked",
                send_error="leak_check",
            )
            self.store.record_error(
                conversation.open_conversation_id,
                trigger.open_message_id,
                "leak_check",
                reply_text,
            )
            self._notify(
                title=f"CEO agent blocked leak: {conversation.title}",
                message=reply_text[:120],
            )
            return

        self._notify(title=f"CEO auto reply: {conversation.title}", message=reply_text)
        if self.dry_run:
            self.store.update_reply_attempt(
                attempt_id,
                send_status="dry_run",
            )
            return
        try:
            send_conversation_id = (
                None if conversation.single_chat else conversation.open_conversation_id
            )
            retry_count, send_result = self._send_with_retry(
                send_conversation_id,
                reply_text,
                at_users=at_users,
                user_id=direct_user_id,
                open_dingtalk_id=direct_open_dingtalk_id
                if conversation.single_chat and not direct_user_id
                else None,
            )
        except Exception as exc:
            self.store.update_reply_attempt(
                attempt_id,
                send_status="failed",
                send_error=str(exc),
                retry_count=0
                if getattr(exc, "needs_authorization", False)
                else max(self.send_attempts - 1, 0),
            )
            self.store.record_error(
                conversation.open_conversation_id,
                trigger.open_message_id,
                "send",
                str(exc),
            )
            self._notify(
                title=f"CEO auto reply failed: {conversation.title}",
                message=str(exc)[:120],
            )
            return
        self.store.update_reply_attempt(
            attempt_id,
            send_status="sent",
            retry_count=retry_count,
        )
        self.store.record_sent_reply(
            conversation.open_conversation_id,
            trigger.open_message_id,
            reply_text,
            send_result_json=json.dumps(send_result or {}, ensure_ascii=False),
            recall_key=DwsClient.extract_recall_key(send_result),
        )
        self._mark_seen(new_messages)

    def _send(
        self,
        conversation_id: str | None,
        text: str,
        at_users: list[str] | None = None,
        user_id: str | None = None,
        open_dingtalk_id: str | None = None,
    ):
        if self.dry_run:
            return None
        return self.dws.send_message(
            conversation_id,
            text,
            at_users=at_users,
            user_id=user_id,
            open_dingtalk_id=open_dingtalk_id,
        )

    def _send_with_retry(
        self,
        conversation_id: str | None,
        text: str,
        at_users: list[str] | None = None,
        user_id: str | None = None,
        open_dingtalk_id: str | None = None,
    ) -> tuple[int, dict | None]:
        errors: list[str] = []
        for attempt_number in range(1, self.send_attempts + 1):
            try:
                send_result = self._send(
                    conversation_id,
                    text,
                    at_users=at_users,
                    user_id=user_id,
                    open_dingtalk_id=open_dingtalk_id,
                )
                return attempt_number - 1, send_result
            except Exception as exc:
                if getattr(exc, "needs_authorization", False):
                    raise exc
                errors.append(f"attempt {attempt_number}: {exc}")
        raise RuntimeError(" | ".join(errors))

    def _ding_self(self, text: str) -> None:
        if self.dry_run:
            return
        self.dws.ding_self(text)

    def _reply_at_users(self, trigger: DingTalkMessage) -> list[str]:
        current_user_id = self.store.get_current_user_id()
        sender_user_id = trigger.sender_user_id or self.dws.resolve_message_sender(trigger)
        users: list[str] = []
        for user_id in [sender_user_id, *trigger.mentioned_user_ids]:
            if not user_id:
                continue
            if current_user_id and user_id == current_user_id:
                continue
            if user_id not in users:
                users.append(user_id)
        return users

    @staticmethod
    def _format_reply_text(
        trigger: DingTalkMessage, reply_text: str, at_users: list[str]
    ) -> str:
        quote = DingTalkAutoReplyWorker._fake_quote(trigger)
        placeholders = " ".join(f"<@{user_id}>" for user_id in at_users)
        if placeholders:
            return f"{quote}\n\n{placeholders} {reply_text}"
        return f"{quote}\n\n{reply_text}"

    @staticmethod
    def _fake_quote(trigger: DingTalkMessage) -> str:
        normalized = DingTalkAutoReplyWorker._quote_source_text(trigger.content)
        excerpt = DingTalkAutoReplyWorker._truncate_quote_text(
            normalized,
            unit_limit=QUOTE_INFORMATION_UNIT_LIMIT,
        )
        return f"> {trigger.sender_name}: {excerpt}"

    @staticmethod
    def _quote_source_text(text: str) -> str:
        without_links = MEDIA_OR_LINK_PATTERN.sub(" ", text)
        without_mentions = QUOTE_MENTION_PATTERN.sub(" ", without_links)
        normalized = " ".join(without_mentions.split())
        return normalized or "原消息"

    @staticmethod
    def _truncate_quote_text(text: str, unit_limit: int) -> str:
        matches = list(QUOTE_WORD_OR_CJK_PATTERN.finditer(text))
        if len(matches) <= unit_limit:
            return text
        end_index = matches[unit_limit - 1].end()
        return f"{text[:end_index].rstrip()}..."

    def _notify(self, title: str, message: str) -> None:
        send_macos_notification(title=title, message=message, url=None)

    def _mark_seen(self, messages: list[DingTalkMessage]) -> None:
        if self.dry_run:
            return
        for message in messages:
            self.store.mark_seen(message.open_message_id, message.open_conversation_id)

    def _persist_codex_session_id(
        self,
        conversation: DingTalkConversation,
        before_session_id: str | None,
        after_session_id: str | None,
    ) -> None:
        if not after_session_id or after_session_id == before_session_id:
            return
        self.store.upsert_conversation(
            conversation_id=conversation.open_conversation_id,
            title=conversation.title,
            single_chat=conversation.single_chat,
            codex_session_id=after_session_id,
        )

    @staticmethod
    def _is_stale_codex_resume(
        decision: CodexDecision, session_id: str | None
    ) -> bool:
        if not session_id or decision.action != CodexAction.STOP_WITH_ERROR:
            return False
        reason = decision.reason
        return (
            "thread/resume failed" in reason
            and "no rollout found for thread id" in reason
        )

    def _build_prompt(
        self,
        conversation: DingTalkConversation,
        new_messages: list[DingTalkMessage],
        context_messages: list[DingTalkMessage],
        *,
        include_thread_prompt: bool = True,
        linked_documents: list[LinkedDocumentContext] | None = None,
    ) -> str:
        return build_turn_prompt(
            conversation,
            new_messages,
            context_messages,
            style_lines=self._style_prompt_lines(
                conversation,
                new_messages,
                context_messages,
            ),
            include_thread_prompt=include_thread_prompt,
            linked_documents=linked_documents,
            known_people_lines=self._known_people_prompt_lines(
                new_messages,
                context_messages,
            ),
        )

    def _known_people_prompt_lines(
        self,
        new_messages: list[DingTalkMessage],
        context_messages: list[DingTalkMessage],
        limit: int = 20,
    ) -> list[str]:
        messages = [*new_messages, *context_messages]
        combined_text = "\n".join(
            part
            for message in messages
            for part in (
                message.sender_name,
                message.content,
                message.quoted_content or "",
            )
        )
        people: dict[str, str] = {}
        for message in messages:
            if message.sender_user_id and message.sender_name.strip():
                people.setdefault(message.sender_user_id, message.sender_name.strip())

        for user_id in self.store.list_org_user_ids():
            if len(people) >= limit:
                break
            profile = self.store.get_org_user_profile(user_id)
            if profile is None or not self._profile_name_matches_text(
                profile.name,
                combined_text,
            ):
                continue
            people.setdefault(profile.user_id, profile.name)

        return [f"- {name}: user_id={user_id}" for user_id, name in people.items()]

    @staticmethod
    def _profile_name_matches_text(name: str, text: str) -> bool:
        normalized_name = name.strip()
        if not normalized_name:
            return False
        if normalized_name in text:
            return True
        if len(normalized_name) >= 3 and normalized_name[1:] in text:
            return True
        return False

    def _style_prompt_lines(
        self,
        conversation: DingTalkConversation,
        new_messages: list[DingTalkMessage],
        context_messages: list[DingTalkMessage],
    ) -> list[str]:
        lines: list[str] = []
        if self.style_profile:
            lines.append(f"{principal_name()} 语气规则:")
            lines.extend(
                line for line in self.style_profile.splitlines() if line.strip()
            )

        examples = retrieve_similar_examples(
            self._style_query(conversation, new_messages, context_messages),
            self.style_records,
            limit=self.style_example_limit,
        )
        if examples:
            lines.append(
                "相似历史回复风格例子（只学习语气、判断顺序和句式结构；不要复用例子里的事实、人名、项目名、客户名、数字或结论；不要引用这些例子）:"
            )
            for index, example in enumerate(examples, start=1):
                lines.append(
                    f"- 例{index}: {self._style_example_text(example.derek_reply)}"
                )
        return lines

    def _style_query(
        self,
        conversation: DingTalkConversation,
        new_messages: list[DingTalkMessage],
        context_messages: list[DingTalkMessage],
    ) -> str:
        query_parts = [conversation.title]
        query_parts.extend(message.content for message in new_messages)
        query_parts.extend(message.content for message in context_messages[-5:])
        return "\n".join(query_parts)

    @staticmethod
    def _style_example_text(text: str, max_characters: int = 120) -> str:
        normalized = " ".join(text.split())
        if len(normalized) <= max_characters:
            return normalized
        return f"{normalized[:max_characters]}..."
