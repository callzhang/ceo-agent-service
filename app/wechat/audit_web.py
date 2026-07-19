"""Narrow local Tutorial routes for WeChat target selection.

Duplicate display names stay separable by stable target_id. The direct/group
trigger rule is enforced at request validation, so an invalid combination is a
422 rather than business-logic drift. No raw message previews, DB paths, keys, or
binding evidence are exposed.
"""
from __future__ import annotations

from typing import Callable, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, model_validator

from app.wechat.models import WechatReplyScope


class WechatScopeTarget(BaseModel):
    target_type: Literal["direct", "group"]
    target_id: str
    display_name: str
    trigger_mode: str
    conversation_id: str = ""

    @model_validator(mode="after")
    def _trigger_matches_type(self):
        expected = (
            "every_inbound_text" if self.target_type == "direct"
            else "mention_current_account"
        )
        if self.trigger_mode != expected:
            raise ValueError(f"{self.target_type} requires trigger_mode={expected}")
        return self


class WechatReplyScopeRequest(BaseModel):
    account_id: str
    targets: list[WechatScopeTarget]


def register_wechat_review_routes(app: FastAPI, *, store_factory: Callable[[], object],
                                  sender_factory: Callable[[object], object]) -> None:
    """Confirm-mode review UI: list ready_to_send deliveries with a 发送 (approve)
    and 拒绝 (reject) button. Approve runs the real send in a background thread
    (idle-gated), so the request returns immediately; the page auto-refreshes to
    show sending -> sent/failed. Nothing sends without this explicit click."""
    import html as _html
    import threading
    from fastapi.responses import HTMLResponse, RedirectResponse

    def _buckets(store):
        pend = store.list_wechat_deliveries_by_status("ready_to_send")
        sending = store.list_wechat_deliveries_by_status("sending")
        recent = (store.list_wechat_deliveries_by_status("sent")
                  + store.list_wechat_deliveries_by_status("send_unknown")
                  + store.list_wechat_deliveries_by_status("failed"))
        return pend, sending, recent

    def _item(d, actionable):
        text = _html.escape((d.reply_text or "")[:600])
        meta = f"#{d.id} · [{_html.escape(d.target_type)}] {_html.escape(d.target_id)} · " \
               f"<b>{_html.escape(d.status)}</b>"
        if d.error:
            meta += f" · <span class='wx-err'>{_html.escape(d.error)}</span>"
        actions = ""
        if actionable:
            actions = (
                f"<form method='post' action='/wechat/deliveries/{d.id}/approve' style='display:inline'>"
                f"<button class='wx-send' type='submit'>发送</button></form> "
                f"<form method='post' action='/wechat/deliveries/{d.id}/reject' style='display:inline'>"
                f"<button type='submit'>拒绝</button></form>")
        return (f"<div class='wx-item'><div class='wx-meta'>{meta}</div>"
                f"<div class='wx-text'>{text}</div><div class='wx-actions'>{actions}</div></div>")

    @app.get("/wechat/review", response_class=HTMLResponse)
    def wechat_review():
        store = store_factory()
        pend, sending, recent = _buckets(store)
        parts = [
            "<meta http-equiv='refresh' content='5'>",
            "<style>body{font-family:-apple-system,system-ui,sans-serif;max-width:820px;margin:24px auto;padding:0 16px;color:#1a1a1a}"
            ".wx-item{border:1px solid #e2e5e9;border-radius:10px;padding:12px 14px;margin:10px 0}"
            ".wx-meta{color:#667085;font-size:12px;margin-bottom:6px}.wx-err{color:#c0392b}"
            ".wx-text{white-space:pre-wrap;line-height:1.5}.wx-actions{margin-top:8px}"
            "button{padding:6px 16px;border-radius:8px;border:1px solid #ccd;background:#f6f7f9;cursor:pointer;font-size:14px}"
            "button.wx-send{background:#12a150;color:#fff;border-color:#12a150;font-weight:700}</style>",
            "<h2>WeChat 待发审核</h2>",
        ]
        if not pend and not sending:
            parts.append("<p style='color:#667085'>没有待发送的消息。</p>")
        parts += [_item(d, True) for d in pend]
        parts += [_item(d, False) for d in sending]
        if recent:
            parts.append("<h3 style='color:#667085;font-size:13px;margin-top:22px'>最近</h3>")
            parts += [_item(d, False) for d in recent[-10:]]
        return HTMLResponse("".join(parts))

    @app.get("/wechat/deliveries")
    def wechat_deliveries_json():
        store = store_factory()
        pend, sending, recent = _buckets(store)
        def j(d):
            return {"id": d.id, "target_type": d.target_type, "target_id": d.target_id,
                    "reply_text": d.reply_text, "status": d.status, "error": d.error}
        return {"pending": [j(d) for d in pend], "sending": [j(d) for d in sending],
                "recent": [j(d) for d in recent[-10:]]}

    @app.post("/wechat/deliveries/{delivery_id}/approve")
    def wechat_approve(delivery_id: int):
        def _run():
            try:
                from app.wechat import service as _svc
                store = store_factory()
                _svc.approve_wechat_delivery(store, sender_factory(store), delivery_id)
            except Exception:
                pass
        threading.Thread(target=_run, daemon=True).start()
        return RedirectResponse("/wechat/review", status_code=303)

    @app.post("/wechat/deliveries/{delivery_id}/reject")
    def wechat_reject(delivery_id: int):
        from app.wechat import service as _svc
        _svc.reject_wechat_delivery(store_factory(), delivery_id)
        return RedirectResponse("/wechat/review", status_code=303)


def register_wechat_tutorial_routes(app: FastAPI, *, setup_factory: Callable[[], object]) -> None:
    @app.get("/tutorial/wechat/conversations")
    def list_targets(query: str = "", kind: str = "direct", limit: int = 50, offset: int = 0):
        if kind not in {"direct", "group"} or not 1 <= limit <= 100 or offset < 0:
            raise HTTPException(status_code=422, detail="invalid target query")
        items = setup_factory().list_targets(query=query, kind=kind, limit=limit, offset=offset)
        return {"items": items}

    @app.post("/tutorial/wechat/reply-scope")
    def save_scope(payload: WechatReplyScopeRequest):
        service = setup_factory()
        scopes = [
            WechatReplyScope(
                account_id=payload.account_id, target_type=t.target_type,
                target_id=t.target_id, conversation_id=t.conversation_id or t.target_id,
                display_name=t.display_name, trigger_mode=t.trigger_mode,
            )
            for t in payload.targets
        ]
        service.store.replace_wechat_reply_scopes(payload.account_id, scopes)
        return {"saved": len(scopes)}
