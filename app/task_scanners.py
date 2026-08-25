import fnmatch
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.dingtalk_models import DingTalkMessage
from app.store import AutoReplyStore
from app.task_models import WorkItem

LOCAL_FILE_SCANNER = "local_files"
AI_MINUTES_SCANNER = "ai_minutes"
OA_PENDING_SCANNER = "oa_pending"
DEFAULT_LOCAL_FILE_EXCLUDE_PARTS = {
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "AI听记",
    "build",
    "daily frontier report",
    "dist",
    "node_modules",
    "site-packages",
    "venv",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scan_now(now: datetime | None = None) -> datetime:
    return now if now is not None else datetime.now().astimezone()


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


def _has_default_excluded_path_part(path: Path) -> bool:
    return any(
        part in DEFAULT_LOCAL_FILE_EXCLUDE_PARTS
        or part.casefold().startswith("skill-worktrees")
        for part in path.parts
    )


def _local_file_source_ref(path: Path, *, digest: str, size: int) -> str:
    return f"{path}#sha256={digest}:size={size}"


def scan_local_workspace_files(
    store: AutoReplyStore,
    *,
    workspace: Path,
    include_globs: tuple[str, ...] = ("*.md", "*.txt"),
    exclude_globs: tuple[str, ...] = (),
    enqueue_existing_on_first_scan: bool = False,
    max_new_items: int | None = None,
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
    path_refs: dict[str, str] = (
        dict(previous_path_refs) if max_new_items is not None else {}
    )
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
        if _has_default_excluded_path_part(relative):
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
        if max_new_items is not None and count >= max_new_items:
            path_refs.pop(resolved_text, None)
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
    max_new_items: int | None = None,
) -> int:
    list_minutes = getattr(dws, "list_minutes", None)
    list_minutes_page = getattr(dws, "list_minutes_page", None)
    if list_minutes is None and list_minutes_page is None:
        store.set_daily_scan_state(
            AI_MINUTES_SCANNER,
            last_success_at="",
            cursor_json="{}",
            last_error="dws list_minutes unavailable",
        )
        return 0

    state = store.get_daily_scan_state(AI_MINUTES_SCANNER) or {}
    raw_cursor = state.get("cursor_json") or "{}"
    try:
        cursor = json.loads(raw_cursor)
    except json.JSONDecodeError:
        cursor = {}
        raw_cursor = "{}"
    previous_seen_ids = set(str(value) for value in (cursor.get("seen_ids") or []))
    previous_oldest_at = str(cursor.get("oldest_seen_at") or "").strip()

    pagination_error = ""
    try:
        if list_minutes_page is not None:
            minutes_items, oldest_seen_at, pagination_error = (
                _list_incremental_ai_minutes(
                    list_minutes_page,
                    oldest_seen_at=previous_oldest_at,
                    has_prior_cursor=bool(previous_seen_ids or previous_oldest_at),
                )
            )
        else:
            minutes_items = list_minutes()
            oldest_seen_at = _oldest_minutes_item_time(minutes_items)
    except Exception as exc:
        store.set_daily_scan_state(
            AI_MINUTES_SCANNER,
            last_success_at=state.get("last_success_at") or "",
            cursor_json=raw_cursor,
            last_error=str(exc),
        )
        return 0

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
        if minutes_id in previous_seen_ids:
            continue
        if first_scan and not enqueue_existing_on_first_scan:
            seen_ids.add(minutes_id)
            continue
        if max_new_items is not None and count >= max_new_items:
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
        seen_ids.add(minutes_id)
        count += 1

    cursor_state: dict[str, object] = {
        "seen_ids": sorted(seen_ids),
        "oldest_seen_at": oldest_seen_at,
    }
    if pagination_error:
        cursor_state.update(
            {
                "pagination_deferred": True,
                "pagination_error": pagination_error,
            }
        )
    store.set_daily_scan_state(
        AI_MINUTES_SCANNER,
        last_success_at=_utc_now(),
        cursor_json=json.dumps(cursor_state, sort_keys=True),
        last_error="",
    )
    return count


def scan_pending_oa_approvals(
    store: AutoReplyStore,
    dws,
    *,
    now: datetime | None = None,
    lookback_days: int = 365,
    page_size: int = 30,
    max_pages: int = 10,
    max_new_items: int | None = None,
) -> int:
    list_pending = getattr(dws, "list_pending_oa_approvals", None)
    read_tasks = getattr(dws, "read_oa_approval_tasks", None)
    # The DWS OA detail adapter can receive a valid DingTalk response and then
    # fail while decoding it.  The service-owned OA reader returns the original
    # typed process instance and is the single detail source for this workflow.
    read_detail = getattr(dws, "read_oa_process_instance_openapi", None)
    read_records = getattr(dws, "read_oa_approval_records", None)
    if list_pending is None:
        store.set_daily_scan_state(
            OA_PENDING_SCANNER,
            last_success_at="",
            cursor_json="{}",
            last_error="dws list_pending_oa_approvals unavailable",
        )
        return 0
    if read_tasks is None:
        store.set_daily_scan_state(
            OA_PENDING_SCANNER,
            last_success_at="",
            cursor_json="{}",
            last_error="dws read_oa_approval_tasks unavailable",
        )
        return 0
    if read_detail is None:
        store.set_daily_scan_state(
            OA_PENDING_SCANNER,
            last_success_at="",
            cursor_json="{}",
            last_error="DingTalk OA detail reader unavailable",
        )
        return 0

    scan_time = _scan_now(now)
    scan_date = scan_time.date().isoformat()
    scan_timestamp = scan_time.strftime("%Y-%m-%d %H:%M:%S")
    window_start = (scan_time - timedelta(days=lookback_days)).isoformat(
        timespec="seconds"
    )
    window_end = scan_time.isoformat(timespec="seconds")
    approvals = []
    try:
        for page in range(1, max_pages + 1):
            page_items = list_pending(
                page=page,
                size=page_size,
                start=window_start,
                end=window_end,
            )
            approvals.extend(page_items)
            if len(page_items) < page_size:
                break
    except Exception as exc:
        store.set_daily_scan_state(
            OA_PENDING_SCANNER,
            last_success_at="",
            cursor_json="{}",
            last_error=str(exc),
        )
        return 0

    current_user_id = ""
    get_current_user_id = getattr(dws, "get_current_user_id", None)
    if get_current_user_id is not None:
        try:
            current_user_id = str(get_current_user_id() or "")
        except Exception:
            current_user_id = ""

    previous_revisions: dict[str, str] = {}
    previous_state = store.get_daily_scan_state(OA_PENDING_SCANNER)
    if previous_state is not None:
        try:
            cursor = json.loads(previous_state["cursor_json"])
            revisions = cursor.get("process_revisions", {})
            if isinstance(revisions, dict):
                previous_revisions = {
                    str(process_id): str(revision)
                    for process_id, revision in revisions.items()
                }
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    queued = 0
    skipped_missing_task_id: list[str] = []
    read_failures: list[str] = []
    seen_process_ids: set[str] = set()
    queued_process_ids: list[str] = []
    process_revisions: dict[str, str] = {}
    for approval in approvals:
        process_instance_id = str(
            getattr(approval, "process_instance_id", "") or ""
        ).strip()
        if not process_instance_id or process_instance_id in seen_process_ids:
            continue
        seen_process_ids.add(process_instance_id)
        if max_new_items is not None and queued >= max_new_items:
            continue
        try:
            tasks_payload = read_tasks(process_instance_id)
            detail_payload = read_detail(process_instance_id)
        except Exception:
            read_failures.append(process_instance_id)
            continue
        task_id = _pending_oa_task_id_for_current_user(
            {"result": [detail_payload, tasks_payload]},
            current_user_id=current_user_id,
        )
        if not task_id:
            skipped_missing_task_id.append(process_instance_id)
            continue
        records_payload: Any = {}
        if read_records is not None:
            try:
                records_payload = read_records(process_instance_id)
            except Exception:
                pass
        revision = _oa_pending_approval_revision(
            task_id,
            records_payload,
            current_user_id=current_user_id,
        )
        if not revision:
            continue
        process_revisions[process_instance_id] = revision
        if previous_revisions.get(process_instance_id) == revision:
            continue
        title = str(getattr(approval, "title", "") or "").strip()
        process_name = str(getattr(approval, "process_name", "") or "").strip()
        label = title or process_name or process_instance_id
        oa_url = (
            "https://aflow.dingtalk.com/detail?"
            f"procInstId={quote(process_instance_id)}&taskId={quote(task_id)}"
        )
        trigger = DingTalkMessage(
            open_conversation_id="oa_pending_scan",
            open_message_id=f"oa-pending:{process_instance_id}:{revision}",
            conversation_title="审批待办",
            single_chat=True,
            sender_name="Derek OA",
            message_type="text",
            create_time=scan_timestamp,
            content=(
                "审批待办扫描发现新增或有新消息的待处理审批："
                f"{label}\n"
                f"[查看审批]({oa_url})\n"
                "请按钉钉 OA 审批技能（dingtalk-misc 的 references/oa.md）审阅完整审批材料、"
                "历史处理记录和当前节点；"
                "材料不足时评论要求补充，只有规则和证据满足时才执行审批动作。"
            ),
            raw_payload={
                "source": "oa_pending_scan",
                "processInstanceId": process_instance_id,
                "taskId": task_id,
                "processName": process_name,
                "title": title,
            },
        )
        inserted = store.enqueue_reply_task(
            conversation_id=trigger.open_conversation_id,
            conversation_title=trigger.conversation_title,
            single_chat=trigger.single_chat,
            trigger_message_id=trigger.open_message_id,
            trigger_create_time=trigger.create_time,
            trigger_sender=trigger.sender_name,
            trigger_text=trigger.content,
            trigger_message_json=trigger.model_dump_json(),
            oa_url=oa_url,
            channel="dingtalk",
        )
        if inserted:
            queued += 1
            queued_process_ids.append(process_instance_id)

    store.set_daily_scan_state(
        OA_PENDING_SCANNER,
        last_success_at=_utc_now(),
        cursor_json=json.dumps(
            {
                "scan_date": scan_date,
                "window_end": window_end,
                "window_start": window_start,
                "seen_process_instance_ids": sorted(seen_process_ids),
                "queued_process_instance_ids": queued_process_ids,
                "process_revisions": process_revisions,
                "skipped_missing_task_id_process_instance_ids": skipped_missing_task_id,
                "read_failure_process_instance_ids": read_failures,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        # Keep per-process read failures in the cursor. The list scan itself
        # completed, so this is follow-up evidence rather than scanner failure.
        last_error="",
    )
    return queued


def _pending_oa_task_id_for_current_user(
    payload: Any,
    *,
    current_user_id: str = "",
) -> str:
    tasks = _oa_task_records(payload)
    if not tasks:
        return ""
    running_for_current_user: list[str] = []
    running: list[str] = []
    all_task_ids: list[str] = []
    for task in tasks:
        task_id = _oa_task_field(task, ("taskId", "taskid", "task_id", "id"))
        if not task_id:
            continue
        all_task_ids.append(task_id)
        status = _oa_task_field(task, ("status", "taskStatus", "task_status"))
        user_id = _oa_task_field(task, ("userId", "userid", "user_id"))
        is_running = status.upper() == "RUNNING" if status else True
        if is_running:
            running.append(task_id)
        if is_running and current_user_id and user_id == current_user_id:
            running_for_current_user.append(task_id)
    if current_user_id:
        return running_for_current_user[0] if running_for_current_user else ""
    for candidates in (running, all_task_ids):
        if candidates:
            return candidates[0]
    return ""


def _oa_approval_revision(
    task_id: str,
    records_payload: Any,
    *,
    exclude_user_id: str = "",
) -> str:
    records = _oa_operation_records(records_payload)
    if exclude_user_id:
        records = [
            record
            for record in records
            if _oa_task_field(record, ("userId", "userid", "user_id"))
            != exclude_user_id
        ]
    latest_operation = max(
        records,
        key=lambda record: (
            _oa_task_field(record, ("operationTime", "date", "time")),
            _oa_task_field(record, ("operationType", "type")),
            _oa_task_field(record, ("userId", "userid", "user_id")),
        ),
        default={},
    )
    marker = "|".join(
        (
            _oa_task_field(latest_operation, ("operationTime", "date", "time")),
            _oa_task_field(latest_operation, ("operationType", "type")),
            _oa_task_field(latest_operation, ("userId", "userid", "user_id")),
            _oa_task_field(latest_operation, ("operationResult", "result")),
        )
    )
    return hashlib.sha256(f"{task_id}|{marker}".encode()).hexdigest()[:16]


def _oa_pending_approval_revision(
    task_id: str,
    records_payload: Any,
    *,
    current_user_id: str,
) -> str:
    """Return a revision only when the newest OA record still needs review."""
    records = _oa_operation_records(records_payload)
    latest_operation = max(
        records,
        key=lambda record: (
            _oa_task_field(record, ("operationTime", "date", "time")),
            _oa_task_field(record, ("operationType", "type")),
            _oa_task_field(record, ("userId", "userid", "user_id")),
        ),
        default={},
    )
    if current_user_id and _oa_task_field(
        latest_operation, ("userId", "userid", "user_id")
    ) == current_user_id:
        return ""
    return _oa_approval_revision(
        task_id,
        records_payload,
        exclude_user_id=current_user_id,
    )


def _oa_operation_records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        records: list[dict[str, Any]] = []
        nested = value.get("operationRecords")
        if isinstance(nested, list):
            records.extend(item for item in nested if isinstance(item, dict))
        for key in ("result", "data", "process_instance"):
            records.extend(_oa_operation_records(value.get(key)))
        return records
    if isinstance(value, list):
        return [
            record
            for item in value
            for record in _oa_operation_records(item)
        ]
    return []


def _oa_task_records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        records: list[dict[str, Any]] = []
        for key in ("tasks", "taskList", "taskIdList"):
            nested = value.get(key)
            if isinstance(nested, list):
                records.extend(item for item in nested if isinstance(item, dict))
        for key in ("result", "data", "process_instance"):
            records.extend(_oa_task_records(value.get(key)))
        return records
    if isinstance(value, list):
        records: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, dict):
                if any(
                    key in item
                    for key in ("taskId", "taskid", "task_id", "id")
                ):
                    records.append(item)
                records.extend(_oa_task_records(item))
        return records
    return []


def _oa_task_field(task: dict[str, Any], names: tuple[str, ...]) -> str:
    for name in names:
        value = task.get(name)
        if value is not None:
            return str(value).strip()
    return ""


def _minutes_item_time(item: dict) -> str:
    return str(
        item.get("createdAt")
        or item.get("startTimeISO")
        or item.get("startTime")
        or ""
    ).strip()


def _parse_minutes_item_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _oldest_minutes_item_time(items: list[dict]) -> str:
    dated_items = [
        (_parse_minutes_item_time(_minutes_item_time(item)), _minutes_item_time(item))
        for item in items
    ]
    dated_items = [item for item in dated_items if item[0] is not None]
    if not dated_items:
        return ""
    return min(dated_items, key=lambda item: item[0])[1]


def _list_incremental_ai_minutes(
    list_minutes_page,
    *,
    oldest_seen_at: str,
    has_prior_cursor: bool,
) -> tuple[list[dict], str, str]:
    """Read from newest until the durable time boundary, without full rescans."""
    items: list[dict] = []
    cursor = ""
    seen_tokens: set[str] = set()
    boundary = _parse_minutes_item_time(oldest_seen_at)
    completed_pages = 0
    for _ in range(100):
        try:
            page = list_minutes_page(limit=50, cursor=cursor)
        except Exception as exc:
            if not completed_pages:
                raise
            return (
                items,
                oldest_seen_at or _oldest_minutes_item_time(items),
                str(exc),
            )
        completed_pages += 1
        page_items = [item for item in (page.get("items") or []) if isinstance(item, dict)]
        if boundary is None:
            items.extend(page_items)
            page_oldest = _oldest_minutes_item_time(page_items)
            # Existing ID-only cursors and dated first scans establish a durable
            # boundary from one newest page instead of walking stale history.
            if has_prior_cursor or page_oldest:
                return items, page_oldest, ""
        else:
            newer_items = [
                item
                for item in page_items
                if (
                    (item_time := _parse_minutes_item_time(_minutes_item_time(item)))
                    is not None
                    and item_time > boundary
                )
            ]
            items.extend(newer_items)
            if len(newer_items) != len(page_items):
                return items, oldest_seen_at, ""
        cursor = str(page.get("next_token") or "")
        has_more = bool(page.get("has_more"))
        if not has_more or not cursor or cursor in seen_tokens:
            break
        seen_tokens.add(cursor)
    return items, oldest_seen_at or _oldest_minutes_item_time(items), ""
