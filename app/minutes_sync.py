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
    """Return the provider's summary as the markdown this archive is written in.

    ``fullSummary`` carries one of two things: the markdown itself, or a JSON
    insight report (``meta_info``/``overview``/``menu``/``details``).  Writing
    the report verbatim puts a JSON document under "# AI Summary" -- a file
    that looks synced and cannot be read -- so it is rendered into the same
    markdown the rest of the archive uses.  An empty payload is a minute whose
    summary the provider has not generated, which is a real state; any other
    shape is a contract change and must fail the item so it stays visible.
    """
    if isinstance(summary, str):
        return _rendered_summary_document(summary)
    if not summary:
        return ""
    if isinstance(summary, dict):
        text = summary.get("fullSummary")
        if isinstance(text, str):
            return _rendered_summary_document(text)
    raise MinutesSummaryShapeUnknown(
        f"unreadable minutes summary payload: {sorted(summary)}"
        if isinstance(summary, dict)
        else f"unreadable minutes summary payload of type {type(summary).__name__}"
    )


def _rendered_summary_document(text: str) -> str:
    """Render an insight-report document; leave plain markdown untouched."""
    stripped = text.strip()
    if not stripped.startswith("{"):
        return text
    try:
        document = json.loads(stripped)
    except json.JSONDecodeError:
        return text
    if not isinstance(document, dict):
        return text
    if "meta_info" not in document:
        # A JSON summary in a shape no rule here reads would otherwise be
        # written verbatim, which is the unreadable archive this function
        # exists to prevent.
        raise MinutesSummaryShapeUnknown(
            f"unreadable minutes summary document: {sorted(document)[:6]}"
        )
    meta = document.get("meta_info")
    meta = meta if isinstance(meta, dict) else {}
    lines: list[str] = []
    title = str(meta.get("title") or "").strip()
    subtitle = str(meta.get("subtitle") or "").strip()
    if title:
        lines.append(f"> **主题**: {title}")
    if subtitle and subtitle != title:
        lines.append(f"> **议题**: {subtitle}")
    started = _summary_start_time(meta.get("minutes_start_time"))
    if started:
        lines.append(f"> **时间**: {started}")
    tags = meta.get("tags")
    if isinstance(tags, list):
        joined = ", ".join(str(tag).strip() for tag in tags if str(tag).strip())
        if joined:
            lines.append(f"> **标签**: {joined}")
    overview = str(document.get("overview") or meta.get("overview") or "").strip()
    if overview:
        lines.extend(["", overview])
    for section in _summary_sections(document):
        lines.extend(["", *section])
    return "\n".join(lines).strip()


def _summary_start_time(value: object) -> str:
    try:
        moment = datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return ""
    return moment.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _summary_sections(document: dict[str, Any]) -> list[list[str]]:
    """One section per slice, in the order the provider returned them."""
    details = {
        str(entry.get("slice_id") or ""): entry.get("detail_json")
        for entry in document.get("details") or []
        if isinstance(entry, dict)
    }
    sections: list[list[str]] = []
    menu = document.get("menu")
    entries = menu if isinstance(menu, list) else []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        heading = str(entry.get("title") or "").strip()
        body: list[str] = []
        if heading:
            body.append(f"## {heading}")
        summary_line = str(entry.get("summary") or "").strip()
        if summary_line:
            body.extend(["", summary_line])
        detail = details.get(str(entry.get("slice_id") or ""))
        if isinstance(detail, dict):
            rendered = _summary_blocks(detail.get("blocks"), depth=0)
            if rendered:
                body.extend(["", *rendered])
        if body:
            sections.append(body)
    return sections


def _summary_blocks(blocks: object, *, depth: int) -> list[str]:
    """Render the provider's block tree, keeping text no rule anticipated."""
    lines: list[str] = []
    if isinstance(blocks, str):
        text = blocks.strip()
        return [text] if text else []
    if isinstance(blocks, list):
        for block in blocks:
            lines.extend(_summary_blocks(block, depth=depth))
        return lines
    if not isinstance(blocks, dict):
        return lines
    title = str(blocks.get("title") or "").strip()
    kind = str(blocks.get("type") or "")
    if kind == "module" and title:
        lines.append(f"{'#' * min(depth + 3, 6)} {title}")
        lines.append("")
    elif title:
        lines.append(f"- **{title}**")
    for item in _summary_text_items(blocks.get("content")):
        lines.append(f"    - {item}" if title and kind != "module" else item)
        if not (title and kind != "module"):
            lines.append("")
    for key in ("children", "items", "blocks"):
        nested = blocks.get(key)
        if nested is not None:
            lines.extend(
                _summary_blocks(nested, depth=depth + (1 if kind == "module" else 0))
            )
    return [line for index, line in enumerate(lines) if line or index]


def _summary_text_items(content: object) -> list[str]:
    if isinstance(content, str):
        text = content.strip()
        return [text] if text else []
    if isinstance(content, list):
        items: list[str] = []
        for entry in content:
            items.extend(_summary_text_items(entry))
        return items
    return []


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
