"""Local review routes for Feishu scopes and outbound deliveries."""
from __future__ import annotations

import html
import ipaddress
import secrets
from hmac import compare_digest
from typing import Callable
from urllib.parse import parse_qs, quote, urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse


DELIVERY_STATUSES = (
    "ready_to_send",
    "sending",
    "retry",
    "sent",
    "send_unknown",
    "failed",
    "rejected",
)
_CSRF_TOKEN = secrets.token_urlsafe(32)


def csrf_form_input() -> str:
    """Return the process-local token field used by every Feishu mutation."""
    return (
        "<input type='hidden' name='csrf_token' value='"
        f"{html.escape(_CSRF_TOKEN, quote=True)}'>"
    )


def _is_loopback_host(value: str) -> bool:
    host = (value or "").strip().rstrip(".").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _effective_port(parts) -> int | None:
    try:
        if parts.port is not None:
            return parts.port
    except ValueError:
        return None
    return {"http": 80, "https": 443}.get(parts.scheme.lower())


def _same_local_origin(request: Request, source: str) -> bool:
    try:
        expected = urlsplit(str(request.url))
        supplied = urlsplit(source)
        expected_host = (expected.hostname or "").rstrip(".").lower()
        supplied_host = (supplied.hostname or "").rstrip(".").lower()
    except (TypeError, ValueError):
        return False
    return bool(
        _is_loopback_host(expected_host)
        and supplied.scheme.lower() in {"http", "https"}
        and supplied.scheme.lower() == expected.scheme.lower()
        and supplied_host == expected_host
        and _effective_port(supplied) == _effective_port(expected)
        and not supplied.username
        and not supplied.password
    )


async def _require_local_mutation(request: Request) -> None:
    """Fail closed unless a state-changing request is local and same-origin."""
    try:
        request_host = request.url.hostname or ""
    except ValueError:
        request_host = ""
    client_host = request.client.host if request.client is not None else ""
    if not _is_loopback_host(request_host) or not _is_loopback_host(client_host):
        raise HTTPException(status_code=403, detail="local audit request required")
    source = request.headers.get("origin") or request.headers.get("referer") or ""
    if not source or not _same_local_origin(request, source):
        raise HTTPException(status_code=403, detail="same-origin audit request required")
    supplied_token = request.headers.get("x-ceo-audit-csrf", "").strip()
    if not supplied_token:
        supplied_token = await _form_value(request, "csrf_token")
    if not supplied_token or not compare_digest(supplied_token, _CSRF_TOKEN):
        raise HTTPException(status_code=403, detail="invalid audit CSRF token")


def _safe_next(value: str) -> str:
    if value.startswith("/") and not value.startswith("//"):
        return value
    return "/feishu/review"


async def _form_value(request: Request, key: str, default: str = "") -> str:
    values = parse_qs(
        (await request.body()).decode("utf-8"), keep_blank_values=True
    ).get(key, [])
    return values[0].strip() if values else default


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
            "style='display:inline'>"
            f"{csrf_form_input()}"
            "<input type='hidden' name='approved_by' value='local-audit-review'>"
            "<button class='fs-send' type='submit'>批准</button></form> "
        )
    disable = (
        f"<form method='post' action='/feishu/scopes/{path_type}/{path_id}/disable' "
        "style='display:inline'>"
        f"{csrf_form_input()}"
        "<input type='hidden' name='approved_by' value='local-audit-review'>"
        "<button type='submit'>禁用</button></form>"
    )
    return (
        "<div class='fs-item'>"
        f"<div class='fs-meta'>[{target_type}] {target_id} · <b>{state}</b></div>"
        f"<div class='fs-text'>{name}</div>"
        f"<div class='fs-actions'>{approve}{disable}</div>"
        "</div>"
    )


def _delivery_item(delivery, attempt, *, actionable: bool) -> str:
    draft = html.escape((delivery.reply_text or "")[:2000])
    error = html.escape(delivery.error or "")
    trigger = ""
    reason = ""
    if attempt is not None:
        trigger = html.escape((attempt.trigger_text or "")[:1200])
        reason = html.escape((attempt.codex_reason or "")[:1200])
    actions = ""
    if actionable:
        actions = (
            f"<form method='post' action='/feishu/deliveries/{delivery.id}/approve' "
            "style='display:inline'>"
            f"{csrf_form_input()}"
            "<input type='hidden' name='approved_by' value='local-audit-review'>"
            "<button class='fs-send' type='submit'>发送</button></form> "
            f"<form method='post' action='/feishu/deliveries/{delivery.id}/reject' "
            "style='display:inline'>"
            f"{csrf_form_input()}"
            "<button type='submit'>拒绝</button></form>"
        )
    details = ""
    if trigger:
        details += f"<div class='fs-context'><b>触发：</b>{trigger}</div>"
    if reason:
        details += f"<div class='fs-context'><b>决策：</b>{reason}</div>"
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
        f"<div class='fs-meta'>#{delivery.id} · chat {html.escape(delivery.chat_id)} "
        f"· <b>{html.escape(delivery.status)}</b> · attempts={delivery.attempts}</div>"
        f"{details}<div class='fs-text'>{draft}</div>"
        f"<div class='fs-actions'>{actions}</div>"
        "</div>"
    )


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
    def feishu_review() -> HTMLResponse:
        from app import config
        from app.feishu.setup import dependency_status

        store = store_factory()
        app_id = config.feishu_app_id()
        scopes = store.list_feishu_reply_scopes(app_id=app_id) if app_id else []
        deliveries = store.list_feishu_deliveries(
            statuses=DELIVERY_STATUSES, app_id=app_id, limit=100
        ) if app_id else []
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
            "<meta http-equiv='refresh' content='5'>",
            "<style>body{font-family:-apple-system,system-ui,sans-serif;max-width:900px;margin:24px auto;padding:0 16px;color:#1a1a1a}"
            ".fs-item{border:1px solid #e2e5e9;border-radius:10px;padding:12px 14px;margin:10px 0}"
            ".fs-meta{color:#667085;font-size:12px;margin-bottom:6px}.fs-text{white-space:pre-wrap;line-height:1.5}"
            ".fs-context{font-size:13px;color:#475467;margin:5px 0}.fs-error{font-size:12px;color:#b42318;margin:5px 0}"
            ".fs-actions{margin-top:8px}button{padding:6px 16px;border-radius:8px;border:1px solid #ccd;background:#f6f7f9;cursor:pointer}"
            "button.fs-send{background:#3370ff;color:#fff;border-color:#3370ff;font-weight:700}"
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
                        actionable=(
                            delivery.status in {"ready_to_send", "retry"}
                            and not delivery.approved_at
                        ),
                    )
                )
        else:
            parts.append("<p>没有投递记录。</p>")
        return HTMLResponse("".join(parts))

    @app.get("/feishu/deliveries")
    def feishu_deliveries() -> JSONResponse:
        from app import config

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
        return JSONResponse(
            {
                "items": [
                    {
                        "id": row.id,
                        "attempt_id": row.attempt_id,
                        "chat_id": row.chat_id,
                        "status": row.status,
                        "reply_text": row.reply_text,
                        "attempts": row.attempts,
                        "approved_at": row.approved_at,
                        "approved_by": row.approved_by,
                        "error_code": row.error_code,
                        "error": row.error,
                        "created_at": row.created_at,
                        "updated_at": row.updated_at,
                    }
                    for row in rows
                ]
            }
        )

    async def _review_scope(
        request: Request,
        target_type: str,
        target_id: str,
        *,
        approved: bool,
    ) -> RedirectResponse:
        from app import config

        await _require_local_mutation(request)
        if target_type not in {"direct_sender", "group"}:
            raise HTTPException(status_code=422, detail="invalid Feishu target type")
        app_id = config.feishu_app_id()
        if not app_id:
            raise HTTPException(status_code=409, detail="Feishu App ID is missing")
        approved_by = await _form_value(request, "approved_by")
        if not approved_by:
            raise HTTPException(status_code=422, detail="approved_by is required")
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

        await _require_local_mutation(request)
        if not config.feishu_live_send_allowed():
            raise HTTPException(status_code=409, detail="Feishu outbound gates are closed")
        app_id = str(config.feishu_app_id() or "").strip()
        if not app_id:
            raise HTTPException(status_code=409, detail="Feishu App ID is missing")
        approved_by = await _form_value(request, "approved_by")
        if not approved_by:
            raise HTTPException(status_code=422, detail="approved_by is required")
        try:
            store_factory().approve_feishu_delivery(
                delivery_id,
                app_id=app_id,
                approved_by=approved_by,
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

        await _require_local_mutation(request)
        app_id = str(config.feishu_app_id() or "").strip()
        if not app_id:
            raise HTTPException(status_code=409, detail="Feishu App ID is missing")
        try:
            store_factory().reject_feishu_delivery(
                delivery_id,
                app_id=app_id,
                rejected_by="local-audit-review",
            )
        except PermissionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return RedirectResponse(_safe_next(next), status_code=303)
