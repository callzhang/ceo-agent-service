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
DEFAULT_WORK_PROFILE_TEXT = """# Work Profile

No distilled work profile has been generated yet.

This placeholder lets the service start before local corpus/profile preparation
has finished. Replace it by running `build-work-profile` or by setting
`CEO_WORK_PROFILE_PATH` to another Markdown profile file.
"""


@dataclass(frozen=True)
class LinkedDocumentContext:
    url: str
    title: str
    markdown: str


@dataclass(frozen=True)
class MaterialReferenceContext:
    kind: str
    reference: str
    source_message_id: str
    source_sender: str
    source_time: str


def work_profile_instruction() -> str:
    path = work_profile_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_WORK_PROFILE_TEXT, encoding="utf-8")
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
    material_references: list[MaterialReferenceContext] | None = None,
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

    material_references_block = ""
    if material_references:
        material_lines: list[str] = [
            "待读取材料（由 agent 判断是否读取）:",
            (
                "如果判断依赖材料正文，必须先读取材料；如果消息正文已经足够，可以不读取。"
                "读取失败时不要臆测材料内容，应说明权限或材料问题。"
            ),
            "DWS 读取命令提示:",
            "- 钉钉文档: dws doc info --node <URL> --format json；需要正文时 dws doc read --node <URL> --format json",
            "- AI 听记: dws minutes get info --id <MINUTES_ID> --format json",
            "- 普通文件: 先用消息中的文件名和上下文判断是否需要读取；需要时使用 DWS 文件/云盘能力查询或下载。",
        ]
        for index, material in enumerate(material_references, start=1):
            material_lines.extend(material_reference_lines(index, material))
        material_references_block = _prompt_section_block(
            material_lines,
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
            "material_references_block": material_references_block,
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
    reactions = _message_reaction_records(message.raw_payload)
    if reactions:
        record["reactions"] = reactions
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
    reaction_lines = _message_reaction_lines(message.raw_payload)
    if reaction_lines:
        lines.append(f"  已有 reaction: {'；'.join(reaction_lines)}")
    return lines


def _message_reaction_lines(raw_payload: dict) -> list[str]:
    return [
        _format_message_reaction(record)
        for record in _message_reaction_records(raw_payload)
    ]


def _message_reaction_records(raw_payload: dict) -> list[dict[str, object]]:
    raw_reactions = raw_payload.get("emotionReplyList")
    if not isinstance(raw_reactions, list):
        return []

    records: list[dict[str, object]] = []
    for raw_reaction in raw_reactions:
        if not isinstance(raw_reaction, dict):
            continue
        reaction = _first_non_empty_string(
            raw_reaction,
            ("emoji", "text", "emotion", "emotionName", "emotionId"),
        )
        users = _string_list(raw_reaction.get("replyUsers"))
        if not reaction and not users:
            continue

        record: dict[str, object] = {}
        if reaction:
            record["reaction"] = reaction
        if users:
            record["users"] = users
        records.append(record)
    return records


def _format_message_reaction(record: dict[str, object]) -> str:
    reaction = str(record.get("reaction") or "未知")
    users = record.get("users")
    if isinstance(users, list) and users:
        return f"{reaction}（{', '.join(str(user) for user in users)}）"
    return reaction


def _first_non_empty_string(raw: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = raw.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [text for item in value if (text := str(item).strip())]
    if value is None:
        return []
    text = str(value).strip()
    return [text] if text else []


def linked_document_lines(index: int, document: LinkedDocumentContext) -> list[str]:
    markdown = _clean_document_markdown(document.markdown)
    return [
        f"- 文档{index}: {document.title or '未命名钉钉文档'}",
        f"  链接: {_shorten_url(document.url)}",
        "  正文:",
        *[f"    {line}" for line in markdown.splitlines() if line.strip()],
    ]


def material_reference_lines(index: int, material: MaterialReferenceContext) -> list[str]:
    return [
        f"- 材料{index}:",
        f"  类型: {material.kind}",
        f"  引用: {_shorten_url(material.reference)}",
        f"  来源消息: {material.source_message_id}",
        f"  发送人: {material.source_sender}",
        f"  时间: {material.source_time}",
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
