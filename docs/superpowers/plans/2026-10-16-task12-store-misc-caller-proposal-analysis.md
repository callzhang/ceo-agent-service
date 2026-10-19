# Four residual Tasks families: unselected Store caller proposal

The adjacent patch is an incremental **unselected caller proposal** for `business_object_tasks`, `oa_notification_events`, `okr_review_items` and `wechat_memory_import_jobs`. It applies **after** the exact committed existing-eight Store proposal, never directly to current application source. All application files remain unchanged. This document describes a proposed source conversion with static copied-code verification; it does not establish public-method behavior or activate a release.

| Input/output | SHA256 |
| --- | --- |
| Original `app/store.py` | `ce66a4dd7302080126fb178b4dc8e3983acbc68b2f70c9107bd3e584c4207d5f` |
| After existing8 prerequisite | `f4b3b9ddf3341c48b9fe77b6fbfa81fbd376f3549fe4d086f45efe2a37950bec` |
| After this incremental proposal | `705c8f0e73ff9df17cb5f8118da96e99f0b520f9b0c4b6895e67b05a94cdd849` |
| This patch | `aeb1ffd38995428d61f9f01bf4d0a58dfdd223b510b358ff6a7c2e01976706c4` |

Prerequisite: [2026-10-16-task8-store-caller-proposal.patch](2026-10-16-task8-store-caller-proposal.patch), SHA256 `691cc165fdbaaa4e56a04b907dc6c76cbbc717c4e0c3fc9f3c31a95857cc873c`. New patch: [2026-10-16-task12-store-misc-caller-proposal.patch](2026-10-16-task12-store-misc-caller-proposal.patch). Exact per-site source SQL, parameter expressions, source method anchors and validation results are recorded in the [companion JSON](2026-10-16-task12-store-misc-caller-proposal-analysis.json). JSON SQL-line anchors refer to the prerequisite candidate; each owner also retains its original source method line.

## Source coverage and retained behavior

The independent AST/literal/JoinedStr walk of the actual pinned source finds **19 runtime owners**, two historical DDL owners and module-level manifest references. Source metadata owner lists were used for navigation; the proposal separately covers the actual mixed mutation statements, indirect query leaves and runtime-parent dictionary leaf. This is bounded static source coverage, not proof that arbitrary runtime-built SQL outside this slice is correct.

| Native operation | Count | Proposed target operation |
| --- | ---: | --- |
| Plain INSERT | 4 | Two mapping inserts, OKR item and WeChat import insert via `insert_task12` |
| INSERT OR IGNORE | 2 | Mapping alias and OA notification insert via the same helper with `ignore=True` |
| Payload UPDATE | 5 | Four OA CAS/bulk updates and WeChat terminal update retain native statement through pure `task12_set_parts` |
| Physical-parent bulk UPDATE | 1 | Native single `UPDATE tasks SET parent_id=?` with identical mapping-parent membership predicate |
| Mapping DELETE | 1 | Native single key-scoped target DELETE |
| Read expressions | 16 | 19 `task12_projection` occurrences: mapping14, OA3, WeChat2 |

The six inserts retain the exact source values, omitted defaults, plain/IGNORE choice and caller transaction context. Existing current callers in these four families contain **no INSERT OR REPLACE and no RETURNING contract**. This proposal adds neither. Their source replacement/hidden-ROWID semantics remain the canonical Task12 writer/declaration contract and require full-release copy/inverse acceptance; a physical Tasks ID must never be substituted for a source ROWID. The two original callers consuming `cursor.lastrowid` remain `record_okr_review_item` and `begin_wechat_memory_import_job`; `insert_task12` returns source-shaped independent AUTO IDs there. OA insert readback retains the same natural-key lookup and returns its source public ID even after IGNORE.

Business mapping retains all four source fields: nullable `business_object_key`, physical `reply_task_id`, independent `created_at` and `updated_at`. `task12_projection` aliases `parent_id` back to `reply_task_id`; its hidden source ROWID remains `legacy_key`, distinct from the physical mapping row ID. The provisional-key path still deletes its provisional mapping and inserts the new key; it does not turn this into a key-only payload UPDATE. The source allocator determines the inserted hidden ROWID, including native reuse when the deleted row was the maximum. Pending single-chat merge still repoints every alias in **one bulk statement** before duplicate reply removal, with no timestamp rewrite. Multiple aliases per parent, keys differing from the parent canonical key and repeated historical reply keys remain representable. No global reply-history business-key unique constraint is proposed.

Mapping source aliases remain unchanged in `get_reply_task_for_business_object`, the needs-human reconciliation, failed supersession, all three unresolved-Attention query forms and `_operation_logs_base_query`. Those reads join the physical reply parent to the existing-eight source projection exactly as before. The mixed existing8 UPDATE with a mapping EXISTS remains one native statement; only its mapping relation changes.

OA `task_id` remains the provider node coordinate, including adoption from an empty node and exact process/node/status predicates. The four updates preserve original parameter order, pending/adopted membership, required-reply flag, zero-attempt claim, sending-attempt CAS, timestamps, rowcounts and failure reset. Their finite generated-field WHERE expressions and pure payload SET run through native SQL with existing caller transaction ownership. Payload trigger message JSON remains present for delivery.

OKR item `request_id` remains the existing public request value with no invented parent FK. Both objective and KR weights retain native REAL-affinity storage/projection through the accepted Task12 writer/declaration; the proposal introduces no plain JSON read that would bypass its REAL projection barrier. Adopted `item_json` remains stored, and no terminal/body-retirement policy is added.

WeChat begin retains nonblank input validation and independent import IDs without deduplication. Finish retains the same read, same-terminal no-op, conflicting-terminal rejection and running-only native UPDATE. `_runtime_operation_parent_exists` now reads its source public import job ID through `task12_projection`, preserving running-state membership and the existing workload-key parsing.

## Verification actually performed

A fresh task-owned code copy was built directly from source SHA `ce66a4dd...`; `git apply --check` and actual application succeeded sequentially for the exact prerequisite and this proposal. The resulting source is byte-identical to the pinned candidate above. The pinned Python3.13 executable `/Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python` parsed and compiled the candidate in memory, imported it without invoking any constructor, and ran Ruff successfully.

All **779 direct class-method definitions** (776 `AutoReplyStore`, three `CodexSessionLock`) retain their signatures, decorators and return annotations. Nineteen method bodies change against the existing-eight candidate; the other760 class-method ASTs remain exact. Of the91 bodies changed by the prerequisite,86 remain exact; five mixed methods are extended: `_ensure_business_reply_task`, `_runtime_operation_parent_exists`, `reconcile_valid_needs_human_projections`, `replace_pending_single_chat_reply_task_trigger` and `skip_failed_reply_tasks_superseded_by_terminal_business_object`. Every existing `task8_*` helper-call AST remains exact, including within those five. All non-Store module statements remain exact apart from the necessary new import.

Existing context-manager expressions, explicit transaction/commit/rollback/savepoint statements and native transaction ownership are AST-identical. All five new SET calls use empty bound-value maps and exact source RHS expressions with no helper-bound parameters; source placeholder order stays in the native statement and original parameters remain unchanged. Six insert helper calls retain source-shaped result IDs. Zero native four-family FROM/JOIN/UPDATE/INTO references remain in current Store runtime literal/JoinedStr leaves. Historical declarations and manifest names are intentionally excluded from that residual claim and listed below.

No public DAO call or SQLite database was executed for this proposal. There is no evidence here for native constraint/error priority, live CAS races, allocator behavior through these public methods, source-target data equality or provider outcomes. Existing canonical writer tests do not substitute for that root-owned paired acceptance. No real DB, provider, native CLI, network, main, push, service or deploy action occurred.

## Constructor, manifest and external dependency obligations

- `_initialize` retains its entire historical multi-statement schema script, separate WeChat create/index SQL, and all original migration calls. `_migrate_oa_notification_events` retains its historical OA table/index declarations. Root must explicitly order/select current final schema construction and retirement; this patch does not rewrite source history or create compatibility paths.
- Module required-table/index/column manifests retain native names. Root must replace the final current manifest coherently with complete Tasks20 selection while preserving historical profiles. This proposal changes only its added Task12 import at module level.
- The existing8 obligations for `_initialize` and `_migrate_removed_runtime` remain. Store/Email connection constructors must register the accepted exact scalar functions when selecting the complete paired cutover; they are unchanged here.
- Direct business-map SQL also remains outside this owned Store slice: `app/quality_gate.py:530`, `app/reply_task_deferral.py:148`, `app/rule_question_retirement.py:68`, and `app/audit_web.py:2765,3143,3180,3484,3902`. Root must include these current readers before table retirement. Other modules calling the public methods retain their public contracts; this proposal does not exercise their provider paths.
- Feedback four, Workbench retained children two, Email two, complete constructors/source-copy/current-fact inverse/predecessor writes/reforward/public/native/UI/final11 acceptance remain independently owned root integration work. No release is selected by this document or patch.

## Exact changed methods

- `AutoReplyStore._ensure_business_reply_task` (original source line 9094).
- `AutoReplyStore._operation_logs_base_query` (original source line 29687).
- `AutoReplyStore._runtime_operation_parent_exists` (original source line 10344).
- `AutoReplyStore.adopt_oa_notification_events` (original source line 9329).
- `AutoReplyStore.begin_wechat_memory_import_job` (original source line 27984).
- `AutoReplyStore.claim_oa_reminder_target` (original source line 9376).
- `AutoReplyStore.count_current_unresolved_problem_attempts` (original source line 24617).
- `AutoReplyStore.finish_wechat_memory_import_job` (original source line 28001).
- `AutoReplyStore.get_reply_task_for_business_object` (original source line 14203).
- `AutoReplyStore.list_current_unresolved_problem_attempt_summaries` (original source line 24498).
- `AutoReplyStore.list_current_unresolved_problem_attempts` (original source line 24371).
- `AutoReplyStore.list_pending_oa_reminder_targets` (original source line 9349).
- `AutoReplyStore.mark_oa_reminder_failed` (original source line 9405).
- `AutoReplyStore.mark_oa_reminder_sent` (original source line 9390).
- `AutoReplyStore.reconcile_valid_needs_human_projections` (original source line 23305).
- `AutoReplyStore.record_oa_notification_event` (original source line 9266).
- `AutoReplyStore.record_okr_review_item` (original source line 17728).
- `AutoReplyStore.replace_pending_single_chat_reply_task_trigger` (original source line 18423).
- `AutoReplyStore.skip_failed_reply_tasks_superseded_by_terminal_business_object` (original source line 23405).
