# Legacy WorkTodo inline owner caller integration (unpublished candidate)

This unit selects the accepted direct WorkTodo import owner component in the
current Store20/Email52 candidate. It is an intermediate eleven ordinary-table
graph: `business_source_documents.body` remains, and final body-free provenance,
consistent-copy acceptance, independent reviews, and publication remain open.
No production database, provider, native Agent CLI, outbound send, commit, push,
or deploy was used in this unit.

The current importer keeps the source manifest, digest, supporting evidence,
transaction, formal Task/Signal creation, matching-existing-target checks,
idempotent retry, conflict refusal, and created/skipped/formal/history counts.
History-only Project/Update items return before any owner lookup. Formal WorkTodo
imports read and assign `business.import_task_id`, which refers to the adopted
Task public ID; the WorkTodo projection deliberately excludes this column.
The unreachable Project-match branch, its empty registry key, and its always-zero
result and CLI counts are removed. Import manifest format is now version 3;
version 2 is rejected before item decoding and must be regenerated with the
normal plan command. No compatibility reader or writer is installed.

The paired FK-off transaction captures and converts the old link after canonical
Email and before both markers, transports only declaration metadata, and
restores the old link first on inverse. Store and Email current predicates,
fresh constructors, known-object repair, and pair preflight select the same
current owner column and absent old table/Action guard. Historical source
factories retain their original declaration roles. Fresh Email canonical
conversion explicitly recognizes the selected guardless Action shape; its
historical conversion and inverse continue to require the old guard by default.
Supported populated
WorkTodo-to-Task links preserve owner and target; link-local ID, time and
allocator are intentionally retired. The component refuses unsupported
alternatives, inbound dependencies, dangling links, and metadata drift before
DDL. Target Task FK delete/public-ID restrictions remain; obsolete independent
source-owner restriction is retired.

Validation at this point:

- One real RED: `python -m pytest -q tests/test_task_semantic_import.py::test_current_import_owner_is_inline_and_legacy_container_is_absent` failed because fresh Store still created `business_legacy_links` (91.83 s).
- The same fresh-current test passed after integration (103.70 s).
- Component roundtrip, source-backed formal import, and history-only import: 3 passed (217.23 s).
- Paired forward on `fresh_candidate` and paired inverse on `actual`: forward passed; inverse reached successful rollback, then failed only an added incorrect final-source Action-guard assertion. The final Store19/Email51 source never had that intermediate guard. After removing that assertion, inverse passed (118.40 s). The paired child retained its existing 900 s deadline and no retry.
- Manifest version-2 early refusal: 1 passed (1.82 s). Paired inverse metadata transport: 1 passed (1.36 s). CLI parser node: 1 passed (4.91 s).
- `ruff check` on the bounded changed Python/test files, `py_compile` on changed runtime files, and `git diff --check` passed.
- Cold constructor orders Store→Email and Email→Store initially failed because the
  canonical Email source recognizer expected the retired Action guard. After the
  explicit fresh stage choice, both passed (2 passed, 137.09 s).
- Accepted component regressions reran after that stage choice: 17 passed
  (98.04 s), including independent historical source construction, populated
  links, exact inverse, and refusal cases.
- The first focused historical canonical test reached a stale test fixture:
  `current13` installed the old Action guard onto the new eleven-table graph
  without its old link table. SQLite correctly refused the invalid trigger
  during the dependency probe (1 failed, 111.16 s). The fixture now reconstructs
  the pinned empty historical Legacy declarations and removes the empty owner
  column independently before installing old Actions. The same node then passed
  (1 passed, 121.67 s). This is a test-source correction, not a runtime
  acceptance broadening.

The component acceptance file and implementation module retain their accepted
SHA-256 values; this integration does not revise the accepted component. The
current candidate has not passed an actual source copy, independent SPEC or
QUALITY review, capacity measurement, or deployment. Its target remains an
intermediate eleven-table graph with `business_source_documents.body`.

SHA-256 provenance below uses the accepted R3 `runtime_pins`/`files` when
present; the component test uses accepted R4. `not frozen` means no pre-edit
hash was recorded in those receipts; it does not claim a Git baseline.

| File | Accepted prior SHA-256 | Current SHA-256 |
| --- | --- | --- |
| `app/task_semantic_import.py` | `6103fcdf352548d8f76a49ca8f1bc01cddbef47b251c2ebbd35f91deb3cdf9ae` | `128ee995619ba30126dc944398fbf1d05be562f1fef6a21a702e4ea6571d0978` |
| `app/cli.py` | `bc985c24c9e3ab80b678dea15a4bf24dad77fd0a8d8a98a74814e4cc96804f3c` | `f8a9683668de38ab47f3b9ab9981ca91c1ceb6a2ffbaf9f5730160894d8ebe68` |
| `app/store.py` | `a720e714df09e67dcb97932b3ca4d4b79c956b035ef9c7651dbb233af419f630` | `ee189ee1b44316695aa9e3dc5ef0c948e4145b291c40f50ede7b7ce02c96e222` |
| `app/email_store.py` | `8ad3d75acecadda93e54fc0a61f68e12455ccd90ef40064fcec1dc47189e0252` | `0f8b56c593dce0ca6912394fa01da347e302608cc387ded7f97fcf40feedfdf1` |
| `app/action_email_schema_release.py` | `e1fa68c6e2a547db30a00d70c7c79ec317b784ee3454bed3a258ac475fcec523` | `3b366e1eb03dac256e00d481cf3142e7877c5c07bca6fc01a6dc83f1f4c09dc5` |
| `app/email_canonical_storage.py` | `141f6b7fe18d03c8025eee2e044a534d6b13536a6165c346065a5ee67adf9adf` | `b9398e0ae83e22aa543f378038f27c349bc2d0510a6a9b4c902f5ac8000f1f90` |
| `app/business_schema_release.py` | `af39be1c51cdbc85e37e0850e3f465ac08a9b015f192e5ccbce760f0e0ea3258` | `6d73c737a3ea93c6a43862e8d5c4189986320522c372c574bc05f7247a3a7ece` |
| `app/action_followup_sync_definitions.py` | `a0bece8f7a056887ae682ae065cbc109f9282b47191af10ddd07ef13f31503b2` | `9685411c63989586e3c047a4b45c235b9f9b395dae0bd62baeef180edfe5a08f` |
| `app/action_storage.py` | `3cfd0fbcd222b95d57b8e3524a58aae913ec5a7d04b9ccd145bd65566c1af74b` | `48c65f5db86f499ec3faf419ce462e5e778c72cc29f7960a11b6dc17c0aabc0b` |
| `app/configs_schema_release.py` | `a4375acec5dc5296d883da1275772cf05ef0bcc648301ea6082c10bfc6bc599d` | `d56695d9ec45e6490c1c8689994fe3c118890249b90310855b1e00f0471176d4` |
| `app/runs11_current_schema.py` | `1caf65f8b79c92f5986ae688a4cb98e3c2c719eb0c11aa5dfc8298569fb92288` | `af751fbc0eeb84ae976c1462a6e34c5bc79c1ab3f99dfc5875e807103c35dc2c` |
| `tests/test_task_semantic_import.py` | `not frozen` | `cecd11dc895b790b580d604865a3ab970836172e53618041c6004d3a041b4169` |
| `tests/test_task_semantic_store.py` | `not frozen` | `68346bcd8a82d3b2684efb3c3f9d49d9157cce692e112058cd532b460632e329` |
| `tests/test_email_action_schema_release.py` | `7f4e6b3962ddde17cd937bfaa5dc171122d83985c707b031ad82ed3377d2091f` | `38838965e2587c83029b1c50efe639fde28dafaea4c601bbaf01a51d2b30bb7d` |
| `tests/test_business_legacy_owner_storage.py` | `824b10101e86f595f687fbd9ced51e20067d2405942f17833b779ad49235fd1d` | `61290fd965ced4c60003d1557a3cc2f14f982321d0a440ee788b680d360a610e` |
| `tests/test_email_canonical_current12.py` | `3d3e7df494ae606bf86f097f322cf692076333855e979d95c56ceab3587b5ae9` | `1bde19908d53043fa22f280ba67b65bd95e9bfa5bcef6df6558e5707b556ec26` |
| `tests/test_email_canonical_storage.py` | `not frozen` | `b30d36f038a4f915a48af43015ad82ff2cef3308129008693559581417ad48d2` |
| `tests/test_cli.py` | `not frozen` | `1b50b40ffa2cefff22c785ff33fa82fb05deb56362571195b4ffe8b751b5475f` |
| `docs/architecture.md` | `c18feec899fde5139b7995443366fe3164fb5221485c4e42d5ff8149a37a252b` | `bf61004a773066b50691f7a5d17720b70494e1baecc1ef13299c0b1bd4a07fbb` |
| `docs/runtime-mechanism.md` | `413a936c3b3db72d2962bdc931eea42fed0e08ddd1d90bd79cecd6d6c1bb8c22` | `71873828221cf0d132657976cd73be8758c07f656e9ae9a66a0a7bf2f832c4aa` |

The unchanged accepted component is `app/business_legacy_owner_storage.py`
`5fe14154f30af3bd3a857da3bcf84341d7aa10f094dee379c5bbc96cede4d5d2`;
its acceptance receipt is
`docs/superpowers/specs/2026-10-18-legacy-inline-owner-component-acceptance.json`
`b3db80f39b5f2c1a868eb7b22604c580e1ade633e6d1a18c2100bafab32067a3`.

Fresh SPEC review of the first caller freeze requested two same-scope corrections.
The top-level architecture and runtime summaries still called the previously
selected twelve-table graph current, and a historical 15-table section used
present-tense current language. They now identify the unpublished eleven-table
graph, the retained source-document body, the old Legacy declaration only in
historical/source roles, the accepted 12-table R3 immediate predecessor, and
the exact inverse order (Legacy owner before canonical Email before earlier
Actions). No historical mechanism was removed.

A paired marker BLOB/timestamp/pipe-payload test mocked `_paired_initializer`
stdout without the two newly required declaration-only lines. The exact test
node first failed for both `actual` and `fresh_candidate` at missing
`EMAIL_CANONICAL_INVERSE` (2 failed, 9.57 s). The fixture now emits and asserts
`EMAIL_CANONICAL_INVERSE` and `LEGACY_OWNER_INVERSE`; the same two-node run
passed in 509.46 s, retaining the BLOB timestamp, original marker, pipe
payload, apply/rollback/reforward, and final loaded assertions. This was a
test transport fixture repair; runtime code and the paired child deadline were
unchanged. No component or cold-constructor suite was rerun for these
documentation/test-only corrections.

Fresh QUALITY review of R2 accepted SPEC but found two implementation issues.
Its seven-node diagnostic ended 6 passed, 1 failed in 410.55 s; the only
failed node was the known-object repair of
`trg_actions_runtime_skill_load_receipts_update`. The diagnostic temp copy
was removed. That result is a local test failure, not an actual-source copy.

The importer regression first failed because each write transaction called
`_rows` and materialized its whole legacy kind after the once-per-apply
fingerprint (1 failed, 61.68 s). The selected source counts of 975 WorkProjects,
3,327 WorkTodos and 5,468 WorkUpdates made that path unbounded per item. Apply
now uses finite source-shaped point projections for each item and supporting
WorkUpdate inside the writer transaction. WorkProject and WorkTodo plans search
`idx_business_public_id`; WorkUpdate's selected numeric domain searches
`idx_actions_public_id (record_type=? AND <expr>=?)`. The default historical
projection SQL remains unchanged. SQLite's full WorkUpdate source projection
materializes generated REAL from JSON integer `0` as integer `0`, whereas an
unsorted indexed point query returns `0.0`. A one-row deterministic sort keeps
the original source digest's storage class while retaining the indexed search.
The focused regression compares the complete corpus and point dictionaries
and SHA-256 digests for both integer `0` and noninteger REAL `0.375`, and checks
EXPLAIN shows the indexed search before the one-row sort. No digest policy,
source fields, cache, or index changed. It passed after repair (1 passed,
37.85 s). Supporting-source changes after preflight still refuse before
formal import writes (2 passed, 91.10 s); formal retry/idempotence and
history-only behavior passed in the explicit-assignment case (1 passed,
3 deselected, 27.79 s).

The failing current repair node initially reproduced the strict-manifest
failure (1 failed, 42.82 s). A targeted assertion then proved that Store's
nonfresh bootstrap had recreated both `business_legacy_links` and
`idx_business_legacy_links_task` before that manifest check (1 failed,
64.36 s). The existing native Legacy DDL is now executed only for fresh
transient source construction, before its accepted conversion; current repair
never constructs and drops it. The same node passed (1 passed, 93.16 s),
including retained WorkTodo data/nullable owner, absent old objects at the
strict check, and exact missing-trigger repair. One accepted component
source/inverse node passed (1 passed, 25.25 s), checking the fresh source
constructor still reaches the native exact historical declaration.

Focused Ruff, Python compilation, and `git diff --check` passed. No whole
suite, actual source/production database, provider, native CLI, send, commit,
push, or deploy was used in this R3 author repair.

R2 freeze `docs/superpowers/specs/2026-10-18-legacy-inline-owner-callers-freeze-r2.json`
remains immutable at SHA-256
`9b209f06c6c3f0eb8437103c43713a55b86381be9971f71d103b53128b567a28`.
The exact R2-to-R3 delivery deltas are:

| File | R2 SHA-256 | R3 author SHA-256 |
| --- | --- | --- |
| `app/task_semantic_import.py` | `122e351f759a0f5f495596553941e5547dc6b2cd860e7a1ba74afe6477573b47` | `128ee995619ba30126dc944398fbf1d05be562f1fef6a21a702e4ea6571d0978` |
| `app/store.py` | `57c2a2995592ef0be426bcec6d5982ed4a6c68fd66cffa53eec42324fa7f5793` | `ee189ee1b44316695aa9e3dc5ef0c948e4145b291c40f50ede7b7ce02c96e222` |
| `app/action_followup_sync_definitions.py` | `1398828eb035746aead194d7d57f67ceb700bfa71798054627a6baa793b6303a` | `9685411c63989586e3c047a4b45c235b9f9b395dae0bd62baeef180edfe5a08f` |
| `tests/test_task_semantic_import.py` | `a3df41d25450dc66581b31a43e567ef503cbfdb9495fad2e480665f0e7b129c0` | `cecd11dc895b790b580d604865a3ab970836172e53618041c6004d3a041b4169` |
| `tests/test_email_action_schema_release.py` | `c40a83d5f0976a48052a20fa827c5425902cd577bf6a6324b5dae163fc3fdd45` | `38838965e2587c83029b1c50efe639fde28dafaea4c601bbaf01a51d2b30bb7d` |
| `docs/architecture.md` | `e1e6d7b66884560844571237ec4e5c5e8eea3975851f0752afeea63a8e785d3f` | `bf61004a773066b50691f7a5d17720b70494e1baecc1ef13299c0b1bd4a07fbb` |
| `docs/runtime-mechanism.md` | `321e30a9151e7043f658b42a2c3292329253808f74c5ed5422720e4279790515` | `71873828221cf0d132657976cd73be8758c07f656e9ae9a66a0a7bf2f832c4aa` |

The validation note itself and claim-row hashes are reported outside this self-referential table.
