from dataclasses import dataclass

from app.store import FeedbackEvent


@dataclass(frozen=True)
class ServiceBugfixClassification:
    accepted: bool
    title: str = ""
    reason: str = ""


SERVICE_BUGFIX_REQUEST_MARKERS = (
    "ceo agent",
    "ceo-agent",
    "ceo-agent-service",
    "分身",
    "自动回复",
    "这个服务",
    "本服务",
)
SERVICE_BUGFIX_PROBLEM_MARKERS = (
    "bug",
    "regression",
    "crash",
    "failed",
    "failure",
    "broken",
    "报错",
    "错误",
    "失败",
    "崩",
    "坏",
    "回归",
    "没生效",
    "不能",
    "无法",
    "漏",
)
ARBITRARY_DEVELOPMENT_MARKERS = (
    "codex",
    "改代码",
    "写代码",
    "任意代码",
    "任何代码",
    "随便改",
)
NEW_FEATURE_REQUEST_MARKERS = (
    "新功能",
    "新增功能",
    "做一个",
    "实现一个",
    "开发一个",
    "任意代码",
    "任何代码",
    "随便改",
)


def classify_service_bugfix_feedback(
    event: FeedbackEvent,
) -> ServiceBugfixClassification:
    comment = event.comment.strip()
    if not comment:
        return ServiceBugfixClassification(False)
    normalized = comment.casefold()
    has_service_anchor = any(
        marker.casefold() in normalized for marker in SERVICE_BUGFIX_REQUEST_MARKERS
    )
    has_problem = any(
        marker.casefold() in normalized for marker in SERVICE_BUGFIX_PROBLEM_MARKERS
    )
    mentions_code_change = any(
        marker.casefold() in normalized for marker in ARBITRARY_DEVELOPMENT_MARKERS
    )
    asks_for_new_development = any(
        marker.casefold() in normalized for marker in NEW_FEATURE_REQUEST_MARKERS
    )
    has_bare_development_request = (
        ("开发" in normalized or "实现" in normalized)
        and asks_for_new_development
    )
    is_arbitrary_development = mentions_code_change or has_bare_development_request
    if is_arbitrary_development or not (has_service_anchor and has_problem):
        return ServiceBugfixClassification(False)
    title = _candidate_title(comment)
    return ServiceBugfixClassification(
        accepted=True,
        title=title,
        reason="用户反馈明确指向 CEO 服务自身的 bug、失败或回归。",
    )


def _candidate_title(comment: str) -> str:
    compact = " ".join(comment.split())
    if len(compact) <= 80:
        return compact
    return compact[:77] + "..."
