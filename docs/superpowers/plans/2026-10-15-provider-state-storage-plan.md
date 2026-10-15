# Provider-State Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Build one unselected `provider_state` declaration and source-shaped storage unit for the two current provider-fact families, preserving their 12 native source fields and independently keyed current rows.

**Architecture:** One ordinary SQLite row per observation or folder generation. Keep the source composite identity components and shared time as real typed columns, put the four observation-only optional values in a finite tagged payload, and expose them through source-affinity generated columns. Three partial indexes implement the two independent identities and observation folder lookup. The caller owns the transaction; this unit does not select a release, migrate data, or change provider behavior.

**Tech Stack:** Python `sqlite3`, existing `_encoded_sqlite_value`, `_encoded_value`, and `_encoded_value_check` helpers, SQLite JSON1/generated columns/partial indexes, pytest, Ruff.

---

## Authority, boundary, and choice

The accepted source is `docs/database-redesign-provider-state-source-contract.md`, its `tests/test_provider_state_source.py` oracle, the 12 CSV rows at `docs/superpowers/specs/2026-10-11-database-redesign-field-map.csv:645-656`, and both literal source variants under `groups.provider_state` in `docs/superpowers/specs/2026-10-15-database-redesign-config-state-native-source.json`. Those artifacts bind published `7dadc492c1c62d5b0624f2f97dd0c89f4f358646`, Store `2026-10-13.5`, Email `51`. The captured 72,141 observation/55 generation rows are frozen counts, not live measurements. The approved ten-core-plus-ledger design assigns these families to the single `provider_state` table. The broader Store `2026-10-14.1`/Email `52` pair, with Email and Business work, remains unpublished; this plan does not select or apply it.

Two rows with the same `account_id` and folder ID may coexist: one observation keyed additionally by stable message identity, one generation keyed by account/folder. A single `(record_type,legacy_key)` JSON key would add a redundant copy and complicate SQLite's source TEXT affinity. A split-table target would fail the approved one-table assignment. Choose real composite components plus `record_type`: the observation key is `(account_id,stable_message_identity)` and the generation key is `(account_id,provider_folder_id)`, each within its partial type. `id INTEGER PRIMARY KEY` follows the existing shared-table physical shape as an internal rowid alias; it is not a public identity, allocator, FK target, or caller result. It has no `AUTOINCREMENT`. No legacy-key column, public-ID stream, trigger, FK, or separate lookup index is justified.

### Exact field placement

| Source family | Source fields and target placement |
| --- | --- |
| Observation, actual order | `account_id` real TEXT; `stable_message_identity` real TEXT; `state` real TEXT; `provider_folder_id` real TEXT; `provider_folder_name` payload/generated TEXT; `category_key` payload/generated TEXT; `important` payload/generated INTEGER; `observed_at` real TEXT; `important_signals_json` payload/generated TEXT. Fresh order swaps the last two source ordinals only. |
| Generation | `account_id` real TEXT; `provider_folder_id` real TEXT; `observed_at` real TEXT. Its `stable_message_identity` and `state` are structural NULL and its payload is exactly `{}`. |

The observation payload has **exactly** `provider_folder_name`, `category_key`, `important`, `important_signals_json`; each key is present even when its source value is SQL NULL. Use `_encoded_sqlite_value` before JSON serialization, then `_encoded_value` for each generated projection. This retains source-accepted SQLite classes, including BLOBs that survive TEXT affinity and numeric strings that acquire INTEGER affinity. Do not replace source SQL constraints with Python `str`/`bool` checks or insist that native-valid signal JSON be a list at storage time. The available-row application projection still applies its stricter list-of-nonblank-strings check later. The source tables have no column defaults, FK, triggers, `WITHOUT ROWID`, or AUTOINCREMENT. This target adds only structural `record_type`, `id`, and `payload_json`.

## Next unit: files and complete declaration

| File | Responsibility |
| --- | --- |
| `app/provider_state_definitions.py` | Finite type/field constants, the exact generated-column declaration, three indexes, and named source projections. |
| `app/provider_state_storage.py` | Install the unselected declaration and perform source-shaped reads, point writes, batch upserts, and set-based reconciliation under a caller transaction. |
| `tests/test_provider_state_storage.py` | Synthetic literal-source-versus-target oracle for both captured variants, with behavior and query-plan controls. |
| `docs/architecture.md`, `docs/runtime-mechanism.md` | Add a short paragraph explicitly identifying the unselected storage unit in the same implementation commit; do not describe it as a selected runtime schema. |
| `docs/agent-claims.md` | Implementer claims these files before editing and stages only owned hunks. |

The executing agent must not edit the accepted source capture/oracle, existing codecs, Store callers, release/version modules, migration coordinator, provider client, or production database in this unit.

The definition builder must produce this SQL, with `V(f) = _encoded_value('payload_json', '$.' + f)` and `C(f) = _encoded_value_check('payload_json', '$.' + f)` expanded at construction time. `V` and `C` are compile-time Python helpers, **not** SQL functions or runtime placeholders. Their exact current expressions are in `app/action_email_definitions.py:597-617`; `_encoded_sqlite_value` is in `app/action_storage.py:849-863`.

```sql
CREATE TABLE provider_state (
    id INTEGER PRIMARY KEY,
    record_type TEXT NOT NULL CHECK(record_type IN ('observation','folder_generation')),
    account_id TEXT NOT NULL CHECK(trim(account_id) != ''),
    stable_message_identity TEXT,
    state TEXT,
    provider_folder_id TEXT,
    observed_at TEXT NOT NULL CHECK(trim(observed_at) != ''),
    payload_json TEXT NOT NULL
        CHECK(typeof(payload_json)='text' AND json_valid(payload_json)
              AND json_type(payload_json)='object'),
    provider_folder_name TEXT GENERATED ALWAYS AS (V(provider_folder_name)) VIRTUAL,
    category_key TEXT GENERATED ALWAYS AS (V(category_key)) VIRTUAL,
    important INTEGER GENERATED ALWAYS AS (V(important)) VIRTUAL,
    important_signals_json TEXT GENERATED ALWAYS AS (V(important_signals_json)) VIRTUAL,
    CHECK(
      (record_type='observation'
       AND stable_message_identity IS NOT NULL
       AND trim(stable_message_identity) != ''
       AND state IS NOT NULL AND state IN ('available','unavailable','excluded')
       AND json_type(payload_json,'$.provider_folder_name') IS NOT NULL
       AND json_type(payload_json,'$.category_key') IS NOT NULL
       AND json_type(payload_json,'$.important') IS NOT NULL
       AND json_type(payload_json,'$.important_signals_json') IS NOT NULL
       AND json_remove(payload_json,'$.provider_folder_name','$.category_key',
                       '$.important','$.important_signals_json')='{}'
       AND C(provider_folder_name) AND C(category_key)
       AND C(important) AND C(important_signals_json)
       AND (important IS NULL OR important IN (0,1))
       AND (important_signals_json IS NULL OR json_valid(important_signals_json)))
      OR
      (record_type='folder_generation'
       AND stable_message_identity IS NULL AND state IS NULL
       AND provider_folder_id IS NOT NULL AND trim(provider_folder_id) != ''
       AND payload_json='{}')
    )
);
CREATE UNIQUE INDEX idx_provider_state_observation_identity
    ON provider_state(account_id,stable_message_identity)
    WHERE record_type='observation';
CREATE UNIQUE INDEX idx_provider_state_generation_identity
    ON provider_state(account_id,provider_folder_id)
    WHERE record_type='folder_generation';
CREATE INDEX idx_provider_state_observation_folder
    ON provider_state(account_id,provider_folder_id)
    WHERE record_type='observation';
```

The generated `V` expressions inherit TEXT or INTEGER affinity from their declared columns. The required source checks operate on those projections, after source-like affinity, with SQLite's three-valued CHECK behavior. For example, numeric text `"1"` becomes INTEGER 1, and JSON scalar text `"42"` is valid for `important_signals_json`; BLOB values accepted by the literal source are still admitted when the corresponding native check permits them. Do not add a `STRICT` table or a JSON-array check. Both unique indexes use native BINARY collation, and all three indexes have `record_type` in their literal partial predicate rather than an extra key column. `EXPLAIN QUERY PLAN` must show the observation identity index for point reads and the observation folder index for account/folder reconciliation; generation point reads use its identity index. The retired `idx_email_provider_observations_lookup` is not recreated.

The implementation must construct this SQL with the existing helpers and check the expanded DDL in an isolated in-memory SQLite database. The `CHECK` must not accidentally evaluate missing-field `C(...)` on generation rows: its `OR` branch is complete. Generated observation-only fields are NULL for generations because their payload is `{}`.

## Exact storage API and statement semantics

Expose these finite functions in `app/provider_state_storage.py` (all accept `sqlite3.Connection` first):

```python
create_provider_state_schema(db) -> None
get_observation(db, account_id, stable_message_identity) -> dict | None
get_folder_generation(db, account_id, provider_folder_id) -> dict | None
insert_observation(db, values: Mapping[str, object], *, ignore: bool = False) -> int
insert_folder_generation(db, values: Mapping[str, object], *, ignore: bool = False) -> int
upsert_observations(db, rows: Sequence[Mapping[str, object]]) -> None
upsert_folder_generations(db, rows: Sequence[Mapping[str, object]]) -> None
mark_authoritative_missing(db, account_id, folder_id, observed_at) -> int
mark_unavailable_folder(db, account_id, folder_id, observed_at) -> int
mark_inactive_accounts(db, observed_at) -> int
update_important_signals(db, account_id, stable_message_identity,
                         important, important_signals_json) -> bool
delete_observation(db, account_id, stable_message_identity) -> int
delete_folder_generation(db, account_id, provider_folder_id) -> int
```

`get_*` projects exactly the named source fields, not target `id`, `record_type`, or `payload_json`. The observation projection names fields in the actual order above; the separate fresh tuple swaps signal/time only for oracle comparison and migration-by-name. A `SELECT` list of generated values returns native SQLite storage classes. Use literal `record_type='observation'` / `'folder_generation'` in queries so the partial indexes apply. Get/delete return absence as `None`/`0`; insert returns SQLite cursor `rowcount` (1 or 0 for `OR IGNORE`), not `lastrowid`. Successful updates of the same key retain the internal rowid, but callers cannot depend on it. Plain duplicate INSERT raises `sqlite3.IntegrityError`; ignored duplicate/failed CHECK returns zero where native SQLite `OR IGNORE` does, while CHECK failure may be ignored and an invalid generated expression may still raise as native SQLite does. No `REPLACE` or public-ID allocation API is added.

All write functions require `db.in_transaction` and never call `commit`, `rollback`, `BEGIN`, or a connection context manager. The caller uses `BEGIN IMMEDIATE` for the scan's observation/reconciliation/generation group, and its rollback restores the group. Installers only execute DDL and do not select the schema. `upsert_*` use `executemany` with named values and no Python full-table read. Each row has complete source field values; the storage helper fills omitted optional observation fields with `None` only for plain INSERT, while scan upserts supply all nine. The only application validation in this unit is a finite field-name/kind check plus `_encoded_sqlite_value` to reject values the Python sqlite3 binder itself cannot represent. Let SQLite enforce source affinity and constraints.

`upsert_observations` uses this conflict target and changed-fact guard, comparing projected values rather than payload text so encoding differences that source affinity erases do not advance `observed_at`:

```sql
INSERT INTO provider_state
 (record_type,account_id,stable_message_identity,state,provider_folder_id,
  observed_at,payload_json)
 VALUES ('observation',?,?,?,?,?,?)
 ON CONFLICT(account_id,stable_message_identity)
 WHERE record_type='observation' DO UPDATE SET
  state=excluded.state,
  provider_folder_id=excluded.provider_folder_id,
  observed_at=excluded.observed_at,
  payload_json=excluded.payload_json
 WHERE provider_state.state IS NOT excluded.state
    OR provider_state.provider_folder_id IS NOT excluded.provider_folder_id
    OR provider_state.provider_folder_name IS NOT excluded.provider_folder_name
    OR provider_state.category_key IS NOT excluded.category_key
    OR provider_state.important IS NOT excluded.important
    OR provider_state.important_signals_json IS NOT excluded.important_signals_json;
```

`upsert_folder_generations` changes only time under its own partial identity:

```sql
INSERT INTO provider_state
 (record_type,account_id,provider_folder_id,observed_at,payload_json)
 VALUES ('folder_generation',?,?,?,'{}')
 ON CONFLICT(account_id,provider_folder_id)
 WHERE record_type='folder_generation' DO UPDATE SET
  observed_at=excluded.observed_at
 WHERE provider_state.observed_at IS NOT excluded.observed_at;
```

Scan staging remains a connection-local `current_provider_membership(account_id,provider_folder_id,stable_message_identity, PRIMARY KEY(...))` and optional `current_provider_accounts(account_id PRIMARY KEY)`, populated with batched `INSERT OR IGNORE`. This storage unit reads those existing temp names; future Store integration owns their creation, population, and cleanup. They can be staged before the caller's `BEGIN IMMEDIATE` just as the current Store does; this module never commits staging or starts the publication transaction.

`mark_authoritative_missing` must execute one indexed SQL UPDATE for each explicitly authoritative `(account_id,folder_id)`: filter `record_type='observation' AND account_id=? AND provider_folder_id=?`, exclude rows in `current_provider_membership` by all three original identity fields, and apply the original changed-row predicate. Set `state='unavailable'`, `provider_folder_id=NULL`, `observed_at=?`, and `payload_json` to the exact four-NULL payload. `mark_inactive_accounts` executes the same mutation for observations whose `account_id` is absent from `current_provider_accounts`; the caller invokes it only when `active_account_ids is not None`, so an explicit empty table invalidates all accounts. `mark_unavailable_folder` filters by the observation folder index, retains `provider_folder_id` and the original payload's `provider_folder_name`, sets category/importance/signals to NULL through `json_set(..., json('null'))`, and updates time only when the original predicate says to. A partial scan never calls `mark_authoritative_missing` for its folder. No deletion-by-age or classification existence check is allowed.

The three update statements are literal templates. `NULL_PAYLOAD` below is a Python bind value made by `json.dumps` of the four named NULL keys; no SQL identifier is substituted at runtime. The caller has already populated the named temp tables. Their indexes make the correlated membership tests bounded by `(account_id,provider_folder_id,stable_message_identity)` or `account_id`.

```sql
-- mark_authoritative_missing: (observed_at, NULL_PAYLOAD,
--                              account_id, folder_id, folder_id, observed_at)
UPDATE provider_state AS o
SET state='unavailable', provider_folder_id=NULL,
    observed_at=?, payload_json=?
WHERE record_type='observation' AND account_id=? AND provider_folder_id=?
  AND NOT EXISTS (
      SELECT 1 FROM current_provider_membership AS m
      WHERE m.account_id=o.account_id AND m.provider_folder_id=?
        AND m.stable_message_identity=o.stable_message_identity)
  AND (state IS NOT 'unavailable' OR provider_folder_id IS NOT NULL
       OR provider_folder_name IS NOT NULL OR category_key IS NOT NULL
       OR important IS NOT NULL OR important_signals_json IS NOT NULL
       OR observed_at IS NOT ?);

-- mark_inactive_accounts: (observed_at, NULL_PAYLOAD, observed_at)
UPDATE provider_state AS o
SET state='unavailable', provider_folder_id=NULL,
    observed_at=?, payload_json=?
WHERE record_type='observation'
  AND NOT EXISTS (SELECT 1 FROM current_provider_accounts AS a
                  WHERE a.account_id=o.account_id)
  AND (state IS NOT 'unavailable' OR provider_folder_id IS NOT NULL
       OR provider_folder_name IS NOT NULL OR category_key IS NOT NULL
       OR important IS NOT NULL OR important_signals_json IS NOT NULL
       OR observed_at IS NOT ?);

-- mark_unavailable_folder: (observed_at, account_id, folder_id, observed_at)
UPDATE provider_state
SET state='unavailable', observed_at=?,
    payload_json=json_set(payload_json,
        '$.category_key',json('null'), '$.important',json('null'),
        '$.important_signals_json',json('null'))
WHERE record_type='observation' AND account_id=? AND provider_folder_id=?
  AND (state IS NOT 'unavailable' OR category_key IS NOT NULL
       OR important IS NOT NULL OR important_signals_json IS NOT NULL
       OR observed_at IS NOT ?);
```

`update_important_signals` is a single point UPDATE with `record_type='observation' AND account_id=? AND stable_message_identity=? AND state='available'`. Set only payload `important` and `important_signals_json` through `json_set` of their separately tagged JSON values; return `cursor.rowcount == 1`. Neither `observed_at` nor the generation row changes. The later Store caller continues to derive `important` from recognized names and serialize the signal list as it currently does; this storage helper accepts the two exact source column values and imposes no new signal policy. `delete_*` are source-shaped point deletes for isolated storage controls, not scan tombstones.

```sql
-- Binds: json.dumps(_encoded_sqlite_value(important)),
--        json.dumps(_encoded_sqlite_value(important_signals_json)),
--        account_id, stable_message_identity.
UPDATE provider_state
SET payload_json=json_set(payload_json,
        '$.important',json(?), '$.important_signals_json',json(?))
WHERE record_type='observation' AND account_id=?
  AND stable_message_identity=? AND state='available';
```

For a later classification read, join classification identity to an observation on its real composite columns and `record_type='observation'`, then LEFT JOIN a generation on `account_id/provider_folder_id` and `record_type='folder_generation'`; project `coalesce(g.observed_at,o.observed_at)`. An observation may exist before classification, so no FK or classifier-dependent admission may be added. For unavailable/excluded rows the existing projection returns before signal decoding; available rows still require a JSON list of nonblank strings at application projection time. The five current Store methods retain their source behavior during the later caller unit.

## Test-first execution steps

### Task 1: Declare and inspect the unselected table

- [x] Claim only the implementation files in `docs/agent-claims.md`. Write `tests/test_provider_state_storage.py` with a failing import/schema test. Use the literal `source_db(variant)` helper from `tests/test_provider_state_source.py` as the independent control; do not mirror target SQL as its oracle.
- [x] Run `pytest -q tests/test_provider_state_storage.py::test_target_declaration` and observe the expected missing-module/import failure.
- [x] Add `app/provider_state_definitions.py` with the exact DDL builder and three indexes above, then the schema installer in `app/provider_state_storage.py`. Run that exact test; assert `table_xinfo`, index predicates/columns/collation, no FK/triggers/AUTOINCREMENT, payload/generated types, and `EXPLAIN QUERY PLAN` index names.

### Task 2: Source-shaped point reads and writes

- [x] Add a parametrized failing test over `actual` and `fresh` literal source DDL: same named source inserts, key collisions, case-sensitive keys, optional NULLs, blank required fields, invalid state/important/JSON, numeric-string INTEGER affinity, native-valid scalar `"42"` signal JSON, source-accepted BLOB values, plain versus `OR IGNORE`, point read/delete/reinsert, and source rowid stability only as a private diagnostic. Compare field values and `typeof` for every one of the 12 source fields; do not compare target internal `id` to source ROWID.
- [x] Implement the finite named source projection, payload codec, transaction check, point insert/read/delete. Re-run that parametrized test. Use `BEGIN IMMEDIATE` in target controls; a write without a transaction raises `sqlite3.ProgrammingError` before any mutation.

### Task 3: Batch scan mutations and rollback

- [x] Add a failing synthetic two-account/two-folder comparison using literal source SQL as the independent baseline. Sequence: insert observation before classification; unchanged scan preserves observation time while generation advances; payload encodings of `important='1'` versus `1` and `category_key=42` versus `'42'` have the same source-affinity values and must leave the old observation time; folder move updates one observation identity; partial scan retains missing members; only explicit authoritative membership tombstones them; unavailable folder retains folder ID/name and clears category/importance/signals; `None` active-account argument skips reconciliation, empty active-account table invalidates all; direct signal update matches only available rows and keeps **both** observation and generation times; `coalesce` generation overlay matches. Use a connection-local temp membership/accounts table and compare all projected rows after each step, including `typeof`.
- [x] Implement `upsert_*` with batched SQL and the three set-based UPDATEs above, then the single-row signal update. A deliberately failing generation insert after an observation upsert must roll back both target changes under the caller transaction, as the literal-source control does. Inspect `EXPLAIN QUERY PLAN` for point and folder paths; no Python loop over the 72k-row inventory.
- [x] Run `pytest -q tests/test_provider_state_source.py tests/test_provider_state_storage.py`, `ruff check app/provider_state_definitions.py app/provider_state_storage.py tests/test_provider_state_storage.py`, `python -m compileall -q app/provider_state_definitions.py app/provider_state_storage.py tests/test_provider_state_storage.py`, and `git diff --check`. No whole live-tree pytest run.

### Task 4: Document and commit the unit

- [x] Add one precise paragraph each to `docs/architecture.md` and `docs/runtime-mechanism.md`: the single-table unit is present but unselected, holds two typed composite-key kinds, keeps generation overlay and caller-owned transaction, and leaves current Store/provider routes on the old tables.
- [x] Review the final diff and stage only this unit's new modules/test, scoped behavior paragraphs, and implementer's claim row. Commit the unselected unit. The docs-only plan commit is separate.

## Later boundaries, outside this unit

The next separate task is a pure forward/inverse migration of **current facts** for both actual and fresh source declarations. It must bind rows by names, preserve every valid observation/generation independent of classification or age, prove the inverse including source-affinity classes and identity, and account for the actual/fresh signal/time ordinal difference. A later current-caller task converts the five Store methods and paired release wiring, then proves genuine `7dadc492` cold-source writes, reforward/inverse, and the exact Store/Email pair in the same transaction with its marker. The current source-method AST equality assertion is temporary pre-rewrite evidence; replace its role with behavior oracle/caller equivalence after the intended SQL change rather than weakening the source checks. No permanent old-table views or backups, automatic release selection, provider calls, or space-savings claim belongs in this plan. Root-owned independent SPEC then QUALITY review precedes implementation.


## Root acceptance, 2026-10-15

Sequential fresh SPEC and QUALITY accept docs-only101c121e. SPEC independently
expanded and executed the draft DDL/UPSERT, checked19 affinity cases against
both literal source variants, and ran11 source tests; QUALITY independently
checked the complete plan/source/codec/caller boundary and ran11 source tests.
The next implementation unit is the unselected declaration/storage/oracle only.
Later caller integration must preserve the existing reconciliation order:
authoritative folders, inactive accounts, then unavailable folders, including
overlapping inputs. No target schema or migration has been selected or run.
