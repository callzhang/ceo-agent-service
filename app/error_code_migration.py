"""Explicit, reviewable migration of legacy current error projections.

The migration never edits meeting run history.  It only replaces the current
job projection and embeds the old value as ``legacy_code`` in that projection
so the historical run rows remain the source of truth for what happened.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProjectionMigrationResult:
    scanned: int
    changed: int
    dry_run: bool


_LEGACY_MEETING_MESSAGES = {
    "runtime_effect_policy_violation": (
        "runtime_execution_failed",
        "result",
        "runtime",
    ),
    "runtime_attempt_active": (
        "runtime_session_conflict",
        "connect",
        "runtime",
    ),
    "multi-party meeting has no sendable group": (
        "provider_target_failed",
        "target",
        "dingtalk",
    ),
}


def migrate_legacy_meeting_projections(
    store: Any, *, dry_run: bool = True
) -> ProjectionMigrationResult:
    """Migrate legacy meeting job errors without rewriting run history.

    Only failed meeting jobs whose JSON error contains one of the known legacy
    messages are changed.  The original ``meeting_alignment_runs.error`` rows
    are never updated.  ``dry_run`` reports the exact number of eligible rows
    without writing anything.
    """

    with store._connect() as db:
        rows = db.execute(
            "select id, error from meeting_alignment_jobs where status='failed'"
        ).fetchall()
        scanned = 0
        updates: list[tuple[str, int]] = []
        for row in rows:
            try:
                payload = json.loads(str(row["error"] or ""))
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            message = str(payload.get("message") or "").strip()
            mapping = _LEGACY_MEETING_MESSAGES.get(message)
            if mapping is None:
                continue
            scanned += 1
            code, stage, source = mapping
            migrated = {
                **payload,
                "kind": code,
                "code": code,
                "stage": stage,
                "source": source,
                "source_code": message,
                "retryable": True,
                "legacy_code": payload.get("kind") or message,
            }
            updates.append((json.dumps(migrated, ensure_ascii=False), int(row["id"])))
        if not dry_run:
            db.executemany(
                "update meeting_alignment_jobs set error=?, updated_at=current_timestamp where id=?",
                updates,
            )
        return ProjectionMigrationResult(
            scanned=scanned,
            changed=len(updates),
            dry_run=dry_run,
        )
