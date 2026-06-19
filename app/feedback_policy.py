from dataclasses import dataclass


FEEDBACK_REQUIRED_LINK_PREFIX = (
    "【需要反馈】请点下面的 👍 / 👎 评价本次回复；长期不评价会跳过后续自动回复："
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
