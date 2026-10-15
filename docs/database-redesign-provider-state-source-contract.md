# Provider-state source contract (2026-10-15)

This is bounded **source evidence** for `email_provider_observations` and
`email_provider_folder_observation_generations`. It does not choose a target
representation or authorize a migration, retirement, provider call, or live
database action.

## Provenance and scope

- Frozen metadata:
  [config/state native source](superpowers/specs/2026-10-15-database-redesign-config-state-native-source.json),
  `groups.provider_state`, `actual.tables`, and `fresh.tables`. The capture
  binds published producer `7dadc492c1c62d5b0624f2f97dd0c89f4f358646`,
  Store `2026-10-13.5`, Email `51`. The capture's 72,141 observation and
  55 generation rows are frozen informational counts, not current counts.
- The original
  [field map](superpowers/specs/2026-10-11-database-redesign-field-map.csv)
  has exactly 12 rows for these two tables. It maps each field to
  `provider_state.payload.<same field name>` and preserves source SQL type,
  nullability, and PK position. This proposed CSV destination is **not** a
  selected physical target.
- `tests/test_provider_state_source.py` executes the literal captured DDL
  and explicit index SQL in separate private in-memory databases for both
  actual and fresh variants. It checks all columns, defaults, PK positions,
  hidden fields, index order/collations, and the absence of FK, triggers,
  sequences, `WITHOUT ROWID`, integer PK alias, and `AUTOINCREMENT`.
  No source rows, private configuration, provider network, or native Agent
  CLI are involved.

## Exact field and constraint map

The source has no column defaults. `TEXT` and `INTEGER` below are SQLite
affinities, not strict storage-class declarations. The two composite PKs use
native `BINARY` collation and explicitly non-null components.

| Table | Source ordinal actual / fresh | Field | Type | Null | PK | Source constraint and application meaning |
| --- | ---: | --- | --- | --- | ---: | --- |
| observations | 0 / 0 | `account_id` | TEXT | no | 1 | nonblank; mailbox scope of identity |
| observations | 1 / 1 | `stable_message_identity` | TEXT | no | 2 | nonblank; stable message key within account |
| observations | 2 / 2 | `state` | TEXT | no | 0 | one of `available`, `unavailable`, `excluded` |
| observations | 3 / 3 | `provider_folder_id` | TEXT | yes | 0 | current folder; cleared by authoritative tombstone or inactive account |
| observations | 4 / 4 | `provider_folder_name` | TEXT | yes | 0 | current display name; cleared with folder identity above |
| observations | 5 / 5 | `category_key` | TEXT | yes | 0 | active bound category, or `junk` for spam/trash; null otherwise |
| observations | 6 / 6 | `important` | INTEGER | yes | 0 | null or 0/1; scan requires a boolean and stores 0/1 |
| observations | 7 / 8 | `observed_at` | TEXT | no | 0 | nonblank; last changed message fact or tombstone time |
| observations | 8 / 7 | `important_signals_json` | TEXT | yes | 0 | null or `json_valid`; scan writes a JSON list of nonblank names |
| generations | 0 / 0 | `account_id` | TEXT | no | 1 | nonblank; mailbox scope |
| generations | 1 / 1 | `provider_folder_id` | TEXT | no | 2 | nonblank; folder scope |
| generations | 2 / 2 | `observed_at` | TEXT | no | 0 | nonblank; latest scan generation for this account/folder |

The actual observation table gained `important_signals_json` after its
original `observed_at`; the published fresh declaration places signals
before time. Positional copying would therefore swap fields. Both variants
have the same nine named observation fields and three generation fields.

Native SQLite accepts numeric `"1"` into `important` as stored integer 1,
and `"42"` as valid JSON text. It accepts null optional folder/category/
important/signal fields. It rejects blank required keys/time, invalid state,
important outside 0/1, malformed JSON, and duplicate composite keys.
`account-a/message-a` and `account-a/MESSAGE-A` are distinct, as are the
same message or folder keys under different accounts. These source tables
have independent identities: there is no FK to `email_accounts`,
`email_classifications`, or each other.

Native `UPDATE` and conflict `DO UPDATE` keep the hidden ROWID for a
composite key; `DELETE` removes the row, and a later insert may reuse a
ROWID because these tables have no AUTOINCREMENT stream. The provider
methods use the composite keys, never these hidden ROWIDs or `lastrowid`.
The scoped `app/` search found no rowid/lastrowid consumer of either table.
The source point lookup uses
`sqlite_autoindex_email_provider_observations_1`; folder membership uses
`idx_email_provider_observations_folder(account_id,provider_folder_id)`;
generation lookup uses its composite-PK autoindex. The former separate
`idx_email_provider_observations_lookup` is dropped in the current
bootstrap/release path and is absent from both frozen variants.

## Current application and caller contract

The five `EmailStore` provider writer/projection method ASTs in the current
`ac60db6d` worktree are identical to the genuine published `7dadc492`
Git blob. This comparison covers
`record_current_provider_observations`,
`record_provider_important_signals`,
`get_provider_classification_state`,
`get_provider_classification_states`, and
`_provider_state_from_observation`. It does not imply that every surrounding
bootstrap, route, or producer is unchanged.

| Path | Existing caller/consumer | Exact use |
| --- | --- | --- |
| Scan publish | `app/email_training_observer.py` `ProviderTrainingObserver.observe` | Supplies observations, explicit authoritative/unavailable folder sets, active account IDs, and scan time to `record_current_provider_observations` after its cached observation-state write. |
| Direct signal change | `app/web_api/email.py` star/important endpoint | After provider `set_important_signal`, calls `record_provider_important_signals(account_id, stable_message_identity, names)`; the store updates an existing **available** row only and returns whether one row matched. |
| Page projection | `app/web_api/email.py` classification list | Calls `get_provider_classification_states(row_ids)`; both single and page methods join classification identity to observation, then generation by account/folder. |
| Single projection | `EmailStore.get_provider_classification_state` | Reads one classification and its current observation; missing classification and missing provider fact produce distinct reasons. Direct `app/` search found no additional static caller of this single method. |
| Bootstrap | `app/email_current_schema.py`, `app/email_store.py` | Fresh DDL, legacy column ensure, v43-to-v44 generation backfill, and index maintenance preserve the named source shape. |

The scan writer validates nonblank account/message/folder ID/name, boolean
`provider_important`, nonblank string signal names, and nonblank scan time
before its transaction. Identity is `(account_id,stable_message_identity)`;
moving a message to another folder updates one row in place. Unchanged
facts leave that row's `observed_at` intact while the generation row
advances. The projection returns `coalesce(generation.observed_at,
observation.observed_at)`. An observation can exist before any
classification; classifying later makes that fact visible through the
classification projection.

An omitted message in a partial scan is retained. A listed unavailable folder
marks its existing rows unavailable and clears category/importance/signals,
while retaining folder ID/name so the unknown folder can be tracked.
Only an explicitly authoritative folder may tombstone missing membership;
that update clears folder ID/name and associated category/importance/signals.
`active_account_ids=None` skips account reconciliation; an explicit empty
sequence marks all existing accounts unavailable and clears their folder
facts. The writer stages membership in a connection-local temporary table,
then makes observation upserts, reconciliation, and generation upserts inside
one existing `BEGIN IMMEDIATE` transaction. A synthetic failure in the
generation update rolls back an earlier observation change in that call.

For an **available** row, the application signal projection is stricter than
the native `json_valid` CHECK: JSON must decode to a list of nonblank
strings, or it raises `EmailPersistenceCorruption`. An available row projects
as `junk`, `categorized`, or `unclassified` from `category_key`.
Unavailable/excluded rows return their provider-truth reason and time before
signal JSON decoding; a native-valid scalar in those rows is not shape-checked
on that projection path.
Star/Important flags derive from recognized signal names, while
`important` also remains a separate boolean field. A Star/Flag write
does not create a provider observation and cannot change a nonavailable row.

## Proof boundary and unresolved choices

The focused tests verify literal SQLite source behavior and current
application methods against a synthetic database, including an injected
transaction failure. They do not show current production row counts, a
copied production migration, a provider response, or target behavior.
The target design must still decide how to preserve the two independent
composite identities, folder-generation timestamp overlay, authoritative
versus partial membership, and available-row signal-list projection. The hidden
ROWIDs and historical lookup index have no observed provider caller
contract, but retiring any implementation detail belongs to the later
target/release proof.
