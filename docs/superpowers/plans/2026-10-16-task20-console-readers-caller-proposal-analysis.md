# Unselected Tasks20 console reader proposal

The adjacent patch closes the bounded Tasks read conversion in exactly `app/audit_web.py`, `app/quality_gate.py` and `app/rule_question_retirement.py`. It applies directly to their pinned current sources; it does not apply the external-writer proposal or edit its eight files. All application/test files remain unchanged. This is copied-code/static evidence only: no database or public reader/retirement method was executed and no release is selected.

Patch: [2026-10-16-task20-console-readers-caller-proposal.patch](2026-10-16-task20-console-readers-caller-proposal.patch), SHA256 `a823ff570d1d8b6045f1289853831f7893531f78badc1281134bb80389f717df`. The [JSON analysis](2026-10-16-task20-console-readers-caller-proposal-analysis.json) freezes exact source SQL-building expressions,88 SQL call sites including their parameter/keyword expressions, source/candidate module AST hashes, changed-owner AST hashes and verification receipts.

| File | Source SHA256 | Candidate SHA256 |
| --- | --- | --- |
| `app/audit_web.py` | `036f4f46259485c5e341cab8171071b732377d2cee685d90f41eceb8d4dc77e7` | `d10a0995be2abaf7cd6163e0337f474933d724081353a9d806050ad65b2b9559` |
| `app/quality_gate.py` | `c9888b13f4b4965a62793150d030cdbe3e85fe7ed8d45178ff9604eb9475da3f` | `0a198fff93ce894bf721c3a888b963bb15d694387fd4e538ff1ac85d6793340a` |
| `app/rule_question_retirement.py` | `e2bf5b4fd78dd40fd557480b3c6c6211f56eb6d40cebb209b582fdc58f3cac8e` | `73d78b8e5c638ab7b5183d7f1a10146e2d5a371eb475cc23f6b3c037063b6561` |

## Read closure and identity contracts

Nine existing-eight module projections are selected locally through accepted `task20_projection`: Reply, Work summary, Meeting and OKR in each console module, and Reply in retirement. These replace only the historical `task_storage` projection imports used by these files. Existing function consumers, source-shaped aliases and all remaining imports are retained. `REPLY_TASK_CURRENT_SQL` remains imported from Store; its canonical input/current-pointer shape must come from the already prepared existing-eight Store proposal during complete cutover. This patch does not duplicate or change that shared projection.

Seven business-object-map relations become `task12_projection('business_object_tasks')`: audit five, quality one and retirement one. Existing alias names, physical reply parent values, business-key predicates, latest/current-generation conditions, supersession/recovery exclusions and attempt/candidate/selection joins remain intact. Nullable business keys and multiple aliases remain representable; no new uniqueness, filtering, timestamp, audit or decision policy is added.

The audit module uses35 finite `task20_column` expressions for its direct shared-Tasks reads. Its direct physical-parent semantics remain explicit:

| Original owner and source anchors | Proposed change and preserved behavior |
| --- | --- |
| `_work_summary_input_queue_snapshot`2653; `_meeting_job_queue_snapshot`2677; `_okr_review_queue_snapshot`2697 | Nine native count/latest/latest-error statements retain kind scope, lower/coalesce/trim filters, statuses, ordering and limits while selecting typed status/update/error columns. Scalar result aliases remain source-shaped. Legacy numeric public-ID error tie-breaks remain `cast(legacy_key as integer)`. Source body/payload fields are not added to the result. |
| `_reply_task_queue_snapshot`2760 | Existing materialized current projection retains structural `tasks.id`, physical business parent comparison and current-key predicate. Typed business key/status/time/error fields retain `status`, `updated_at` and `error` output aliases. Latest/error ordering still uses the same physical ID selected by the original CTE. It is not silently replaced by the public Reply projection. |
| `_reply_attempt_queue_snapshot`3076 | Both business-map relations change; all existing current attempt, delivery generation, retry/recovery and supersession predicates and SQL fragments remain. |
| `_queue_attention_rows`3372, indexed reply candidate3489 and work-summary candidate3567 | Keep direct compact ID selection, kind filters and source public numeric values. The forced source `idx_reply_tasks_status` becomes accepted `idx_task20_published_1`; its exact declaration is the existing `_PUBLISHED_INDEXES` ordinal1 for typed Reply status/id. No index is added and no forced-index clause is removed. The work-summary dynamic status selector maps its existing finite `status_column` through `task20_column`. |
| `_queue_attention_rows`, classification3611–3629 | Both failed-row query levels use `task12_projection(..., include_rowid=True)`, preserving hidden source ROWID membership, public nullable TEXT `task_id`, JSON context/summary, exact failed predicate, update/task-ID ordering and optional limit. Shared physical Tasks IDs never substitute for source ROWIDs. |
| `_queue_attention_rows`, meeting memory3698–3710 | Keep the native `events.meeting_job_id=jobs.id` physical join and meeting kind guard. Only `jobs.title`/`jobs.meeting_id` become typed generated fields; event metadata and ordering remain unchanged. |
| `_human_decision_attention_rows`3887 | Source-shaped map relation only; existing approval, digest, generation, current question, selection and latest-attempt predicates remain exact. |
| Quality `_check_structured_needs_human`494 | Source-shaped map relation only; original structured-result validity and Attention policy remain. |
| Quality `_check_email_classification_tasks`640 | Three classification counts use source-shaped relations; failed/running/pending memberships, stale/due cutoffs, severity, issue codes and detail text stay exact. |
| Retirement `_retire_in_connection`61 | Source-shaped map relation only; all original obsolete-question eligibility, native-result read/validation, authority/receipt checks, preview/apply branch, Action/State writes and returned receipt remain unchanged. |

No query aliases a child physical ID into its source public ID. New-kind classification source ROWIDs remain numeric `legacy_key`; business parent references remain physical `parent_id`. Existing-eight direct physical IDs, public projection fields and canonical current-input pointers keep their original roles. The proposal does not repair or redefine any pre-existing comparison between these coordinates.

## Seven generic queue helpers and classification presence

The source queue specifications keep their names, status/time/error column arguments and return descriptors. `Email classifications` becomes physically present when `tasks` exists, including when the selected kind has zero rows. Presence is not inferred from finding a classification record. The seven existing helper roles are handled as follows:

| Helper | Exact target interpretation |
| --- | --- |
| `_sqlite_table_exists`3941 | Map only the classification logical source name to physical `tasks`, then execute the original SQLite schema existence query with the same parameter shape. Every other table name is unchanged. |
| `_queue_status_counts`3949 | Select the classification source-shaped relation, including its hidden ROWID. Preserve the existing dynamic status expression/group/order and all other native sources. |
| `_queue_count_for`3965 | Entire function AST unchanged. Existing status-set/count arithmetic remains. |
| `_queue_retryable_count`3969 | Entire function AST unchanged. The source queries only its native DingTalk Todo retryable-token branch; classification still returns zero. No retry policy is introduced. |
| `_queue_latest_value`3995 | Select only classification's source-shaped relation; retain dynamic column, ordering, LIMIT1 and empty-result behavior. |
| `_queue_latest_error`4002 | Obtain logical source columns before selecting the classification relation. Both outer/inner ROWID references therefore see the original source ROWID. Status, nonblank-error, ordering and LIMIT1 remain exact. |
| `_sqlite_table_columns`4026 | For classification, return the accepted finite14 source fields from `TASK20_FIELDS` if its mapped physical source exists, otherwise an empty set as the original missing-table PRAGMA did. All other tables use the original PRAGMA unchanged. |

Classification has **no native `id` column**. Returning physical Tasks columns would cause `_queue_latest_error` to append `id desc`, changing source behavior and conflating source keys with global physical identity. The finite source metadata preserves the original `updated_at desc` order. It does not invent a tie-breaker, dynamically parse SQL or inspect stored row content.

Quality's existing `physical_source` mapping receives the classification→Tasks entry. Its optional-source enumeration and conditional classifier check use that same mapping. Logical `checked_sources` values and all required/optional source lists are unchanged. Other optional sources remain native. Required-queue absence still produces the original report and issue text; no new gate, policy or feature is introduced. The proposal assumes the selected Tasks20 declaration; it does not detect old layouts or fall back to native tables.

## Verification performed

A task-owned copied tree accepted exact `git apply --check` and application; applied bytes equal the candidate hashes above. Reverse-check/application restored all three original source blobs exactly. Final source hashes were rechecked against the real working tree. All temporary trees were removed.

The pinned Python3.13 executable `/Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python -B` compiled all three candidates in memory. Ruff with the repository config and `--no-cache` passed. All three copied modules imported with `sqlite3.connect` replaced by a function that raises on any invocation; no constructor/database was called. The same DB-forbidden import verified classification's14-field metadata has no `id` and confirmed the exact accepted forced-index declaration. Scalar registration/constructors remain root cutover work and are not called by this patch.

All **545 function signatures/decorators/return annotations** remain exact, including nested routes and class methods. Sixteen existing function bodies change; **529 other bodies** remain AST-identical. Breakdown: audit510/12changed/498exact, quality32/3changed/29exact, retirement3/1changed/2exact. All52 original `execute`/`executemany` parameter and keyword expressions and all36 `_count` parameter expressions remain exact. Existing connection/context-manager, PRAGMA, begin/commit/rollback/close ASTs are unchanged. Unrelated module statements, lists, policies, constants and all other imports remain exact.

An initial static candidate placed projection assignments before later imports, which Ruff rejected; assignments were moved after the original import sections. Static review also caught generated-field CTE outputs needing their original `updated_at`/`error` names; explicit aliases were added before final apply/reverse/compile/Ruff/import verification. These were proposal corrections, not changes to application source. No SQLite execution or public semantic result is claimed by the static checks.

## Root integration and direct-method proof still required

The separate external-writer proposal `b74fce7d` owns eight other files and is untouched here. This patch has no prerequisite file overlap with it. Store's existing-eight proposal must be selected alongside these imports to supply the canonical current Reply projection; constructor/manifests, historical factories and complete Tasks20 release coordination remain separate root obligations.

The pinned accepted declarations are SHA `cf58403bcc9dc7bf4c3ef5b9a777ff941c7a6de210e04b29e4cb7abb1e0df4d0`; Task12 helper at final validation is SHA `127311aa2ed303066fc3dd325a5642c0e61725a2d8db3b5bedfc218e804e013f`. This reader patch does not use its INSERT-expression extension and creates no helper gap or workaround.

Before transfer, root must execute actual source/candidate methods on corresponding exact source/target fixtures, retaining original SQL and all existing quality/Attention/retirement predicates. Required distinguishing cases include:

- Queue snapshots and all seven helpers with empty classification, failed/running/pending/done records, nullable TEXT task IDs, hidden ROWIDs differing from global physical IDs, tied update timestamps, blank versus nonblank errors, missing physical Tasks and unchanged native optional sources. Assert source column sets and latest-error behavior, not merely equal counts.
- All four old-eight snapshot families with tied timestamps, physical/public IDs that differ where source allows, current-map aliases and superseded historical records. Execute the forced index query and materialized CTE to verify index selection and output aliases.
- Attention's reply/business-map recovery, canonical input, scheduled later success, external-error exclusions and both limited/unlimited reads; classification source-ROWID membership and meeting-memory physical parent join with typed title fallback. Preserve the existing JSON and rendering outputs.
- `scan_hourly_quality` required-source failure, optional classification enumeration and all original stale/due/status memberships, without broadening the policy. Direct classification count helpers alone do not prove the complete report.
- Retirement preview plus controlled apply/replay/conflict paths through the actual method, with native-result/receipt fixtures and original transactions. Verify eligibility errors, unchanged unrelated Actions/State facts and original returned receipts; no live external action is needed.

No app/test/database/provider/native CLI/network/main/push/service/deploy action, new ordinary view, compatibility path, parser, audit/authorization/effect/safety logic or source-body policy was added. Full public reader acceptance, constructors, copy/current-fact inverse, predecessor writes/reforward, native/UI/business outcomes and final eleven-table reconciliation remain unproved by this proposal.
