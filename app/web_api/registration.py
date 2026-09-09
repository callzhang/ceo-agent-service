"""Registration for the React console's domain APIs.

The adapters in this module intentionally return resource-shaped JSON.  The
legacy HTML handlers remain available for the external bridge and for the
old form routes during the migration, but React never consumes their HTML.
"""

from collections.abc import Callable
from html import unescape
import json
import re
import subprocess
from typing import Any
from urllib.parse import quote, urlencode
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.web_api.attention import AttentionListEnvelope, group_attention_rows
from app.web_api.attempts import build_attempt_detail
from app.web_api.common import ApiListMeta, ApiMeta, json_safe, normalize_display_value, snapshot_at
from app.web_api.tasks import (
    ConsoleTaskDetail,
    ConsoleTaskDetailEnvelope,
    ConsoleTaskListEnvelope,
    ConsoleSentTodo,
    ConsoleSentTodoListEnvelope,
    sent_todo_payload,
    task_detail,
    task_list_response,
)
from app.web_api.settings import info_payload
from app.web_api.status import StatusEnvelope, StatusMeta, WorkerStatus
from app.web_api.email import register_email_routes
from app.web_api.scheduled_tasks import register_scheduled_task_routes
from app.agent_cron.options import ScheduledTaskOptionService
from app.skill_features import FeatureRegistry
from app.skill_files import (
    SkillFileService,
    SkillFileValidationError,
)
from app.managed_skills import (
    ManagedSkillValidationError,
    import_repository_managed_skills,
    export_managed_skill_revision,
    validate_managed_skill_name,
)
from app.feedback_processing import (
    FeedbackIterationDecision,
    FeedbackIterationAssociationMismatchError,
    FeedbackIterationDisabledError,
    FeedbackProcessingBatchError,
    FeedbackProcessingClaimError,
    FeedbackProcessingReopenError,
    ResolutionEvidence,
    build_feedback_start_message,
    detail_references,
    persisted_feedback_summary,
    project_feedback_status,
)


def _legacy_settings_error_message(body_text: str) -> str:
    """Extract the legacy form validation text before returning a JSON error."""
    match = re.search(r'<p class="attempt-warning">(.*?)</p>', body_text, flags=re.DOTALL)
    if not match:
        return "保存失败，请检查字段"
    return unescape(re.sub(r"<[^>]+>", "", match.group(1))).strip() or "保存失败，请检查字段"


def register_console_routes(
    app: FastAPI,
    store_factory: Callable[[], Any],
    *,
    status_payload_factory: Callable[[], Any],
    feedback_backlog_factory: Callable[[], Any],
    attention_rows_factory: Callable[[], Any],
    task_row_builder: Callable[..., Any] | None = None,
    history_chart_factory: Callable[[int], Any] | None = None,
    email_store_factory: Callable[[], Any] | None = None,
    email_learning_factory: Callable[[], Any] | None = None,
    feature_registry_factory: Callable[[], FeatureRegistry] | None = None,
    skill_file_service_factory: Callable[[], SkillFileService] | None = None,
    dws_factory: Callable[[], Any] | None = None,
    scheduled_task_option_service_factory: Callable[
        [], ScheduledTaskOptionService
    ] | None = None,
    scheduled_task_wake_callback: Callable[[], None] | None = None,
    scheduled_task_now: Callable[[], Any] | None = None,
) -> None:
    feature_registry_factory = feature_registry_factory or FeatureRegistry
    skill_file_service_factory = skill_file_service_factory or SkillFileService

    def list_meta(*, page: int, page_size: int, total: int, snapshot: str):
        return ApiListMeta(
            snapshot_at=snapshot, page=page, page_size=page_size, total=total,
            next_cursor=str(page + 1) if page * page_size < total else "",
            has_more=page * page_size < total,
        )

    def list_envelope(items: list[Any], *, page: int, page_size: int, total: int):
        return {"items": [json_safe(item) for item in items], "meta": list_meta(
            page=page, page_size=page_size, total=total, snapshot=snapshot_at()
        ).model_dump(mode="json")}

    def item_envelope(item: Any):
        return {"item": json_safe(item), "meta": {"snapshot_at": snapshot_at()}}

    def history_log_detail_url(log: Any) -> str:
        source_table = str(getattr(log, "source_table", "") or "")
        source_id = int(getattr(log, "source_id", 0) or 0)
        project_id = int(getattr(log, "project_id", 0) or 0)
        todo_id = int(getattr(log, "todo_id", 0) or 0)
        follow_up_id = int(getattr(log, "follow_up_id", 0) or 0)
        if source_table == "meeting_alignment_runs":
            return f"/meeting-attempts/{source_id}"
        if source_table == "follow_up_drafts":
            return f"/tasks/{project_id}#follow-up-{follow_up_id or source_id}"
        if source_table in {
            "work_updates",
            "todo_evidence_candidates",
            "work_todo_dingtalk_links",
        }:
            if project_id and todo_id:
                return f"/tasks/{project_id}#todo-{todo_id}"
            if project_id:
                return f"/tasks/{project_id}"
        if source_table == "reply_attempts":
            return f"/attempts/{source_id}"
        return ""

    def history_log_item(log: Any) -> dict[str, Any]:
        source_table = str(getattr(log, "source_table", "") or "")
        history_type = str(getattr(log, "history_type", "") or "")
        if source_table == "meeting_alignment_runs":
            kind = "meeting"
        elif source_table in {"work_updates", "todo_evidence_candidates", "follow_up_drafts", "work_todo_dingtalk_links"}:
            kind = "task"
        else:
            kind = "reply"
        title = normalize_display_value(getattr(log, "context", "") or getattr(log, "summary", "") or getattr(log, "category", ""))
        summary = normalize_display_value(getattr(log, "summary", ""))
        detail = normalize_display_value(getattr(log, "detail", ""))
        if source_table == "meeting_alignment_runs":
            input_text = normalize_display_value(getattr(log, "context", ""))
            output_text = summary
        elif source_table == "follow_up_drafts":
            input_text = summary
            output_text = detail
        elif source_table == "todo_evidence_candidates":
            input_text = summary
            output_text = detail or summary
        elif source_table == "work_updates":
            input_text = summary
            output_text = detail
        elif source_table == "work_todo_dingtalk_links":
            input_text = summary
            output_text = detail
        else:
            input_text = summary
            output_text = detail or summary
        return {
            "id": str(getattr(log, "source_id", 0) or 0),
            "occurred_at": getattr(log, "occurred_at", ""),
            "title": title,
            "type": history_type or kind,
            "status": normalize_display_value(getattr(log, "status", "")),
            "summary": summary or output_text or input_text,
            "actor": normalize_display_value(getattr(log, "source_actor", "")),
            "detail_url": history_log_detail_url(log),
            "kind": kind,
            "input": input_text,
            "output": output_text,
            "action": normalize_display_value(getattr(log, "action", "")),
        }

    def queue_history_item(task: Any) -> dict[str, Any]:
        """Render a live queue item without presenting it as an execution run."""
        status = str(getattr(task, "status", "") or "").strip().lower()
        progress = (
            "正在由执行器处理。"
            if status == "processing"
            else "已入队，等待执行器领取。"
        )
        error = normalize_display_value(getattr(task, "error", ""))
        if error:
            progress = f"{progress} {error}"
        return {
            "id": f"task-{int(getattr(task, 'id', 0) or 0)}",
            "occurred_at": str(getattr(task, "updated_at", "") or ""),
            "title": normalize_display_value(getattr(task, "conversation_title", "")),
            "type": "queue",
            "status": status,
            "summary": progress,
            "actor": normalize_display_value(getattr(task, "trigger_sender", "")),
            "detail_url": "/workers",
            "kind": "queue",
            "input": normalize_display_value(getattr(task, "trigger_text", "")),
            "output": progress,
            "action": "reply_task",
        }

    def queue_history_matches(task: Any, *, query: str, status: str) -> bool:
        task_status = str(getattr(task, "status", "") or "").strip().lower()
        if status and task_status != status:
            return False
        query_text = query.strip().casefold()
        if not query_text:
            return True
        searchable = " ".join(
            str(getattr(task, field, "") or "")
            for field in (
                "conversation_id", "conversation_title", "trigger_message_id",
                "trigger_sender", "trigger_text", "status", "error",
            )
        ).casefold()
        return query_text in searchable

    def stored_json(value: str, fallback: Any):
        try:
            return json.loads(value or "")
        except (TypeError, json.JSONDecodeError):
            return fallback

    def command_result(*, item: Any = None, message: str = "已完成", ok: bool = True):
        return {"ok": ok, "item": json_safe(item), "message": message,
                "meta": {"updated_at": snapshot_at()}}

    def fresh_zero_feedback_backlog() -> dict[str, int]:
        """Read strict, synchronous queue evidence for feedback resolution."""

        backlog = feedback_backlog_factory()
        if not isinstance(backlog, dict):
            raise ValueError("fresh local backlog evidence is unavailable")
        evidence: dict[str, int] = {}
        for name in ("processing", "failed", "retryable"):
            value = backlog.get(name)
            if type(value) is not int or value != 0:
                raise ValueError("fresh local backlog evidence is unavailable or non-zero")
            evidence[name] = value
        return evidence

    if email_store_factory is not None:
        register_email_routes(
            app,
            email_store_factory,
            email_learning_factory=email_learning_factory,
        )

    if scheduled_task_option_service_factory is not None:
        register_scheduled_task_routes(
            app,
            store_factory,
            option_service_factory=scheduled_task_option_service_factory,
            wake_callback=scheduled_task_wake_callback,
            now=scheduled_task_now,
        )

    async def json_object(request: Request) -> dict[str, Any]:
        if "application/json" not in request.headers.get("content-type", ""):
            raise HTTPException(status_code=415, detail="JSON Content-Type required")
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="JSON object required")
        return payload

    @app.get("/api/console/tasks", response_model=ConsoleTaskListEnvelope)
    def console_tasks(
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        q: str = "",
        category: str = "",
        task_state: str = "",
        sort: str = "",
    ):
        return task_list_response(
            store_factory(),
            page=page,
            page_size=page_size,
            query=q,
            category=category,
            task_state=task_state,
            sort=sort,
            row_builder=task_row_builder,
        )

    @app.get("/api/console/tasks/sent-todos", response_model=ConsoleSentTodoListEnvelope)
    def console_sent_todos(
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=5000, ge=1, le=5000),
    ):
        rows = [
            sent_todo_payload(record)
            for record in store_factory().list_sent_todo_records(limit=5000)
        ]
        total = len(rows)
        start = (page - 1) * page_size
        return ConsoleSentTodoListEnvelope(
            items=[ConsoleSentTodo.model_validate(row) for row in rows[start : start + page_size]],
            meta=list_meta(page=page, page_size=page_size, total=total, snapshot=snapshot_at()),
        )

    @app.get("/api/console/tasks/{project_id}/details", response_model=ConsoleTaskDetailEnvelope)
    def console_task_details_alias(project_id: int):
        return console_task_detail(project_id)

    @app.get("/api/console/tasks/{project_id}", response_model=ConsoleTaskDetailEnvelope)
    def console_task_detail(project_id: int):
        store = store_factory()
        with store.read_snapshot():
            item = task_detail(store, project_id)
        if item is None:
            return JSONResponse(
                {"ok": False, "code": "not_found", "message": "Task project not found", "details": {"project_id": project_id}},
                status_code=404,
            )
        return ConsoleTaskDetailEnvelope(
            item=ConsoleTaskDetail.model_validate(item),
            meta=ApiMeta(snapshot_at=snapshot_at()),
        )

    @app.get(
        "/api/console/status",
        response_model=StatusEnvelope,
        response_model_exclude_none=True,
    )
    def console_status():
        item = WorkerStatus.model_validate(json_safe(status_payload_factory()))
        return StatusEnvelope(item=item, meta=StatusMeta(snapshot_at=snapshot_at()))

    @app.get("/api/console/attention", response_model=AttentionListEnvelope)
    def console_attention():
        groups = group_attention_rows(attention_rows_factory())
        return AttentionListEnvelope(
            items=groups,
            meta=ApiListMeta(
                snapshot_at=snapshot_at(),
                total=sum(group.count for group in groups),
            ),
        )

    @app.get("/api/console/history")
    def console_history(
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        q: str = "",
        status: str = "",
        object_type: str = "",
        chart_range: str = Query(default="24h"),
        include_chart: bool = Query(default=True),
    ):
        store = store_factory()
        status_key = status.strip().lower()
        statuses = ("done", "sent") if status_key == "done" else ((status_key,) if status_key else None)
        object_type_key = object_type.strip().lower()
        history_types = (
            (object_type_key,)
            if object_type_key and object_type_key != "queue"
            else None
        )
        visible_source_tables = (
            "reply_attempts",
            "meeting_alignment_runs",
            "work_updates",
            "todo_evidence_candidates",
            "follow_up_drafts",
            "work_todo_dingtalk_links",
        )
        queue_items = (
            [
                queue_history_item(task)
                for task in store.list_reply_tasks(statuses=("pending", "processing"))
                if queue_history_matches(task, query=q, status=status_key)
            ]
            if object_type_key in {"", "queue"}
            else []
        )
        log_total, rows = store.list_operation_logs_with_count(
            # Queue rows can only displace log rows from the requested page.
            # Fetch through that page before merging both ordered sources.
            limit=page * page_size,
            offset=0,
            query=q,
            statuses=statuses,
            history_types=history_types,
            source_tables=visible_source_tables,
        )
        all_items = [*queue_items, *(history_log_item(row) for row in rows)]
        all_items.sort(
            key=lambda item: str(item.get("occurred_at") or ""), reverse=True
        )
        total = log_total + len(queue_items)
        start = (page - 1) * page_size
        items = all_items[start:start + page_size]
        response = list_envelope(items, page=page, page_size=page_size, total=total)
        if include_chart and history_chart_factory is not None:
            chart_hours = {"24h": 24, "1w": 24 * 7, "1m": 24 * 30}.get(chart_range.strip().lower(), 24)
            response["chart"] = json_safe(history_chart_factory(chart_hours))
        return response

    @app.get("/api/console/history/chart")
    def console_history_chart(range: str = Query(default="24h")):
        if history_chart_factory is None:
            return {"chart": {}}
        chart_hours = {"24h": 24, "1w": 24 * 7, "1m": 24 * 30}.get(range.strip().lower(), 24)
        return {"chart": json_safe(history_chart_factory(chart_hours)), "meta": {"snapshot_at": snapshot_at()}}

    @app.get("/api/console/history/errors/{error_id}")
    def console_error_detail(error_id: int):
        error = store_factory().get_error(error_id)
        if error is None:
            return JSONResponse(
                {"ok": False, "code": "not_found", "message": "Error record not found", "details": {}},
                status_code=404,
            )
        resolved = bool(error.resolved_at)
        return item_envelope({
            "id": error.id,
            "title": error.kind,
            "kind": error.kind,
            "status": "resolved" if resolved else "failed",
            "summary": error.detail,
            "error": error.detail,
            "created_at": error.created_at,
            "updated_at": error.resolved_at or error.created_at,
            "resolved_at": error.resolved_at,
            "resolution": error.resolution,
            "context": "service error",
            "runtime": {
                "conversation_id": error.conversation_id or "",
                "message_id": error.message_id or "",
            },
        })

    @app.get("/api/console/history/{attempt_id}")
    def console_history_detail(attempt_id: int):
        status, payload = build_attempt_detail(store_factory(), attempt_id)
        if payload is None:
            return JSONResponse({"ok": False, "code": "not_found", "message": "Attempt not found", "details": {}}, status_code=404)
        return JSONResponse(item_envelope(payload), status_code=status)

    @app.post("/api/console/history/{attempt_id}/feedback")
    async def console_history_feedback(attempt_id: int, request: Request):
        payload = await json_object(request)
        store = store_factory()
        feedback = str(payload.get("feedback") or payload.get("reviewer_feedback") or "")
        if not store.record_reply_feedback(
            attempt_id,
            feedback=feedback,
            corrected_reply_text=str(payload.get("corrected_reply") or ""),
        ):
            return JSONResponse({"ok": False, "code": "not_found", "message": "Attempt not found", "details": {}}, status_code=404)
        from app.audit_web import _record_feedback_event_for_attempt
        _record_feedback_event_for_attempt(store, attempt_id, feedback)
        return command_result(message="反馈已保存")

    @app.post("/api/console/history/{attempt_id}/rerun")
    async def console_history_rerun(attempt_id: int, request: Request):
        from app.audit_web import handle_rerun_attempt_post
        status, _headers, body = handle_rerun_attempt_post(store_factory(), attempt_id, return_to="/history")
        if status >= 400:
            return JSONResponse({"ok": False, "code": "rerun_failed", "message": "无法重跑该 Attempt", "details": {"technical": normalize_display_value(body)}}, status_code=status)
        return command_result(message="重跑已提交")

    @app.post("/api/console/history/{attempt_id}/recall")
    async def console_history_recall(attempt_id: int, request: Request):
        del request
        if dws_factory is None:
            return JSONResponse({"ok": False, "code": "recall_unavailable", "message": "当前服务未配置撤回通道", "details": {}}, status_code=503)
        from app.audit_web import handle_recall_post
        status, _headers, body = handle_recall_post(
            store_factory(), dws_factory(), attempt_id, return_to="/history"
        )
        if status >= 400:
            return JSONResponse({"ok": False, "code": "recall_failed", "message": "无法撤回该 Attempt", "details": {"technical": normalize_display_value(body)}}, status_code=status)
        return command_result(message="撤回已提交")

    @app.post("/api/console/history/{attempt_id}/open-wechat-message")
    async def console_open_wechat_message(attempt_id: int, request: Request):
        del request
        store = store_factory()
        attempt = store.get_reply_attempt(attempt_id)
        if attempt is None or str(getattr(attempt, "channel", "") or "") != "wechat":
            return JSONResponse({"ok": False, "code": "not_found", "message": "未找到微信消息", "details": {}}, status_code=404)
        reply_task = store.get_reply_task_for_message(
            attempt.conversation_id, attempt.trigger_message_id, channel="wechat",
        )
        delivery = store.get_wechat_delivery_for_task(reply_task.id) if reply_task else None
        if delivery is None:
            return JSONResponse({"ok": False, "code": "not_found", "message": "未找到对应微信会话", "details": {}}, status_code=404)
        scope = store.get_wechat_reply_scope(
            delivery.account_id, delivery.target_type, delivery.target_id,
        )
        if scope is None:
            return JSONResponse({"ok": False, "code": "not_configured", "message": "该微信会话未在 Config 中配置", "details": {}}, status_code=409)
        from app.wechat import service
        visible_title = service.build_sender().open_and_identify(
            scope.display_name,
            search_query=scope.display_name,
            expected_recent_text=None,
            keep_foreground=True,
        )
        if visible_title != scope.display_name:
            return JSONResponse({"ok": False, "code": "open_failed", "message": "未能定位这条微信消息", "details": {}}, status_code=409)
        return command_result(message=f"已打开微信消息：{visible_title}")

    @app.post("/api/console/history/{attempt_id}/human-decision")
    async def console_history_human_decision(attempt_id: int, request: Request):
        payload = await json_object(request)
        instruction = str(payload.get("instruction") or "").strip()
        feedback_scope = str(payload.get("feedback_scope") or "one_time").strip()
        skill_update_requested = bool(payload.get("skill_update_requested", False))
        from app.audit_web import handle_needs_human_decision_post
        form = urlencode({
            "instruction": instruction,
            "feedback_scope": feedback_scope,
            "skill_update_requested": "1" if skill_update_requested else "",
        }).encode("utf-8")
        status, _headers, body = handle_needs_human_decision_post(
            store_factory(), attempt_id, form, return_to="/history"
        )
        if status >= 400:
            return JSONResponse({"ok": False, "code": "decision_failed", "message": "无法提交人工决策", "details": {"technical": normalize_display_value(body)}}, status_code=status)
        return command_result(message="人工决策已提交")

    @app.get("/api/console/meeting-attempts/{run_id}")
    def console_meeting_detail(run_id: int):
        store = store_factory()
        run = store.get_meeting_alignment_run(run_id)
        if run is None:
            return JSONResponse({"ok": False, "code": "not_found", "message": "Meeting attempt not found", "details": {}}, status_code=404)
        job = store.get_meeting_alignment_job(run.job_id)
        if job is None:
            return JSONResponse({"ok": False, "code": "not_found", "message": "Meeting job not found", "details": {}}, status_code=404)
        decision = stored_json(run.decision_json, {})
        if not isinstance(decision, dict):
            decision = {}
        trigger_reasons = decision.get("trigger_reasons")
        trigger_text = ", ".join(str(item) for item in trigger_reasons) if isinstance(trigger_reasons, list) else ""
        target = decision.get("target")
        decision_target = ""
        if isinstance(target, dict):
            decision_target = str(target.get("title") or target.get("conversation_id") or "")
        participants = stored_json(job.participants_json, [])
        if not participants:
            source = stored_json(job.source_json, {})
            if isinstance(source, dict):
                evidence = source.get("calendar_evidence")
                if isinstance(evidence, dict):
                    participants = evidence.get("participants") or []
        participant_names = [
            str(item.get("name") or "").strip()
            for item in participants
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        ]
        participant_preview = ", ".join(participant_names)
        mentions = stored_json(job.mentions_json, [])
        mention_values = []
        if isinstance(mentions, list):
            for mention in mentions:
                if isinstance(mention, dict):
                    value = mention.get("display_name") or mention.get("name") or mention.get("mention_name") or mention.get("user_id") or ""
                else:
                    value = mention
                if str(value).strip():
                    mention_values.append(str(value).strip())
        mention_text = ", ".join(mention_values)
        run_status = run.status
        if run_status == "no_action":
            run_status = "skipped"
        elif run_status in {"retry", "failed"}:
            run_status = "failed"
        elif run_status == "ready_to_send" and job.status == "sent":
            run_status = "sent"
        elif run_status == "ready_to_send" and store.has_later_meeting_alignment_run(run.job_id, run.id):
            run_status = "ready_to_send"
        elif run_status == "ready_to_send" and job.status in {"retry", "failed"}:
            run_status = "failed"
        fields = [
            {"label": "meeting id", "value": job.meeting_id},
            {"label": "action", "value": str(decision.get("action") or "")},
            {"label": "status", "value": run_status},
            {"label": "job status", "value": job.status},
            {"label": "target kind", "value": job.target_kind},
            {"label": "delivery target", "value": job.target_title or job.target_id},
            {"label": "decision target", "value": decision_target},
            {"label": "Mention resolution", "value": mention_text},
            {"label": "ended at", "value": job.ended_at},
            {"label": "participants", "value": participant_preview},
        ]
        conversation_id = ""
        if isinstance(target, dict):
            conversation_id = str(target.get("conversation_id") or "").strip()
        if not conversation_id:
            conversation_id = str(job.target_id or "").strip()
        dingtalk_url = (
            f"/open-dingtalk-popup?conversation_id={quote(conversation_id, safe='')}"
            if conversation_id
            else ""
        )
        try:
            # Reuse the legacy trace normalizer so the React DTO preserves the
            # readable title/metadata/args/output structure and call pairing.
            from app.audit_web import _audit_event_uses_for_attempt

            tool_uses = _audit_event_uses_for_attempt(run)
        except Exception:
            # Old or partially persisted runs may not have a readable Codex
            # transcript. Their stored event payload is still useful evidence.
            tool_uses = stored_json(run.audit_tool_events_json, [])
        return item_envelope({
            "id": run.id,
            "title": job.title,
            "type": "meeting",
            "status": run_status,
            "conversation": {
                "label": "会议",
                "title": job.title,
                "subtitle": f"参会人：{participant_preview}" if participant_preview else "",
            },
            "metadata": fields,
            # Keep the generic resource fields for API consumers while the
            # meeting-specific presentation uses the structured sections below.
            "input": {
                "meeting_id": job.meeting_id,
                "ended_at": job.ended_at,
                "participants": participants,
            },
            "decision": decision,
            "output": job.final_message,
            "trigger": {
                "title": "Trigger",
                "text": "\n".join([
                    f"title: {job.title}",
                    f"meeting id: {job.meeting_id}",
                    f"ended at: {job.ended_at}",
                    f"participants: {participant_preview}",
                    *([f"trigger reasons: {trigger_text}"] if trigger_text else []),
                ]),
            },
            "audit_explanation": {"title": "Codex reason", "text": run.audit_summary},
            "generated_reply": {"title": "生成回复", "text": job.final_message or "No generated reply recorded."},
            "audit_summary": run.audit_summary,
            "tool_uses": tool_uses,
            "runtime": {
                "run_status": run.status,
                "job_status": job.status,
                "audit_summary": run.audit_summary,
                "audit_tool_events": stored_json(run.audit_tool_events_json, []),
                "error": run.error or job.error,
                "created_at": run.created_at,
                "finished_at": run.finished_at,
                "updated_at": run.updated_at,
            },
            "actions": {
                "agent_url": f"/codex/{run.codex_session_id}" if run.codex_session_id else "",
                "dingtalk_url": dingtalk_url,
            },
        })

    @app.get("/api/console/oa-approvals/{process_instance_id:path}")
    def console_oa_detail(process_instance_id: str):
        histories = store_factory().list_oa_attempt_histories([process_instance_id])
        attempts = histories.get(process_instance_id, [])
        if not attempts:
            return JSONResponse({"ok": False, "code": "not_found", "message": "OA approval not found", "details": {}}, status_code=404)
        return item_envelope({"process_instance_id": process_instance_id,
                              "status": attempts[-1].send_status,
                              "title": attempts[-1].conversation_title,
                              "attempts": [{"id": a.id, "status": a.send_status,
                                            "action": a.oa_action, "remark": a.oa_remark,
                                            "created_at": a.created_at} for a in attempts]})

    @app.get("/api/console/feedback")
    def console_feedback(page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100), q: str = "", status: str = ""):
        store = store_factory()
        all_rows = store.list_user_feedback_items(limit=10000, offset=0)
        needle = q.strip().casefold()
        filtered = []
        for row in all_rows:
            processing = store.get_feedback_processing_item(row.key)
            row_status = project_feedback_status(row, processing)
            haystack = " ".join((row.comment, row.conversation_title, row.trigger_sender, row.trigger_text, row_status)).casefold()
            if (status.strip() and row_status != status.strip()) or (needle and needle not in haystack):
                continue
            refs = detail_references(row)
            filtered.append({
                "id": row.key, "feedback_key": row.key,
                "attempt_id": str(row.attempt_id) if row.attempt_id else "",
                "status": row_status, "processing_status": row_status,
                "rating": row.rating_label or row.rating,
                "comment": row.comment or "未填写评语",
                "context": " · ".join(x for x in (row.conversation_title, row.trigger_sender, row.trigger_text[:140]) if x),
                "created_at": row.received_at or row.updated_at, "key": row.key,
                "summary": persisted_feedback_summary(row), "references": refs,
                "batch_id": processing.batch_id if processing else "",
                "processing_task_id": processing.workbench_task_id if processing else "",
            })
        start = (page - 1) * page_size
        envelope = list_envelope(filtered[start:start + page_size], page=page, page_size=page_size, total=len(filtered))
        envelope["pending_count"] = sum(
            1 for row in all_rows
            if project_feedback_status(row, store.get_feedback_processing_item(row.key)) == "pending"
        )
        return envelope

    def _feedback_items_for_batch(store: Any, batch_id: str) -> list[dict[str, Any]]:
        batch_rounds = store.list_feedback_processing_rounds_for_batch(batch_id)
        source_rows = {
            row.key: row
            for row in store.list_user_feedback_items_by_keys(
                [round_item.feedback_key for round_item in batch_rounds]
            )
        }
        rows = []
        for round_item in batch_rounds:
            row = source_rows.get(round_item.feedback_key)
            projected = json_safe(round_item)
            projected.update({
                "processing_status": round_item.status,
                "summary": persisted_feedback_summary(row) if row else "",
                "references": detail_references(row) if row else [],
                "processing_task_id": round_item.workbench_task_id,
            })
            rows.append(projected)
        return rows

    def _feedback_processing_state(store: Any, feedback_key: str) -> dict[str, Any] | None:
        processing = store.get_feedback_processing_item(feedback_key)
        if processing is None:
            return None
        history = store.list_feedback_processing_rounds(feedback_key)
        current = next(
            (
                round_item
                for round_item in history
                if round_item.id == processing.current_round_id
            ),
            None,
        )
        payload = json_safe(processing)
        payload["current_processing"] = json_safe(current) if current else None
        payload["processing_history"] = [json_safe(round_item) for round_item in history]
        return payload

    def _feedback_iteration_runtime_context(store: Any) -> dict[str, object]:
        """Project only the exact active runtime identity into a feedback turn."""
        config = store.get_active_runtime_skill_config()
        if config is None:
            return {}
        revisions: list[dict[str, object]] = []
        for binding in store.list_runtime_skill_bindings(config.id):
            if not binding.enabled:
                continue
            revision = store.get_managed_skill_revision(binding.revision_id)
            if revision is not None:
                revisions.append({
                    "skill_id": revision.skill_id,
                    "revision_number": revision.revision_number,
                    "sha256": revision.sha256,
                })
        return {"config_id": config.id, "loaded_revisions": revisions}

    @app.post("/api/console/feedback/batches")
    async def console_feedback_batch_claim(request: Request):
        payload = await json_object(request)
        raw_keys = payload.get("feedback_keys", payload.get("keys"))
        if not isinstance(raw_keys, list) or not raw_keys or any(not isinstance(key, str) or not key.strip() for key in raw_keys):
            raise HTTPException(status_code=400, detail="feedback_keys must be a non-empty string array")
        keys = list(dict.fromkeys(key.strip() for key in raw_keys))
        batch_id = payload.get("batch_id")
        if batch_id is not None and (not isinstance(batch_id, str) or not batch_id.strip()):
            raise HTTPException(status_code=400, detail="batch_id must be a non-empty string")
        task_id = payload.get("workbench_task_id", payload.get("task_id", ""))
        turn_id = payload.get("workbench_turn_id", payload.get("turn_id", ""))
        if task_id is not None and not isinstance(task_id, str):
            raise HTTPException(status_code=400, detail="workbench_task_id must be a string")
        if turn_id is not None and not isinstance(turn_id, str):
            raise HTTPException(status_code=400, detail="workbench_turn_id must be a string")
        cleaned_batch_id = batch_id.strip() if isinstance(batch_id, str) else uuid4().hex
        claim_store = store_factory()
        known_rows = claim_store.list_user_feedback_items(limit=10000, offset=0)
        known_keys = {row.key for row in known_rows}
        missing = [key for key in keys if key not in known_keys]
        if missing:
            return JSONResponse({"ok": False, "code": "not_found", "message": "Feedback not found", "details": {"feedback_keys": missing}}, status_code=404)
        try:
            claimed = claim_store.claim_feedback_processing_items(cleaned_batch_id, keys)
        except FeedbackIterationDisabledError as exc:
            return JSONResponse({"ok": False, "code": exc.error_code, "message": "反馈迭代已关闭", "details": {}}, status_code=409)
        except FeedbackProcessingClaimError as exc:
            return JSONResponse({"ok": False, "code": exc.error_code, "message": "反馈已被其他处理批次占用", "details": {}}, status_code=409)
        except FeedbackProcessingBatchError as exc:
            return JSONResponse({"ok": False, "code": "feedback_batch_conflict", "message": str(exc), "details": {}}, status_code=409)
        store = store_factory()
        if task_id or turn_id:
            for item in claimed:
                store.associate_feedback_processing_turn(
                    item.feedback_key,
                    expected_batch_id=cleaned_batch_id,
                    workbench_task_id=(task_id or "").strip(),
                    workbench_turn_id=(turn_id or "").strip(),
                )
        imports = [item for item in store.list_feedback_import_items(limit=10000, offset=0) if item.feedback_key in keys]
        imports.sort(key=lambda item: keys.index(item.feedback_key))
        refreshed_batch = store.get_feedback_processing_batch(cleaned_batch_id)
        item = {"batch_id": cleaned_batch_id, "status": refreshed_batch.status if refreshed_batch else "processing", "feedback_keys": keys, "items": _feedback_items_for_batch(store, cleaned_batch_id), "start_message": build_feedback_start_message(cleaned_batch_id, imports, runtime_context=_feedback_iteration_runtime_context(store))}
        return command_result(item=item, message="反馈批次已领取")

    @app.get("/api/console/feedback/batches/{batch_id}")
    def console_feedback_batch_detail(batch_id: str):
        store = store_factory()
        batch = store.get_feedback_processing_batch(batch_id)
        if batch is None:
            return JSONResponse({"ok": False, "code": "not_found", "message": "Feedback batch not found", "details": {}}, status_code=404)
        return item_envelope({"batch_id": batch.batch_id, "status": batch.status, "requested_count": batch.requested_count, "created_at": batch.created_at, "updated_at": batch.updated_at, "resolved_at": batch.resolved_at, "items": _feedback_items_for_batch(store, batch.batch_id), "decisions": [json_safe(item) for item in store.list_feedback_iteration_decisions(batch.batch_id)]})

    @app.post("/api/console/feedback/batches/{batch_id}/decisions", status_code=201)
    async def console_feedback_iteration_decision(batch_id: str, request: Request):
        payload = await json_object(request)
        raw_decision = payload.get("decision")
        task_id = payload.get("workbench_task_id", "")
        turn_id = payload.get("workbench_turn_id", "")
        if (
            not isinstance(task_id, str)
            or not isinstance(turn_id, str)
            or not task_id.strip()
            or not turn_id.strip()
        ):
            return JSONResponse({"ok": False, "code": "validation_error", "message": "workbench_task_id and workbench_turn_id must be non-empty strings", "details": {}}, status_code=422)
        try:
            decision = FeedbackIterationDecision.model_validate(raw_decision)
            record = store_factory().record_feedback_iteration_decision(
                batch_id,
                decision,
                workbench_task_id=task_id,
                workbench_turn_id=turn_id,
            )
        except FeedbackIterationAssociationMismatchError as exc:
            return JSONResponse({"ok": False, "code": exc.error_code, "message": "反馈决策必须绑定当前处理轮", "details": {}}, status_code=409)
        except (ValueError, TypeError) as exc:
            return JSONResponse({"ok": False, "code": "validation_error", "message": str(exc), "details": {}}, status_code=422)
        return json_safe(record)

    @app.patch("/api/console/feedback/batches/{batch_id}")
    async def console_feedback_batch_association(batch_id: str, request: Request):
        payload = await json_object(request)
        task_id = payload.get("workbench_task_id", payload.get("task_id", ""))
        turn_id = payload.get("workbench_turn_id", payload.get("turn_id", ""))
        if not isinstance(task_id, str) or not isinstance(turn_id, str) or not task_id.strip() or not turn_id.strip():
            raise HTTPException(status_code=400, detail="workbench_task_id and workbench_turn_id are required")
        store = store_factory()
        batch = store.get_feedback_processing_batch(batch_id)
        if batch is None:
            return JSONResponse({"ok": False, "code": "not_found", "message": "Feedback batch not found", "details": {}}, status_code=404)
        if batch.status != "processing":
            return JSONResponse({"ok": False, "code": "feedback_batch_conflict", "message": "Only the current processing batch may be associated", "details": {}}, status_code=409)
        try:
            for item in _feedback_items_for_batch(store, batch.batch_id):
                store.associate_feedback_processing_turn(
                    item["feedback_key"],
                    expected_batch_id=batch.batch_id,
                    workbench_task_id=task_id.strip(),
                    workbench_turn_id=turn_id.strip(),
                )
        except ValueError:
            return JSONResponse({"ok": False, "code": "feedback_batch_conflict", "message": "Only the current processing batch may be associated", "details": {}}, status_code=409)
        refreshed = store.get_feedback_processing_batch(batch.batch_id)
        return command_result(item={"batch_id": batch.batch_id, "status": refreshed.status if refreshed else batch.status, "requested_count": refreshed.requested_count if refreshed else batch.requested_count, "items": _feedback_items_for_batch(store, batch.batch_id), "workbench_task_id": task_id.strip(), "workbench_turn_id": turn_id.strip()}, message="反馈批次关联已保存")

    @app.patch("/api/console/feedback/items/{feedback_id}")
    async def console_feedback_item_patch(feedback_id: str, request: Request):
        payload = await json_object(request)
        allowed = {"test_evidence", "tests", "restart_evidence", "restart", "health_evidence", "health", "commit_sha", "note", "status", "workbench_task_id", "task_id", "workbench_turn_id", "turn_id", "attempt_id", "agent_run_id", "associations"}
        unknown = set(payload) - allowed
        if unknown:
            raise HTTPException(status_code=400, detail="unsupported feedback evidence field")
        kwargs = {
            "test_evidence": payload.get("test_evidence", payload.get("tests")),
            "restart_evidence": payload.get("restart_evidence", payload.get("restart")),
            "health_evidence": payload.get("health_evidence", payload.get("health")),
            "commit_sha": payload.get("commit_sha"), "note": payload.get("note"), "status": payload.get("status"),
        }
        if kwargs["status"] == "resolved":
            return JSONResponse({"ok": False, "code": "feedback_evidence_invalid", "message": "only batch resolution may mark feedback resolved", "details": {}}, status_code=409)
        if kwargs["status"] is not None and kwargs["status"] not in {"pending", "processing"}:
            return JSONResponse({"ok": False, "code": "feedback_evidence_invalid", "message": "unsupported feedback processing status", "details": {}}, status_code=409)
        for name in ("test_evidence", "restart_evidence", "health_evidence"):
            if kwargs[name] is not None and (not isinstance(kwargs[name], dict) or isinstance(kwargs[name], list)):
                raise HTTPException(status_code=400, detail=f"{name} must be a JSON object")
        for name in ("commit_sha", "note", "status"):
            if kwargs[name] is not None and not isinstance(kwargs[name], str):
                raise HTTPException(status_code=400, detail=f"{name} must be a string")
        store = store_factory()
        current = store.get_feedback_processing_item(feedback_id)
        if current is None:
            return JSONResponse({"ok": False, "code": "not_found", "message": "Feedback item not found", "details": {}}, status_code=404)
        if kwargs["status"] == "processing" or (kwargs["status"] == "pending" and current.status != "pending"):
            return JSONResponse({"ok": False, "code": "feedback_evidence_invalid", "message": "only atomic batch claim may mark feedback processing", "details": {}}, status_code=409)
        associations = payload.get("associations", {})
        if not isinstance(associations, dict):
            raise HTTPException(status_code=400, detail="associations must be a JSON object")
        task_id = payload.get("workbench_task_id", payload.get("task_id", associations.get("workbench_task_id", associations.get("task_id", current.workbench_task_id))))
        turn_id = payload.get("workbench_turn_id", payload.get("turn_id", associations.get("workbench_turn_id", associations.get("turn_id", current.workbench_turn_id))))
        attempt_id = payload.get("attempt_id", associations.get("attempt_id", current.attempt_id))
        agent_run_id = payload.get("agent_run_id", associations.get("agent_run_id", current.agent_run_id))
        if not isinstance(task_id, str) or not isinstance(turn_id, str) or not isinstance(attempt_id, int) or isinstance(attempt_id, bool) or not isinstance(agent_run_id, int) or isinstance(agent_run_id, bool):
            raise HTTPException(status_code=400, detail="feedback associations have invalid types")
        if "associations" in payload or any(name in payload for name in ("workbench_task_id", "task_id", "workbench_turn_id", "turn_id", "attempt_id", "agent_run_id")):
            try:
                store.associate_feedback_processing_turn(
                    feedback_id,
                    expected_batch_id=current.batch_id,
                    workbench_task_id=task_id.strip(),
                    workbench_turn_id=turn_id.strip(),
                    attempt_id=attempt_id,
                    agent_run_id=agent_run_id,
                )
            except ValueError as exc:
                return JSONResponse({"ok": False, "code": "feedback_evidence_invalid", "message": str(exc), "details": {}}, status_code=409)
        try:
            item = store.patch_feedback_processing_item_evidence(feedback_id, **kwargs)
        except (TypeError, ValueError) as exc:
            return JSONResponse({"ok": False, "code": "feedback_evidence_invalid", "message": str(exc), "details": {}}, status_code=409)
        if item is None:
            return JSONResponse({"ok": False, "code": "not_found", "message": "Feedback item not found", "details": {}}, status_code=404)
        return command_result(item=item, message="反馈证据已保存")

    @app.post("/api/console/feedback/items/{feedback_id}/reopen")
    async def console_feedback_item_reopen(feedback_id: str, request: Request):
        try:
            payload = await json_object(request)
        except HTTPException:
            payload = {}
        reason = payload.get("reason")
        if (
            set(payload) != {"reason"}
            or not isinstance(reason, str)
            or not reason.strip()
        ):
            return JSONResponse(
                {
                    "ok": False,
                    "code": "feedback_reopen_invalid",
                    "message": "Feedback reopen requires exactly one non-blank reason",
                    "details": {},
                },
                status_code=422,
            )
        store = store_factory()
        try:
            reopened = store.reopen_feedback_processing_item(
                feedback_id,
                reason=reason,
            )
        except FeedbackProcessingReopenError as exc:
            return JSONResponse(
                {
                    "ok": False,
                    "code": exc.error_code,
                    "message": str(exc),
                    "details": {},
                },
                status_code=409,
            )
        if reopened is None:
            return JSONResponse(
                {
                    "ok": False,
                    "code": "not_found",
                    "message": "Feedback item not found",
                    "details": {},
                },
                status_code=404,
            )
        refreshed = _feedback_processing_state(store, reopened.feedback_key)
        return command_result(item=refreshed, message="反馈已重新打开")

    @app.post("/api/console/feedback/batches/{batch_id}/resolve")
    async def console_feedback_batch_resolve(batch_id: str, request: Request):
        payload = await json_object(request)
        allowed = {
            "commit_sha",
            "test_evidence",
            "tests",
            "restart_evidence",
            "restart",
            "health_evidence",
            "health",
            "associations",
        }
        if set(payload) - allowed:
            return JSONResponse(
                {
                    "ok": False,
                    "code": "feedback_resolution_invalid",
                    "message": "unsupported feedback resolution field",
                    "details": {},
                },
                status_code=409,
            )
        try:
            backlog_evidence = fresh_zero_feedback_backlog()
        except Exception:
            return JSONResponse(
                {
                    "ok": False,
                    "code": "feedback_resolution_incomplete",
                    "message": "Fresh local backlog evidence is unavailable or non-zero",
                    "details": {},
                },
                status_code=409,
            )
        try:
            evidence = ResolutionEvidence.model_validate(
                {**payload, "backlog_evidence": backlog_evidence}
            )
        except Exception as exc:
            return JSONResponse({"ok": False, "code": "feedback_resolution_invalid", "message": str(exc), "details": {}}, status_code=409)
        try:
            from app.config import repo_root
            commit_sha = evidence.commit_sha.strip()
            if len(commit_sha) != 40 or any(
                character not in "0123456789abcdefABCDEF" for character in commit_sha
            ):
                raise ValueError("resolution requires a 40-character commit SHA")
            commit_check = subprocess.run(
                ["git", "cat-file", "-e", f"{commit_sha}^{{commit}}"],
                cwd=repo_root(),
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            if commit_check.returncode != 0:
                raise ValueError("resolution commit does not exist")
            ancestor_check = subprocess.run(
                ["git", "merge-base", "--is-ancestor", commit_sha, "main"],
                cwd=repo_root(),
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            if ancestor_check.returncode != 0:
                raise ValueError("resolution commit is not an ancestor of local main")
            resolved = store_factory().resolve_feedback_processing_batch(
                batch_id,
                evidence,
                commit_is_ancestor=True,
            )
        except (ValueError, subprocess.SubprocessError, OSError) as exc:
            return JSONResponse({"ok": False, "code": "feedback_resolution_incomplete", "message": str(exc), "details": {}}, status_code=409)
        if not resolved:
            return JSONResponse({"ok": False, "code": "not_found", "message": "Feedback batch not found", "details": {}}, status_code=404)
        return command_result(item={"batch_id": batch_id, "status": "resolved"}, message="反馈批次已解决")

    @app.get("/api/console/feedback/{feedback_id}")
    def console_feedback_detail(feedback_id: str):
        store = store_factory()
        row = next((candidate for candidate in store.list_user_feedback_items(limit=10000, offset=0) if candidate.key == feedback_id.strip()), None)
        if row is None:
            return JSONResponse({"ok": False, "code": "not_found", "message": "Feedback not found", "details": {}}, status_code=404)
        processing = store.get_feedback_processing_item(row.key)
        processing_state = (
            _feedback_processing_state(store, row.key) if processing else None
        )
        return item_envelope({
            "id": row.key, "feedback_key": row.key, "feedback_token": row.feedback_token,
            "rating": row.rating_label or row.rating, "comment": row.comment,
            "source": row.source, "received_at": row.received_at,
            "attempt_id": str(row.attempt_id) if row.attempt_id else "", "agent_run_id": row.agent_run_id,
            "conversation_title": row.conversation_title, "trigger_sender": row.trigger_sender,
            "trigger_text": row.trigger_text, "summary": persisted_feedback_summary(row),
            "references": detail_references(row),
            "status": project_feedback_status(row, processing),
            "batch_id": processing.batch_id if processing else "",
            "processing_task_id": processing.workbench_task_id if processing else "",
            "processing": json_safe(processing) if processing else None,
            "current_processing": (
                processing_state["current_processing"] if processing_state else None
            ),
            "processing_history": (
                processing_state["processing_history"] if processing_state else []
            ),
        })

    @app.post("/api/console/feedback/{feedback_id}/resolve")
    def console_feedback_resolve(feedback_id: str):
        return JSONResponse({"ok": False, "code": "feedback_batch_required", "message": "Feedback must be resolved through a processing batch", "details": {"feedback_id": feedback_id}}, status_code=409)

    @app.post("/api/console/feedback/sync")
    def console_feedback_sync():
        from app.audit_web import handle_user_feedback_sync_post
        status, _headers, _body = handle_user_feedback_sync_post(store_factory())
        if status >= 400:
            return JSONResponse({"ok": False, "code": "sync_failed", "message": "同步反馈失败", "details": {}}, status_code=status)
        return command_result(message="已同步最新反馈")

    def _skill_error_response(exc: SkillFileValidationError) -> JSONResponse:
        message = str(exc)
        if message.startswith("unknown Skill:"):
            return JSONResponse(
                {"ok": False, "code": "not_found", "message": "Skill not found", "details": {}},
                status_code=404,
            )
        return JSONResponse(
            {"ok": False, "code": "validation_error", "message": "Skill 校验失败", "details": {"reason": message}},
            status_code=422,
        )

    def _skill_payload(document: Any, registry: FeatureRegistry) -> dict[str, Any]:
        return {
            "name": document.name,
            "description": document.description,
            "managed_by": document.managed_by,
            "path": str(document.path),
            "content": document.content,
            "sha256": document.sha256,
            "referenced_by": _skill_references(registry, document.name),
        }

    def _skill_references(registry: FeatureRegistry, name: str) -> list[str] | None:
        try:
            return list(registry.features_for_skill(name))
        except ValueError:
            # Discovery is intentionally broader than registry-name validation:
            # expose malformed project directories as invalid rows instead of
            # allowing one bad directory to break the complete catalog.
            return None

    def _available_skill_names(service: SkillFileService) -> set[str]:
        available: set[str] = set()
        for project_skill in service.list_skills():
            try:
                document = service.get_skill(project_skill.name)
            except SkillFileValidationError:
                continue
            available.add(document.name)
        return available

    def _managed_skill_payload(skill: Any) -> dict[str, Any]:
        return {
            "id": skill.id,
            "name": skill.name,
            "display_name": skill.display_name,
            "created_at": skill.created_at,
        }

    def managed_store() -> Any:
        """Open the Settings control plane with its baseline safely reconciled."""
        store = store_factory()
        import_repository_managed_skills(store)
        return store

    def _managed_revision_payload(store: Any, revision: Any) -> dict[str, Any]:
        receipt = store.latest_managed_skill_export_receipt(revision.id)
        export = {
            "status": "exported" if receipt is not None else "not_exported",
            "receipt": (
                {
                    "id": receipt.id,
                    "revision_id": receipt.revision_id,
                    "sha256": receipt.sha256,
                    "path": receipt.path,
                    "created_at": receipt.created_at,
                }
                if receipt is not None
                else None
            ),
        }
        return {
            "id": revision.id,
            "skill_id": revision.skill_id,
            "revision_number": revision.revision_number,
            "content": revision.content,
            "sha256": revision.sha256,
            "parent_revision_id": revision.parent_revision_id,
            "source": revision.source,
            "created_at": revision.created_at,
            "export": export,
        }

    def _runtime_config_payload(store: Any, config: Any) -> dict[str, Any]:
        return {
            "id": config.id,
            "parent_id": config.parent_id,
            "status": config.status,
            "created_at": config.created_at,
            "bindings": [
                {
                    "skill_id": binding.skill_id,
                    "revision_id": binding.revision_id,
                    "enabled": binding.enabled,
                    "load_order": binding.load_order,
                    "purpose": binding.purpose,
                }
                for binding in store.list_runtime_skill_bindings(config.id)
            ],
        }

    @app.get("/api/console/settings/managed-skills")
    def console_managed_skills():
        return {"items": [_managed_skill_payload(skill) for skill in managed_store().list_managed_skills()]}

    @app.post("/api/console/settings/managed-skills", status_code=201)
    async def console_create_managed_skill(request: Request):
        payload = await json_object(request)
        try:
            name = validate_managed_skill_name(payload.get("name"))
            skill = managed_store().create_managed_skill(
                name, payload.get("display_name")
            )
        except ValueError as exc:
            status = 409 if "already exists" in str(exc) else 422
            return JSONResponse(
                {"ok": False, "code": "conflict" if status == 409 else "validation_error", "message": str(exc), "details": {}},
                status_code=status,
            )
        return _managed_skill_payload(skill)

    @app.get("/api/console/settings/managed-skills/{skill_id}/revisions")
    def console_managed_skill_revisions(skill_id: int):
        store = managed_store()
        if store.get_managed_skill(skill_id) is None:
            return JSONResponse({"ok": False, "code": "not_found", "message": "Managed Skill not found", "details": {}}, status_code=404)
        return {"items": [_managed_revision_payload(store, revision) for revision in store.list_managed_skill_revisions(skill_id)]}

    @app.post("/api/console/settings/managed-skills/{skill_id}/revisions", status_code=201)
    async def console_create_managed_skill_revision(skill_id: int, request: Request):
        payload = await json_object(request)
        if set(payload) - {"content", "parent_revision_id"}:
            return JSONResponse({"ok": False, "code": "validation_error", "message": "unsupported managed revision fields", "details": {}}, status_code=422)
        try:
            revision = managed_store().create_managed_skill_revision(
                skill_id,
                payload.get("content"),
                source="settings",
                parent_revision_id=payload.get("parent_revision_id"),
            )
        except ManagedSkillValidationError as exc:
            return JSONResponse({"ok": False, "code": "validation_error", "message": str(exc), "details": {}}, status_code=422)
        except ValueError as exc:
            message = str(exc)
            status = 404 if "does not exist" in message else 409 if "already exists" in message else 422
            return JSONResponse({"ok": False, "code": "not_found" if status == 404 else "conflict" if status == 409 else "validation_error", "message": message, "details": {}}, status_code=status)
        return _managed_revision_payload(managed_store(), revision)

    @app.get("/api/console/settings/managed-skill-revisions/{revision_id}")
    def console_managed_skill_revision(revision_id: int):
        revision = managed_store().get_managed_skill_revision(revision_id)
        if revision is None:
            return JSONResponse({"ok": False, "code": "not_found", "message": "Managed Skill revision not found", "details": {}}, status_code=404)
        return _managed_revision_payload(managed_store(), revision)

    @app.post("/api/console/settings/managed-skill-revisions/{revision_id}/export")
    def console_export_managed_skill_revision(revision_id: int):
        store = managed_store()
        try:
            exported = export_managed_skill_revision(store, revision_id)
        except ManagedSkillValidationError as exc:
            return JSONResponse({"ok": False, "code": "validation_error", "message": str(exc), "details": {}}, status_code=422)
        except ValueError as exc:
            return JSONResponse({"ok": False, "code": "not_found", "message": str(exc), "details": {}}, status_code=404)
        receipt = exported.receipt
        return {
            "name": exported.name,
            "revision_id": exported.revision_id,
            "sha256": exported.sha256,
            "path": str(exported.path),
            "export": {
                "status": "exported",
                "receipt": {
                    "id": receipt.id,
                    "revision_id": receipt.revision_id,
                    "sha256": receipt.sha256,
                    "path": receipt.path,
                    "created_at": receipt.created_at,
                },
            },
        }

    @app.post("/api/console/settings/runtime-skill-configs", status_code=201)
    async def console_create_runtime_skill_config(request: Request):
        payload = await json_object(request)
        if set(payload) != {"expected_parent_id", "bindings"}:
            return JSONResponse({"ok": False, "code": "validation_error", "message": "expected_parent_id and bindings are required", "details": {}}, status_code=422)
        try:
            store = managed_store()
            config = store.create_runtime_skill_config(
                payload["bindings"], expected_parent_id=payload["expected_parent_id"]
            )
        except ValueError as exc:
            message = str(exc)
            status = 409 if "parent conflict" in message else 422
            return JSONResponse({"ok": False, "code": "conflict" if status == 409 else "validation_error", "message": message, "details": {}}, status_code=status)
        return _runtime_config_payload(store, config)

    @app.get("/api/console/settings/runtime-skill-configs/current")
    def console_current_runtime_skill_config():
        store = managed_store()
        selected = store.get_pending_or_active_runtime_skill_config()
        active = store.get_active_runtime_skill_config()
        return {
            "pending_or_active": _runtime_config_payload(store, selected) if selected else None,
            "active": _runtime_config_payload(store, active) if active else None,
        }

    @app.get("/api/console/settings/feedback-iteration")
    def console_feedback_iteration_capability():
        store = managed_store()
        selected = store.get_pending_or_active_runtime_skill_config()
        active = store.get_active_runtime_skill_config()
        return {
            "enabled": store.feedback_iteration_enabled(),
            "config_id": selected.id if selected is not None else None,
            "status": selected.status if selected is not None else "unconfigured",
            "active": (
                {"enabled": store.feedback_iteration_enabled(active.id), "config_id": active.id, "status": active.status}
                if active is not None else None
            ),
        }

    @app.post("/api/console/settings/feedback-iteration")
    async def console_set_feedback_iteration_capability(request: Request):
        payload = await json_object(request)
        if set(payload) != {"enabled"} or type(payload.get("enabled")) is not bool:
            return JSONResponse({"ok": False, "code": "validation_error", "message": "enabled must be a boolean", "details": {}}, status_code=422)
        try:
            store = managed_store()
            config = store.set_feedback_iteration_enabled(payload["enabled"])
        except ValueError as exc:
            return JSONResponse({"ok": False, "code": "conflict", "message": str(exc), "details": {}}, status_code=409)
        active = store.get_active_runtime_skill_config()
        return {"enabled": store.feedback_iteration_enabled(config.id), "config_id": config.id, "status": config.status,
                "active": ({"enabled": store.feedback_iteration_enabled(active.id), "config_id": active.id, "status": active.status} if active is not None else None)}

    @app.get("/api/console/settings/runtime-skill-configs/{config_id}/load-receipts")
    def console_runtime_skill_load_receipts(config_id: int):
        store = managed_store()
        if store.get_runtime_skill_config(config_id) is None:
            return JSONResponse({"ok": False, "code": "not_found", "message": "Runtime Skill configuration not found", "details": {}}, status_code=404)
        return {"items": [
            {"id": receipt.id, "config_id": receipt.config_id, "pid": receipt.pid,
             "loaded_json": receipt.loaded_json, "error": receipt.error,
             "created_at": receipt.created_at}
            for receipt in store.list_runtime_skill_load_receipts(config_id)
        ]}

    @app.get("/api/console/settings/skills")
    def console_settings_skills():
        registry = feature_registry_factory()
        service = skill_file_service_factory()
        documents: dict[str, Any] = {}
        skills: list[dict[str, Any]] = []
        for project_skill in service.list_skills():
            references = _skill_references(registry, project_skill.name)
            if references is None:
                skills.append(
                    {
                        "name": project_skill.name,
                        "description": "",
                        "managed_by": "",
                        "path": str(project_skill.path),
                        "content": "",
                        "sha256": "",
                        "referenced_by": [],
                        "status": "invalid",
                        "error": f"invalid Skill name: {project_skill.name!r}",
                    }
                )
                continue
            try:
                document = service.get_skill(project_skill.name)
            except SkillFileValidationError as exc:
                skills.append(
                    {
                        "name": project_skill.name,
                        "description": "",
                        "managed_by": "",
                        "path": str(project_skill.path),
                        "content": "",
                        "sha256": "",
                        "referenced_by": references,
                        "status": "invalid",
                        "error": str(exc),
                    }
                )
                continue
            documents[document.name] = document
            skills.append({**_skill_payload(document, registry), "status": "ready"})
        available_skills = set(documents)
        features = [
            {
                "feature_id": definition.feature_id,
                "name": definition.name,
                "description": definition.description,
                "skills": list(definition.skills),
                "enabled": registry.is_enabled(definition.feature_id),
                "status": registry.feature_status(definition.feature_id, available_skills),
            }
            for definition in registry.list_features()
        ]
        return {"features": features, "skills": skills}

    @app.post("/api/console/settings/skills/{feature_id}/toggle")
    async def console_settings_skill_toggle(feature_id: str, request: Request):
        payload = await json_object(request)
        enabled = payload.get("enabled")
        if type(enabled) is not bool:
            return JSONResponse(
                {"ok": False, "code": "validation_error", "message": "enabled must be a boolean", "details": {}},
                status_code=422,
            )
        registry = feature_registry_factory()
        try:
            state = registry.set_enabled(feature_id, enabled)
        except KeyError:
            return JSONResponse(
                {"ok": False, "code": "not_found", "message": "Feature not found", "details": {}},
                status_code=404,
            )
        except ValueError as exc:
            return JSONResponse(
                {"ok": False, "code": "validation_error", "message": str(exc), "details": {}},
                status_code=422,
            )
        except (OSError, IOError):
            return JSONResponse(
                {"ok": False, "code": "persistence_failed", "message": "功能状态保存失败", "details": {}},
                status_code=500,
            )
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "code": "persistence_failed", "message": "功能状态保存失败", "details": {"reason": normalize_display_value(exc)}},
                status_code=500,
            )
        return {
            "feature_id": state.feature_id,
            "enabled": state.enabled,
            "status": registry.feature_status(
                state.feature_id,
                _available_skill_names(skill_file_service_factory()),
            ),
        }

    @app.get("/api/console/settings/skills/{skill_name}")
    def console_settings_skill_detail(skill_name: str):
        registry = feature_registry_factory()
        if _skill_references(registry, skill_name) is None:
            return JSONResponse(
                {"ok": False, "code": "validation_error", "message": "invalid Skill name", "details": {}},
                status_code=422,
            )
        try:
            document = skill_file_service_factory().get_skill(skill_name)
        except SkillFileValidationError as exc:
            return _skill_error_response(exc)
        return _skill_payload(document, registry)

    @app.put("/api/console/settings/skills/{skill_name}")
    async def console_settings_skill_update(skill_name: str, request: Request):
        await json_object(request)
        return JSONResponse(
            {
                "ok": False,
                "code": "managed_skill_migration_required",
                "message": "Create an immutable managed Skill revision instead.",
                "details": {"managed_revisions_path": "/api/console/settings/managed-skills"},
            },
            status_code=410,
        )

    @app.get("/api/console/settings/{section}")
    def console_settings(section: str):
        payload: Any = None
        allowed = {"status", "info", "configuration", "agent-runtime", "prompts", "connectors", "audit-rules", "attention"}
        if section not in allowed:
            return JSONResponse({"ok": False, "code": "not_found", "message": "Unknown settings section", "details": {}}, status_code=404)
        if section == "status":
            payload = json_safe(status_payload_factory())
        elif section == "attention":
            payload = {"items": [json_safe(item) for item in group_attention_rows(attention_rows_factory())]}
        elif section == "connectors":
            payload = json_safe(status_payload_factory()).get("connectors", {})
        else:
            from app import config as app_config
            if section == "info":
                fields = {"principal": app_config.principal_display_name(), "workspace": str(app_config.workspace_path()), "repository": str(app_config.repo_root())}
                payload = {"fields": fields, **info_payload()}
            elif section == "configuration":
                from app.audit_web import (
                    _configuration_compatibility_entries,
                    _configuration_entries,
                )
                groups = []
                for name, rows in _configuration_entries().items():
                    groups.append(
                        {
                            "name": name,
                            "items": [
                                {
                                    "key": key,
                                    "value": value,
                                    "description": description,
                                    "editable": editable,
                                }
                                for key, value, description, editable in rows
                            ],
                        }
                    )
                fields = {
                    item["key"]: item["value"]
                    for group in groups
                    for item in group["items"]
                }
                payload = {
                    "section": section,
                    "fields": fields,
                    "groups": groups,
                    "compatibility": [
                        {
                            "key": key,
                            "value": value,
                            "description": description,
                            "editable": editable,
                        }
                        for key, value, description, editable in _configuration_compatibility_entries()
                    ],
                }
            elif section == "prompts":
                from app.developer_prompt import (
                    read_developer_prompt_template,
                    read_user_prompt_template,
                    render_developer_prompt_template,
                    render_user_prompt_template,
                )
                developer_template = read_developer_prompt_template()
                user_template = read_user_prompt_template()
                fields = {"developer_template": developer_template, "user_template": user_template}
                payload = {
                    "section": section,
                    "fields": fields,
                    "preview": {
                        "developer": render_developer_prompt_template(developer_template),
                        "user": render_user_prompt_template(user_template, {}),
                    },
                }
            elif section == "audit-rules":
                from app.audit_rules import (
                    AgentRole,
                    _render_audit_variables,
                    read_audit_rules_template,
                    render_audit_rules,
                )
                template = read_audit_rules_template()
                fields = {"template": template}
                payload = {
                    "section": section,
                    "fields": fields,
                    "preview": {
                        "template": _render_audit_variables(template),
                        "consumer": render_audit_rules(AgentRole.CONSUMER),
                        "audit": render_audit_rules(AgentRole.AUDIT),
                    },
                }
            else:
                env = app_config.read_env_file()
                fields = {key: env.get(key, "") for key in (
                    "CEO_CODEX_MODEL", "CEO_CODEX_MODEL_REASONING_EFFORT",
                    "CEO_AGENT_RUNTIME_ROUTES", "CEO_CODEX_API_BASE_URL",
                    "CEO_CODEX_API_MODEL", "CEO_CODEX_API_KEY",
                    "CEO_FRIDAY_RUNTIME_BASE_URL", "CEO_FRIDAY_RUNTIME_PROJECT_ID",
                    "CEO_FRIDAY_RUNTIME_PROVIDER_BASE_URL",
                    "CEO_FRIDAY_RUNTIME_PROVIDER_MODEL",
                    "CEO_FRIDAY_RUNTIME_PROVIDER_API_KEY",
                    "CEO_FRIDAY_RUNTIME_AUTH_DISABLED",
                    "CEO_FRIDAY_RUNTIME_TICKET", "CEO_FRIDAY_SESSION_TOKEN",
                )}
            if payload is None:
                payload = {"section": section, "fields": fields}
            payload["secrets"] = ["CEO_CODEX_API_KEY", "CEO_FRIDAY_RUNTIME_TICKET", "CEO_FRIDAY_SESSION_TOKEN"] if section == "agent-runtime" else []
        return item_envelope(payload)

    @app.get("/api/console/tutorial")
    def console_tutorial():
        from app.audit_web import build_wizard_status
        return item_envelope(json_safe(build_wizard_status(store_factory())))

    @app.post("/api/console/connectors/{connector}/login")
    def console_connector_login(connector: str):
        from app.channel_gate import start_connector_auth_login
        try:
            command, process = start_connector_auth_login(connector)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return command_result(
            item={"connector": connector, "command": command, "pid": process.pid, "started": True},
            message="登录窗口已启动，请完成网页授权后刷新状态。",
        )

    @app.post("/api/console/tutorial/check/{step_id}")
    async def console_tutorial_check(step_id: str, request: Request):
        from app.audit_web import _require_available_setup_action, _repo_root, check_setup_step, get_step_definition
        store = store_factory()
        try:
            step = get_step_definition(step_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown setup step") from exc
        _require_available_setup_action(store, f"check_{step.id}", kind="check")
        result = check_setup_step(step_id, repo_root=_repo_root(), store=store)
        store.upsert_setup_wizard_step(step_id=result.step_id, status=result.status, summary=result.summary)
        return command_result(item=json_safe(result), message=result.summary)

    @app.post("/api/console/tutorial/run/{action_id}")
    def console_tutorial_run(action_id: str, request: Request):
        from app.audit_web import _require_available_setup_action, _repo_root, run_setup_action
        store = store_factory()
        _require_available_setup_action(store, action_id, kind="run")
        event = run_setup_action(action_id, repo_root=_repo_root(), env=dict(__import__("os").environ))
        store.record_setup_wizard_event(step_id=event.step_id, action_id=event.action_id, status=event.status, summary=event.summary, evidence_json=json.dumps(event.evidence, ensure_ascii=False), stdout_excerpt=event.stdout_excerpt, stderr_excerpt=event.stderr_excerpt)
        if event.step_id != "unknown":
            store.upsert_setup_wizard_step(
                step_id=event.step_id,
                status=event.next_step_status
                or ("done" if event.status == "done" else "failed"),
                summary=event.summary,
            )
        return command_result(item=json_safe(event), message=event.summary, ok=event.status == "done")

    @app.post("/api/console/tutorial/confirm/{step_id}")
    async def console_tutorial_confirm(step_id: str, request: Request):
        payload = await json_object(request)
        from app.audit_web import _require_available_setup_action, confirm_setup_step
        store = store_factory()
        _require_available_setup_action(store, f"confirm_{step_id}", kind="confirm")
        event = confirm_setup_step(step_id, store=store, confirmed_by=str(payload.get("confirmed_by") or "local-user"), evidence={str(k): str(v) for k, v in (payload.get("evidence") or {}).items()})
        store.record_setup_wizard_event(step_id=event.step_id, action_id=event.action_id, status=event.status, summary=event.summary, evidence_json=json.dumps(event.evidence, ensure_ascii=False), stdout_excerpt=event.stdout_excerpt, stderr_excerpt=event.stderr_excerpt)
        return command_result(item=json_safe(event), message=event.summary)

    @app.get("/api/console/notifications")
    def console_notifications():
        rows = [{"id": row["id"], "category": row["category"], "summary": row["summary"], "updated_at": row["updated_at"]} for row in attention_rows_factory()]
        return list_envelope(rows, page=1, page_size=max(20, len(rows)), total=len(rows))

    @app.get("/api/console/codex/sessions")
    def console_codex_sessions():
        rows = []
        for conversation in store_factory().list_codex_conversations():
            rows.append({"id": conversation.codex_session_id or conversation.conversation_id,
                         "title": conversation.title, "type": "single" if conversation.single_chat else "group",
                         "detail_url": f"/codex/{conversation.codex_session_id}" if conversation.codex_session_id else ""})
        return list_envelope(rows, page=1, page_size=max(20, len(rows)), total=len(rows))

    @app.get("/api/console/codex/sessions/{session_id}")
    def console_codex_session(session_id: str):
        from app.codex_history import render_local_codex_session
        rendered = render_local_codex_session(session_id)
        related = store_factory().list_reply_attempts_for_codex_session(session_id)
        return item_envelope({"session_id": session_id, "available": not rendered.missing,
                              "events": [json_safe(event.__dict__) for event in rendered.events] if not rendered.missing else [],
                              "related_attempts": [{"id": item.id, "status": item.send_status} for item in related],
                              "message": "本机执行记录不可用" if rendered.missing else ""})

    @app.get("/api/console/wechat/review")
    def console_wechat_review():
        store = store_factory()
        rows = []
        for status in ("ready_to_send", "sending", "sent", "send_unknown", "failed"):
            for delivery in store.list_wechat_deliveries_by_status(status):
                rows.append(json_safe(delivery))
        return list_envelope(rows, page=1, page_size=max(20, len(rows)), total=len(rows))

    @app.get("/api/console/wechat/deliveries")
    def console_wechat_deliveries():
        return console_wechat_review()

    @app.get("/api/console/wechat/conversations")
    def console_wechat_conversations():
        store = store_factory()
        rows = []
        for state in store.list_wechat_read_states():
            account_id = str(state.get("account_id") or "")
            rows.extend(json_safe(scope) for scope in store.list_wechat_reply_scopes(account_id))
        return list_envelope(rows, page=1, page_size=max(20, len(rows)), total=len(rows))

    @app.get("/api/console/wechat/targets")
    def console_wechat_targets(
        query: str = "", kind: str = "all", limit: int = 50, offset: int = 0,
    ):
        if kind not in {"all", "direct", "group"} or not 1 <= limit <= 100 or offset < 0:
            return JSONResponse(
                {"ok": False, "code": "validation_error", "message": "目标查询参数无效", "details": {}},
                status_code=422,
            )
        store = store_factory()
        from app.wechat import service as wechat_service

        state = wechat_service.ready_account_state(store)
        if state is None:
            response = list_envelope([], page=1, page_size=limit, total=0)
            response["account_id"] = ""
            return response
        try:
            setup = wechat_service.build_setup_service(store)
            kinds = (kind,) if kind != "all" else ("direct", "group")
            candidates = [
                item
                for target_kind in kinds
                for item in setup.list_targets(
                    query=query, kind=target_kind, limit=100, offset=0,
                )
            ]
        except Exception as exc:
            return JSONResponse(
                {
                    "ok": False,
                    "code": "wechat_targets_unavailable",
                    "message": "暂时无法读取微信联系人，请确认 WeChat Reader 已连接。",
                    "details": {"reason": normalize_display_value(exc)},
                },
                status_code=409,
            )
        candidates.sort(
            key=lambda item: (
                str(item.get("display_name", "")).casefold(),
                str(item.get("target_type", "")),
                str(item.get("target_id", "")),
            )
        )
        total = len(candidates)
        page_items = candidates[offset:offset + limit]
        response = list_envelope(
            page_items,
            page=offset // limit + 1,
            page_size=limit,
            total=total,
        )
        response["account_id"] = str(state.get("account_id") or "")
        return response

    @app.get("/api/console/wechat/memory-review")
    def console_wechat_memory_review():
        rows = store_factory().list_wechat_memory_candidates()
        return list_envelope(rows, page=1, page_size=max(20, len(rows)), total=len(rows))

    @app.post("/api/console/wechat/memory-review/{candidate_id}/{action}")
    async def console_wechat_memory_action(candidate_id: int, action: str, request: Request):
        payload = await json_object(request)
        if action not in {"approve", "reject", "revoke"}:
            return JSONResponse({"ok": False, "code": "validation_error", "message": "不支持的审核动作", "details": {}}, status_code=422)
        try:
            result = store_factory().review_wechat_memory_candidate(candidate_id, action, reviewer=str(payload.get("reviewer") or "local-user"), final_statement=str(payload.get("final_statement") or ""))
        except ValueError as exc:
            return JSONResponse({"ok": False, "code": "validation_error", "message": "Memory 候选审核失败", "details": {"reason": normalize_display_value(exc)}}, status_code=422)
        return command_result(item=result, message="审核状态已更新")

    @app.post("/api/console/wechat/memory-review/{candidate_id}/resolve-unknown")
    async def console_wechat_memory_resolve_unknown(candidate_id: int, request: Request):
        payload = await json_object(request)
        try:
            result = store_factory().resolve_wechat_memory_candidate_write_unknown(candidate_id, reviewer=str(payload.get("reviewer") or "local-user"), confirm=bool(payload.get("confirm")))
        except ValueError as exc:
            return JSONResponse({"ok": False, "code": "validation_error", "message": "无法解决 unknown 写入", "details": {"reason": normalize_display_value(exc)}}, status_code=422)
        return command_result(item=result, message="已记录 unknown 处理结果")

    @app.post("/api/console/wechat/deliveries/{delivery_id}/approve")
    async def console_wechat_approve(delivery_id: int, request: Request):
        from app.wechat import service
        try:
            from app.wechat.accessibility import WechatSender
            store = store_factory()
            sender = WechatSender(store, service.build_sender(), user_initiated=True)
            result = service.approve_wechat_delivery(store, sender, delivery_id)
        except Exception as exc:
            return JSONResponse({"ok": False, "code": "delivery_failed", "message": "发送失败", "details": {"reason": normalize_display_value(exc)}}, status_code=409)
        return command_result(item=result, message="发送动作已提交")

    @app.post("/api/console/wechat/deliveries/{delivery_id}/retry")
    async def console_wechat_retry_expired(delivery_id: int, request: Request):
        del request
        from app.wechat import service
        try:
            from app.wechat.accessibility import WechatSender
            store = store_factory()
            sender = WechatSender(store, service.build_sender(), user_initiated=True)
            result = service.retry_expired_wechat_delivery(store, sender, delivery_id)
        except Exception as exc:
            return JSONResponse({"ok": False, "code": "delivery_failed", "message": "无法重试这条微信消息", "details": {"reason": normalize_display_value(exc)}}, status_code=409)
        message = "微信消息已发送" if result == "sent" else f"微信发送状态：{result}"
        return command_result(item=result, message=message)

    @app.post("/api/console/wechat/deliveries/{delivery_id}/reject")
    async def console_wechat_reject(delivery_id: int, request: Request):
        try:
            store_factory().set_wechat_delivery_status(delivery_id, "failed", error="user_rejected")
        except Exception as exc:
            return JSONResponse({"ok": False, "code": "delivery_failed", "message": "拒绝失败", "details": {"reason": normalize_display_value(exc)}}, status_code=409)
        return command_result(message="已拒绝发送")

    @app.post("/api/console/wechat/reply-scope")
    async def console_wechat_reply_scope(request: Request):
        payload = await json_object(request)
        try:
            from app.wechat.audit_web import WechatReplyScopeRequest
            from app.wechat.models import WechatReplyScope

            parsed = WechatReplyScopeRequest.model_validate(payload)
            scopes = [
                WechatReplyScope(
                    account_id=parsed.account_id,
                    target_type=target.target_type,
                    target_id=target.target_id,
                    conversation_id=target.conversation_id or target.target_id,
                    display_name=target.display_name,
                    trigger_mode=target.trigger_mode,
                )
                for target in parsed.targets
            ]
            store_factory().replace_wechat_reply_scopes(parsed.account_id, scopes)
            result = {"account_id": parsed.account_id, "saved": len(scopes)}
        except Exception as exc:
            return JSONResponse({"ok": False, "code": "validation_error", "message": "回复范围保存失败", "details": {"reason": normalize_display_value(exc)}}, status_code=422)
        return command_result(item=result, message="回复范围已保存")

    @app.post("/api/console/settings/{section}")
    async def console_settings_command(section: str, request: Request):
        payload = await json_object(request)
        fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else payload
        if section == "configuration":
            encoded: dict[str, Any] = {"config_key": list(fields), "config_value": [str(value) for value in fields.values()]}
        elif section in {"prompts", "audit-rules"}:
            encoded = {"prompt": str(payload.get("prompt") or "developer"), "template": str(payload.get("template") or fields.get("template") or "")}
        elif section == "agent-runtime":
            from app import config as app_config

            routes = {
                route.strip()
                for route in str(fields.get("CEO_AGENT_RUNTIME_ROUTES") or "").split(",")
                if route.strip()
            }
            codex_api_enabled = fields.get("codex_api_enabled")
            if codex_api_enabled is None:
                codex_api_enabled = "1" if "codex_api" in routes else "0"
            friday_auth_disabled = fields.get("friday_runtime_auth_disabled")
            if friday_auth_disabled is None:
                friday_auth_disabled = fields.get("CEO_FRIDAY_RUNTIME_AUTH_DISABLED")
            if friday_auth_disabled is None:
                friday_auth_disabled = app_config.read_env_file().get("CEO_FRIDAY_RUNTIME_AUTH_DISABLED", "0")
            encoded = {
                "codex_model": str(fields.get("codex_model") or fields.get("CEO_CODEX_MODEL") or ""),
                "codex_reasoning_effort": str(fields.get("codex_reasoning_effort") or fields.get("CEO_CODEX_MODEL_REASONING_EFFORT") or ""),
                "codex_api_enabled": "1" if str(codex_api_enabled).lower() in {"1", "true", "yes", "on"} else "0",
                "codex_api_model": str(fields.get("codex_api_model") or fields.get("CEO_CODEX_API_MODEL") or ""),
                "codex_api_base_url": str(fields.get("codex_api_base_url") or fields.get("CEO_CODEX_API_BASE_URL") or ""),
                "codex_api_token": str(fields.get("codex_api_token") or fields.get("CEO_CODEX_API_KEY") or ""),
                "friday_runtime_settings_present": "1",
                "friday_runtime_enabled": "1" if "friday_runtime" in str(fields.get("CEO_AGENT_RUNTIME_ROUTES") or "").split(",") else "0",
                "friday_runtime_base_url": str(fields.get("friday_runtime_base_url") or fields.get("CEO_FRIDAY_RUNTIME_BASE_URL") or ""),
                "friday_runtime_project_id": str(fields.get("friday_runtime_project_id") or fields.get("CEO_FRIDAY_RUNTIME_PROJECT_ID") or ""),
                "friday_runtime_provider_base_url": str(fields.get("friday_runtime_provider_base_url") or fields.get("CEO_FRIDAY_RUNTIME_PROVIDER_BASE_URL") or ""),
                "friday_runtime_provider_model": str(fields.get("friday_runtime_provider_model") or fields.get("CEO_FRIDAY_RUNTIME_PROVIDER_MODEL") or ""),
                "friday_runtime_provider_api_key": str(fields.get("friday_runtime_provider_api_key") or fields.get("CEO_FRIDAY_RUNTIME_PROVIDER_API_KEY") or ""),
                "friday_runtime_ticket": str(fields.get("friday_runtime_ticket") or fields.get("CEO_FRIDAY_RUNTIME_TICKET") or ""),
                "friday_session_token": str(fields.get("friday_session_token") or fields.get("CEO_FRIDAY_SESSION_TOKEN") or ""),
                "friday_runtime_auth_disabled": str(friday_auth_disabled),
            }
        else:
            encoded = {str(k): str(v) for k, v in fields.items()}
        body = urlencode(encoded, doseq=True).encode()
        from app.audit_web import handle_configuration_post, handle_settings_prompt_post, handle_settings_audit_rules_post, handle_agent_runtime_config_post
        handlers = {"configuration": handle_configuration_post, "prompts": handle_settings_prompt_post, "audit-rules": handle_settings_audit_rules_post, "agent-runtime": handle_agent_runtime_config_post}
        handler = handlers.get(section)
        if handler is None:
            return JSONResponse({"ok": False, "code": "unsupported", "message": "此 Settings 区域不支持写入", "details": {}}, status_code=400)
        status, _headers, body_text = handler(body)
        if status >= 400:
            return JSONResponse({"ok": False, "code": "validation_error", "message": _legacy_settings_error_message(body_text), "details": {}}, status_code=status)
        return command_result(message="已保存")
