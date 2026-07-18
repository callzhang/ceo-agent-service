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
