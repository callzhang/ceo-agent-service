"""Compare History's reply keys with the published predicate on a read-only copy.

This is an inspectable adaptation of the method run interactively on 2026-10-14.
It reads the original
public projections, computes both published NOT EXISTS conditions in memory,
then compares every visible public ID with the candidate branch. No row body or
private ID is written to disk or printed.
"""

import hashlib
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

from app.action_storage import REPLY_ATTEMPT_COMMON_SQL
from app.run_storage import AGENT_RUN_SQL
from app.store import AutoReplyStore
from app.task_storage import REPLY_TASK_SQL


PUBLISHED_COMMIT = "2773d6f43518b64c85f1d4bed9d520bd5563df07"
SOURCE = Path.home() / "Library/Application Support/ceo-agent-service/auto-reply.sqlite3"
MEETING_BRANCH = "\n                union all\n                select\n                    'meeting:"


def latest_predicate(source: str) -> str:
    before_meeting = source.split(MEETING_BRANCH, 1)[0]
    predicate = before_meeting[before_meeting.rfind("                  and not exists ("):]
    return predicate.replace("{AGENT_RUN_SQL}", AGENT_RUN_SQL).replace(
        "{REPLY_ATTEMPT_COMMON_SQL}", REPLY_ATTEMPT_COMMON_SQL
    )


def main() -> None:
    current_code = Path("app/store.py").read_text()
    published_code = subprocess.check_output(
        ["git", "show", f"{PUBLISHED_COMMIT}:app/store.py"], text=True
    )
    current_predicate = latest_predicate(current_code)
    published_predicate = latest_predicate(published_code)
    assert "latest_task_attempt" in current_predicate
    assert "newer_attempt" in published_predicate

    with tempfile.TemporaryDirectory(prefix="history-query-proof-", dir="/tmp") as directory:
        copied = Path(directory) / "copy.sqlite3"
        source_db = sqlite3.connect(f"file:{SOURCE}?mode=ro", uri=True)
        copy_db = sqlite3.connect(copied)
        source_db.backup(copy_db)
        copy_db.close()
        source_db.close()

        db = sqlite3.connect(f"file:{copied}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        db.execute("pragma query_only=on")
        store = AutoReplyStore.__new__(AutoReplyStore)
        store._email_actions_history_tables_seen = bool(
            db.execute("select 1 from sqlite_master where name='email_actions'").fetchone()
        )
        source_sql = f"""
            select a.id as attempt_id, a.agent_run_id,
                   r.id as run_id, r.reply_task_id as task_id,
                   r.execution_generation as run_generation,
                   t.id as task_id_found, t.execution_generation as task_generation
            from ({REPLY_ATTEMPT_COMMON_SQL}) a
            left join ({AGENT_RUN_SQL}) r on r.id=a.agent_run_id
            left join ({REPLY_TASK_SQL}) t on t.id=r.reply_task_id
        """
        source_rows = list(db.execute(source_sql))

        # Original newer side includes every run generation and requires a
        # real run/task ID equality. SQL NULL never equals another NULL.
        maxima = {}
        for row in source_rows:
            if row["run_id"] is not None and row["task_id"] is not None:
                maxima[row["task_id"]] = max(
                    maxima.get(row["task_id"], row["attempt_id"]), row["attempt_id"]
                )
        expected = sorted(
            row["attempt_id"] for row in source_rows
            if not (
                row["task_id_found"] is not None
                and row["run_generation"] is not None
                and row["task_generation"] is not None
                and row["run_generation"] != row["task_generation"]
            )
            and not (
                row["run_id"] is not None
                and row["task_id"] is not None
                and maxima[row["task_id"]] > row["attempt_id"]
            )
        )

        candidate = store._operation_logs_base_query(("reply_attempts",))
        assert candidate.count(current_predicate) == 1
        started = time.monotonic()
        actual = [
            row[0] for row in db.execute(
                "select source_id from (" + candidate + ") order by source_id"
            )
        ]
        key_seconds = time.monotonic() - started
        assert actual == expected
        plan = [
            row[3] for row in db.execute(
                "explain query plan select source_id from (" + candidate + ")"
            )
        ]
        materializations = sum("MATERIALIZE latest_task_attempt" in node for node in plan)
        assert materializations == 1

        picks = sorted(set([
            expected[0], expected[len(expected) // 4],
            expected[len(expected) // 2], expected[-1], *expected[-12:]
        ]))
        subset = store._operation_logs_base_query(("reply_attempts",), picks)
        assert subset.count(current_predicate) == 1
        published = subset.replace(current_predicate, published_predicate)
        complete_rows = []
        for query in (published, subset):
            started = time.monotonic()
            db.set_progress_handler(lambda: time.monotonic() - started > 15, 10000)
            try:
                complete_rows.append([
                    tuple(row) for row in db.execute(
                        "select * from (" + query + ") order by source_id"
                    )
                ])
            finally:
                db.set_progress_handler(None, 0)
        assert complete_rows[0] == complete_rows[1]

        digest = hashlib.sha256(",".join(map(str, actual)).encode()).hexdigest()
        print(
            f"source_attempts={len(source_rows)} visible={len(actual)} "
            f"key_set_equal=true id_sha256={digest} "
            f"materializations={materializations} key_seconds={key_seconds:.3f} "
            f"selected_complete_rows={len(complete_rows[0])} complete_rows_equal=true"
        )
        db.close()


if __name__ == "__main__":
    main()
