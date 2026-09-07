from __future__ import annotations

from urllib.parse import parse_qs, urlsplit


def oa_identifiers_from_url(url: str) -> tuple[str, str]:
    query = parse_qs(urlsplit(url).query)
    values = {
        "".join(key.replace("_", "").casefold().split()): value
        for key, value in query.items()
    }
    process_values = values.get("procinstid") or values.get("processinstanceid")
    task_values = values.get("taskid")
    process_id = str(process_values[0]).strip() if process_values else ""
    task_id = str(task_values[0]).strip() if task_values else ""
    return process_id, task_id


def reply_business_object_key(
    *,
    channel: str,
    conversation_id: str,
    trigger_message_id: str,
    oa_url: str = "",
    explicit_key: str = "",
) -> str:
    key = explicit_key.strip()
    if key:
        return key
    process_id, task_id = oa_identifiers_from_url(oa_url)
    if process_id and task_id:
        return f"oa:{process_id}:{task_id}"
    return f"message:{channel.strip()}:{conversation_id.strip()}:{trigger_message_id.strip()}"
