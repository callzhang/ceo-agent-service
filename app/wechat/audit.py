"""Independent, model-only review of a persisted WeChat delivery."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.agent_result import ResultParseError, parse_typed_agent_result
from app.agent_runtime_contracts import ROLE_BOUND_AGENT_TOOLS
from app.agent_runtime_router import (
    CodexCommandFactory,
    RoutedCodexExecutionError,
    RoutedResultCodec,
    RoutedResultValidationError,
    RoutedResultValidationRetry,
)
from app.store import AgentRole


class WechatAuditDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    outcome: Literal["approve", "reject"]
    candidate_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    summary: str = Field(min_length=1)


def _value(record, name: str):
    return record[name] if hasattr(record, "keys") else getattr(record, name)


def wechat_audit_candidate(task, delivery) -> dict[str, object]:
    """The frozen source identity and exact prepared effect being reviewed."""
    return {
        "delivery_id": _value(delivery, "id"),
        "task_id": _value(task, "id"),
        "execution_generation": _value(delivery, "execution_generation"),
        "account_id": _value(delivery, "account_id"),
        "target_type": _value(delivery, "target_type"),
        "target_id": _value(delivery, "target_id"),
        "conversation_id": _value(delivery, "conversation_id"),
        "conversation_title": _value(task, "conversation_title"),
        "reply_text": _value(delivery, "reply_text"),
        "trigger_message_id": _value(task, "trigger_message_id"),
        "trigger_sender": _value(task, "trigger_sender"),
        "trigger_text": _value(task, "trigger_text"),
    }


def wechat_audit_digest(candidate: dict[str, object]) -> str:
    body = json.dumps(candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class WechatDeliveryAuditor:
    """Persist one independent Audit run, then bind its result to the delivery."""

    def __init__(self, store, routed_execution, reader, account):
        self.store = store
        self.routed_execution = routed_execution
        self.reader = reader
        self.account = account

    def review(self, delivery) -> bool:
        task = self.store.get_reply_task(delivery.task_id)
        if task is None or task.execution_generation != delivery.execution_generation:
            return False
        candidate = wechat_audit_candidate(task, delivery)
        digest = wechat_audit_digest(candidate)
        if self.store.wechat_delivery_review_approved(delivery.id):
            return True
        runs = self.store.list_agent_runs_for_task_generation(
            task.id, task.execution_generation, load_events=False
        )
        consumers = [run for run in runs if run.role is AgentRole.CONSUMER
                     and run.status == "completed"]
        if not consumers:
            return False
        parent = max(consumers, key=lambda run: run.id)
        operation_id = f"wechat-delivery:{delivery.id}"
        audits = [run for run in runs if run.role is AgentRole.AUDIT
                  and run.parent_agent_run_id == parent.id
                  and run.operation_id == operation_id]
        for run in reversed(audits):
            if run.status == "completed":
                completed = self.store.get_agent_run(run.id)
                if completed is None or not completed.final_result_json:
                    return False
                decision = WechatAuditDecision.model_validate_json(completed.final_result_json)
                if decision.candidate_digest != digest:
                    self.store.set_wechat_delivery_status(
                        delivery.id, "failed",
                        error="wechat_audit_candidate_changed",
                        pre_action_failure=True,
                    )
                    return False
                self.store.record_wechat_delivery_review(delivery.id, run.id, decision)
                return decision.outcome == "approve"
            if run.status == "running":
                break
        latest = max(audits, key=lambda run: run.id) if audits else None
        failures = [run for run in audits if run.status == "failed"]
        if len(failures) >= 3:
            self.store.set_wechat_delivery_status(
                delivery.id, "failed", error="wechat_audit_unavailable",
                pre_action_failure=True,
            )
            return False
        if failures and (latest is None or latest.status != "running"):
            last = max(failures, key=lambda run: run.id)
            if last.completed_at:
                ended = datetime.fromisoformat(last.completed_at.replace("Z", "+00:00"))
                if ended.tzinfo is None:
                    ended = ended.replace(tzinfo=timezone.utc)
                if (datetime.now(timezone.utc) - ended).total_seconds() < 60:
                    return False
        try:
            context = self.reader.read_messages(
                self.account, conversation_id=delivery.conversation_id,
                conversation_type=delivery.target_type, limit=20,
            )
        except Exception:
            return False
        owner = f"wechat-audit:{delivery.id}:{uuid4().hex}"
        turn_attempt = (
            latest.turn_attempt if latest is not None and latest.status == "running"
            else len(audits)
        )
        claim = self.store.claim_agent_run(
            task.id, task.execution_generation, role=AgentRole.AUDIT,
            proposal_revision=0, turn_attempt=turn_attempt,
            parent_agent_run_id=parent.id, operation_id=operation_id,
            owner=owner, lease_seconds=1800,
        )
        if not claim.claimed:
            return False
        try:
            prompt = json.dumps({
                "current_time": datetime.now().astimezone().isoformat(),
                "candidate": candidate,
                "candidate_digest": digest,
                "recent_messages": [
                    {"message_id": message.message_id,
                     "sender": message.sender_display_name,
                     "sent_at": message.sent_at, "text": message.text}
                    for message in context
                ],
            }, ensure_ascii=False)

            def parse(raw: str) -> str:
                try:
                    result = parse_typed_agent_result(raw, WechatAuditDecision)
                    if result.candidate_digest != digest:
                        raise ValueError("WeChat Audit candidate digest mismatch")
                except (ResultParseError, ValueError) as exc:
                    raise RoutedResultValidationError(
                        "invalid WeChat Audit result", raw_output=raw
                    ) from exc
                return result.model_dump_json()

            result = self.routed_execution.execute(
                workload_kind="agent_run", workload_key=str(claim.run.id),
                prompt=prompt,
                command_factory=CodexCommandFactory.standard(
                    developer_instructions=(
                        "Independently review the proposed WeChat message against the original "
                        "request and recent conversation. Check recipient, content, facts, and timing. "
                        "The candidate reply_text is the exact final body after the service appends "
                        "its required signature; distinguish that service-owned postfix from the "
                        "Consumer's message when assessing defects. "
                        "Return only JSON with outcome (approve or reject), candidate_digest, "
                        "and a concise summary. Do not send anything."
                    ),
                    model_only=True,
                ),
                parser=parse,
                result_codec=RoutedResultCodec.text(schema_id="wechat_audit.v1"),
                required_capabilities=frozenset({ROLE_BOUND_AGENT_TOOLS}),
                result_validation_retry=RoutedResultValidationRetry.exactly_once(
                    correction_instructions="Return the required JSON for the same candidate digest."
                ),
            )
            decision = WechatAuditDecision.model_validate_json(result.value)
            self.store.complete_agent_run(
                claim.run.id, decision.model_dump(mode="json"), owner=owner,
                transcript_end_line=result.transcript_end,
            )
        except RoutedCodexExecutionError as exc:
            if exc.retryable_external_dependency or exc.runtime_unavailable:
                # The router keeps its failed attempt and native session under
                # this AgentRun ID. Reclaim the same run after a pause so it can
                # resume, including a correction turn interrupted by 429.
                self.store.renew_agent_run_lease(
                    claim.run.id, owner=owner, lease_seconds=60,
                )
                return False
            self.store.fail_agent_run(
                claim.run.id,
                {"code": "wechat_audit_failed", "detail": exc.code},
                owner=owner,
            )
            return False
        except Exception as exc:
            self.store.fail_agent_run(
                claim.run.id,
                {"code": "wechat_audit_failed", "detail": type(exc).__name__},
                owner=owner,
            )
            return False
        self.store.record_wechat_delivery_review(delivery.id, claim.run.id, decision)
        return decision.outcome == "approve"
