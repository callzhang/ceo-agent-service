# Task12 Email native caller proposal, 2026-10-16

**Complete, unselected runtime caller proposal.** [Executable patch](2026-10-16-task12-email-native-caller-proposal.patch) changes only `app/email_task_adapter.py` and the five scoped runtime methods in `app/email_store.py`. [Machine analysis](2026-10-16-task12-email-native-caller-proposal-analysis.json) pins source/candidate/canonical hashes, every function AST, all changed SQL bindings, and static evidence. The prior [source-gap analysis](2026-10-16-task12-email-caller-proposal-analysis.md) remains intact and describes the old interface; the new canonical native parts interface supplies the required capabilities.

The patch depends on exact canonical `2026-10-16-task12-native-parts-candidate.py`, SHA-256 `8a7a84db53006065697ae44a2e39dc27a2008ca2687b6ddfc54e23ff9cc1729a`. It uses `task12_insert_parts`, `task12_set_parts`, the unchanged `insert_task12` signature, source projections and declared typed source getters. No new wrapper, transaction owner or policy is introduced.

## Complete runtime scope

| Module | Changed bodies | Native statements converted | Other functions |
|---|---:|---|---:|
| EmailStore | 5 | Historical enqueue UPSERT, two historical-list SELECTs, historical state UPDATE, classification durable-row SELECT, classification progress SELECT | 347 exact |
| EmailClassificationTaskAdapter module | 11 | Two INSERTs, five UPDATEs, two DELETEs, ten SELECTs | 42 exact |

The adapter bodies are ensure_task, open_offline_task, close_offline_task, purge_offline_tasks, has_stable_record, stable_provider_uids, get_task, retry_failed_tasks, claim_next, _expire_running_claims and _finish. The five EmailStore bodies are _validate_durable_rows, enqueue_historical_page, list_historical_candidates, set_historical_candidate_state and email_processing_progress. The 25 direct source SQL sites are all covered. Delegating adapter methods such as recover_running_tasks, complete and fail keep their original bodies and call the converted methods.

Every one of the 405 source function signatures is exact. Sixteen function bodies change; 389 function ASTs are exact, including both constructors, connection contexts, historical factories/migrations, identity/hash/input-compaction routines, hydration, other classes, and unrelated Email/adapter functionality. Original module-level declarations and imports remain exact; only Task12/Task20 imports and finite projection/column maps are added.

## Statement and result contracts

**Historical enqueue** retains the existing BEGIN IMMEDIATE, per-candidate validation/digest/locator derivation, candidate loop, and final traversal `upsert_state` in the same connection context. Prepared INSERT is followed by a native ON CONFLICT tail over the four exact generated source-key columns. Its `WHERE record_type='email_historical_candidates'` matches the exact partial UNIQUE index declaration. The DO UPDATE SET encodes only normalized_text, provider_message_json, uidvalidity, uid and updated_at from `excluded` generated source fields. The old physical row and legacy source ROWID, state, reason, attempted_at and next_retry_at survive an update conflict. The method still observes no cursor result and returns None. No lookup/split update, REPLACE or second writer replaces this UPSERT.

**Historical state** uses pure SET preparation followed by one native UPDATE with the original four business-key conditions expressed through declared typed getters. Pure preparation performs no SQL, so the native UPDATE remains first DML and owns its existing implicit BEGIN. It still uses rowcount and raises the missing-row ValueError after the committing `_connect` context. Pending/deferred/terminal validation, the 60-second retry calculation, timestamp calls and None return remain exact. Historical list queries preserve both quota/sort/limit/bindings and source-shaped returned fields; the source column named normalized_text remains the digest exposed as normalized_input_hash.

**Classification insertion** keeps exact public TEXT task_id, channel/stable identity, omitted native defaults and explicit timestamp calls. Ensure still rechecks under BEGIN IMMEDIATE and validates immutable payload/digest. Offline INSERT OR REPLACE uses canonical `insert_task12(..., replace=True)`; its accepted native zero-row allocator UPDATE acquires an implicit write transaction without committing. The helper retains native source ROWID replacement while physical identity remains an internal coordinate.

**Classification mutation** uses pure SET parts and raw native SQL with `record_type` plus typed source-field conditions. Explicit retry remains one multirow UPDATE returning its native rowcount. Close/purge remain native DELETEs, with exact task_id/owner and `{}` input predicates; purge returns native multirow rowcount. Claim still rechecks under its existing immediate BEGIN and sets generation+1 and attempt_count+1 as SQL expressions on the original row. The source expiry loop remains a loop and returns the count selected; no new source-row loop is introduced for a bulk statement.

**Completion/failure** retains task_id + running + owner + generation CAS. The done branch alone begins its original immediate transaction, verifies the lease, computes existing compact input, and writes it with completion. Non-done finish preserves implicit UPDATE transaction and retains input. Result JSON, retry timing, errors and lease fields remain the original source values. Read projections expose source task fields without leaking global physical IDs. No scoped caller currently observes hidden ROWID, so these scoped SELECTs do not add a rowid field.

The classification progress query preserves the existing status filter `('pending','processing')` exactly, including that spelling; this storage conversion does not change status behavior. Durable-row validation retains its original JSON/redaction/lease checks and reads source fields through the finite projection.

## Static validation and exact boundary

The fixed project Python, with PYTHONDONTWRITEBYTECODE=1, compiled both candidate modules and imported a complete copied app with the exact canonical Task12 module. No constructor or public DAO method ran. Ruff passed both candidate modules. The patch passed apply-check, apply with exact candidate-byte comparison, reverse-check and reverse with exact original-byte comparison in the owned temporary directory.

AST validation compares every function, including duplicate source method definitions by occurrence. All signatures and all 389 unrelated function ASTs are exact. Each changed body's explicit transaction SQL sequence, rowcount/fetchone/fetchall/commit/rollback counts, and actual For/While loop markers remain equal. All original module declarations and other classes are exact. Exact source/candidate SQL calls and parameter expressions are in the JSON. These are static checks, not a behavioral equivalence claim.

Root must register the existing ceo_native_json codec at the current Email connection boundary before selecting this patch; claim counter and excluded-field expressions need that existing codec. This patch does not change `_open_connection` or register a function implicitly. Root must also select exact current Tasks20 declaration/manifest and both native queue retirements. Current schema validation maps/fresh source factories and every original historical migration remain untouched here.

The Store runtime classification parent check, Attention source-ROWID/local SQL, generic console queue/presence/metadata helpers, optional quality-gate source checks and `_count` forwarding, and explicit maintenance compaction remain external root-owned caller obligations. Their exact source inventory is preserved in the prior gap analysis; this patch does not claim full service caller closure.

Next acceptance belongs to root: actual/fresh synthetic public DAO differential calls; errors/ABORT/rollback and rowcounts; immutable input, owner/generation CAS, done-only compaction, offline replacement, deferred ordering, and enqueue/traversal cursor coordination; then combined migration/current-fact inverse/genuine predecessor/reforward/native/UI/final11 verification. No copied migration or public DAO acceptance is claimed by these static checks.

Only proposal documents and the own claim row are committed. No application/test edits, constructor/schema selection, real database, provider, native CLI, network, main, push or deploy action occurred. Task-owned temporary code copies and pointer file are removed after validation and artifact generation.
