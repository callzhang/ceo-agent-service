"""Routing for DingTalk OA messages that are observations of one OA case.

An OA system notification and a human chat reminder are inputs to the same
scheduled approval workflow.  Neither one is an independent approval task.
"""

from dataclasses import dataclass
from enum import StrEnum
import re
from urllib.parse import parse_qs, urlparse

from app.dingtalk_models import DingTalkMessage
from app.oa_approval import extract_oa_url


OA_APPROVAL_LINK_PATTERN = re.compile(
    r"aflow\.dingtalk\.com|dinghash(?:=|%3D)approval|swfrom(?:=|%3D)oa",
    re.IGNORECASE,
)
OA_CHAT_REMINDER_PATTERN = re.compile(
    r"^\s*\[Ding]\S{1,40}提醒您审批", re.IGNORECASE
)


class OaNotificationKind(StrEnum):
    SYSTEM_NOTIFICATION = "system_notification"
    CHAT_REMINDER = "chat_reminder"


@dataclass(frozen=True)
class OaNotificationRoute:
    kind: OaNotificationKind
    process_instance_id: str
    task_id: str
    requires_reply: bool


def classify_oa_notification(
    message: DingTalkMessage,
    oa_url: str = "",
) -> OaNotificationRoute | None:
    """Classify only OA notifications that must not create an Agent run."""

    content = message.content.strip()
    if not oa_url:
        oa_url = extract_oa_url(content)
    process_instance_id = _query_value(
        oa_url, "procInstId", "processInstanceId", "process_instance_id"
    )
    task_id = _query_value(oa_url, "taskId", "task_id")
    is_chat_reminder = bool(OA_CHAT_REMINDER_PATTERN.search(content))
    if is_chat_reminder and message.sender_name.strip() != "OA审批":
        return OaNotificationRoute(
            kind=OaNotificationKind.CHAT_REMINDER,
            process_instance_id=process_instance_id,
            task_id=task_id,
            requires_reply=True,
        )
    has_oa_reference = bool(oa_url or OA_APPROVAL_LINK_PATTERN.search(content))
    if not has_oa_reference:
        return None
    if message.sender_name.strip() == "OA审批":
        return OaNotificationRoute(
            kind=OaNotificationKind.SYSTEM_NOTIFICATION,
            process_instance_id=process_instance_id,
            task_id=task_id,
            requires_reply=False,
        )
    return None


def oa_case_key(process_instance_id: str) -> str:
    process_id = process_instance_id.strip()
    if not process_id:
        raise ValueError("OA process instance id is required")
    return f"oa:{process_id}"


def oa_node_key(process_instance_id: str, task_id: str) -> str:
    process_id = process_instance_id.strip()
    node_id = task_id.strip()
    if not process_id:
        raise ValueError("OA process instance id is required")
    return f"{oa_case_key(process_id)}:{node_id}" if node_id else oa_case_key(process_id)


def render_oa_result_reply(
    *,
    process_instance_id: str,
    task_id: str,
    action: str,
    status: str,
    summary: str,
    oa_url: str,
) -> str:
    """Render a deterministic, human-readable result for the chat reminder."""

    normalized_action = action.strip().casefold()
    action_label = {
        "approve": "通过",
        "agree": "通过",
        "同意": "通过",
        "return": "退回",
        "redirect": "退回",
        "退回": "退回",
        "reject": "拒绝",
        "refuse": "拒绝",
        "拒绝": "拒绝",
        "comment": "留言并保留待处理",
        "评论": "留言并保留待处理",
    }.get(normalized_action, action.strip() or "已审阅")
    normalized_status = status.strip().casefold()
    status_label = {
        "done": "已完成",
        "completed": "已完成",
        "commented": "已完成本轮处理",
        "sent": "已完成",
        "needs_human": "已升级人工",
        "pending": "待人工处理",
        "failed": "未完成",
    }.get(normalized_status, status.strip() or "已处理")
    lines = [
        "OA 审批结果",
        f"处理状态：{status_label}",
        f"审批动作：{action_label}",
    ]
    if summary.strip():
        lines.append(f"说明：{summary.strip()}")
    if oa_url.strip():
        lines.append(f"查看审批：{oa_url.strip()}")
    return "\n".join(lines)


def _query_value(url: str, *keys: str) -> str:
    if not url:
        return ""
    query = parse_qs(urlparse(url).query)
    for key in keys:
        values = query.get(key)
        if values and values[0].strip():
            return values[0].strip()
    return ""
