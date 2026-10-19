# Feedback four: complete unselected Store runtime caller proposal

The adjacent patch is a complete **unselected incremental caller proposal** for `feedback_iteration_decisions`, `feedback_processing_batches`, `feedback_processing_items` and `feedback_processing_rounds`, covering their existing51source fields. It applies only after the exact existing8 Store proposal and the exact Task12 misc Store proposal, in that order. Application files remain unchanged; this artifact provides copied-code/static verification, not public-method behavioral acceptance or release selection.

| Boundary | SHA256 |
| --- | --- |
| Current unchanged application Store | `ce66a4dd7302080126fb178b4dc8e3983acbc68b2f70c9107bd3e584c4207d5f` |
| Required incremental Store input | `705c8f0e73ff9df17cb5f8118da96e99f0b520f9b0c4b6895e67b05a94cdd849` |
| Proposed output Store | `13dbf3724919fbeac89b7d2aca895ca2651339b3fcb35a322e56ef10d108d55b` |
| This patch | `9bf3323406b39fc06fff84496bd6fb2bcda6c3eb990e96a550b878e433a8105d` |
| Required separate canonical INSERT-expression helper | `7174fc6da84370fda1eda1733fd4e3337cae0bd0a7c776a665964efcc79c8833` |

Prerequisites: [existing8 Store patch](2026-10-16-task8-store-caller-proposal.patch), SHA691cc165fdbaaa4e56a04b907dc6c76cbbc717c4e0c3fc9f3c31a95857cc873c; [Task12 misc Store patch](2026-10-16-task12-store-misc-caller-proposal.patch), SHAaeb1ffd38995428d61f9f01bf4d0a58dfdd223b510b358ff6a7c2e01976706c4. New [feedback patch](2026-10-16-task12-feedback-caller-proposal.patch) and [complete source/anchor/validation JSON](2026-10-16-task12-feedback-caller-proposal-analysis.json). `source_line` anchors refer to unchanged real application source; `base_line` anchors refer to the prerequisite-composed Store.

## Exact scope and helper boundary

An independent AST/literal/JoinedStr walk of actual current source found102feedback-family/coupled leaves:85current runtime leaves and17historical/profile leaves. The current conversion scope is61SQL leaves across20methods:7INSERT (4native UPSERT,2plain AUTO INSERT,1IGNORE),11UPDATE (including2finite dynamic evidence SET statements), and43read leaves. Another24runtime SQL leaves for only the three retained families remain byte/AST-exact. This walk also covers the formatted conflict query in batch creation, the finite assignments/args builder and all validators reading item/round/batch/decision lineage. No current feedback SQL uses hidden ROWID, so this proposal never requests `include_rowid`.

The first helper API could not represent the three actual native INSERT expressions at source19934 (`upsert_feedback_event`),20322 (`create_feedback_processing_batch`) and22539 (`resolve_feedback_event`). The first two derive status/resolved_at from feedback_events inside the INSERT; the third explicitly evaluates current_timestamp in the INSERT. Six native source probes over actual/fresh item declarations plus the same frozen actual retained event declaration confirm their source behavior; two omitted-default controls differ, and two old-helper keyword rejections prove that missing capability. These are source/diagnostic probes, not public-method target acceptance.

Root separately supplied [the complete native INSERT-expression helper candidate](2026-10-16-task12-insert-expression-candidate.py) with `insert_sql=None, insert_parameters=()`. The exact canonical helper above was copied into the task-owned code copy for compile/import/API verification. It excludes these fields from source default substitution and encodes each scalar once inside the final INSERT with existing ceo_native_json; expression parameters follow the encoded bound payload. Only status/resolved_at need this extension here; all public identity/parent dependencies remain bound. The helper's transfer/review/acceptance remains root-owned and this proposal depends on it. There is no SQL string surgery, extra scalar pre-read, source-clock substitution, row-loop replacement, fallback or dual writer.

## Preserved runtime contracts

All source reads use source-shaped `task12_projection` relations, retaining existing aliases, column names/order, membership predicates, grouping and sorting. Decision and round public AUTO IDs remain independent source streams carried through legacy_key; they are never confused with physical Tasks IDs. The two original lastrowid consumers now read `int(_task12_insert.source_rowid)` after the same native execute succeeds. The original unused physical cursor binding is removed at those two sites; no source rowcount branch consumes it. Child decision_id/feedback_key/round_id continue to receive the exact original source-public values. Decision insert parent_id resolves the bound public batch key through the existing composite parent mechanism.

Every native UPDATE uses pure task12_set_parts with empty bound-value maps and native scalar update_sql; the original UPDATE argument tuples/lists are untouched. Eleven UPDATE statements and four UPSERT SET fragments produce15such calls with zero helper-bound parameters. Public round ID WHERE checks use their generated source column; item and batch keys, statuses, zero-current-round sentinel and all association/receipt CAS predicates keep source values. Native bulk statements, old-row RHS and conditional UPSERT logic remain in one statement with original rowcount checks. No decision UPDATE/DELETE is introduced; existing append-only declaration/guards remain untouched.

The dynamic evidence builder retains the existing finite fields, value validation, source JSON serialization, equality checks, early returns and args order. Its assignments container changes from SQL-string list to field→native-SQL dict so each original scalar expression is encoded in the same UPDATE; it does not change processing status or add evidence policy. Round and item receive their own typed WHERE columns and the same existing arguments.

Claim retains enabled/membership/source checks, same-batch idempotency, prior history validation, max(round_number)+1, receipt_version2, pending/current_round_id0 CAS, transition append and batch advancement. Association and evidence patch retain exact current item/round/batch membership and both rowcount checks. Resolution retains lineage/source/decision/receipt validation, one source timestamp, round+item writes, transition append, source feedback resolution and batch CAS. Reopen retains latest resolved receipt validation, one-time reopen stamp, preserved old round evidence, resolved→pending transition, source sentinels/current-round0 and source-event reopen. All existing exception nodes and messages remain AST-exact.

`feedback_iteration_decision_items`, `feedback_processing_transitions` and `feedback_events` remain separate retained native tables in this proposal. Their INSERT/UPDATEs and retained-only readers remain exact; mixed joins replace only a four-kind parent relation with its source-shaped Tasks projection. This does not merge the three retained families into Actions, create new audit/safety rules, or alter business statuses.

## Verification performed

A task-owned copy of app code was built with the exact prerequisite-composed Store and pinned helper candidate. All three Store patches apply in order with git apply --check; resulting bytes match the proposed output. Reversing the feedback patch restores the exact705c8f0eincremental input; reversing all three restores the exactce66a4ddcurrent source. Pinned Python3.13.12 parses/compiles the candidate and imports it without any constructor; copied helper import path and insert-expression API are asserted. Ruff passes for both copied Store and canonical helper.

All779direct class methods retain signatures, decorators, return annotations and type parameters. Exactly20method bodies change against the prerequisite candidate;759unrelated method ASTs are exact. All114prior task8 helper-call ASTs remain exact. Existing transaction context expressions, explicit transaction/commit/rollback/savepoint statements, native declaration constants, historical/profile methods and every raise node are exact. All24retained-only current SQL leaf ASTs remain exact. A scoped residual scan finds zero original four-family FROM/JOIN/UPDATE/INTO relation references inside current runtime owners; source/history/module profile names are deliberately not covered by that claim.

No current public DAO method, final schema constructor, real database, native trajectory body, provider, native CLI, network, service, UI, outbound action, main merge, push or deploy was exercised. Source probes and copied-code/static checks do not prove SQL constraint/error priority, CAS races, allocator clocks through public methods, copied rows, retained-child migration correctness or external outcomes. Root owns actual/fresh public lifecycle proof and whole-release acceptance.

## Exact unconverted integration obligations

- `_initialize` retains the full original schema script and source feedback item/round alterations, receipt-version backfill and `_backfill_feedback_processing_rounds` call. The final paired fresh/current constructor must stop recreating retired feedback families and order/select Tasks20 coherently; source profiles must keep their original native history.
- `_feedback_processing_round_backfill_needed`, `_backfill_feedback_processing_rounds`, `_feedback_processing_round_storage_is_valid`, `_feedback_processing_round_guard_state_is_valid` and `_schema_manifest_is_current_in_connection` are intentionally unchanged. Root must reconcile current schema validation with Tasks20 selection while preserving historical/source profiles. No compatibility layer is introduced here.
- Module required-table/index/column manifests, source round CHECK/guard DDL and source decision/decision-item append-only declarations remain unchanged. They belong to complete current constructor/schema selection, with native profiles preserved.
- The retained decision-items declaration still has three native public-source FKs. Root needs a migration companion translating decision_id, feedback_key and round_id to the already declared full-UNIQUE, kind-restricted Tasks20 public aliases, preserving all other child DDL, indices and append-only triggers. No child source ID or child declaration changes in this runtime patch.
- Both connection constructors need the exact scalar codec only with complete paired activation. Remaining Email/Workbench callers, copy/current-fact inverse/true predecessor writes/reforward, native CLI/fake-outbound/UI/receipts and final10core+1Emailledger acceptance remain root-owned. The existing8/misc integration obligations remain unchanged.

## Exact changed methods

- `AutoReplyStore.upsert_feedback_event`.
- `AutoReplyStore.record_feedback_iteration_decision`.
- `AutoReplyStore.list_feedback_iteration_decisions`.
- `AutoReplyStore.create_feedback_processing_batch`.
- `AutoReplyStore.claim_feedback_processing_items`.
- `AutoReplyStore.reopen_feedback_processing_item`.
- `AutoReplyStore.list_feedback_processing_rounds`.
- `AutoReplyStore.list_feedback_processing_rounds_for_batch`.
- `AutoReplyStore.associate_feedback_processing_turn`.
- `AutoReplyStore.get_feedback_processing_batch`.
- `AutoReplyStore.get_feedback_processing_item`.
- `AutoReplyStore.patch_feedback_processing_item_evidence`.
- `AutoReplyStore._validate_current_feedback_processing_batch_lineage`.
- `AutoReplyStore._validate_resolved_feedback_processing_batch`.
- `AutoReplyStore._resolution_feedback_iteration_decision`.
- `AutoReplyStore.resolve_feedback_processing_batch`.
- `AutoReplyStore.list_user_feedback_items`.
- `AutoReplyStore.list_user_feedback_items_by_keys`.
- `AutoReplyStore.count_pending_user_feedback_items`.
- `AutoReplyStore.resolve_feedback_event`.
