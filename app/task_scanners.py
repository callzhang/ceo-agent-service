import fnmatch
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from app.store import AutoReplyStore
from app.task_models import WorkItem

LOCAL_FILE_SCANNER = "local_files"
AI_MINUTES_SCANNER = "ai_minutes"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _matches_any(path: Path, patterns: tuple[str, ...]) -> bool:
    text = str(path)
    name = path.name
    return any(
        fnmatch.fnmatch(text, pattern) or fnmatch.fnmatch(name, pattern)
        for pattern in patterns
    )


def _read_text_excerpt_and_digest(path: Path, limit: int = 6000) -> tuple[str, str]:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return "", ""
    return text[:limit], hashlib.sha256(raw).hexdigest()


def _is_under_workspace(path: Path, workspace: Path) -> bool:
    try:
        path.relative_to(workspace)
    except ValueError:
        return False
    return True


def _has_hidden_path_part(path: Path) -> bool:
    return any(part.startswith(".") for part in path.parts)


def _local_file_source_ref(path: Path, *, digest: str, size: int) -> str:
    return f"{path}#sha256={digest}:size={size}"


def scan_local_workspace_files(
    store: AutoReplyStore,
    *,
    workspace: Path,
    include_globs: tuple[str, ...] = ("*.md", "*.txt"),
    exclude_globs: tuple[str, ...] = (),
    enqueue_existing_on_first_scan: bool = False,
) -> int:
    workspace = workspace.expanduser().resolve()
    if not workspace.exists() or not workspace.is_dir():
        store.set_daily_scan_state(
            LOCAL_FILE_SCANNER,
            last_success_at="",
            cursor_json="{}",
            last_error=f"workspace missing: {workspace}",
        )
        return 0

    state = store.get_daily_scan_state(LOCAL_FILE_SCANNER) or {}
    try:
        cursor = json.loads(state.get("cursor_json") or "{}")
    except json.JSONDecodeError:
        cursor = {}
    previous_path_refs = dict(cursor.get("path_refs") or {})
    first_scan = not previous_path_refs
    path_refs: dict[str, str] = {}
    count = 0

    for path in sorted(workspace.rglob("*")):
        if not path.is_file():
            continue
        resolved = path.resolve()
        if not _is_under_workspace(resolved, workspace):
            continue
        relative = resolved.relative_to(workspace)
        if _has_hidden_path_part(relative):
            continue
        if exclude_globs and _matches_any(resolved, exclude_globs):
            continue
        if include_globs and not _matches_any(resolved, include_globs):
            continue
        stat = resolved.stat()
        mtime = stat.st_mtime
        resolved_text = str(resolved)
        excerpt, digest = _read_text_excerpt_and_digest(resolved)
        if not excerpt.strip():
            continue
        source_ref = _local_file_source_ref(
            resolved,
            digest=digest,
            size=stat.st_size,
        )
        path_refs[resolved_text] = source_ref
        if previous_path_refs.get(resolved_text) == source_ref:
            continue
        if first_scan and not enqueue_existing_on_first_scan:
            continue
        item = WorkItem.model_validate(
            {
                "source": {
                    "type": "local_file",
                    "ref": source_ref,
                    "title": resolved.name,
                    "created_at": datetime.fromtimestamp(
                        mtime,
                        timezone.utc,
                    ).isoformat(),
                },
                "summary": excerpt,
                "project_name": resolved.stem,
                "context": {
                    "sender": "",
                    "participants": [],
                    "source_conversation_kind": "file",
                    "source_conversation_title": resolved.name,
                },
            }
        )
        store.enqueue_work_summary_input(
            source_type=item.source.type.value,
            source_ref=item.source.ref,
            payload_json=item.model_dump_json(),
        )
        count += 1

    store.set_daily_scan_state(
        LOCAL_FILE_SCANNER,
        last_success_at=_utc_now(),
        cursor_json=json.dumps(
            {
                "path_refs": path_refs,
            },
            sort_keys=True,
        ),
        last_error="",
    )
    return count


def scan_ai_minutes(
    store: AutoReplyStore,
    dws,
    *,
    enqueue_existing_on_first_scan: bool = False,
) -> int:
    list_minutes = getattr(dws, "list_minutes", None)
    if list_minutes is None:
        store.set_daily_scan_state(
            AI_MINUTES_SCANNER,
            last_success_at="",
            cursor_json="{}",
            last_error="dws list_minutes unavailable",
        )
        return 0

    list_minutes_page = getattr(dws, "list_minutes_page", None)
    try:
        if list_minutes_page is not None:
            minutes_items = _list_all_ai_minutes(list_minutes_page)
        else:
            minutes_items = list_minutes()
    except Exception as exc:
        store.set_daily_scan_state(
            AI_MINUTES_SCANNER,
            last_success_at="",
            cursor_json="{}",
            last_error=str(exc),
        )
        return 0

    state = store.get_daily_scan_state(AI_MINUTES_SCANNER) or {}
    try:
        cursor = json.loads(state.get("cursor_json") or "{}")
    except json.JSONDecodeError:
        cursor = {}
    previous_seen_ids = set(str(value) for value in (cursor.get("seen_ids") or []))
    first_scan = not previous_seen_ids
    seen_ids = set(previous_seen_ids)
    count = 0
    for minutes in minutes_items:
        minutes_id = str(
            minutes.get("taskUuid")
            or minutes.get("minutesId")
            or minutes.get("id")
            or minutes.get("task_uuid")
            or minutes.get("uuid")
            or ""
        )
        if not minutes_id:
            continue
        seen_ids.add(minutes_id)
        if minutes_id in previous_seen_ids:
            continue
        if first_scan and not enqueue_existing_on_first_scan:
            continue
        title = str(minutes.get("title") or f"AI minutes {minutes_id}")
        item = WorkItem.model_validate(
            {
                "source": {
                    "type": "ai_minutes",
                    "ref": minutes_id,
                    "title": title,
                    "created_at": str(
                        minutes.get("createdAt")
                        or minutes.get("startTimeISO")
                        or minutes.get("startTime")
                        or ""
                    ),
                },
                "summary": json.dumps(minutes, ensure_ascii=False),
                "project_name": title,
                "context": {
                    "sender": "",
                    "participants": [],
                    "source_conversation_kind": "minutes",
                    "source_conversation_title": title,
                },
            }
        )
        store.enqueue_work_summary_input(
            source_type=item.source.type.value,
            source_ref=item.source.ref,
            payload_json=item.model_dump_json(),
        )
        count += 1

    store.set_daily_scan_state(
        AI_MINUTES_SCANNER,
        last_success_at=_utc_now(),
        cursor_json=json.dumps(
            {"seen_ids": sorted(seen_ids)},
            sort_keys=True,
        ),
        last_error="",
    )
    return count


def _list_all_ai_minutes(list_minutes_page) -> list[dict]:
    items: list[dict] = []
    next_token = ""
    seen_tokens: set[str] = set()
    for _ in range(100):
        page = list_minutes_page(max_results=50, next_token=next_token)
        page_items = page.get("items") or []
        items.extend(item for item in page_items if isinstance(item, dict))
        next_token = str(page.get("next_token") or "")
        has_more = bool(page.get("has_more"))
        if not has_more or not next_token or next_token in seen_tokens:
            break
        seen_tokens.add(next_token)
    return items
