"""Compose the WeChat channel into produce/consume/reconcile steps and loops.

Loops start only when the reader flag is on AND exactly one persisted account is
``ready`` with a non-empty self wxid; the sender flag is checked again per delivery.
Recovery runs before sender startup and turns orphaned ``sending`` rows into
``send_unknown`` (never ``ready_to_send``). Everything is disabled by default.
"""
from __future__ import annotations

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


def build_reader(mirror_dir, passphrase_file, *, self_username: str = ""):
    from app.wechat.backend import WcdbReaderBackend
    from app.wechat.reader import WechatReader
    from app.wechat.key_provider import PassphraseFileKeyProvider

    backend = WcdbReaderBackend(mirror_dir, self_username=self_username)
    return WechatReader(backend, PassphraseFileKeyProvider(passphrase_file))


def build_setup_service(store):
    """Construct a WechatSetupService from config (reader + accessibility preflight)."""
    from app import config
    from app.wechat.setup import WechatSetupService
    from app.wechat.accessibility import MacWechatAccessibility

    reader = build_reader(config.wechat_mirror_dir(), config.wechat_passphrase_file())

    def _preflight() -> str:
        try:
            return MacWechatAccessibility().preflight()
        except Exception:
            return "unknown"

    return WechatSetupService(store, reader, _preflight)


def run_produce_once(store, reader, account, *, self_user_id: str) -> int:
    from app.wechat.producer import WechatReplyProducer

    return WechatReplyProducer(
        store, reader, account, self_user_id=self_user_id
    ).run_once()


def run_consume_once(store, runner, reader, account) -> int:
    from app.wechat.consumer import WechatReplyConsumer

    return WechatReplyConsumer(store, runner, reader, account).run_once()


def recover_before_sender(store, reader) -> list:
    """Reconcile orphaned deliveries before any sender starts."""
    from app.wechat.accessibility import reconcile_incomplete_deliveries

    return reconcile_incomplete_deliveries(store, reader)


# ---- confirm-mode delivery gating (CEO_WECHAT_SEND_MODE) ----

def pending_wechat_deliveries(store) -> list:
    """Deliveries awaiting a decision (ready_to_send). In confirm mode these are
    what the user reviews and approves before anything is sent."""
    return store.list_wechat_deliveries_by_status("ready_to_send")


def _scope_for_delivery(store, delivery):
    return store.get_wechat_reply_scope(
        delivery.account_id, delivery.target_type, delivery.target_id
    )


def process_ready_wechat_deliveries(store, sender, *, mode: str, sender_enabled: bool) -> int:
    """Auto mode + sender enabled: send every ready_to_send delivery. Confirm mode
    (or sender disabled): send nothing — hold them for explicit approval. Returns
    the number sent."""
    if not sender_enabled or mode != "auto":
        return 0
    sent = 0
    for delivery in pending_wechat_deliveries(store):
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
    scope = _scope_for_delivery(store, delivery)
    if scope is None:
        raise ValueError("no reply scope for delivery target")
    return sender.send(delivery, scope).status


def reject_wechat_delivery(store, delivery_id: int) -> None:
    """User rejects a pending delivery: mark failed, never send."""
    store.set_wechat_delivery_status(delivery_id, "failed", error="user_rejected")


def verify_wechat_binding(store, scope, *, runner, is_unique: bool) -> str:
    """Real (non-asserted) binding verification. Sets binding_status to:
      - ``verified`` iff the display name maps to EXACTLY this conversation in the
        DB (is_unique) AND opening it in WeChat shows that same name (UI title);
      - ``conflict`` if the name is not DB-unique (can't disambiguate by name);
      - ``unverified`` if the UI could not be corroborated.
    Stores a fingerprint + redacted evidence; never a raw identity. Returns the
    new status."""
    from app.wechat.accessibility import target_fingerprint

    ui_title = ""
    try:
        ui_title = runner.open_and_identify(scope.display_name) if runner is not None else ""
    except Exception:
        ui_title = ""
    ui_match = bool(ui_title) and ui_title == scope.display_name

    if not is_unique:
        status = "conflict"
    elif ui_match:
        status = "verified"
    else:
        status = "unverified"

    fingerprint = target_fingerprint(scope.account_id, scope.target_type, scope.target_id, ui_title)
    evidence = {
        "basis": "db_unique_name+ui_title_match",
        "db_unique": str(is_unique),
        "ui_title_match": str(ui_match),
        "fingerprint": fingerprint,
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
