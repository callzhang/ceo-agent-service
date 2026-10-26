# Current native reference readers — 2026-10-26

## Scope and cause

Derek requires current structures only and no old compatibility logic. The
History Meeting search had its own workload-key/aliased-row reference selector,
while detail/native search used native Run parents. The redundant helpers were
`latest_standalone_ref`, `exact_standalone_ref` and `legacy_codex_ref`; exact had
no application caller. They are now retired, together with the unused transcript
alias projection. History search uses the same `native_run_ref` as detail and
Meeting-native search. Search metadata and the current Run's real session/range
columns are retained; neither is an old physical schema.

A direct replacement probe exposed a deterministic shared-reader bug: it chose
any completed child call, so a later `meeting_audit` could replace a `meeting`
execution. The actual producers separately write task/meeting/meeting_audit.
The reader now binds parent_id plus the existing execution workload kind. This
only chooses the correct recorded result; it does not alter Audit, authorization,
retry, execution states, delivery or provider policy.

## Preservation and regression evidence

- Current metadata comparison:795 indexed Meeting entries have exactly the same
  native locator as the old History selector after unification.
- Shared-reader correction:58 Meeting Runs previously chose another role's call.
  Real original trajectories398104/399558 reproduce native_decision_invalid with
  the Audit locator and decode successfully with the actual execution locator.
- Four regressions first fail on the original code: Task/Meeting must not choose
  a later other-role call; both search surfaces must use the execution's actual
  parent and must not substitute an Audit or another Run with the same key.
-39 focused cases pass in22.67s;25 actual Store FTS/embedding, History and Meeting
  runtime callers pass in63.76s; Ruff/diff pass. No real provider calls.
- Existing exact OKR session/range and failed-Meeting unavailable cases now test
  the current shared resolver directly. No transcript/body copies are introduced.
- History metadata candidate membership/title/alias/order and Run-session binding
  remain; Meeting-native candidate enumeration remains separate. FTS/embedding
  scoring is untouched. Current Run locators with no recorded execution attempt
  remain factual reads; failed/running attempts do not fall back to a stale Run.
- No schema/version/data migration or production write is part of this change.

## Release boundary

Independent read-only review approves implementation/SPEC, with current-contract
updates required in the same commit; those updates are included. Standard exact-
target gate, deployment, production UI/API/queue readback and authorized backup/
test-artifact cleanup remain pending. The current-data comparison and real
trajectory probes are read-only; they are not production deployment evidence.
