# Phase5A actions consolidation validation

Status: reviewed development unit, not deployed. The approved final goal remains ten business/runtime tables plus migration metadata; this six-table unit does not complete actions or phases6–8.

## Exact source and target

Source Store `2026-10-12.1`, target `2026-10-13.1`; Email schema remains51. The schema-only source inventory is `superpowers/specs/2026-10-13-database-redesign-phase5a-inventory.json`. It contains the actual48 fields across review_candidates/candidate_reviews/candidate_selections/candidate_supplements/candidate_executions/candidate_action_attempts. Those declarations and six logical task/run parent triggers are the source contract. All source columns are retained; there is no new business field or retention policy.

Target is one physical actions table with six finite record types. Old integer IDs remain public per-type keys; physical IDs and all independent candidate/review/selection/execution/run/task foreign keys are distinct. Source JSON strings/digests remain raw strings. Generated fields retain source TEXT/INTEGER affinity; constraints, defaults and unique identities retain source semantics. No old table, compatibility view or dual-write path is maintained. The independent external_action_results table remains for the next actions unit.

Source primary keys are ordinary INTEGER PRIMARY KEY, not AUTOINCREMENT. Public allocation preserves max-plus-one, deletion/reuse, negative IDs and SQLite upper-bound allocation. No sequence watermark is fabricated for this unit.

## Review and regression evidence

SPEC review confirmed field/declaration mapping, finite types, cross-reference/public-ID and current-fact inverse contracts. It reproduced an external view dependency that would survive with a broken reference; the correction uses SQLite's parser in a schema-only memory database. Direct/quoted/chained views and read/update/delete/instead-of triggers are checked before original-database DDL. Unrelated views/triggers and FTS values survive. The native-copy CLI import-order finding was fixed and `--help` succeeds with PYTHONPATH unset.

QUALITY independently found and closed bootstrap late name rejection and SQLite ASCII NOCASE gaps. Constructor tests prove views, indexes and triggers occupying reserved declarations are refused before schema changes. Mixed-case inbound FK/dependency and repair checks retain SQLite name semantics; record types, payload values and non-ASCII names are not folded. All existing lease, CAS, late-receipt, independent ledger and source13 fixture assertions remain.

Frozen foundation SHA256:

- app/action_storage.py: 798ddf5ea22b0c160ec0191493c44995f975a401184bca5c96353d238d6688d6
- tests/test_action_storage.py: b17b7e718db2fb9a93c396393912fdab68b04531da6a1089e17dde29a370b6dd

Independent final foundation suite:51 passed. QUALITY's final related storage/release/manifest/identity/late-receipt check:89 passed in13.08s, no unresolved P1/P2. Root's affected reviewed/console fixture rerun:57 passed; related Task/Meeting/Workbench/rule-question/orchestration readers:71 passed. All app/tests Ruff and diff checks pass. These overlapping scopes are not summed into a fabricated full-suite count.

The five offline RepositoryUpdater failures are dependencies, verification, restart, health and loaded-schema verification. They exercise the real paired-release ordering, restore source schema before old start, and retain replacement business facts and an independent external receipt. No service/process/provider action occurs in these tests.

## Actual current-source copy

Actual old-code commit is2d1e94e4a29820b2a8bc9f8cfed0577cc07f8128. A consistent SQLite backup captured547 candidates,538 reviews,0 selections,3 supplements,143 executions and138 attempts:1369 rows. All137 physical tables (including sqlite_sequence and FTS structures) were fingerprinted by values, storage types and retained rowids; the intended schema-version row is checked separately.

Forward comparison passed for all tables. Synthetic insert/update/delete facts were added to the candidate supplement type without executing an original task. Paired inverse retained those facts and exactly restored original schema declarations and marker timestamp. The actual old commit's app tree was extracted from Git into the task's temporary directory; a fresh old-code AutoReplyStore constructor left all restored facts unchanged. Fresh re-forward and original-source unchanged checks passed.

The first proof preceded the final namespace/bootstrap corrections. The retained immutable source was then reused for the final loop after merging current main as0169d05593a3bc4359a3ccdd6d33e02844e7d898. All137 table fingerprints, exact source declarations and marker, updated/deleted/new synthetic facts and a new independent receipt were checked through corrected inverse, actual2d old-code startup, merged constructor forward, inverse and fresh re-forward. All311 application file hashes match the final proof. The durable count/hash-only receipt is `../evals/database_redesign/phase5a_copy_proof.v1.json`, SHA2560a0653a49125a29261ab38bc249b96415e2235ca62f88e0643e5ecf6dd290652. No business originals or test DBs are stored in the repository.

The merged scoped verification passed154 tests, including concurrent main's OA and meeting recovery changes. Draft PR72 is https://github.com/callzhang/ceo-agent-service/pull/72; CI is in progress. Original work data, copied databases and old code remain task-owned temporary acceptance material and will be removed after final verified evidence is retained.

## Remaining gates

Current main is integrated and corrected application/full-copy proof binding has passed. Run complete CI, including the console checks; deploy only after these gates pass. Read back production marker, PID, health, queues, Attention and History. Continue the remaining actions families and phases6–8 toward the unchanged10+1 goal.
