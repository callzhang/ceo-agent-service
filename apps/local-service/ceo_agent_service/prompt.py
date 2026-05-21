import re
import unicodedata
from dataclasses import dataclass
from html import unescape
from urllib.parse import urlsplit, urlunsplit

from ceo_agent_service.config import (
    principal_display_name,
    principal_handoff_name,
    responsibility_summary,
)
from ceo_agent_service.dingtalk_models import DingTalkConversation, DingTalkMessage


MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*]\([^)]+\)")
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)]\((https?://[^)]+)\)")
RAW_URL_RE = re.compile(r"https?://[^\s)]+")
HTML_TAG_RE = re.compile(r"<[^>]+>")
LINKED_DOCUMENT_MARKDOWN_LIMIT = 20000


@dataclass(frozen=True)
class LinkedDocumentContext:
    url: str
    title: str
    markdown: str


def ceo_agent_thread_prompt() -> str:
    principal = principal_display_name()
    handoff_name = principal_handoff_name()
    responsibility = responsibility_summary()
    return f"""# CEO Agent Prompt

你是 {principal} 的钉钉自动回复分身。

工作原则：
- 先判断是否需要回复；群聊只有明确需要 {principal} 处理时才回复。
- {principal} 的组织职责：{responsibility}
- 单聊未读消息默认作为候选，但仍要判断是否需要回复。
- 系统类信息、机器人通知、审批/OA/日程/文件状态/自动同步等通知性消息，只记录 no_reply，不要代表 {principal} 回复；除非真人在同一条新消息里明确向 {principal} 提问或要求 {principal} 处理。
- 只回答“新消息”提出的问题；“上下文消息”包含前 20 条和未读后续到当前，只能帮助理解背景和后续状态，不能当成新的待回复问题。
- 如果上下文显示问题已经被其他人或 {principal} 处理完，返回 no_reply。
- 如果新消息询问 {principal} 是否已经完成某个线下动作，除非上下文明示完成状态，否则不要断言已完成或未完成；改为说明下一步动作。
- 如果新消息是在催 {principal} 本人执行现实动作、进入会议、接电话、到现场、查看即时消息或做只有 {principal} 本人才能做的事，不能代 {principal} 声称他正在、即将或已经执行现实动作，也不能替 {principal} 承诺马上处理；应 handoff_to_human，让 {handoff_name} 本人接管。
- 如果新消息要求审核、定稿或确认文件/报告/材料，先让对方把需要审核的文件或链接发出来；你可以给初步反馈，但最终定稿或确认必须说明还需要 {handoff_name} 本人确认。
- 如果新消息要求 comments、审核、定稿或确认，并且“上下文消息”或“引用”里已经有被评论对象、文件名、正文、摘要或链接，必须优先使用这些上下文材料；不要忽略上文后直接要求对方重新发。只有上下文和“已获取的钉钉材料”都没有正文或可读取线索时，才追问可访问正文或链接。
- 如果新消息涉及 OA、审批或催办，必须先阅读 `management/OA/钉钉审批审阅原则.md`。审批审阅不是替 {principal} 执行审批动作；必须先看完整表单、附言、留言、流程节点、附件和链接材料，缺任何实质材料时不能给批准、退回或拒绝结论，只能说明材料不足、要求补材料或 handoff_to_human。

检索原则：
- 回答任何问题前，先检索本地 workspace，尽量找到前文、背景材料、相关文档、会议记录、岗位要求、简历或历史讨论后再回答。
- 因为全局规则不会自动注入，本 thread 必须主动使用 graphify：先阅读 `graphify-out/GRAPH_REPORT.md` 理解核心节点和社区结构；优先使用 `graphify query "<问题>"`、`graphify explain "<概念>"` 或 `graphify path "<A>" "<B>"` 找关系，再用 `rg` 和打开文件补充证据。
- 如果“新消息”或“引用”里有 `https://alidocs.dingtalk.com/i/nodes/` 钉钉在线文档链接，必须先读取文档正文再判断；优先使用 prompt 中“已获取的钉钉材料”内容。如果没有该区块，必须调用 `dws doc read --node "<链接>" --format json` 读取正文。禁止用 curl、HTTP API 或浏览器直接读钉钉在线文档；如果文档读不到，不能凭感觉回复，返回 stop_with_error 并在 audit_summary 说明失败原因。
- 普通钉钉文件和钉钉在线文档不同。如果“已获取的钉钉材料”里已经有普通文件正文，必须基于正文回答。如果材料区块只显示“钉钉普通文件已定位，但正文未能读取”，说明服务未能取得文件内容；当对方要求 comments、审核、总结、判断或修改意见时，不能只凭文件名回复，应返回 stop_with_error 或追问可访问正文。
- 回答外部候选人是否匹配、是否推进、是否降级评估前，必须先检索 workspace 里的岗位要求/JD/岗位画像，并查看上下文提到的简历文件或链接内容；如果拿不到岗位要求或简历内容，不能凭一句消息下结论，应追问补充材料或说明材料齐全后再判断。

隐私和权限：
- 必须输出 sensitivity_kind: general、internal_personnel 或 external_candidate。
- 内部员工的人事问题必须输出 internal_personnel；如果知道对象，输出 personnel_subject_user_id，否则留空。
- 发信人讨论自己的请假、调休、晋升诉求、绩效反馈、工作状态、代码提交、工作节奏或个人安排时，人事对象就是发信人，personnel_subject_user_id 必须填写该消息的 sender_user_id；单聊和群聊都适用，不要追问“关于谁”。
- 外部候选人问题必须输出 external_candidate；如果岗位/部门能从会话名、消息或引用里看出来，输出 candidate_context_known=true，否则为 false。
- 如果知道候选人对应的钉钉部门 id，输出 candidate_department_ids；不知道部门 id 时留空，不要编造。
- 不要输出引用、来源、文件路径、session id 或 thread id。
- reply_text 不得提及 Codex、graphify、本地 workspace、本地检索、工具、session、thread、文件路径或任何运行环境细节；只能说“我这边看到/没看到材料”“当前材料不足”等用户可理解表述。

输出协议：
- 只输出合法 JSON，不要输出 Markdown 或解释文字。
- action 必须是 send_reply、ask_clarifying_question、handoff_to_human、no_reply 或 stop_with_error。
- 当 action 是 send_reply 或 ask_clarifying_question 时，reply_text 必须非空；不知道就追问，不要输出空回复。
- 为了本地审计，必须输出 audit_documents 和 audit_summary。audit_documents 是数组，每项包含 path/title/relevance；记录你实际检索、打开或依据的本地文档、钉钉文件、简历、JD、岗位画像或会议记录。没有查看文档时输出空数组。audit_summary 是可审计的简要判断依据，说明用了哪些事实和规则；不要输出逐字思维链、内心草稿或隐藏推理。
- audit_summary 可以记录事实和规则，但不要写 Codex、graphify、本地 workspace、本地路径、session、thread 等运行细节；这些细节只放在 audit_documents 或工具事件里。
- 如果 send_reply 或 ask_clarifying_question 的 audit_documents 为空，audit_summary 必须明确说明未找到可用文档证据，或说明这个问题只需要上下文判断。
"""


def build_turn_prompt(
    conversation: DingTalkConversation,
    new_messages: list[DingTalkMessage],
    context_messages: list[DingTalkMessage],
    *,
    style_lines: list[str],
    include_thread_prompt: bool,
    linked_documents: list[LinkedDocumentContext] | None = None,
    known_people_lines: list[str] | None = None,
) -> str:
    lines: list[str] = []
    lines.extend(style_lines)
    lines.extend(
        [
            "当前待处理消息:",
            f"会话: {conversation.title}",
            f"会话类型: {'单聊' if conversation.single_chat else '群聊'}",
            "新消息:",
        ]
    )
    for message in new_messages:
        lines.extend(message_lines(message))

    if known_people_lines:
        lines.append(
            "可用组织人员标识（如内部人员问题对象匹配这些人，personnel_subject_user_id 必须使用对应 user_id）:"
        )
        lines.extend(known_people_lines)

    if linked_documents:
        lines.append("已获取的钉钉材料:")
        for index, document in enumerate(linked_documents, start=1):
            lines.extend(linked_document_lines(index, document))

    lines.append("上下文消息（前 20 条 + 后续到当前）:")
    for message in context_messages:
        lines.extend(message_lines(message))
    return "\n".join(lines)


def message_lines(message: DingTalkMessage) -> list[str]:
    content = sanitize_dingtalk_prompt_text(message.content)
    sender_identity = (
        f" sender_user_id={message.sender_user_id}" if message.sender_user_id else ""
    )
    lines = [
        f"- {message.sender_name}{sender_identity} {message.create_time}: {content}"
    ]
    if message.quoted_content:
        quoted_content = sanitize_dingtalk_prompt_text(message.quoted_content)
        if quoted_content and not _all_lines_present(quoted_content, content):
            lines.append(f"  引用: {quoted_content}")
    return lines


def linked_document_lines(index: int, document: LinkedDocumentContext) -> list[str]:
    markdown = _clean_document_markdown(document.markdown)
    return [
        f"- 文档{index}: {document.title or '未命名钉钉文档'}",
        f"  链接: {_shorten_url(document.url)}",
        "  正文:",
        *[f"    {line}" for line in markdown.splitlines() if line.strip()],
    ]


def sanitize_dingtalk_prompt_text(text: str) -> str:
    cleaned_lines: list[str] = []
    seen_lines: set[str] = set()
    for raw_line in text.splitlines():
        line = MARKDOWN_IMAGE_RE.sub("", raw_line).strip()
        if not line:
            continue
        line = MARKDOWN_LINK_RE.sub(_format_markdown_link, line)
        line = RAW_URL_RE.sub(lambda match: _shorten_url(match.group(0)), line)
        if line in seen_lines:
            continue
        cleaned_lines.append(line)
        seen_lines.add(line)
    return "\n".join(cleaned_lines)


def _clean_document_markdown(markdown: str) -> str:
    text = unescape(markdown)
    text = HTML_TAG_RE.sub("", text)
    text = "\n".join(line.rstrip() for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) <= LINKED_DOCUMENT_MARKDOWN_LIMIT:
        return text
    return text[:LINKED_DOCUMENT_MARKDOWN_LIMIT].rstrip() + "\n[文档正文过长，后续内容已截断]"


def _format_markdown_link(match: re.Match[str]) -> str:
    label = match.group(1).strip()
    url = match.group(2).strip()
    short_url = _shorten_url(url)
    if label == url or label.startswith("http://") or label.startswith("https://"):
        return f"链接: {short_url}"
    return f"{label}: {short_url}"


def _shorten_url(url: str) -> str:
    if _has_unbalanced_url_host_brackets(url) or _has_invalid_nfkc_url_host(url):
        return url
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _url_authority(url: str) -> str:
    scheme_separator = url.find("://")
    if scheme_separator < 0:
        return ""
    authority_start = scheme_separator + len("://")
    authority_end = len(url)
    for delimiter in ("/", "?", "#"):
        delimiter_index = url.find(delimiter, authority_start)
        if delimiter_index >= 0:
            authority_end = min(authority_end, delimiter_index)
    return url[authority_start:authority_end]


def _has_unbalanced_url_host_brackets(url: str) -> bool:
    authority = _url_authority(url)
    return ("[" in authority) != ("]" in authority)


def _has_invalid_nfkc_url_host(url: str) -> bool:
    authority = _url_authority(url)
    normalized_candidate = (
        authority.replace("@", "").replace(":", "").replace("#", "").replace("?", "")
    )
    normalized = unicodedata.normalize("NFKC", normalized_candidate)
    return normalized != normalized_candidate and any(
        char in normalized for char in "/?#@:"
    )


def _all_lines_present(needle: str, haystack: str) -> bool:
    return all(line in haystack for line in needle.splitlines() if line.strip())
