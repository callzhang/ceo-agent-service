import csv
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ceo_agent_service.config import (
    assistant_signature,
    principal_name,
    style_speaker_names,
)

SIGNATURE = assistant_signature()
MIN_REPLY_INFORMATION_UNITS = 20
MAX_DINGTALK_REPLY_CHARACTERS = 300
SYSTEM_MESSAGE_PREFIXES = (
    "AI自动抓取，用于公司记忆",
    "AI自动抓取，用于会议纪要整理",
)
TEXT_MESSAGE_TYPES = {"text"}
MESSAGE_TYPE_KEYS = (
    "msgType",
    "messageType",
    "contentType",
    "content_type",
    "msg_type",
    "type",
)
RENDERED_NON_TEXT_PREFIXES = (
    "[文件]",
    "[图片]",
    "[视频]",
    "[日程]",
    "[Ding]",
)
DOCUMENT_LIKE_PREFIXES = (
    "已更新文档",
    "总体判断",
    "最新发现",
    "结论",
    "建议这一页",
)


class CorpusRecord(BaseModel):
    source_type: str
    source_title: str
    timestamp: str
    context: str
    derek_reply: str
    message_id: str
    conversation_id: str
    speaker_name: str
    metadata_json: str


FIELDNAMES = list(CorpusRecord.model_fields.keys())
MEDIA_OR_LINK_PATTERN = re.compile(
    r"!\[[^\]]*\]\([^)]*\)|"
    r"\[[^\]]*\]\([^)]*\)|"
    r"https?://\S+|"
    r"dingtalk://\S+|"
    r"\[(?:文件|图片|视频|日程|Ding)\]",
    re.IGNORECASE,
)
CHINESE_CHARACTER_PATTERN = re.compile(r"[\u4e00-\u9fff]")
WORD_PATTERN = re.compile(r"[A-Za-z0-9]+(?:[-_'][A-Za-z0-9]+)*")
MARKDOWN_TABLE_PATTERN = re.compile(r"\|.*\|[\s\S]*\|[-:\s|]+\|")
BULLET_LINE_PATTERN = re.compile(r"(^|\n)\s*[-*]\s+")


def extract_minutes_records(path: Path, source_title: str) -> list[CorpusRecord]:
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    records: list[CorpusRecord] = []
    previous_text = ""
    index = 0

    while index < len(lines):
        speaker = lines[index]
        if speaker.startswith("#"):
            index += 1
            continue

        if index + 2 < len(lines) and _looks_like_timestamp(lines[index + 1]):
            timestamp = lines[index + 1]
            text = lines[index + 2]
            if speaker in set(style_speaker_names()) and is_informative_reply(text):
                records.append(
                    CorpusRecord(
                        source_type="minutes",
                        source_title=source_title,
                        timestamp=timestamp,
                        context=previous_text[-500:],
                        derek_reply=text,
                        message_id=f"{path.name}:{index}",
                        conversation_id=str(path),
                        speaker_name=speaker,
                        metadata_json="{}",
                    )
                )
            previous_text = text
            index += 3
            continue

        previous_text = speaker
        index += 1

    return records


def append_records(csv_path: Path, records: list[CorpusRecord]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    existing_ids = _existing_message_ids(csv_path)
    write_header = not csv_path.exists() or csv_path.stat().st_size == 0

    with csv_path.open("a", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        for record in records:
            if not is_informative_reply(record.derek_reply):
                continue
            if record.message_id in existing_ids:
                continue
            writer.writerow(record.model_dump())
            existing_ids.add(record.message_id)


def write_records(csv_path: Path, records: list[CorpusRecord]) -> int:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    seen_message_ids: set[str] = set()
    with csv_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        writer.writeheader()
        for record in records:
            if not is_informative_reply(record.derek_reply):
                continue
            if record.message_id in seen_message_ids:
                continue
            writer.writerow(record.model_dump())
            seen_message_ids.add(record.message_id)
            written += 1
    return written


def _existing_message_ids(csv_path: Path) -> set[str]:
    if not csv_path.exists() or csv_path.stat().st_size == 0:
        return set()

    with csv_path.open("r", encoding="utf-8", newline="") as file:
        return {row["message_id"] for row in csv.DictReader(file) if row.get("message_id")}


def load_corpus_records(csv_path: Path) -> list[CorpusRecord]:
    if not csv_path.exists() or csv_path.stat().st_size == 0:
        return []

    records: list[CorpusRecord] = []
    with csv_path.open("r", encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file):
            try:
                record = CorpusRecord.model_validate(row)
            except ValueError:
                continue
            if is_informative_reply(record.derek_reply):
                records.append(record)
    return records


def build_style_profile(records: list[CorpusRecord]) -> str:
    return "\n".join(
        [
            f"# {principal_name()} Style Profile",
            "",
            "- 先结论，再解释原因，再给下一步动作。",
            "- 语气直接，围绕业务目标、客户价值、组织效率和执行优先级。",
            "- 少客套，不做长篇铺垫。",
            "- 对不清楚的问题先收敛问题，不急着给泛泛判断。",
            "- 不复用语料中的人名、项目隐私和临时情绪。",
        ]
    )


def retrieve_similar_examples(
    query: str,
    records: list[CorpusRecord],
    limit: int = 5,
) -> list[CorpusRecord]:
    query_chars = set(query)
    scored = []
    for record in records:
        haystack_chars = set(record.context + record.derek_reply)
        score = len(query_chars & haystack_chars)
        if score > 0:
            scored.append((score, record))

    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored:
        return []

    minimum_score = max(3, int(scored[0][0] * 0.35))
    return [record for score, record in scored[:limit] if score >= minimum_score]


def build_dingtalk_records_from_sender_payload(
    payload: dict[str, Any],
    limit: int,
) -> list[CorpusRecord]:
    records: list[CorpusRecord] = []
    for conversation in payload.get("result", {}).get("conversationMessagesList", []):
        title = str(conversation.get("title") or "")
        conversation_id = str(conversation.get("openConversationId") or "")
        single_chat = bool(conversation.get("singleChat", False))
        for message in conversation.get("messages", []):
            if not is_plain_text_dingtalk_message(message):
                continue
            content = str(message.get("content") or "")
            if not is_conversational_dingtalk_text(content):
                continue
            if not is_informative_reply(content):
                continue
            quoted_message = message.get("quotedMessage") or {}
            metadata = {
                "sender_open_dingtalk_id": message.get("senderOpenDingTalkId"),
                "single_chat": single_chat,
                "source": "dws chat message list-by-sender",
            }
            records.append(
                CorpusRecord(
                    source_type="dingtalk",
                    source_title=title,
                    timestamp=str(message.get("createTime") or ""),
                    context=str(quoted_message.get("content") or "")[-500:],
                    derek_reply=content,
                    message_id=str(message.get("openMessageId") or ""),
                    conversation_id=conversation_id,
                    speaker_name=str(message.get("sender") or ""),
                    metadata_json=json.dumps(metadata, ensure_ascii=False),
                )
            )
            if len(records) >= limit:
                return records
    return records


def is_informative_reply(text: str) -> bool:
    if SIGNATURE in text:
        return False
    if is_system_corpus_message(text):
        return False
    return count_information_units(text) >= MIN_REPLY_INFORMATION_UNITS


def is_plain_text_dingtalk_message(message: dict[str, Any]) -> bool:
    message_type = _message_type(message)
    if message_type is not None:
        return message_type.lower() in TEXT_MESSAGE_TYPES

    content = str(message.get("content") or "").strip()
    if not content:
        return False
    if content.startswith(RENDERED_NON_TEXT_PREFIXES):
        return False
    if MEDIA_OR_LINK_PATTERN.search(content):
        return False
    return not is_system_corpus_message(content)


def is_conversational_dingtalk_text(text: str) -> bool:
    normalized_text = text.strip()
    if len(normalized_text) > MAX_DINGTALK_REPLY_CHARACTERS:
        return False
    if normalized_text.startswith(DOCUMENT_LIKE_PREFIXES):
        return False
    if MARKDOWN_TABLE_PATTERN.search(normalized_text):
        return False
    if len(BULLET_LINE_PATTERN.findall(normalized_text)) >= 3:
        return False
    if normalized_text.count("\n") >= 5:
        return False
    return True


def _message_type(message: dict[str, Any]) -> str | None:
    for key in MESSAGE_TYPE_KEYS:
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def is_system_corpus_message(text: str) -> bool:
    normalized_text = text.strip()
    return any(
        normalized_text.startswith(prefix)
        and "如和工作内容无关或者涉及个人隐私，请拒绝" in normalized_text
        for prefix in SYSTEM_MESSAGE_PREFIXES
    )


def count_information_units(text: str) -> int:
    cleaned_text = MEDIA_OR_LINK_PATTERN.sub(" ", text)
    chinese_count = len(CHINESE_CHARACTER_PATTERN.findall(cleaned_text))
    without_chinese = CHINESE_CHARACTER_PATTERN.sub(" ", cleaned_text)
    word_count = len(WORD_PATTERN.findall(without_chinese))
    return chinese_count + word_count


def _looks_like_timestamp(value: str) -> bool:
    return ":" in value and any(character.isdigit() for character in value)
