import fnmatch
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


def _read_text_excerpt(path: Path, limit: int = 6000) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ""
    return text[:limit]


def _is_under_workspace(path: Path, workspace: Path) -> bool:
    try:
        path.relative_to(workspace)
    except ValueError:
        return False
    return True


def scan_local_workspace_files(
    store: AutoReplyStore,
    *,
    workspace: Path,
    include_globs: tuple[str, ...] = ("*.md", "*.txt"),
    exclude_globs: tuple[str, ...] = (),
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
    previous_mtime = float(cursor.get("max_mtime", 0))
    max_mtime = previous_mtime
    count = 0

    for path in sorted(workspace.rglob("*")):
        if not path.is_file():
            continue
        resolved = path.resolve()
        if not _is_under_workspace(resolved, workspace):
            continue
        if exclude_globs and _matches_any(resolved, exclude_globs):
            continue
        if include_globs and not _matches_any(resolved, include_globs):
            continue
        mtime = resolved.stat().st_mtime
        max_mtime = max(max_mtime, mtime)
        if mtime <= previous_mtime:
            continue
        excerpt = _read_text_excerpt(resolved)
        if not excerpt.strip():
            continue
        item = WorkItem.model_validate(
            {
                "source": {
                    "type": "local_file",
                    "ref": str(resolved),
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
        cursor_json=json.dumps({"max_mtime": max_mtime}, sort_keys=True),
        last_error="",
    )
    return count


def scan_ai_minutes(store: AutoReplyStore, dws) -> int:
    list_minutes = getattr(dws, "list_minutes", None)
    if list_minutes is None:
        store.set_daily_scan_state(
            AI_MINUTES_SCANNER,
            last_success_at="",
            cursor_json="{}",
            last_error="dws list_minutes unavailable",
        )
        return 0

    count = 0
    for minutes in list_minutes():
        minutes_id = str(minutes.get("taskUuid") or minutes.get("minutesId") or "")
        if not minutes_id:
            continue
        title = str(minutes.get("title") or f"AI minutes {minutes_id}")
        item = WorkItem.model_validate(
            {
                "source": {
                    "type": "ai_minutes",
                    "ref": minutes_id,
                    "title": title,
                    "created_at": str(minutes.get("createdAt") or ""),
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
        cursor_json="{}",
        last_error="",
    )
    return count
