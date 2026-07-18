"""Compose the WeChat channel into produce/consume/reconcile steps and loops.

Loops start only when the reader flag is on AND the persisted capability for a
single account is ``ready``; the sender flag is checked again per delivery.
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


def ready_account_state(store) -> dict | None:
    ready = [s for s in store.list_wechat_read_states() if s["capability_status"] == "ready"]
    return ready[0] if len(ready) == 1 else None


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
