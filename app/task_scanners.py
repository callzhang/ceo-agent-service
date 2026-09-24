import fnmatch
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.agent_cron.commands import (
    SERVICE_COMMAND_CONSUMER_CONTEXT_KEY,
    current_service_command_consumer_context,
)
from app.dingtalk_models import DingTalkMessage
from app.dws_client import OA_PENDING_PAGE_SIZE_MAX
from app.store import AutoReplyStore
from app.task_models import WorkItem
from app.skill_features import FeatureRegistry

AI_MINUTES_SCANNER = "ai_minutes"
MEETING_TODO_SCANNER = "meeting_todos"
OA_PENDING_SCANNER = "oa_pending"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scan_now(now: datetime | None = None) -> datetime:
    return now if now is not None else datetime.now().astimezone()


def _read_text_excerpt_and_digest(path: Path, limit: int = 6000) -> tuple[str, str]:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return "", ""
    return text[:limit], hashlib.sha256(raw).hexdigest()


def scan_ai_minutes(
    store: AutoReplyStore,
    dws,
    *,
    enqueue_existing_on_first_scan: bool = False,
    max_new_items: int | None = None,
    feature_registry: FeatureRegistry | None = None,
) -> int:
    if not (feature_registry or FeatureRegistry()).feature_enabled("work_tracking"):
        return 0
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


def _minutes_id(minutes: dict[str, Any]) -> str:
    return str(
        minutes.get("taskUuid")
        or minutes.get("minutesId")
        or minutes.get("id")
        or minutes.get("task_uuid")
        or minutes.get("uuid")
        or ""
    ).strip()


def _minutes_todo_actions(payload: Any) -> list[Any] | None:
    """Return the explicit action-item collection from supported DWS shapes.

    ``None`` means the payload did not contain a recognized collection. This is
    deliberately different from an explicit empty list: an unknown response
    must be retried instead of being recorded as a meeting with no Todo.
    """
    if not isinstance(payload, dict):
        return None
    for key in ("actions", "actionItems", "action_items", "todos"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    for key in ("result", "data"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            actions = _minutes_todo_actions(nested)
            if actions is not None:
                return actions
    return None


def _canonical_minutes_todos_payload(
    *,
    minutes: dict[str, Any],
    todos_payload: dict[str, Any],
    actions: list[Any],
) -> tuple[str, str]:
    payload = {
        "meeting": minutes,
        "todos": todos_payload,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    canonical_actions = json.dumps(
        actions,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return canonical, hashlib.sha256(canonical_actions.encode("utf-8")).hexdigest()


def scan_meeting_todos(
    store: AutoReplyStore,
    dws,
    *,
    max_new_items: int | None = None,
    feature_registry: FeatureRegistry | None = None,
) -> int:
    """Queue new or revised DingTalk meeting Todos for the Tasks consumer."""
    if not (feature_registry or FeatureRegistry()).feature_enabled("work_tracking"):
        return 0
    list_minutes = getattr(dws, "list_minutes", None)
    get_minutes_todos = getattr(dws, "get_minutes_todos", None)
    if list_minutes is None or get_minutes_todos is None:
        missing = (
            "list_minutes" if list_minutes is None else "get_minutes_todos"
        )
        store.set_daily_scan_state(
            MEETING_TODO_SCANNER,
            last_success_at="",
            cursor_json="{}",
            last_error=f"dws {missing} unavailable",
        )
        return 0

    state = store.get_daily_scan_state(MEETING_TODO_SCANNER) or {}
    raw_cursor = state.get("cursor_json") or "{}"
    try:
        cursor = json.loads(raw_cursor)
    except json.JSONDecodeError:
        cursor = {}
        raw_cursor = "{}"
    previous_digests = {
        str(key): str(value)
        for key, value in dict(cursor.get("todo_digests") or {}).items()
    }
    todo_digests = dict(previous_digests)

    try:
        minutes_items = [
            item for item in list_minutes(limit=50) if isinstance(item, dict)
        ]
    except Exception as exc:
        store.set_daily_scan_state(
            MEETING_TODO_SCANNER,
            last_success_at=state.get("last_success_at") or "",
            cursor_json=raw_cursor,
            last_error=str(exc),
        )
        return 0

    scheduled_consumer = current_service_command_consumer_context()
    errors: list[str] = []
    count = 0
    for minutes in minutes_items:
        minutes_id = _minutes_id(minutes)
        if not minutes_id:
            continue
        try:
            todos_payload = get_minutes_todos(minutes_id)
        except Exception as exc:
            errors.append(f"{minutes_id}: {exc}")
            continue
        actions = _minutes_todo_actions(todos_payload)
        if actions is None:
            errors.append(f"{minutes_id}: unrecognized minutes todos response")
            continue
        canonical, digest = _canonical_minutes_todos_payload(
            minutes=minutes,
            todos_payload=todos_payload,
            actions=actions,
        )
        if previous_digests.get(minutes_id) == digest:
            continue
        if not actions:
            todo_digests[minutes_id] = digest
            continue
        if max_new_items is not None and count >= max_new_items:
            continue

        title = str(minutes.get("title") or f"AI minutes {minutes_id}").strip()
        source_ref = f"{minutes_id}#todos-sha256={digest}"
        item = WorkItem.model_validate(
            {
                "source": {
                    "type": "ai_minutes",
                    "ref": source_ref,
                    "title": f"{title}行动项",
                    "created_at": _minutes_item_time(minutes),
                },
                "summary": canonical,
                "project_name": title,
                "context": {
                    "sender": "",
                    "participants": [],
                    "source_conversation_kind": "minutes",
                    "source_conversation_title": title,
                },
                "scheduled_consumer": (
                    scheduled_consumer.to_payload() if scheduled_consumer else {}
                ),
            }
        )
        store.enqueue_work_summary_input(
            source_type=item.source.type.value,
            source_ref=item.source.ref,
            payload_json=item.model_dump_json(),
        )
        todo_digests[minutes_id] = digest
        count += 1

    last_success_at = (
        (state.get("last_success_at") or "") if errors else _utc_now()
    )
    store.set_daily_scan_state(
        MEETING_TODO_SCANNER,
        last_success_at=last_success_at,
        cursor_json=json.dumps(
            {"todo_digests": todo_digests},
            ensure_ascii=False,
            sort_keys=True,
        ),
        last_error="; ".join(errors),
    )
    return count


def scan_pending_oa_approvals(
    store: AutoReplyStore,
    dws,
    *,
    now: datetime | None = None,
    lookback_days: int = 365,
    page_size: int = OA_PENDING_PAGE_SIZE_MAX,
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
    pending_scan_complete = False
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
                pending_scan_complete = True
                break
    except Exception as exc:
        store.set_daily_scan_state(
            OA_PENDING_SCANNER,
            last_success_at="",
            cursor_json="{}",
            last_error=str(exc),
        )
        return 0

    store.backfill_oa_audit_metadata()
    reconciled_completed_attempt_ids: list[int] = []
    reconciliation_read_failures: list[str] = []
    detail_cache: dict[str, Any] = {}
    tasks_cache: dict[str, Any] = {}
    pending_task_ids_by_process: dict[str, set[str]] = {}
    for approval in approvals:
        process_instance_id = str(
            getattr(approval, "process_instance_id", "") or ""
        ).strip()
        if not process_instance_id or process_instance_id in tasks_cache:
            continue
        try:
            tasks_payload = read_tasks(process_instance_id)
            detail_payload = read_detail(process_instance_id)
        except Exception:
            reconciliation_read_failures.append(process_instance_id)
            continue
        tasks_cache[process_instance_id] = tasks_payload
        detail_cache[process_instance_id] = detail_payload

    current_user_id = ""
    get_current_user_id = getattr(dws, "get_current_user_id", None)
    if get_current_user_id is not None:
        try:
            current_user_id = str(get_current_user_id() or "")
        except Exception:
            current_user_id = ""
    for process_instance_id, tasks_payload in tasks_cache.items():
        task_id = _pending_oa_task_id_for_current_user(
            {"result": [detail_cache[process_instance_id], tasks_payload]},
            current_user_id=current_user_id,
        )
        if task_id:
            pending_task_ids_by_process.setdefault(process_instance_id, set()).add(
                task_id
            )

    for attempt in store.list_open_oa_needs_human_attempts():
        process_instance_id = attempt.oa_process_instance_id.strip()
        try:
            detail_payload = detail_cache.get(process_instance_id)
            if detail_payload is None:
                detail_payload = read_detail(process_instance_id)
                detail_cache[process_instance_id] = detail_payload
        except Exception:
            reconciliation_read_failures.append(process_instance_id)
            continue
        terminal_state = _oa_terminal_process_state(detail_payload)
        process_result = ""
        if terminal_state is not None:
            _status, process_result = terminal_state
        elif pending_scan_complete and attempt.oa_task_id.strip():
            pending_task_ids = pending_task_ids_by_process.get(
                process_instance_id, set()
            )
            if attempt.oa_task_id.strip() not in pending_task_ids:
                process_result = "OA_TASK_NOT_PENDING"
        if not process_result:
            continue
        if store.resolve_completed_oa_needs_human_attempt(
            attempt.id,
            process_instance_id=process_instance_id,
            process_result=process_result,
        ):
            reconciled_completed_attempt_ids.append(attempt.id)

    previous_revisions: dict[str, str] = {}
    previous_queued_days: dict[str, str] = {}
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
            queued_days = cursor.get("queued_days", {})
            if isinstance(queued_days, dict):
                previous_queued_days = {
                    str(process_id): str(day)
                    for process_id, day in queued_days.items()
                }
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    queued = 0
    skipped_missing_task_id: list[str] = []
    read_failures: list[str] = []
    seen_process_ids: set[str] = set()
    queued_process_ids: list[str] = []
    process_revisions: dict[str, str] = {}
    queued_days: dict[str, str] = dict(previous_queued_days)
    scheduled_consumer = current_service_command_consumer_context()
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
            tasks_payload = tasks_cache.get(process_instance_id)
            if tasks_payload is None:
                tasks_payload = read_tasks(process_instance_id)
            detail_payload = detail_cache.get(process_instance_id)
            if detail_payload is None:
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
        applicant_user_id, applicant_open_dingtalk_id = _cache_oa_applicant_profile(
            store,
            dws,
            detail_payload,
        )
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
        # An unchanged approval is normally skipped, but it can still be waiting
        # on the principal: a turn that ended without a decision and without a
        # comment leaves it pending forever, because nothing else will change
        # its revision. Look at that one again once a day so it cannot fall out
        # of the pipeline silently.
        #
        # Once the review comment is there, the ball is in someone else's court
        # and a daily reminder is just noise: wait for new activity instead. The
        # revision already excludes the principal's own records, so it moves
        # only when somebody else comments or acts -- which is exactly the
        # wake-up signal.
        if previous_revisions.get(process_instance_id) == revision and (
            _oa_principal_has_commented(
                records_payload, current_user_id=current_user_id
            )
            or previous_queued_days.get(process_instance_id) == scan_date
        ):
            continue
        queued_days[process_instance_id] = scan_date
        title = str(getattr(approval, "title", "") or "").strip()
        process_name = str(getattr(approval, "process_name", "") or "").strip()
        label = title or process_name or process_instance_id
        oa_url = (
            "https://aflow.dingtalk.com/detail?"
            f"procInstId={quote(process_instance_id)}&taskId={quote(task_id)}"
        )
        trigger = DingTalkMessage(
            # One conversation per approval. The Agent session is keyed by this
            # id, so a shared one let every approval after the first resume the
            # transcript of the previous approval and answer "已在当前会话中完成
            # 处理" without making a single tool call -- one of them reporting an
            # approval it never executed.
            open_conversation_id=f"oa_pending_scan:{process_instance_id}",
            # The day is part of the trigger identity so a daily revisit is a
            # new input for the same business object: without it the id repeats
            # and the enqueue is deduplicated away.
            open_message_id=f"oa-pending:{process_instance_id}:{revision}:{scan_date}",
            conversation_title="审批待办",
            single_chat=True,
            sender_name="Derek OA",
            message_type="text",
            create_time=scan_timestamp,
            content=(
                "审批待办扫描发现新增或有新消息的待处理审批："
                f"{label}\n"
                f"[查看审批]({oa_url})\n"
                # Name the principal outright. The turn was only ever handed the
                # applicant's id, so it had to work out which id was Derek's and
                # sometimes picked the one it had: on one contract approval it
                # took the applicant's id for Derek's, concluded Derek's own task
                # belonged to someone else, and skipped it twice.
                + (
                    f"本条待办属于审批人 Derek（userId {current_user_id}），"
                    f"当前任务 taskId {task_id} 就是他的待办；"
                    "表单与流水里出现的 originatorUserid 是申请人，不是审批人，"
                    "不要用申请人的 userId 判断待办归属。\n"
                    if current_user_id
                    else ""
                )
                +
                # The审批 rules live in our own Skill. Naming the vendor's
                # dingtalk-misc reference here sent every turn to read that
                # instead: run 20020 read dingtalk-oa-approval zero times and
                # dingtalk-misc seven. Worse, `dws upgrade` overwrites the
                # vendor Skills, so a rule written there does not survive.
                "请先完整读取 ~/.agents/skills/dingtalk-oa-approval/SKILL.md，"
                "并按其中的原则、风险与确信度口径、information_completeness 与 "
                "rule_coverage 评分规则和动作选择执行；"
                "dingtalk-misc 的 references/oa.md 只作为 dws 命令用法参考，"
                # The action mapping is the generic Skill's decision table.
                # This sentence used to make every template's action obey a
                # processCode-matched rule card; only the finance templates have
                # cards, so every other approval had no action authority and
                # stopped at needs_human from 2026-09-22.
                "判断标准由本次所选的星尘业务 Skill 提供，动作一律按通用 Skill 的完整决策表；"
                "只有财务模板（processCode 在财务 Skill 登记表内）按其规则卡处理，"
                "其他审批类型没有规则卡不是规则缺口。规则只在 Skill 里，不读参考文档。"
                "在此前提下审阅完整审批材料、历史处理记录和当前节点。"
            ),
            raw_payload={
                "source": "oa_pending_scan",
                "processInstanceId": process_instance_id,
                "taskId": task_id,
                **({"principalUserid": current_user_id} if current_user_id else {}),
                "processName": process_name,
                "title": title,
                **(
                    {"originatorUserid": applicant_user_id}
                    if applicant_user_id
                    else {}
                ),
                **(
                    {"originatorOpenDingTalkId": applicant_open_dingtalk_id}
                    if applicant_open_dingtalk_id
                    else {}
                ),
                **(
                    {
                        SERVICE_COMMAND_CONSUMER_CONTEXT_KEY: (
                            scheduled_consumer.to_payload()
                        )
                    }
                    if scheduled_consumer
                    else {}
                ),
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
            # Each scan is an independent look at the approval's external
            # state, so the turn must not resume the previous turn's session.
            # A resumed one answers "已在此前一次运行中完成实时审阅" from its own
            # transcript and makes no tool call at all: four consecutive runs on
            # 98194 returned the same text, byte for byte, without reading
            # DingTalk once.
            store.clear_conversation_runtime_sessions(trigger.open_conversation_id)
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
                "queued_days": queued_days,
                "skipped_missing_task_id_process_instance_ids": skipped_missing_task_id,
                "read_failure_process_instance_ids": read_failures,
                "reconciliation_read_failure_process_instance_ids": (
                    reconciliation_read_failures
                ),
                "reconciled_completed_attempt_ids": (
                    reconciled_completed_attempt_ids
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        # Keep per-process read failures in the cursor. The list scan itself
        # completed, so this is follow-up evidence rather than scanner failure.
        last_error="",
    )
    return queued


def _oa_terminal_process_state(value: Any) -> tuple[str, str] | None:
    """Extract a definite terminal process state, never a task-row status."""

    if not isinstance(value, dict):
        return None
    status = str(
        value.get("status")
        or value.get("processInstanceStatus")
        or value.get("process_instance_status")
        or ""
    ).strip().upper()
    terminal_statuses = {
        "CANCELED",
        "CANCELLED",
        "CLOSED",
        "COMPLETED",
        "REVOKED",
        "TERMINATED",
    }
    if status in terminal_statuses:
        result = str(
            value.get("processInstanceResult")
            or value.get("process_instance_result")
            or status.casefold()
        ).strip()
        return status, result
    for key in ("result", "data", "process_instance", "processInstance"):
        nested = _oa_terminal_process_state(value.get(key))
        if nested is not None:
            return nested
    return None


def _cache_oa_applicant_profile(
    store: AutoReplyStore,
    dws: Any,
    detail_payload: Any,
) -> tuple[str, str]:
    """Cache the live OA originator mapping for later direct-user delivery."""
    if not isinstance(detail_payload, dict):
        return "", ""
    applicant_user_id = oa_originator_user_id(detail_payload)
    if not applicant_user_id:
        return "", ""
    get_profiles = getattr(dws, "get_user_profiles", None)
    if get_profiles is None:
        return applicant_user_id, ""
    try:
        profiles = get_profiles([applicant_user_id])
    except Exception:
        return applicant_user_id, ""
    for profile in profiles:
        if str(getattr(profile, "user_id", "") or "").strip() != applicant_user_id:
            continue
        open_dingtalk_id = str(
            getattr(profile, "open_dingtalk_id", "") or ""
        ).strip()
        store.upsert_org_user_profile(
            user_id=applicant_user_id,
            name=str(getattr(profile, "name", "") or ""),
            title=str(getattr(profile, "title", "") or ""),
            open_dingtalk_id=open_dingtalk_id or None,
            manager_user_id=getattr(profile, "manager_user_id", None),
            manager_name=str(getattr(profile, "manager_name", "") or ""),
            department_ids=set(getattr(profile, "department_ids", set()) or set()),
            department_names=set(
                getattr(profile, "department_names", set()) or set()
            ),
            org_labels=list(getattr(profile, "org_labels", []) or []),
            has_subordinate=getattr(profile, "has_subordinate", None),
        )
        return applicant_user_id, open_dingtalk_id
    cached = store.get_org_user_profile(applicant_user_id)
    return applicant_user_id, (cached.open_dingtalk_id or "") if cached else ""


def oa_originator_user_id(value: Any) -> str:
    if isinstance(value, dict):
        for key in (
            "originatorUserid",
            "originatorUserId",
            "originator_userid",
            "originator_user_id",
        ):
            candidate = value.get(key)
            if candidate is not None and str(candidate).strip():
                return str(candidate).strip()
        for nested in value.values():
            candidate = oa_originator_user_id(nested)
            if candidate:
                return candidate
    elif isinstance(value, list):
        for nested in value:
            candidate = oa_originator_user_id(nested)
            if candidate:
                return candidate
    return ""


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


# Commenting on an approval is not deciding it; DingTalk records both as
# operation records by the same user.
OA_COMMENT_OPERATION_TYPES = frozenset({"ADD_REMARK", "add_remark", "COMMENT"})


def _oa_principal_has_commented(
    records_payload: Any,
    *,
    current_user_id: str,
) -> bool:
    """Return whether the principal has already left a comment on the approval."""
    if not current_user_id:
        return False
    return any(
        _oa_task_field(record, ("userId", "userid", "user_id")) == current_user_id
        and _oa_task_field(record, ("operationType", "type"))
        in OA_COMMENT_OPERATION_TYPES
        for record in _oa_operation_records(records_payload)
    )


def _oa_pending_approval_revision(
    task_id: str,
    records_payload: Any,
    *,
    current_user_id: str,
) -> str:
    """Return a revision only when the newest OA record still needs review.

    A record written by the principal normally means the approval has been
    dealt with.  A comment does not: the agent posts one as the principal
    whenever it reviews without deciding, and the approval stays `RUNNING`
    and still waiting on them.  Treating that comment as "dealt with" made
    the approval permanently invisible to this scan.
    """
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
    if (
        current_user_id
        and _oa_task_field(latest_operation, ("userId", "userid", "user_id"))
        == current_user_id
        and _oa_task_field(latest_operation, ("operationType", "type"))
        not in OA_COMMENT_OPERATION_TYPES
    ):
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
