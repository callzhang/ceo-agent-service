"""Project service input from immutable native invocation records."""

import json
from itertools import islice

from app import native_trajectory as native


def _codex_text(record, *, role, kind):
    body = record.get("payload", {})
    if not isinstance(body, dict) or record.get("type") != "response_item" or body.get("type") != "message" or body.get("role") != role:
        return None
    content = body.get("content", [])
    metadata = body.get("internal_chat_message_metadata_passthrough")
    if not isinstance(metadata, dict):
        return None
    kinds = metadata.get("content_item_kinds", [])
    if not isinstance(content, list) or not isinstance(kinds, list) or len(content) != len(kinds) or not all(isinstance(block, dict) for block in content):
        return None
    blocks = [block.get("text") for block, source in zip(content, kinds)
              if source == kind and block.get("type") == "input_text"]
    return "\n".join(blocks) if blocks and all(isinstance(text, str) for text in blocks) else None


def codex_session_wire_schema(session_id: str) -> dict[str, object] | None:
    """Read the service wire schema actually supplied when a Codex session began."""
    path = native.find_codex_session_path(session_id)
    if path is None:
        return None
    marker = "## Pydantic Wire Contract\n"
    try:
        with path.open(encoding="utf-8") as stream:
            for line in islice(stream, 32):
                record = json.loads(line)
                if not isinstance(record, dict):
                    return None
                developer = _codex_text(
                    record, role="developer", kind="generic.developer_instructions"
                )
                if developer is None:
                    continue
                if marker not in developer:
                    continue
                source = developer.split(marker, 1)[1]
                schema, end = json.JSONDecoder().raw_decode(source)
                if not isinstance(schema, dict):
                    return None
                tail = source[end:]
                return schema if not tail or tail.startswith("\n\n## ") else None
    except (OSError, ValueError, TypeError):
        return None
    return None


def _codex_input(attempt, *, codex_home=None):
    path = native.find_codex_session_path(
        attempt.session_id, codex_home=codex_home
    )
    records = native._codex_turn_records(path, attempt.transcript_start, attempt.transcript_end)
    tasks = [text for record in records
             if (text := _codex_text(record, role="user", kind="user.text")) is not None]
    if len(tasks) != 1:
        return None
    developers = [text for record in records
                  if (text := _codex_text(record, role="developer", kind="generic.developer_instructions")) is not None]
    origin = "本轮原生 Developer 记录"
    if not developers and attempt.transcript_start:
        prefix = native._native_records(path, 0, attempt.transcript_start)
        developers = [text for record in prefix
                      if (text := _codex_text(record, role="developer", kind="generic.developer_instructions")) is not None]
        origin = "session 已保存的 Developer（非本轮重新提交回执）"
    return {"developer_instructions": developers[-1] if developers else "",
            "task_prompt": tasks[0], "submitted_input": tasks[0], "developer_source": origin if developers else "Developer 来源未记录"}


def _claude_input(attempt):
    path = native.claude_session_path(attempt.session_id)
    records = native._native_records(path, attempt.transcript_start, attempt.transcript_end)
    inputs = []
    for record in records:
        if record.get("type") != "user" or record.get("sessionId") != attempt.session_id:
            continue
        message = record.get("message", {})
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content", [])
        texts = [content] if isinstance(content, str) else [block["text"] for block in content if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)] if isinstance(content, list) else []
        text = "\n".join(texts)
        if text.startswith("<developer-instructions>\n"):
            inputs.append(text)
    if len(inputs) != 1:
        return None
    text = inputs[0]
    prefix, delimiter, suffix = "<developer-instructions>\n", "\n</developer-instructions>\n<task>\n", "\n</task>"
    if not text.startswith(prefix) or not text.endswith(suffix) or text.count(delimiter) != 1:
        return None
    developer, task = text[len(prefix):-len(suffix)].split(delimiter)
    return {"developer_instructions": developer, "task_prompt": task,
            "submitted_input": text, "developer_source": "本轮原生 Claude 合并输入"}


def read_attempt_input(attempt, *, codex_home=None):
    if not attempt.session_id or attempt.transcript_end <= attempt.transcript_start:
        return None
    if attempt.runtime_kind == "codex_cli":
        return _codex_input(attempt, codex_home=codex_home)
    if attempt.runtime_kind == "claude_cli":
        return _claude_input(attempt)
    return None
