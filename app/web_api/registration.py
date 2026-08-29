"""Registration for the React console's domain APIs.

The adapters in this module intentionally return resource-shaped JSON.  The
legacy HTML handlers remain available for the external bridge and for the
old form routes during the migration, but React never consumes their HTML.
"""

from collections.abc import Callable
import json
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.web_api.attention import AttentionListEnvelope, group_attention_rows
from app.web_api.common import ApiItemEnvelope, ApiListMeta, ApiMeta, json_safe, normalize_display_value, snapshot_at
from app.web_api.tasks import (
    ConsoleTaskDetail,
    ConsoleTaskDetailEnvelope,
    ConsoleTaskListEnvelope,
    task_detail,
    task_list_response,
)
from app.web_api.settings import info_payload


def register_console_routes(
    app: FastAPI,
    store_factory: Callable[[], Any],
    *,
    status_payload_factory: Callable[[], Any],
    attention_rows_factory: Callable[[], Any],
    task_row_builder: Callable[[Any, list[Any]], Any] | None = None,
) -> None:
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

    def command_result(*, item: Any = None, message: str = "已完成", ok: bool = True):
        return {"ok": ok, "item": json_safe(item), "message": message,
                "meta": {"updated_at": snapshot_at()}}

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
    ):
        return task_list_response(
            store_factory(),
            page=page,
            page_size=page_size,
            query=q,
            category=category,
            task_state=task_state,
            row_builder=task_row_builder,
        )

    @app.get("/api/console/tasks/{project_id}/details", response_model=ConsoleTaskDetailEnvelope)
    def console_task_details_alias(project_id: int):
        return console_task_detail(project_id)

    @app.get("/api/console/tasks/{project_id}", response_model=ConsoleTaskDetailEnvelope)
    def console_task_detail(project_id: int):
        item = task_detail(store_factory(), project_id)
        if item is None:
            return JSONResponse(
                {"ok": False, "code": "not_found", "message": "Task project not found", "details": {"project_id": project_id}},
                status_code=404,
            )
        return ConsoleTaskDetailEnvelope(
            item=ConsoleTaskDetail.model_validate(item),
            meta=ApiMeta(snapshot_at=snapshot_at()),
        )

    @app.get("/api/console/status", response_model=None)
    def console_status():
        item = json_safe(status_payload_factory())
        return ApiItemEnvelope(item=item, meta=ApiMeta(snapshot_at=snapshot_at()))

    @app.get("/api/console/attention", response_model=AttentionListEnvelope)
    def console_attention():
        groups = group_attention_rows(attention_rows_factory())
        return AttentionListEnvelope(
            items=groups,
            meta=ApiListMeta(snapshot_at=snapshot_at(), total=len(groups)),
        )

    @app.get("/api/console/history")
    def console_history(
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        q: str = "",
        status: str = "",
        object_type: str = "",
    ):
        store = store_factory()
        statuses = (status,) if status.strip() else None
        object_types = (object_type,) if object_type.strip() else None
        total = store.count_history_items(
            send_statuses=statuses, query_text=q, object_types=object_types
        )
        rows = store.list_history_items(
            limit=page_size, offset=(page - 1) * page_size,
            send_statuses=statuses, query_text=q, object_types=object_types,
        )
        items = []
        for row in rows:
            kind = str(row.kind)
            detail = (
                f"/meeting-attempts/{row.source_id}" if kind == "meeting"
                else f"/tasks/{row.project_id}" if kind == "task" and row.project_id
                else f"/attempts/{row.source_id}"
            )
            items.append({
                "id": str(row.source_id), "occurred_at": row.created_at,
                "title": normalize_display_value(row.source_title),
                "type": normalize_display_value(row.object_type),
                "status": normalize_display_value(row.status),
                "summary": normalize_display_value(row.output_text or row.input_text),
                "actor": normalize_display_value(row.source_actor),
                "detail_url": detail,
                "kind": kind, "input": normalize_display_value(row.input_text),
                "output": normalize_display_value(row.output_text),
                "action": normalize_display_value(row.action),
            })
        return list_envelope(items, page=page, page_size=page_size, total=total)

    @app.get("/api/console/history/{attempt_id}")
    def console_history_detail(attempt_id: int):
        attempt = store_factory().get_reply_attempt(attempt_id)
        if attempt is None:
            return JSONResponse({"ok": False, "code": "not_found", "message": "Attempt not found", "details": {}}, status_code=404)
        return item_envelope({
            "id": attempt.id, "title": attempt.conversation_title,
            "status": attempt.send_status, "type": attempt.action,
            "input": attempt.trigger_text, "decision": attempt.codex_reason,
            "output": attempt.final_reply_text or attempt.draft_reply_text,
            "reviewer_feedback": attempt.reviewer_feedback,
            "corrected_reply": attempt.corrected_reply_text,
            "created_at": attempt.created_at, "updated_at": attempt.updated_at,
            "runtime": {"agent_run_id": attempt.agent_run_id, "retry_count": attempt.retry_count,
                        "send_error": normalize_display_value(attempt.send_error)},
        })

    @app.post("/api/console/history/{attempt_id}/feedback")
    async def console_history_feedback(attempt_id: int, request: Request):
        payload = await json_object(request)
        if not store_factory().record_reply_feedback(
            attempt_id,
            feedback=str(payload.get("feedback") or payload.get("reviewer_feedback") or ""),
            corrected_reply_text=str(payload.get("corrected_reply") or ""),
        ):
            return JSONResponse({"ok": False, "code": "not_found", "message": "Attempt not found", "details": {}}, status_code=404)
        return command_result(message="反馈已保存")

    @app.post("/api/console/history/{attempt_id}/rerun")
    async def console_history_rerun(attempt_id: int, request: Request):
        from app.audit_web import handle_rerun_attempt_post
        status, _headers, body = handle_rerun_attempt_post(store_factory(), attempt_id, return_to="/history")
        if status >= 400:
            return JSONResponse({"ok": False, "code": "rerun_failed", "message": "无法重跑该 Attempt", "details": {"technical": normalize_display_value(body)}}, status_code=status)
        return command_result(message="重跑已提交")

    @app.get("/api/console/meeting-attempts/{run_id}")
    def console_meeting_detail(run_id: int):
        run = store_factory().get_agent_run(run_id)
        if run is None:
            return JSONResponse({"ok": False, "code": "not_found", "message": "Meeting attempt not found", "details": {}}, status_code=404)
        return item_envelope({"id": run.id, "status": run.status, "role": run.role.value,
                              "operation_id": run.operation_id, "result": json_safe(run.final_result_json),
                              "runtime": {"created_at": run.created_at, "started_at": run.started_at,
                                          "completed_at": run.completed_at}})

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
            row_status = "resolved" if row.resolved_at.strip() or row.reviewer_feedback.strip() or row.corrected_reply_text.strip() else "pending"
            haystack = " ".join((row.comment, row.conversation_title, row.trigger_sender, row.trigger_text, row_status)).casefold()
            if (status.strip() and row_status != status.strip()) or (needle and needle not in haystack):
                continue
            filtered.append({"id": row.key, "attempt_id": str(row.attempt_id) if row.attempt_id else "",
                             "status": row_status, "rating": row.rating_label or row.rating,
                             "comment": row.comment or "未填写评语", "context": " · ".join(x for x in (row.conversation_title, row.trigger_sender, row.trigger_text[:140]) if x),
                             "created_at": row.received_at or row.updated_at, "key": row.key})
        start = (page - 1) * page_size
        return list_envelope(filtered[start:start + page_size], page=page, page_size=page_size, total=len(filtered))

    @app.post("/api/console/feedback/{feedback_id}/resolve")
    def console_feedback_resolve(feedback_id: str):
        if not store_factory().resolve_feedback_event(feedback_id):
            return JSONResponse({"ok": False, "code": "not_found", "message": "Feedback not found", "details": {}}, status_code=404)
        return command_result(message="已标记为已处理")

    @app.post("/api/console/feedback/sync")
    def console_feedback_sync():
        from app.audit_web import handle_user_feedback_sync_post
        status, _headers, _body = handle_user_feedback_sync_post(store_factory())
        if status >= 400:
            return JSONResponse({"ok": False, "code": "sync_failed", "message": "同步反馈失败", "details": {}}, status_code=status)
        return command_result(message="已同步最新反馈")

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
                    read_audit_rules_template,
                    render_audit_rules,
                )
                template = read_audit_rules_template()
                fields = {"template": template}
                payload = {
                    "section": section,
                    "fields": fields,
                    "preview": {
                        "template": template,
                        "consumer": render_audit_rules(AgentRole.CONSUMER),
                        "audit": render_audit_rules(AgentRole.AUDIT),
                    },
                }
            else:
                env = app_config.read_env_file()
                fields = {key: env.get(key, "") for key in ("CEO_CODEX_MODEL", "CEO_CODEX_MODEL_REASONING_EFFORT", "CEO_AGENT_RUNTIME_ROUTES", "CEO_CODEX_API_BASE_URL", "CEO_CODEX_API_MODEL", "CEO_FRIDAY_RUNTIME_BASE_URL", "CEO_FRIDAY_RUNTIME_PROJECT_ID")}
            if payload is None:
                payload = {"section": section, "fields": fields}
            payload["secrets"] = ["CEO_CODEX_API_KEY", "CEO_FRIDAY_RUNTIME_TICKET", "CEO_FRIDAY_SESSION_TOKEN"] if section == "agent-runtime" else []
        return item_envelope(payload)

    @app.get("/api/console/tutorial")
    def console_tutorial():
        from app.audit_web import build_wizard_status
        return item_envelope(json_safe(build_wizard_status(store_factory())))

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
    async def console_tutorial_run(action_id: str, request: Request):
        from app.audit_web import _require_available_setup_action, _repo_root, run_setup_action
        store = store_factory()
        _require_available_setup_action(store, action_id, kind="run")
        event = run_setup_action(action_id, repo_root=_repo_root(), env=dict(__import__("os").environ))
        store.record_setup_wizard_event(step_id=event.step_id, action_id=event.action_id, status=event.status, summary=event.summary, evidence_json=json.dumps(event.evidence, ensure_ascii=False), stdout_excerpt=event.stdout_excerpt, stderr_excerpt=event.stderr_excerpt)
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
            sender = WechatSender(store, service.build_sender())
            result = service.approve_wechat_delivery(store, sender, delivery_id)
        except Exception as exc:
            return JSONResponse({"ok": False, "code": "delivery_failed", "message": "发送失败", "details": {"reason": normalize_display_value(exc)}}, status_code=409)
        return command_result(item=result, message="发送动作已提交")

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
        from app.wechat.service import build_setup_service
        try:
            result = build_setup_service(store_factory()).save_reply_scope(payload)
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
            encoded = {
                "codex_model": str(fields.get("codex_model") or fields.get("CEO_CODEX_MODEL") or ""),
                "codex_reasoning_effort": str(fields.get("codex_reasoning_effort") or fields.get("CEO_CODEX_MODEL_REASONING_EFFORT") or ""),
                "codex_api_enabled": "1" if str(fields.get("codex_api_enabled") or "CEO_CODEX_API_KEY" in fields).lower() in {"1", "true", "yes", "on"} else "0",
                "codex_api_model": str(fields.get("codex_api_model") or fields.get("CEO_CODEX_API_MODEL") or ""),
                "codex_api_base_url": str(fields.get("codex_api_base_url") or fields.get("CEO_CODEX_API_BASE_URL") or ""),
                "codex_api_token": str(fields.get("codex_api_token") or ""),
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
            return JSONResponse({"ok": False, "code": "validation_error", "message": "保存失败，请检查字段", "details": {"technical": normalize_display_value(body_text)}}, status_code=status)
        return command_result(message="已保存")
