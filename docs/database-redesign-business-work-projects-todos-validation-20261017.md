# Business Work Projects and Todos validation — 2026-10-17

This isolated candidate moves the existing `work_projects` and `work_todos`
source rows into the typed `business` table. The exact source-to-target
dispositions remain in
`docs/superpowers/specs/2026-10-11-database-redesign-field-map.csv`; the
actual/fresh declarations are captured in
`tests/fixtures/business_work_projects_source_declarations.json`.

## Exact field disposition

`work_projects.id` becomes the `work_project` public ID and `legacy_key`;
`title`, `status`, `created_at`, and `updated_at` use the existing common
Business columns. The following source values stay in typed payload fields:
`category`, `tags_json`, `priority`, `risk_level`, `needs_derek_attention`,
`owner_user_id`, `owner_name`, `related_people_json`, `goal`, `background`,
`facts_json`, `current_state`, `blocker`, `next_step`, `next_follow_up_at`,
`follow_up_mode`, `source_conversations_json`, `last_activity_at`, and
`owner_evidence_json`. `memory_context_json` is intentionally canceled; it is
not copied or replaced.

`work_todos.id` becomes the `work_todo` public ID and `legacy_key`; `title`,
`status`, `created_at`, `updated_at`, and `completed_at` use existing common
Business columns. `project_id`, `owner_user_id`, `owner_name`, `priority`,
`deadline_at`, `next_follow_up_at`, `follow_up_question`, `blocker`,
`completion_evidence_json`, `created_from_update_id`, `description`, and
`owner_evidence_json` stay in typed payload fields. The `project_id` remains
the source's integer reference; this slice does not add a foreign key that the
source declaration did not have.

The two source allocators remain separate public-ID streams. Existing child
references are migrated according to their actual source declarations; no new
domain fields or guessed links are introduced. DAO-facing IDs, row shapes,
completion evidence and the caller-owned transaction boundary are preserved.

## Frozen actual-copy evidence

The source was opened read-only and copied to a disposable SQLite database. Its
SHA-256 before and after the tests remained
`970a909a9cfcb4fe604d2111d5572560d4744978df20fb317c4e433059566320`; its
`PRAGMA quick_check` result was `ok`.

| Source fact | Rows / value |
| --- | ---: |
| `work_projects` | 975 |
| `work_todos` | 3,327 |
| Todos by status | 2,914 open; 350 done; 57 waiting_owner; 6 cancelled |
| Todo rows with a missing project | 0 |
| Populated `memory_context_json` rows | 904 |
| Populated cache bytes (SQLite BLOB length) | 992,368 bytes |
| Cache entries containing `official_project_registry_key` | 0 |

The Business forward copy retained all nine mapped Business source families'
parent facts, public IDs and allocator waters. Exact mapped counts for the two
new kinds were 975 and 3,327. Existing child facts and allocator waters also
matched; the already-present `runs` → `tasks` FK finding was unchanged. A
completed TODO retained its `completed_at` value and completion receipt, whose
source reference was `dingtalk_todo:57373496870`.

On a separate disposable copy, current-fact inverse then reforward succeeded;
the canceled cache reset to its declared default rather than being retained as
a second copy. A separate schema-rollback rehearsal compared the source and
rolled-back copy by complete SQLite `iterdump`; the dumps matched exactly and
the copy passed `quick_check`. All migration writes and rollback rehearsals
were confined to temporary copies. The original frozen source stayed
byte-for-byte unchanged.

## Test boundary and release status

- Business schema-release and storage tests: **454 passed**.
- Project repair, semantic import and retrieval tests: **59 passed**.
- Relevant Console project tests: **6 passed**.
- The scheduled-task History route and TODO completion-scan behavior after the
  current schema changes: **3 passed**.
- Relevant project/todo Store tests: **5 passed**.
- Follow-up/sync source and current-fact inverse test files: **90 passed**.
- `ruff`, Python `compileall`, and `git diff --check`: **passed**.
- The three relevant Audit Project detail render tests passed. A broader Audit
  file run reached 41 passes, then hit a stale Codex-session fixture that still
  passes removed `summary_text`, `fts_text`, and `embedding` arguments.
- The broad TODO-sync/follow-up run reached 15 passes, then hit an old
  generation-fence fixture that directly updates removed
  `dispatcher_claim_leases`; it is outside this Work Project/Todo data mapping
  and is not counted as passing.
- The broad Console API run reached 85 passes, then stopped in an old fixture
  that inserted into the retired `scheduled_tasks` table. The fixture was
  updated to use `create_scheduled_task`; the formerly failing History route
  test now passes against the current schedule projection.
- The broad CLI file reached 86 passes, then hit an existing fixture that
  writes the removed physical `tasks.updated_at` column; this is not counted as
  passing and is outside the canceled-cache CLI command removal.
- Frontend `npm run build` could not start because this worktree has no
  `frontend/node_modules/.bin/tsc` installed; no dependency installation was
  performed.

This verifies only this local Business slice on the frozen database copy. The
full paired 116-to-11 release, final 10-core-table-plus-ledger count,
production migration, publication and deployment remain unverified and have
not occurred.

## Business9 actual/fresh exact-row proof continuation (2026-10-17)

The earlier Business image assertion compared physical source-column order to
the target order. That treated legitimate source/fresh layout differences as
data loss and included the canceled Work Project `memory_context_json` cache.
The comparison now projects each source through its approved actual or fresh
field map before comparing values and SQLite storage types. The synthetic
forward/inverse/reforward proof passes for both layouts; it covers all nine
Business kinds, 15 retained child tables, allocator waters, original-row
preservation, updates/deletes and current-fact inversion.

The same test was repeated against an isolated SQLite online-backup copy of
the frozen 970a source (`970a909a9cfcb4fe604d2111d5572560d4744978df20fb317c4e433059566320`):
all 9 source parent families and 15 child families (5,006 parent rows and
1,990 child rows) passed Business forward, current-fact inverse and reforward.
`quick_check` remained `ok`, the existing single foreign-key finding stayed
unchanged, the source hash stayed fixed, and the temporary copy was removed.

The combined publication regression's unrelated-table and allocator checks
were updated to recognize the already-proven State, Configs, Tasks, Runs,
Business and Provider source/target families, retired FTS shadow tables, and
the empty retired training-observation table. Its email-account fixture now
compares against the typed Config projection in the source's actual/fresh field
order. The paired current-public-write, old-public-write, inverse and reforward
case now passes with all nine Business IDs and an intermediate total of 46
ordinary tables. This is useful paired publication evidence, not the approved
final 11-table result; exact reconciliation and implementation of the
remaining 46-to-11 source-family routes remain open.

A separate read-only production check found the live checkout at
`f4d787f9` and the database marker at Store `2026-09-25.1` / Email 43; the
frozen 970a source and archived 7dad code therefore are not a current
production binding. No live database was modified.
