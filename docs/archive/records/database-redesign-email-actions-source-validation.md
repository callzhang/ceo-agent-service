# Email action source oracle, 2026-10-13

The frozen source inventory covers nine existing email action tables (103 columns)
and four dependent tables (41 columns). `tests/test_email_action_source.py` and
`tests/test_email_action_children_source.py` execute the captured literal SQLite
DDL and objects in memory. The combined oracle builds all thirteen tables for
both captured parent variants and compares every child table's SQL, column
metadata, foreign keys, indexes, index columns, and own triggers. It also
matches the four child object sets against each variant's frozen related-object
capture, the twelve inbound foreign keys among the thirteen tables, and all
thirteen de-duplicated external trigger SQL declarations (eight from the nine-table
capture and five from the child extension).

The focused native cases establish source behavior that a later storage design
must account for: nine nullable `TEXT` public primary keys admit multiple
`NULL` rows; `email_actions` exposes an independent ordinary signed `rowid`
that can be updated, reused after deleting the highest row, set to zero or a
negative value, and allocated randomly at the signed maximum; its `TEXT`
identity can contain BLOB bytes. The direct retry child has an immutable
composite identity and a restrictive action parent. Feedback permits a null
`expected_current_action_plan_id`. The reply claim and receipt retain
restrictive plan parents, while SQL permits any valid receipt JSON update;
the application narrows enrichment to a previously empty SMTP acceptance ID. The
external dispatch triggers block account and message changes while a claim is
dispatching in the narrow synthetic setup.

The fixture substitutes only the classification, account, and message tables
needed for foreign-key and trigger closure; their full production definitions
and all other EmailStore objects are outside this test. These checks establish
captured source SQLite semantics, not target design, migration correctness,
runtime behavior, provider effects, native-file compatibility, or production
row correctness.

Focused verification: `python -m pytest -q tests/test_email_action_source.py
tests/test_email_action_children_source.py --basetemp=/tmp/email13-source-oracle-pytest`
reported **36 passed**. `ruff check tests/test_email_action_children_source.py`
passed. The task-owned temporary pytest directory was cleaned after validation.

## Independent acceptance

Fresh sequential SPEC and QUALITY reviews accepted source-oracle commit
`cf47ee0eb4852e84a9b8b6457448cad390216d2c`. Each independently passed the same
36 focused cases, Ruff and whitespace checks; their owned temporary directories
were removed. SPEC also checked the frozen parent capture digest against the
child inventory and the receipt writer's actual enrichment boundary. These
counts overlap and do not represent additional distinct scenarios.

Fresh sequential SPEC and QUALITY then accepted docs-only implementation plan
and field-map commit `6a273df766e900bd7792bfe204e2ec346c7f6cb8`. Independent
metadata comparisons found exactly 144 unique source fields across thirteen
families, with no missing/extra fields or source-type/nullability/key mismatch.
The plan explicitly addresses the shared classification-parent constructor
dependency and the existing refusal of independent simultaneous Store/Email
schema changes. Those mechanisms remain implementation gates, not working
code proven by this document. The full ten-core-table goal remains incomplete.

## Actual published E predecessor, 2026-10-14

The [published-source capture](superpowers/specs/2026-10-14-database-redesign-email-actions-published-source.json) and [source map](superpowers/specs/2026-10-14-database-redesign-email-actions-published-source-map.md) bind one production read transaction at checkout `eb797e6bd35de695c269054a2e697af8fde2baa5` before and after, Store `2026-10-13.5`, Email `51`. The `actions` object manifest is exactly the default sixteen-type `actual_no_memory_fk` declaration; all thirteen Email tables and 144 column definitions match the frozen actual variant. All thirteen external blockers, twelve inbound FKs, source indexes, own triggers, and native sequence entries were captured without recording business row values. The actual claim column order is the frozen appended-audit-column variant. The newer checkout and 122 `sqlite_schema` table entries, including internal `sqlite_sequence` (121 user tables), do not change this source match.

The two scoped source-oracle files now cover each of the thirteen external blocker paths, both action attempt-count trigger directions, immediate FK failure, BLOB composite public keys, and `INSERT OR REPLACE` refusal for the immutable retry identity, in addition to their earlier rowid/public-key cases. `python -m pytest -q tests/test_email_action_source.py tests/test_email_action_children_source.py` with an owned `/tmp` `TemporaryDirectory` basetemp reported **68 passed**. This is source-only SQLite evidence. It does not establish target migration, inverse/copy acceptance, old-code compatibility, provider effects, or UI readiness.

Independent SPEC review found one missing ordinary-ROWID contrast: unlike an AUTOINCREMENT table, an ignored duplicate carrying an explicit signed-maximum ROWID does not advance `email_actions` allocation. The source-only regression executes on both frozen physical variants and verifies the next ordinary insert receives ROWID 2 after an existing ROWID 1. The same two oracle files now report **70 passed** in a cleaned owned `/tmp` basetemp; Ruff and whitespace checks pass. This repair changes no target storage or production state.


## Published-source independent acceptance

Root accepted Task1 at `467b4dc2` after sequential independent SPEC then QUALITY.
SPEC initially found the missing ordinary-rowid ignored-high contract; the
separate repair proves ignored explicit maximum ordinary rowid does not advance
allocation, in both frozen variants. SPEC recheck independently passed70 cases;
QUALITY independently passed the same70 cases, Ruff, compilation and diff check.
The counts overlap. Both accepted exact actual13-table/144-field metadata,
13 external blockers,12 inbound FKs and the full69-object sixteen-type actual
actions declaration. No application code, target marker, constructor, provider
or production data changed. Actual-copy/genuine-old-code proof is still pending.
