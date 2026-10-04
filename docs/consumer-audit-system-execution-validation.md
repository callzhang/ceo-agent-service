# Consumer / Audit / System Execution Validation

Status: implementation in progress, not deployed.

Baseline ref: `a7d4738a3b6591abc4d8fc69236b5517b4f98004`. Frozen cases: `evals/consumer_audit_system_execution/v1.json`. Persisted-contract replay is separate from model/business evaluation and controlled live receipt verification.

Initial RED evidence: `test_consumer_human_question_must_wait_for_whole_candidate_audit` failed on 2026-10-04 before runtime edits: `_derive_state` returned terminal Consumer `needs_human` with `audit_result=None` instead of `_NextAudit`. No external action occurred.

Production read-only bootstrap: installed launchd service root `/Users/derek/Services/ceo-agent-service`; supervisor PID 86943, worker PID 86946; effective DB from process args `/Users/derek/Library/Application Support/ceo-agent-service/auto-reply.sqlite3`.

Hard-refusal release regression: task 386115 / attempt 15006 / Consumer 22322 / Audit 22323–22328. The handoff reports one native send-tool harness refusal followed by repeated result corrections, with underlying `source_code=provider_risk_rejected`; no alternative send is authorized. These historical objects must remain failed and must not be automatically replayed by the new executor. Their actual persisted/live state remains to be read back.
