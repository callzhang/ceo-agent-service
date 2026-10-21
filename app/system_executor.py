"""Execute only the exact persisted plan approved for the current task generation."""

import json
from dataclasses import dataclass
from typing import Literal, Mapping, Protocol
from uuid import uuid4

from app.agent_contracts import (
    AuditExternalResult,
    ConsumerAgentResult,
    ConsumerProposal,
    ProposedAction,
    SystemExecutionResult,
    parse_persisted_consumer_result,
)
from app.external_action_identity import expected_external_action
from app.reviewed_candidates import candidate_digest
from app.model_action_schema import candidate_option


@dataclass(frozen=True)
class ActionOutcome:
    status: Literal["verified", "confirmed_no_effect", "uncertain", "failed", "business_state_changed"]
    provider_result: dict[str, object]


class ActionHandler(Protocol):
    def dispatch(
        self, action: ProposedAction, *, action_key: str, candidate: dict[str, object]
    ) -> ActionOutcome: ...

    def reconcile(
        self, action: ProposedAction, *, action_key: str, candidate: dict[str, object]
    ) -> ActionOutcome | None: ...


class SystemExecutor:
    def __init__(
        self,
        store: object,
        handlers: Mapping[tuple[str, str], ActionHandler] | None = None,
        *,
        dws: object | None = None,
        owner: str | None = None,
        dry_run: bool = False,
        lease_seconds: int = 300,
    ) -> None:
        if (owner is not None and not owner.strip()) or lease_seconds <= 0:
            raise ValueError("executor needs a positive lease and owner")
        self.store = store
        self.source_client = dws
        if handlers is None:
            from app.system_action_handlers import native_dws_handlers

            handlers = native_dws_handlers(store, dws) if dws is not None else {}
        self.handlers = handlers
        self.owner = owner or f"system-executor:{uuid4()}"
        self.dry_run = dry_run
        self.lease_seconds = lease_seconds

    @staticmethod
    def _failure(code: str, summary: str, keys: list[str] | None = None, *, retryable: bool = False, source_code: str = "", source: str = "", authorization_required: bool = False, session_continuable: bool = False) -> SystemExecutionResult:
        return SystemExecutionResult(
            outcome="failed", summary=summary,
            error={"code": code, "retryable": retryable,
                   "authorization_required": authorization_required,
                   "stage": "system_execution", "source_code": source_code,
                   "source": source, "session_continuable": session_continuable},
            completed_action_keys=tuple(keys or ()),
        )

    def _prior_action_matches(self, receipt: dict[str, object], action: ProposedAction) -> bool:
        action_key = receipt.get("external_action_key")
        source = self.store.get_verified_action_source(action_key) if isinstance(action_key, str) else None
        actual = action.model_dump(include={"capability", "operation", "target", "payload", "effect"}, mode="json")
        if source is not None:
            prior_candidate = json.loads(source["candidate_json"])
            branch = (candidate_option(source["candidate_json"], source["option_key"])
                      if source["selection_id"] is not None else None)
            plan = branch.get("plan") if branch else prior_candidate.get("proposal")
            actions = plan.get("actions") if isinstance(plan, dict) else None
            index = source["action_index"]
            if not isinstance(actions, list) or not isinstance(index, int) or index >= len(actions):
                return False
            old = ProposedAction.model_validate(actions[index])
            return old.action_identity == action.action_identity and old.model_dump(
                include={"capability", "operation", "target", "payload", "effect"}, mode="json"
            ) == actual
        run_id = receipt.get("first_agent_run_id")
        adopted = self.store.adopted_candidate_for_consumer_run(run_id) if run_id is not None else None
        if adopted is None:
            return False
        try:
            prior = parse_persisted_consumer_result(adopted["candidate_json"])
        except ValueError:
            return False
        plans = ([prior.proposal] if prior.proposal is not None else []) + [
            option.plan for option in prior.decision_options if option.plan is not None
        ]
        prior_actions = [
            old.model_dump(include={"capability", "operation", "target", "payload", "effect"}, mode="json")
            for plan in plans for old in plan.actions
            if old.action_identity == action.action_identity
        ]
        # The old receipt points at a Consumer run, not at one option. If that
        # run offered materially different alternatives under this identity,
        # the ledger cannot prove which alternative was actually dispatched.
        return bool(prior_actions) and all(old == actual for old in prior_actions)

    def _project_completed_message(
        self, action: ProposedAction, action_key: str, row: dict[str, object], task: object
    ) -> None:
        if action.capability != "dingtalk-chat" or action.operation not in {
            "send_message", "send_group_message", "send_direct_message", "reply_to_message",
        }:
            return
        receipt = self.store.get_candidate_external_action(action_key)
        if receipt is None:
            raise ValueError("verified DingTalk action has no durable receipt")
        body = action.payload.get("content", action.payload.get("text", action.payload.get("reply_text")))
        if not isinstance(body, str) or not body:
            raise ValueError("reviewed DingTalk message body is missing")
        self.store.record_completed_agent_message_delivery(
            agent_run_id=row["audit_run_id"], external_action_key=action_key,
            business_object_key=task.business_object_key,
            action_identity=action.action_identity, operation=action.operation,
            target_identifiers=action.target,
            conversation_id=task.conversation_id,
            trigger_message_id=task.trigger_message_id,
            reply_text=body,
            provider_result=json.loads(receipt["provider_result_json"]),
        )
        if self.source_client is not None:
            from app.processing_reaction import ProcessingReaction

            ProcessingReaction(self.store, self.source_client).finish(
                task.conversation_id, task.trigger_message_id,
            )

    def execute_consumer_permission_request(
        self, task: object, candidate_id: int, consumer_result: ConsumerAgentResult,
        *, permission_budget: object, now: object,
    ) -> SystemExecutionResult:
        """Execute the narrowly typed Consumer request_access purpose action.

        This is not an OA approval proposal: it only requests read access to a
        named file. It uses the Consumer candidate and existing OA comment
        handler, while the purpose budget stores the request's idempotency and
        provider readback state. OA approve/reject/return still use execute().
        """
        output = consumer_result.purpose_output or {}
        if output.get("purpose") != "oa" or output.get("action") != "request_access":
            return self._failure("permission_request_contract_invalid", "Consumer did not return an OA access request")
        target = output.get("target") or {}
        file_id = target.get("file_id")
        process_id = target.get("process_instance_id")
        if not isinstance(file_id, str) or not file_id.strip() or not isinstance(process_id, str) or not process_id.strip():
            return self._failure("permission_request_target_invalid", "Permission request needs an exact process and file")
        row = self.store.get_review_candidate(candidate_id)
        if (row is None or row["task_id"] != task.id
                or row["execution_generation"] != task.execution_generation
                or row["consumer_run_id"] is None):
            return self._failure("permission_request_candidate_invalid", "Permission request candidate is stale")
        candidate = parse_persisted_consumer_result(row["candidate_json"])
        if candidate.purpose_output != consumer_result.purpose_output or candidate.proposal is None or len(candidate.proposal.actions) != 1:
            return self._failure("permission_request_candidate_mismatch", "Permission request differs from the persisted Consumer result")
        action = candidate.proposal.actions[0]
        if (action.capability != "dingtalk-oa" or action.operation != "comment"
                or action.target.get("process_instance_id") != process_id
                or action.target.get("file_id") != file_id):
            return self._failure("permission_request_action_invalid", "Only a file-scoped OA access comment can use this path")
        handler = self.handlers.get(("dingtalk-oa", "comment"))
        if handler is None:
            return self._failure("permission_request_handler_unavailable", "OA comment handler is unavailable")
        identity = expected_external_action(action, action_index=0,
                                           business_object_key=(task.business_object_key
                                               or f"reply-task:{task.id}"))
        action_key = str(identity["external_action_key"])
        try:
            request, created = permission_budget.begin_access_request(
                file_id=file_id, action_key=action_key, candidate_id=candidate_id, now=now,
            )
        except ValueError as exc:
            return self._failure("permission_request_duplicate", str(exc))
        if request["status"] == "verified":
            return SystemExecutionResult(outcome="executed", summary="Read-only file access request was already verified",
                completed_action_keys=(action_key,))
        if request["status"] == "failed":
            return self._failure("permission_request_previously_failed", "The exact permission request already failed")
        attempt = {"status": "uncertain", "external_action_key": action_key,
                   "created_at": request["created_at"]}
        try:
            if created:
                outcome = handler.dispatch(action, action_key=action_key,
                    candidate={**row, "action_attempt": attempt})
            else:
                outcome = handler.reconcile(action, action_key=action_key,
                    candidate={**row, "action_attempt": attempt})
                if outcome is None:
                    outcome = ActionOutcome("uncertain", {"reason": "permission_request_readback_inconclusive"})
        except Exception as exc:
            outcome = ActionOutcome("uncertain", {"reason": "permission_request_provider_exception",
                "exception_type": type(exc).__name__})
        state = outcome.status
        permission_budget.finish_access_request(
            file_id=file_id, action_key=action_key,
            status="verified" if state == "verified" else "failed" if state == "failed" else "uncertain",
            result=outcome.provider_result, now=now,
        )
        if state != "verified":
            return self._failure("permission_request_" + state,
                "Read-only access request was not verified", retryable=state == "uncertain")
        return SystemExecutionResult(outcome="executed", summary="Read-only file access request verified",
            completed_action_keys=(action_key,))

    def _check_reviewed_sources(
        self, candidate: ConsumerAgentResult, context: object | None,
        plan: ConsumerProposal, execution_id: int, candidate_id: int,
    ) -> SystemExecutionResult | None:
        if not candidate.source_bindings:
            return None
        from app.reviewed_sources import ReviewedSourceReadError, changed_candidate_sources

        verified = [
            item for item in self.store.list_candidate_action_attempts(execution_id)
            if item["status"] == "verified"
        ]
        receipt_refs = [
            {"action_identity": plan.actions[item["action_index"]].action_identity,
             "external_action_key": item["external_action_key"]}
            for item in verified
        ]
        completed = tuple(ref["action_identity"] for ref in receipt_refs)
        try:
            changes = changed_candidate_sources(
                candidate, context, self.source_client, check_provider=True,
            )
        except Exception as exc:
            detail = (exc if isinstance(exc, ReviewedSourceReadError)
                      else ReviewedSourceReadError(exc)).error
            result = self._failure(
                "authorization_required" if detail.authorization_required else "reviewed_source_unavailable",
                "Reviewed source could not be reread", list(completed),
                retryable=detail.retryable, source_code=detail.source_code,
                source=detail.source,
                authorization_required=detail.authorization_required,
                session_continuable=detail.session_continuable,
            )
            self.store.finish_candidate_execution(
                execution_id, self.owner,
                "retry" if detail.retryable else "failed", result,
            )
            return result
        if not changes:
            return None
        result = SystemExecutionResult(
            outcome="failed", summary="Reviewed source facts changed before dispatch",
            error={"code": "business_state_changed", "retryable": False,
                   "authorization_required": False},
            external_result=AuditExternalResult(
                operation_id=f"reviewed-source:{candidate_id}",
                live_result_reference={"evidence": changes,
                                       "completed_actions": receipt_refs},
            ),
            completed_action_keys=completed,
        )
        self.store.finish_candidate_execution(
            execution_id, self.owner, "failed", result,
            invalidate_reason="business_state_changed",
        )
        return result

    def execute(
        self, task: object, candidate_id: int, review_id: int, *,
        context: object | None = None, dry_run: bool = False,
        reconciliation_only: bool = False,
    ) -> SystemExecutionResult:
        row = self.store.current_reviewed_candidate(task.id, task.execution_generation)
        if row is None or row["execution_generation"] != task.execution_generation or row["id"] != candidate_id or row["review_id"] != review_id:
            return self._failure("approved_candidate_missing", "No current approved candidate")
        candidate = parse_persisted_consumer_result(row["candidate_json"])
        if candidate_digest(candidate) != row["candidate_digest"]:
            return self._failure("candidate_digest_mismatch", "Stored candidate content changed")
        if candidate.outcome.value == "needs_human":
            if row["selection_id"] is None:
                return self._failure("decision_not_selected", "A reviewed choice is still required")
            branch = candidate_option(row["candidate_json"], row["option_key"])
            option = next((item for item in candidate.decision_options if item.key == branch.get("key")), None)
            if option is None or option.model_dump(mode="json") != branch:
                return self._failure("selection_mismatch", "Selected branch differs from reviewed candidate")
            plan = option.plan
            stop_reason = option.reason if option.terminal_outcome == "skipped" else None
        else:
            plan = candidate.proposal
            stop_reason = "No action is required" if candidate.outcome.value == "no_action" else None
        if dry_run or self.dry_run:
            return SystemExecutionResult(outcome="dry_run", summary="Approved plan inspected without dispatch")
        if reconciliation_only:
            existing = self.store.get_candidate_execution(candidate_id)
            attempts = self.store.list_candidate_action_attempts(existing["id"]) if existing else []
            if (existing is None or existing["status"] != "uncertain"
                or not isinstance(plan, ConsumerProposal) or not plan.actions
                or len(attempts) != len(plan.actions)
                or any(item["action_index"] != index or item["status"] not in ("uncertain", "dispatched", "verified")
                    for index, item in enumerate(attempts))
                or any(action.capability != "dingtalk-oa" or action.operation != "revert_task"
                    for action in plan.actions)):
                return self._failure("reconciliation_precondition_failed",
                    "Only a persisted uncertain OA return can be reconciled without dispatch")
        current_task = self.store.get_reply_task(task.id)
        if current_task is None or current_task.execution_generation != task.execution_generation:
            return self._failure("task_generation_mismatch", "Task generation changed")
        try:
            execution = self.store.claim_candidate_execution(candidate_id, review_id, self.owner, self.lease_seconds)
        except ValueError as exc:
            if str(exc) == "historical_runtime_risk_refusal":
                return self._failure("provider_risk_rejected", "The provider previously refused this operation")
            raise
        if execution is None:
            existing = self.store.get_candidate_execution(row["id"])
            if existing is not None and existing["status"] in ("done", "skipped"):
                return SystemExecutionResult.model_validate_json(existing["result_json"])
            return self._failure("execution_claim_unavailable", "Execution is already claimed or invalidated", retryable=existing is not None and existing["status"] in ("running", "retry", "uncertain"))
        execution_id = execution["id"]
        if stop_reason is not None:
            result = SystemExecutionResult(outcome="skipped", summary=stop_reason)
            self.store.finish_candidate_execution(execution_id, self.owner, "skipped", result)
            return result
        if not isinstance(plan, ConsumerProposal):
            result = self._failure("executable_plan_missing", "No executable plan is bound")
            self.store.finish_candidate_execution(execution_id, self.owner, "failed", result)
            return result
        completed: list[str] = []
        receipt_refs: list[dict[str, str]] = []
        for index, action in enumerate(plan.actions):
            self.store.renew_candidate_execution_claim(execution_id, self.owner, self.lease_seconds)
            attempt_status = next((item["status"] for item in self.store.list_candidate_action_attempts(execution_id)
                                   if item["action_index"] == index), None)
            if reconciliation_only and attempt_status not in ("uncertain", "dispatched", "verified"):
                result = self._failure("reconciliation_attempt_missing",
                    "Persisted OA return attempt is missing", completed)
                self.store.finish_candidate_execution(execution_id, self.owner, "failed", result)
                return result
            reconcile_existing = attempt_status in ("uncertain", "dispatched")
            if attempt_status != "verified" and not reconcile_existing:
                source_result = self._check_reviewed_sources(candidate, context, plan, execution_id, candidate_id)
                if source_result is not None:
                    return source_result
            handler = self.handlers.get((action.capability, action.operation))
            if handler is None:
                result = self._failure("unsupported_reviewed_action", f"Unsupported action: {action.capability}/{action.operation}", completed)
                self.store.finish_candidate_execution(execution_id, self.owner, "failed", result)
                return result
            identity = expected_external_action(action, action_index=index, business_object_key=current_task.business_object_key)
            action_key = str(identity["external_action_key"])
            prior_receipt = self.store.get_candidate_external_action(action_key)
            if prior_receipt is not None and not self._prior_action_matches(prior_receipt, action):
                result = self._failure("completed_action_content_conflict", "A completed action has different approved content", completed)
                self.store.finish_candidate_execution(execution_id, self.owner, "failed", result)
                return result
            prior_attempt_reader = getattr(self.store, "get_prior_candidate_action_attempt", None)
            prior_attempt = prior_attempt_reader(action_key, execution_id) if callable(prior_attempt_reader) else None
            if prior_attempt is not None and prior_attempt.get("status") == "failed":
                prior_result = json.loads(prior_attempt.get("result_json", "{}"))
                if prior_result.get("reason") == "reviewed_message_preparation_mismatch":
                    try:
                        reconciliation = handler.reconcile(
                            action, action_key=action_key,
                            candidate={**row, "action_attempt": prior_attempt},
                        )
                    except Exception as exc:
                        reconciliation = None
                        reconciliation_error = type(exc).__name__
                    else:
                        reconciliation_error = ""
                    if reconciliation is None or reconciliation.status not in ("verified", "confirmed_no_effect"):
                        result = self._failure(
                            "external_action_reconciliation_incomplete",
                            "Provider readback did not establish the prior message outcome",
                            completed, retryable=True,
                            source_code=reconciliation_error,
                        )
                        self.store.finish_candidate_execution(execution_id, self.owner, "failed", result)
                        return result
                    self.store.record_prior_candidate_action_reconciliation(
                        execution_id, self.owner, index, action_key,
                        reconciliation.status, reconciliation.provider_result,
                    )
            attempt = self.store.begin_candidate_action(execution_id, self.owner, index, action_key)
            if attempt["status"] == "verified":
                self._project_completed_message(action, action_key, row, current_task)
                completed.append(action.action_identity)
                receipt_refs.append({"action_identity": action.action_identity, "external_action_key": action_key})
                continue
            try:
                if reconciliation_only and attempt["status"] != "verified":
                    outcome = handler.reconcile(
                        action, action_key=action_key,
                        candidate={**row, "action_attempt": attempt},
                    )
                    if outcome is None:
                        outcome = ActionOutcome("uncertain", {"reason": "reconciliation_inconclusive"})
                elif attempt["status"] == "uncertain" or (
                    attempt["status"] == "dispatched" and attempt_status in ("uncertain", "dispatched")
                ):
                    outcome = handler.reconcile(
                        action, action_key=action_key,
                        candidate={**row, "action_attempt": attempt},
                    )
                    if outcome is None:
                        outcome = ActionOutcome("uncertain", {"reason": "reconciliation_inconclusive"})
                elif attempt["status"] == "dispatched":
                    if attempt_status in ("verified", "uncertain"):
                        source_result = self._check_reviewed_sources(candidate, context, plan, execution_id, candidate_id)
                        if source_result is not None:
                            return source_result
                    outcome = handler.dispatch(action, action_key=action_key, candidate={**row, "action_attempt": attempt})
                else:
                    outcome = ActionOutcome("failed", {"reason": "prior_action_failure"})
            except Exception as exc:
                source_code = getattr(exc, "code", "")
                source = getattr(exc, "server_key", "")
                prior_result = json.loads(attempt.get("result_json", "{}"))
                outcome = ActionOutcome("uncertain", {**prior_result,
                    "reason": "provider_exception", "exception_type": type(exc).__name__,
                    "source_code": str(source_code) if source_code else "",
                    "source": str(source) if source else "",
                })
            if outcome.status == "verified":
                self.store.record_candidate_external_action(
                    execution_id, self.owner, index, action_key, action.operation,
                    action.target, outcome.provider_result,
                )
                self._project_completed_message(action, action_key, row, current_task)
                completed.append(action.action_identity)
                receipt_refs.append({"action_identity": action.action_identity, "external_action_key": action_key})
                continue
            if outcome.status == "business_state_changed":
                self.store.record_candidate_action_outcome(
                    execution_id, self.owner, index, "failed", outcome.provider_result,
                )
                result = SystemExecutionResult(
                    outcome="failed",
                    summary=f"Reviewed action {action.action_identity} no longer matches provider business state",
                    error={"code": "business_state_changed", "retryable": False, "authorization_required": False},
                    external_result=AuditExternalResult(
                        operation_id=action_key,
                        live_result_reference={
                            "action_identity": action.action_identity,
                            "action_index": index,
                            "evidence": outcome.provider_result,
                            "completed_actions": receipt_refs,
                        },
                    ),
                    completed_action_keys=tuple(completed),
                )
                self.store.finish_candidate_execution(
                    execution_id, self.owner, "failed", result,
                    invalidate_reason="business_state_changed",
                )
                return result
            self.store.record_candidate_action_outcome(execution_id, self.owner, index, outcome.status, outcome.provider_result)
            if outcome.status == "confirmed_no_effect":
                code, status = "external_action_no_effect", "retry"
            elif outcome.status == "uncertain":
                code, status = "external_action_uncertain", "uncertain"
            else:
                code, status = "external_action_failed", "failed"
            result = self._failure(
                code, f"Action {action.action_identity} was not verified", completed,
                retryable=status in ("retry", "uncertain"),
                source_code=str(outcome.provider_result.get("source_code") or ""),
                source=str(outcome.provider_result.get("source") or ""),
            )
            self.store.finish_candidate_execution(execution_id, self.owner, status, result)
            return result
        result = SystemExecutionResult(
            outcome="executed", summary="All approved actions verified",
            external_result=AuditExternalResult(
                operation_id=receipt_refs[-1]["external_action_key"],
                live_result_reference={"actions": receipt_refs},
            ),
            completed_action_keys=tuple(completed),
        )
        self.store.finish_candidate_execution(execution_id, self.owner, "done", result)
        return result
