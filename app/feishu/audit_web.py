"""Local review routes for Feishu scopes and outbound deliveries."""
from __future__ import annotations

import html
from typing import Callable
from urllib.parse import parse_qs, quote, unquote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.audit_security import (
    audit_html_security_headers,
    csrf_form_input,
    require_local_mutation,
    require_local_request,
    script_nonce_attr,
)
from app.feishu.approval_preview import (
    action_approval_preview,
    action_list_preview,
    delivery_approval_preview,
    delivery_list_preview,
)
from app.store import FEISHU_RECALLABLE_DELIVERY_STATUSES


DELIVERY_STATUSES = (
    "ready_to_send",
    "sending",
    "retry",
    "sent",
    "send_unknown",
    "failed",
    "rejected",
)
ACTION_STATUSES = (
    "ready",
    "sending",
    "sent",
    "retry",
    "result_unknown",
    "failed",
    "rejected",
)
_NO_STORE_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Referrer-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
}


def _review_html_response(content: str) -> HTMLResponse:
    """Return sensitive operator HTML that cannot be cached or framed."""
    return HTMLResponse(
        content,
        headers={**_NO_STORE_HEADERS, **audit_html_security_headers()},
    )


def _review_json_response(content: object) -> JSONResponse:
    """Return local audit data without leaving a browser/proxy cache copy."""
    return JSONResponse(content, headers=_NO_STORE_HEADERS)


def _safe_next(value: str) -> str:
    decoded = unquote(value)
    if (
        decoded.startswith("/")
        and not decoded.startswith("//")
        and "\\" not in decoded
        and not any(ord(character) < 32 for character in decoded)
    ):
        return value
    return "/feishu/review"


async def _form_value(request: Request, key: str, default: str = "") -> str:
    values = parse_qs(
        (await request.body()).decode("utf-8"), keep_blank_values=True
    ).get(key, [])
    return values[0].strip() if values else default


def _review_form_text(
    value: str, *, field: str, maximum: int, required: bool = False
) -> str:
    """Validate a small operator-supplied identity before store mutation."""
    if required and not value:
        raise HTTPException(status_code=422, detail=f"{field} is required")
    if len(value) > maximum or any(ord(character) < 32 for character in value):
        raise HTTPException(status_code=422, detail=f"{field} is invalid")
    return value


def _review_message_ids(value: str) -> tuple[str, ...]:
    """Parse a bounded, ordered one-ID-per-line reconciliation field."""
    if len(value) > 52_000:
        raise HTTPException(status_code=422, detail="message_ids is invalid")
    if any(ord(character) < 32 and character not in "\r\n" for character in value):
        raise HTTPException(status_code=422, detail="message_ids is invalid")
    message_ids = tuple(line.strip() for line in value.splitlines() if line.strip())
    if len(message_ids) > 100:
        raise HTTPException(status_code=422, detail="message_ids is invalid")
    if any(
        len(message_id) > 512
        or any(ord(character) < 32 for character in message_id)
        for message_id in message_ids
    ):
        raise HTTPException(status_code=422, detail="message_ids is invalid")
    return message_ids


def _scope_item(scope) -> str:
    target_type = html.escape(scope.target_type)
    target_id = html.escape(scope.target_id)
    name = html.escape(scope.display_name or scope.target_id)
    state = html.escape(scope.binding_status)
    path_type = quote(scope.target_type, safe="")
    path_id = quote(scope.target_id, safe="")
    approve = ""
    if scope.binding_status != "verified" or not scope.enabled:
        approve = (
            f"<form method='post' action='/feishu/scopes/{path_type}/{path_id}/approve' "
            "style='display:inline-block'>"
            f"{csrf_form_input()}"
            "<label>审核人 <input name='approved_by' required maxlength='128' "
            "autocomplete='off'></label> "
            "<button class='fs-send' type='submit'>批准</button></form> "
        )
    disable = (
        f"<form method='post' action='/feishu/scopes/{path_type}/{path_id}/disable' "
        "style='display:inline-block'>"
        f"{csrf_form_input()}"
        "<label>操作人 <input name='approved_by' required maxlength='128' "
        "autocomplete='off'></label> "
        "<button type='submit'>禁用</button></form>"
    )
    return (
        "<div class='fs-item'>"
        f"<div class='fs-meta'>[{target_type}] {target_id} · <b>{state}</b></div>"
        f"<div class='fs-text'>{name}</div>"
        f"<div class='fs-actions'>{approve}{disable}</div>"
        "</div>"
    )


def _delivery_item(
    delivery,
    attempt,
    *,
    approvable: bool,
    rejectable: bool,
) -> str:
    preview = delivery_approval_preview(delivery)
    target = preview["target"]
    mentions = preview["mentions"]
    draft = html.escape(str(preview["text"]), quote=True)
    error = html.escape(delivery.error or "")
    trigger = ""
    reason = ""
    if attempt is not None:
        trigger = html.escape((attempt.trigger_text or "")[:1200])
        reason = html.escape((attempt.codex_reason or "")[:1200])
    controls: list[str] = []
    if approvable:
        controls.append(
            f"<form method='post' action='/feishu/deliveries/{delivery.id}/approve' "
            "style='display:inline-block'>"
            f"{csrf_form_input()}"
            "<input type='hidden' name='approval_hash' value='"
            f"{html.escape(delivery.approval_hash, quote=True)}'>"
            "<label>审核人 <input name='approved_by' required maxlength='128' "
            "autocomplete='off'></label> "
            "<button class='fs-send' type='submit'>发送</button></form>"
        )
    if rejectable:
        controls.append(
            f"<form method='post' action='/feishu/deliveries/{delivery.id}/reject' "
            "style='display:inline-block'>"
            f"{csrf_form_input()}"
            "<label>拒绝人 <input name='rejected_by' required maxlength='128' "
            "autocomplete='off'></label> "
            "<button type='submit'>拒绝</button></form>"
        )
    if delivery.status == "send_unknown":
        controls.append(
            f"<form method='post' action='/feishu/deliveries/{delivery.id}/reconcile'>"
            f"{csrf_form_input()}"
            "<label>核验结果 <select name='outcome' required>"
            "<option value=''>请选择</option>"
            "<option value='sent'>已核验连续分片前缀已发送</option>"
            "<option value='not_sent'>不确定的下一片已确认未发送</option>"
            "</select></label> "
            "<label>证据 <select name='evidence_kind' required>"
            "<option value='feishu_ui'>飞书 UI</option>"
            "<option value='message_lookup'>消息查询</option>"
            "<option value='admin_audit'>管理员审计</option></select></label> "
            "<label>核验人 <input name='verified_by' required maxlength='128' "
            "autocomplete='off'></label> "
            f"<label>已核验的连续 Message ID 前缀：持久前缀 + "
            f"恰好一个新核验 ID（每行一个；"
            f"完整计划 {delivery.expected_chunks} 个；结构性隔离不可补造或"
            "恢复计划，只能选择未发送后终态关闭）"
            "<textarea name='message_ids' rows='3' autocomplete='off'></textarea>"
            "</label> "
            "<label>Request Log ID <input name='request_log_id' maxlength='256' "
            "autocomplete='off'></label> "
            "<button class='fs-send' type='submit'>记录核验结果</button></form>"
        )
    if delivery.status == "failed" and delivery.error_code == "verified_not_sent":
        controls.append(
            f"<form method='post' action='/feishu/deliveries/{delivery.id}/requeue'>"
            f"{csrf_form_input()}"
            "<label>复核人 <input name='verified_by' required maxlength='128' "
            "autocomplete='off'></label> "
            "<label>证据 <select name='evidence_kind' required>"
            "<option value='feishu_ui'>飞书 UI</option>"
            "<option value='message_lookup'>消息查询</option>"
            "<option value='admin_audit'>管理员审计</option></select></label> "
            "<label>可执行时间（可选 ISO 8601）<input name='available_at' "
            "maxlength='64' autocomplete='off'></label> "
            "<button type='submit'>重新排队（撤销旧批准）</button></form>"
        )
    actions = " ".join(controls)
    details = ""
    if trigger:
        details += f"<div class='fs-context'><b>触发：</b>{trigger}</div>"
    if reason:
        details += f"<div class='fs-context'><b>决策：</b>{reason}</div>"
    mention_preview = ", ".join(
        f"{html.escape(str(mention['alias']))}="
        f"{html.escape(str(mention['fingerprint']))}"
        for mention in mentions
    ) or "none"
    chunk_preview = "".join(
        "<div class='fs-text'><b>分片 "
        f"{int(chunk['ordinal']) + 1}/{int(preview['expected_chunks'])}：</b>\n"
        f"{html.escape(str(chunk['text']), quote=True)}</div>"
        for chunk in preview["chunks"]
    )
    details += (
        "<div class='fs-context'><b>预览哈希：</b>"
        f"{html.escape(delivery.approval_hash)} · "
        f"format={html.escape(str(preview['reply_format']))} · "
        f"chunks={preview['expected_chunks']} · "
        f"generation={preview['review_generation']} · "
        f"plan={html.escape(str(preview['chunk_plan_sha256']))}</div>"
        "<div class='fs-context'><b>目标来源：</b>"
        f"{html.escape(str(target['provenance']))} · "
        f"conversation={html.escape(str(target['conversation_fingerprint']))} · "
        f"message={html.escape(str(target['message_fingerprint']))}</div>"
        "<div class='fs-context'><b>线程内回复：</b>"
        f"{str(bool(preview['reply_in_thread'])).lower()}</div>"
        "<div class='fs-context'><b>提及：</b>"
        f"{mention_preview}</div>"
    )
    if error:
        details += f"<div class='fs-error'>{error}</div>"
    if delivery.approved_at:
        details += (
            "<div class='fs-context'><b>批准：</b>"
            f"{html.escape(delivery.approved_by)} · "
            f"{html.escape(delivery.approved_at)}</div>"
        )
    return (
        "<div class='fs-item'>"
        f"<div class='fs-meta'>#{delivery.id} · "
        f"<b>{html.escape(delivery.status)}</b> · attempts={delivery.attempts}</div>"
        f"{details}<div class='fs-text'><b>完整待发送文本：</b>\n{draft}</div>"
        f"{chunk_preview}"
        f"<div class='fs-actions'>{actions}</div>"
        "</div>"
    )


def _action_summary(action) -> str:
    """Return a compact effect label; full immutable details live in preview."""
    effect = action_approval_preview(action)["effect"]
    if action.kind == "add_reaction":
        return f"emoji={effect['emoji_type']}"
    if action.kind == "handoff_notify":
        text_summary = action_list_preview(action)["effect"]["text_summary"]
        return f"handoff text={text_summary}"
    return "bot-owned message recall"


def _action_preview_html(action) -> str:
    preview = action_approval_preview(action)
    target = preview["target"]
    effect = preview["effect"]
    details = (
        "<div class='fs-context'><b>目标来源：</b>"
        f"{html.escape(str(target['provenance']))} · "
        f"target={html.escape(str(target['fingerprint']))}</div>"
    )
    if action.kind == "handoff_notify":
        details += (
            "<div class='fs-text'><b>完整交接通知：</b>\n"
            f"{html.escape(str(effect['text']), quote=True)}</div>"
        )
    elif action.kind == "add_reaction":
        details += (
            "<div class='fs-context'><b>Reaction：</b>"
            f"{html.escape(str(effect['emoji_type']), quote=True)}</div>"
        )
    else:
        details += (
            "<div class='fs-context'><b>动作：</b>撤回该机器人自有消息</div>"
        )
    return details


def _receipt_json(receipt) -> dict[str, object]:
    return {
        "id": receipt.id,
        "delivery_id": receipt.delivery_id,
        "ordinal": receipt.ordinal,
        "message_id": receipt.message_id,
        "status": receipt.status,
        "recall_action_id": receipt.recall_action_id,
        "created_at": receipt.created_at,
        "updated_at": receipt.updated_at,
    }


def _action_json(action, *, include_preview: bool = False) -> dict[str, object]:
    """Return only review metadata; never serialize the complete action model."""
    item = {
        "id": action.id,
        "reply_task_id": action.reply_task_id,
        "attempt_id": action.attempt_id,
        "kind": action.kind,
        "risk": action.risk,
        "status": action.status,
        "target_type": (
            "trusted_handoff_recipient"
            if action.kind == "handoff_notify"
            else "message"
        ),
        "summary": _action_summary(action),
        "preview": action_list_preview(action),
        "approved_at": action.approved_at,
        "approved_by": action.approved_by,
        "attempts": action.attempts,
        "error_code": action.error_code,
        "created_at": action.created_at,
        "updated_at": action.updated_at,
    }
    if include_preview:
        item["approval_hash"] = action.approval_hash
        item["approval_preview"] = action_approval_preview(action)
    return item


def _receipt_item(receipt, delivery) -> str:
    recall = ""
    if (
        receipt.status == "active"
        and delivery is not None
        and delivery.app_id == receipt.app_id
        and delivery.status in FEISHU_RECALLABLE_DELIVERY_STATUSES
    ):
        recall = (
            f"<form method='post' action='/feishu/receipts/{receipt.id}/recall' "
            "style='display:inline'>"
            f"{csrf_form_input()}"
            "<label>审核人 <input name='approved_by' required "
            "autocomplete='off'></label> "
            "<button class='fs-danger' type='submit'>撤回</button></form>"
        )
    return (
        "<div class='fs-item'>"
        f"<div class='fs-meta'>receipt #{receipt.id} · delivery "
        f"#{receipt.delivery_id} · chunk {receipt.ordinal} · "
        f"<b>{html.escape(receipt.status)}</b></div>"
        f"<div class='fs-context'>message {html.escape(receipt.message_id)}</div>"
        f"<div class='fs-actions'>{recall}</div>"
        "</div>"
    )


def _action_item(action) -> str:
    controls: list[str] = []
    if action.status in {"ready", "retry"}:
        if not action.approved_at:
            controls.append(
                f"<form method='post' action='/feishu/actions/{action.id}/approve' "
                "style='display:inline'>"
                f"{csrf_form_input()}"
                f"<input type='hidden' name='approval_hash' value='"
                f"{html.escape(action.approval_hash, quote=True)}'>"
                "<label>审核人 <input name='approved_by' required "
                "autocomplete='off'></label> "
                "<button class='fs-send' type='submit'>批准动作</button></form>"
            )
        # Rejection is a local safety operation and remains available after
        # approval or after the corresponding outbound feature is disabled.
        controls.append(
            f"<form method='post' action='/feishu/actions/{action.id}/reject' "
            "style='display:inline'>"
            f"{csrf_form_input()}"
            "<label>拒绝人 <input name='rejected_by' required "
            "autocomplete='off'></label> "
            "<button type='submit'>拒绝动作</button></form>"
        )
    elif action.status == "result_unknown":
        remote_field = ""
        if action.kind == "add_reaction":
            remote_field = (
                "<label>Reaction ID <input name='remote_id' maxlength='512' "
                "autocomplete='off'></label> "
            )
        elif action.kind == "handoff_notify":
            remote_field = (
                "<label>通知 Message ID <input name='remote_id' maxlength='512' "
                "autocomplete='off'></label> "
            )
        controls.append(
            f"<form method='post' action='/feishu/actions/{action.id}/reconcile'>"
            f"{csrf_form_input()}"
            "<label>核验结果 <select name='outcome' required>"
            "<option value='applied'>已发生</option>"
            "<option value='not_applied'>未发生</option></select></label> "
            "<label>证据 <select name='evidence_kind' required>"
            "<option value='feishu_ui'>飞书 UI</option>"
            "<option value='message_lookup'>消息查询</option>"
            "<option value='admin_audit'>管理员审计</option></select></label> "
            "<label>核验人 <input name='verified_by' maxlength='128' required "
            "autocomplete='off'></label> "
            f"{remote_field}"
            "<label>Request Log ID <input name='request_log_id' maxlength='256' "
            "autocomplete='off'></label> "
            "<button class='fs-send' type='submit'>记录核验结果</button></form>"
        )
    elif action.status == "failed" and action.error_code == "verified_not_applied":
        controls.append(
            f"<form method='post' action='/feishu/actions/{action.id}/requeue'>"
            f"{csrf_form_input()}"
            "<label>复核人 <input name='verified_by' maxlength='128' required "
            "autocomplete='off'></label> "
            "<label>证据 <select name='evidence_kind' required>"
            "<option value='feishu_ui'>飞书 UI</option>"
            "<option value='message_lookup'>消息查询</option>"
            "<option value='admin_audit'>管理员审计</option></select></label> "
            "<label>可执行时间（可选 ISO 8601）<input name='available_at' "
            "maxlength='64' autocomplete='off'></label> "
            "<button type='submit'>重新排队（撤销旧批准）</button></form>"
        )
    approval = ""
    if action.approved_at:
        approval = (
            "<div class='fs-context'><b>批准：</b>"
            f"{html.escape(action.approved_by)} · "
            f"{html.escape(action.approved_at)}</div>"
        )
    return (
        "<div class='fs-item'>"
        f"<div class='fs-meta'>action #{action.id} · "
        f"{html.escape(action.kind)} · risk {html.escape(action.risk)} · "
        f"<b>{html.escape(action.status)}</b></div>"
        f"{_action_preview_html(action)}"
        f"{approval}<div class='fs-actions'>{' '.join(controls)}</div>"
        "</div>"
    )


def _require_action_kind_gate(kind: str, *, require_live: bool) -> None:
    from app import config

    enabled = {
        "add_reaction": config.feishu_reaction_enabled,
        "recall_message": config.feishu_recall_enabled,
        "handoff_notify": config.feishu_handoff_enabled,
    }.get(kind)
    if enabled is None or not enabled():
        raise HTTPException(
            status_code=409,
            detail="Feishu action kind gate is closed",
        )
    if require_live and not config.feishu_live_send_allowed():
        raise HTTPException(status_code=409, detail="Feishu outbound gates are closed")


def register_feishu_review_routes(
    app: FastAPI,
    *,
    store_factory: Callable[[], object],
    health_factory: Callable[[], object | None] | None = None,
) -> None:
    """Register review routes without importing or constructing the SDK.

    Approval is durable local state only.  The already-running single-channel
    runtime claims approved rows; this web process never constructs an SDK
    client or opens another WebSocket.
    """

    @app.get("/feishu/review", response_class=HTMLResponse)
    def feishu_review(request: Request) -> HTMLResponse:
        from app import config
        from app.feishu.setup import dependency_status

        require_local_request(request)
        store = store_factory()
        app_id = config.feishu_app_id()
        scopes = store.list_feishu_reply_scopes(app_id=app_id) if app_id else []
        deliveries = store.list_feishu_deliveries(
            statuses=DELIVERY_STATUSES, app_id=app_id, limit=100
        ) if app_id else []
        receipts = (
            store.list_feishu_delivery_receipts(app_id=app_id)
            if app_id
            else []
        )
        message_actions = (
            store.list_feishu_message_actions(
                app_id=app_id,
                statuses=ACTION_STATUSES,
                limit=100,
            )
            if app_id
            else []
        )
        deliveries_by_id = {delivery.id: delivery for delivery in deliveries}
        for receipt in receipts:
            if receipt.delivery_id not in deliveries_by_id:
                owner = store.get_feishu_delivery(receipt.delivery_id)
                if owner is not None:
                    deliveries_by_id[owner.id] = owner
        attempts = {
            delivery.attempt_id: store.get_reply_attempt(delivery.attempt_id)
            for delivery in deliveries
            if delivery.attempt_id
        }
        health = health_factory() if health_factory is not None else None
        health_status = getattr(health, "status", "not_running")
        connected_at = getattr(health, "connected_at", "")
        reconnect_at = getattr(health, "last_reconnect_at", "")
        dependencies = dependency_status()
        parts = [
            f"<script {script_nonce_attr()}>(()=>{{let formDirty=false;"
            "document.addEventListener('input',(event)=>{if(event.target&&event.target.form){formDirty=true;}});"
            "document.addEventListener('change',(event)=>{if(event.target&&event.target.form){formDirty=true;}});"
            "window.setInterval(()=>{const active=document.activeElement;"
            "const editing=active&&['INPUT','SELECT','TEXTAREA'].includes(active.tagName);"
            "if(!formDirty&&!editing){window.location.reload();}},5000);})();</script>",
            "<style>body{font-family:-apple-system,system-ui,sans-serif;max-width:900px;margin:24px auto;padding:0 16px;color:#1a1a1a}"
            ".fs-item{border:1px solid #e2e5e9;border-radius:10px;padding:12px 14px;margin:10px 0}"
            ".fs-meta{color:#667085;font-size:12px;margin-bottom:6px}.fs-text{white-space:pre-wrap;line-height:1.5}"
            ".fs-context{font-size:13px;color:#475467;margin:5px 0}.fs-error{font-size:12px;color:#b42318;margin:5px 0}"
            ".fs-actions{margin-top:8px}button{padding:6px 16px;border-radius:8px;border:1px solid #ccd;background:#f6f7f9;cursor:pointer}"
            "button.fs-send{background:#3370ff;color:#fff;border-color:#3370ff;font-weight:700}"
            "button.fs-danger{background:#b42318;color:#fff;border-color:#b42318;font-weight:700}"
            ".fs-status{background:#f7f8fa;border-radius:10px;padding:12px 14px}</style>",
            "<p><a href='/'>← History</a></p><h2>飞书通道审核</h2>",
            "<div class='fs-status'>",
            f"连接：{html.escape(str(health_status))} · 最近连接：{html.escape(str(connected_at or '-'))} "
            f"· 最近重连：{html.escape(str(reconnect_at or '-'))}<br>",
            f"App ID：{'configured' if config.feishu_app_id() else 'missing'} · "
            f"App Secret：{'configured' if config.feishu_app_secret() else 'missing'} · "
            f"SDK：{'configured' if dependencies.channel_version_ok and dependencies.oapi_version_ok else 'missing'}",
            "</div>",
            "<h3>回复目标</h3>",
        ]
        if scopes:
            parts.extend(_scope_item(scope) for scope in scopes)
        else:
            parts.append("<p>尚未发现目标。</p>")
        parts.append("<h3>投递</h3>")
        if deliveries:
            for delivery in reversed(deliveries):
                parts.append(
                    _delivery_item(
                        delivery,
                        attempts.get(delivery.attempt_id),
                        approvable=(
                            delivery.status in {"ready_to_send", "retry"}
                            and not delivery.approved_at
                        ),
                        rejectable=delivery.status in {"ready_to_send", "retry"},
                    )
                )
        else:
            parts.append("<p>没有投递记录。</p>")
        parts.append("<h3>消息收据</h3>")
        if receipts:
            for receipt in reversed(receipts):
                parts.append(
                    _receipt_item(
                        receipt,
                        deliveries_by_id.get(receipt.delivery_id),
                    )
                )
        else:
            parts.append("<p>没有消息收据。</p>")
        parts.append("<h3>消息动作</h3>")
        if message_actions:
            parts.extend(
                _action_item(action) for action in reversed(message_actions)
            )
        else:
            parts.append("<p>没有消息动作。</p>")
        return _review_html_response("".join(parts))

    @app.get("/feishu/deliveries")
    def feishu_deliveries(
        request: Request, include_preview: bool = False
    ) -> JSONResponse:
        from app import config

        require_local_request(request)
        app_id = str(config.feishu_app_id() or "").strip()
        rows = (
            store_factory().list_feishu_deliveries(
                statuses=DELIVERY_STATUSES,
                app_id=app_id,
                limit=100,
            )
            if app_id
            else []
        )
        return _review_json_response(
            {
                "items": [
                    {
                        "id": row.id,
                        "attempt_id": row.attempt_id,
                        "status": row.status,
                        "summary": f"[redacted:{len(row.reply_text)} chars]",
                        "preview": delivery_list_preview(row),
                        "reply_format": row.reply_format,
                        "expected_chunks": row.expected_chunks,
                        "attempts": row.attempts,
                        "approved_at": row.approved_at,
                        "approved_by": row.approved_by,
                        "error_code": row.error_code,
                        "error": row.error,
                        "created_at": row.created_at,
                        "updated_at": row.updated_at,
                        **(
                            {
                                "approval_hash": row.approval_hash,
                                "approval_preview": delivery_approval_preview(row),
                            }
                            if include_preview
                            else {}
                        ),
                    }
                    for row in rows
                ]
            }
        )

    @app.get("/feishu/receipts")
    def feishu_receipts(request: Request) -> JSONResponse:
        from app import config

        require_local_request(request)
        app_id = str(config.feishu_app_id() or "").strip()
        rows = (
            store_factory().list_feishu_delivery_receipts(app_id=app_id)
            if app_id
            else []
        )
        return _review_json_response(
            {"items": [_receipt_json(row) for row in rows]}
        )

    @app.get("/feishu/actions")
    def feishu_actions(
        request: Request, include_preview: bool = False
    ) -> JSONResponse:
        from app import config

        require_local_request(request)
        app_id = str(config.feishu_app_id() or "").strip()
        rows = (
            store_factory().list_feishu_message_actions(
                app_id=app_id,
                statuses=ACTION_STATUSES,
                limit=100,
            )
            if app_id
            else []
        )
        return _review_json_response(
            {
                "items": [
                    _action_json(row, include_preview=include_preview)
                    for row in rows
                ]
            }
        )

    @app.post("/feishu/receipts/{receipt_id}/recall")
    async def create_and_approve_recall(
        request: Request,
        receipt_id: int,
        next: str = "/feishu/review",
    ) -> RedirectResponse:
        """Create one receipt-bound R4 action and record local approval only."""
        from app import config
        from app.feishu.actions import build_message_action

        await require_local_mutation(request)
        _require_action_kind_gate("recall_message", require_live=True)
        app_id = str(config.feishu_app_id() or "").strip()
        if not app_id:
            raise HTTPException(status_code=409, detail="Feishu App ID is missing")
        approved_by = _review_form_text(
            await _form_value(request, "approved_by"),
            field="approved_by",
            maximum=128,
            required=True,
        )
        store = store_factory()
        try:
            receipt, delivery = (
                store.validate_feishu_delivery_receipt_for_recall(
                    receipt_id, app_id=app_id
                )
            )
        except (PermissionError, ValueError) as exc:
            raise HTTPException(
                status_code=409,
                detail=str(exc),
            ) from exc
        action = build_message_action(
            reply_task_id=delivery.reply_task_id,
            attempt_id=delivery.attempt_id,
            app_id=app_id,
            chat_id=delivery.chat_id,
            action_key=f"manual_recall:receipt:{receipt.id}",
            kind="recall_message",
            target_message_id=receipt.message_id,
        )
        saved_id = 0
        try:
            saved = store.create_feishu_message_action(
                action,
                actor=approved_by,
            )
            saved_id = saved.id
            if saved.approved_at:
                if saved.approval_hash != action.approval_hash:
                    raise ValueError("Feishu recall action approval hash changed")
                return RedirectResponse(_safe_next(next), status_code=303)
            store.approve_feishu_message_action(
                saved.id,
                app_id=app_id,
                approved_by=approved_by,
                expected_approval_hash=action.approval_hash,
            )
        except (PermissionError, ValueError) as exc:
            # A concurrent identical click may have completed between create
            # and approve.  Treat only that exact, already-approved identity
            # as idempotent; every other replay fails closed.
            current = (
                store.get_feishu_message_action(saved_id)
                if saved_id
                else None
            )
            if not (
                current is not None
                and current.app_id == app_id
                and current.kind == "recall_message"
                and current.target_message_id == receipt.message_id
                and current.approval_hash == action.approval_hash
                and current.approved_at
            ):
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        return RedirectResponse(_safe_next(next), status_code=303)

    def _configured_action(store, action_id: int, app_id: str):
        action = store.get_feishu_message_action(action_id)
        if action is None:
            raise HTTPException(
                status_code=404,
                detail="Feishu message action not found",
            )
        if action.app_id != app_id:
            raise HTTPException(
                status_code=409,
                detail="Feishu message action App ID does not match",
            )
        return action

    def _configured_delivery(store, delivery_id: int, app_id: str):
        delivery = store.get_feishu_delivery(delivery_id)
        if delivery is None:
            raise HTTPException(status_code=404, detail="Feishu delivery not found")
        if delivery.app_id != app_id:
            raise HTTPException(
                status_code=409,
                detail="Feishu delivery App ID does not match",
            )
        return delivery

    @app.post("/feishu/actions/{action_id}/approve")
    async def approve_feishu_action(
        request: Request,
        action_id: int,
        next: str = "/feishu/review",
    ) -> RedirectResponse:
        from app import config

        await require_local_mutation(request)
        app_id = str(config.feishu_app_id() or "").strip()
        if not app_id:
            raise HTTPException(status_code=409, detail="Feishu App ID is missing")
        store = store_factory()
        action = _configured_action(store, action_id, app_id)
        _require_action_kind_gate(action.kind, require_live=True)
        approved_by = _review_form_text(
            await _form_value(request, "approved_by"),
            field="approved_by",
            maximum=128,
            required=True,
        )
        approval_hash = await _form_value(request, "approval_hash")
        if not approval_hash:
            raise HTTPException(status_code=422, detail="approval_hash is required")
        try:
            store.approve_feishu_message_action(
                action_id,
                app_id=app_id,
                approved_by=approved_by,
                expected_approval_hash=approval_hash,
            )
        except (PermissionError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return RedirectResponse(_safe_next(next), status_code=303)

    @app.post("/feishu/actions/{action_id}/reject")
    async def reject_feishu_action(
        request: Request,
        action_id: int,
        next: str = "/feishu/review",
    ) -> RedirectResponse:
        from app import config

        await require_local_mutation(request)
        app_id = str(config.feishu_app_id() or "").strip()
        if not app_id:
            raise HTTPException(status_code=409, detail="Feishu App ID is missing")
        store = store_factory()
        _configured_action(store, action_id, app_id)
        rejected_by = await _form_value(request, "rejected_by")
        _review_form_text(
            rejected_by, field="rejected_by", maximum=128, required=True
        )
        try:
            store.reject_feishu_message_action(
                action_id,
                app_id=app_id,
                rejected_by=rejected_by,
            )
        except (PermissionError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return RedirectResponse(_safe_next(next), status_code=303)

    @app.post("/feishu/actions/{action_id}/reconcile")
    async def reconcile_feishu_action(
        request: Request,
        action_id: int,
        next: str = "/feishu/review",
    ) -> RedirectResponse:
        """Record independent final-state evidence without opening a client."""
        from app import config

        await require_local_mutation(request)
        app_id = str(config.feishu_app_id() or "").strip()
        if not app_id:
            raise HTTPException(status_code=409, detail="Feishu App ID is missing")
        store = store_factory()
        action = _configured_action(store, action_id, app_id)
        outcome = await _form_value(request, "outcome")
        if outcome not in {"applied", "not_applied"}:
            raise HTTPException(status_code=422, detail="outcome is invalid")
        evidence_kind = await _form_value(request, "evidence_kind")
        if evidence_kind not in {"feishu_ui", "message_lookup", "admin_audit"}:
            raise HTTPException(status_code=422, detail="evidence_kind is invalid")
        verified_by = _review_form_text(
            await _form_value(request, "verified_by"),
            field="verified_by",
            maximum=128,
            required=True,
        )
        remote_id = _review_form_text(
            await _form_value(request, "remote_id"),
            field="remote_id",
            maximum=512,
        )
        request_log_id = _review_form_text(
            await _form_value(request, "request_log_id"),
            field="request_log_id",
            maximum=256,
        )
        if outcome == "not_applied" and remote_id:
            raise HTTPException(
                status_code=422,
                detail="not_applied must not include a remote identifier",
            )
        if action.kind == "recall_message" and remote_id:
            raise HTTPException(
                status_code=422,
                detail="recall reconciliation must not include a remote identifier",
            )
        if (
            outcome == "applied"
            and action.kind in {"add_reaction", "handoff_notify"}
            and not remote_id
        ):
            identifier = (
                "reaction ID"
                if action.kind == "add_reaction"
                else "message ID"
            )
            raise HTTPException(
                status_code=422,
                detail=f"applied {action.kind} requires {identifier}",
            )
        try:
            store.reconcile_feishu_message_action_unknown(
                action_id,
                app_id=app_id,
                outcome=outcome,
                verified_by=verified_by,
                evidence_kind=evidence_kind,
                remote_id=remote_id,
                request_log_id=request_log_id,
            )
        except (PermissionError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return RedirectResponse(_safe_next(next), status_code=303)

    @app.post("/feishu/actions/{action_id}/requeue")
    async def requeue_feishu_action(
        request: Request,
        action_id: int,
        next: str = "/feishu/review",
    ) -> RedirectResponse:
        """Create a fresh retry opportunity after verified non-application."""
        from app import config

        await require_local_mutation(request)
        app_id = str(config.feishu_app_id() or "").strip()
        if not app_id:
            raise HTTPException(status_code=409, detail="Feishu App ID is missing")
        store = store_factory()
        _configured_action(store, action_id, app_id)
        evidence_kind = await _form_value(request, "evidence_kind")
        if evidence_kind not in {"feishu_ui", "message_lookup", "admin_audit"}:
            raise HTTPException(status_code=422, detail="evidence_kind is invalid")
        verified_by = _review_form_text(
            await _form_value(request, "verified_by"),
            field="verified_by",
            maximum=128,
            required=True,
        )
        available_at = _review_form_text(
            await _form_value(request, "available_at"),
            field="available_at",
            maximum=64,
        )
        try:
            store.requeue_feishu_message_action_after_verification(
                action_id,
                app_id=app_id,
                verified_by=verified_by,
                evidence_kind=evidence_kind,
                available_at=available_at,
            )
        except (PermissionError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return RedirectResponse(_safe_next(next), status_code=303)

    async def _review_scope(
        request: Request,
        target_type: str,
        target_id: str,
        *,
        approved: bool,
    ) -> RedirectResponse:
        from app import config

        await require_local_mutation(request)
        if target_type not in {"direct_sender", "group"}:
            raise HTTPException(status_code=422, detail="invalid Feishu target type")
        app_id = config.feishu_app_id()
        if not app_id:
            raise HTTPException(status_code=409, detail="Feishu App ID is missing")
        approved_by = _review_form_text(
            await _form_value(request, "approved_by"),
            field="approved_by",
            maximum=128,
            required=True,
        )
        try:
            store_factory().review_feishu_reply_scope(
                app_id,
                target_type,
                target_id,
                approved=approved,
                approved_by=approved_by,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return RedirectResponse("/feishu/review", status_code=303)

    @app.post("/feishu/scopes/{target_type}/{target_id}/approve")
    async def approve_scope(
        request: Request, target_type: str, target_id: str
    ) -> RedirectResponse:
        return await _review_scope(
            request, target_type, target_id, approved=True
        )

    @app.post("/feishu/scopes/{target_type}/{target_id}/disable")
    async def disable_scope(
        request: Request, target_type: str, target_id: str
    ) -> RedirectResponse:
        return await _review_scope(
            request, target_type, target_id, approved=False
        )

    @app.post("/feishu/deliveries/{delivery_id}/approve")
    async def approve_feishu_delivery(
        request: Request, delivery_id: int, next: str = "/feishu/review"
    ) -> RedirectResponse:
        from app import config

        await require_local_mutation(request)
        if not config.feishu_live_send_allowed():
            raise HTTPException(status_code=409, detail="Feishu outbound gates are closed")
        app_id = str(config.feishu_app_id() or "").strip()
        if not app_id:
            raise HTTPException(status_code=409, detail="Feishu App ID is missing")
        approved_by = _review_form_text(
            await _form_value(request, "approved_by"),
            field="approved_by",
            maximum=128,
            required=True,
        )
        approval_hash = await _form_value(request, "approval_hash")
        if not approval_hash:
            raise HTTPException(status_code=422, detail="approval_hash is required")
        try:
            store_factory().approve_feishu_delivery(
                delivery_id,
                app_id=app_id,
                approved_by=approved_by,
                expected_approval_hash=approval_hash,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return RedirectResponse(_safe_next(next), status_code=303)

    @app.post("/feishu/deliveries/{delivery_id}/reject")
    async def reject_feishu_delivery(
        request: Request, delivery_id: int, next: str = "/feishu/review"
    ) -> RedirectResponse:
        from app import config

        await require_local_mutation(request)
        app_id = str(config.feishu_app_id() or "").strip()
        if not app_id:
            raise HTTPException(status_code=409, detail="Feishu App ID is missing")
        rejected_by = _review_form_text(
            await _form_value(request, "rejected_by"),
            field="rejected_by",
            maximum=128,
            required=True,
        )
        try:
            store_factory().reject_feishu_delivery(
                delivery_id,
                app_id=app_id,
                rejected_by=rejected_by,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return RedirectResponse(_safe_next(next), status_code=303)

    @app.post("/feishu/deliveries/{delivery_id}/reconcile")
    async def reconcile_feishu_delivery(
        request: Request,
        delivery_id: int,
        next: str = "/feishu/review",
    ) -> RedirectResponse:
        """Record independent send evidence without constructing an SDK client."""
        from app import config

        await require_local_mutation(request)
        app_id = str(config.feishu_app_id() or "").strip()
        if not app_id:
            raise HTTPException(status_code=409, detail="Feishu App ID is missing")
        store = store_factory()
        delivery = _configured_delivery(store, delivery_id, app_id)
        outcome = await _form_value(request, "outcome")
        if outcome not in {"sent", "not_sent"}:
            raise HTTPException(status_code=422, detail="outcome is invalid")
        evidence_kind = await _form_value(request, "evidence_kind")
        if evidence_kind not in {"feishu_ui", "message_lookup", "admin_audit"}:
            raise HTTPException(status_code=422, detail="evidence_kind is invalid")
        verified_by = _review_form_text(
            await _form_value(request, "verified_by"),
            field="verified_by",
            maximum=128,
            required=True,
        )
        request_log_id = _review_form_text(
            await _form_value(request, "request_log_id"),
            field="request_log_id",
            maximum=256,
        )
        message_ids = _review_message_ids(
            await _form_value(request, "message_ids")
        )
        try:
            store.reconcile_feishu_delivery_unknown(
                delivery_id,
                app_id=app_id,
                outcome=outcome,
                verified_by=verified_by,
                evidence_kind=evidence_kind,
                message_ids=message_ids,
                expected_chunks=(delivery.expected_chunks if outcome == "sent" else 0),
                request_log_id=request_log_id,
            )
        except (PermissionError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return RedirectResponse(_safe_next(next), status_code=303)

    @app.post("/feishu/deliveries/{delivery_id}/requeue")
    async def requeue_feishu_delivery(
        request: Request,
        delivery_id: int,
        next: str = "/feishu/review",
    ) -> RedirectResponse:
        """Requeue only a verified non-send and revoke any old approval."""
        from app import config

        await require_local_mutation(request)
        app_id = str(config.feishu_app_id() or "").strip()
        if not app_id:
            raise HTTPException(status_code=409, detail="Feishu App ID is missing")
        store = store_factory()
        _configured_delivery(store, delivery_id, app_id)
        evidence_kind = await _form_value(request, "evidence_kind")
        if evidence_kind not in {"feishu_ui", "message_lookup", "admin_audit"}:
            raise HTTPException(status_code=422, detail="evidence_kind is invalid")
        verified_by = _review_form_text(
            await _form_value(request, "verified_by"),
            field="verified_by",
            maximum=128,
            required=True,
        )
        available_at = _review_form_text(
            await _form_value(request, "available_at"),
            field="available_at",
            maximum=64,
        )
        try:
            store.requeue_feishu_delivery_after_verification(
                delivery_id,
                app_id=app_id,
                verified_by=verified_by,
                evidence_kind=evidence_kind,
                available_at=available_at,
            )
        except (PermissionError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return RedirectResponse(_safe_next(next), status_code=303)
