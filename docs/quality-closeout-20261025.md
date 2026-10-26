# PR #294 Quality closeout

## Ownership and evidence boundary

The release checkpoint is `8e8c62716048136f810213c78ef373f8e5054b20`.
The Signal target is Store `2026-10-25.3` / Email 54, 476 schema objects,
SHA256 `234e17843afc171b4fa8cb42f3e1d959ef2ba415f43e295dc7db640ab3d02221`.
The predecessor remains Store `2026-10-25.2`, 478 objects with its original digest.
The root business-model task owns the remaining test corrections, final Quality,
standard deployment, live readback and cleanup. The scheduled-release task owns
its protected migration/selector changes and independently reviews them; it does
not deploy concurrently.

No production schema migration or deployment has occurred during this closeout.
A read-only production check still found Store `2026-10-25.2`, 1,140 Signals and
a 514,969,600-byte main database file. This is a dated observation, not a promise
that production remains unchanged.

## Failure classification

- Historical Task20 migration tests called a current Task writer that owns lifecycle
  fields in physical columns. The frozen source owns them in packed payloads.
  The repair must use frozen layouts/defaults, retaining exact source fingerprints.
- Current Action DAO tests were pointed at historical Actions/Email fixtures.
  Current tests must use the selected native schema and native Task/Run parents.
  Fields and direct writers already retired from production are not current APIs.
- Current Sent/execution tests still assert old physical aliases/containers. Their
  business identity, provider-body, correction and no-resend checks must be mapped
  to current Action/Receipt and Consumer Run owners; current behavior must not be
  replaced by a runtime compatibility branch.
- Prior Quality run `40240169213` failed early in historical Action Entity release
  tests, plus two deterministic Consumer/Audit end-to-end tests reporting an active
  Run. The release task has repaired the historical release declaration. The two
  end-to-end failures remain separately tracked until reproduced and verified.

## Acceptance to complete

Single related test files are run locally. Final CI and the mandatory clean-clone
standard-deploy test gate are separate evidence. Final Signal migration acceptance
must use the 476-object target on a current production-derived copy, exercise
current-data inverse, actual predecessor cold read/write and forward migration,
and preserve the existing FK finding set. Test effects remain substitutes.

Deployment, production functionality readback and authorized artifact/backup
cleanup remain pending. Prior 475-object copy/UI evidence and the earlier expired
artifact cleanup remain recorded in `signal-source-owner-validation.md`; they do
not substitute for final 476-object acceptance or current-head Quality.

## Historical fixture unit — completed locally

Commit `4c8105ea8` moves the existing packed Task20 and historical Reply seed
helpers into shared frozen test support. The model-selection migration fixture
reads selections from their exact historical candidate owner, leaving the native
runtime reader untouched. All original test nodes, expected facts, fingerprint
checks and inverse assertions remain.

- `tests/test_model_action_schema.py`: 39 passed (327.64 seconds).
- `tests/test_final_action_schema.py`: 11 passed (61.68 seconds).
- Independent SPEC and QUALITY reviews approved; Ruff and diff checks pass.
- `tests/test_model_result_references.py` was not rerun for this helper-only unit:
  its native runtime tests do not call its unused historical seed helpers.

Quality run `40255367950` at checkpoint `8e8c62716` passed lint, frontend and
browser jobs and passed the previously failing Action Entity preflight. Python
shards then stopped at stale current receipt-caller setup/projection assertions,
one nonempty-unknown-schema bootstrap test, and the two E2E wire fixtures. This
run is still red; the local 50 executions do not substitute for final Quality.

## Current Action tests — owner correction

The current DAO fixture now constructs the selected AutoReplyStore schema; Task
and Run parents use native entity storage. Native execution identity is checked
independently against the Consumer-role `runs` row and its adopted result, rather
than inferring it from two projected aliases. WorkUpdate checks retain summary,
defaults, guarded updates, creation time and BLOB storage and explicitly exclude
the two retired fields.

The first single-file runs passed 10 Action-vector, 6 Sent-result and 1 execution
case. Two additional ownership/retired-field assertions were then added during
SPEC review; their exact nodes still require a fresh run. No runtime or schema
behavior changed in this unit.

Only obsolete direct representations were retired:

| Retired test representation | Existing current coverage / disposition |
| --- | --- |
| Direct `review_candidates` Action writer | Native adopted-result and reviewed execution Store tests; frozen migration fixtures remain explicit. |
| Direct Email plan/claim bootstrap in the generic vector test | `test_email_action_unsubscribe_dao.py` retains continuation upsert, scalar codec and complete Store-cycle coverage. |
| Direct `sent_replies` insertion/projection | Current completed-delivery Action/Receipt public readback and pointer-chain tests. |
| Mutating a Sent external key between different owners | Not a current API contract; current Action identity/idempotency/conflict tests remain. |
| Copying shared receipt bodies into retired Sent/attempt rows | `test_model_result_references.py::test_receipt_correction_preserves_other_attempts_original_meaning` and `test_action_entity_storage.py` retain correction and independent Sent-pointer semantics. |
| Immutable execution alias bytes and split public/physical identity | The alias was retired. Current test retains claim, lease renewal, exact Consumer Run identity, receipt ownership, terminal result, no re-claim and FK checks. |
| Old Sent-result indexes and page-cost formula | Current external-key index plan is checked; historical index cardinality is not a current contract. |

The raw string vector still preserves whitespace, scalar/array/object text, NUL
and Unicode bytes through current receipt storage and the Sent pointer chain.
SQL NULL is tested as a distinct valid current value. The retired writer's NULL,
numeric and BLOB rejection rules are not reintroduced. Current native readback
proves the one actual substitute dispatch; the seeded historical case proves
stable effect identities and idempotence, without inventing a send counter.

Independent SPEC re-review and QUALITY approved the current owner test unit.
The four changed assertion nodes were rerun after review: WorkUpdate and native
Task-delivery 2 passed; indexed Sent lookup 1 passed; native execution 1 passed.
The lookup requires a keyed `SEARCH`, and Task-delivery uses returned native
parent IDs. Scoped Ruff and diff checks pass. These runs extend, rather than
replace, the preceding 10/6/1 single-file evidence.
