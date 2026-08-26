import json
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
    "image_dependency_unavailable": (
        "图片暂时无法读取；Agent 已继续检查文字和其他材料，若判断确实依赖图片，"
        "将向发信人索要文字关键信息或可查看版本。"
    ),
    "oa_live_evidence_conflict": (
        "审批所需的关键信息暂时不可用，系统没有执行审批动作。"
    ),
    "live_evidence_conflict": (
        "审批所需的关键信息暂时不可用，系统没有执行审批动作。"
    ),
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
    requires_reconciliation_resolution: bool = False,
) -> HistoryAttention | None:
    external_effects = {
        "none": "未执行任何外部动作",
        "confirmed": "已确认产生外部动作",
        "unknown": "执行结果未知，不能安全重放",
    }
    if side_effect_state not in external_effects:
        raise ValueError(f"invalid side effect state: {side_effect_state}")

    status = attempt.send_status.strip().lower()
    persisted_code = attempt.send_error.strip()
    reason = (
        persisted_code
        if persisted_code in _READABLE_FAILURE_REASONS
        else (
            attempt.audit_summary
            or attempt.codex_reason
            or persisted_code
            or "处理未完成"
        )
    ).strip()
    reason = _readable_failure_reason(reason)
    external_effect = external_effects[side_effect_state]

    if (
        task is not None
        and task.manual_rerun_attempt_id == attempt.id
        and task.status in {"pending", "processing"}
    ):
        rerun_reason = (
            "正在按钉钉 OA 审批技能重新读取当前审批；"
            "没有执行同意、拒绝或退回。"
            if attempt.oa_process_instance_id.strip()
            else "正在重新读取当前事项；本轮没有执行外部动作。"
        )
        return HistoryAttention(
            kind="automatic_recovery",
            reason=rerun_reason,
            external_effect="未执行任何外部动作",
            retry_attempt=task.attempts,
            retry_limit=MAX_REPLY_TASK_ATTEMPTS,
            retry_at=task.available_at,
            actions=(HistoryAction("details", "技术详情"),),
        )

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
        if requires_reconciliation_resolution:
            return HistoryAttention(
                kind="needs_manager",
                reason=reason,
                external_effect=external_effect,
                actions=(
                    HistoryAction(
                        "confirmed_occurred",
                        "确认已执行",
                        consequence="确认外部动作已经发生，并结束当前任务。",
                    ),
                    HistoryAction(
                        "confirmed_not_occurred",
                        "确认未执行",
                        consequence="确认外部动作没有发生，并安全重开同一个任务。",
                    ),
                    HistoryAction(
                        "terminate_unrecoverable",
                        "无法确认并停止",
                        consequence="保留审计记录并停止处理，不会自动重放。",
                    ),
                    HistoryAction("details", "技术详情"),
                ),
            )
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
    if item.action.strip().lower().startswith("follow_up_"):
        attention = follow_up_history_attention(
            status=item.status,
            output_text=item.output_text,
        )
        if attention is not None:
            return attention
    return HistoryAttention(
        kind="needs_manager",
        reason=_readable_failure_reason(item.output_text.strip() or "任务动作未完成"),
        external_effect="任务记录未确认完成",
        actions=(
            HistoryAction("manual", "人工处理"),
            HistoryAction("details", "技术详情"),
        ),
    )


def follow_up_history_attention(
    *,
    status: str,
    output_text: str,
) -> HistoryAttention | None:
    if status.strip().lower() != "failed":
        return None
    payload = _json_dict(output_text)
    confirmed_not_sent = (
        str(payload.get("delivery_state") or "").strip().lower()
        in {"not_sent", "failed"}
        or str(payload.get("external_side_effect") or "").strip().lower()
        == "none"
    )
    if not confirmed_not_sent:
        return None
    return HistoryAttention(
        kind="needs_manager",
        reason=_readable_failure_reason(
            str(payload.get("error") or output_text or "发送失败")
        ),
        external_effect="已确认未发送跟进消息",
        actions=(
            HistoryAction(
                "repair_follow_up",
                "让 Agent 重新核验负责人",
                consequence=(
                    "Agent 会核验当前活跃负责人并修复原跟进，"
                    "不会创建新的跟进事项。"
                ),
            ),
            HistoryAction(
                "cancel_follow_up",
                "取消本次跟进",
                consequence="停止原跟进，不发送消息。",
            ),
            HistoryAction("details", "技术详情"),
        ),
    )


def _json_dict(value: str) -> dict[str, object]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _readable_failure_reason(reason: str) -> str:
    normalized = reason.strip()
    return _READABLE_FAILURE_REASONS.get(normalized, normalized)
