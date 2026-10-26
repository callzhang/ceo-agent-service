# Signal source owner acceptance — 2026-10-25

## Scope and exact declarations

The adapter cleanup is separate commit `d2dc7c6c3`. The Signal candidate moves the
existing source fields to the existing provenance parent for every Signal;
public IDs, business facts, source lookup/availability and receipts retain their
meaning. No Audit, authorization, retry or external-send policy was changed.

| Contract | Exact frozen value |
|---|---|
| Source | Store2026-10-25.2 / Email54;478 objects |
| Source manifest SHA256 | `7b4c91875faf9cf1bc57f912fc692b7c2a3966a03b9b2e384b2d8470ce0c832b` |
| Target | Store2026-10-25.3 / Email54;476 objects |
| Target manifest SHA256 | `234e17843afc171b4fa8cb42f3e1d959ef2ba415f43e295dc7db640ab3d02221` |
| Frozen standalone source Actions scaffold | 215 objects;`679e4bd4a5b9166a10754884a7dcab0b7b33b5c9137e01d270888b5cc2ff46a0` |
| Production-derived source cohort | 1140 Signals:110 inline,1030 referenced;172 provenance parents |
| Existing FK exception | `('runs',190042,'tasks',1)`;unchanged |

The full manifest, rather than the standalone scaffold, is the release source.
The target removes12 inline-source generated columns, two inline source indexes
and the Signal leaf trigger; locator validation stays on the immutable parent.
The current Actions target retains the immutable Email retry-request insert
guard; this schema guard is distinct from the Signal migration transformation.
The Signal payload contains its required reference and four own facts. No new
physical table or original-body storage is introduced. These are model/DDL
changes; no physical database shrinkage is claimed.

## Executed production-copy proof

`scripts/validate_signal_source_owner_copy.py` was run against a disposable
SQLite backup of current production, with actual predecessor code archived from
`dc07ddcbe`. It does not start a worker or call a provider.

The completed run covered forward publication and exact manifest checks; new
single-source and shared-source Signals; one synthetic Action/receipt; inverse
of the current data; real old-code Store cold reads and writes; and a second
forward publication. The final report was:

```json
{"pass_validation":true,"original_signals":1140,"original_parents":172,"final_signals":1145,"preserved_tables":48,"original_fk_findings":[["runs",190042,"tasks",1]],"seconds":886.7,"real_provider_calls":0}
```

The comparison covers typed stored-row hashes for all48 non-provenance tables
(excluding only the version marker and separately verified Signal payloads),
original shared parent IDs/values, complete reconstructed Signal source and
own facts, physical/public identities, allocation watermarks and the original
FK findings. It preserves the newly written Action and substitute receipt.

A second continuation probe on the first inverse copy additionally used the
real old DAO to append to both post-publication sources: sole-owner inline
promoted on its next owner, while the shared source reused its parent. Complete
source binding/locator equality and indexed source lookup were checked. Two
Signals on a fresh old-code source were also added. Standard reforward followed
by the same typed comparison passed with1147 Signals. The committed probe now
includes these continuation cases.

An initial harness invocation passed a string to the old Store constructor,
which requires a Path; it failed before old-code writes. The corrected probe
pins imports to the frozen old checkout and passes a Path. This harness error
is not counted as a business or migration success. The two completed checks
above supply the previously missing cold-code evidence.

## Focused tests and independent reviews

- Adapter:66 backend tests,44 console API tests,9 page tests, TypeScript;
  independent SPEC/QUALITY approved. The live formal-Task rejection regression
  was restored during review.
- Signal release:7 tests pass, including exact source/target hashes, current-data
  inverse/reforward, new facts/receipt/sequences, malformed pair rejection,
  ambiguous source rejection and transaction rollback after an injected rebuild
  error. The final target freeze and PK transfer comparison are included.
- Runtime owner:55 tests pass in the final combined changed-runtime group;
  separately completed physical provenance19, Store Signal7 and current Action
  schema plus frozen predecessor6. The new storage file has10 passing cases.
  These counts overlap and are not summed into a fictitious unique total.
- Assembly:8 executable contracts passed; regenerating the committed target SQL
  then passed both deterministic/executable artifact tests (2).
- Affected readers:79 tests pass across business Console source reconstruction,
  project context, business resolution, Attention and minutes owner backfill.
- Scoped Ruff, compilation and diff checks pass. Independent migration SPEC and
  implementation QUALITY reviews approve the candidate; reviewers did not run
  the production-copy proof themselves.

Two ABI catalog tests remain red on both this candidate and the untouched
`dc07ddcbe` checkout: the complete historical Action layout test and the stable
Action type-code test still equate the old61/62-kind registry with the current
70-kind registry. Baseline execution reproduced both failures in1.06s. Their
Quality owner was notified; no codec/type code or assertion was silently changed
here. No full-suite-green or deployment claim is made by these scoped results.

## Publication boundary

The other task owns selector/preflight hookup and the old Action release files.
This task did not edit `store_schema_release.py`, `current_publication_sources.py`,
`action_entity_schema_release.py`, `current_action_schema_release.py` or their
protected tests. Its interface is
`prepare_signal_source_owner_release(path)` with publish/verify_loaded/rollback/
finalize. Only the exact frozen25.2/54→25.3/54 pair is approved.

Integrated Quality, standard deploy, production schema/health/queue/Attention/
History/source-detail/receipt readback and final artifact cleanup remain pending.
All completed writes above were confined to disposable copies; no real outbound
send occurred. The previous A5 native acceptance evidence is retained separately
and is not replaced by this storage verification.
