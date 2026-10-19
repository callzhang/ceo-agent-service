# Business model simplification execution

User authorized Subagent-Driven implementation of all four units, copy validation and schema inverse before publication; provider sends must remain substitutes in tests. This is active work, not a completion report.

## Source and ownership boundary

- Worktree: `/Users/derek/.codex/worktrees/business-model-plans/ceo-agent-service`, branch `codex/business-model-plans-20261019`.
- Planning baseline f873de562 was merged with actual deployed d6a1b024c as 580ebf6b5; only additive claim conflict resolved, preserving both sides.
- Tool afc31ccd8: TDD 5 missing-tool failures, one route-order regression, final 7 passed; independent SPEC and QUALITY approve. Root rerun 7 passed.
- Frozen read-only online-backup source: `/private/tmp/ceo-model-simplification-0zjph5co/source.sqlite3`, 472,539,136 bytes, quick_check ok. Temporary test source; not a new retained production backup. Original production untouched.
- `business-model-ownership.json`: 1,256 actual stored/generated fields, 123 declared payload record kinds across Tasks/Actions/Business/Configs/State; includes empty supported types. Runs fields remain captured in physical schema and run ownership document, not counted as payload kinds. Default retain_current is conservative ownership, not proof of necessity.
- Two concrete semantic maps: `business-model-actions-email-ownership.md`, `business-model-task-business-ownership.md`.
- `business-model-ui-coverage.json`: all 36 actual routes and page-specific interactions, all not_run. No browser/CLI/production behavior acceptance yet.

## Baseline test defect before runtime changes

Root ran `python -m pytest -q tests/test_reviewed_candidate_store.py tests/test_email_preview.py` on deployed-lineage baseline before A1 component edits: 34 passed, 5 failed, 232.14 seconds. The five failures are obsolete direct current-schema fixture writes at test_reviewed_candidate_store.py lines 389/463/491/545/563 (`tasks.execution_generation`, flat status, and retired service_state). This is not an A1 regression and not a reason to weaken assertions. Later caller-stage fixture ownership must use existing task8_set_parts and state projection, preserving generation, selection and history assertions.

Ownership milestone: independent SPEC initially identified missing per-field inverse/caller rules and record-specific A2 handling; root corrected them. SPEC re-review APPROVE; fresh QUALITY APPROVE. This approves the ownership contract, not runtime migrations.

## Active runtime implementation

A1 component: new target removes candidate_selections.branch_json storage slot AND generated column, retaining candidate_id/option_key. Explicit target compiler keeps default/source factory fingerprints exact. Forward verifies bound immutable option; inverse derives branch from current rows, including post-forward new selections. No stale business-row backup and no source DB file restoration.

Runtime component is not yet reviewed/activated/published. Next: component RED/GREEN → independent SPEC → QUALITY → frozen-copy forward/new-fact inverse/reforward → caller conversion and release integration. A2 keeps the independently distinct {} attempt fact; B1 keeps model_text and RFC identity differences. C/D retain real aliases, history and provider IDs until explicit owner conversion.

Independent A1 SPEC replay passed all original 25 cases and found a real dependency defect: an unknown or modified trigger owned by `configs` was excluded from the SQLite dependency probe merely by its table ownership. The new regression matrix reproduced eight failures across forward/inverse and both source variants. The correction compares each exempted trigger's exact name and SQL against the published Configs declaration; targeted verification and SPEC rereview are in progress. No production-copy migration has run yet.

Parent acceptance runner is temporary `/private/tmp/ceo-model-simplification-0zjph5co/validate_a1.py`: source/candidate SQLite backup, table stored-value hashes, exact full-schema inverse, preservation of the one existing Runs FK finding, a new selection bound to a real approved candidate/review with a unique option key, then actual archived `d6a1b024` Store cold-open (asserting loaded module path), and reforward equality of current stored facts. This is prepared acceptance, not a passing result.

Downstream appendix SPEC identified three implementation-contract gaps: A3 review/selection references must remain physical typed FKs, not opaque JSON fields; C must map independent retained lease-ledger facts and per-kind attempts semantics and include Run owner conversion; D's first history-only cutover is not full retirement and must preserve the linked provider Todo completion path until its fact/receipt owner is migrated. A3/C/D descriptions are corrected and independent SPEC rereview approves the scoped contracts. These are not exemptions from the parent plan's completion criteria; the corresponding runtime work remains unimplemented.
