"""One in-process WeChat producer pass for the scheduled service command."""

from __future__ import annotations

from collections.abc import Callable
import errno

from app.config import wechat_reader_enabled
from app.wechat.reader_ipc import ReaderIpcError
from app.wechat.service import (
    account_from_state,
    build_reader,
    ready_account_state,
    run_produce_once,
)


WECHAT_READER_COMPONENT = "wechat.reader"
WECHAT_DATA_PERMISSION_REQUIRED = "wechat_data_permission_required"
WECHAT_READER_UNAVAILABLE = "wechat_reader_unavailable"
READER_RESTART_AFTER_FAILURES = 3


class WechatProduceOnceCommand:
    """Run the WeChat producer once, the same pass as ``app.wechat.cli produce-once``.

    Reader availability is a reader health fact, not a trigger failure: a
    disabled reader, a missing account, or an unreachable Reader app returns a summary, reports
    itself once through the ``wechat.reader`` health component and the error
    log, and requests one Reader restart after repeated IPC failures. The
    next successful pass clears that report. Any other exception is a real
    command failure and propagates to the trigger.
    """

    def __init__(
        self,
        store,
        *,
        restart_reader: Callable[[], None],
        reader_factory: Callable[[], object] = build_reader,
    ) -> None:
        self._store = store
        self._restart_reader = restart_reader
        self._reader_factory = reader_factory
        self._reader = None
        self._consecutive_reader_failures = 0
        self._reported = False

    def __call__(self) -> str:
        if not wechat_reader_enabled():
            return "wechat produce-once skipped: reader disabled"
        state = ready_account_state(self._store)
        if state is None:
            return "wechat produce-once skipped: no ready WeChat account"
        account = account_from_state(state)
        if self._reader is None:
            self._reader = self._reader_factory()
        try:
            queued = run_produce_once(
                self._store, self._reader, account, self_user_id=account.self_user_id
            )
        except ReaderIpcError as exc:
            if exc.code == "permission_required":
                return self._permission_required(
                    "CEO WeChat Reader App Data permission is required; "
                    "producer paused until it is granted."
                )
            return self._reader_unavailable(exc)
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EPERM}:
                raise
            return self._permission_required(
                "WeChat data access was denied; producer paused until it is granted."
            )
        self._consecutive_reader_failures = 0
        self._reported = False
        self._store.resolve_errors_recovered_by_wechat_reader()
        self._store.set_service_health_component(WECHAT_READER_COMPONENT, state="healthy")
        return f"wechat produce-once queued={queued}"

    def _permission_required(self, detail: str) -> str:
        if not self._reported:
            self._store.record_error("wechat", "", WECHAT_DATA_PERMISSION_REQUIRED, detail)
            self._reported = True
        return "wechat produce-once paused: data permission required"

    def _reader_unavailable(self, exc: ReaderIpcError) -> str:
        self._consecutive_reader_failures += 1
        if (
            self._consecutive_reader_failures >= READER_RESTART_AFTER_FAILURES
            and not self._reported
        ):
            self._restart_reader()
            self._store.set_service_health_component(
                WECHAT_READER_COMPONENT,
                state="degraded",
                detail="reader unavailable; automatic restart requested",
            )
            self._store.record_error(
                "wechat", "", WECHAT_READER_UNAVAILABLE,
                f"WeChat reader unavailable; producer retrying automatically: {exc}",
            )
            self._reported = True
        return f"wechat produce-once reader unavailable: {exc}"
