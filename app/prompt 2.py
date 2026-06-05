import json
import re
import unicodedata
from dataclasses import dataclass
from html import unescape
from urllib.parse import urlsplit, urlunsplit

from app.config import (
    principal_display_name,
    work_profile_path,
)
from app.developer_prompt import render_user_prompt
from app.dingtalk_models import DingTalkConversation, DingTalkMessage


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


def work_profile_instruction() -> str:
    path = work_profile_path()
    if not path.exists():
        return ""
    profile = path.read_text(encoding="utf-8").strip()
    if not profile:
        return ""
    principal = principal_display_name()
    return f"""

{principal} 工作人格 Profile:
- 以下 profile 内容已由服务端注入；不要再尝试读取 profile 文件路径。
- 学习其中的心智模型、决策启发式、表达DNA、价值观/反模式、核心张力和场景硬规则。
- 使用 profile 时不要逐字复述章节名、证据 id、本地路径或调研过程；只把它转化为更接近 {principal} 的判断顺序、追问方式和回复边界。
- profile 不能覆盖既有硬规则：现实动作必须 handoff、审批/OA 必须看完整材料、人事敏感问题谨慎处理、候选人判断必须看岗位和简历证据、reply_text 不得暴露本地路径或工具细节。

Profile 内容:
{profile}
"""


def ceo_agent_thread_prompt() -> str:
    from app.developer_prompt import render_developer_prompt

    return render_developer_prompt()


def build_turn_prompt(
    conversation: DingTalkConversation,
    new_messages: list[DingTalkMessage],
    context_messages: list[DingTalkMessage],
    *,
    style_lines: list[str],
    include_thread_prompt: bool,
    linked_documents: list[LinkedDocumentContext] | None = None,
    image_download_errors: list[str] | None = None,
    known_people_lines: list[str] | None = None,
    sender_org_lines: list[str] | None = None,
) -> str:
    current_message_lines = [
        "当前待处理消息:",
        f"会话: {conversation.title}",
        f"会话类型: {'单聊' if conversation.single_chat else '群聊'}",
        "新消息:",
    ]
    for message in new_messages:
        current_message_lines.extend(message_lines(message))

    sender_org_block = ""
    if sender_org_lines:
        sender_org_block = _prompt_section_block(
            ["发信人组织信息(JSON):", *sender_org_lines],
            trailing_newline=True,
        )

    known_people_block = ""
    if known_people_lines:
        known_people_block = _prompt_section_block(
            [
                "可用组织人员标识（如内部人员问题对象匹配这些人，personnel_subject_user_id 必须使用对应 user_id）:",
                *known_people_lines,
            ],
            trailing_newline=True,
        )

    linked_documents_block = ""
    if linked_documents:
        linked_document_lines_: list[str] = ["已获取的钉钉材料:"]
        for index, document in enumerate(linked_documents, start=1):
            linked_document_lines_.extend(linked_document_lines(index, document))
        linked_documents_block = _prompt_section_block(
            linked_document_lines_,
            trailing_newline=True,
        )

    image_download_block = ""
    if image_download_errors:
        image_download_block = _prompt_section_block(
            [
                "图片读取状态:",
                (
                    "以下图片未能下载。如果当前问题依赖图片内容，不能臆测图片细节；"
                    "应说明图片读取失败并追问可查看版本。"
                    "如果当前问题可基于文字上下文独立处理，可以继续处理。"
                ),
                *[f"- {error}" for error in image_download_errors],
            ],
            trailing_newline=True,
        )

    context_messages_block = (
        "上下文消息（自上次回复后的新信息，最多 20 条）:\n"
        f"{json.dumps(_context_message_records(context_messages), ensure_ascii=False, indent=2)}"
    )

    return render_user_prompt(
        {
            "style_lines": _prompt_section_block(style_lines, trailing_newline=True),
            "current_message_block": _prompt_section_block(
                current_message_lines,
                trailing_newline=True,
            ),
            "sender_org_block": sender_org_block,
            "known_people_block": known_people_block,
            "linked_documents_block": linked_documents_block,
            "image_download_block": image_download_block,
            "context_messages_block": context_messages_block,
        }
    ).strip("\n")


def _prompt_section_block(
    lines: list[str],
    *,
    trailing_newline: bool = False,
) -> str:
    if not lines:
        return ""
    block = "\n".join(lines)
    if trailing_newline:
        return f"{block}\n"
    return block


def _context_message_records(messages: list[DingTalkMessage]) -> list[dict]:
    return [_context_message_record(message) for message in messages]


def _context_message_record(message: DingTalkMessage) -> dict:
    sender: dict[str, str] = {"name": message.sender_name}
    if message.sender_user_id:
        sender["user_id"] = message.sender_user_id
    if message.sender_open_dingtalk_id:
        sender["open_dingtalk_id"] = message.sender_open_dingtalk_id

    record: dict = {
        "open_message_id": message.open_message_id,
        "create_time": message.create_time,
        "sender": sender,
        "content": sanitize_dingtalk_prompt_text(message.content),
    }
    if message.message_type:
        record["message_type"] = message.message_type
    if message.mentioned_user_ids:
        record["mentioned_user_ids"] = message.mentioned_user_ids
    if message.quoted_message_id or message.quoted_content:
        quoted: dict[str, str] = {}
        if message.quoted_message_id:
            quoted["open_message_id"] = message.quoted_message_id
        if message.quoted_content:
            quoted["content"] = sanitize_dingtalk_prompt_text(message.quoted_content)
        record["quoted"] = quoted
    return record


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
