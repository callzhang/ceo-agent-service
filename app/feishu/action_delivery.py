"""Lease-owned sender for the closed Feishu IM action outbox."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Callable

from app.feishu.client import FeishuSendResult
from app.feishu.delivery import TARGET_PROBE_UNKNOWN_ERROR_CODE, error_code
from app.feishu.rate_limit import SlidingWindowMutationBudget


ACTION_RETRYABLE_CODES = frozenset({"rate_limited", "not_connected"})
ACTION_TERMINAL_CODES = frozenset(
    {"format_error", "permission_denied", "target_revoked"}
)
ACTION_UNCERTAIN_CODES = frozenset({"send_timeout", "unknown"})
ACTION_RECONCILIATION_EVIDENCE_KINDS = frozenset(
    {"feishu_ui", "message_lookup", "admin_audit"}
)
DEFAULT_ACTION_TIMEOUT_SECONDS = 30.0
DEFAULT_ACTION_LEASE_STALE_SECONDS = 5 * 60


def _action_error_code(exc: BaseException) -> str:
    if isinstance(exc, PermissionError):
        return "permission_denied"
    if isinstance(exc, ValueError):
        return "format_error"
    return error_code(exc)


@dataclass(frozen=True)
class FeishuMessageActionOutcome:
    status: str
    error_code: str = ""
    error: str = ""
    remote_id: str = ""
    request_log_id: str = ""


@dataclass(frozen=True)
class FeishuMessageActionReconciliationDecision:
    """Validated final-state decision for one manually verified unknown action.

    Persistence remains a store responsibility so the action row, recall
    receipt, and append-only audit event can be committed atomically.  Keeping
    the closed decision contract beside the sender prevents UI/CLI callers from
    inventing provider-specific terminal states.
    """

    final_status: str
    remote_id: str = ""
    request_log_id: str = ""
    error_code: str = ""
    verified_by: str = ""
    evidence_kind: str = ""
    audit_event_type: str = ""
    recall_receipt_status: str = ""


def _safe_reconciliation_identity(
    value: str, *, field: str, max_length: int, required: bool = False
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"Feishu action reconciliation {field} is invalid")
    normalized = value.strip()
    if required and not normalized:
        raise ValueError(f"Feishu action reconciliation requires {field}")
    if (
        len(normalized) > max_length
        or normalized != value
        or any(ord(character) < 32 for character in normalized)
    ):
        raise ValueError(f"Feishu action reconciliation {field} is invalid")
    return normalized


def plan_message_action_reconciliation(
    action,
    *,
    outcome: str,
    verified_by: str,
    evidence_kind: str,
    remote_id: str = "",
    request_log_id: str = "",
) -> FeishuMessageActionReconciliationDecision:
    """Validate deterministic evidence for an action ``result_unknown``.

    ``applied`` means the requested final effect is independently visible;
    ``not_applied`` means independent evidence proves it did not happen.  This
    function never performs a network request and never turns uncertainty into
    a retry.  A separate, reviewed requeue operation is required after a
    verified ``not_applied`` decision.
    """

    if getattr(action, "status", "") != "result_unknown":
        raise ValueError("Feishu action reconciliation requires result_unknown")
    kind = str(getattr(action, "kind", "") or "")
    if kind not in {"add_reaction", "recall_message", "handoff_notify"}:
        raise ValueError("unknown Feishu message action kind")
    if not isinstance(outcome, str):
        raise ValueError("unknown Feishu action reconciliation outcome")
    normalized_outcome = outcome.strip().lower().replace("-", "_")
    if normalized_outcome not in {"applied", "not_applied"}:
        raise ValueError("unknown Feishu action reconciliation outcome")
    reviewer = _safe_reconciliation_identity(
        verified_by, field="verified_by", max_length=128, required=True
    )
    if not isinstance(evidence_kind, str):
        raise ValueError("unknown Feishu action reconciliation evidence kind")
    evidence = evidence_kind.strip().lower()
    if evidence not in ACTION_RECONCILIATION_EVIDENCE_KINDS:
        raise ValueError("unknown Feishu action reconciliation evidence kind")
    safe_remote_id = _safe_reconciliation_identity(
        remote_id, field="remote_id", max_length=512
    )
    safe_request_log_id = _safe_reconciliation_identity(
        request_log_id, field="request_log_id", max_length=256
    )

    if normalized_outcome == "applied":
        if kind in {"add_reaction", "handoff_notify"} and not safe_remote_id:
            identifier = "reaction ID" if kind == "add_reaction" else "message ID"
            raise ValueError(
                f"verified applied {kind} requires Feishu {identifier}"
            )
        if kind == "recall_message" and safe_remote_id:
            raise ValueError("verified recall must not copy its target message ID")
        return FeishuMessageActionReconciliationDecision(
            final_status="sent",
            remote_id=safe_remote_id,
            request_log_id=safe_request_log_id,
            verified_by=reviewer,
            evidence_kind=evidence,
            audit_event_type="unknown_verified_applied",
            recall_receipt_status=(
                "recalled" if kind == "recall_message" else ""
            ),
        )

    if safe_remote_id:
        raise ValueError(
            "verified not-applied action must not include a remote identifier"
        )
    return FeishuMessageActionReconciliationDecision(
        final_status="failed",
        request_log_id=safe_request_log_id,
        error_code="verified_not_applied",
        verified_by=reviewer,
        evidence_kind=evidence,
        audit_event_type="unknown_verified_not_applied",
        recall_receipt_status=("active" if kind == "recall_message" else ""),
    )


def _normalize_handoff_target_allowlist(values) -> frozenset[str]:
    if values is None:
        return frozenset()
    if isinstance(values, str):
        raise ValueError("Feishu handoff allowlist must be a local sequence")
    normalized: set[str] = set()
    for raw in values:
        if not isinstance(raw, str):
            raise ValueError("Feishu handoff allowlist contains invalid target")
        value = raw.strip()
        suffix = value[3:] if value.startswith("ou_") else ""
        if (
            raw != value
            or not suffix
            or len(value) > 256
            or any(
                character
                not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
                for character in suffix
            )
        ):
            raise ValueError("Feishu handoff allowlist contains invalid target")
        normalized.add(value)
    if len(normalized) > 20:
        raise ValueError("Feishu handoff allowlist contains too many targets")
    return frozenset(normalized)


class FeishuMessageActionSender:
    """The only runtime component allowed to drain message action rows."""

    def __init__(
        self,
        store,
        client,
        *,
        sender_enabled: bool = False,
        live_send_allowed: bool = False,
        reactions_enabled: bool = False,
        recalls_enabled: bool = False,
        handoff_enabled: bool = False,
        handoff_target_allowlist=(),
        send_mode: str = "confirm",
        max_actions_per_minute: int = 10,
        max_attempts: int = 3,
        action_timeout_seconds: float = DEFAULT_ACTION_TIMEOUT_SECONDS,
        action_lease_stale_seconds: int = DEFAULT_ACTION_LEASE_STALE_SECONDS,
        now: Callable[[], datetime] | None = None,
        monotonic_clock: Callable[[], float] = monotonic,
        mutation_budget: SlidingWindowMutationBudget | None = None,
    ):
        if send_mode not in {"confirm", "auto"}:
            raise ValueError("Feishu action send_mode must be confirm or auto")
        if (
            max_actions_per_minute <= 0
            or max_attempts <= 0
            or action_timeout_seconds <= 0
            or action_lease_stale_seconds <= action_timeout_seconds
        ):
            raise ValueError("Feishu message action limits must be positive")
        self.store = store
        self.client = client
        self.sender_enabled = sender_enabled
        self.live_send_allowed = live_send_allowed
        self.reactions_enabled = reactions_enabled
        self.recalls_enabled = recalls_enabled
        self.handoff_enabled = handoff_enabled
        self.handoff_target_allowlist = _normalize_handoff_target_allowlist(
            handoff_target_allowlist
        )
        self.send_mode = send_mode
        self.max_actions_per_minute = max_actions_per_minute
        self.max_attempts = max_attempts
        self.action_timeout_seconds = action_timeout_seconds
        self.action_lease_stale_seconds = action_lease_stale_seconds
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.monotonic_clock = monotonic_clock
        self.mutation_budget = mutation_budget or SlidingWindowMutationBudget(
            max_actions_per_minute,
            monotonic_clock=monotonic_clock,
        )
        # Kept as a compatibility alias for focused tests and local diagnostics.
        self._sent_times = self.mutation_budget._mutation_times

    @property
    def outbound_gate_open(self) -> bool:
        return self.sender_enabled and self.live_send_allowed

    @property
    def enabled_kinds(self) -> tuple[str, ...]:
        kinds: list[str] = []
        if self.reactions_enabled:
            kinds.append("add_reaction")
        if self.recalls_enabled:
            kinds.append("recall_message")
        if self.handoff_enabled:
            kinds.append("handoff_notify")
        return tuple(kinds)

    def _authenticated_app_id(self) -> str:
        app_id = str(getattr(self.client, "app_id", "") or "").strip()
        if not app_id:
            raise PermissionError("Feishu authenticated client App ID is unavailable")
        return app_id

    def _require_kind_gate(self, kind: str) -> None:
        if kind not in self.enabled_kinds:
            raise PermissionError(f"Feishu {kind} gate is closed")

    def _require_action_binding(self, action):
        app_id = self._authenticated_app_id()
        if action.app_id != app_id:
            raise PermissionError("Feishu message action App ID does not match client")
        if not action.lease_token:
            raise ValueError("Feishu message action has no active lease")
        self._require_kind_gate(action.kind)
        current = self.store.validate_feishu_message_action_for_send(
            action.id,
            app_id=app_id,
            lease_token=action.lease_token,
        )
        immutable_fields = (
            "reply_task_id",
            "attempt_id",
            "app_id",
            "chat_id",
            "action_key",
            "kind",
            "target_message_id",
            "target_open_id",
            "payload_json",
            "payload_sha256",
            "idempotency_key",
            "review_generation",
            "approval_hash",
            "risk",
        )
        if any(
            getattr(current, field) != getattr(action, field)
            for field in immutable_fields
        ):
            self.store.transition_feishu_message_action(
                current.id,
                from_statuses=("sending",),
                to_status="failed",
                app_id=current.app_id,
                expected_lease_token=current.lease_token,
                error_code="format_error",
                error="message_action_claim_identity_changed",
                actor="action-sender",
            )
            raise ValueError("Feishu message action identity changed")
        return current

    def _rate_slot(self) -> bool:
        return self.mutation_budget.try_acquire()

    def _retry_at(self, attempts: int, *, rate_limited: bool = False) -> str:
        seconds = 60 if rate_limited else min(300, max(5, 2 ** max(1, attempts)))
        return (
            self.now().astimezone(timezone.utc) + timedelta(seconds=seconds)
        ).isoformat()

    def _transition(self, action, status: str, **fields):
        return self.store.transition_feishu_message_action(
            action.id,
            from_statuses=("sending",),
            to_status=status,
            app_id=action.app_id,
            expected_lease_token=action.lease_token,
            **fields,
        )

    def _superseded_outcome(
        self, action
    ) -> FeishuMessageActionOutcome | None:
        current = self.store.get_feishu_message_action(action.id)
        if (
            current is not None
            and current.status == "rejected"
            and current.error_code == "superseded"
        ):
            return FeishuMessageActionOutcome(
                "rejected",
                "superseded",
                "superseded_by_newer_feishu_trigger",
            )
        return None

    async def _probe_reaction_target(
        self, action
    ) -> FeishuMessageActionOutcome | None:
        """Fail closed unless the persisted trigger still exists remotely.

        This is a read-only provider call and therefore happens before both
        the mutation budget and the durable remote-mutation fence.  An
        indeterminate probe can be retried safely; it must never be treated as
        an unknown mutation result.
        """

        try:
            target_state = await asyncio.wait_for(
                self.client.fetch_message_state(
                    action.app_id, action.target_message_id
                ),
                timeout=self.action_timeout_seconds,
            )
        except asyncio.CancelledError:
            next_remote_failures = action.remote_failures + 1
            retryable = next_remote_failures < self.max_attempts
            self._transition(
                action,
                "retry" if retryable else "failed",
                available_at=(
                    self._retry_at(next_remote_failures) if retryable else ""
                ),
                remote_failures=next_remote_failures,
                error_code=TARGET_PROBE_UNKNOWN_ERROR_CODE,
                error=(
                    "reaction_target_probe_cancelled"
                    if retryable
                    else "reaction_target_probe_cancelled_max_attempts"
                ),
            )
            raise
        except Exception:
            target_state = None

        state = str(getattr(target_state, "state", "unknown") or "unknown")
        if state == "exists":
            return None
        if state == "absent":
            error = "reaction_target_revoked_before_send"
            self._transition(
                action,
                "failed",
                error_code="target_revoked",
                error=error,
                actor="action-sender",
                audit_event_type="reaction_target_revoked",
            )
            return FeishuMessageActionOutcome(
                "failed", "target_revoked", error
            )

        next_remote_failures = action.remote_failures + 1
        retryable = next_remote_failures < self.max_attempts
        error = (
            "reaction_target_state_unknown"
            if retryable
            else "reaction_target_state_unknown_max_attempts"
        )
        self._transition(
            action,
            "retry" if retryable else "failed",
            available_at=(
                self._retry_at(next_remote_failures) if retryable else ""
            ),
            remote_failures=next_remote_failures,
            error_code=TARGET_PROBE_UNKNOWN_ERROR_CODE,
            error=error,
        )
        return FeishuMessageActionOutcome(
            "retry" if retryable else "failed",
            TARGET_PROBE_UNKNOWN_ERROR_CODE,
            error,
        )

    async def _invoke(self, action) -> FeishuSendResult:
        if action.kind == "add_reaction":
            payload = json.loads(action.payload_json)
            return await self.client.add_reaction(
                action.app_id,
                action.target_message_id,
                payload["emoji_type"],
            )
        if action.kind == "recall_message":
            return await self.client.recall_message(
                action.app_id, action.target_message_id
            )
        if action.kind == "handoff_notify":
            return await self.client.send_handoff(action)
        raise ValueError("unsupported Feishu message action kind")

    def _finish_result(
        self, action, result: FeishuSendResult
    ) -> FeishuMessageActionOutcome:
        if not result.success and (result.reaction_id or result.message_ids):
            error = "action_failed_with_remote_identifier"
            self._transition(
                action,
                "result_unknown",
                remote_failures=action.remote_failures + 1,
                request_log_id=result.request_log_id,
                error_code="unknown",
                error=error,
            )
            return FeishuMessageActionOutcome(
                "result_unknown", "unknown", error
            )
        if result.success:
            if action.kind == "add_reaction":
                if not result.reaction_id or result.message_ids:
                    self._transition(
                        action,
                        "result_unknown",
                        remote_failures=action.remote_failures + 1,
                        request_log_id=result.request_log_id,
                        error_code="unknown",
                        error="reaction_success_result_shape_invalid",
                    )
                    return FeishuMessageActionOutcome(
                        "result_unknown",
                        "unknown",
                        "reaction_success_result_shape_invalid",
                    )
                remote_id = result.reaction_id
            elif action.kind == "handoff_notify":
                if result.reaction_id or len(result.message_ids) != 1:
                    self._transition(
                        action,
                        "result_unknown",
                        remote_failures=action.remote_failures + 1,
                        request_log_id=result.request_log_id,
                        error_code="unknown",
                        error="handoff_success_result_shape_invalid",
                    )
                    return FeishuMessageActionOutcome(
                        "result_unknown",
                        "unknown",
                        "handoff_success_result_shape_invalid",
                    )
                remote_id = result.message_id
            else:
                # Recall responses do not need to echo the deleted target.  The
                # receipt ownership and status transition are committed locally.
                if result.reaction_id or result.message_ids:
                    error = "recall_success_result_shape_invalid"
                    self._transition(
                        action,
                        "result_unknown",
                        remote_failures=action.remote_failures + 1,
                        request_log_id=result.request_log_id,
                        error_code="unknown",
                        error=error,
                    )
                    return FeishuMessageActionOutcome(
                        "result_unknown", "unknown", error
                    )
                remote_id = ""
            self._transition(
                action,
                "sent",
                remote_failures=0,
                remote_id=remote_id,
                request_log_id=result.request_log_id,
            )
            return FeishuMessageActionOutcome(
                "sent",
                remote_id=remote_id,
                request_log_id=result.request_log_id,
            )

        code = result.error_code
        if code not in ACTION_RETRYABLE_CODES | ACTION_TERMINAL_CODES | ACTION_UNCERTAIN_CODES:
            code = "unknown"
        error = f"feishu_action_failed:{code}"
        next_remote_failures = action.remote_failures + 1
        if action.kind == "recall_message" and code == "target_revoked":
            self._transition(
                action,
                "sent",
                remote_failures=next_remote_failures,
                request_log_id=result.request_log_id,
                audit_event_type="already_absent",
            )
            return FeishuMessageActionOutcome(
                "sent", request_log_id=result.request_log_id
            )
        if (
            code in ACTION_RETRYABLE_CODES
            and next_remote_failures < self.max_attempts
        ):
            self._transition(
                action,
                "retry",
                available_at=self._retry_at(
                    next_remote_failures, rate_limited=code == "rate_limited"
                ),
                remote_failures=next_remote_failures,
                request_log_id=result.request_log_id,
                error_code=code,
                error=error,
            )
            return FeishuMessageActionOutcome("retry", code, error)
        if code in ACTION_RETRYABLE_CODES | ACTION_TERMINAL_CODES:
            self._transition(
                action,
                "failed",
                remote_failures=next_remote_failures,
                request_log_id=result.request_log_id,
                error_code=code,
                error=error,
            )
            return FeishuMessageActionOutcome("failed", code, error)
        self._transition(
            action,
            "result_unknown",
            remote_failures=next_remote_failures,
            request_log_id=result.request_log_id,
            error_code=code,
            error=error,
        )
        return FeishuMessageActionOutcome("result_unknown", code, error)

    async def send_claimed(self, action) -> FeishuMessageActionOutcome:
        if not self.outbound_gate_open:
            raise PermissionError("Feishu message action outbound gates are closed")
        if action.status != "sending":
            raise ValueError("Feishu message action must be atomically claimed first")
        action = self._require_action_binding(action)
        approval_required = self.send_mode == "confirm" or action.risk == "R4"
        if approval_required and not (action.approved_at and action.approved_by):
            error = "durable_approval_missing_at_send"
            self._transition(
                action,
                "failed",
                error_code="format_error",
                error=error,
                actor="action-sender",
                audit_event_type="approval_missing_at_send",
            )
            return FeishuMessageActionOutcome("failed", "format_error", error)
        if (
            action.kind == "handoff_notify"
            and action.target_open_id not in self.handoff_target_allowlist
        ):
            error = "handoff_target_no_longer_allowlisted"
            self._transition(
                action,
                "failed",
                error_code="target_revoked",
                error=error,
                actor="action-sender",
                audit_event_type="handoff_target_revoked",
            )
            return FeishuMessageActionOutcome(
                "failed", "target_revoked", error
            )
        if action.kind == "add_reaction":
            probe_outcome = await self._probe_reaction_target(action)
            if probe_outcome is not None:
                return probe_outcome
        if not self._rate_slot():
            try:
                self._transition(
                    action,
                    "retry",
                    available_at=self._retry_at(
                        action.attempts, rate_limited=True
                    ),
                    error_code="rate_limited",
                    error="local_rate_limit",
                )
            except ValueError:
                superseded = self._superseded_outcome(action)
                if superseded is not None:
                    return superseded
                raise
            return FeishuMessageActionOutcome(
                "retry", "rate_limited", "local_rate_limit"
            )
        mutation_at = self.now().astimezone(timezone.utc).isoformat()
        try:
            fenced = self.store.begin_feishu_message_action_mutation(
                action.id,
                app_id=action.app_id,
                lease_token=action.lease_token,
                now=mutation_at,
            )
        except ValueError as exc:
            if "remote mutation already started" not in str(exc):
                raise
            self._transition(
                action,
                "result_unknown",
                error_code="unknown",
                error="remote_mutation_fence_already_started",
            )
            return FeishuMessageActionOutcome(
                "result_unknown",
                "unknown",
                "remote_mutation_fence_already_started",
            )
        if fenced is None:
            return FeishuMessageActionOutcome(
                "rejected",
                "superseded",
                "superseded_by_newer_feishu_trigger",
            )
        action = fenced
        try:
            result = await asyncio.wait_for(
                self._invoke(action), timeout=self.action_timeout_seconds
            )
        except asyncio.CancelledError:
            self._transition(
                action,
                "result_unknown",
                remote_failures=action.remote_failures + 1,
                error_code="send_timeout",
                error="feishu_action_cancelled_result_unknown",
            )
            raise
        except Exception as exc:
            code = _action_error_code(exc)
            if code not in (
                ACTION_RETRYABLE_CODES | ACTION_TERMINAL_CODES | ACTION_UNCERTAIN_CODES
            ):
                code = "unknown"
            error = f"feishu_action_exception:{code}:{type(exc).__name__}"
            next_remote_failures = action.remote_failures + 1
            if action.kind == "recall_message" and code == "target_revoked":
                self._transition(
                    action,
                    "sent",
                    remote_failures=next_remote_failures,
                    audit_event_type="already_absent",
                )
                return FeishuMessageActionOutcome("sent")
            if (
                code in ACTION_RETRYABLE_CODES
                and next_remote_failures < self.max_attempts
            ):
                self._transition(
                    action,
                    "retry",
                    available_at=self._retry_at(next_remote_failures),
                    remote_failures=next_remote_failures,
                    error_code=code,
                    error=error,
                )
                return FeishuMessageActionOutcome("retry", code, error)
            if code in ACTION_RETRYABLE_CODES | ACTION_TERMINAL_CODES:
                self._transition(
                    action,
                    "failed",
                    remote_failures=next_remote_failures,
                    error_code=code,
                    error=error,
                )
                return FeishuMessageActionOutcome("failed", code, error)
            self._transition(
                action,
                "result_unknown",
                remote_failures=next_remote_failures,
                error_code=code,
                error=error,
            )
            return FeishuMessageActionOutcome("result_unknown", code, error)
        return self._finish_result(action, result)

    async def process_once(self, limit: int = 10) -> int:
        if limit <= 0 or not self.outbound_gate_open or not self.enabled_kinds:
            return 0
        app_id = self._authenticated_app_id()
        processed = 0
        for _ in range(limit):
            recover_orphaned_message_actions(
                self.store,
                app_id=app_id,
                max_age_seconds=self.action_lease_stale_seconds,
                now=self.now(),
            )
            claimed = self.store.claim_feishu_message_actions(
                1,
                app_id=app_id,
                kinds=self.enabled_kinds,
                send_mode=self.send_mode,
            )
            if not claimed:
                break
            await self.send_claimed(claimed[0])
            processed += 1
        return processed

    async def approve_and_send(
        self,
        action_id: int,
        *,
        expected_approval_hash: str,
        approved_by: str,
    ) -> FeishuMessageActionOutcome:
        if not self.outbound_gate_open:
            raise PermissionError("Feishu message action outbound gates are closed")
        if not isinstance(approved_by, str) or not approved_by.strip():
            raise ValueError(
                "Feishu message action approval requires approved_by"
            )
        app_id = self._authenticated_app_id()
        pending = self.store.get_feishu_message_action(action_id)
        if pending is None or pending.app_id != app_id:
            raise ValueError("Feishu message action is not sendable")
        self._require_kind_gate(pending.kind)
        if expected_approval_hash != pending.approval_hash:
            raise ValueError("Feishu message action approval hash changed")
        if not pending.approved_at:
            pending = self.store.approve_feishu_message_action(
                action_id,
                app_id=app_id,
                approved_by=approved_by,
                expected_approval_hash=expected_approval_hash,
            )
        action = self.store.claim_feishu_message_action(
            action_id,
            app_id=app_id,
            kinds=self.enabled_kinds,
            send_mode="confirm",
        )
        if action is None:
            raise ValueError("Feishu message action is not sendable")
        return await self.send_claimed(action)

    def reject(
        self, action_id: int, *, rejected_by: str = "local-reviewer"
    ) -> None:
        self.store.reject_feishu_message_action(
            action_id,
            app_id=self._authenticated_app_id(),
            rejected_by=rejected_by,
        )


def recover_orphaned_message_actions(
    store,
    *,
    app_id: str = "",
    max_age_seconds: int = DEFAULT_ACTION_LEASE_STALE_SECONDS,
    now: datetime | None = None,
) -> int:
    """Retry only pre-mutation crashes; quarantine every fenced orphan."""
    recovered = 0
    for action in store.list_stale_feishu_message_actions(
        max_age_seconds, app_id=app_id, now=now
    ):
        try:
            if action.mutation_started_at:
                store.transition_feishu_message_action(
                    action.id,
                    from_statuses=("sending",),
                    to_status="result_unknown",
                    app_id=action.app_id,
                    expected_lease_token=action.lease_token,
                    error_code="unknown",
                    error="orphaned_action_requires_review",
                    actor="action-recovery",
                    audit_event_type="orphaned_result_unknown",
                )
            else:
                store.transition_feishu_message_action(
                    action.id,
                    from_statuses=("sending",),
                    to_status="retry",
                    app_id=action.app_id,
                    expected_lease_token=action.lease_token,
                    error_code="not_connected",
                    error="orphaned_before_remote_mutation",
                    actor="action-recovery",
                    audit_event_type="orphaned_pre_mutation_retry",
                )
        except (ValueError, PermissionError):
            continue
        recovered += 1
    return recovered
