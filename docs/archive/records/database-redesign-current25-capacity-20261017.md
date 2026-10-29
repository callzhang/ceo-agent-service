# Current25 copied-database capacity, 2026-10-17

Derivative of accepted `2026-10-17-business-candidates-current-copy.json` SHA256 `5c5a0e30f2a8dde986e08a7aa5f98a8c1de4d6801740f6bdd4e200f2c15ec09e`. Consistent production online backup opened URI mode=ro/query_only; complete116→25→116→25 PASS738.207s. This is a copied-database page/dbstat measurement, not a new production file-size/WAL observation. MB below means 1,000,000 bytes.

| Metric | Source | First target | Final after synthetic writes |
|---|---:|---:|---:|
| Allocated page bytes | 502,329,344 | 560,893,952 | 561,041,408 |
| Active page bytes | 502,312,960 | 560,893,952 | 561,041,408 |
| Free pages | 4 | 0 | 0 |

First target grows by 58,564,608 allocated bytes. Active growth is 58,580,992 bytes: table pages +40,087,552, index pages +16,920,576, internal schema pages +1,572,864. The16,384-byte difference is source free pages. This is an additive allocation decomposition, not proof that any particular codec or index caused the growth.

| First target table | Data MB | Index MB | Total MB |
|---|---:|---:|---:|
| actions | 176.378 | 32.121 | 208.499 |
| runs | 83.714 | 51.757 | 135.471 |
| tasks | 61.149 | 10.879 | 72.028 |
| email_classifications | 37.310 | 3.400 | 40.710 |
| email_messages | 34.730 | 1.610 | 36.340 |
| provider_state | 14.361 | 12.784 | 27.144 |
| business | 10.469 | 1.290 | 11.760 |
| state | 6.562 | 2.707 | 9.269 |
| configs | 7.348 | 1.389 | 8.737 |
| business_source_documents | 7.340 | 0.025 | 7.365 |
| sqlite_schema | 1.995 | 0.000 | 1.995 |
| business_task_signals | 0.684 | 0.430 | 1.114 |
| business_task_evidence | 0.049 | 0.070 | 0.119 |
| business_attention_events | 0.090 | 0.004 | 0.094 |
| email_historical_classification_history | 0.066 | 0.020 | 0.086 |
| business_task_anchor_links | 0.020 | 0.008 | 0.029 |
| schedules | 0.016 | 0.008 | 0.025 |
| business_legacy_links | 0.004 | 0.016 | 0.020 |
| business_task_relations | 0.004 | 0.012 | 0.016 |
| email_historical_operations | 0.004 | 0.008 | 0.012 |
| business_work_cluster_tasks | 0.004 | 0.008 | 0.012 |
| business_attention_tasks | 0.004 | 0.008 | 0.012 |
| business_attention_proposal_tasks | 0.004 | 0.008 | 0.012 |
| business_project_evidence | 0.004 | 0.004 | 0.008 |
| business_task_date_evidence | 0.004 | 0.004 | 0.008 |
| sqlite_sequence | 0.004 | 0.000 | 0.004 |
| email_schema_migrations | 0.004 | 0.000 | 0.004 |

Final has no virtual or shadow objects. Target freelist is zero; merely running another VACUUM cannot reclaim58.56MB of free pages. Per-page unused space is not equivalent to freelist space and is not all recoverable. The first target is the cleaner migration comparison; final includes explicit synthetic current and archived-old writes.

Next capacity work must measure source versus target retained payload/row bytes and exact query-index use on the same input before changing representation/indexes. Do not infer that fewer tables saves space, delete retained business results/receipts, or introduce the unapproved30day polling retention policy. Final table consolidation, complete final-schema acceptance and production publication remain open.

## Source-to-target allocation grouping and Task codec diagnosis

The allocation grouping below uses only the same accepted copy receipt. Existing target tables keep their names; retired tables use the approved CSV destination plus already-implemented compiler mappings for search metadata and two later Email Action sources. Derived FTS/empty-retired pages and SQLite internals are separate. This is physical allocation accounting, not a new whole-row mapping verification.

| Group | Source data+index MB | Target data+index MB | Change MB |
|---|---:|---:|---:|
| actions | 178.704 | 208.499 | +29.794 |
| tasks | 58.270 | 72.028 | +13.758 |
| runs | 131.695 | 135.471 | +3.777 |
| business | 10.088 | 11.760 | +1.671 |
| configs | 7.152 | 8.737 | +1.585 |
| state | 4.850 | 9.269 | +4.420 |
| provider_state | 25.039 | 27.144 | +2.105 |

Largest same-copy growth is Actions +29.794MB (data +19.980MB, indexes +9.814MB), then Tasks +13.758MB (data +10.809MB, indexes +2.949MB). Exact bytes, including the two later Email sources, are recorded in the companion allocation JSON.

Separately, a later readonly snapshot streams all20 genuine Task families (36,190rows) through the exact current serializer: payload53,686,777B; native scalar payload estimate42,861,444B; JSON keys/separators8,425,465B; encoded-value overhead2,399,868B. Source8 facts match the initial8-only diagnostic exactly across those two diagnostic snapshots. Native scalar estimates exclude SQLite record headers/page packing, and this later snapshot is not asserted identical to the738s migration copy. The current serializer measurement identifies real representation costs; it does not itself prove page savings from a changed codec.

Dropping nullable NULL entries is only a92,208-byte serialized candidate under current data, and the current schema explicitly requires those keys. Even indiscriminate empty/null/zero omission is an unverified upper bound2,413,382B; it would require exact default/NULL semantics and full roundtrip checks. Do not spend a broad migration rewriting NULL handling as if that solves58.56MB growth. No encoding was changed.

Native meeting input source_json is11,910,033B over757terminal rows;10,187,284B decoded scheduled_consumer content occurs in290rows. All290prompts exactly match their existing scheduled run/task/snapshot identity. Protocols have4hashes/6,680,500decoded bytes; structured materials279occurrences/4hashes/3,266,547B, but only93occurrences/466,317B currently match canonical managed revisions exactly. Remaining186material occurrences have2hashes and are not proved canonical by this probe. This identifies duplicate input/cache content for a separate provenance/reader-and-lifecycle unit, not safe blanket source_json deletion. Calendar linkage/metadata/business results/receipts must remain. No retirement executed.


## Exact meeting context reconstruction follow-up

Later readonly genuine-builder proof `superpowers/specs/2026-10-17-meeting-context-canonical-rebuild.json` SHA0aee70892418c7fa5d60bb38916a2cd93be9fee9bc62b25644c7474d8d626730 tests all290contexts. Existing scheduled snapshot identity/prompt, managed revision identities/content and two exact recorded operation Skill files are used without copying original material. Current source-file hashes are153cf6e6… (minutes) andf42f6fe6… (calendar). Both also exactly match existing local Git commit38213012e1d39a5943fa87e9ea94bfae25e222e3 in /Users/derek/.agents; no new file/version archive is created.

222contexts/7,867,162decoded bytes rebuild exactly, including whole source_json bytes after replacing only scheduled_consumer:129canonical material joins/4,482,501B and93current consumer discoveries/3,384,661B. Optional original mode and field-presence metadata is retained; no fallback to current unrelated instructions. This is potential reference-based input retirement, not bytes already removed or guaranteed SQLite savings.

68contexts/2,320,122B do not match current builder sources. Exact source headers identify three old operation-file versions (minutes a4091f71… and1f8349c2…, calendar8b765dea…). A bounded log query for these two exact paths returns only one commit and does not recover those versions. The next finite probe `superpowers/specs/2026-10-17-meeting-context-native-protocol-gap-probe.json` SHA4f330f8fd253b0fafb41f4bd8792e4d11b471f8193fe293dc803ab00e612dba5 follows the two classes to at most two latest persisted meeting/runtime attempts each: all four exact Codex paths are unavailable in the existing finder (which includes archived sessions). This does not prove permanent unavailability or authorize deleting unverified rows. No body retirement, decoder mode, source schema, runtime lifecycle, provider access or send was changed.

A separate implementation must first define exact reference storage using existing run/snapshot/revision identities, update every actual reader, preserve current active-input/retry/material semantics, keep required calendar linkage and business results/receipts, and verify inverse/cold opening. Failed source checks are listed separately. Do not treat source_json as a uniformly deletable transcript or turn this diagnostic into a new body archive.
