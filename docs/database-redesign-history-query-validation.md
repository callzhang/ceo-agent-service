# History latest Attempt query validation (2026-10-14)

Implementation: `5afbed0be52349295d459a7c259f00f38333342f` on
`codex/database-redesign-history-query-20261014`, based on published
`2773d6f43518b64c85f1d4bed9d520bd5563df07`. This changes only the
`_operation_logs_base_query` reply-attempt latest-task exclusion. The existing
current-generation exclusion remains separate. There is no schema, index,
policy, API, UI or provider change.

The former correlated predicate searched every newer projected Attempt and
joined its projected run for each outer Attempt. The replacement materializes
the maximum **public** Attempt ID for each physical Agent run task, then tests
whether that maximum is greater than the outer public ID. Its source includes
all generations, as the original newer side did. The materialization lives
inside the reply branch so source-table pruning, count, chart-status and page
hydration all retain a valid self-contained query. The separate legacy
`_history_items_query` path was not changed.

Focused regression evidence: the 240-task typed fixture used 479,000 sampled
SQLite VM steps on published source, exceeding the 100,000-step assertion;
the candidate used 29,000 steps. The suite also covers same-generation newer
Attempts, old-to-current and current-to-old generations, NULL and dangling
run/task parents under disabled foreign-key enforcement, and reply public IDs
that differ from physical action IDs. History page/count/chart-status results
and body hydration passed. `tests/test_store_history_pagination.py`,
`tests/test_history_types.py`, and `tests/test_history.py`: 36 passed;
Ruff and `git diff --check` passed.

Post-commit actual-copy proof used one SQLite online backup from the current
production database, opened only as a query-only copy in a task-owned temporary
directory and removed after the run. The copy passed `PRAGMA quick_check` and
was 475,582,464 bytes. The original source projections supplied all 14,886
reply Attempts, their Agent runs and their reply tasks. An independent in-memory
oracle applied both original `NOT EXISTS` predicates to these rows, preserving
SQLite's NULL comparison behavior and including newer Attempts from every
generation. Its complete ordered visible public-ID set matched the candidate
query exactly: 12,508 IDs; SHA-256 of the comma-separated ascending IDs was
`01d5fe4aaead7c30f66afc25598b8c8201a7a3b48c626ec29bc55558abe81561`.
Candidate key projection took 0.168 s on this copy, and `EXPLAIN QUERY PLAN`
showed one `MATERIALIZE latest_task_attempt` node.

For 15 selected visible Attempt IDs spanning early, middle and latest records,
the published SQL and candidate SQL returned identical complete History rows
(all fields), taking 0.018 s and 0.044 s respectively. The candidate full
source-key page query with `count(*) over()` returned 20 rows and total 67,106
in 0.242 s. These timings are local copy observations, not a live browser/API
acceptance. The original complete reply-key query exceeded a deliberate 30 s
SQLite progress limit; an earlier original all-row projection was interrupted
after roughly 75 s. Neither interrupted result was counted as an equivalence
proof, and the expensive original query was not retried. The complete key-set
oracle and bounded original SQL row comparison are the equivalence evidence.

This branch has not been pushed or deployed. Live History page, chart and
operational readback remain publication acceptance steps.
