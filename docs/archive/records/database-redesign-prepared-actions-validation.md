# A5a — prepared message owns its dispatch claim and accepted receipt

> Status reconciliation, 2026-10-21: this is a scoped historical implementation/acceptance record. Its publication-pending statements are preserved as history; the subsequent Store19.11／Email53 combined copy, inverse and production deployment are recorded in [final release evidence](database-gpt6-release-validation.md). See [current execution progress](business-model-execution-progress.md) for unit status; historical FAIL results are not rewritten.

This is the local implementation and review handoff for Store `2026-10-19.8` /
Email53 → Store `2026-10-19.9` / Email53. The independently archived source is
`c4a9e794d0fe7b8954a8746b01fec118337dc188`. At the implementer handoff, this worktree had not committed, pushed, invoked
a native Agent/provider, opened a production database, or published this change. Full production-copy, native CLI and deployment evidence
belong to the parent release task. C1 publication status is separate.

The implementation freeze is
[`2026-10-20-prepared-action-source-freeze.json`](superpowers/specs/2026-10-20-prepared-action-source-freeze.json):
1,300 files across app/tests/scripts, CI skill/runtime inputs and Python/Node
package manifests and lockfiles. Its aggregate is
`80104a5f67d4b7af2d71564b97de2f645b7062e2edbd2ff3020041ac0d51690a`.
The separate
[frozen-source manifest](superpowers/specs/2026-10-20-prepared-action-frozen-sources.json)
records 24 unchanged files, fourteen historical Actions declaration outputs
(seven stages × both Reply variants), and the C1 and historical Task declaration
outputs. `action_storage.py` current code is allowed to change; its historical
factory outputs remain exact. There is no runtime schema probe or fallback.

## Field ownership and physical declaration

The source has thirteen public fields across three kinds. Six belong to the
prepared parent, four repeat its exact composite identity, and three are unique
child facts. The target retains the six parent facts and stores only those
three unique facts beside the existing parent payload.

| Source fact | Target owner / stored field | Meaning and preservation |
| --- | --- | --- |
| `outbound_postfixes.channel`, `.delivery_key` | Same parent `legacy_key`; same generated channel/key | Exact string pair; `01` and `1` remain distinct. No new integer/public alias. |
| `outbound_postfixes.final_body`, `.feedback_token`, `.postfix_version`, `.created_at` | Same original parent `payload_json` | Entire raw JSONB BLOB remains unchanged, including nonminimal framing. First preparation remains immutable through the Store API. |
| `outbound_postfix_receipts.provider_result_json` | Parent `postfix_receipt_provider_result_json TEXT` | Original provider string verbatim; JSON is not parsed, canonicalized or reserialized by migration. |
| `outbound_postfix_receipts.created_at` | Parent `postfix_receipt_created_at TEXT` | Original independent accepted-receipt clock. |
| `native_reply_dispatches.created_at` | Parent `postfix_dispatch_created_at TEXT` | Original independent possible-effect claim clock. |
| Child channel/key | Parent channel/key projected through the original source field names | Both children were keyed by this pair; no inbound business reference to a child physical ID was found. |

The source compact payload CHECKs require text for these Postfix values. The
source writer accepts Python `str` or `None`, then applies the existing
CHECK/IGNORE behavior; it does not admit BLOB/REAL/integer Postfix fields.
Nullable TEXT is therefore the literal source representation. There is no
scalar codec, encoding flag or invented business status. SQL NULL means no
child fact. Empty strings and the literal provider strings `null` and `{}`
remain present values. Receipt value and its own timestamp must be jointly
NULL or jointly text. Dispatch presence is its own non-NULL timestamp. Only
`outbound_postfixes` may hold any of the three slots; other kinds must have all
three NULL.

Current Actions has 59 physical kinds, down from 61: 58 business kinds plus the
existing execution identity alias. The two child kinds are excluded from the
current finite layout and allowed `_kind` CHECK; `postfix_type` and its composite
self-FK are removed. Parent generated channel/key expressions cover the parent;
the existing unique `(record_type,channel,delivery_key)` index remains. The
numeric-public-ID index predicate deliberately retains its exact old exclusion
text, so unrelated expression-index selection stays unchanged. No permanent
index, table, view, archive, mirror, trigger or fallback reader is added.

## Current caller and lifecycle boundary

`AutoReplyStore._prepare_outbound_postfix_in_transaction`,
`claim_native_reply_dispatch`, and `record_outbound_postfix_receipt` now call the
existing `shared_action_lifecycle.transition` with `origin="prepared"` and the
finite real operations `prepare`, `claim`, and `outcome`. The common writer
creates the prepared parent or fills its first empty claim/receipt slots. The
old three Store insert paths no longer insert either child kind. Generic
historical child CRUD remains available only to explicit source migration and
test oracles; target DDL refuses those physical child records.

Prepare/get/claim/receipt signatures and returned DTO/JSON/boolean shapes are
unchanged. Current receipt/claim projections expose the original source column
names from the parent. Publication readers select those projections for the
exact new marker and use the explicit historical projections for older declared
publication sources. There is no schema-dependent runtime DML.

The Store caller keeps first preparation, first claim and first receipt. It
keeps the original input trimming where the public source API did so; raw claim
identity remains untrimmed. Original required-None/CHECK/IGNORE behavior and
missing-parent `IntegrityError("FOREIGN KEY constraint failed")` are preserved
at the actual Store boundary. Receipt and dispatch clocks never use the parent
preparation clock.

The three commits remain separate:

1. Prepare and commit the final message.
2. Commit the possible-effect claim before invoking the provider.
3. Commit the accepted provider receipt before verifying delivery.

The accepted receipt is not a verified global receipt. An ambiguous
`processQueryKey` is reverified without resending; an interrupted claimed send
uses existing reconciliation. Echo suppression reads the same immutable parent
body and preparation clock. The service path creates no Task, AgentRun or global
verified receipt. `service_message_sender.py`, `outbound_postfix.py`, C1 Task
logic, Audit, authorization, cancellation and retry policy are unchanged.

The old no-caller physical child CRUD/FK operations are deliberately retired,
including deleting/rekeying a parent while a physical child exists. There is no
replacement trigger: such a trigger would impose new FK-off/IGNORE/REPLACE
semantics. Public Store behavior is the preserved business boundary. The frozen
historical tests still test those original physical contracts explicitly.

## Paired release and current-data inverse

`prepared_action_schema_release` accepts only exact Store19.8/Email53 with the
original specialized Actions declaration and unchanged C1 Tasks. Both supported
Reply declarations (`actual_no_memory_fk`, `fresh_memory_fk`) are explicit.
Normal Store/Email/Workbench constructors accept the target or a new database;
they refuse the predecessor and unknown/mixed declarations before writing.

The schema transition runs in the caller's FK-off transaction. It checks the
full source declaration, declared dependencies, source field representability,
parent presence and preexisting FK diagnostics. A temporary table stages only
retained current Actions; its ID index supports point attachment of each moved
fact. It copies all stored columns with their original SQLite values and types,
retains each parent payload byte, restores exact `sqlite_sequence` rows and
ROWIDs, compares current business facts and verifies the complete target DDL.
Temporary tables are removed before return. No persistent business snapshot is
kept. Metadata consists of the Reply declaration variant and the release's
original marker/Email ledger/FK diagnostics.

Inverse reads the **current** parent slots, including preparation, claim and
accepted receipt written after publication, and reconstructs the original
specialized Actions declaration and child source facts. The release restores
the original Store marker and its original clock and leaves the Email53 ledger
unchanged. Failed target load uses this same inverse; a second rollback is
idempotent. Old archived Store, EmailStore and WorkbenchStore then cold-open the
restored database, and a new forward publication preserves those same facts.

Exact inverse boundary:

- Every retained parent/unrelated Action physical ID, stored SQL value/type,
  raw payload BLOB and real sequence name/ROWID/watermark remains exact.
- Each retired child is compared by exact source composite key, per-kind
  presence, original provider string/type and its independent timestamp.
- Retired private child physical IDs, equivalent JSON spellings of their
  `legacy_key`, child JSON array framing and the next ordinary private physical
  allocation may differ. They are not public source fields. Actions is ordinary
  `INTEGER PRIMARY KEY`; there is no Postfix AUTOINCREMENT namespace.
- All unrelated tables and existing FK findings are retained. The nonzero FK
  fixture uses exactly `("runs",190042,"tasks",1)` and preserves its Run BLOB/REAL
  values through forward, inverse and reforward. No FK finding is repaired or
  suppressed.

This is not a byte-exact inverse of every physical Actions row or index page.
Reconstructed private child IDs can change index varint sizes and page packing.
The business boundary above is the intended inverse contract.

## Executed evidence

Interpreter: `/Users/derek/miniforge3/bin/python`, Python 3.12.11, SQLite 3.53.4.
Raw logs, actual c4 source archives and synthetic databases are retained for the
parent's independent reviews in
`/private/tmp/ceo-a5a-postfix-owner.g4HsZc`.

| Validation | Result | Raw evidence |
| --- | --- | --- |
| Fail-first ownership mechanism | 3 expected source failures: three physical rows and unknown prepared origin | `red.log` |
| New schema/current-fact inverse cases, both Reply variants | 10 passed, 323.40 s | `schema4.log` |
| Actual c4 DAO differential; paired release; failed target load; strict old cold start; reforward; source freeze | 4 passed, 274.01 s | `release1.log` |
| Existing Postfix storage/release/callers, sender, outbound and publication markers; owner cases; nonzero FK fixture | 159 passed, 129.92 s | `focused3.log` |
| Unknown target declaration refuses inverse without writes, both Reply variants | 2 passed, 36.28 s | `target-ddl.log` |
| Final import placement verification | 3 passed, 4.02 s | `final-import.log` |
| Changed Python AST / Ruff / diff whitespace | 28 Python files parsed, all checks passed | `lint2.log` |
| Historical declaration/file equivalence and 1,300-file freeze | 24 files, fourteen Actions outputs and two Task outputs exact | `freeze.log`, manifests above |
| Synthetic space/current-state comparison | All public facts, retained values/types/IDs/bytes, other-table values and sequences exact through inverse/reforward | `space.json`, `measure.py` |

The main batches cover 175 distinct passing cases; the final import check repeats
three of them. No whole test suite was run. Provider/verification substitutes
observe committed rows through separate connections before returning, assert
one send across ambiguous verification/reopen, and assert claim-only recovery
reconciles instead of sending again. They make no network/model calls.

Exact focused command (each command also used a distinct task-owned `--basetemp`
and redirected stdout/stderr to the matching raw log):

```sh
/Users/derek/miniforge3/bin/python -m pytest -q tests/test_prepared_action_schema.py
/Users/derek/miniforge3/bin/python -m pytest -q tests/test_prepared_action_release.py
/Users/derek/miniforge3/bin/python -m pytest -q \
  tests/test_prepared_action_owner.py \
  tests/test_prepared_action_schema.py::test_unrelated_existing_fk_finding_and_raw_run_are_preserved \
  tests/test_action_postfix_callers.py tests/test_service_message_sender.py \
  tests/test_outbound_postfix.py tests/test_action_postfix_storage.py \
  tests/test_action_postfix_schema_release.py tests/test_state_publication_marker.py
/Users/derek/miniforge3/bin/python -m pytest -q \
  tests/test_prepared_action_schema.py::test_inverse_refuses_unknown_target_declaration_before_write
```

`schema4.log` ran the first ten schema cases before the two subsequently added
parameterized tests; those four cases have their separate passing evidence.
Initial own-fixture setup errors are retained in earlier logs and are not
counted as passes. The existing historical TEXT test adapter already lacked the
`execution_owner` argument on actual c4 (reproduced in `source-historical4.log`
and `source-historical5.log`). It now accepts and asserts that flag is false,
and explicitly binds its historical Postfix SQL exports. Existing source
assertions, DDL and expected physical FK tests were kept. This is test fixture
selection, with no runtime compatibility path. The earlier proposed parent
restriction test (`parent-red.log`) was withdrawn after source caller inspection
and parent review established the retired internal CRUD boundary; no trigger
or public-policy change was implemented.

## Synthetic rows and space

The fixture uses 1,352 parents, 783 receipts and 155 dispatch claims, matching
the source population counts provided by the parent, but **all field values are
synthetic**. It adds 1,000 unrelated errors. This is not a production-size cost
estimate. Complete per-index `dbstat` values are in `space.json`.

| Measurement | Source | Target | Inverse | Reforward |
| --- | ---: | ---: | ---: | ---: |
| Physical Actions rows | 3,290 | 2,352 | 3,290 | 2,352 |
| `payload_json` bytes | 543,342 | 479,596 | 543,342 | 479,596 |
| Moved TEXT fact bytes | 0 | 53,730 | 0 | 53,730 |
| Actions table page bytes | 770,048 | 696,320 | 778,240 | 696,320 |
| Actions index page bytes | 1,056,768 | 876,544 | 1,032,192 | 876,544 |
| Actions + indexes | 1,826,816 | 1,572,864 | 1,810,432 | 1,572,864 |
| Database file bytes | 4,669,440 | 4,669,440 | 4,669,440 | 4,669,440 |
| Free pages | 0 | 62 | 3 | 62 |

The logical payload-plus-moved-text total falls by 10,016 bytes; that figure is
not a full row/index storage metric. The measured table/index allocation falls
by 253,952 bytes. Three nullable stored columns also impose per-row record
header cost on unrelated Actions, so a different population can have a different
net cost. No VACUUM, file shrink or production space reduction is claimed.

## Remaining independent release work

The parent owns fresh SPEC → QUALITY review, full current production-copy
migration/current-fact inverse/strict old cold/reforward, native CLI using
outbound substitutes, merge/push and the established deploy with live readback.
This local handoff does not establish those outcomes. The earlier native
bare-body assertion versus the existing service `(via agent)` formatting
contract is a separate unresolved contract issue; A5a neither fixes it nor
weakens Audit. C2, Reply local-result A5b, OKR/Attempt ownership and legacy D
remain outside this implementation.


## Independent local reviews

Fresh SPEC (`a5a_prepared_spec_review`) then QUALITY
(`a5a_prepared_quality_review`) both APPROVE the 1,300-file `80104a5f…`
implementation and its explicit retired-private-identity boundary. Both
independently verified frozen sources, actual c4 archive and retained logs;
QUALITY additionally read the owned synthetic current/inverse databases. Neither
reviewer reran tests, factories, provider/native or production actions. The root
rehashes the same 1,300 inputs, all frozen files and the exact 34 owned paths
before committing. Full parent copy/native/current-fact inverse and publication
remain separate pending work.
