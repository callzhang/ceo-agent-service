"""One deterministic pass that mirrors DingTalk AI minutes into local files.

This is a service command, not an Agent task: every step is a fixed rule over
what the provider returns.  The one place the retired ``ceo-minutes-sync``
Skill asked for judgement -- whether to request access to a restricted minute
-- is settled here by a duration threshold instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from app.dws_client import DwsError


MINUTES_SYNC_SCANNER = "ai_minutes_sync"

# The archive the existing local minutes already live in, relative to the
# service workspace.
MINUTES_ARCHIVE_DIRECTORY = "AI听记"

# A restricted minute shorter than this is not worth asking its owner for
# access.  Requesting access is an action other people see, so the rule only
# fires when the duration is known and above the threshold.
RESTRICTED_MINUTE_MINIMUM_DURATION = timedelta(minutes=5)

_SOURCE_URL_PREFIX = "https://shanji.dingtalk.com/app/transcribes/"


@dataclass(frozen=True)
class MinutesSyncResult:
    """Every discovered minute lands in exactly one outcome."""

    discovered: int = 0
    synced: int = 0
    skipped: int = 0
    permission_requested: int = 0
    permission_pending: int = 0
    failed: int = 0

    def __post_init__(self) -> None:
        accounted = (
            self.synced
            + self.skipped
            + self.permission_requested
            + self.permission_pending
            + self.failed
        )
        if accounted != self.discovered:
            raise ValueError("minutes sync outcomes do not account for every item")

    def summary(self) -> str:
        return (
            f"discovered={self.discovered} synced={self.synced} "
            f"skipped={self.skipped} "
            f"permission_requested={self.permission_requested} "
            f"permission_pending={self.permission_pending} failed={self.failed}"
        )


def _timecode(offset_ms: object) -> str:
    try:
        total_seconds = max(0, int(offset_ms)) // 1000
    except (TypeError, ValueError):
        total_seconds = 0
    return f"[{total_seconds // 60:02d}:{total_seconds % 60:02d}]"


def _speaker(paragraph: dict[str, Any]) -> str:
    for key in ("nickName", "speakerDisplay"):
        value = str(paragraph.get(key) or "").strip()
        if value:
            return value
    return ""


def render_archive(
    *,
    task_uuid: str,
    summary: object,
    paragraphs: list[dict[str, Any]],
) -> str:
    """Render the archive in the layout the existing local archive already uses."""
    lines = [
        f"<!-- row_key: {task_uuid} -->",
        f"<!-- source_url: {_SOURCE_URL_PREFIX}{task_uuid} -->",
        "",
        "# AI Summary",
        "",
        summary if isinstance(summary, str) else json.dumps(summary, ensure_ascii=False),
        "",
        "# Transcript",
        "",
    ]
    for paragraph in paragraphs:
        text = str(paragraph.get("paragraph") or "").strip()
        if not text:
            continue
        speaker = _speaker(paragraph)
        prefix = f"{_timecode(paragraph.get('startTime'))} "
        lines.append(f"{prefix}{speaker}: {text}" if speaker else f"{prefix}{text}")
    return "\n".join(lines) + "\n"


def _archive_path(archive_dir: Path, *, title: str, started_at: datetime) -> Path:
    # Keep the existing "<title>/<title> <date> <time>.md" layout so new files
    # sit beside the ones already there.
    safe_title = title.replace("/", "-").strip() or "未命名听记"
    stamp = started_at.strftime("%Y-%m-%d %H:%M")
    return archive_dir / safe_title / f"{safe_title} {stamp}.md"


def _started_at(basic: dict[str, Any]) -> datetime:
    raw = basic.get("startTime")
    try:
        return datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc).astimezone()
    except (TypeError, ValueError, OSError):
        return datetime.now(timezone.utc).astimezone()


def _duration(basic: dict[str, Any]) -> timedelta | None:
    raw = basic.get("duration")
    try:
        return timedelta(milliseconds=int(raw))
    except (TypeError, ValueError):
        return None


def should_request_access(duration: timedelta | None) -> bool:
    """Request access only for a restricted minute known to be long enough.

    An unknown duration never triggers a request: the request is visible to the
    minute's owner, so the rule stays silent rather than guessing.
    """
    return duration is not None and duration >= RESTRICTED_MINUTE_MINIMUM_DURATION


def _is_restricted_minute_error(error: DwsError) -> bool:
    """Whether this error means *this minute* is restricted to us.

    Not implemented on purpose.  ``DwsError.needs_authorization`` covers
    PAT_HIGH_RISK_NO_PERMISSION, PAT_MEDIUM_RISK_NO_PERMISSION and
    AGENT_CODE_NOT_EXISTS, which are credential-level failures affecting every
    call -- not per-minute access.  Treating them as "restricted" would fire an
    access request at every minute in the list the moment the credential broke,
    which is exactly the mass-request behaviour the sync must never produce.
    Until the provider's real per-minute denial code is observed and recorded
    here, no error is classified as restricted, so no access request is sent.
    """
    del error
    return False


def sync_minutes_once(
    store,
    dws,
    *,
    archive_dir: Path,
    max_new_items: int | None = None,
    now: datetime | None = None,
) -> MinutesSyncResult:
    """Mirror every not-yet-archived accessible minute into ``archive_dir``."""
    state = store.get_daily_scan_state(MINUTES_SYNC_SCANNER) or {}
    try:
        cursor = json.loads(state.get("cursor_json") or "{}")
    except json.JSONDecodeError:
        cursor = {}
    archived = {str(value) for value in (cursor.get("archived_ids") or [])}
    pending = {str(value) for value in (cursor.get("permission_pending_ids") or [])}

    try:
        listed = dws.parse_minutes_list(dws.list_minutes())
    except DwsError as exc:
        store.set_daily_scan_state(
            MINUTES_SYNC_SCANNER,
            last_success_at=state.get("last_success_at") or "",
            cursor_json=json.dumps(cursor, sort_keys=True),
            last_error=str(exc),
        )
        return MinutesSyncResult()

    candidates: list[str] = []
    for item in listed:
        task_uuid = str(item.get("taskUuid") or item.get("minutesId") or "").strip()
        if task_uuid and task_uuid not in archived and task_uuid not in candidates:
            candidates.append(task_uuid)
    # A minute whose access was requested earlier is retried even once it has
    # dropped off the recent list.
    for task_uuid in sorted(pending):
        if task_uuid not in archived and task_uuid not in candidates:
            candidates.append(task_uuid)
    if max_new_items is not None:
        candidates = candidates[:max_new_items]

    synced = skipped = requested = still_pending = failed = 0
    for task_uuid in candidates:
        try:
            basic_payload = dws.get_minutes_info(task_uuid)
            basic = (basic_payload or {}).get("result") or {}
            summary_payload = dws.get_minutes_summary(task_uuid)
            summary = (summary_payload or {}).get("result") or {}
            paragraphs = (
                dws.get_all_minutes_transcription(task_uuid) or {}
            ).get("paragraphs") or []
        except DwsError as exc:
            if not _is_restricted_minute_error(exc):
                failed += 1
                continue
            duration = _read_duration_for_restricted(dws, task_uuid)
            if not should_request_access(duration):
                skipped += 1
                pending.discard(task_uuid)
                continue
            # Reaching here needs a verified per-minute denial code; see
            # _is_restricted_minute_error.  Until then the item is recorded for
            # a later pass rather than mailing its owner a request.
            still_pending += 1
            pending.add(task_uuid)
            continue

        if not paragraphs:
            failed += 1
            continue
        path = _archive_path(
            archive_dir,
            title=str(basic.get("title") or task_uuid),
            started_at=_started_at(basic),
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            render_archive(
                task_uuid=task_uuid, summary=summary, paragraphs=paragraphs
            ),
            encoding="utf-8",
        )
        archived.add(task_uuid)
        pending.discard(task_uuid)
        synced += 1

    store.set_daily_scan_state(
        MINUTES_SYNC_SCANNER,
        last_success_at=(now or datetime.now(timezone.utc)).astimezone(
            timezone.utc
        ).isoformat(),
        cursor_json=json.dumps(
            {
                "archived_ids": sorted(archived),
                "permission_pending_ids": sorted(pending),
            },
            sort_keys=True,
        ),
        last_error="",
    )
    return MinutesSyncResult(
        discovered=len(candidates),
        synced=synced,
        skipped=skipped,
        permission_requested=requested,
        permission_pending=still_pending,
        failed=failed,
    )


def _read_duration_for_restricted(dws, task_uuid: str) -> timedelta | None:
    """Read only the basic envelope, which may survive a restricted transcript."""
    try:
        basic = (dws.get_minutes_info(task_uuid) or {}).get("result") or {}
    except DwsError:
        return None
    return _duration(basic)
