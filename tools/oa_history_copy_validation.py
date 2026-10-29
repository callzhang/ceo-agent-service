"""Compare existing OA outcomes, quality facts and exact bodies without exporting them."""

from collections import defaultdict
from contextlib import contextmanager
import hashlib
import json

from app import approval_history as current
from app.oa_history_normalization import load_oa_history_source
from app.oa_history_storage import list_recorded_oa_results
from app.reviewed_candidate_store import ReviewedCandidateStoreMixin
from app.schema_sources import oa_history_28_5 as source


class _ExistingVerifiedReads(ReviewedCandidateStoreMixin):
    def __init__(self, db):
        self.db = db

    @contextmanager
    def _connect(self):
        yield self.db


def capture(db, *, normalized):
    """Use the same frozen population and verified ledger for both resolvers."""
    attempts, runs = load_oa_history_source(db)
    previous = db.row_factory
    import sqlite3
    db.row_factory = sqlite3.Row
    try:
        reader = _ExistingVerifiedReads(db)
        verified = [
            action
            for task_id in sorted({r.reply_task_id for group in runs.values() for r in group})
            for action in reader.list_verified_candidate_actions(task_id)
        ]
        recorded = list_recorded_oa_results(db, [a.id for a in attempts]) if normalized else []
        resolver = current if normalized else source
        arguments = {"verified_actions": verified}
        if normalized:
            arguments["recorded_actions"] = recorded
        outcomes = {
            a.id: str(resolver.resolve_approval_history_result(a, runs[a.id], **arguments))
            for a in attempts
        }
        groups = defaultdict(list)
        for attempt in attempts:
            groups[attempt.oa_process_instance_id or f"attempt:{attempt.id}"].append(attempt)
        grouped = {
            key: str(resolver.resolve_approval_history_group_result(group, runs, **arguments))
            for key, group in groups.items()
        }
        quality = {}
        for attempt in attempts:
            if normalized:
                values = [r["outcome_json"] for r in recorded if r["attempt_id"] == attempt.id]
                quality[attempt.id] = any(db.execute(
                    "select case when json_valid(?) then "
                    "json_extract(?,'$.success')=1 and "
                    "upper(coalesce(json_extract(?,'$.taskStatus'),''))='COMPLETED' else 0 end",
                    (value,) * 3,
                ).fetchone()[0] == 1 for value in values)
            else:
                raw = attempt.oa_action_result_json
                quality[attempt.id] = db.execute(
                    "select case when json_valid(?) then coalesce("
                    "json_extract(?,'$.success'),json_extract(?,'$.result.success'),"
                    "json_extract(?,'$.dws_action_result.success'))=1 and upper(coalesce("
                    "json_extract(?,'$.taskStatus'),json_extract(?,'$.result.taskStatus'),''))"
                    "='COMPLETED' else 0 end", (raw,) * 6,
                ).fetchone()[0] == 1
        bodies = {a.id: hashlib.sha256(a.oa_action_result_json.encode()).hexdigest() for a in attempts}
        return {"attempts": outcomes, "groups": grouped, "quality": quality, "bodies": bodies}
    finally:
        db.row_factory = previous


def summary(snapshot):
    """Only population sizes and evidence hashes belong in the acceptance record."""
    return {
        key: {"count": len(value), "sha256": hashlib.sha256(
            json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()}
        for key, value in snapshot.items()
    }
