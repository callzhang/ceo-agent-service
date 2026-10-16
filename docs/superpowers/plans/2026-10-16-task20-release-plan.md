# Source-bound Tasks20 conversion implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development for mechanical application after canonical approval. Root owns migration decisions. Steps below do not authorize current caller selection or deployment.

**Goal:** Convert the exact original eight-kind Tasks table and twelve native task tables into the accepted finite declaration; restore the original schema using current facts, preserving raw identities, SQLite values/types, independent source counters and three retained child endpoints.

**Architecture:** A caller-owned FK-off transaction captures declaration-only inverse metadata plus six counter-presence bits. Temporary affinity-free tables stage current rows while `tasks` is rebuilt without ALTER RENAME; native scalar encoding preserves source storage. Inverse reconstructs all original 82 columns, with the 508 per-kind unused positions explicitly NULL, and copies current twelve-kind facts back to their native sources. This component does not select constructors, callers, schema markers or release entrypoints.

**Tech Stack:** Pinned Python3.13.12 / SQLite3.50.4, existing Task8/Task12 SQL parts and Tasks20 declarations, focused pytest.

## Complete canonical files

- `docs/superpowers/plans/2026-10-16-task20-release-candidate.py`: complete future `app/task20_schema_release.py` contents. No runtime dependency on evidence JSON.
- `docs/superpowers/plans/2026-10-16-task20-release-regression.py`: executable actual/fresh native differential regressions against this canonical module.
- `docs/superpowers/plans/2026-10-16-task20-release-preflight.py`: exact pinned focused test runner; no copied app installation and no app edits.
- `docs/superpowers/plans/2026-10-16-task20-release-actual-copy-preflight.py`: disposable nonempty frozen-source component proof. Only a concise aggregate report may be retained.

Source truth is the accepted frozen metadata `../specs/2026-10-16-task8-native-source.json`, `../specs/2026-10-16-tasks12-native-source.json`, and `../specs/2026-10-16-tasks12-fresh-source.json`, alongside source code `app/task_storage.py` and accepted `app/task20_definitions.py`. The captured fourteen declarations are finite fingerprinted actual/fresh profiles. Neither raw row bodies nor source counter values belong in inverse metadata.

## Task 1: Canonical implementation and source regression

- [x] Write complete canonical and executable regressions before application changes. Freeze exact actual/fresh hashes from the native metadata schemas, not from a modified target factory.
- [x] Run the actual/fresh cases. Correct the demonstrated inverse column-order bug by projecting staging input columns explicitly by name. The native fresh and actual processing-item column orders differ; positional target `SELECT *` cannot restore them.
- [x] Add 112 actual/fresh forward/inverse finite-name collision cases after independent SPEC reproduced TEMP shadowing before DDL. Check all fourteen relation names plus their staging names case-insensitively, preserving MAIN facts, sequences, caller TX and TEMP witness.
- [x] Verify nonempty exact old82 values/types and input pointers; all twelve source ROWIDs/public IDs and allocator streams; child ROWID/three FKs; source absent/zero/deleted-high/FULL allocator cases; current writes/inverse/native SQL write/reforward; preserved external physical FK; original-schema rollback and TEMP collision.

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python -B docs/superpowers/plans/2026-10-16-task20-release-preflight.py
```

Expected: 176 focused cases pass after both TEMP relation and allocator corrections; the eight allocator regressions are separate. Run Ruff with `--no-cache` on the four canonical files. Do not credit setup errors as behavioral failures. Initial fixture-only JSON/field errors were corrected before this evidence.

## Task 2: Nonempty component rehearsal

- [x] Refreshed installed-module proof56.058s with allthree hashes and unrelated TEMP counter witness; run the exact owned-copy script below. It hash-verifies preserved source970a, copies it to a task-owned temporary directory, performs forward/cold SQLite reopen/inverse and exact original ordinary-family comparison, then current target write/inverse/native-source SQL write/reforward. Close every connection and clean the owned directory in `finally`.

```sh
PYTHONDONTWRITEBYTECODE=1 TASK20_COPY_REPORT=docs/superpowers/specs/2026-10-16-task20-release-actual-copy.json /Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python -B docs/superpowers/plans/2026-10-16-task20-release-actual-copy-preflight.py
```

Expected source116ordinary becomes104 and inverse116. This standalone count differs deliberately from the integrated State67 baseline; it does not replace that complete paired-release receipt or prove the future55 intermediate. Preserve the source FK baseline exactly, exact original schema and full sqlite_sequence on no-write inverse; verify all original ordinary families with both-direction value/type comparisons. Native-source SQL is not genuine archived app initialization. URI read-only ATTACH requires opening the copied connection with `uri=True`; its initial harness failure did not indicate a migration error, and the owned copy was cleaned.

## Task 3: Sequential independent reviews and application

- [x] Fresh bounded SPEC172/9.61s then prerequisite-expanded184/9.15s. Fresh bounded SPEC review of canonical component/regression, source fields and actual/fresh declarations. Resolve any actual mismatch in canonical code and test before application.
- [x] Fresh QUALITY184/7.81s after SPEC; its prior silent TEMP sequence finding is resolved by exact four-statement qualification, with four supplemental roundtrips. Fresh bounded QUALITY review after SPEC passes, including meaningful source-preserving semantic mutants rather than setup failures. Do not let reviewers select application/callers/migration/production.
- [x] Exact unselected applicationdfbfc07bd, writer184/8.13s. Mechanically copy the accepted exact canonical into `app/task20_schema_release.py`, add the accepted regression under `tests/` with only the documented module-import/path substitution, and verify exact hashes/AST scope. This is still unselected helper code.
- [x] Committed own code/test/docs hunks; applied fresh SPEC184/8.62s then QUALITY184/7.83s PASS. Installed hashes and copy limits recorded in ../specs/2026-10-16-task20-release-installed-acceptance.json.

## Task 4: Coupled runtime selection belongs to the parent plan

The parent `2026-10-16-task20-coupled-cutover-plan.md` requires all nine caller proposals, remaining reader/maintenance semantics, current Store/Email/Workbench constructors and shape maps, scalar hooks, finite manifests/publication source roles, paired forward/inverse ordering and marker/ledger closure in one coherent selection. This canonical component alone does not change running behavior or authorize a partial constructor.

After complete selection, require the integrated nonempty55-table intermediate, genuine immutable-production predecessor cold reads/writes and reforward, real CLI with fake outbound, UI/queries/performance and copy-only page/file measurements. Final target remains ten core tables plus Email ledger, so even55 does not finish the redesign. No main/push/production write/restart/deploy until the complete reviewable publication and existing final approval boundary.
