"""WeChat Tutorial connection service (independent channel status).

Auto-selects exactly one detected account, probes capability, persists read
state, and reports separate database and accessibility status. A blocked reader
still returns a *successful action* whose resulting step is blocked — it never
reports the channel ready. Returns a plain result the wizard maps to its event
model, so this stays decoupled from the shared wizard code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from app.wechat import service
from app.wechat.models import WechatAccount


@dataclass
class WechatSetupResult:
    action_id: str
    status: str
    next_step_status: str = ""
    summary: str = ""
    evidence: dict = field(default_factory=dict)


class WechatSetupService:
    def __init__(self, store, reader, accessibility_preflight: Callable[[], str],
                 accessibility_request: Callable[[], str] | None = None,
                 accounts_provider: Callable[[], list[WechatAccount]] | None = None):
        self.store = store
        self.reader = reader
        self.accessibility_preflight = accessibility_preflight
        self.accessibility_request = accessibility_request
        if accounts_provider is None:
            raise ValueError("accounts_provider must come from the dedicated reader")
        self.accounts_provider = accounts_provider

    def discover_accounts(self) -> list[WechatAccount]:
        return self.accounts_provider()

    def connect(self, selected_account_id: str = "") -> WechatSetupResult:
        accounts = self.discover_accounts()
        if not selected_account_id and len(accounts) != 1:
            return WechatSetupResult(
                action_id="connect_wechat", status="failed",
                summary="Select exactly one detected WeChat account.",
                evidence={"account_count": len(accounts)},
            )
        account = next(
            item for item in accounts
            if item.account_id == (selected_account_id or accounts[0].account_id)
        )
        capability = self.reader.probe(account)
        from app import config

        self_user_id = config.wechat_self_user_id() or account.self_user_id
        if not self_user_id and capability.status == "ready":
            detect = getattr(self.reader, "detect_self_username", None)
            if detect is not None:
                self_user_id = detect(account)
        self.store.upsert_wechat_read_state(
            account_id=account.account_id, account_dir=account.account_dir,
            db_dir=account.db_dir, app_version=account.app_version,
            self_user_id=self_user_id,
            capability_status=capability.status, capability_reason=capability.reason,
        )
        accessibility_status = self.accessibility_preflight()
        if accessibility_status != "ready" and self.accessibility_request is not None:
            accessibility_status = self.accessibility_request()
        next_step_status = capability.status
        if capability.status == "ready" and accessibility_status != "ready":
            next_step_status = "blocked"
        return WechatSetupResult(
            action_id="connect_wechat", status="done",
            next_step_status=next_step_status,
            summary=capability.reason or "WeChat database is connected.",
            evidence={
                "account_id": account.account_id,
                "database_status": capability.status,
                "accessibility_status": accessibility_status,
            },
        )

    def check(self) -> WechatSetupResult:
        health = getattr(self.reader, "health", None)
        try:
            reader_ready = health is not None and health().get("status") == "ready"
        except Exception:
            reader_ready = False
        if not reader_ready:
            return WechatSetupResult(
                action_id="check_wechat_connection",
                status="needs_action",
                summary="CEO WeChat Reader app is not running.",
            )
        states = self.store.list_wechat_read_states()
        ready = [row for row in states if row["capability_status"] == "ready"]
        accessibility_status = self.accessibility_preflight()
        done = len(ready) == 1 and accessibility_status == "ready"
        if len(ready) == 1 and accessibility_status != "ready":
            summary = "CEO WeChat Sender app needs Accessibility permission."
        else:
            summary = "WeChat is ready." if done else "Connect one WeChat account."
        return WechatSetupResult(
            action_id="check_wechat_connection",
            status="done" if done else "needs_action",
            summary=summary,
            evidence={"accessibility_status": accessibility_status},
        )

    def verify(self) -> WechatSetupResult:
        check = self.check()
        scopes = self.store.list_wechat_reply_scopes_for_ready_account(enabled_only=True)
        accessibility_status = self.accessibility_preflight()
        complete = check.status == "done" and bool(scopes) and accessibility_status == "ready"
        return WechatSetupResult(
            action_id="verify_wechat", status="done",
            next_step_status="done" if complete else "blocked",
            summary="WeChat scope verified." if complete else "Select at least one stable reply target.",
            evidence={
                "selected_target_count": len(scopes),
                "accessibility_status": accessibility_status,
            },
        )

    def list_targets(self, *, query: str, kind: str, limit: int, offset: int) -> list[dict]:
        state = service.ready_account_state(self.store)
        if state is None:
            return []
        account = service.account_from_state(state)
        return self.reader.list_targets(account, kind=kind, query=query, limit=limit, offset=offset)
