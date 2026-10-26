"""Read persisted reply actions for focused storage assertions."""

from app.action_storage import REPLY_ATTEMPT_COMMON_SQL
from app.store import AutoReplyStore, ReplyAttempt


def reply_action_snapshot(store: AutoReplyStore) -> list[ReplyAttempt]:
    with store._connect() as db:
        rows = db.execute(
            f"select * from ({REPLY_ATTEMPT_COMMON_SQL}) order by id desc"
        ).fetchall()
    return [ReplyAttempt.model_validate(dict(row)) for row in rows]
