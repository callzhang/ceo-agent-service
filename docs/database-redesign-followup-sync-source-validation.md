# Follow-up / Todo-sync source preparation validation

This is preparation for the nine still-physical families assigned to actions by the approved design. The captured live source is production commit `9a2a23fcaad6eb73f6131d5b7a70d22ae02418cd`, Store `2026-10-13.4` (Store18), latest Email `51`. Capture time is `2026-10-14T05:42:16.304289+00:00`; the production commit was unchanged at capture end. It is not the future publication predecessor following E or Email. Candidate navigation is bound separately to `406ec60c`; no new version or defaulting decision was made.

Evidence:

- `docs/superpowers/specs/2026-10-14-database-redesign-followup-sync-source.json`: exact two-variant declarations, PRAGMAs, indexes/xinfo, FK and external schema closure, native sequence metadata, non-sensitive aggregates, code fingerprints and finite navigation inventories.
- `docs/superpowers/specs/2026-10-14-database-redesign-followup-sync-source-map.md`: source roles, field lists, current caller paths, receipt/recovery semantics and coupling to the existing business/provenance/Dispatcher objects.

## Read boundary

The live database was opened through a `mode=ro` SQLite URI, with `query_only=on`, and all metadata, source marker rows and field aggregates were captured in one read transaction. No business row values, row hashes, credential values, provider payload bodies, original native files or trajectory contents were exported. Counts/lengths/storage-class distributions are aggregate metadata; length is `length(CAST(column AS BLOB))`, not page occupancy or recoverable space. No provider/native command or live database mutation ran.

Nine-family source PRAGMAs were captured directly. Related-table exact DDL/FKs/objects came from the same transaction; their additional xinfo is explicitly marked as in-memory declaration replay. The direct closure is two incoming FKs, five outgoing FKs and one external SQL object (`business_legacy_links`). This does not claim a whole-repository dependency graph or extend the implementation scope to all referenced business parents.

## Genuine fresh-source variant

A task-owned `git archive` of the exact production commit's `app/` was used in a new subprocess, with cwd and PYTHONPATH pointing at the archive. `AutoReplyStore(Path(synthetic_empty_db))` followed by `EmailStore(Path(synthetic_empty_db))` constructed the fresh source. A separate import-path assertion verified that `app.store` and `app.email_store` resolve under the archive, and the JSON records their source hashes. No candidate constructor or copied production rows supplied the fresh variant.

All nine actual/fresh field sets match the approved CSV: 150 fields total, zero additional/retired fields. Actual row counts are 5,468 / 2,477 / 65 / 0 / 0 / 242 / 0 / 875 / 0 in the source-map family order. The genuine variant differences are retained: historical `follow_up_drafts.updated_at DEFAULT ''` versus fresh `current_timestamp`, historical follow-up/link column order, and the rebuilt legacy outbox's literal declaration/index whitespace. No normalization or behavior change is proposed.

All nine identities are independent INTEGER PRIMARY KEY AUTOINCREMENT ROWID aliases. PRAGMA notnull=0 does not make an integer-PK public identity nullable. No ordinary non-AUTOINCREMENT ROWID, composite or nullable public-ID variant occurs in this group. Five native sequence entries exist; four business families have no entry, which is distinct from a saved zero.

## Metadata assertions

An inline disposable validator replayed the exact nine table declarations and explicit index statements into in-memory SQLite `3.53.4`, separately for actual and genuine fresh source. It passed 1,103 assertions, counted by category in the JSON:

| Assertion group | Count |
|---|---:|
| Exact table_xinfo, exact FK list and exact index-name sets | 54 |
| Index unique/origin/partial properties and exact index_xinfo | 96 |
| AUTOINCREMENT identity declarations and absence of owned triggers | 36 |
| Per-column row counts, storage-class sums and NULL/empty bounds | 900 |
| Total counts, CSV field sets, external SQL set, source commit stability and CHECK extraction | 17 |
| Total | 1,103 |

The source declares eight CHECK expressions, 24 indexes, five outgoing FKs and zero owned triggers. The validation checks metadata fidelity and aggregate arithmetic; it does not test target storage, migration/inverse, source writer equivalence, effect idempotency, native acceptance, provider delivery, CI or deployment. No runtime/test code was changed and no pytest suite ran.

## Scoped caller verification

The finite Python AST inventories record 193 literal table-name sites and 113 attribute references in both captured code variants, with 54 Store method dependency entries. Method values passed to `_all_pages` are included; generic constructor-name collisions are excluded. This is navigation evidence, not type inference or proof that each symbol is active. Every business-path statement in the source map was checked against the relevant code: button-only current follow-up send, historical legacy drafts, both Dispatcher outbox consumers, native provider-ID/readback receipts, unknown-create settlement, Task completion, History/Attention/quality-gate and explicit backfill/import paths.

The graphify skill was used only as a temporary code navigation aid on seven scoped files: 28,026 words, 337 nodes and 856 edges, deterministic structural extraction, zero semantic model tokens. The detector itself reported the scoped corpus fits one context. Source code, not graph edges, supports the conclusions. No graph or code archive is retained as a historical backup.

## Cleanup and scope

Cleanup completed and verified: `/tmp/ceo-followup-sync-source.qECbIs` (resolved `/private/tmp/ceo-followup-sync-source.qECbIs`) was removed and its absence asserted before commit. This removed the exact-code archive, synthetic empty SQLite database and sidecars, scoped code/graph/cache and disposable scripts. The JSON records the UTC cleanup receipt. No production database backup was created or retained.

No Email source artifacts, runtime modules, tests, architecture/runtime behavior contract, plans, target schema/version, provider state or production data were edited. No push or deployment is part of this unit. The source-only documentation is ready for the root's separate independent review after commit; downstream implementation remains separately owned.
