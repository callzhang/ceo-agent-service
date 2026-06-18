from dataclasses import dataclass


FEEDBACK_REQUIRED_LINK_PREFIX = "请对我的服务提供反馈，长期不评价将跳过："
FEEDBACK_BLOCK_REPLY_TEXT = "请对我提供反馈后再提问"


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
