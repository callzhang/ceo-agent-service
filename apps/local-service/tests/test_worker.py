from pathlib import Path

from ceo_agent_service.corpus import CorpusRecord
from ceo_agent_service.dingtalk_models import (
    CodexAction,
    CodexDecision,
    DingTalkConversation,
    DingTalkMessage,
    SensitivityKind,
)
from ceo_agent_service.dws_client import DwsDocumentSearchResult, DwsError
from ceo_agent_service.store import AutoReplyStore
from ceo_agent_service.worker import HANDOFF_ACK, DingTalkAutoReplyWorker


CONTEXT_HEADER = "上下文消息（前 20 条 + 后续到当前）:"


class FakeDws:
    def __init__(
        self,
        conversations: list[DingTalkConversation],
        messages: dict[str, list[DingTalkMessage]],
        unread_messages: dict[str, list[DingTalkMessage]] | None = None,
        read_errors: dict[str, Exception] | None = None,
        send_error: Exception | None = None,
        ding_error: Exception | None = None,
        current_user_error: Exception | None = None,
        send_result: dict | None = None,
    ):
        self.conversations = conversations
        self.messages = messages
        self.unread_messages = unread_messages or messages
        self.read_errors = read_errors or {}
        self.send_error = send_error
        self.ding_error = ding_error
        self.current_user_error = current_user_error
        self.send_result = send_result
        self.docs: dict[str, dict] = {}
        self.document_search_results: dict[str, list[DwsDocumentSearchResult]] = {}
        self.download_docs: dict[str, dict | Exception] = {}
        self.read_doc_calls: list[str] = []
        self.search_document_calls: list[tuple[str, int]] = []
        self.download_doc_calls: list[str] = []
        self.sent: list[tuple[str, str]] = []
        self.sent_at_users: list[list[str]] = []
        self.direct_user_ids: list[str | None] = []
        self.send_attempt_count = 0
        self.dings: list[str] = []
        self.user_departments: dict[str, set[str]] = {}
        self.hr_users: set[str] = set()
        self.manager_chains: dict[str, list[str]] = {}
        self.resolved_senders: dict[str, str] = {}
        self.current_user_id = "derek-user-1"

    def list_unread_conversations(self, count: int) -> list[DingTalkConversation]:
        assert count == 50
        return self.conversations

    def read_recent_messages(
        self, conversation: DingTalkConversation
    ) -> list[DingTalkMessage]:
        if conversation.open_conversation_id in self.read_errors:
            raise self.read_errors[conversation.open_conversation_id]
        return self.messages.get(conversation.open_conversation_id, [])

    def read_unread_messages(
        self, conversation: DingTalkConversation
    ) -> list[DingTalkMessage]:
        return self.unread_messages.get(conversation.open_conversation_id, [])

    def read_doc(self, node: str) -> dict:
        self.read_doc_calls.append(node)
        if node not in self.docs:
            raise DwsError(f"doc not found: {node}")
        return self.docs[node]

    def search_documents(
        self, query: str, page_size: int = 5
    ) -> list[DwsDocumentSearchResult]:
        self.search_document_calls.append((query, page_size))
        return self.document_search_results.get(query, [])

    def download_doc(self, node: str) -> dict:
        self.download_doc_calls.append(node)
        result = self.download_docs.get(node)
        if isinstance(result, Exception):
            raise result
        return result or {}

    def send_message(
        self,
        conversation_id: str | None,
        text: str,
        at_users: list[str] | None = None,
        user_id: str | None = None,
        open_dingtalk_id: str | None = None,
    ) -> None:
        self.send_attempt_count += 1
        if self.send_error:
            raise self.send_error
        self.sent.append((conversation_id or "", text))
        self.sent_at_users.append(at_users or [])
        self.direct_user_ids.append(user_id)
        return self.send_result

    def ding_self(self, text: str) -> None:
        if self.ding_error:
            raise self.ding_error
        self.dings.append(text)

    def resolve_message_sender(self, message: DingTalkMessage) -> str:
        if message.sender_user_id:
            return message.sender_user_id
        if message.sender_open_dingtalk_id in self.resolved_senders:
            return self.resolved_senders[message.sender_open_dingtalk_id]
        raise RuntimeError("sender not resolved")

    def is_hr_user(self, user_id: str) -> bool:
        return user_id in self.hr_users

    def user_in_manager_chain(self, manager_user_id: str, subject_user_id: str) -> bool:
        return manager_user_id in self.manager_chains.get(subject_user_id, [])

    def get_user_department_ids(self, user_id: str) -> set[str]:
        if user_id not in self.user_departments:
            raise RuntimeError("department not resolved")
        return self.user_departments[user_id]

    def is_current_user_message(self, message: DingTalkMessage) -> bool:
        if self.current_user_error:
            raise self.current_user_error
        return message.sender_user_id == self.current_user_id


class FakeCodex:
    def __init__(
        self,
        decision: CodexDecision,
        last_session_id: str | None = None,
        next_session_id: str | None = None,
        audit_tool_events: list[dict[str, str]] | None = None,
        transcript_start_line: int = 0,
        transcript_end_line: int = 0,
    ):
        self.decision = decision
        self.last_session_id = last_session_id
        self.next_session_id = next_session_id
        self.last_audit_tool_events = audit_tool_events or []
        self.last_transcript_start_line = transcript_start_line
        self.last_transcript_end_line = transcript_end_line
        self.calls: list[tuple[str, str | None]] = []

    def decide(self, prompt: str, session_id: str | None) -> CodexDecision:
        self.calls.append((prompt, session_id))
        if self.next_session_id is not None:
            self.last_session_id = self.next_session_id
        return self.decision


def conversation(single_chat: bool = False) -> DingTalkConversation:
    return DingTalkConversation(
        open_conversation_id="cid-1",
        title="Friday",
        single_chat=single_chat,
        unread_point=1,
    )


def message(
    content: str,
    message_id: str = "msg-1",
    single_chat: bool = False,
    quoted_content: str | None = None,
    sender_user_id: str | None = "sender-user-1",
    message_type: str | None = None,
) -> DingTalkMessage:
    return DingTalkMessage(
        open_conversation_id="cid-1",
        open_message_id=message_id,
        conversation_title="Friday",
        single_chat=single_chat,
        sender_name="周俊杰",
        sender_open_dingtalk_id="sender-1",
        sender_user_id=sender_user_id,
        message_type=message_type,
        create_time="2026-05-13 18:00:00",
        content=content,
        quoted_message_id="quoted-1" if quoted_content else None,
        quoted_content=quoted_content,
    )


def derek_message(
    content: str,
    message_id: str = "derek-msg-1",
    create_time: str = "2026-05-13 18:00:01",
) -> DingTalkMessage:
    msg = message(
        content=content,
        message_id=message_id,
        sender_user_id="derek-user-1",
    )
    msg.create_time = create_time
    return msg


def test_handoff_does_not_clear_on_current_user_message_before_trigger(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_conversation("cid-1", "Friday", False, None)
    store.enter_handoff(
        "cid-1",
        "trigger-msg-1",
        "需要真人",
        handoff_message_create_time="2026-05-13 18:00:00",
    )
    monkeypatch.setattr("ceo_agent_service.worker.send_macos_notification", lambda **_: None)
    old_message = derek_message("之前我已经回复过", message_id="old-derek-msg-1")
    old_message.create_time = "2026-05-13 17:59:59"
    dws = FakeDws([conversation()], {"cid-1": [old_message]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=codex)

    worker.run_once()

    assert store.is_in_handoff("cid-1") is True
    assert codex.calls == []
    assert dws.sent == []
    assert store.has_seen("old-derek-msg-1") is True


def make_worker(
    tmp_path: Path,
    dws: FakeDws,
    codex: FakeCodex,
    monkeypatch,
    style_profile: str = "",
    style_records: list[CorpusRecord] | None = None,
    dry_run: bool = False,
) -> DingTalkAutoReplyWorker:
    monkeypatch.setattr("ceo_agent_service.worker.send_macos_notification", lambda **_: None)
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.set_current_user_id("derek-user-1")
    return DingTalkAutoReplyWorker(
        store=store,
        dws=dws,
        codex=codex,
        dry_run=dry_run,
        style_profile=style_profile,
        style_records=style_records,
    )


def test_group_without_derek_mention_does_not_call_codex_or_send(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws([conversation()], {"cid-1": [message("同步一下进展")]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="收到")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert dws.sent == []


def test_unresolvable_non_candidate_sender_does_not_block_conversation(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("OA审批通知", sender_user_id=None)]},
        current_user_error=RuntimeError("sender not resolved"),
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert dws.sent == []
    assert worker.store.count_errors() == 0


def test_single_chat_rendered_schedule_is_skipped_before_codex(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.HANDOFF_TO_HUMAN, reason="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert dws.sent == []
    assert dws.dings == []
    assert worker.store.has_seen("msg-1") is True
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "no_reply"
    assert attempt.send_status == "skipped"
    assert attempt.codex_reason == "system_or_notification_message"


def test_non_text_message_type_is_skipped_before_codex(
    tmp_path: Path, monkeypatch
):
    trigger = message("日程卡片", single_chat=True, message_type="calendar")
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert dws.sent == []
    assert worker.store.get_reply_attempt(1).action == "no_reply"


def test_structured_link_card_is_skipped_before_codex(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "\n".join(
            [
                "表单标题",
                "字段一: A",
                "字段二: B",
                "字段三: C",
                "字段四: D",
                "[dingtalk://dingtalkclient/action/open_platform_link?x=1](dingtalk://dingtalkclient/action/open_platform_link?x=1)",
            ]
        ),
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert dws.sent == []
    assert worker.store.get_reply_attempt(1).action == "no_reply"


def test_question_with_link_still_goes_to_codex(tmp_path: Path, monkeypatch):
    trigger = message("这个链接里的方案怎么看？ https://example.com/a", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="test",
            audit_summary="只需上下文判断，不需要回复。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert len(codex.calls) == 1


def test_bare_link_is_skipped_before_codex(tmp_path: Path, monkeypatch):
    trigger = message("@磊哥 https://example.com/a", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert dws.sent == []
    assert worker.store.get_reply_attempt(1).action == "no_reply"


def test_group_mention_sends_signed_reply(tmp_path: Path, monkeypatch):
    trigger = message(
        "@Derek Zen(磊哥) @晓民 这个怎么处理？",
        quoted_content="这个ACL表看一下",
    )
    trigger.mentioned_user_ids = ["derek-user-1", "mentioned-user-1"]
    dws = FakeDws(
        [conversation()],
        {
            "cid-1": [
                trigger,
                message("前面上下文", message_id="msg-0"),
            ]
        },
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先按A方案走")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert dws.sent == [
        (
            "cid-1",
            "> 周俊杰: 这个怎么处理？\n\n"
            "<@sender-user-1> <@mentioned-user-1> 先按A方案走（by磊哥分身）",
        )
    ]
    assert dws.sent_at_users == [["sender-user-1", "mentioned-user-1"]]
    assert len(codex.calls) == 1
    prompt = codex.calls[0][0]
    assert "CEO Agent Prompt" in prompt
    assert "你是 Derek 的钉钉自动回复分身" in prompt
    assert "先判断是否需要回复" in prompt
    assert "系统类信息、机器人通知、审批/OA/日程/文件状态/自动同步等通知性消息" in prompt
    assert "只记录 no_reply，不要代表 Derek 回复" in prompt
    assert "回答任何问题前，先检索本地 workspace" in prompt
    assert "graphify-out/GRAPH_REPORT.md" in prompt
    assert "graphify query" in prompt
    assert "只回答“新消息”提出的问题" in prompt
    assert "不要断言已完成或未完成" in prompt
    assert "不能代 Derek 声称他正在、即将或已经执行现实动作" in prompt
    assert "应 handoff_to_human" in prompt
    assert "先让对方把需要审核的文件或链接发出来" in prompt
    assert "最终定稿或确认必须说明还需要" in prompt
    assert "本人确认" in prompt
    assert "必须先检索 workspace 里的岗位要求" in prompt
    assert "查看上下文提到的简历文件或链接内容" in prompt
    assert "不能凭一句消息下结论" in prompt
    assert "必须输出 audit_documents 和 audit_summary" in prompt
    assert "不要输出逐字思维链" in prompt
    assert "send_reply、ask_clarifying_question、handoff_to_human、no_reply 或 stop_with_error" in prompt
    assert "会话: Friday" in prompt
    assert "@Derek Zen(磊哥) @晓民 这个怎么处理？" in prompt
    assert "引用: 这个ACL表看一下" in prompt
    assert "前面上下文" in prompt


def test_success_notification_keeps_full_reply_text(tmp_path: Path, monkeypatch):
    trigger = message("@Derek Zen(磊哥) 请给一下你的看法")
    trigger.mentioned_user_ids = ["derek-user-1"]
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    reply_body = "我倾向于按这个方向收敛：" + "先看行业经验和交付闭环，" * 12
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text=reply_body)
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    notifications: list[dict[str, str | None]] = []
    monkeypatch.setattr(
        "ceo_agent_service.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    worker.run_once()

    assert len(notifications[0]["message"]) > 120
    assert notifications == [
        {
            "title": "CEO auto reply: Friday",
            "message": dws.sent[0][1],
            "url": None,
        }
    ]


def test_dingtalk_doc_link_is_read_before_codex(tmp_path: Path, monkeypatch):
    doc_url = "https://alidocs.dingtalk.com/i/nodes/doc123?utm_source=im"
    canonical_doc_url = "https://alidocs.dingtalk.com/i/nodes/doc123"
    trigger = message(f"{doc_url} @Derek Zen(磊哥) 看下根因和解法")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.docs[canonical_doc_url] = {
        "title": "数据导入导出业务低效根因和最终解法",
        "markdown": (
            "核心结论：根因是协作方式不对。\n"
            "客户业务逻辑、项目配置确认、数据工具排查被混在一起。"
        ),
    }
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="按协作方式拆分")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    worker.run_once()

    assert dws.read_doc_calls == [canonical_doc_url]
    assert dws.sent == []
    prompt = codex.calls[0][0]
    assert "已获取的钉钉材料:" in prompt
    assert "数据导入导出业务低效根因和最终解法" in prompt
    assert "根因是协作方式不对" in prompt
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "dry_run"


def test_dingtalk_doc_link_in_context_is_read_before_codex(tmp_path: Path, monkeypatch):
    doc_url = "https://alidocs.dingtalk.com/i/nodes/doc-in-context?utm_source=im"
    canonical_doc_url = "https://alidocs.dingtalk.com/i/nodes/doc-in-context"
    context_doc = message(
        f"[文档] 方案: {doc_url}",
        message_id="doc-msg-1",
    )
    trigger = message(
        "@Derek Zen(磊哥) 磊哥comments一下",
        message_id="msg-2",
        quoted_content=f"[文档] 方案: {doc_url}",
    )
    dws = FakeDws([conversation()], {"cid-1": [context_doc, trigger]})
    dws.docs[canonical_doc_url] = {
        "title": "推进方案",
        "markdown": "下一步建议：先做客户需求收敛，再做交付排期。",
    }
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先收敛需求")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    worker.run_once()

    assert dws.read_doc_calls == [canonical_doc_url]
    prompt = codex.calls[0][0]
    assert "已获取的钉钉材料:" in prompt
    assert "下一步建议：先做客户需求收敛" in prompt


def test_referenced_file_message_is_located_before_codex(tmp_path: Path, monkeypatch):
    file_message = message(
        "[文件] 02_下一步推进建议.md",
        message_id="file-msg-1",
    )
    trigger = message(
        "@Derek Zen(磊哥) 磊哥comments一下",
        message_id="msg-2",
        quoted_content="[文件] 02_下一步推进建议.md",
    )
    trigger.quoted_message_id = "file-msg-1"
    dws = FakeDws([conversation()], {"cid-1": [file_message, trigger]})
    dws.document_search_results["02_下一步推进建议.md"] = [
        DwsDocumentSearchResult(
            node_id="node-1",
            name="02_下一步推进建议",
            extension="md",
            content_type="OTHER",
            node_type="file",
            doc_url="https://alidocs.dingtalk.com/i/nodes/node-1",
        )
    ]
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="建议补边界和owner")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    worker.run_once()

    assert dws.search_document_calls == [("02_下一步推进建议.md", 5)]
    assert dws.download_doc_calls == []
    prompt = codex.calls[0][0]
    assert "已获取的钉钉材料:" in prompt
    assert "钉钉普通文件已定位，但尚未下载正文" in prompt
    assert "node_id: node-1" in prompt
    assert "extension: md" in prompt
    assert 'dws doc download --node "node-1" --format json' in prompt
    assert "不要只凭文件名回复" in prompt


def test_referenced_file_metadata_does_not_expose_download_credentials(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "@Derek Zen(磊哥) 磊哥comments一下",
        quoted_content="[文件] 02_下一步推进建议.md",
    )
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.document_search_results["02_下一步推进建议.md"] = [
        DwsDocumentSearchResult(
            node_id="node-1",
            name="02_下一步推进建议",
            extension="md",
            content_type="OTHER",
            node_type="file",
            doc_url="https://alidocs.dingtalk.com/i/nodes/node-1",
        )
    ]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.ASK_CLARIFYING_QUESTION,
            reply_text="我现在只能看到文件名，麻烦贴一下正文。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    worker.run_once()

    prompt = codex.calls[0][0]
    assert dws.download_doc_calls == []
    assert "钉钉普通文件已定位，但尚未下载正文" in prompt
    assert "node_id: node-1" in prompt
    assert "authorizationUrl" not in prompt


def test_dingtalk_doc_read_failure_blocks_codex(tmp_path: Path, monkeypatch):
    trigger = message(
        "https://alidocs.dingtalk.com/i/nodes/missing @Derek Zen(磊哥) 看下"
    )
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert dws.read_doc_calls == ["https://alidocs.dingtalk.com/i/nodes/missing"]
    assert codex.calls == []
    assert dws.sent == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "stop_with_error"
    assert attempt.send_status == "failed"
    assert "linked_dingtalk_doc_read_failed" in attempt.send_error


def test_codex_stop_with_error_sends_macos_notification(tmp_path: Path, monkeypatch):
    trigger = message("@Derek Zen(磊哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.STOP_WITH_ERROR,
            reason="codex exec failed",
            macos_notify=False,
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)
    notifications: list[dict[str, str | None]] = []
    monkeypatch.setattr(
        "ceo_agent_service.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    worker.run_once()

    assert dws.sent == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "stop_with_error"
    assert attempt.send_status == "failed"
    assert notifications == [
        {
            "title": "CEO agent error: Friday",
            "message": "codex exec failed",
            "url": None,
        }
    ]


def test_long_trigger_quote_is_capped_by_twenty_information_units(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "@Derek Zen(磊哥) 如果是私有化的POC都是走产研评估流程的，如果是VOC需求也都是PRD评审后走我们正常Sprint迭代流程的",
    )
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="流程方向没问题")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    sent_text = dws.sent[0][1]
    quote, reply = sent_text.split("\n\n", 1)
    assert quote == "> 周俊杰: 如果是私有化的POC都是走产研评估流程的，如果..."
    assert reply == "<@sender-user-1> 流程方向没问题（by磊哥分身）"


def test_resume_prompt_only_includes_turn_message_without_repeating_thread_prompt(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Derek Zen(磊哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="handled"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.upsert_conversation(
        "cid-1",
        title="Friday",
        single_chat=False,
        codex_session_id="session-1",
    )

    worker.run_once()

    prompt, session_id = codex.calls[0]
    assert session_id == "session-1"
    assert "当前待处理消息" in prompt
    assert "CEO Agent Prompt" not in prompt
    assert "你是 Derek 的钉钉自动回复分身" not in prompt
    assert "回答任何问题前，先检索本地 workspace" not in prompt
    assert "graphify query" not in prompt
    assert "@Derek Zen(磊哥) 这个怎么处理？" in prompt


def test_stale_codex_resume_clears_session_and_retries_with_full_prompt(
    tmp_path: Path, monkeypatch
):
    class SequencedCodex:
        def __init__(self):
            self.calls: list[tuple[str, str | None]] = []
            self.last_session_id: str | None = None
            self.last_audit_tool_events: list[dict[str, str]] = []
            self.last_transcript_start_line = 0
            self.last_transcript_end_line = 0

        def decide(self, prompt: str, session_id: str | None) -> CodexDecision:
            self.calls.append((prompt, session_id))
            self.last_session_id = session_id
            if len(self.calls) == 1:
                return CodexDecision(
                    action=CodexAction.STOP_WITH_ERROR,
                    reason=(
                        "thread/resume failed: no rollout found for thread id "
                        "session-1 (code -32600)"
                    ),
                )
            self.last_session_id = "session-2"
            return CodexDecision(
                action=CodexAction.NO_REPLY,
                reason="already handled",
                audit_summary="只需上下文判断，不需要回复。",
            )

    trigger = message("@Derek Zen(磊哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = SequencedCodex()
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.upsert_conversation(
        "cid-1",
        title="Friday",
        single_chat=False,
        codex_session_id="session-1",
    )

    worker.run_once()

    assert [session_id for _, session_id in codex.calls] == ["session-1", None]
    assert "CEO Agent Prompt" not in codex.calls[0][0]
    assert "CEO Agent Prompt" in codex.calls[1][0]
    assert worker.store.get_codex_session_id("cid-1") == "session-2"
    assert worker.store.count_reply_attempts() == 1
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "no_reply"
    assert "thread/resume failed" not in attempt.codex_reason
    assert worker.store.count_errors() == 0


def test_sent_reply_records_recall_key_from_send_result(tmp_path: Path, monkeypatch):
    trigger = message("@Derek Zen(磊哥) 这个怎么处理？")
    dws = FakeDws(
        [conversation()],
        {"cid-1": [trigger]},
        send_result={"result": {"processQueryKey": "key-1"}},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先按A方案走")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    sent_reply = worker.store.get_sent_reply("cid-1", "msg-1")
    assert sent_reply is not None
    assert sent_reply.recall_key == "key-1"
    assert '"processQueryKey": "key-1"' in sent_reply.send_result_json


def test_existing_dry_run_attempt_does_not_call_codex_again(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Derek Zen(磊哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该重新生成")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)
    attempt_id = worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="周俊杰",
        trigger_text=trigger.content,
        action="send_reply",
        sensitivity_kind="general",
        draft_reply_text="先按A方案走",
        send_status="dry_run",
    )
    worker.store.update_reply_attempt(
        attempt_id,
        final_reply_text="> 周俊杰: @Derek Zen(磊哥) 这个怎么处理？\n\n"
        "<@sender-user-1> 先按A方案走（by磊哥分身）",
    )

    worker.run_once()

    assert codex.calls == []
    assert dws.sent == []
    assert worker.store.count_reply_attempts() == 1


def test_failed_send_retries_existing_final_reply_without_calling_codex(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Derek Zen(磊哥) 这个怎么处理？")
    dws = FakeDws(
        [conversation()],
        {"cid-1": [trigger]},
        send_result={"result": {"processQueryKey": "key-1"}},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该重新生成")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    final_reply = (
        "> 周俊杰: @Derek Zen(磊哥) 这个怎么处理？\n\n"
        "<@sender-user-1> 先按A方案走（by磊哥分身）"
    )
    attempt_id = worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="周俊杰",
        trigger_text=trigger.content,
        action="send_reply",
        sensitivity_kind="general",
        draft_reply_text="先按A方案走",
        send_status="failed",
    )
    worker.store.update_reply_attempt(
        attempt_id,
        final_reply_text=final_reply,
        send_error="network",
    )

    worker.run_once()

    assert codex.calls == []
    assert dws.sent == [("cid-1", final_reply)]
    assert dws.sent_at_users == [["sender-user-1"]]
    attempt = worker.store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.send_status == "sent"
    assert worker.store.get_sent_reply("cid-1", "msg-1") is not None
    assert worker.store.has_seen("msg-1") is True


def test_rerun_message_retries_existing_failed_attempt_without_calling_codex(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Derek Zen(磊哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该重新生成")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    final_reply = (
        "> 周俊杰: @Derek Zen(磊哥) 这个怎么处理？\n\n"
        "<@sender-user-1> 先按A方案走（by磊哥分身）"
    )
    attempt_id = worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="周俊杰",
        trigger_text=trigger.content,
        action="send_reply",
        sensitivity_kind="general",
        send_status="failed",
    )
    worker.store.update_reply_attempt(
        attempt_id,
        final_reply_text=final_reply,
        send_error="network",
    )

    processed = worker.rerun_message(conversation(), "msg-1")

    assert processed == "msg-1"
    assert codex.calls == []
    assert dws.sent == [("cid-1", final_reply)]
    assert worker.store.get_reply_attempt(attempt_id).send_status == "sent"


def test_rerun_message_can_force_new_codex_decision(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Derek Zen(磊哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="改走B方案")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    old_attempt_id = worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="周俊杰",
        trigger_text=trigger.content,
        action="send_reply",
        sensitivity_kind="general",
        send_status="sent",
    )

    worker.rerun_message(conversation(), "msg-1", force_new_decision=True)

    assert len(codex.calls) == 1
    assert worker.store.count_reply_attempts() == 2
    assert worker.store.get_reply_attempt(old_attempt_id).send_status == "sent"
    assert dws.sent == [
        (
            "cid-1",
            "> 周俊杰: 这个怎么处理？\n\n"
            "<@sender-user-1> 改走B方案（by磊哥分身）",
        )
    ]


def test_force_new_rerun_starts_fresh_codex_session(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Derek Zen(磊哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.NO_REPLY),
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.upsert_conversation("cid-1", "Friday", False, "old-session")

    worker.rerun_message(conversation(), "msg-1", force_new_decision=True)

    assert codex.calls[0][1] is None
    assert "你是 Derek 的钉钉自动回复分身" in codex.calls[0][0]


def test_reply_attempt_records_codex_audit_fields(tmp_path: Path, monkeypatch):
    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@Derek Zen(磊哥) 这个候选人是否推进？")]},
    )
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="先补岗位画像和简历再判断",
            audit_documents=[
                {
                    "path": "面试/项目经理/岗位画像.md",
                    "title": "项目经理岗位画像",
                    "relevance": "判断候选人是否匹配",
                }
            ],
            audit_summary="缺少简历内容，因此要求补齐材料后再判断。",
        ),
        audit_tool_events=[
            {
                "tool": "exec_command",
                "command": "rg -n 岗位 /Users/derek/Documents/memory/面试",
            }
        ],
        next_session_id="session-1",
        transcript_start_line=4,
        transcript_end_line=12,
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert "项目经理/岗位画像.md" in attempt.audit_documents_json
    assert "rg -n 岗位" in attempt.audit_tool_events_json
    assert attempt.audit_summary == "缺少简历内容，因此要求补齐材料后再判断。"
    assert attempt.codex_session_id == "session-1"
    assert attempt.codex_transcript_start_line == 4
    assert attempt.codex_transcript_end_line == 12


def test_prompt_includes_style_profile_and_similar_corpus_examples(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@Derek Zen(磊哥) 这个项目排期怎么处理？")]},
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="dry run"))
    style_records = [
        CorpusRecord(
            source_type="dingtalk",
            source_title="Friday",
            timestamp="2026-05-13",
            context="项目排期要不要改",
            derek_reply="先定优先级，再确认谁负责、什么时候交付、怎么验收。",
            message_id="style-1",
            conversation_id="cid-style-1",
            speaker_name="磊哥",
            metadata_json="{}",
        ),
        CorpusRecord(
            source_type="dingtalk",
            source_title="HR",
            timestamp="2026-05-13",
            context="候选人怎么样",
            derek_reply="先看岗位匹配，再看负责范围和是否真正承担过结果。",
            message_id="style-2",
            conversation_id="cid-style-2",
            speaker_name="磊哥",
            metadata_json="{}",
        ),
        CorpusRecord(
            source_type="dingtalk",
            source_title="技术部",
            timestamp="2026-05-13",
            context="项目排期风险",
            derek_reply="先把风险拆成产品、算法和交付三类，每类只留一个负责人和一个截止时间。",
            message_id="style-3",
            conversation_id="cid-style-3",
            speaker_name="磊哥",
            metadata_json="{}",
        ),
    ]
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        style_profile="# Derek Style Profile\n- 先结论，再解释原因。",
        style_records=style_records,
    )

    worker.run_once()

    prompt = codex.calls[0][0]
    assert "Derek 语气规则:" in prompt
    assert "- 先结论，再解释原因。" in prompt
    assert "相似历史回复风格例子" in prompt
    assert "只学习语气、判断顺序和句式结构" in prompt
    assert "不要复用例子里的事实、人名、项目名、客户名、数字或结论" in prompt
    assert "先定优先级，再确认谁负责、什么时候交付、怎么验收。" in prompt
    assert prompt.count("- 例") == 2
    assert "先看岗位匹配" not in prompt
    assert "cid-style-1" not in prompt
    assert "Friday" in prompt


def test_group_name_reference_without_direct_at_does_not_queue(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@张晓民(Xiaomin张晓民) 这个和磊哥预期一致")]},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert dws.sent == []


def test_algorithm_owner_multi_mention_is_framed_as_derek_responsibility(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation()],
        {
            "cid-1": [
                message(
                    "@ET(张毅倜(ET)) @Derek Zen(磊哥) "
                    "aijam是否可以把算法大神们纳入进来？",
                    message_id="msg-algo-owner",
                )
            ]
        },
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="可以，算法这边应该参与")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert dws.sent == [
        (
            "cid-1",
            "> 周俊杰: aijam是否可以把算法大神们纳入进来？\n\n"
            "<@sender-user-1> 可以，算法这边应该参与（by磊哥分身）",
        )
    ]
    prompt = codex.calls[0][0]
    assert "Derek 的组织职责包括算法负责人" in prompt
    assert "即使同时 @ 了别人，也应视为需要 Derek 回复" in prompt


def test_group_direct_mention_found_in_recent_context_is_queued(
    tmp_path: Path, monkeypatch
):
    old_direct_mention = message(
        "@Derek Zen(磊哥) 旧消息看一下",
        message_id="msg-old",
    )
    old_direct_mention.create_time = "2026-05-15 18:34:47"
    latest_unread = message("最新未读只是同步进展", message_id="msg-new")
    latest_unread.create_time = "2026-05-15 18:35:47"
    dws = FakeDws(
        [conversation()],
        {
            "cid-1": [
                old_direct_mention,
                latest_unread,
            ]
        },
        unread_messages={"cid-1": [latest_unread]},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="我看一下")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert len(codex.calls) == 1
    assert dws.sent == [
        (
            "cid-1",
            "> 周俊杰: 旧消息看一下\n\n"
            "<@sender-user-1> 我看一下（by磊哥分身）",
        )
    ]


def test_group_seen_direct_mention_found_in_recent_context_does_not_queue(
    tmp_path: Path, monkeypatch
):
    old_direct_mention = message(
        "@Derek Zen(磊哥) 旧消息看一下",
        message_id="msg-old",
    )
    latest_unread = message("最新未读只是同步进展", message_id="msg-new")
    dws = FakeDws(
        [conversation()],
        {
            "cid-1": [
                old_direct_mention,
                latest_unread,
            ]
        },
        unread_messages={"cid-1": [latest_unread]},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.mark_seen("msg-old", "cid-1")

    worker.run_once()

    assert codex.calls == []
    assert dws.sent == []


def test_prompt_context_limits_after_sorting_reverse_chronological_history():
    messages = []
    for index in range(25):
        item = message(f"history {index}", message_id=f"msg-{index}")
        item.create_time = f"2026-05-13 18:{index:02d}:00"
        messages.append(item)
    reverse_chronological = list(reversed(messages))

    context = DingTalkAutoReplyWorker._prompt_context_messages(
        reverse_chronological,
        [],
        previous_limit=20,
    )

    assert [item.open_message_id for item in context] == [
        f"msg-{index}" for index in range(5, 25)
    ]


def test_build_prompt_includes_known_people_from_org_cache(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws([conversation(single_chat=True)], {})
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.upsert_org_user_profile(
        user_id="subject-user-1",
        name="张晓民",
        open_dingtalk_id=None,
        manager_user_id=None,
        department_ids=set(),
    )
    trigger = message(
        "磊哥，晓民的转正时间快到了。",
        single_chat=True,
        message_id="msg-personnel",
    )

    prompt = worker._build_prompt(
        conversation(single_chat=True),
        [trigger],
        [trigger],
    )

    assert "- 张晓民: user_id=subject-user-1" in prompt


def test_group_stale_direct_mention_found_in_recent_context_does_not_queue(
    tmp_path: Path, monkeypatch
):
    stale_direct_mention = message(
        "@Derek Zen(磊哥) 旧消息看一下",
        message_id="msg-old",
    )
    stale_direct_mention.create_time = "2026-04-30 17:34:59"
    latest_unread = message("最新未读只是同步进展", message_id="msg-new")
    latest_unread.create_time = "2026-05-15 18:35:47"
    dws = FakeDws(
        [conversation()],
        {
            "cid-1": [
                stale_direct_mention,
                latest_unread,
            ]
        },
        unread_messages={"cid-1": [latest_unread]},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert dws.sent == []


def test_single_chat_old_candidate_context_does_not_become_new_question(
    tmp_path: Path, monkeypatch
):
    old_candidate_context = message(
        "这个候选人怎么样？",
        message_id="msg-old-candidate",
        single_chat=True,
    )
    old_candidate_context.create_time = "2026-05-13 17:00:00"
    latest_unread = message("好的", message_id="msg-new-ok", single_chat=True)
    latest_unread.create_time = "2026-05-13 18:00:00"
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [old_candidate_context, latest_unread]},
        unread_messages={"cid-1": [latest_unread]},
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="ack only"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert dws.sent == []
    assert len(codex.calls) == 1
    prompt = codex.calls[0][0]
    assert "只回答“新消息”提出的问题" in prompt
    new_messages_section = prompt.split("新消息:", 1)[1].split(CONTEXT_HEADER, 1)[0]
    context_section = prompt.split(CONTEXT_HEADER, 1)[1]
    assert "好的" in new_messages_section
    assert "这个候选人怎么样？" not in new_messages_section
    assert "这个候选人怎么样？" in context_section


def test_prompt_context_includes_previous_20_plus_unread_tail(
    tmp_path: Path, monkeypatch
):
    old_messages = [
        message(f"历史上下文 {index:02d}", message_id=f"old-{index:02d}")
        for index in range(25)
    ]
    for index, old_message in enumerate(old_messages):
        old_message.create_time = f"2026-05-13 17:{index:02d}:00"
    trigger = message("@Derek Zen(磊哥) 这个需要你看一下", message_id="trigger-msg")
    trigger.create_time = "2026-05-13 18:00:00"
    downstream = message("我已经处理好了", message_id="downstream-msg")
    downstream.create_time = "2026-05-13 18:01:00"
    dws = FakeDws(
        [conversation()],
        {"cid-1": old_messages},
        unread_messages={"cid-1": [trigger, downstream]},
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="handled"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert dws.sent == []
    assert len(codex.calls) == 1
    prompt = codex.calls[0][0]
    new_messages_section = prompt.split("新消息:", 1)[1].split(CONTEXT_HEADER, 1)[0]
    context_section = prompt.split(CONTEXT_HEADER, 1)[1]
    assert "历史上下文 04" not in context_section
    assert "历史上下文 05" in context_section
    assert "历史上下文 24" in context_section
    assert "@Derek Zen(磊哥) 这个需要你看一下" in new_messages_section
    assert "我已经处理好了" not in new_messages_section
    assert "@Derek Zen(磊哥) 这个需要你看一下" in context_section
    assert "我已经处理好了" in context_section


def test_no_reply_action_does_not_send(tmp_path: Path, monkeypatch):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    monkeypatch.setattr("ceo_agent_service.worker.send_macos_notification", lambda **_: None)
    dws = FakeDws([conversation()], {"cid-1": [message("@Derek Zen(磊哥) cc一下")]})
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="cc only"))
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=codex)

    worker.run_once()

    assert dws.sent == []
    assert store.has_seen("msg-1") is True
    attempt = store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "no_reply"
    assert attempt.send_status == "skipped"
    assert attempt.codex_reason == "cc only"


def test_handoff_sends_ack_dings_self_and_marks_handoff(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    monkeypatch.setattr("ceo_agent_service.worker.send_macos_notification", lambda **_: None)
    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@Derek Zen(磊哥) 不要分身，真人看一下")]},
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.HANDOFF_TO_HUMAN))
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=codex)

    worker.run_once()

    expected_ack = (
        "> 周俊杰: 不要分身，真人看一下\n\n"
        f"{HANDOFF_ACK}"
    )
    assert dws.sent == [("cid-1", expected_ack)]
    assert len(dws.dings) == 1
    assert "Friday" in dws.dings[0]
    assert "不要分身" in dws.dings[0]
    assert "previous split-person reply: none" in dws.dings[0]
    assert store.is_in_handoff("cid-1") is True
    attempt = store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.final_reply_text == expected_ack


def test_handoff_clears_when_current_user_replies_manually(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_conversation("cid-1", "Friday", False, None)
    store.enter_handoff(
        "cid-1",
        "msg-1",
        "需要真人",
        handoff_message_create_time="2026-05-13 18:00:00",
    )
    monkeypatch.setattr("ceo_agent_service.worker.send_macos_notification", lambda **_: None)
    dws = FakeDws(
        [conversation()],
        {"cid-1": [derek_message("我来看一下", message_id="derek-msg-1")]},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=codex)

    worker.run_once()

    assert store.is_in_handoff("cid-1") is False
    assert codex.calls == []
    assert dws.sent == []
    assert store.has_seen("derek-msg-1") is True


def test_handoff_does_not_clear_on_split_person_reply(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_conversation("cid-1", "Friday", False, None)
    store.enter_handoff(
        "cid-1",
        "msg-1",
        "需要真人",
        handoff_message_create_time="2026-05-13 18:00:00",
    )
    monkeypatch.setattr("ceo_agent_service.worker.send_macos_notification", lambda **_: None)
    dws = FakeDws(
        [conversation()],
        {
            "cid-1": [
                derek_message(
                    "我让磊哥本人看一下。（by磊哥分身）",
                    message_id="split-msg-1",
                )
            ]
        },
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=codex)

    worker.run_once()

    assert store.is_in_handoff("cid-1") is True
    assert codex.calls == []
    assert dws.sent == []
    assert store.has_seen("split-msg-1") is True


def test_handoff_current_user_lookup_failure_records_error_without_marking_seen(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_conversation("cid-1", "Friday", False, None)
    store.enter_handoff(
        "cid-1",
        "msg-1",
        "需要真人",
        handoff_message_create_time="2026-05-13 18:00:00",
    )
    monkeypatch.setattr("ceo_agent_service.worker.send_macos_notification", lambda **_: None)
    dws = FakeDws(
        [conversation()],
        {"cid-1": [derek_message("我来看一下", message_id="derek-msg-1")]},
        current_user_error=RuntimeError("current user lookup failed"),
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=codex)

    worker.run_once()

    assert store.is_in_handoff("cid-1") is True
    assert store.has_seen("derek-msg-1") is False
    assert store.count_errors() == 2
    assert codex.calls == []


def test_single_chat_unread_is_processed_without_mention(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [message("这个今天能拍吗？", single_chat=True)]},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="可以，先推进")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert dws.sent == [
        (
            "",
            "> 周俊杰: 这个今天能拍吗？\n\n可以，先推进（by磊哥分身）",
        )
    ]
    assert dws.sent_at_users == [[]]
    assert dws.direct_user_ids == ["sender-user-1"]
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "sent"
    assert attempt.direct_user_id == "sender-user-1"
    assert attempt.direct_open_dingtalk_id == "sender-1"
    assert attempt.final_reply_text == (
        "> 周俊杰: 这个今天能拍吗？\n\n可以，先推进（by磊哥分身）"
    )


def test_fake_quote_removes_links_and_mentions_before_truncating():
    trigger = message(
        "https://alidocs.dingtalk.com/i/nodes/doc123 "
        "@Derek Zen(磊哥) @Shawn Hou(侯光焕) @Xingzu Liu(刘兴祖) "
        "@张晓民(Xiaomin张晓民) 数据导入导出业务的根因和解法"
    )

    quote = DingTalkAutoReplyWorker._fake_quote(trigger)

    assert quote == "> 周俊杰: 数据导入导出业务的根因和解法"
    assert "http" not in quote
    assert "@Derek" not in quote
    assert "@Shawn" not in quote


def test_single_chat_current_user_message_does_not_call_codex(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [derek_message("AI自动抓取，用于会议纪要整理")]},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert dws.sent == []


def test_run_once_max_batches_stops_after_limit(tmp_path: Path, monkeypatch):
    conv_1 = DingTalkConversation(
        open_conversation_id="cid-1",
        title="技术部",
        single_chat=False,
        unread_point=1,
    )
    conv_2 = DingTalkConversation(
        open_conversation_id="cid-2",
        title="产品部",
        single_chat=False,
        unread_point=1,
    )
    dws = FakeDws(
        [conv_1, conv_2],
        {
            "cid-1": [message("@Derek Zen(磊哥) 第一个问题", message_id="msg-1")],
            "cid-2": [message("@Derek Zen(磊哥) 第二个问题", message_id="msg-2")],
        },
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先推进")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once(max_batches=1)

    assert len(codex.calls) == 1
    assert len(dws.sent) == 1
    assert dws.sent[0][0] == "cid-1"
    assert worker.store.has_seen("msg-1") is True
    assert worker.store.has_seen("msg-2") is False


def test_single_chat_current_user_display_name_does_not_call_codex(
    tmp_path: Path, monkeypatch
):
    self_message = message(
        "AI自动抓取，用于会议纪要整理",
        single_chat=True,
        sender_user_id=None,
    )
    self_message.sender_name = "磊哥"
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [self_message]},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert dws.sent == []


def test_message_before_current_user_reply_does_not_call_codex(
    tmp_path: Path, monkeypatch
):
    requester = message(
        "@Derek Zen(磊哥) push了",
        message_id="msg-before-self",
    )
    requester.create_time = "2026-05-13 08:45:50"
    manual_reply = derek_message(
        "@周俊杰(周俊杰) 我merge了",
        message_id="msg-self-after",
        create_time="2026-05-13 11:00:03",
    )
    dws = FakeDws(
        [conversation()],
        {"cid-1": [requester, manual_reply]},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert dws.sent == []


def test_message_after_current_user_reply_still_calls_codex(
    tmp_path: Path, monkeypatch
):
    manual_reply = derek_message(
        "这个ACL表@张晓民(Xiaomin张晓民) 看一下",
        message_id="msg-self-before",
        create_time="2026-05-13 15:15:14",
    )
    requester = message(
        "@Derek Zen(磊哥) 我和俊杰聊下",
        message_id="msg-after-self",
    )
    requester.create_time = "2026-05-13 15:16:49"
    dws = FakeDws(
        [conversation()],
        {"cid-1": [manual_reply, requester]},
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="handled"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert len(codex.calls) == 1
    assert "@Derek Zen(磊哥) 我和俊杰聊下" in codex.calls[0][0]
    assert "这个ACL表" not in codex.calls[0][0].split("新消息:", 1)[1].split(CONTEXT_HEADER, 1)[0]


def test_read_failure_records_error_and_continues_next_conversation(
    tmp_path: Path, monkeypatch
):
    bad_conversation = DingTalkConversation(
        open_conversation_id="cid-bad",
        title="bad",
        single_chat=False,
        unread_point=1,
    )
    good_conversation = DingTalkConversation(
        open_conversation_id="cid-good",
        title="good",
        single_chat=False,
        unread_point=1,
    )
    good_message = message(
        "@Derek Zen(磊哥) 这个怎么处理？",
        message_id="msg-good",
    )
    good_message.open_conversation_id = "cid-good"
    dws = FakeDws(
        [bad_conversation, good_conversation],
        {"cid-good": [good_message]},
        read_errors={"cid-bad": RuntimeError("forbidden request")},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先按A方案走")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert dws.sent == [
        (
            "cid-good",
            "> 周俊杰: 这个怎么处理？\n\n"
            "<@sender-user-1> 先按A方案走（by磊哥分身）",
        )
    ]
    assert len(codex.calls) == 1
    assert worker.store.count_errors() == 1


def test_internal_personnel_question_missing_subject_asks_clarifying_question(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [message("这个人后续怎么处理？", single_chat=True)]},
    )
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="可以晋升",
            sensitivity_kind=SensitivityKind.INTERNAL_PERSONNEL,
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert dws.sent == [
        (
            "",
            "> 周俊杰: 这个人后续怎么处理？\n\n"
            "这个是关于谁的问题？（by磊哥分身）",
        )
    ]


def test_internal_personnel_question_allows_hr_requester(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [message("张三转正怎么看？", single_chat=True)]},
    )
    dws.hr_users.add("sender-user-1")
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="建议先观察一个月",
            sensitivity_kind=SensitivityKind.INTERNAL_PERSONNEL,
            personnel_subject_user_id="subject-user-1",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert dws.sent == [
        (
            "",
            "> 周俊杰: 张三转正怎么看？\n\n"
            "建议先观察一个月（by磊哥分身）",
        )
    ]


def test_internal_personnel_question_allows_subject_manager(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [message("张三绩效怎么定？", single_chat=True)]},
    )
    dws.manager_chains["subject-user-1"] = ["sender-user-1"]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="先按事实反馈",
            sensitivity_kind=SensitivityKind.INTERNAL_PERSONNEL,
            personnel_subject_user_id="subject-user-1",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert dws.sent == [
        (
            "",
            "> 周俊杰: 张三绩效怎么定？\n\n"
            "先按事实反馈（by磊哥分身）",
        )
    ]


def test_internal_personnel_question_refuses_unrelated_requester(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [message("张三绩效怎么定？", single_chat=True)]},
    )
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="先按事实反馈",
            sensitivity_kind=SensitivityKind.INTERNAL_PERSONNEL,
            personnel_subject_user_id="subject-user-1",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert dws.sent == [
        (
            "",
            "> 周俊杰: 张三绩效怎么定？\n\n"
            "这个涉及内部人事隐私，我不能回答。（by磊哥分身）",
        )
    ]


def test_candidate_question_missing_department_asks_clarifying_question(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [message("这个候选人怎么样？", single_chat=True)]},
    )
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="可以推进",
            sensitivity_kind=SensitivityKind.EXTERNAL_CANDIDATE,
            candidate_context_known=False,
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert dws.sent == [
        (
            "",
            "> 周俊杰: 这个候选人怎么样？\n\n"
            "这个候选人是哪个岗位/部门的？（by磊哥分身）",
        )
    ]


def test_candidate_question_allows_related_department_requester(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [message("这个候选人怎么样？", single_chat=True)]},
    )
    dws.user_departments["sender-user-1"] = {"dept-sales"}
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="可以推进",
            sensitivity_kind=SensitivityKind.EXTERNAL_CANDIDATE,
            candidate_context_known=True,
            candidate_department_ids=["dept-sales"],
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert dws.sent == [
        (
            "",
            "> 周俊杰: 这个候选人怎么样？\n\n"
            "可以推进（by磊哥分身）",
        )
    ]


def test_candidate_question_refuses_unrelated_department_requester(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [message("这个候选人怎么样？", single_chat=True)]},
    )
    dws.user_departments["sender-user-1"] = {"dept-product"}
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="可以推进",
            sensitivity_kind=SensitivityKind.EXTERNAL_CANDIDATE,
            candidate_context_known=True,
            candidate_department_ids=["dept-sales"],
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert dws.sent == [
        (
            "",
            "> 周俊杰: 这个候选人怎么样？\n\n"
            "这个候选人信息只回答相关部门的人。（by磊哥分身）",
        )
    ]


def test_permission_lookup_failure_records_error_and_does_not_send(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    monkeypatch.setattr("ceo_agent_service.worker.send_macos_notification", lambda **_: None)
    dws = FakeDws(
        [conversation(single_chat=True)],
        {
            "cid-1": [
                message("张三绩效怎么定？", single_chat=True, sender_user_id=None)
            ]
        },
    )
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="先按事实反馈",
            sensitivity_kind=SensitivityKind.INTERNAL_PERSONNEL,
            personnel_subject_user_id="subject-user-1",
        )
    )
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=codex)

    worker.run_once()

    assert dws.sent == []
    assert store.count_errors() == 1
    assert store.has_seen("msg-1") is False


def test_dry_run_does_not_mutate_terminal_state(tmp_path: Path, monkeypatch):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    monkeypatch.setattr("ceo_agent_service.worker.send_macos_notification", lambda **_: None)
    dws = FakeDws([conversation()], {"cid-1": [message("@Derek Zen(磊哥) 这个怎么处理？")]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先按A方案走")
    )
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=codex, dry_run=True)

    worker.run_once()

    assert dws.sent == []
    assert store.has_seen("msg-1") is False
    assert store.count_sent_replies() == 0
    assert store.is_in_handoff("cid-1") is False


def test_send_failure_records_error_and_does_not_mark_seen(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    monkeypatch.setattr("ceo_agent_service.worker.send_macos_notification", lambda **_: None)
    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@Derek Zen(磊哥) 这个怎么处理？")]},
        send_error=RuntimeError("send failed"),
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先按A方案走")
    )
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=codex)

    worker.run_once()

    assert store.has_seen("msg-1") is False
    assert store.count_sent_replies() == 0
    assert store.count_errors() == 1
    assert dws.send_attempt_count == 2
    attempt = store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert attempt.retry_count == 1
    assert "attempt 1: send failed" in attempt.send_error
    assert "attempt 2: send failed" in attempt.send_error


def test_pat_authorization_error_is_recorded_as_failed_without_retry_or_url(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    monkeypatch.setattr("ceo_agent_service.worker.send_macos_notification", lambda **_: None)
    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@Derek Zen(磊哥) 这个怎么处理？")]},
        send_error=DwsError(
            "dws command failed with exit code 4: PAT_HIGH_RISK_NO_PERMISSION",
            code="PAT_HIGH_RISK_NO_PERMISSION",
        ),
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先按A方案走")
    )
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=codex)

    worker.run_once()

    assert dws.send_attempt_count == 1
    assert store.has_seen("msg-1") is False
    assert store.count_sent_replies() == 0
    attempt = store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert attempt.retry_count == 0
    assert "PAT_HIGH_RISK_NO_PERMISSION" in attempt.send_error
    assert "authorizationUrl" not in attempt.send_error
    assert "open-dev.dingtalk.com" not in attempt.send_error


def test_handoff_ding_failure_does_not_mark_seen_or_enter_handoff(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    monkeypatch.setattr("ceo_agent_service.worker.send_macos_notification", lambda **_: None)
    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@Derek Zen(磊哥) 不要分身，真人看一下")]},
        ding_error=RuntimeError("ding failed"),
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.HANDOFF_TO_HUMAN))
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=codex)

    worker.run_once()

    assert dws.sent == []
    assert store.has_seen("msg-1") is False
    assert store.is_in_handoff("cid-1") is False
    assert store.count_errors() == 1


def test_persists_codex_last_session_id_after_decision(tmp_path: Path, monkeypatch):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    monkeypatch.setattr("ceo_agent_service.worker.send_macos_notification", lambda **_: None)
    dws = FakeDws([conversation()], {"cid-1": [message("@Derek Zen(磊哥) cc一下")]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.NO_REPLY, reason="cc only"),
        next_session_id="session-1",
    )
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=codex)

    worker.run_once()

    assert store.get_codex_session_id("cid-1") == "session-1"


def test_stale_codex_last_session_id_is_not_persisted(tmp_path: Path, monkeypatch):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    monkeypatch.setattr("ceo_agent_service.worker.send_macos_notification", lambda **_: None)
    dws = FakeDws([conversation()], {"cid-1": [message("@Derek Zen(磊哥) cc一下")]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.NO_REPLY, reason="cc only"),
        last_session_id="stale-session",
    )
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=codex)

    worker.run_once()

    assert store.get_codex_session_id("cid-1") is None
