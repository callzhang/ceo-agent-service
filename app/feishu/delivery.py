"""Exactly-once-oriented, fail-closed Feishu delivery state machine."""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Any, Callable
from uuid import UUID, uuid5

from app.feishu.client import FeishuSendResult
from app.feishu.payloads import (
    FeishuReplyPayload,
    delivery_chunk_idempotency_key,
    delivery_chunk_plan_sha256,
    split_reply_payload,
)
from app.feishu.rate_limit import SlidingWindowMutationBudget


DELIVERY_UUID_NAMESPACE = UUID("49f6141e-9852-5e2d-8e9e-3c4207468328")
TARGET_PROBE_UNKNOWN_ERROR_CODE = "target_probe_unknown"
KNOWN_ERROR_CODES = frozenset(
    {
        "format_error",
        "target_revoked",
        "rate_limited",
        "permission_denied",
        "upload_failed",
        "download_failed",
        "ssrf_blocked",
        "send_timeout",
        "not_connected",
        "unknown",
    }
)
RETRYABLE_CODES = frozenset({"rate_limited", "not_connected"})
TERMINAL_CODES = frozenset(
    {
        "format_error",
        "target_revoked",
        "permission_denied",
        "upload_failed",
        "download_failed",
        "ssrf_blocked",
    }
)
DEFAULT_SEND_TIMEOUT_SECONDS = 60.0
DEFAULT_SEND_LEASE_STALE_SECONDS = 5 * 60
_OPEN_ID_RE = re.compile(r"^ou_[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class FeishuDeliveryOutcome:
    status: str
    error_code: str = ""
    error: str = ""
    message_id: str = ""
    request_log_id: str = ""


def delivery_idempotency_key(
    *, app_id: str, reply_task_id: int, trigger_message_id: str
) -> str:
    """Create the stable UUID stored once with a delivery and reused forever."""
    if not app_id or reply_task_id <= 0 or not trigger_message_id:
        raise ValueError("delivery idempotency identity is incomplete")
    name = f"{app_id}\0{reply_task_id}\0{trigger_message_id}"
    return str(uuid5(DELIVERY_UUID_NAMESPACE, name))


def error_code(value: Any) -> str:
    code = getattr(value, "code", "")
    if not code:
        code = getattr(getattr(value, "error", None), "code", "")
    code = str(getattr(code, "value", code) or "").strip().lower()
    if code in KNOWN_ERROR_CODES:
        return code
    if isinstance(value, (TimeoutError, asyncio.TimeoutError)):
        return "send_timeout"
    name = type(value).__name__.lower()
    if "timeout" in name:
        return "send_timeout"
    if "notconnected" in name or "not_connected" in name:
        return "not_connected"
    return "unknown"


class FeishuDeliverySender:
    """Only class in the channel allowed to invoke ``client.send_reply``."""

    def __init__(
        self,
        store,
        client,
        *,
        sender_enabled: bool = False,
        live_send_allowed: bool = False,
        send_mode: str = "confirm",
        max_sends_per_minute: int = 10,
        max_attempts: int = 3,
        send_timeout_seconds: float = DEFAULT_SEND_TIMEOUT_SECONDS,
        send_lease_stale_seconds: int = DEFAULT_SEND_LEASE_STALE_SECONDS,
        now: Callable[[], datetime] | None = None,
        monotonic_clock: Callable[[], float] = monotonic,
        reply_mention_sender_enabled: Callable[[], bool] | None = None,
        reply_mention_open_ids: Callable[[], tuple[str, ...]] | None = None,
        mutation_budget: SlidingWindowMutationBudget | None = None,
    ):
        if send_mode not in {"confirm", "auto"}:
            raise ValueError("Feishu send_mode must be confirm or auto")
        if (
            max_sends_per_minute <= 0
            or max_attempts <= 0
            or send_timeout_seconds <= 0
            or send_lease_stale_seconds <= send_timeout_seconds
        ):
            raise ValueError("Feishu delivery limits must be positive")
        self.store = store
        self.client = client
        self.sender_enabled = sender_enabled
        self.live_send_allowed = live_send_allowed
        self.send_mode = send_mode
        self.max_sends_per_minute = max_sends_per_minute
        self.max_attempts = max_attempts
        self.send_timeout_seconds = send_timeout_seconds
        self.send_lease_stale_seconds = send_lease_stale_seconds
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.monotonic_clock = monotonic_clock
        if reply_mention_sender_enabled is not None and not callable(
            reply_mention_sender_enabled
        ):
            raise ValueError("Feishu reply mention gate must be callable")
        if reply_mention_open_ids is not None and not callable(
            reply_mention_open_ids
        ):
            raise ValueError("Feishu reply mention allowlist must be callable")
        self._reply_mention_sender_enabled = (
            reply_mention_sender_enabled or (lambda: False)
        )
        self._reply_mention_open_ids = (
            reply_mention_open_ids or (lambda: ())
        )
        self.mutation_budget = mutation_budget or SlidingWindowMutationBudget(
            max_sends_per_minute,
            monotonic_clock=monotonic_clock,
        )
        # Kept as a compatibility alias for focused tests and local diagnostics.
        self._sent_times = self.mutation_budget._mutation_times

    @property
    def outbound_gate_open(self) -> bool:
        return self.sender_enabled and self.live_send_allowed

    def _authenticated_app_id(self) -> str:
        app_id = str(getattr(self.client, "app_id", "") or "").strip()
        if not app_id:
            raise PermissionError("Feishu authenticated client App ID is unavailable")
        return app_id

    def _require_delivery_binding(
        self, delivery, *, require_send_lease: bool = True
    ):
        app_id = self._authenticated_app_id()
        if str(getattr(delivery, "app_id", "") or "").strip() != app_id:
            raise PermissionError("Feishu delivery App ID does not match client")
        lease_token = str(getattr(delivery, "lease_token", "") or "").strip()
        if require_send_lease and not lease_token:
            raise ValueError("Feishu delivery has no active send lease")
        validated = self.store.validate_feishu_delivery_for_send(
            int(getattr(delivery, "id", 0) or 0),
            app_id=app_id,
            lease_token=(lease_token if require_send_lease else ""),
        )
        if validated.attempt_id != int(
            getattr(delivery, "attempt_id", 0) or 0
        ):
            raise ValueError("Feishu delivery attempt identity changed")
        immutable_fields = (
            "reply_task_id",
            "attempt_id",
            "app_id",
            "chat_id",
            "reply_to_message_id",
            "reply_in_thread",
            "reply_text",
            "reply_format",
            "mention_open_ids",
            "payload_sha256",
            "idempotency_key",
            "expected_chunks",
            "chunk_plan_sha256",
            "review_generation",
            "approval_hash",
        )
        if any(
            getattr(validated, field) != getattr(delivery, field, None)
            for field in immutable_fields
        ):
            raise ValueError("Feishu delivery immutable snapshot changed")
        return validated

    def _rate_slot(self) -> bool:
        return self.mutation_budget.try_acquire()

    def _reply_mentions_currently_authorized(self, delivery) -> bool:
        """Revalidate frozen mention identities against current local policy."""
        mentions = tuple(getattr(delivery, "mention_open_ids", ()) or ())
        if not mentions:
            return True
        try:
            if not bool(self._reply_mention_sender_enabled()):
                return False
            configured = self._reply_mention_open_ids()
        except Exception:
            return False
        if not isinstance(configured, (tuple, list)):
            return False
        allowed: list[str] = []
        for open_id in configured:
            if (
                not isinstance(open_id, str)
                or open_id != open_id.strip()
                or not _OPEN_ID_RE.fullmatch(open_id)
            ):
                return False
            if open_id not in allowed:
                allowed.append(open_id)
        if len(allowed) > 20:
            return False
        return all(open_id in allowed for open_id in mentions)

    def _fail_closed_revoked_mentions(
        self,
        delivery,
        *,
        known_message_ids: tuple[str, ...] = (),
        request_log_id: str = "",
    ) -> FeishuDeliveryOutcome:
        error = "reply_mention_authorization_revoked_before_send"
        self._transition_with_prefix(
            delivery,
            "failed",
            known_message_ids=known_message_ids,
            request_log_id=request_log_id,
            error_code="permission_denied",
            error=error,
            actor="sender",
            audit_event_type="mention_authorization_revoked",
            audit_detail=f"mention_count={len(delivery.mention_open_ids)}",
        )
        return FeishuDeliveryOutcome(
            "failed",
            "permission_denied",
            error,
            message_id=(known_message_ids[0] if known_message_ids else ""),
            request_log_id=request_log_id,
        )

    def _retry_at(self, attempts: int, *, rate_limited: bool = False) -> str:
        seconds = 60 if rate_limited else min(300, max(5, 2 ** max(1, attempts)))
        return (self.now().astimezone(timezone.utc) + timedelta(seconds=seconds)).isoformat()

    def _transition(self, delivery, status: str, **fields):
        return self.store.transition_feishu_delivery(
            delivery.id,
            from_statuses=("sending",),
            to_status=status,
            expected_lease_token=delivery.lease_token,
            **fields,
        )

    def _superseded_outcome(self, delivery) -> FeishuDeliveryOutcome | None:
        current = self.store.get_feishu_delivery(delivery.id)
        if (
            current is not None
            and current.status == "rejected"
            and current.error_code == "superseded"
        ):
            return FeishuDeliveryOutcome(
                "rejected",
                "superseded",
                "superseded_by_newer_feishu_trigger",
            )
        return None

    def _transition_with_prefix(
        self,
        delivery,
        status: str,
        *,
        known_message_ids: tuple[str, ...] = (),
        request_log_id: str = "",
        **fields,
    ):
        if known_message_ids:
            fields["feishu_message_id"] = known_message_ids[0]
            fields["message_ids"] = known_message_ids
        if request_log_id:
            fields["request_log_id"] = request_log_id
        return self._transition(delivery, status, **fields)

    def _finish_failure(
        self,
        delivery,
        result: FeishuSendResult,
        *,
        remote_failures: int,
        known_message_ids: tuple[str, ...] = (),
        failure_error: str = "",
    ) -> FeishuDeliveryOutcome:
        code = (
            result.error_code
            if result.error_code in KNOWN_ERROR_CODES
            else "unknown"
        )
        error = failure_error or f"feishu_send_failed:{code}"
        next_remote_failures = remote_failures + 1
        # A response that contains an ID proves a mutation occurred but a
        # contradictory failed result cannot prove the chunk's final state.
        if result.message_ids:
            self._transition_with_prefix(
                delivery,
                "send_unknown",
                known_message_ids=known_message_ids,
                request_log_id=result.request_log_id,
                remote_failures=next_remote_failures,
                error_code="unknown",
                error="feishu_partial_delivery_result_unknown",
            )
            return FeishuDeliveryOutcome(
                "send_unknown",
                "unknown",
                "feishu_partial_delivery_result_unknown",
                message_id=(known_message_ids[0] if known_message_ids else ""),
                request_log_id=result.request_log_id,
            )
        if code in RETRYABLE_CODES and next_remote_failures < self.max_attempts:
            self._transition_with_prefix(
                delivery,
                "retry",
                known_message_ids=known_message_ids,
                available_at=self._retry_at(
                    next_remote_failures, rate_limited=code == "rate_limited"
                ),
                request_log_id=result.request_log_id,
                remote_failures=next_remote_failures,
                error_code=code,
                error=error,
            )
            return FeishuDeliveryOutcome("retry", code, error)
        if code in TERMINAL_CODES or code in RETRYABLE_CODES:
            self._transition_with_prefix(
                delivery,
                "failed",
                known_message_ids=known_message_ids,
                request_log_id=result.request_log_id,
                remote_failures=next_remote_failures,
                error_code=code,
                error=error,
            )
            return FeishuDeliveryOutcome(
                "failed",
                code,
                error,
                message_id=(known_message_ids[0] if known_message_ids else ""),
                request_log_id=result.request_log_id,
            )
        # Only timeouts and unknown results lack proof of non-delivery.
        self._transition_with_prefix(
            delivery,
            "send_unknown",
            known_message_ids=known_message_ids,
            request_log_id=result.request_log_id,
            remote_failures=next_remote_failures,
            error_code=code,
            error=error,
        )
        return FeishuDeliveryOutcome(
            "send_unknown",
            code,
            error,
            message_id=(known_message_ids[0] if known_message_ids else ""),
            request_log_id=result.request_log_id,
        )

    @staticmethod
    def _chunk_plan(delivery) -> tuple[str, ...]:
        payload = FeishuReplyPayload(
            kind=delivery.reply_format,
            text=delivery.reply_text,
            mention_open_ids=delivery.mention_open_ids,
        )
        if payload.sha256() != delivery.payload_sha256:
            raise ValueError("Feishu delivery payload snapshot changed")
        chunks = split_reply_payload(payload)
        if len(chunks) != delivery.expected_chunks:
            raise ValueError("Feishu delivery chunk plan changed")
        if delivery_chunk_plan_sha256(chunks) != delivery.chunk_plan_sha256:
            raise ValueError("Feishu delivery chunk boundaries changed")
        return chunks

    def _record_chunk_result(
        self,
        delivery,
        *,
        ordinal: int,
        result: FeishuSendResult,
    ) -> str:
        if len(result.message_ids) != 1:
            raise ValueError("Feishu SDK returned an unexpected wire chunk set")
        message_id = result.message_ids[0]
        self.store.record_feishu_delivery_chunk(
            delivery.id,
            app_id=delivery.app_id,
            lease_token=delivery.lease_token,
            ordinal=ordinal,
            expected_chunks=delivery.expected_chunks,
            message_id=message_id,
            request_log_id=result.request_log_id,
        )
        return message_id

    async def send_claimed(self, delivery) -> FeishuDeliveryOutcome:
        if not self.outbound_gate_open:
            raise PermissionError("Feishu outbound gates are closed")
        if delivery.status != "sending":
            raise ValueError("Feishu delivery must be atomically claimed first")
        delivery = self._require_delivery_binding(delivery)
        if not self._reply_mentions_currently_authorized(delivery):
            return self._fail_closed_revoked_mentions(delivery)
        if self.send_mode == "confirm" and not (
            delivery.approved_at and delivery.approved_by
        ):
            self._transition(
                delivery,
                "failed",
                error_code="format_error",
                error="durable_approval_missing_at_send",
                actor="sender",
                audit_event_type="approval_missing_at_send",
            )
            return FeishuDeliveryOutcome(
                "failed", "format_error", "durable_approval_missing_at_send"
            )
        chunks = self._chunk_plan(delivery)
        try:
            prior_receipts = self.store.validate_feishu_delivery_receipt_prefix(
                delivery.id, app_id=delivery.app_id
            )
        except ValueError:
            self._transition(
                delivery,
                "send_unknown",
                error_code="unknown",
                error="delivery_receipt_prefix_invalid",
            )
            return FeishuDeliveryOutcome(
                "send_unknown", "unknown", "delivery_receipt_prefix_invalid"
            )

        known_message_ids = [receipt.message_id for receipt in prior_receipts]
        remote_failures = int(delivery.remote_failures)
        last_request_log_id = (
            prior_receipts[-1].request_log_id
            if prior_receipts
            else delivery.request_log_id
        )
        # A crash can happen after the final durable receipt but before the
        # terminal CAS.  No remote probe or mutation is needed to converge it.
        if len(known_message_ids) == delivery.expected_chunks:
            self._transition_with_prefix(
                delivery,
                "sent",
                known_message_ids=tuple(known_message_ids),
                request_log_id=last_request_log_id,
                remote_failures=0,
            )
            return FeishuDeliveryOutcome(
                "sent",
                message_id=known_message_ids[0],
                request_log_id=last_request_log_id,
            )

        try:
            target_state = await asyncio.wait_for(
                self.client.fetch_message_state(
                    delivery.app_id, delivery.reply_to_message_id
                ),
                timeout=self.send_timeout_seconds,
            )
        except asyncio.CancelledError:
            # The existence probe has no remote mutation, so the claimed row
            # can safely become retryable rather than result-unknown.
            next_remote_failures = remote_failures + 1
            retryable = next_remote_failures < self.max_attempts
            self._transition_with_prefix(
                delivery,
                "retry" if retryable else "failed",
                known_message_ids=tuple(known_message_ids),
                available_at=(
                    self._retry_at(next_remote_failures) if retryable else ""
                ),
                remote_failures=next_remote_failures,
                error_code=TARGET_PROBE_UNKNOWN_ERROR_CODE,
                error=(
                    "trigger_state_probe_cancelled"
                    if retryable
                    else "trigger_state_probe_cancelled_max_attempts"
                ),
            )
            raise
        except Exception:
            target_state = None
        state = str(getattr(target_state, "state", "unknown") or "unknown")
        if state == "absent":
            self._transition_with_prefix(
                delivery,
                "failed",
                known_message_ids=tuple(known_message_ids),
                error_code="target_revoked",
                error="reply_target_revoked_before_send",
            )
            return FeishuDeliveryOutcome(
                "failed", "target_revoked", "reply_target_revoked_before_send"
            )
        if state != "exists":
            next_remote_failures = remote_failures + 1
            if next_remote_failures < self.max_attempts:
                self._transition_with_prefix(
                    delivery,
                    "retry",
                    known_message_ids=tuple(known_message_ids),
                    available_at=self._retry_at(next_remote_failures),
                    remote_failures=next_remote_failures,
                    error_code=TARGET_PROBE_UNKNOWN_ERROR_CODE,
                    error="reply_target_state_unknown",
                )
                return FeishuDeliveryOutcome(
                    "retry",
                    TARGET_PROBE_UNKNOWN_ERROR_CODE,
                    "reply_target_state_unknown",
                )
            self._transition_with_prefix(
                delivery,
                "failed",
                known_message_ids=tuple(known_message_ids),
                remote_failures=next_remote_failures,
                error_code=TARGET_PROBE_UNKNOWN_ERROR_CODE,
                error="reply_target_state_unknown_max_attempts",
            )
            return FeishuDeliveryOutcome(
                "failed",
                TARGET_PROBE_UNKNOWN_ERROR_CODE,
                "reply_target_state_unknown_max_attempts",
            )
        first_remote_call = True
        for ordinal in range(len(known_message_ids), len(chunks)):
            chunk = chunks[ordinal]
            if not self._reply_mentions_currently_authorized(delivery):
                return self._fail_closed_revoked_mentions(
                    delivery,
                    known_message_ids=tuple(known_message_ids),
                    request_log_id=last_request_log_id,
                )
            if not self._rate_slot():
                try:
                    self._transition_with_prefix(
                        delivery,
                        "retry",
                        known_message_ids=tuple(known_message_ids),
                        available_at=self._retry_at(
                            delivery.attempts, rate_limited=True
                        ),
                        error_code="rate_limited",
                        error="local_rate_limit",
                    )
                except ValueError:
                    superseded = self._superseded_outcome(delivery)
                    if superseded is not None:
                        return superseded
                    raise
                return FeishuDeliveryOutcome(
                    "retry",
                    "rate_limited",
                    "local_rate_limit",
                    message_id=(known_message_ids[0] if known_message_ids else ""),
                )
            chunk_key = delivery_chunk_idempotency_key(
                delivery_key=delivery.idempotency_key,
                ordinal=ordinal,
                expected_chunks=delivery.expected_chunks,
                chunk_plan_sha256=delivery.chunk_plan_sha256,
                payload_sha256=delivery.payload_sha256,
            )
            mutation_at = self.now().astimezone(timezone.utc).isoformat()
            if first_remote_call:
                try:
                    fenced = self.store.begin_feishu_delivery_mutation(
                        delivery.id,
                        app_id=delivery.app_id,
                        lease_token=delivery.lease_token,
                        now=mutation_at,
                    )
                except ValueError as exc:
                    if "remote mutation already started" not in str(exc):
                        raise
                    self._transition_with_prefix(
                        delivery,
                        "send_unknown",
                        known_message_ids=tuple(known_message_ids),
                        request_log_id=last_request_log_id,
                        error_code="unknown",
                        error="remote_mutation_fence_already_started",
                    )
                    return FeishuDeliveryOutcome(
                        "send_unknown",
                        "unknown",
                        "remote_mutation_fence_already_started",
                        message_id=(
                            known_message_ids[0] if known_message_ids else ""
                        ),
                        request_log_id=last_request_log_id,
                    )
                if fenced is None:
                    return FeishuDeliveryOutcome(
                        "rejected",
                        "superseded",
                        "superseded_by_newer_feishu_trigger",
                    )
                delivery = fenced
                first_remote_call = False
            else:
                self.store.heartbeat_feishu_delivery_send(
                    delivery.id,
                    app_id=delivery.app_id,
                    lease_token=delivery.lease_token,
                    now=mutation_at,
                )
            try:
                result = await asyncio.wait_for(
                    self.client.send_reply_chunk(
                        delivery,
                        text=chunk,
                        ordinal=ordinal,
                        expected_chunks=delivery.expected_chunks,
                        idempotency_key=chunk_key,
                    ),
                    timeout=self.send_timeout_seconds,
                )
            except asyncio.CancelledError:
                self._transition_with_prefix(
                    delivery,
                    "send_unknown",
                    known_message_ids=tuple(known_message_ids),
                    request_log_id=last_request_log_id,
                    error_code="send_timeout",
                    error="feishu_send_cancelled_result_unknown",
                )
                raise
            except Exception as exc:
                code = error_code(exc)
                error = f"feishu_send_exception:{code}:{type(exc).__name__}"
                return self._finish_failure(
                    delivery,
                    FeishuSendResult(False, error_code=code),
                    remote_failures=remote_failures,
                    known_message_ids=tuple(known_message_ids),
                    failure_error=error,
                )

            last_request_log_id = result.request_log_id or last_request_log_id
            # A provider ID attached to a failed/contradictory result is only
            # unconfirmed evidence.  Persisting it as an active receipt would
            # skip that ordinal after a verified-not-sent requeue.  Only an
            # unequivocally successful one-ID result advances the prefix.
            if result.success and len(result.message_ids) == 1:
                message_id = self._record_chunk_result(
                    delivery, ordinal=ordinal, result=result
                )
                known_message_ids.append(message_id)
                remote_failures = 0
            elif len(result.message_ids) > 1:
                self._transition_with_prefix(
                    delivery,
                    "send_unknown",
                    known_message_ids=tuple(known_message_ids),
                    request_log_id=last_request_log_id,
                    error_code="unknown",
                    error="sdk_returned_unplanned_wire_chunks",
                )
                return FeishuDeliveryOutcome(
                    "send_unknown",
                    "unknown",
                    "sdk_returned_unplanned_wire_chunks",
                    message_id=(known_message_ids[0] if known_message_ids else ""),
                    request_log_id=last_request_log_id,
                )

            if not result.success:
                return self._finish_failure(
                    delivery,
                    result,
                    remote_failures=remote_failures,
                    known_message_ids=tuple(known_message_ids),
                )
            if not result.message_ids:
                self._transition_with_prefix(
                    delivery,
                    "send_unknown",
                    known_message_ids=tuple(known_message_ids),
                    request_log_id=last_request_log_id,
                    error_code="unknown",
                    error="successful_response_missing_message_id",
                )
                return FeishuDeliveryOutcome(
                    "send_unknown",
                    "unknown",
                    "successful_response_missing_message_id",
                    message_id=(known_message_ids[0] if known_message_ids else ""),
                    request_log_id=last_request_log_id,
                )

        self._transition_with_prefix(
            delivery,
            "sent",
            known_message_ids=tuple(known_message_ids),
            request_log_id=last_request_log_id,
            remote_failures=0,
        )
        return FeishuDeliveryOutcome(
            "sent",
            message_id=known_message_ids[0],
            request_log_id=last_request_log_id,
        )

    async def process_once(self, limit: int = 10) -> int:
        """Claim rows for this client; confirm mode requires durable approval."""
        if limit <= 0 or not self.outbound_gate_open:
            return 0
        app_id = self._authenticated_app_id()
        processed = 0
        for _ in range(limit):
            recover_orphaned_sending(
                self.store,
                app_id=app_id,
                max_age_seconds=self.send_lease_stale_seconds,
                now=self.now(),
            )
            deliveries = self.store.claim_feishu_deliveries(
                1,
                statuses=("ready_to_send", "retry"),
                app_id=app_id,
                approved_only=self.send_mode == "confirm",
            )
            if not deliveries:
                break
            await self.send_claimed(deliveries[0])
            processed += 1
        return processed

    async def approve_and_send(
        self,
        delivery_id: int,
        *,
        expected_approval_hash: str,
        approved_by: str,
    ) -> FeishuDeliveryOutcome:
        """Approve durably, then send through this already-connected client."""
        if not self.outbound_gate_open:
            raise PermissionError("Feishu outbound gates are closed")
        if not isinstance(approved_by, str) or not approved_by.strip():
            raise ValueError("Feishu delivery approval requires approved_by")
        app_id = self._authenticated_app_id()
        pending = self.store.get_feishu_delivery(delivery_id)
        if pending is None:
            raise ValueError(f"Feishu delivery {delivery_id} is not sendable")
        self._require_delivery_binding(pending, require_send_lease=False)
        if expected_approval_hash != pending.approval_hash:
            raise ValueError("Feishu delivery approval hash changed")
        if not pending.approved_at:
            pending = self.store.approve_feishu_delivery(
                delivery_id,
                app_id=app_id,
                approved_by=approved_by,
                expected_approval_hash=expected_approval_hash,
            )
        delivery = self.store.claim_feishu_delivery(
            delivery_id,
            statuses=("ready_to_send", "retry"),
            app_id=app_id,
            approved_only=True,
        )
        if delivery is None:
            raise ValueError(f"Feishu delivery {delivery_id} is not sendable")
        return await self.send_claimed(delivery)

    def reject(self, delivery_id: int) -> None:
        updated = self.store.reject_feishu_delivery(
            delivery_id,
            app_id=self._authenticated_app_id(),
            error="user_rejected",
        )
        if updated.status != "rejected":
            raise ValueError(f"Feishu delivery {delivery_id} is not rejectable")


def recover_orphaned_sending(
    store,
    *,
    app_id: str = "",
    max_age_seconds: int = DEFAULT_SEND_LEASE_STALE_SECONDS,
    now: datetime | None = None,
) -> int:
    """Retry only pre-mutation crashes; quarantine every uncertain send."""
    recovered = 0
    for delivery in store.list_stale_feishu_sending(
        max_age_seconds, app_id=app_id, now=now
    ):
        try:
            receipts = store.validate_feishu_delivery_receipt_prefix(
                delivery.id, app_id=delivery.app_id
            )
        except ValueError:
            receipts = None
        try:
            message_ids = tuple(
                receipt.message_id for receipt in (receipts or ())
            )
            complete = (
                receipts is not None
                and len(message_ids) == delivery.expected_chunks
            )
            pre_mutation = (
                receipts is not None
                and not message_ids
                and not delivery.mutation_started_at
            )
            if complete:
                next_status = "sent"
                next_error_code = ""
                next_error = ""
                audit_event_type = "orphaned_receipts_completed"
            elif pre_mutation:
                next_status = "retry"
                next_error_code = "not_connected"
                next_error = "orphaned_before_remote_mutation"
                audit_event_type = "orphaned_pre_mutation_retry"
            else:
                next_status = "send_unknown"
                next_error_code = "unknown"
                next_error = "orphaned_sending_requires_review"
                audit_event_type = "orphaned_send_unknown"
            updated = store.transition_feishu_delivery(
                delivery.id,
                from_statuses=("sending",),
                to_status=next_status,
                feishu_message_id=(message_ids[0] if message_ids else ""),
                message_ids=message_ids,
                request_log_id=(
                    receipts[-1].request_log_id
                    if complete and receipts
                    else ""
                ),
                error_code=next_error_code,
                error=next_error,
                expected_lease_token=delivery.lease_token,
                actor="sender-recovery",
                audit_event_type=audit_event_type,
            )
        except ValueError:
            continue
        recovered += int(updated is not None)
    return recovered
