"""Narrow local Tutorial routes for WeChat target selection.

Duplicate display names stay separable by stable target_id. The direct/group
trigger rule is enforced at request validation, so an invalid combination is a
422 rather than business-logic drift. No raw message previews, DB paths, keys, or
binding evidence are exposed.
"""
from __future__ import annotations

from typing import Callable, Literal

from fastapi import FastAPI, HTTPException, Request
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


def register_wechat_memory_review_routes(
    app: FastAPI, *, store_factory: Callable[[], object],
    writer_factory: Callable[[object], object],
) -> None:
    """Human review for cleaned candidates. There is deliberately no bulk approve."""
    import html
    from urllib.parse import parse_qs
    from fastapi.responses import HTMLResponse, RedirectResponse

    async def _form(request: Request) -> dict[str, list[str]]:
        return parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)

    def _one(form: dict[str, list[str]], key: str) -> str:
        values = form.get(key, [])
        return values[0].strip() if values else ""

    @app.get("/wechat/memory-review", response_class=HTMLResponse)
    def memory_review(status: str = "", category: str = "", sensitivity: str = ""):
        rows = store_factory().list_wechat_memory_candidates(
            status=status or None, category=category or None,
            sensitivity=sensitivity or None,
        )
        parts = [
            "<style>body{font-family:-apple-system,system-ui,sans-serif;margin:24px}"
            "table{border-collapse:collapse;width:100%}th,td{border:1px solid #ddd;"
            "padding:7px;vertical-align:top}textarea{width:24em;height:4em}"
            ".meta{color:#667085;font-size:12px}</style>",
            "<h2>微信 Memory 人工审核</h2>",
            "<p>候选只会在人工逐条批准后，通过下方明确勾选写入。此页不提供批量批准。</p>",
            "<form method='get'><input name='status' placeholder='status' value='",
            html.escape(status), "'><input name='category' placeholder='category' value='",
            html.escape(category), "'><input name='sensitivity' placeholder='sensitivity' value='",
            html.escape(sensitivity), "'><button>筛选</button></form>",
            "<form method='post'><table><tr><th>选择</th><th>清理后内容</th>"
            "<th>分类/置信度/敏感度</th><th>最小证据</th><th>来源时间/清理说明</th>"
            "<th>状态/写入</th><th>审核</th></tr>",
        ]
        for row in rows:
            cid = int(row["id"])
            statement = html.escape(row["edited_statement"] or row["statement"])
            parts.extend([
                "<tr><td><input type='checkbox' name='candidate_id' value='", str(cid), "'></td>",
                "<td>", statement, "</td><td>", html.escape(row["category"]), " / ",
                html.escape(f"{row['confidence']:.2f}"), " / ", html.escape(row["sensitivity"]),
                "</td><td>", html.escape(row["evidence_excerpt"]), "</td><td><span class='meta'>",
                html.escape(f"{row['source_time_start']} — {row['source_time_end']}"), "</span><br>",
                html.escape(row["cleanup_notes"]), "</td><td>", html.escape(row["status"]),
                " / ", html.escape(row["memory_write_status"] or "not_written"), "</td><td>",
            ])
            if row["status"] == "pending":
                parts.extend([
                    f"<textarea name='final_statement_{cid}'>", html.escape(row["statement"]),
                    f"</textarea><br><input name='reviewer_{cid}' placeholder='reviewer'>",
                    f"<button formaction='/wechat/memory-review/{cid}/approve' formmethod='post'>批准</button>",
                    f"<button formaction='/wechat/memory-review/{cid}/reject' formmethod='post'>拒绝</button>",
                ])
            elif row["status"] == "approved":
                parts.extend([
                    f"<input name='reviewer_{cid}' placeholder='reviewer'>",
                    f"<button formaction='/wechat/memory-review/{cid}/revoke' formmethod='post'>撤销批准</button>"
                ])
            parts.append("</td></tr>")
        parts.append(
            "</table><button formaction='/wechat/memory-review/write-approved' "
            "formmethod='post'>写入已勾选且已批准项</button> "
            "<input name='reviewer' placeholder='bulk reject reviewer'> "
            "<button formaction='/wechat/memory-review/reject-selected' "
            "formmethod='post'>批量拒绝已勾选项</button></form>"
        )
        return HTMLResponse("".join(parts))

    @app.post("/wechat/memory-review/write-approved")
    async def write_approved(request: Request):
        form = await _form(request)
        store = store_factory()
        writer = writer_factory(store)
        for raw_id in form.get("candidate_id", []):
            try:
                writer.write(int(raw_id))
            except (ValueError, RuntimeError):
                continue
        return RedirectResponse("/wechat/memory-review", status_code=303)

    @app.post("/wechat/memory-review/reject-selected")
    async def reject_selected(request: Request):
        form = await _form(request)
        store = store_factory()
        reviewer = _one(form, "reviewer")
        for raw_id in form.get("candidate_id", []):
            try:
                store.review_wechat_memory_candidate(int(raw_id), "reject", reviewer=reviewer)
            except ValueError:
                continue
        return RedirectResponse("/wechat/memory-review", status_code=303)

    @app.post("/wechat/memory-review/{candidate_id}/approve")
    async def approve_memory(candidate_id: int, request: Request):
        form = await _form(request)
        final_statement = _one(form, "final_statement") or _one(
            form, f"final_statement_{candidate_id}"
        )
        try:
            store_factory().review_wechat_memory_candidate(
                candidate_id, "approve", reviewer=(
                    _one(form, f"reviewer_{candidate_id}") or _one(form, "reviewer")
                ),
                final_statement=final_statement,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return RedirectResponse("/wechat/memory-review", status_code=303)

    @app.post("/wechat/memory-review/{candidate_id}/reject")
    async def reject_memory(candidate_id: int, request: Request):
        form = await _form(request)
        try:
            store_factory().review_wechat_memory_candidate(
                candidate_id, "reject", reviewer=(
                    _one(form, f"reviewer_{candidate_id}") or _one(form, "reviewer")
                ))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return RedirectResponse("/wechat/memory-review", status_code=303)

    @app.post("/wechat/memory-review/{candidate_id}/revoke")
    async def revoke_memory(candidate_id: int, request: Request):
        form = await _form(request)
        try:
            store_factory().review_wechat_memory_candidate(
                candidate_id, "revoke", reviewer=(
                    _one(form, f"reviewer_{candidate_id}") or _one(form, "reviewer")
                ))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return RedirectResponse("/wechat/memory-review", status_code=303)


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
