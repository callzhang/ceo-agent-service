from dataclasses import dataclass

from app.agent_contracts import DecisionOption
from app.history import HistoryItem
from app.meeting_alignment import DEFAULT_MEETING_MAX_ATTEMPTS
from app.meeting_alignment_models import MeetingAlignmentJob, MeetingAlignmentRun
from app.store import ReplyAttempt, ReplyTask
from app.worker import MAX_REPLY_TASK_ATTEMPTS


_READABLE_FAILURE_REASONS = {
    "consumer_retry_exhausted": (
        "Agent 生成回复连续重试后仍未得到可验证结果，已达到本轮重试上限。"
    ),
    "audit_retry_exhausted": (
        "Agent 执行审计连续重试后仍未得到可验证结果，已达到本轮重试上限。"
    ),
    "codex_process_failed": "Agent 执行进程未成功完成，因此本轮没有得到可验证结果。",
    "codex_result_invalid": "Agent 已返回结果，但结果不符合当前校验契约。",
    "codex_result_missing": "Agent 运行结束，但没有输出可验证结果。",
    "agent_read_only_violation": "Agent 在只读阶段尝试执行外部动作，系统已安全阻止。",
}


@dataclass(frozen=True)
class HistoryAction:
    key: str
    label: str
    instruction: str = ""
    consequence: str = ""


@dataclass(frozen=True)
class HistoryAttention:
    kind: str
    reason: str
    external_effect: str
    retry_attempt: int = 0
    retry_limit: int = 0
    retry_at: str = ""
    actions: tuple[HistoryAction, ...] = ()


def reply_history_attention(
    attempt: ReplyAttempt,
    *,
    task: ReplyTask | None,
    decision_options: tuple[DecisionOption, ...],
    side_effect_state: str,
) -> HistoryAttention | None:
    external_effects = {
        "none": "未执行任何外部动作",
        "confirmed": "已确认产生外部动作",
        "unknown": "执行结果未知，不能安全重放",
    }
    if side_effect_state not in external_effects:
        raise ValueError(f"invalid side effect state: {side_effect_state}")

    status = attempt.send_status.strip().lower()
    reason = (
        attempt.audit_summary
        or attempt.codex_reason
        or attempt.send_error
        or "处理未完成"
    ).strip()
    reason = _readable_failure_reason(reason)
    external_effect = external_effects[side_effect_state]

    if (
        status == "failed"
        and side_effect_state == "none"
        and task is not None
        and task.status in {"pending", "processing"}
    ):
        return HistoryAttention(
            kind="automatic_recovery",
            reason=reason,
            external_effect=external_effect,
            retry_attempt=task.attempts,
            retry_limit=MAX_REPLY_TASK_ATTEMPTS,
            retry_at=task.available_at,
            actions=(HistoryAction("details", "技术详情"),),
        )

    if status == "needs_human":
        choices = tuple(
            HistoryAction(
                option.key,
                option.label,
                option.instruction,
                option.consequence,
            )
            for option in decision_options
        )
        safe_tail = (
            _management_tail(include_retry=False)
            if side_effect_state == "none"
            else _management_tail(include_retry=False, include_defer=False)
        )
        return HistoryAttention(
            kind="needs_manager",
            reason=reason,
            external_effect=external_effect,
            actions=choices + safe_tail,
        )

    if status == "failed":
        return HistoryAttention(
            kind="needs_manager",
            reason=reason,
            external_effect=external_effect,
            actions=(
                _management_tail(include_retry=True)
                if side_effect_state == "none"
                else _management_tail(include_retry=False, include_defer=False)
            ),
        )
    return None


def _management_tail(
    *,
    include_retry: bool,
    include_defer: bool = True,
) -> tuple[HistoryAction, ...]:
    actions: list[HistoryAction] = []
    if include_retry:
        actions.append(HistoryAction("retry", "重试当前任务"))
    if include_defer:
        actions.append(
            HistoryAction(
                "defer",
                "暂不处理",
                (
                    "暂不处理当前事项。审批类事项必须通知实际申请人仍待处理、"
                    "缺少的材料或事实，以及下一步需要做什么。"
                ),
            )
        )
    actions.extend(
        (
            HistoryAction("manual", "人工处理"),
            HistoryAction("details", "技术详情"),
        )
    )
    return tuple(actions)


def meeting_history_attention(
    run: MeetingAlignmentRun,
    job: MeetingAlignmentJob,
) -> HistoryAttention | None:
    reason = (
        run.audit_summary
        or run.error
        or job.error
        or "会议任务未完成"
    ).strip()
    reason = _readable_failure_reason(reason)
    if job.status == "retry":
        return HistoryAttention(
            kind="automatic_recovery",
            reason=reason,
            external_effect="未确认发送会议对齐消息",
            retry_attempt=job.attempts,
            retry_limit=DEFAULT_MEETING_MAX_ATTEMPTS,
            retry_at=job.available_at,
            actions=(HistoryAction("details", "技术详情"),),
        )
    if job.status in {"failed", "quarantined"}:
        return HistoryAttention(
            kind="needs_manager",
            reason=reason,
            external_effect="未确认发送会议对齐消息",
            actions=(
                HistoryAction("manual", "人工处理"),
                HistoryAction("details", "技术详情"),
            ),
        )
    return None


def task_history_attention(item: HistoryItem) -> HistoryAttention | None:
    if item.status.strip().lower() != "failed":
        return None
    return HistoryAttention(
        kind="needs_manager",
        reason=_readable_failure_reason(item.output_text.strip() or "任务动作未完成"),
        external_effect="任务记录未确认完成",
        actions=(
            HistoryAction("manual", "人工处理"),
            HistoryAction("details", "技术详情"),
        ),
    )


def _readable_failure_reason(reason: str) -> str:
    normalized = reason.strip()
    return _READABLE_FAILURE_REASONS.get(normalized, normalized)
