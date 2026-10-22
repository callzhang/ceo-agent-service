# Original Tasks82 source-slot disposition, 2026-10-16

Every original wide-Tasks physical slot is accounted for by source kind: **148 structural/mapped positions plus508 forced-NULL positions across eight82-column shapes**. In a read-only production snapshot of25,921 rows, all1,589,061 unused row cells were SQL NULL: zero non-NULL cells, zero nonempty values and zero value bytes. The exact source CHECK clauses require those NULLs. Root explicitly approved reconstructing them as SQL NULL for the current-fact inverse. This is evidence and disposition only; no inverse or runtime code was implemented.

The [machine-readable crosswalk](2026-10-16-task20-source-slot-disposition-analysis.json) contains all133 logical source fields, every kind's full source projection, each of the508 unused slots with aggregate counts/storage classes/default SQL, and the exact applicable CHECK clause indexed by each slot. The134 mapped fields of the twelve added kinds are outside this original-wide-slot question. No arbitrary row-history JSON or new task field is required.

## Verified source and aggregate boundary

Production HEAD was44ee956d10a1360c1ba9b44bf08f4c61e630beb4 before and after the snapshot. SQLite3.50.4 under pinned Python3.13 opened the existing database with URI `mode=ro`, set `query_only=on`, began one read snapshot, then rolled back and closed. Queries returned schema metadata and aggregate counts/types/lengths only; no row body, input text or receipt was fetched, output or stored. No copy, backup, VACUUM or database write occurred.

The table has82 physical columns. Table DDL SHA256 is `ee4330cb187a80d4699db16e56b61e47f992165ed44a840f4ecbe8059888374c`, exactly the accepted field map and independently frozen source artifact. All table/index/trigger objects, table_xinfo,23 index definitions and index_xinfo entries, and three FKs match the frozen source metadata; only index enumeration sequence is excluded from index-list comparison. There are eight triggers. All82 physical columns have no explicit default SQL. An omitted nullable physical slot therefore receives SQL NULL.

| Source kind | Rows | Logical fields | Physical structure/mapped | Forced-NULL slots | Non-NULL unused cells | Unused value bytes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| reply_tasks | 7551 | 26 | 28 | 54 | 0 | 0 |
| reply_task_inputs | 8262 | 17 | 19 | 63 | 0 | 0 |
| work_summary_inputs | 9108 | 14 | 16 | 66 | 0 | 0 |
| meeting_alignment_jobs | 754 | 23 | 25 | 57 | 0 | 0 |
| okr_review_requests | 20 | 16 | 18 | 64 | 0 | 0 |
| weekly_okr_analysis_jobs | 169 | 11 | 13 | 69 | 0 | 0 |
| workbench_tasks | 20 | 7 | 9 | 73 | 0 | 0 |
| workbench_turns | 37 | 19 | 20 | 62 | 0 | 0 |

The table partitions are disjoint and cover all82 columns for each kind. Logical count includes public `id`; physical coverage also retains `tasks.id` and `record_type`, while logical public id is stored in `legacy_key`. Workbench turn `final_text` is a constant empty-TEXT projection with no physical column, so its19 logical fields correspond to20 used physical slots rather than21. Byte totals mean `sum(length(cast(value as blob)))`, with NULL contributing0; they do not measure SQLite record header/page overhead.

## Exact schema proof and defaults

`app/task_storage.py:441-480` derives the six original kinds' physical fields through `COLUMN_MAP`, maps `reply_task_id` to `parent_id`, and emits a per-kind `field is null` CHECK for every unrelated base field. `:519-559` derives Workbench fields, maps `task_id` to `parent_id`, excludes the projected `final_text` sentinel, requires unrelated physical fields NULL for both Workbench kinds, and separately requires all Workbench-added fields NULL for the six numeric kinds. The report extracts the exact outer CHECK fragments from the verified live DDL and checks complete column tokens against every508-slot complement; it does not infer eligibility merely from empty current data.

There are **no non-NULL static defaults or dynamic timestamp defaults among unused physical slots**. `TASK_DEFAULT_SQL` at `:365-422` and `:577-583` belongs to mapped per-kind logical fields; `insert_task` at `:639-656` inserts omitted logical defaults inside its native statement and distinguishes explicit None from omission. Applying those defaults to another kind's unused physical column would change source behavior and can violate its CHECK. In particular, `created_at`/`updated_at` default expressions in logical source declarations are not permission to replace historical mapped values with a new clock. No current-timestamp-versus-historical-value comparison was made.

## Consumers and preservation obligations

The inspected source closure is finite: historical task projection/default/schema code, Store and Workbench DAOs, the accepted Task8 writer/caller inventories, and the eight-file external-writer and three-file console-reader analyses. This is not a whole-program exhaustive-use claim.

- `task_storage.py:585-637` projects numeric public ids from `legacy_key`, Workbench public UUIDs from `legacy_key`, WB public parent keys through physical `parent_id`, and Reply public input keys through physical input pointers. Those physical identities and pointers are used by source readers, joins, FKs and writers and must remain exact independently of displayed public keys.
- `store.py:322-344` joins current/claimed Reply source projections to canonical input facts. Its canonical input facts are mapped fields, not unused physical padding.
- `store.py:16666-16698` and the eight other meeting RETURNING sites observe the wide row shape. `_meeting_alignment_job_from_row` at `:15797-15805` selects declared `SOURCE_COLUMNS`; delivery claims around `:16801-16828` separately use physical returned `id`. Exact source-shaped DTOs alone do not prove the required raw identity preservation.
- `workbench/store.py:215-218` and `:502-509` insert public UUIDs and physical parents while omitting physical id. DTO hydration at `:2493-2504` selects model fields. The source `final_text` sentinel must stay empty-TEXT; it is not stored history.
- `storage_maintenance.py:140-162` has a real NULL-sensitive consumer: the original `error_detail<>empty` SELECT/UPDATE has no kind predicate. Source CHECKs make `error_detail` NULL for every kind except WB turns, so SQL three-valued logic implicitly excludes those rows. Reconstructing exact NULLs preserves that behavior; replacing padding with empty strings/default numbers is not equivalent generally.
- `dingtalk_delivery_repair.py:83-145` has a raw `tasks.*` read; inspected downstream keys are physical `id`, `execution_generation`, `status` and `error`. No extra business fact in its unused padding was established. Unknown raw consumers may exist; exact NULL reconstruction preserves the original unused values for all schema-valid rows anyway.
- `task_schema_is_current` at `task_storage.py:690-720` compares exact source declaration and explicit schema objects. The inverse must restore the source schema as well as current values.

The finite inventories are [Task8 writer analysis](../../database-redesign-task8-writer-analysis-20261016.md), [Store caller analysis](2026-10-16-task8-store-caller-analysis.md), [Workbench caller analysis](2026-10-16-task8-workbench-caller-analysis.md), [external writers](2026-10-16-task20-external-writers-caller-proposal-analysis.md), and [console readers](2026-10-16-task20-console-readers-caller-proposal-analysis.md). These remain source evidence with their original bounded proof limits.

## Required inverse comparison

**Raw original82-column equality is a valid requirement in addition to logical source-projection equality.** Restore current mapped scalar values with exact SQLite storage classes; preserve existing-eight physical `id`, `record_type`, `legacy_key`, parent and input pointers; reconstruct unrelated physical slots as SQL NULL. This allows exact raw equality for the original shape without any pre-migration task-row image, hidden history payload or unused-slot verification exclusion. The migration acceptance must execute this comparison; this report only establishes that the requirement is justified.

Logical identity alone is insufficient. Reply `current_input_id`/`claimed_input_id` project public input keys, while the raw fields are physical Tasks FKs. WB `task_id` projects the parent's public UUID, while raw `parent_id` is physical. Input `reply_task_id` projects original physical parent_id directly; the source Reply CHECK also equates Reply public id and physical id. Retain the existing physical coordinates rather than reconstructing blindly from a public display value.

Source allocator presence is separate metadata. This snapshot has one positive `sqlite_sequence` entry for Tasks, zero zero-valued entries; no counter value or counter row body was exported. Actual observed runtime inserts use omitted physical id and single VALUES plain/IGNORE/UPSERT, as frozen in the accepted allocator plan and writer inventory. `task20_definitions.py:890-950` tracks existing-eight source water independently from new-kind global allocation, with original absence-versus-zero retained in migration metadata. `tests/test_task8_allocator.py:164-180` already demonstrates captured absent/zero/deleted-high and the first positive automatic allocation. Those accepted tests were inspected, not rerun here. The exact inverse counter algorithm and current-write/rollback proof remain root-owned; this report makes no counter edit or generic allocation API proposal.

The proof applies to this exact source declaration and schema-valid rows. CHECK-disabled writes, modified schemas and unknown program consumers were not audited. No target constructor, migration, genuine predecessor, public DAO, provider, native CLI, main merge, push or deploy was run.
