"""Compose the WeChat channel into produce/consume/reconcile steps and loops.

Loops start only when the reader flag is on AND exactly one persisted account is
``ready`` with a non-empty self wxid; the sender flag is checked again per delivery.
Recovery runs before sender startup and turns orphaned ``sending`` rows into
``send_unknown`` (never ``ready_to_send``). Everything is disabled by default.
"""
from __future__ import annotations

import hashlib

from app.wechat.models import WechatAccount

PRODUCER_THREAD = "ceo-agent-service-wechat-producer"
CONSUMER_THREAD = "ceo-agent-service-wechat-consumer"


def account_from_state(state: dict) -> WechatAccount:
    return WechatAccount(
        account_id=state["account_id"],
        display_name=state.get("account_id", ""),
        self_user_id=state.get("self_user_id", ""),
        account_dir=state["account_dir"],
        db_dir=state["db_dir"],
        app_version=state["app_version"],
    )


def capability_ready_account_state(store) -> dict | None:
    ready = [s for s in store.list_wechat_read_states() if s["capability_status"] == "ready"]
    return ready[0] if len(ready) == 1 else None


def ready_account_state(store) -> dict | None:
    state = capability_ready_account_state(store)
    if state is None or not state.get("self_user_id", "").strip():
        return None
    return state


def wechat_loop_names(*, reader_enabled: bool, capability_ready: bool) -> list[str]:
    if reader_enabled and capability_ready:
        return [PRODUCER_THREAD, CONSUMER_THREAD]
    return []


def build_reader(*, socket_path=None):
    """Build the IPC facade used for all WeChat reads."""
    from app import config
    from app.wechat.reader_ipc import WechatReaderClient

    return WechatReaderClient(
        socket_path or config.wechat_reader_socket(),
        timeout_seconds=config.wechat_reader_timeout_seconds(),
    )


def build_sender(*, socket_path=None):
    """Build the IPC facade for the stable, Accessibility-trusted Sender app."""
    from app import config
    from app.wechat.sender_ipc import WechatSenderClient

    return WechatSenderClient(
        socket_path or config.wechat_sender_socket(),
        timeout_seconds=config.wechat_sender_timeout_seconds(),
    )


def build_setup_service(store):
    """Construct a WechatSetupService from config (reader + accessibility preflight)."""
    from app import config
    from app.wechat.setup import WechatSetupService

    reader = build_reader()
    sender = build_sender()

    def _preflight() -> str:
        try:
            return sender.preflight()
        except Exception:
            return "unknown"

    def _request_accessibility() -> str:
        try:
            return sender.request_accessibility()
        except Exception:
            return "unknown"

    return WechatSetupService(
        store,
        reader,
        _preflight,
        accessibility_request=_request_accessibility,
        accounts_provider=reader.discover_accounts,
    )


def run_produce_once(store, reader, account, *, self_user_id: str) -> int:
    from app.wechat.producer import WechatReplyProducer

    return WechatReplyProducer(
        store, reader, account, self_user_id=self_user_id
    ).run_once()


def run_consume_once(store, runner, reader, account) -> int:
    from app.wechat.consumer import WechatReplyConsumer

    return WechatReplyConsumer(store, runner, reader, account).run_once()


def recover_before_sender(store, reader, account=None) -> list:
    """Recover safe pre-action failures and reconcile uncertain sends."""
    from app.wechat.accessibility import reconcile_incomplete_deliveries

    store.requeue_unperformed_wechat_deliveries()
    return reconcile_incomplete_deliveries(store, reader, account=account)


# ---- confirm-mode delivery gating (CEO_WECHAT_SEND_MODE) ----

def pending_wechat_deliveries(store) -> list:
    """Deliveries awaiting a decision (ready_to_send). In confirm mode these are
    what the user reviews and approves before anything is sent."""
    return store.list_wechat_deliveries_by_status("ready_to_send")


def _scope_for_delivery(store, delivery):
    return store.get_wechat_reply_scope(
        delivery.account_id, delivery.target_type, delivery.target_id
    )


def _sender_is_ready(sender) -> bool:
    runner = getattr(sender, "runner", None)
    preflight = getattr(runner, "preflight", None)
    if preflight is None:
        return True
    try:
        return preflight() == "ready"
    except Exception:
        return False


def _refresh_direct_binding_evidence(delivery, reader, account):
    """Return a transient delivery carrying the current sidebar text evidence.

    Direct-chat replies may wait for minutes before sending. The original trigger
    is therefore not necessarily the text WeChat currently renders in its session
    row. Read the exact conversation again immediately before any UI action and
    bind navigation to its newest message. No suitable current text means the
    delivery must remain pending rather than falling back to a display name.
    """
    if delivery.target_type != "direct" or reader is None or account is None:
        return delivery
    messages = reader.read_messages(
        account,
        conversation_id=delivery.conversation_id,
        conversation_type=delivery.target_type,
        limit=10,
        order="newest",
    )
    if not messages:
        return None
    current_text = next(
        ((message.text or "").strip() for message in messages
         if (message.text or "").strip()),
        "",
    )
    if not current_text:
        return None
    evidence = dict(delivery.evidence)
    evidence["trigger_text"] = current_text
    return delivery.model_copy(update={"evidence": evidence})


def process_ready_wechat_deliveries(
    store,
    sender,
    *,
    mode: str,
    sender_enabled: bool,
    reader=None,
    account=None,
) -> int:
    """Auto mode + sender enabled: send every ready_to_send delivery. Confirm mode
    (or sender disabled): send nothing — hold them for explicit approval. Returns
    the number sent."""
    recover_before_sender(store, reader, account=account)
    if not sender_enabled or mode != "auto":
        return 0
    if not _sender_is_ready(sender):
        return 0
    sent = 0
    for delivery in pending_wechat_deliveries(store):
        try:
            delivery = _refresh_direct_binding_evidence(delivery, reader, account)
        except Exception:
            # This happens before a delivery is claimed or WeChat is touched. Keep
            # it ready so the next low-frequency sender pass can retry safely.
            continue
        if delivery is None:
            continue
        scope = _scope_for_delivery(store, delivery)
        if scope is None:
            continue
        if (
            scope.target_type == "direct"
            and scope.binding_status == "unverified"
            and getattr(sender, "runner", None) is not None
        ):
            verify_wechat_binding(
                store,
                scope,
                runner=sender.runner,
                is_unique=True,
                expected_recent_text=delivery.evidence.get("trigger_text") or "",
            )
            scope = _scope_for_delivery(store, delivery)
            if scope is None:
                continue
        sender.send(delivery, scope)
        sent += 1
    return sent


def approve_wechat_delivery(store, sender, delivery_id: int) -> str:
    """Explicit user approval of one pending delivery (used by UI/CLI). Sends it
    regardless of send mode; returns the resulting delivery status."""
    delivery = next(
        (d for d in pending_wechat_deliveries(store) if d.id == delivery_id), None
    )
    if delivery is None:
        raise ValueError(f"no pending delivery {delivery_id}")
    if not _sender_is_ready(sender):
        raise RuntimeError("WeChat sender is temporarily unavailable")
    scope = _scope_for_delivery(store, delivery)
    if scope is None:
        raise ValueError("no reply scope for delivery target")
    return sender.send(delivery, scope).status


def reject_wechat_delivery(store, delivery_id: int) -> None:
    """User rejects a pending delivery: mark failed, never send."""
    store.set_wechat_delivery_status(delivery_id, "failed", error="user_rejected")


def verify_wechat_binding(
    store, scope, *, runner, is_unique: bool, expected_recent_text: str = "",
) -> str:
    """Real (non-asserted) binding verification. Sets binding_status to:
      - ``verified`` iff the display name maps to EXACTLY this conversation in the
        DB (is_unique) AND opening it in WeChat shows that same name (UI title);
      - ``conflict`` if the name is not DB-unique (can't disambiguate by name);
      - ``unverified`` if the UI could not be corroborated.
    Stores a fingerprint + redacted evidence; never a raw identity. Returns the
    new status."""
    from app.wechat.accessibility import target_fingerprint

    navigation_query = scope.display_name if scope.target_type == "group" else ""
    ui_title = ""
    try:
        ui_title = (
            runner.open_and_identify(
                scope.display_name,
                search_query=navigation_query or None,
                expected_recent_text=expected_recent_text or None,
            )
            if runner is not None else ""
        )
    except Exception:
        ui_title = ""
    ui_match = bool(ui_title) and ui_title == scope.display_name

    if scope.target_type == "direct" and not expected_recent_text.strip():
        status = "unverified"
    elif scope.target_type == "group" and not is_unique:
        status = "conflict"
    elif ui_match:
        status = "verified"
    else:
        status = "unverified"

    fingerprint = target_fingerprint(scope.account_id, scope.target_type, scope.target_id, ui_title)
    evidence = {
        "basis": (
            "sidebar_recent_text+ui_title_match"
            if scope.target_type == "direct"
            else "db_unique_name+ui_title_match"
        ),
        "db_unique": str(is_unique),
        "ui_title_match": str(ui_match),
        "fingerprint": fingerprint,
        "navigation_query": navigation_query,
        "recent_text_sha256": (
            hashlib.sha256(expected_recent_text.encode("utf-8")).hexdigest()
            if expected_recent_text else ""
        ),
    }
    scopes = store.list_wechat_reply_scopes(scope.account_id)
    updated = [
        s.model_copy(update={"binding_status": status, "binding_evidence": evidence})
        if (s.target_type == scope.target_type and s.target_id == scope.target_id) else s
        for s in scopes
    ]
    store.replace_wechat_reply_scopes(scope.account_id, updated)
    return status


def recall_wechat_delivery(store, runner, delivery_id: int, reply_text: str) -> bool:
    """Best-effort recall (撤回) of an already-sent delivery. Only works while the
    2-minute WeChat recall window is open and the runner supports it; returns
    whether recall was performed. Detection of a wrong send is delayed (WAL lag on
    DB reconcile), so this is a backstop, not a guaranteed auto-catch."""
    recall = getattr(runner, "recall_last_outbound", None)
    if recall is None:
        return False
    ok = bool(recall(reply_text))
    if ok:
        store.set_wechat_delivery_status(delivery_id, "failed", error="recalled")
    return ok
