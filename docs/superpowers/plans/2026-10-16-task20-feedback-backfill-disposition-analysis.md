# Feedback startup round-backfill disposition

Root approved the following scoped disposition after this research: explicitly retire the unused `resolve_feedback_event` direct-resolution API and the recurring startup round self-heal from the selected Tasks20 runtime. Preserve genuine historical source/predecessor code for old profiles and inverse tests. No new bulk helper, per-row replacement, runtime guard, business-state normalization or compatibility API is proposed. **Keep `create_feedback_processing_batch`** and normal batch APIs. The approved removal also covers the two obsolete private native guard-replacement methods and old current initializer feedback item seeding; historical predecessor code remains unchanged.

This recommendation depends on the actual current endpoint contract, source writers and data evidence below. It is a deliberate cancellation of obsolete DAO/startup behavior, not a claim that removing it preserves every possible Python method sequence. Root owns implementation/test changes; this document records the scoped decision without selecting runtime code.

The companion JSON pins16 source/artifact inputs,37 source/test owners, exact original SQL predicates, read-only aggregate snapshots and44 expected in-memory public-method checkpoints. No application/test/root-candidate edits, constructor execution, database-file write, provider/network/native CLI action or runtime selection occurred.

## Current and frozen data evidence

The current production checkout was `44ee956d10a1360c1ba9b44bf08f4c61e630beb4` before and after the read snapshot; its markers were Store `2026-10-13.5` / Email51. The database was opened with URI `mode=ro`, `query_only=on` and one explicit read transaction, then rolled back/closed. Only counts and schema/marker metadata were fetched; no feedback identity, evidence value, body or source row was returned.

| Original source predicate / aggregate | Current production | Frozen970a source |
| --- | ---: | ---: |
| Feedback processing items | 48 | 48 |
| Resolved items | 48 | 48 |
| Feedback processing rounds | 3 | 3 |
| Resolved rounds | 3 | 3 |
| `_feedback_processing_round_backfill_needed` exact predicate | 0 | 0 |
| Original bulk INSERT no-history eligibility | 0 | 0 |
| Original bulk UPDATE WHERE eligibility | 0 | 0 |

The current snapshot time is **2026-10-16T20:29:03.203628+00:00**. The frozen file `/private/tmp/ceo-business-combined-actual-u6i3ldg0/source.sqlite3` SHA256 was `970a909a9cfcb4fe604d2111d5572560d4744978df20fb317c4e433059566320` both before and after its read. Current production and frozen source have separate evidence entries; the live service can write after the snapshot.

A second aggregate-only snapshot at20:32:49UTC also checked removal of initializer item seeding: current production44ee and frozen970a each have48 feedback events, **0 missing item projections** by `feedback_events LEFT JOIN feedback_processing_items`, and **0 rows matching the original initializer resolved-projection UPDATE predicate**. The JSON retains those exact original INSERT/UPDATE statements and count queries. `upsert_feedback_event` is the only current event INSERT/upsert writer and pairs event/item creation under one transaction. Other event resolution updates are atomic batch resolution, atomic reopen, and the obsolete direct resolver being retired. No projection normalization was executed.

The JSON retains the exact original queries from AST literals. Count wrappers replace only the outer SELECT list/remove the outer LIMIT; their business predicates are unchanged. Zero eligibility does not prove every arbitrary raw SQL state is impossible. It establishes that neither inspected source needs this backfill now.

## What the startup helper actually does

`store.py:1911-1995` considers only processing/resolved item projections carrying a meaningful batch, association, execution ID, commit, test/restart/health evidence or note. Pending projections and resolved feedback without those fields are excluded. For eligible rows it detects missing history, cross-owned/stale/mismatched positive pointers, or a zero pointer that has one exact latest matching round.

`_backfill_feedback_processing_rounds:1998-2149` executes two native bulk statements. It first inserts a **receipt_version1** round with `INSERT OR IGNORE ... SELECT` only when no round history exists, copying original projection fields/timestamps. It then sets the item pointer to the one exact latest round matching feedback key, batch and status, or zero when matching history is absent/ambiguous/stale. It does not synthesize claim/resolution transitions, finish batches, validate/recreate receipt closure, or resolve a business workflow.

`_reconcile_feedback_processing_round_backfill:2151-2157` checks outside a write transaction and rechecks under the original immediate transaction before those statements. `_ensure_initialized:1882` calls it on a structurally current schema; `_initialize:3830` calls the original bulk helper during source bootstrap. Thus the behavior is currently observable beyond a schema-version change. The source history lookup identifies commit `1303e40c92bcd7b7db0b471a626bda267cf32f49` (2026-08-30, `fix: enforce feedback round migration invariants`) as the introduction of current-schema reconciliation.

## Complete current item/round writer closure

A bounded lexical/AST scan of `app/` and `scripts/` found the source table mutations in Store and the explicit schema-transfer component; no second current business DAO writes these native feedback tables. Source triggers are integer/append-only guards, not hidden repair writers. The9 application call sites reach upsert, claim, association, evidence patch, reopen or batch resolution. Their exact locations/owners are in the JSON.

| Store owner | Relevant behavior and transaction | Can normal current calls require startup backfill? |
| --- | --- | --- |
| `upsert_feedback_event:19876-19948` | `_connect` transaction updates source event and seeds pending item; replay keeps existing processing projection unless the source was already resolved. | Fresh feedback has no processing evidence. A previously resolved source can propagate old/manual mutation, but upsert alone does not create meaningful processing-without-history. |
| `create_feedback_processing_batch:20251-20352` | Immediate transaction checks unresolved source/conflicts, then seeds pending membership. No app/scripts call sites;19 test calls. | Pending state is excluded. Composing it with the legacy direct resolver is a demonstrated exception below. |
| `claim_feedback_processing_items:20354-20557` | Immediate transaction inserts receipt_version2 round, writes positive item pointer and processing state, claim transition and batch state together; retry validates existing lineage. | It is the normal processing-state producer and already owns complete round creation. |
| `associate_feedback_processing_turn:20787-20873` | Requires exact positive pointer/processing round; updates round and item in one immediate transaction. | No. A missing current round is rejected, not created. |
| `patch_feedback_processing_item_evidence:20895-21000` | Rejects status changes, requires exact positive processing round, updates evidence on round/item together. | No. It cannot manufacture processing/resolved state without history. |
| `resolve_feedback_processing_batch:21819-22144` | Validates lineage/evidence; resolves rounds/items/events/batch in one immediate transaction. No-evidence processing shortcut returnsFalse. | No normal unresolved projection is committed halfway through this method. |
| `reopen_feedback_processing_item:20559-20732` | Validates resolved history; records reopen/transition and clears pointer/evidence into pending atomically. | Pending is intentionally excluded; next claim creates the next round atomically. |
| `resolve_feedback_event:22523-22550` | Legacy direct resolver updates event/item to resolved in one `_connect` transaction without changing round, pointer or batch. | **Yes, if retained and called directly.** It has zero app/scripts callers or name-string dispatch references, and both direct-resolution endpoints already reject it. |
| `_initialize` and original bulk helper | Historical event-to-item projection and round-upgrade/pointer repair. | Historical/incomplete profiles can need version1 reconstruction; this is distinct from normal current batch writes. |

Crash inference: claim/association/evidence/resolution/reopen pair writes under one SQLite transaction, so a failure between the paired statements should roll back rather than leave a durable projection without its round. In-memory injected native ABORTs proved whole-transaction rollback for all5 methods on both source profiles. This is statement-failure evidence, not an operating-system process-kill/durability experiment. A crash **between** public claim and association calls leaves the complete claimed round already durable and does not require round synthesis.

## Current endpoints and the five legacy test calls

`registration.py:1481-1483` implements `/api/console/feedback/{feedback_id}/resolve` as409 `feedback_batch_required`, with no Store mutation. `audit_web.py:9507-9513` implements the old form handler the same way and discards its Store/body arguments. Current resolution uses the batch endpoint and `resolve_feedback_processing_batch` at registration1440. `tests/test_console_web_api.py:481` and `tests/test_audit_web.py:3456` assert those endpoint refusals.

No exact `resolve_feedback_event` or `create_feedback_processing_batch` string literal appears in runtime app/scripts; reviewed `getattr(store,...)` and method-name/dispatch matches do not route to either method. README/operational/API documentation has no invocation; the references are frozen redesign source metadata, helper/proposal plans and acceptance records. Arbitrary external Python/operator use remains outside a static repository proof.

The5 direct resolver test calls are **not all fixture-only**:

- `test_feedback_processing.py:1802`: resolved-key setup for claim rejection.
- `test_feedback_processing.py:1879`: source-resolution setup for legacy pending-batch idempotency.
- `test_feedback_processing.py:1903`: the obsolete direct DAO behavior assertion itself.
- `test_audit_web.py:3449`: resolved-feedback setup for the navigation badge.
- `test_console_web_api.py:1173`: resolved-feedback setup for the filtered global pending badge.

None exercises an endpoint that actually resolves feedback directly. Root must explicitly retire the old DAO assertion, while keeping claim rejection, badges/filter counts and valid batch behavior covered through intended fixtures/current APIs. This research changes none of those tests.

## Public traces and counterexamples

Both profiles used original Store public method bodies and the original `_connect`/`_immediate_write_transaction` contexts, with only `_open_connection` pointed to a shared **in-memory** SQLite database. Actual used exact captured native table declarations. Fresh used exact current constructor table literals. Both used captured scoped indexes/triggers and an empty real Config declaration for the capability query. Store was allocated with `__new__`; constructors were never called.

Each profile passed22 expected checkpoints: normal upsert, pending membership, claim/retry, association, evidence, valid batch resolve/retry, reopen/reclaim and source replay stayed ineligible; injected failures after round writes but before paired item writes preserved the entire synthetic feedback snapshot for5 methods. Synthetic snapshots were compared internally; only booleans/counts are retained.

Two sequences of **original public methods**, without raw item/round corruption, establish why cancellation must be explicit:

1. Upsert → claim → evidence patch → `resolve_feedback_event`: exact startup predicate becomes true. Original backfill adds0 rounds, clears the positive pointer to0, and leaves the batch **processing**. It does not complete or repair the batch workflow.
2. Upsert → create pending batch → `resolve_feedback_event`: exact startup predicate becomes true with no round history. Original backfill creates1 version1 round and points to it, but leaves the batch **pending**. The old direct API strands batch state.

These confirm an observable legacy API/startup interaction, while the actual current endpoints already disallow that user flow. They support removing the obsolete direct API together with startup repair; they do not support silently dropping backfill while claiming all retained public method behavior is unchanged.

## Historical test/profile disposition and final boundary

The focused source tests already identify their role. `test_feedback_round_backfill_preserves_legacy_receipts_and_source`1065 deletes round/transition tables and the pointer column. `test_feedback_round_backfill_self_heals_current_schema_and_interruption`1264 explicitly simulates a **Task1-era interrupted migration** by deleting round/transition rows and clearing pointers. Ownership/ambiguity tests1441/1630 and stale-latest reconciliation3218 inject raw row/schema corruption. The migrated-version1 lifecycle test4847 reconstructs old missing tables and verifies legacy receipt continuation. These are valuable historical source/migration tests; they do not show current supported writers committing split item/round state. Tests were inventoried, not run in this research.

Keep genuine archived predecessor code unchanged so inverse tests still exercise its real historical constructor/backfill. Do not introduce a compatibility method in selected new runtime just to serve old test setup. No current source data needs normalization: both observed source snapshots have all three original predicates0. The migration must preserve current facts exactly. At the final quiet-copy boundary, rerun these exact predicates as **validation evidence**. If any becomes nonzero, report the missing business-state proof for root resolution; do not silently repair/discard it or add a business guard.

The evidence establishes source behavior, aggregate state and bounded native public-method transactions. It does not establish final constructor selection, a deployed cancellation, full migration/inverse acceptance, provider outcome or a current production change. Root records the approved disposition in the parent plan and owns all implementation. The selected runtime removal covers the3backfill methods/calls, direct resolver, obsolete native guard replacers and initializer seeding; `create_feedback_processing_batch` stays.
