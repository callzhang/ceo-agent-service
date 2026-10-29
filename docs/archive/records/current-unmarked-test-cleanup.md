# Unmarked current-model test cleanup — 2026-10-26

This follow-up examines normally collected tests after the marker cleanup.
It changes test fixtures and assertions only. There is no application, schema,
authorization, review, retry or provider-policy change and no runtime restart.

## Findings and disposition

| Finding | Current test contract |
| --- | --- |
| Consumer fixtures saved a bare wire hash or a Skill-only hash and some expected unconditional resume | Seed an actual preceding Consumer turn through the real Store and substitute CLI. Matching current selected-Skills/Developer contracts resume; changed or missing contracts start fresh. Preserve force-rerun, failure recovery, route isolation, native schema and frozen-material checks. |
| WorkSummary copy acceptance selected input payload from `tasks.record_type` | Read `work_summary_inputs` through its actual Task foreign key, assert the common Task is done, and retain business result, provenance, compaction/hash/bytes, reopen and idempotency checks. |
| Workbench copy acceptance selected domain rows from the old Task container | Read `workbench_tasks` and `workbench_turns`, join the turn's executable Task, and retain API idempotency, exact single turn and cold-read assertions. |
| A Store initialization test had become an empty constructor smoke test | Force a current-schema cold open, fail if initialization is attempted and compare the complete database dump before/after. |
| A mapping preservation test directly called initialization and described a retained automatic migration | Test the actual current cold-open boundary; preserve exact business key, Task identity and original created/updated timestamps without resetting the schema version. |

No examined test was exclusively for a retired feature and therefore none is
deleted in this follow-up. Obsolete unconditional-resume expectations are
replaced by the existing current contract; they do not add a new policy.

The SQL inventory has 87 candidate functions in 31 files: 73 explicit
predecessor/converter/source oracles, 4 SQL-expression mocks, 4 current-schema
checks, 4 strict-rejection fixtures and the 2 obsolete positive queries fixed
above. Old layouts in explicit inverse tools and rejection tests remain
intentional test inputs, not runtime compatibility. A one-column `reply_tasks`
mock supplies the SQL expression's table alias; it does not construct a runtime
Store. Current `AgentRun.final_result_json` and `ReplyAttempt.agent_run_id` DTO
fields remain valid, and result-envelope validation input does not imply stored
trajectory copies.

## Verification

- Consumer file: 179 passed; current prompt/session fingerprint file: 4 passed.
- Native trajectory, metadata-lock, native-only, reviewed candidate and result
  validation checks: 82 passed. Audit and selected runtime-worker retry/manual
  paths: 62 passed.
- Six Store constructor, mapping preservation and strict schema-rejection
  cases pass. Both copy lifecycle cases fail on the old SQL and pass on the
  current owner queries using a synthetic current Store/Email source.

The copy module's no-network/subprocess guards, original-row/FK checks and
source SHA check remain. No production snapshot or test send is required.
The candidate inventory and focused counts are in the
[validation record](validation/2026-10-26-current-unmarked-tests.json).
This is candidate-driven inspection and focused execution, not a claim that
every repository test was executed. The service remains live, so no full local
suite is run.

Independent SPEC and QUALITY reviews caught a prefix-only hash assertion during
implementation. It now independently computes the complete contract from the
submitted Developer instructions and the current no-Skill wire base; both
routes compare exact hashes and the second refresh must preserve the first.
The two affected cases plus the four fingerprint cases pass after that change.
Both reviews approve the final scoped diff. The335 distinct focused cases above
are not inflated by repeat runs; root also reran the two synthetic-copy cases
successfully (3.64s).

The owned graph/test root and synthetic source root (about10.7MiB combined)
had no open files or external symlinks. The synthetic source retained its exact
SHA, passed SQLite quick_check and had no FK findings. Both roots were removed
and their absence verified; only the concise inventory and acceptance record
remain in Git. No production database backup was created by this follow-up.
