"""Build the existing manual correction context for any reply task channel."""

from app.agent_context import ManualRerunInstruction
from app.agent_contracts import AuditAgentResult, AuditOutcome
from app.store import AgentRole, AutoReplyStore, ReplyTask


def build_manual_rerun_instruction(
    store: AutoReplyStore, task: ReplyTask,
) -> ManualRerunInstruction | None:
    manual_rerun = None
    if task.manual_rerun_attempt_id:
        source_attempt = store.get_reply_attempt(task.manual_rerun_attempt_id)
        if source_attempt is not None:
            prior_audit_feedback = []
            source_run = (
                store.get_agent_run(source_attempt.agent_run_id)
                if source_attempt.agent_run_id
                else None
            )
            if source_run is not None:
                for run in store.list_agent_runs_for_task_generation(
                    task.id, source_run.execution_generation
                ):
                    if (
                        run.role is not AgentRole.AUDIT
                        or run.status != "completed"
                    ):
                        continue
                    review = store.get_candidate_review_for_audit_run(run.id)
                    if review is None:
                        continue
                    audit_result = AuditAgentResult.model_validate_json(review["result_json"])
                    if (
                        audit_result.outcome in (AuditOutcome.RETURN, AuditOutcome.REJECT)
                        and audit_result.feedback is not None
                    ):
                        prior_audit_feedback.append(audit_result.feedback)
            manual_rerun = ManualRerunInstruction(
                source_attempt_id=source_attempt.id,
                reviewer_feedback=source_attempt.reviewer_feedback.strip(),
                suggested_reply_text=(
                    source_attempt.corrected_reply_text.strip()
                    or source_attempt.draft_reply_text.strip()
                    or source_attempt.final_reply_text.strip()
                ),
                feedback_scope=source_attempt.feedback_scope or "one_time",
                skill_update_requested=source_attempt.skill_update_requested,
                skill_update_receipts_json=source_attempt.skill_update_receipts_json,
                prior_audit_feedback=tuple(prior_audit_feedback),
            )
    return manual_rerun
