# Four-kind Business storage acceptance, 2026-10-15

Accepted implementation: `c9c2c74fda5b95f17400b4c32f5d2b6c6aa7f711`, based on
the independently reviewed executable plan at `cc0d135b28a6a1196101597b74de28bca49dbc68`.
Worktree: `codex/database-redesign-business-source-20261015`.
The implementation is unselected: no existing Store constructor or business
caller creates or uses the new table, and no actual data was migrated.

## Scope and native behavior

`app/business_definitions.py` and `app/business_storage.py` supply one coherent
finite storage unit for Task19, Project12, Anchor6 and Project context revision5:
all 42 existing fields, 23 source CHECKs, seven translated logical indexes and
three structural identity indexes. Four public allocation streams remain
independent of the new positive physical Business IDs.

Original public FK values and physical typed endpoints are bound by native
composite FKs, including Project's actual Anchor type. This retains native
NO ACTION when a referenced public ID changes, nullable-edge pairing and
self-merge CHECK precedence. Missing parents reach native FK checking after
ordinary row constraints, preserving INSERT/UPDATE IGNORE behavior. Existing
SQLite value codecs preserve bindable scalar affinity, BLOB, infinities,
SQL NULL/NaN and exact JSON text. Omitted row defaults share one SQL statement.

Ordinary writes require and retain the caller's transaction. Public ID
exhaustion explicitly matches actual source plain INSERT/lastrowid writers:
SQLITE_FULL rolls back that transaction. The independent native source matrix
distinguishes ordinary INSERT from INSERT RETURNING, which retained the
transaction on local SQLite 3.53.4. Using RETURNING as the source oracle would
have tested a different statement from the real callers. No runtime version
fallback or new API mode was added.

## Independent verification

The plan's source-field SPEC comparison verified all 42 names, affinities,
NOT NULL flags, defaults, actual/fresh column orders and 23 CHECK expressions.
Its reviewer also ran eight native-only IGNORE exhaustion controls: all four
kinds and both schemas raised SQLITE_FULL and rolled back the caller transaction.
Fresh plan QUALITY review independently compared 798 scalar inserts and 1,848
ordinary/IGNORE updates against captured native source SQL: zero mismatches.
Those tests used in-memory data and are planning evidence.

For the committed implementation, the implementer first observed collection
exit 2 with the declarations absent, then exit 2 with the writer absent. After
adding the coherent unit:

- New storage suite: 414 passed.
- Storage plus `tests/test_business_core_source.py`: 450 passed.
- Ruff, compilation and diff checks passed; pytest cache was disabled.

Fresh implementation SPEC verified all three Python files byte-for-byte against
the reviewed plan, both behavior-document insertions exactly, and unchanged
pre-existing contents. Exactly six owned files changed. Subsequent fresh QUALITY
review found no actionable issue and independently ran the 414 storage cases:
414 passed in 4.05 seconds on SQLite 3.53.4. It also ran Ruff without cache,
compiled the modules without bytecode writes and checked the diff. Its temporary
pytest root was removed and its absence verified. Native controls used only
closed in-memory connections. No full local suite or model/provider flow ran.

## Remaining boundary

All 19 incoming FK groups from 15 retained child families and the existing
Signal/document/evidence/date/event endpoints still need explicit caller/schema
conversion before a partial release. No constructor selection, schema version,
migration/inverse, copied-database acceptance, production readback, push or deploy
is certified by this storage-unit acceptance. The 158 unproved historical bodies
remain a separate final-retirement dependency; this unit neither copies nor
retires them. The approved ten-core-table-plus-ledger redesign remains incomplete.
