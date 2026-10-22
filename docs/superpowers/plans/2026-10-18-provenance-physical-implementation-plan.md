# Physical Provenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the intermediate body-bearing `business_source_documents` with body-free inline Signal bindings and one `provenance` row per genuinely shared exact source version, while preserving current business facts, IDs, links, receipts, and a rehearsed recovery path.

**Architecture:** Keep `business_task_signals` as the current Actions `record_type`; a unique source version owns its seven source-identity scalars, exact key, SHA-256, UTF-8 length and proved coordinate in that Action. At the second Signal for the same complete eight-part identity, one writer transaction creates a `provenance` row, changes the first Action to a shared FK representation, and inserts the second Action. A unified read projection joins only shared rows to `provenance`. Source producers bind exact coordinates before a new Signal write; the paired release converts current source facts without calling providers from SQL.

**Tech Stack:** Python, SQLite, Pydantic, pytest, existing Store20/Email52 paired coordinator and Actions typed payload declarations. This plan changes the selected intermediate only after its component tests, source availability, copied-data proof, and recovery rehearsal pass. It does not select or execute a production migration.

---

## Evidence and decisions fixed for this unit

- The accepted B/C/D runtime receipt is `../specs/2026-10-18-provenance-runtime-integration-acceptance.json` (SHA-256 `5bbead634445143c40fe81733b6c9309c4e4d9cd603602bfd0aa77d1bd32776c`), tied to freeze R2 SHA-256 `b46beb4a14b046952988a9a6cbc0ebe4b0878a4569c3057a83e03f6586d7de9e`. It still reads `business_source_documents.body`; it is local, not a release.
- `app/task_source_documents.py:49-72` defines the unchanged eight-element identity `[source_type, source_ref, source_time, conversation_id, author_user_id, author_name, author_kind, evidence_text]`. Neither `source_ref` nor body hash alone identifies a version. `SourceVersionBinding.matches_body` in `app/business_source_reader.py` checks full key, SHA-256 and UTF-8 bytes.
- The current `actions` Signal carries `source_type/ref/time`, `conversation_id`, `author_user_id/name/kind`, `source_document_id`, `conversation_title`, `context_json`, `dedupe_key`, `created_at` (`app/action_business_definitions.py`). Title, context, dedupe and Signal created-at are independent per-Signal facts and remain there. The public Signal ID is the existing numeric `legacy_key` projection. All existing task/project/date/relation/Attention evidence FKs continue to refer to that ID.
- Source DDL (`app/store.py:1009-1073`) has a NOT NULL, nonblank, immutable body and an integer Signal FK. The present Actions Signal UPDATE trigger aborts *all* updates. Both declarations must change for the new representation; an empty body, hidden archive or per-Signal copy of shared identity would miss the design.
- The approved design (`../specs/2026-10-11-database-redesign-design.md`) selects ten core tables plus the Email ledger, direct FKs, and provenance only for actually shared exact versions. Keep `business_task_signals` as an existing record-type string; no generic relation or new business field is needed. Ordinary polling retention is outside this unit.
- The latest scoped human decision (`../specs/2026-10-18-provenance-original-retirement-decision.json`, SHA-256 `993ce83d9e42ca8e67f4ea4bcc54ea8aad640c3de075b9e610e55852254646ed`) permits losing originals for exactly 20 listed identity keys and 489,023 UTF-8 bytes. Match the key *and* bytes, retain facts/locator/checks/receipts, display `原文不可用`. It neither proves availability nor authorizes future loss.
- The source analysis `../../database-redesign-provenance-physical-transition-source-analysis-20261018.md` (SHA-256 `f4ce863f611e9c92ae3edc8d0c564170764515e2af0d3e315afb885fba8e0fd1`) identifies the immutable UPDATE conflict, generated-column/index changes, old container ID/allocator question, paired ordering and the new-write locator gap.

## File map and ownership

| Unit | Exact files | Responsibility |
| --- | --- | --- |
| Physical component | `app/provenance_definitions.py` (new), `app/provenance_storage.py` (new), `app/action_business_definitions.py`, `app/action_business_storage.py`, `app/action_storage.py`, `app/email_canonical_storage.py` | Exact `provenance` DDL, conditional Signal typed payload, public-ID-preserving projection, strict representation guard, collision/declaration preflight and atomic promotion. Compose with the actual canonical Email + inline Legacy owner Actions shape through a default-off builder parameter; do not build a toy Signals table. |
| Runtime writer/read | `app/store.py`, `app/business_source_reader.py`, `app/task_semantic_models.py`, `app/task_semantic_service.py`, `app/task_source_documents.py` | Source fact without numeric document alias, inline/shared projection, original reader selection, exact input-to-binding comparison and transaction-owned Signal write. Remove the transient numeric `source_document_id` from consumer/model after physical selection, never replace it with a permanent alias. |
| Producer binding | `app/task_models.py`, `app/task_scanners.py`, `app/task_report_scanner.py`, `app/minutes_owner_backfill.py`, `app/task_agent.py`, `app/native_trajectory.py`, `app/business_minutes_source_reader.py`, `app/business_weekly_source_reader.py`, `app/business_console_source_reader.py`, `app/todo_completion.py`, `app/task_console_actions.py`, `app/task_semantic_import.py` | Carry coordinates with the actual transient source, validate the exact body before Signal insertion, cover every current new-write entry path. Do not infer `codex_cli` from the misnamed `codex_session_id`. |
| Paired release | `app/action_email_schema_release.py`, `app/business_legacy_owner_storage.py` only if its exact guard dependency changes, plus current declaration selectors in `app/business_schema_release.py` and `app/email_current_schema.py` | Add physical component in the existing FK-off paired transaction, pin exact selectors/markers, and preserve canonical Email then Legacy owner composition and current-fact inverse/forward-repair ordering. Historical native declaration factories remain historical. |
| Focused proof | `tests/test_provenance_physical_storage.py` (new), `tests/test_provenance_physical_release.py` (new), `tests/test_business_source_reader.py`, `tests/test_business_minutes_source_reader.py`, `tests/test_business_weekly_source_reader.py`, `tests/test_task_semantic_store.py`, `tests/test_task_agent.py`, `tests/test_minutes_owner_backfill.py`, `tests/test_task_report_scanner.py`, `tests/test_task_evidence_read_api.py`, `tests/test_email_action_schema_release.py`, `frontend/src/pages/TaskDetailPage.test.tsx` | Real Actions shape, exact producer/reader composition, copied database round-trip, unavailable-original UI, constructor/selector and query-plan proof. |
| Truthful docs and receipts | `docs/architecture.md`, `docs/runtime-mechanism.md`, `docs/superpowers/plans/2026-10-11-database-redesign-program.md`, new versioned proof receipts | Update actual behavior with its code commit; record independent component, actual-copy, capacity, recovery and release boundaries. |

Several listed files are concurrently dirty and claimed. Before each execution task, read `docs/agent-claims.md`, claim only the precise files/hunks for that task, and stage only those hunks. This research plan does not claim or edit them.

## Exact target representation

Use one nullable structural `provenance_id` generated from the Signal payload. `provenance` contains only the seven source-identity scalars, `identity_key`, `body_sha256`, `body_utf8_bytes`, and an actual `native_provenance_json` or `provider_locator_json` when one is proved; an authorized historical loss or genuine reference-only source may have neither. It has no title, context, dedupe, Signal timestamp or body. An inline Signal contains exactly those fields and a null `provenance_id`. A shared Signal contains `provenance_id` and none of those identity/check/locator fields. Both retain independent `conversation_title`, `context_json`, `dedupe_key`, `created_at`, plus their unchanged numeric public ID. The identity key is a SHA-256 of the eight-element source key, so a body-free row cannot recompute it in SQL; the write/migration code must compute it from the transient body and compare its check fields. A `provenance` row must have at least two Signal owners at the end of each committed writer transaction; transient one-owner states inside a promotion transaction are permitted. No `first_owner`/`second_owner` business columns are introduced to simulate a deferred count constraint.

The declaration fragment to integrate with `business_action_schema_parts()` is:

```sql
create table provenance (
    id integer primary key,
    source_type text not null,
    source_ref text not null,
    source_time text not null,
    conversation_id text not null,
    author_user_id text not null,
    author_name text not null,
    author_kind text not null check(author_kind in ('human','system','agent','unknown')),
    identity_key text not null unique,
    body_sha256 text not null,
    body_utf8_bytes integer not null check(body_utf8_bytes > 0),
    native_provenance_json text,
    provider_locator_json text,
    check(native_provenance_json is null or provider_locator_json is null)
);
create index idx_provenance_source on provenance(source_type,source_ref,source_time,id);
create index idx_actions_signal_shared on actions(ba_task_signals_provenance_id,business_signal_public_id)
    where record_type='business_task_signals' and ba_task_signals_provenance_id is not null;
create unique index idx_actions_signal_inline_key on actions(ba_task_signals_identity_key)
    where record_type='business_task_signals' and ba_task_signals_provenance_id is null;
```

The complete canonical table definition must also enforce nonblank source type/ref; lower-case 64-character hex for key/hash; positive UTF-8 byte length; valid supported JSON locator shapes; the existing `dedupe_key`, context and author-kind checks; and exact FK `ba_task_signals_provenance_id -> provenance(id)`. These are representation and source-identity checks, not a new review/safety policy. Shared rows require every inline binding column to be SQL NULL; inline rows require every identity/check field non-NULL. The existing `business_signal_public_id` and dedupe unique indexes and all inbound typed FKs remain. `provenance` itself has no body column and an immutable UPDATE/DELETE guard for its logical binding; the only representation update permitted on Actions is the one-way inline-to-shared promotion with the same normalized full binding, independent facts and public ID. A shared-to-inline UPDATE, locator/check mutation, arbitrary payload UPDATE, DELETE or REPLACE still fails.

The unified SQL projection must select the binding from one owner, never duplicate shared identity in each Action. Its central expression is:

```sql
select cast(a.legacy_key as integer) as id,
       coalesce(p.source_type,a.ba_task_signals_source_type) as source_type,
       coalesce(p.source_ref,a.ba_task_signals_source_ref) as source_ref,
       coalesce(p.source_time,a.ba_task_signals_source_time) as source_time,
       coalesce(p.conversation_id,a.ba_task_signals_conversation_id) as conversation_id,
       coalesce(p.author_user_id,a.ba_task_signals_author_user_id) as author_user_id,
       coalesce(p.author_name,a.ba_task_signals_author_name) as author_name,
       coalesce(p.author_kind,a.ba_task_signals_author_kind) as author_kind,
       coalesce(p.identity_key,a.ba_task_signals_identity_key) as identity_key,
       coalesce(p.body_sha256,a.ba_task_signals_body_sha256) as body_sha256,
       coalesce(p.body_utf8_bytes,a.ba_task_signals_body_utf8_bytes) as body_utf8_bytes,
       coalesce(p.native_provenance_json,a.ba_task_signals_native_provenance_json) as native_provenance_json,
       coalesce(p.provider_locator_json,a.ba_task_signals_provider_locator_json) as provider_locator_json,
       a.ba_task_signals_conversation_title as conversation_title,
       a.ba_task_signals_context_json as context_json,
       a.ba_task_signals_dedupe_key as dedupe_key,
       a.ba_task_signals_created_at as created_at
from actions a left join provenance p on p.id=a.ba_task_signals_provenance_id
where a.record_type='business_task_signals';
```

Use the existing `source_type/ref/time,id` Action lookup index only for inline rows; shared rows need the `idx_provenance_source` plus `idx_actions_signal_shared` path. Replace the `source_document_id` index and direct SQL joins. Keep a covered `dedupe_key` and `business_signal_public_id` point lookup. The exact `get_business_task_signal_for_task_source_ref_in_transaction` task/evidence join must use both arms; no full-table scan disguised behind the projection. `EXPLAIN QUERY PLAN` and timings on the copied corpus determine whether the union of indexed inline/shared arms is required instead of a single COALESCE filter. Do not retain duplicate identity fields to save the old index.

## Task 1: Build the unselected physical Actions/provenance component first

**Files:** Create `app/provenance_definitions.py`, `app/provenance_storage.py`, `tests/test_provenance_physical_storage.py`; modify the exact Signal sections of `app/action_business_definitions.py` and `app/action_business_storage.py`, plus narrow default-off declaration plumbing in `app/action_storage.py` and `app/email_canonical_storage.py`, under their owners' claims. No selected Store/coordinator switch in this task.

- [ ] **Step 1: Write RED tests against the actual canonical Email + inline Legacy owner Actions declaration.** Use the exact existing `email_canonical_storage._action_objects(variant, target=True, inline_legacy_owner=True)` and the current coordinator fixture's real `variant`. The new call adds `physical_provenance=True`, which is absent at RED. It must construct all current Actions kinds, canonical `emails(classification_id)` parent references and the inline Legacy owner guard, with only the Signal representation changed. Assert zero/one/two-owner representation, a preserved public Signal ID, all typed inbound FKs, and that the old unconditional update guard rejects promotion. Test a second source with the same ref but changed body/author/time as a different key. Add positive cases where a second owner with a *different dedupe key* arrives with no locator or a different valid locator: both use the first persisted canonical binding and become distinct Signal IDs. A raw insert with the same dedupe key still raises the existing SQLite immutable/unique error. Run `pytest -q tests/test_provenance_physical_storage.py`; expect failure for the missing builder flag and physical component.

```python
def test_second_exact_owner_promotes_first_without_replacing_public_id(current_actions_db, binding):
    first = insert_signal_version(current_actions_db, binding, dedupe_key='signal:a')
    assert current_actions_db.execute('select count(*) from provenance').fetchone()[0] == 0
    second = insert_signal_version(current_actions_db, binding, dedupe_key='signal:b')
    assert first != second
    assert current_actions_db.execute('select count(*) from provenance').fetchone()[0] == 1
    assert signal_binding(current_actions_db, first) == signal_binding(current_actions_db, second) == binding
    assert current_actions_db.execute('pragma foreign_key_check').fetchall() == []

def test_same_version_reuses_canonical_coordinate(current_actions_db, binding):
    from dataclasses import replace
    first = insert_signal_version(current_actions_db, binding, dedupe_key='signal:a')
    incoming = replace(binding, native_provenance_json=None, provider_locator_json=None)
    second = insert_signal_version(current_actions_db, incoming, dedupe_key='signal:b')
    assert second != first
    assert signal_binding(current_actions_db, first) == signal_binding(current_actions_db, second) == binding
    assert current_actions_db.execute('select count(*) from provenance').fetchone()[0] == 1

def test_raw_duplicate_dedupe_does_not_promote(current_actions_db, binding):
    import sqlite3
    import pytest
    first = insert_signal_version(current_actions_db, binding, dedupe_key='signal:a')
    with pytest.raises(sqlite3.IntegrityError):
        insert_signal_version(current_actions_db, binding, dedupe_key='signal:a')
    assert signal_binding(current_actions_db, first) == binding
    assert current_actions_db.execute('select count(*) from provenance').fetchone()[0] == 0
```

- [ ] **Step 2: Implement the minimum declaration and public-ID-preserving writer.** Add `physical_provenance: bool = False` to `business_action_schema_parts()`, passing it through `action_storage._action_definitions`, `_create_action_objects`, `_current_objects`, and `email_canonical_storage._action_objects`; leave every existing caller's default false. The last builder still performs its existing canonical Email parent substitution; the physical flag selects only the new Signal columns/checks/index/guard in the *complete* current Actions definition, including inline Legacy owner. `create_provenance_schema(db)` installs the exact new table before that default-off Actions builder executes. Build `signal_binding(db, public_id)` and `insert_signal_version(db, binding, *, dedupe_key, conversation_title='', context_json='{}', created_at=None)` in the new component; `db` is an existing caller-owned writer and this function retains raw INSERT semantics. Its source lookup is the complete `identity_key`; it compares the seven source scalars and key/hash/bytes against the persisted canonical binding for a *new* dedupe key. An absent or different incoming coordinate is not a new source version and does not reject that owner; reuse the persisted canonical coordinate without mutating it. If no owner, insert inline with the incoming coordinate. If exactly one inline owner, insert the shared row from its persisted canonical binding, UPDATE that original Action to FK-only payload, then INSERT the new Action before returning, all in one caller transaction. If already shared, INSERT another FK-only Signal. A duplicate dedupe key raises through the existing SQLite immutable/unique behavior; do not add an upsert or replay return here. The multi-statement promotion uses a nested SQLite savepoint so a raw duplicate failure cannot leave the first Signal promoted if its caller catches the exception and commits other work. Always use the existing Action public-ID allocator and insert path; never allocate a replacement first ID. End the writer with a query asserting the shared row has at least two owners. No provider/native I/O or commit inside this function. Add a regression assertion that the default `email_canonical_storage._action_objects(..., target=True, inline_legacy_owner=True)` remains byte-identical to its pre-unit declaration.

```python
def insert_signal_version(db, binding, *, dedupe_key, conversation_title='',
                          context_json='{}', created_at=None):
    owner = find_binding_owner(db, binding.identity_key)
    if owner is None:
        return insert_inline_signal(db, binding, dedupe_key, conversation_title, context_json, created_at)
    require_same_logical_source(owner.source, binding)
    if owner.provenance_id is None:
        shared_id = insert_provenance(db, owner.source)
        promote_inline_signal(db, owner.id, shared_id, owner.source)
    else:
        shared_id = owner.provenance_id
    new_id = insert_shared_signal(db, shared_id, dedupe_key, conversation_title, context_json, created_at)
    assert_shared_owner_count(db, shared_id, minimum=2)
    return new_id
```

`require_same_logical_source` compares the seven source scalars, `identity_key`, `body_sha256` and `body_utf8_bytes`, not `native_provenance_json` or `provider_locator_json`. The persisted binding remains canonical when a later observation supplies a different or absent coordinate. A conflict *among persisted bindings* for one key remains an integrity/read failure under `PreparedSources`; it is different from an incoming observation reusing an already persisted version. `TaskSemanticService._signal_id_or_create` and `_require_same_signal` own dedupe replay and compare the logical source plus independent title/context/dedupe facts, without comparing generated `created_at`; Task 2 tests that no-coordinate service replay. No locator upgrade, raw-insert upsert, per-row provider fallback or extra source I/O is implied.

- [ ] **Step 3: Prove the trigger distinguishes representation from fact mutation.** Use a source-preserving UPDATE predicate in the new Actions trigger that permits only inline→shared, requires an existing `provenance` row with equal seven scalars, identity key/hash/bytes/locator, and requires every writable non-payload physical Actions column to remain null-safe equal (`NEW.column IS OLD.column`), including `id`, `record_type`, `legacy_key`, parent/run/task references, state and timestamps. It also requires unchanged title/context/dedupe/Signal created-at within the payload. This replaces the old whole-row unconditional immutable guard; comparing only Signal payload would expose a mutation hole. Test a changed value in *each actual writable outer column* using `PRAGMA table_xinfo(actions)` filtered to non-generated columns, plus direct arbitrary payload UPDATE, DELETE, REPLACE, shared→inline and mismatched FK; all fail. Interrupt before second INSERT and roll back the transaction, then assert zero provenance rows and the first exact inline binding. Run the exact test file GREEN, `python -m compileall -q app/provenance_definitions.py app/provenance_storage.py`, and `git diff --check`.

```sql
-- Core guard predicate, embedded in the complete canonical trigger.
when old.record_type='business_task_signals' and not (
  new.record_type=old.record_type and new.legacy_key=old.legacy_key
  and old.ba_task_signals_provenance_id is null
  and new.ba_task_signals_provenance_id is not null
  and json_remove(new.payload_json,'$.provenance_id') =
      json_remove(old.payload_json,'$.source_type','$.source_ref','$.source_time',
       '$.conversation_id','$.author_user_id','$.author_name','$.author_kind',
       '$.identity_key','$.body_sha256','$.body_utf8_bytes',
       '$.native_provenance_json','$.provider_locator_json')
  and exists(select 1 from provenance p where p.id=new.ba_task_signals_provenance_id
  and p.source_type=old.ba_task_signals_source_type
    and p.source_ref=old.ba_task_signals_source_ref
    and p.source_time=old.ba_task_signals_source_time
    and p.conversation_id=old.ba_task_signals_conversation_id
    and p.author_user_id=old.ba_task_signals_author_user_id
    and p.author_name=old.ba_task_signals_author_name
    and p.author_kind=old.ba_task_signals_author_kind
    and p.identity_key=old.ba_task_signals_identity_key
    and p.body_sha256=old.ba_task_signals_body_sha256
    and p.body_utf8_bytes=old.ba_task_signals_body_utf8_bytes
    and p.native_provenance_json is old.ba_task_signals_native_provenance_json
    and p.provider_locator_json is old.ba_task_signals_provider_locator_json)
) begin select raise(abort,'business task signals are immutable'); end;
```

The complete trigger adds `NEW.column IS OLD.column` for every writable outer Actions column other than `payload_json`; the SQL above shows its payload and shared-binding predicate, including all seven source scalars and nullable locators. The integration test exercises the final trigger SQL verbatim. This first component is **unselected**: the still-selected Store/paired release retains the old body backend until later tasks compose it.

## Task 2: Build the body-free read/write API against the unselected target

**Files:** `app/store.py`, `app/business_source_reader.py`, `app/task_semantic_models.py`, `app/task_semantic_service.py`, `app/task_source_documents.py`; tests `tests/test_task_semantic_store.py`, `tests/test_business_source_reader.py`, `tests/test_task_evidence_read_api.py`.

- [ ] **Step 1: RED:** assert unique/shared Signals return equal `SourceVersionBinding` with different public IDs and dedupe keys; `BusinessTaskSignalFact` and current API no longer expose numeric `source_document_id`; `source_bundle` groups by `identity_key`; all task/project/date/anchor/relation/Attention links still point to their original Signal IDs. At the service boundary, replay the same dedupe key with the same seven source fields, full body and title/context but no optional coordinate; assert the existing ID returns and generated `created_at` does not enter the replay comparison. Separately assert raw Store duplicate-dedupe INSERT still fails. Run the three exact files and record the expected old-body join/model failures.
- [ ] **Step 2: Implement:** make `_business_task_signal_from_row` construct a fact from the body-free joined projection. Replace the four old `document.body` joins and `_business_source_document_id` in the target Store path with the Task 1 writer, passing a `SourceVersionBinding.from_signal(signal, native_provenance=..., provider_locator=...)` already validated against the transient body. `RequestSourceReader` selects exact native/provider/Console readers with no old-body lookup for the target. Remove `source_document_id` from the target `BusinessTaskSignalFact`, `BusinessTaskSignal`, `_source_input_from_fact`, and all current consumer comparisons; retain historical native declarations only in their historical source module. Preserve quote checks and transaction rechecks of the *complete* binding. Exercise this API on a constructed target fixture. Keep default Store constructors and paired Store/Email selectors on the old body declaration until Tasks 3–5 compose producer coverage, target projection, paired markers and recovery in one selected-default switch; no mixed source/target read is accepted.
- [ ] **Step 3: GREEN:** run the three files plus `tests/test_task_semantic_service.py tests/test_task_source_documents.py tests/test_task_retrieval.py tests/test_project_context_service.py`; expect all current paths to use one request-scoped exact read and same-writer fact recheck. Do not make provider reads inside `_immediate_write_transaction`.

```python
def _business_task_signal_from_row(row):
    source = SourceVersionBinding(
        source_type=row['source_type'], source_ref=row['source_ref'],
        source_time=row['source_time'], conversation_id=row['conversation_id'],
        author_user_id=row['author_user_id'], author_name=row['author_name'],
        author_kind=row['author_kind'], identity_key=row['identity_key'],
        body_sha256=row['body_sha256'], body_utf8_bytes=row['body_utf8_bytes'],
        native_provenance_json=row['native_provenance_json'],
        provider_locator_json=row['provider_locator_json'],
    )
    return BusinessTaskSignalFact(
        id=row['id'], source=source, conversation_title=row['conversation_title'],
        context_json=row['context_json'], dedupe_key=row['dedupe_key'],
        created_at=row['created_at'],
    )
```

## Task 3: Bind all newly produced exact versions before Signal insertion

**Files:** `app/task_models.py`, `app/task_scanners.py`, `app/task_report_scanner.py`, `app/minutes_owner_backfill.py`, `app/task_agent.py`, `app/task_semantic_service.py`, `app/native_trajectory.py`, `app/business_minutes_source_reader.py`, `app/business_weekly_source_reader.py`, `app/task_console_actions.py`, `app/todo_completion.py`, `app/task_semantic_import.py`; focused tests named in the file map.

The current `SourceSignal` has no coordinate. `TaskAgentCodexRunner` gets `RoutedCodexExecutionResult.attempt_id`, `session_id`, `transcript_start/end`; the actual `runtime_kind` is on `store.get_agent_runtime_attempt(attempt_id)` (`app/store.py:10290`). The Task Agent prompt removes `work_item.summary` from its JSON and gives the model bounded source ranges (`app/task_agent.py:547,596`; `SOURCE_CONTEXT_LIMIT=2048`). Therefore a session UUID or the result transcript cannot generally locate the complete new Work Item. Bind at the actual upstream producer, then carry a validated coordinate through the queued WorkItem and transient Signal. The queue's active input may contain the original while work is pending; final provenance stores only coordinate/checks. This is a source representation change, not a new review, authorization, or write policy.

| Current new-write path | Actual producer/body | Exact binding path to prove |
| --- | --- | --- |
| `app/task_scanners.py:123` AI Minutes scan | Raw meeting `json.dumps(minutes, ensure_ascii=False)` | Exact list/page record plus its producer serializer; the A2 `meeting+todos` reader is a different shape and cannot be assumed to cover this form. |
| `app/task_scanners.py:239,355` meeting Todos | `_canonical_minutes_todos_payload(meeting,todos,excerpts,summary)` | Existing A2 locator/reader only when the producing meeting endpoint and reader endpoint serialize the same complete dict. |
| `app/minutes_owner_backfill.py:77-95` meeting Todos | Same canonical serializer, meeting from `get_minutes_info(...).result` | Compare that genuine endpoint with A2's exhausted list-page exact UUID record; bind the endpoint that actually produced the body. |
| `app/task_report_scanner.py:130-158` three weekly kinds | `_work_item(ReportDocument).summary` | Existing A3 node/current-or-exact-history-version reader and whole-body check. |
| `app/task_console_actions.py:62` Console | `candidate_action_body(...)` | Existing A4 retained Signal/Event reconstruction; no provider locator required. |
| `app/todo_completion.py:33` direct external TODO completion | `json.dumps(evidence, ensure_ascii=False)` | Prove a stable provider coordinate or exact reconstruction from the retained completion Event's actual evidence, including JSON key order. |
| `app/task_semantic_import.py:435` legacy import | Synthesized excerpt or canonical row | Match the retained genuine legacy source row and its adopted result, or preserve reference-only status if it was never an observed original. |
| Remaining `WorkItemSourceType` values (`reply_attempt`, `local_file`, `memory_recall`, follow-up/TODO check kinds) | No additional WorkItem producer found by the bounded `WorkItem.model_validate`/enqueue search in `app/` | Inspect existing queued-source adapters and actual copied-row type counts before activation; any observed path needs its own genuine coordinate or retained-fact reader. Enum membership or a session field is not proof. |

- [ ] **Step 1: RED producer→reader tests.** For meeting Todos, feed the real `minutes_work_item` producer with a fake `list_minutes_page` record and fake Todos/transcript/summary; call `minutes_provider_locator(signal)` and `read_minutes_source_body` and assert exact `SourceVersionBinding.matches_body`. Separately feed `minutes_owner_backfill` with distinct `get_minutes_info.result` and list-page dictionaries for the same UUID. The second test must fail until the producer and reader use the same exact meeting serialization. For weekly, compose `_work_item(ReportDocument)`, `weekly_provider_locator`, `read_weekly_source_body` at current and `historyVersion=0` with fake DWS. For Console, use `decide_candidate`'s actual formatter and linked Event reconstruction. Run `pytest -q tests/test_business_minutes_source_reader.py tests/test_business_weekly_source_reader.py tests/test_business_console_source_reader.py tests/test_minutes_owner_backfill.py tests/test_task_report_scanner.py`; expect the mixed-endpoint case to reveal its present mismatch if the dictionaries differ.
- [ ] **Step 2: Implement transient coordinate transport.** Add a typed optional `source_locator` to the queued `WorkItemSource` and transient `SourceSignal`, holding only a provider coordinate or exact native coordinate, never the body or a research receipt. `scan_meeting_todos` and `scan_ai_minutes` use the actual exact page/list producer; `scan_task_reports` records the actual node and source version if one is supplied (current version is an explicit current locator, not a guessed history number). `minutes_owner_backfill` must either construct the canonical meeting body from its exact list-page record or record an operational `get_minutes_info.result` coordinate and extend the reader to that endpoint; choose the latter only if full-shape fake comparison proves it. At the Task Agent apply boundary, `SourceSignal` copies this coordinate only for `evidence_origin='current'`; historical memory/session citations remain `REFERENCE_ONLY` per `source_is_observed` and never claim an observed original. `SourceVersionBinding.from_signal` computes identity/hash/bytes from the authoritative transient incoming body and retains the producer's genuine coordinate. In a composed synthetic test, the selected reader later re-reads that coordinate and checks the entire binding. A missing or changed later source is reported unavailable by the existing typed read result; there is no new every-write provider fetch or rejection rule.
- [ ] **Step 3: Close the remaining current write-entry inventory.** `app/todo_completion.py` directly writes `dingtalk_todo` evidence JSON; `app/task_console_actions.py` writes Console facts; `app/task_semantic_import.py` can synthesize legacy source excerpts. Inspect every `SourceSignal(` and `create_business_task_signal_in_transaction(` call found by `rg -n 'SourceSignal\(|create_business_task_signal_in_transaction\(' app`. For each observed source, implement a real existing-provider, retained-fact, or immutable native coordinate and a composed exact reader test. For legacy history/reference-only material, preserve its current cited/not-observed meaning; do not label it available. No runtime may load `docs/superpowers/specs/*` metadata receipts. This finite inventory is a pre-activation gate: if a current observed producer cannot supply exact coordinates, report the exact producer and leave physical selection off. Do not introduce a blanket rejection of new writes merely to make inverse pass.
- [ ] **Step 4: Native path only where the complete literal really exists.** If an actual producer has a complete body in a native JSONL string leaf, derive `{runtime_kind,session_uuid,line_index_zero_based,raw_line_sha256,leaf_path,substring_start_character_zero_based,substring_end_character_exclusive,literal_encoding_mode,decoded_utf8_sha256,decoded_utf8_bytes}` from the persisted attempt's actual kind/session/bounds and one raw line; require unique matching exact literal and prove with `read_native_source_body` in a focused synthetic composition. The producer calls this outside a DB writer. `codex_session_id` remains a legacy run field, not the runtime-kind oracle. A synthetic line with two identical literals must fail ambiguity; a changed raw-line hash must read unavailable. This path is ineligible for a full Work Item summary that never appeared in the native transcript. Run the focused native test file `tests/test_native_source_documents.py` and Task Agent tests.

```python
def bind_new_source(signal: SourceSignal) -> SourceVersionBinding:
    coordinate = signal.source_locator
    binding = SourceVersionBinding.from_signal(
        signal,
        native_provenance=coordinate if coordinate and coordinate.get('runtime_kind') in ('codex_cli','claude_cli') else None,
        provider_locator=coordinate if coordinate and 'kind' in coordinate else None,
    )
    return binding
```

The final API must validate coordinate shape against the producer family without calling the source at every write. The composed test checks producer coordinate, persisted binding and later exact reader/body result; this sketch specifies data flow, not a new reject-on-unavailability rule.

## Task 4: Convert source facts inside the paired release

**Files:** `app/provenance_storage.py`, `app/action_email_schema_release.py`, `app/business_schema_release.py`, `app/email_current_schema.py`; tests `tests/test_provenance_physical_release.py`, `tests/test_email_action_schema_release.py`.

- [ ] **Step 1: RED exact source/target selector tests.** Build both actual and fresh current intermediate shapes, including canonical Email and inline Legacy owner. Assert the new target has exactly the ten core tables `tasks,runs,actions,business,emails,provenance,configs,provider_state,schedules,state` plus `email_schema_migrations`, no `business_source_documents`, no old body/trigger/document index, no unexpected user table or shadow FTS, and the exact existing `trg_sync_legacy_update_public_id` semantics. An Actions declaration mismatch, foreign object collision, unknown source body trigger, malformed target FK, or mismatched Store/Email marker must refuse before DDL. Run the two exact files; expect target selection failures.
- [ ] **Step 2: Implement capture/forward at the correct composition point.** `app/action_email_schema_release.py:650-657` currently migrates Business Actions, Email History, canonical Email, then Legacy owner before markers. Capture physical source declarations and exact source-container `sqlite_sequence` presence/water in preparation. For forward, migrate canonical Email, migrate Legacy owner, then rebuild Actions for the physical Signal/provenance shape *inside the same existing FK-off `BEGIN IMMEDIATE`*, before writing the paired markers. This final rebuild must preserve every non-Signal Action row, physical ID, typed payload, direct FK, allocator water and exact Legacy owner guard; it converts Signals by current count of each full `identity_key`, not an old source_document ID or `source_ref`. No native/provider call occurs from migration SQL or the transaction. Preflight supplies only validated binding coordinates/checks; if an observed source is neither currently exact-readable nor one of the authorized 20 exact losses, leave the selected old body backend intact.
- [ ] **Step 3: Verify current facts before commit and after reopen.** Compare per-family rows/SQLite storage classes, Signal public IDs, dedupe keys, seven identity facts, independent title/context/times, task/project/date/relation/Attention links and evidence roles, adopted quote/facts, business results, external action receipts, all allocator waters, foreign-key findings, exact target declaration and paired markers. The twenty accepted-loss rows still retain their key/hash/bytes and read as unavailable; their bodies occur in no target user table, metadata blob, archive or runtime cache. Run the exact release tests GREEN, then constructor tests in two independent fresh processes. Do not modify historical source fixture hashes to make the current target pass.

```python
def physical_forward(db, prepared):
    require_fk_off_writer(db)
    require_exact_intermediate_declarations(db, prepared)
    source_rows = read_current_signal_and_document_facts(db)
    groups = group_by_full_identity_key(source_rows)
    install_body_free_actions_and_provenance(db)
    for key, rows in groups.items():
        write_provenance_if_shared(db, key, rows)
        for row in rows:
            write_same_public_signal_and_inbound_links(db, row)
    verify_current_fact_fingerprints(db, prepared)
```

## Task 5: Rehearse current-state recovery, genuine old writes, and forward repair

**Files:** `app/provenance_storage.py`, `app/action_email_schema_release.py`, `tests/test_provenance_physical_release.py`; document the selected behavior in `docs/architecture.md` and `docs/runtime-mechanism.md` with the code change.

The old `business_source_documents` has `id INTEGER PRIMARY KEY AUTOINCREMENT`, `created_at`, and its own allocator water. Those are retired container bookkeeping, not preserved Signal identity. Record their exact pre-forward values in the bounded **read-only rehearsal oracle** for pre-existing rows; compare them when testing an exact old-schema inverse. Do not place a permanent numeric alias or old body in current runtime rows. An actual inverse can create genuine historical rows only after it obtains each current exact body, verifies the full key/SHA/bytes, restores the original document ID/created_at/allocator for pre-forward rows from the bounded oracle, and assigns new post-forward document IDs using old AUTOINCREMENT behavior. With no oracle after activation, or an unavailable current body, declare the old inverse unsupported for that state. No blank old body and no stale database restoration.

- [ ] **Step 1: RED copied-state test.** On a disposable copied DB, perform forward; append a *new* exact-locator Signal, evidence relation, ordinary Action and external receipt; inverse from the current target, exercise genuine old-code Signal/evidence/receipt writers, and re-forward. Assert all original and post-forward public IDs, links, roles, receipts and allocator waters survive. A pre-forward source copy may supply only pre-existing bodies during this rehearsal; it cannot supply new post-forward bodies. Run `pytest -q tests/test_provenance_physical_release.py::test_current_fact_inverse_old_write_reforward`; expect failure until inverse is implemented.
- [ ] **Step 2: GREEN exact inverse and missing-body boundary.** Before writing old tables, resolve every current binding outside SQL using recorded locators and `SourceVersionBinding.matches_body`; for pre-forward rehearsal rows, compare against the frozen copy's exact body/key/bytes and restore original container ID/time/water. Rebuild all current rows/receipts and source declarations in the paired inverse transaction. Then remove one new source from its fake provider/native store and assert inverse refuses *before* changing committed DB facts. Repeat with one of the authorized 20 pre-existing losses without the bounded oracle: old inverse is unsupported and the current target remains intact. The same 20-row authorization does not cover the missing new row.
- [ ] **Step 3: Select and prove the stage-5 recovery boundary.** After activation, select **forward repair of the current target** as the default recovery path, preserving current rows, paired markers, generation/claim state and every receipt. A compatible old inverse is used only when all exact bodies and the bounded old-container metadata needed for it are available in that specific rehearsal; do not restore a stale pre-cutover database after the external-effect window. Inject a failure after a fake persisted external receipt, restart a compatible target/repair path, and assert no provider send/replay, no receipt loss, and clean queue/Attention/History readback. Also inject a failure before the effect window and show the same forward-repair path completes without a new write restriction or audit policy. The production deploy script must explicitly support this paired database/code recovery window before activation; current `python -m app.deploy` is not assumed to provide it.

```python
def exact_body_for_old_inverse(binding, reader):
    reading = reader.resolve(binding)
    if not isinstance(reading, AvailableSource) or not binding.matches_body(reading.body):
        raise OldSchemaInverseUnavailable(binding.identity_key)
    return reading.body

def recover_after_cutover(target_db, target_release):
    target_release.verify_current_facts_and_receipts(target_db)
    target_release.repair_forward_in_place(target_db)
    target_release.verify_current_facts_and_receipts(target_db)
```

## Task 6: Actual-copy, capacity and activation gates

**Files:** new metadata-only proof receipt under `docs/superpowers/specs/`, `docs/architecture.md`, `docs/runtime-mechanism.md`, `docs/superpowers/plans/2026-10-11-database-redesign-program.md`; focused tests in the file map. The execution owner chooses a task-owned `/tmp` directory and removes only its own verified temporary copies after evidence readback.

- [ ] **Step 1: Verify source availability on a copied production snapshot, not during migration SQL.** Recompute the current denominator and exact identity/byte cohort. Refresh previously positive provider/native availability using bounded exact coordinates; the known negative weekly historical coordinates need no repeat merely because they once failed. For each observed source outside the 20 authorized keys, require a current exact-reader match. For the 20, check exact key+byte pair and preserve `原文不可用`. Compare `get_minutes_info.result` versus `list_minutes_page` on the actual producer/reader shape. If any new producer/coordinate is unproved, name it and keep the body backend selected; do not silently expand the retirement decision.
- [ ] **Step 2: Run the paired actual-copy and recovery matrix in an owned temporary copy.** Capture source schema/facts/FK findings/allocators; execute forward, exact current-fact inverse with bounded oracle, genuine old writes, re-forward; then post-forward new exact-source Signal/evidence/action/receipt, inverse/reforward when its source exists, and unavailable-new-source forward repair. Run the focused pytest files only, including `tests/test_action_business_storage.py`, `tests/test_business_actions_native_parity.py`, `tests/test_task_semantic_store.py`, `tests/test_email_action_schema_release.py`, source readers, Task Agent, API and frontend pages. Record command, exit code, copy fingerprint and exact source/target facts; an older 116→11→116→11 body-retaining proof is not physical E acceptance.
- [ ] **Step 3: Measure comparable storage and lookup costs.** Use the same copied input and checkpoint state to record `page_count * page_size`, `freelist_count`, per-index `dbstat` bytes, and WAL bytes before/after; separately report post-`VACUUM` size only after its own rehearsal. Run `EXPLAIN QUERY PLAN` plus repeated timings for Signal ID, dedupe, task+source-ref, shared/inline source lookup, Task detail, Attention and History. Fail the gate on unexpected scan or material latency regression; the source document index cannot be carried over as a dummy alias. Verify target ordinary names are exactly ten plus ledger and source bodies do not appear in any target table/metadata archive.
- [ ] **Step 4: Release only after independent acceptance and compatible deploy rehearsal.** Freeze exact code, source decision, copied-data receipt, query/capacity evidence and recovery receipt; get the required independent component/release review used by this repository. Commit only owned hunks with behavior docs in the same commit, push through the established path, and use `python -m app.deploy` only after its explicit paired schema/code recovery support is verified. Read back new PID, healthz, queues, Attention and History; distinguish publication from business outcome. No real outbound send is part of the proof.

```sql
pragma foreign_key_check;
select name from sqlite_schema where type='table' and name not like 'sqlite_%' order by name;
select (select page_count from pragma_page_count) * (select page_size from pragma_page_size) as main_bytes;
select name, sum(pgsize) as bytes from dbstat where name is not null group by name order by bytes desc;
explain query plan select id from actions where business_signal_public_id=?;
explain query plan select id from actions where ba_task_signals_dedupe_key=?;
```

## Self-review and execution boundary

The tasks cover the physical representation, conditional immutable guard, current read/write model, every new observed producer, actual paired composition, exact20 authorized loss, current-state inverse where possible, honest unavailable-new-original boundary, forward repair after effects, copied-data/constructor proof, index/capacity measurement and release readback. There is no runtime body archive, numeric SourceDoc alias, generic relation table, new business field, blanket 30-day policy, or automatic replay.

The largest remaining source questions are deliberately finite: (1) enumerate the actual `SourceSignal` and direct Store write producers and prove a coordinate/reader for each *observed* new version; (2) compare real `get_minutes_info.result` and `list_minutes_page` meeting serialization; (3) prove current availability for source versions outside the exact20 authorized loss cohort; (4) integrate the physical component with the current canonical Email + Legacy owner Actions declaration and genuine old-container inverse metadata. Each has an exact task and failure gate above. Until those gates pass, the current body-bearing backend remains selected and no physical or production claim is warranted.
