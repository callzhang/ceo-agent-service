#!/usr/bin/env python3
"""Recover failed OKR review attempts by reusing their original triggers."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.dws_client import DwsClient  # noqa: E402
from app.okr_review import DwsLiveOkrSource, requested_okr_period  # noqa: E402
from app.store import AutoReplyStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--attempt-id", action="append", type=int, required=True)
    parser.add_argument("--today", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=90)
    args = parser.parse_args()

    db_path = Path(args.db)
    store = AutoReplyStore(db_path)
    dws = DwsClient(transient_retry_attempts=2, transient_retry_delay_seconds=1.0)
    source = DwsLiveOkrSource(
        dws=dws,
        command_template=[
            str(Path(__file__).resolve().parent / "dingteam_okr_live_source.py"),
            "--user-id",
            "{user_id}",
            "--period-label",
            "{period_label}",
        ],
        max_attempts=2,
        timeout_seconds=args.timeout_seconds,
    )

    rows = _load_attempts(db_path, args.attempt_id)
    for row in rows:
        user_id = _extract_user_id(row["send_error"], row["id"], row["source_errors"])
        period = requested_okr_period(row["trigger_text"], today=args.today)
        okr_payload = source.fetch_user_okr(
            user_id=user_id,
            period_label=period.period_label,
        )
        request_id = store.create_okr_review_request(
            conversation_id=row["conversation_id"],
            conversation_title=row["conversation_title"],
            trigger_message_id=row["trigger_message_id"],
            trigger_sender=row["trigger_sender"],
            trigger_sender_user_id=user_id,
            trigger_text=row["trigger_text"],
            period_label=period.period_label,
            period_start=period.period_start,
            period_end=period.period_end,
            okr_source_json=json.dumps(okr_payload, ensure_ascii=False),
        )
        store.update_reply_attempt(
            row["id"],
            action="okr_review",
            send_status="skipped",
            send_error="",
            final_reply_text="",
        )
        _record_attempt_recovery_reason(
            db_path,
            row["id"],
            (
                f"recovered: queued OKR review request {request_id} "
                f"using period {period.period_label}"
            ),
        )
        store.record_error(
            row["conversation_id"],
            row["trigger_message_id"],
            "okr_review_recovered",
            (
                f"recovered failed OKR review attempt {row['id']} "
                f"as request {request_id} with period {period.period_label}"
            ),
        )
        print(
            "queued "
            f"attempt={row['id']} request={request_id} "
            f"sender={row['trigger_sender']} period={period.period_label}",
            flush=True,
        )
    return 0


def _load_attempts(db_path: Path, attempt_ids: list[int]) -> list[sqlite3.Row]:
    placeholders = ",".join("?" for _ in attempt_ids)
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        return db.execute(
            f"""
            select id,
                   conversation_id,
                   conversation_title,
                   trigger_message_id,
                   trigger_sender,
                   trigger_text,
                   send_error,
                   (
                       select group_concat(detail, char(10))
                       from errors
                       where message_id = reply_attempts.trigger_message_id
                         and detail like '%dingteam_okr_live_source.py --user-id%'
                   ) as source_errors
            from reply_attempts
            where id in ({placeholders})
            order by id
            """,
            attempt_ids,
        ).fetchall()


def _extract_user_id(send_error: str, attempt_id: int, *fallback_sources: str) -> str:
    for source in (send_error, *fallback_sources):
        tokens = (source or "").replace(";", " ").split()
        for index, token in enumerate(tokens):
            if token == "--user-id" and index + 1 < len(tokens):
                return tokens[index + 1]
    raise RuntimeError(f"missing OKR user id in attempt {attempt_id}")


def _record_attempt_recovery_reason(
    db_path: Path,
    attempt_id: int,
    reason: str,
) -> None:
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            update reply_attempts
            set codex_reason=?,
                updated_at=current_timestamp
            where id=?
            """,
            (reason, attempt_id),
        )


if __name__ == "__main__":
    raise SystemExit(main())
