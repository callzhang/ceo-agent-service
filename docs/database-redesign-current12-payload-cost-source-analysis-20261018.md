# Current12 payload and index cost: source-only analysis

## Evidence boundary

The immutable R3 actual-copy receipt is
`docs/superpowers/specs/2026-10-17-email-canonical-current-copy.json`
(SHA256 `1d9d2edb43e9d8470bbbdbd37177678c2f72e01a403cfd228223092825493b09`).
Its same-input first target is 554,467,328 active bytes versus 503,767,040 in
the source: **+50,700,288 bytes / +48.35 MiB / +10.06%**. The complete
116→12→116→12 data and schema proof passed; capacity did **not** pass.
The receipt contains `dbstat` object allocations, not row bodies, per-kind
table bytes, or an attribution of the net change. It is a candidate-copy
measurement, not current-production size. This note opened no database.

The inspected representation files match their `runtime_sha256` entries in
that receipt: `app/action_storage.py`, `app/action_email_definitions.py`,
`app/action_email_storage.py`, `app/task20_definitions.py`,
`app/task20_schema_release.py`, `app/task12_storage.py`, `app/run_storage.py`,
`app/runs11_definitions.py`, and `app/runs11_schema_release.py`. The receipt's
first target reports these object allocations:

| Owner | Table MiB | Index MiB | Combined MiB | What the receipt resolves |
|---|---:|---:|---:|---|
| Actions | 169.81 | 32.55 | 202.36 | One folded table and 128 index objects; no per-`record_type` row bytes |
| Runs | 80.36 | 49.74 | 130.10 | One folded table and 30 index objects; no per-kind row bytes |
| Tasks | 58.34 | 10.38 | 68.73 | One folded table and 51 index objects; no per-kind row bytes |

Those combined sizes must not be compared directly to one old table: each
container absorbs several source families. Nor may their sums be called the
net increase; the source's other tables and indexes also disappeared or
changed. The separate current12 capacity note records 9.48 MiB of free pages
and unused space inside occupied pages, which are different quantities.

## Actions: required keys, scalar wrappers, and physical indexes

`SOURCE_COLUMNS`, `PAYLOAD_FIELDS`, and the reference map choose each older
Action kind's finite fields: primary identity becomes `legacy_key`, declared
relationships become physical FKs, and the other fields become `payload_json`
(`app/action_storage.py:101-179`). `reply_attempts` has 54 current source
fields plus historical-variant handling; the email and other added kinds use
their own finite declarations (`app/action_storage.py:138-153`,
`app/action_email_definitions.py:638-683`). The table stores the JSON text and
physical FK columns; ordinary and email source-shaped projections are
`VIRTUAL`, so they do not store a second table value by themselves
(`app/action_storage.py:368-435`, `app/action_email_definitions.py:640-663`).
An index on a virtual column *does* store the indexed value in its B-tree.

The source-shaped JSON repeats key names and JSON punctuation per row. Current
writers fill every field, including evaluated defaults or `null`, for Email,
sync, and task-delivery Actions (`app/action_storage.py:1157-1167`,
`1328-1337`, `1434-1445`); the original six-family migration likewise writes
all selected payload fields (`app/action_storage.py:1940-1954`). This is
encoding overhead, not evidence that two independently stored business facts
are duplicates. The per-kind `CHECK` requires the prescribed types and
`json_remove(...allowed paths...)='{}'` excludes extra keys
(`app/action_storage.py:250-268`, `app/action_email_definitions.py:672-683`).
Omitting a default or nullable key under this declaration would fail the
schema or alter the source-shaped readback. A key-elision/default change needs
a finite per-kind decoder, the original SQLite values and storage classes,
the same current public reads, and a new inverse proof; it is not a safe
serialization-only edit.

Native Action writes encode BLOB as hex and finite REAL as a tagged
significand/exponent pair, with tagged infinities
(`app/action_storage.py:999-1046`). The generated decoder and strict tag check
are `app/action_email_definitions.py:606-635`; the reply-path variant uses its
own decoder/check (`app/action_storage.py:270-288`, `395-401`). These wrappers
can add bytes, but they preserve SQLite scalar type/value behavior for native
bindings, including cases ordinary JSON cannot represent. No present receipt
measures how many such values occur. Replacing this with bare JSON numbers or
strings would need exact scalar and inverse validation; there is no supported
blanket codec switch from source inspection alone.

The receipt's largest Actions indexes are `idx_actions_email_public_key`
(4.89 MiB), `idx_actions_postfix_identity` (3.07 MiB),
`idx_actions_public_id` (2.88 MiB), and two reply-history indexes (2.73 and
2.49 MiB). The first two are full unique indexes across Actions
(`app/action_email_definitions.py:685-702`, `app/action_storage.py:514`):
the email key is the declared parent of composite FKs, and the postfix key is
the parent of the postfix receipt/dispatch FK
(`app/action_storage.py:445-447`). Their entries may contain NULL projected
values for unrelated kinds, but converting them to partial unique indexes
would not provide SQLite's required full parent key for those FKs. The public
ID and reply/history indexes have separate predicates/key orders
(`app/action_storage.py:484-535`); similar names do not prove redundant
constraints or query behavior. No index removal is supported here.

## Runs: typed columns and scheduled index cost

The main Run migration maps source fields to physical typed columns,
retiring only exact empty trajectory/result placeholders after validating
them (`app/run_storage.py:378-399`, `413-497`, `1065-1125`). Its inverse
reconstructs source values through explicit projections
(`app/run_storage.py:1141-1178`). Later Runs11 kinds add typed columns and
require non-applicable added columns to be NULL; they are not a source-shaped
outer JSON payload (`app/runs11_definitions.py:204-270`). Workbench event
`payload_json` is one actual source field, not a general Run codec
(`app/run_storage.py:782-826`). Thus a generic JSON-key explanation for the
80.36 MiB Run table is unsupported. Wide-row headers, repeated public
`legacy_key`, real non-null facts, and page utilization are possible costs;
the receipt cannot apportion them.

The measured 49.74 MiB of Run indexes is concrete. Five scheduled-run indexes
account for 9.53, 9.22, 7.90, 6.80, and 4.56 MiB respectively: dispatch,
claim, event identity, scheduled-instant uniqueness, and latest-by-task.
Their declarations have distinct leading columns, order, predicates, and
uniqueness roles (`app/run_storage.py:827-839`). The dispatcher reads due
pending rows in schedule order and separately checks leases
(`app/dispatcher/adapters.py:110-145`, `197-231`); History uses latest by
owning task (`app/history_types.py:157-167`). Existing query-plan tests
assert the claim and latest indexes (`tests/test_scheduled_task_store.py:957-985`,
`1027-1050`). These facts explain why a name-based cancellation or a single
replacement index is not justified. A narrower predicate or altered key order
could preserve uniqueness yet regress the due/lease/History access paths;
that is a distinct query-plan and performance decision.

## Tasks: finite source-shaped payload

The twenty source kinds have explicit fields, affinities, nullability,
defaults, and storage locations (`app/task20_definitions.py:9-170`,
`528-537`). Every field marked `payload` is placed in the outer JSON object;
physical parent/current-input pointers and `legacy_key` are separate.
`app/task20_definitions.py:665-735` creates source-shaped **VIRTUAL** columns
and requires each allowed payload key, its encoded value form, all applicable
NOT NULL facts, and no extra keys. Source-to-target copy constructs all
declared payload fields (`app/task20_schema_release.py:183-224`). Native
insertion fills omitted fields with the source SQL defaults or JSON null and
serializes through the same native scalar codec
(`app/task12_storage.py:184-221`). Public and inverse projections read the
typed virtual columns and resolve parent public IDs
(`app/task20_definitions.py:954-977`,
`app/task20_schema_release.py:240-263`).

This makes repeated names, punctuation, explicit default values, and nullable
keys a credible cost lead for Tasks. It does not show which kind contributes
most of the 58.34 MiB table, or how much is nested source data such as
`work_summary_inputs.payload_json` (`app/task20_definitions.py:66-83`).
There is no source-supported rule here to delete a body or retain it for an
invented age window. Moving a query key to a physical column would preserve
facts only if its finite type, default, NULL behavior, writes, indexes, and
source/public/inverse projections were mapped together. A `STORED` generated
column would add another stored copy; the present generated projections are
`VIRTUAL` (`app/task20_definitions.py:665-735`). The 10.38 MiB index subset
includes published and native source query/unique contracts plus full unique
retained-child FK endpoints (`app/task20_definitions.py:626-650`,
`746-815`). Index names or equal leading fields alone do not license removal.

## Narrow next measurement after a stable source freeze on a disposable copy

1. Freeze the same input and runtime hashes as R3, then make a **new disposable
   same-input source/target copy** under the existing copy protocol. Do not
   query the production source for this analysis. Report only aggregates:
   record counts and `sum(length(CAST(payload_json AS BLOB)))` by
   `record_type` for Actions/Tasks; for Runs, counts plus per-kind physical
   column storage-class/byte histograms. Include NULL/default counts, scalar
   wrapper tag counts, and nested-JSON byte totals by declared field. Emit no
   row bodies, identifiers, source text, or sampled values. This separates
   plausible key/default/wrapper overhead from actual content without
   changing the data.
2. On that target, retain `dbstat` **per-object** page/unused bytes, index
   cells and declared SQL, and capture relevant `EXPLAIN QUERY PLAN` for
   scheduled claim/dispatch/History and Actions FK/lookup paths. Compare with
   the same source's family/table/index allocations. Page-level table bytes
   are not per-kind bytes; if a kind-level byte estimate is needed, label it
   as serialized payload bytes, not SQLite active bytes.
3. Only if a finite alternative then merits selection, build a paired
   disposable candidate from the *same* source and compare active bytes and
   per-object pages against the unchanged target. Require exact source
   identity, SQLite values/storage classes, public reads, constraint/FK and
   query-plan/performance behavior, then target→source inverse. Evaluate one
   scoped representation or index change at a time. This note selects none.

The design's body-free provenance and ten-core-plus-ledger target remain the
acceptance destination (`docs/superpowers/specs/2026-10-11-database-redesign-design.md:26-49`,
`100-123`). This analysis neither relaxes its business-fact/receipt retention
nor promotes a capacity result from the current R3 receipt.
