from dataclasses import dataclass


FEEDBACK_REQUIRED_LINK_PREFIX = (
    "【请反馈】这次回复有帮助吗？请点 👍 或 👎。收到反馈后将停止提醒；"
    "长期未反馈时，系统可能暂停后续自动回复："
)


@dataclass(frozen=True)
class FeedbackPressureStats:
    unanswered_since_last_feedback: int = 0
    unanswered_older_than_7_days: int = 0
    unanswered_older_than_10_days: int = 0


def requires_feedback_reminder(stats: FeedbackPressureStats) -> bool:
    projected_unanswered = stats.unanswered_since_last_feedback + 1
    return (
        projected_unanswered > 10
        or (
            projected_unanswered > 1
            and stats.unanswered_older_than_7_days > 0
        )
    )


def requires_feedback_block(stats: FeedbackPressureStats) -> bool:
    projected_unanswered = stats.unanswered_since_last_feedback + 1
    return (
        projected_unanswered > 12
        or (
            projected_unanswered > 1
            and stats.unanswered_older_than_10_days > 0
        )
    )
