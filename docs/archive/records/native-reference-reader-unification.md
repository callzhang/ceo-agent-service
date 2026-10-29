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

## Production acceptance and cleanup

Independent implementation/SPEC review approved. Normal main integration kept
separately owned changes intact. Standard exact-target deployment completed
`f143f5f7f → 134af324c`; its mandatory clean-clone gate passed under the unchanged
2GB/5minute test budget. At10:02 UTC, PID29433 read back health200/ok, status,
Attention and History200; failed36/Attention9 match the immediate pre-release
baseline. The complete476-object schema hash is unchanged and FK findings remain
empty.795 search metadata rows remain. The retired helpers are absent from the
installed production module.

Real production APIs for398104 and399558 now return native_available=true with
empty native_reason. Chrome opened both actual pages and confirmed execution
explanations were visible, with the former native-process-unavailable notice
absent. Run190042 still correctly reports unavailable native source and retains
its historical Job4908; no process record was fabricated. No real test send or
business replay was performed. The change introduced no schema or business-data
migration. Metadata membership and scoring were verified by the current-data
locator comparison and focused actual-caller tests, not by inventing a new
production Task.

The obsolete baseline source copy/bytecode were deleted immediately after the
comparison. After production readback, the whole task temporary root and the
integrity-verified workflow backup were removed and path absence verified.
Production/native trajectory originals are preserved. Exact body-free evidence:
[readback](validation/2026-10-26-native-reference-readers.json).
