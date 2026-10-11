import re
import unicodedata
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from app.config import (
    principal_display_name,
    work_profile_path,
    workspace_path,
)
from app.dingtalk_models import DingTalkMessage


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
    read_command: str = ""


def read_work_profile(*, create_missing: bool = True) -> str:
    path = work_profile_path()
    if not path.exists() and create_missing:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_WORK_PROFILE_TEXT, encoding="utf-8")
    return path.read_text(encoding="utf-8").strip() if path.exists() else DEFAULT_WORK_PROFILE_TEXT.strip()


def work_profile_instruction(*, create_missing: bool = True, profile_text: str | None = None) -> str:
    profile = read_work_profile(create_missing=create_missing) if profile_text is None else profile_text.strip()
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


def runtime_context_instruction() -> str:
    """Inject deployment-specific values into the system prompt.

    Skills stay portable and contain no machine paths, environment variable
    names, or personal identity literals. Values needed to interpret those
    Skills are supplied by the running service instead.
    """
    from app.business_skills import bundled_business_skills_root

    return (
        "## Runtime context\n"
        f"- Configured principal: {principal_display_name()}\n"
        f"- Configured workspace root: {workspace_path()}\n"
        f"- Installed business Skill root: {bundled_business_skills_root()}\n"
        "- Capability identities, browser profiles, and IPC endpoints are the "
        "runtime values supplied by the available capability; do not infer or "
        "hard-code them from a Skill."
    )


def write_work_profile(profile: str) -> Path:
    """Persist the profile consumed by the next Consumer or Audit turn."""
    normalized = profile.strip()
    if not normalized:
        raise ValueError("work profile must not be empty")
    path = work_profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized + "\n", encoding="utf-8")
    return path


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
    coalesced_lines = _message_coalesced_lines(message.raw_payload, message.open_message_id)
    if coalesced_lines:
        lines.append("  合并前序消息:")
        lines.extend(f"  {line}" for line in coalesced_lines)
    reaction_lines = _message_reaction_lines(message.raw_payload)
    if reaction_lines:
        lines.append(f"  已有 reaction: {'；'.join(reaction_lines)}")
    return lines


def _message_coalesced_lines(
    raw_payload: dict,
    current_message_id: str,
) -> list[str]:
    raw_messages = raw_payload.get("coalesced_messages")
    if not isinstance(raw_messages, list):
        return []
    lines: list[str] = []
    for raw_message in raw_messages:
        if not isinstance(raw_message, dict):
            continue
        message_id = str(raw_message.get("open_message_id") or "")
        if message_id == current_message_id:
            continue
        sender_name = str(raw_message.get("sender_name") or "").strip()
        create_time = str(raw_message.get("create_time") or "").strip()
        content = sanitize_dingtalk_prompt_text(str(raw_message.get("content") or ""))
        if not content:
            continue
        prefix = " -"
        if sender_name or create_time:
            prefix = f"- {sender_name} {create_time}:".rstrip()
        lines.append(f"{prefix} {content}")
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
    lines = [
        f"- 材料{index}:",
        f"  类型: {material.kind}",
        f"  引用: {_shorten_url(material.reference)}",
        f"  来源消息: {material.source_message_id}",
        f"  发送人: {material.source_sender}",
        f"  时间: {material.source_time}",
    ]
    if material.read_command:
        lines.append(f"  读取命令: {material.read_command}")
    return lines


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
