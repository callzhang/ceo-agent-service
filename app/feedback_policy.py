from dataclasses import dataclass


FEEDBACK_REQUIRED_LINK_PREFIX = (
    "【评价本次回复】请点 👍 或 👎。提交评价后不再提醒；"
    "若连续多次未评价，后续自动回复可能暂停："
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
