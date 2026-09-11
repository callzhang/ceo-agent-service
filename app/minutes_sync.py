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
_MINUTES_LIST_MAX_PAGES = 100


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


class MinutesSummaryShapeUnknown(ValueError):
    """The summary payload carried content in a shape this archive cannot read."""


def summary_markdown(summary: object) -> str:
    """Return the provider's summary markdown for the archive body.

    The provider returns ``{"fullSummary": "<markdown>"}``.  Serialising that
    object instead of reading it writes a JSON blob with escaped newlines into
    the archive -- a file that looks synced and is unreadable.  An empty
    payload is a minute whose summary is not generated yet, which is a real and
    harmless state; any other shape is a provider contract change and must fail
    the item so it stays visible and gets retried.
    """
    if isinstance(summary, str):
        return summary
    if not summary:
        return ""
    if isinstance(summary, dict):
        text = summary.get("fullSummary")
        if isinstance(text, str):
            return text
    raise MinutesSummaryShapeUnknown(
        f"unreadable minutes summary payload: {sorted(summary)}"
        if isinstance(summary, dict)
        else f"unreadable minutes summary payload of type {type(summary).__name__}"
    )


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
        summary_markdown(summary),
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


def _list_all_minutes(
    dws,
    *,
    archived_ids: set[str],
    permission_pending_ids: set[str],
) -> tuple[list[dict[str, Any]], str]:
    """Read the complete minutes listing, returning a deferred error if partial."""
    list_page = getattr(dws, "list_minutes_page", None)
    if list_page is None:
        return dws.parse_minutes_list(dws.list_minutes()), ""

    items: list[dict[str, Any]] = []
    cursor = ""
    seen_cursors: set[str] = set()
    for _ in range(_MINUTES_LIST_MAX_PAGES):
        try:
            page = list_page(cursor=cursor)
        except Exception as exc:
            return items, str(exc)
        if not isinstance(page, dict):
            return items, "invalid minutes list page"
        page_items = page.get("items")
        if not isinstance(page_items, list):
            return items, "invalid minutes list page items"
        typed_items = [item for item in page_items if isinstance(item, dict)]
        items.extend(typed_items)
        page_ids = {
            str(item.get("taskUuid") or item.get("minutesId") or "").strip()
            for item in typed_items
        }
        page_ids.discard("")
        # The provider orders this listing newest-first. The first known item
        # is the durable boundary from a previous successful pass; older pages
        # cannot contain newer work. This keeps the daily sync incremental and
        # avoids a 100-page walk through already-accounted history.
        if page_ids and page_ids.intersection(archived_ids | permission_pending_ids):
            return items, ""
        has_more = page.get("has_more")
        next_token = str(page.get("next_token") or "")
        if has_more is False:
            return items, ""
        if not next_token:
            return items, "minutes list pagination missing next token"
        if next_token in seen_cursors or next_token == cursor:
            return items, "minutes list pagination repeated next token"
        seen_cursors.add(next_token)
        cursor = next_token
    return items, f"minutes list pagination exceeded {_MINUTES_LIST_MAX_PAGES} pages"


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

    listed, pagination_error = _list_all_minutes(
        dws,
        archived_ids=archived,
        permission_pending_ids=pending,
    )

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
        try:
            body = render_archive(
                task_uuid=task_uuid, summary=summary, paragraphs=paragraphs
            )
        except MinutesSummaryShapeUnknown:
            failed += 1
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        archived.add(task_uuid)
        pending.discard(task_uuid)
        synced += 1

    store.set_daily_scan_state(
        MINUTES_SYNC_SCANNER,
        last_success_at=(
            (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
            if not pagination_error
            else state.get("last_success_at") or ""
        ),
        cursor_json=json.dumps(
            {
                "archived_ids": sorted(archived),
                "permission_pending_ids": sorted(pending),
                **(
                    {
                        "pagination_deferred": True,
                        "pagination_error": pagination_error,
                    }
                    if pagination_error
                    else {}
                ),
            },
            sort_keys=True,
        ),
        last_error=pagination_error,
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
