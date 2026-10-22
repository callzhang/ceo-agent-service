import json
from datetime import datetime, timezone
from typing import Any

from app.dws_client import DwsError
from app.store import AutoReplyStore
from app.task_models import ProjectPriority
from app.todo_completion import complete_business_task_from_external_todo

DINGTALK_TODO_TITLE_LIMIT = 80


def _parse_datetime(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    candidates = [text]
    if text.endswith("Z"):
        candidates.append(f"{text[:-1]}+00:00")
    for candidate in candidates:
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            pass
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _deadline_to_iso(value: str) -> str:
    deadline = _parse_datetime(value)
    if deadline is None:
        return ""
    if deadline.tzinfo is None:
        return f"{deadline.isoformat()}+08:00"
    return deadline.isoformat()


def _committed_deadline(store: AutoReplyStore, business_task_id: int) -> str:
    values = [
        fact.value_at
        for fact in store.list_business_task_date_evidence(business_task_id)
        if fact.date_type.value == "committed_deadline_at" and _deadline_to_iso(fact.value_at)
    ]
    return values[-1] if values else ""


def _priority_to_dingtalk(priority: str) -> int:
    priorities = {
        ProjectPriority.P0.value: 40,
        ProjectPriority.P1.value: 30,
        ProjectPriority.P2.value: 20,
        ProjectPriority.NONE.value: 20,
    }
    return priorities.get((priority or "").strip(), 20)


def _payload_candidate_dicts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    values = [payload]
    result = payload.get("result")
    if isinstance(result, dict):
        values.append(result)
        detail = result.get("todoDetailModel")
        if isinstance(detail, dict):
            values.append(detail)
    return values


def _payload_task_id(payload: dict[str, Any]) -> str:
    values = _payload_candidate_dicts(payload)
    for item in values:
        for key in ("todoTaskId", "taskId", "task_id", "id"):
            value = item.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
    return ""


def _payload_done_value(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "done", "completed"}:
            return True
        if normalized in {"false", "open", "pending", "active"}:
            return False
    return None


def _payload_done(payload: dict[str, Any]) -> bool:
    values = _payload_candidate_dicts(payload)
    for item in values:
        for key in ("done", "isDone", "completed", "isCompleted", "status"):
            parsed = _payload_done_value(item.get(key))
            if parsed is not None:
                return parsed
    return False


def _normalize_inline_text(value: str) -> str:
    return " ".join((value or "").split())


def _trim_inline_text(value: str, limit: int) -> str:
    text = _normalize_inline_text(value)
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)]}…"


def _json_list(value: str) -> list:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _dingtalk_todo_description(todo: Any, project: Any = None) -> str:
    parts: list[str] = []
    if project is not None:
        project_title = _normalize_inline_text(str(getattr(project, "title", "") or ""))
        if project_title:
            parts.append(f"项目：{project_title}")
        for label, field in (
            ("项目背景", "background"),
            ("当前状态", "current_state"),
            ("下一步", "next_step"),
            ("阻塞", "blocker"),
        ):
            value = _normalize_inline_text(str(getattr(project, field, "") or ""))
            if value:
                parts.append(f"{label}：{value}")
    for label, field in (
        ("待办", "title"),
        ("待办背景", "description"),
        ("完成标准/确认问题", "follow_up_question"),
        ("待办阻塞", "blocker"),
        ("截止时间", "deadline_at"),
    ):
        value = _normalize_inline_text(str(getattr(todo, field, "") or ""))
        if value:
            parts.append(f"{label}：{value}")
    owner = _normalize_inline_text(str(getattr(todo, "owner_name", "") or ""))
    if owner:
        parts.append(f"负责人：{owner}")
    priority = _normalize_inline_text(str(getattr(todo, "priority", "") or ""))
    if priority:
        parts.append(f"优先级：{priority}")
    return "\n".join(dict.fromkeys(parts))


def _dingtalk_todo_participants(todo: Any, project: Any = None) -> list[dict[str, str]]:
    participants: list[dict[str, str]] = []
    owner_user_id = str(getattr(todo, "owner_user_id", "") or "").strip()
    owner_name = str(getattr(todo, "owner_name", "") or "").strip()
    if owner_user_id:
        participants.append(
            {"user_id": owner_user_id, "name": owner_name, "role": "owner"}
        )
    if project is not None:
        for item in _json_list(project.related_people_json):
            if not isinstance(item, dict):
                continue
            user_id = str(item.get("user_id") or "").strip()
            name = str(item.get("name") or "").strip()
            role = str(item.get("role") or "").strip()
            if user_id:
                participants.append(
                    {"user_id": user_id, "name": name, "role": role}
                )
    unique: dict[str, dict[str, str]] = {}
    for item in participants:
        unique.setdefault(item["user_id"], item)
    return list(unique.values())


def maybe_create_dingtalk_todo(
    store: AutoReplyStore,
    dws: Any,
    *,
    business_task_id: int,
    now: str,
):
    task = store.get_business_task(business_task_id)
    if task is None or task.status.value == "merged":
        return None
    if (
        task.stage.value != "formal"
        or task.status.value not in {"open", "waiting"}
        or task.commitment_status.value != "accepted"
        or not task.owner_user_id.strip()
        or not task.title.strip()
        or not _committed_deadline(store, business_task_id)
    ):
        return None
    link = store.get_active_business_task_dingtalk_link(business_task_id)
    if link is not None:
        if link["status"] == "active" or not link["dingtalk_task_id"]:
            return link
        try:
            readback = dws.get_todo_task(link["dingtalk_task_id"])
            store.update_business_task_dingtalk_link(
                link["id"], status="active",
                last_dingtalk_done=_payload_done(readback),
                last_dingtalk_payload_json=json.dumps(readback, ensure_ascii=False),
                last_pull_at=now, last_error="",
            )
        except (DwsError, RuntimeError) as exc:
            store.update_business_task_dingtalk_link(link["id"], last_error=str(exc))
        return store.get_business_task_dingtalk_link(link["id"])
    link_id, created = store.claim_business_task_dingtalk_link(
        business_task_id=business_task_id,
        executor_user_id=task.owner_user_id,
        executor_name=task.owner_name,
        title_snapshot=_trim_inline_text(task.title, DINGTALK_TODO_TITLE_LIMIT),
        deadline_at_snapshot=_committed_deadline(store, business_task_id),
        priority_snapshot="P2",
        status="creating",
    )
    if not created:
        return store.get_business_task_dingtalk_link(link_id)
    try:
        payload = dws.create_todo_task(
            title=_trim_inline_text(task.title, DINGTALK_TODO_TITLE_LIMIT),
            executor_user_id=task.owner_user_id,
            due=_deadline_to_iso(_committed_deadline(store, business_task_id)),
            priority=_priority_to_dingtalk("P2"),
            description=_dingtalk_todo_description(task),
            tags=[],
            participants=_dingtalk_todo_participants(task),
            files=[],
        )
        dingtalk_task_id = _payload_task_id(payload)
        if not dingtalk_task_id:
            raise RuntimeError("DingTalk todo create response did not include task id")
        store.update_business_task_dingtalk_link(
            link_id, dingtalk_task_id=dingtalk_task_id, last_push_at=now
        )
        readback = dws.get_todo_task(dingtalk_task_id)
        store.update_business_task_dingtalk_link(
            link_id, dingtalk_task_id=dingtalk_task_id, status="active",
            last_dingtalk_done=_payload_done(readback),
            last_dingtalk_payload_json=json.dumps(readback, ensure_ascii=False),
            last_push_at=now, last_pull_at=now, last_error="",
        )
    except (DwsError, RuntimeError) as exc:
        persisted = store.get_business_task_dingtalk_link(link_id)
        status = "creating" if persisted["dingtalk_task_id"] else "failed"
        store.update_business_task_dingtalk_link(link_id, status=status, last_error=str(exc))
    return store.get_business_task_dingtalk_link(link_id)


#: How far back the completion scan looks each run, in list pages of 20.
DINGTALK_COMPLETION_SCAN_PAGES = 30
DINGTALK_COMPLETION_SCAN_PAGE_SIZE = 20


def reconcile_unknown_business_task_todo_creates(
    store: AutoReplyStore,
    dws: Any,
    *,
    limit: int = 100,
) -> int:
    """Settle TODO creates whose outcome was unknown, from their saved receipt.

    Delivery bookkeeping for the TODO create outbox, not a completion check.
    """
    settled = 0
    for pending in store.list_unknown_business_task_todo_creates_with_receipts(limit=limit):
        task_id = str(pending["dingtalk_task_id"])
        try:
            readback = dws.get_todo_task(task_id)
            if _payload_task_id(readback) != task_id:
                raise ValueError("DingTalk TODO read-back identity does not match the saved receipt")
            store.complete_unknown_business_task_todo_sync_outbox_from_receipt(
                outbox_id=int(pending["outbox_id"]),
                provider_readback_json=json.dumps(readback, ensure_ascii=False),
            )
            settled += 1
        except (DwsError, RuntimeError, ValueError) as exc:
            store.update_business_task_dingtalk_link(
                int(pending["link_id"]), last_error=str(exc)
            )
    return settled


def scan_completed_dingtalk_todos(
    store: AutoReplyStore,
    dws: Any,
    *,
    now: str,
    pages: int = DINGTALK_COMPLETION_SCAN_PAGES,
) -> int:
    """Close linked TODOs and Tasks that DingTalk now lists as completed.

    Derek, 2026-09-25: completion is new information, not a separate check.
    The old pull read every active link one by one (660 of them) and only ran
    from the manually started daily maintenance, so the 20 most recent
    completed TODOs Derek created were all still open here. This lists
    completed TODOs instead -- one query per page -- and closes what matches.
    """
    business_links = {
        str(link["dingtalk_task_id"] or "").strip(): link
        for link in store.list_business_task_dingtalk_links(statuses=("active",), limit=100000)
        if str(link["dingtalk_task_id"] or "").strip()
    }
    # Legacy work_todo links remain readable historical evidence during the
    # import window, but are no longer part of the active completion scan. The
    # Task-first business link is the only authoritative completion path; this
    # prevents a provider read from mutating work_todos/work_updates or
    # work_projects after cutover.
    if not business_links:
        return 0
    closed = 0
    for page in range(1, max(1, int(pages)) + 1):
        payload = dws.list_completed_todo_tasks(
            page=page, size=DINGTALK_COMPLETION_SCAN_PAGE_SIZE
        )
        items = _completed_todo_items(payload)
        for item in items:
            task_id = str(item.get("taskId") or item.get("id") or "").strip()
            if task_id in business_links:
                link = business_links.pop(task_id)
                store.update_business_task_dingtalk_link(
                    link["id"], last_dingtalk_done=True,
                    last_dingtalk_payload_json=json.dumps(item, ensure_ascii=False),
                    last_pull_at=now, last_error="",
                )
                if complete_business_task_from_external_todo(
                    store, business_task_id=int(link["business_task_id"]),
                    evidence={
                        "source": f"dingtalk_todo:{task_id}",
                        "reason": "DingTalk TODO marked done",
                        "description": item,
                        "checked_at": now,
                    },
                ):
                    closed += 1
                store.update_business_task_dingtalk_link(link["id"], status="done")
        if len(items) < DINGTALK_COMPLETION_SCAN_PAGE_SIZE or not business_links:
            break
    return closed


def _completed_todo_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("result", "data", "items", "list", "todoCards", "tasks"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _completed_todo_items(value)
            if nested:
                return nested
    return []


def sync_completed_task_to_dingtalk(
    store: AutoReplyStore,
    dws: Any,
    *,
    business_task_id: int,
    evidence: dict[str, object],
    now: str,
) -> str:
    """Apply a completed Business Task to its already-mirrored DingTalk TODO."""
    del evidence
    task = store.get_business_task(business_task_id)
    if task is None or task.status.value == "merged":
        raise ValueError("business Task is missing or merged")
    link = store.get_active_business_task_dingtalk_link(business_task_id)
    if link is None:
        return "skipped"
    task_id = str(link["dingtalk_task_id"] or "").strip()
    if not task_id:
        store.update_business_task_dingtalk_link(
            link["id"], last_error="active DingTalk todo link has no task id"
        )
        return "failed"
    try:
        payload = dws.mark_todo_task_done(task_id, done=True)
        store.update_business_task_dingtalk_link(
            link["id"], status="done", last_dingtalk_done=True,
            last_dingtalk_payload_json=json.dumps(payload, ensure_ascii=False),
            last_push_at=now, last_error="",
        )
        return "completed"
    except (DwsError, RuntimeError) as exc:
        store.update_business_task_dingtalk_link(link["id"], last_error=str(exc))
        return "failed"


def dispatch_claimed_business_task_todo_sync_outbox(
    store: AutoReplyStore,
    dws: Any,
    *,
    item: Any,
    owner: str,
    now: str,
    claim_guard: Any | None = None,
) -> str:
    """Deliver one Task-keyed external effect and persist its receipt."""
    if str(item["status"]) != "running" or str(item["lease_owner"]) != owner:
        raise ValueError("business task todo sync dispatch source is not claimed by owner")
    current = datetime.strptime(now, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    if claim_guard is not None:
        claim_guard.assert_current(current)

    def persist(status: str, *, receipt_json: str = "{}", error: str = "") -> None:
        if claim_guard is not None:
            claim_guard.adapter.finish_delivery(
                claim_guard.envelope, owner=owner, now=current,
                status=status, receipt_json=receipt_json, error=error,
            )
            claim_guard.accept_atomic_source_completion()
        else:
            if status == "failed":
                store.retry_business_task_todo_sync_outbox(
                    outbox_id=int(item["id"]), owner=owner, error=error, now=now,
                )
            else:
                store.finish_business_task_todo_sync_outbox(
                    outbox_id=int(item["id"]), owner=owner, status=status,
                    receipt_json=receipt_json, error=error,
                )

    try:
        task_id = int(item["business_task_id"])
        if item["operation"] == "create":
            link = maybe_create_dingtalk_todo(
                store, dws, business_task_id=task_id, now=now
            )
            if link is None:
                persist("skipped", error="dingtalk_todo_not_eligible_for_mirror")
                return "skipped"
            if link["status"] != "active":
                persist("unknown", error=str(link["last_error"] or "provider_create_receipt_unconfirmed"))
                return "unknown"
            receipt = {
                "link_id": int(link["id"]),
                "dingtalk_task_id": str(link["dingtalk_task_id"]),
            }
        else:
            outcome = sync_completed_task_to_dingtalk(
                store, dws, business_task_id=task_id,
                evidence=json.loads(item["evidence_json"]), now=now,
            )
            if outcome != "completed":
                persist(
                    outcome,
                    error="dingtalk_todo_not_mirrored" if outcome == "skipped" else "dingtalk_todo_effect_not_delivered",
                )
                return outcome
            receipt = {"delivered": True}
        persist("completed", receipt_json=json.dumps(receipt, ensure_ascii=False))
        return "completed"
    except Exception as exc:
        # Once the provider call has started, only readback can resolve an
        # uncertain outcome. Never replay a possibly delivered effect.
        persist("unknown", error=str(exc))
        return "unknown"
