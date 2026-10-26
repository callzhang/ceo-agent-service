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

## Final 476-object current-data copy acceptance — passed

The final pinned 476-object release was exercised on a fresh consistent read-only
production-derived copy using the committed validator and the actual `dc07ddcbe`
predecessor code. The complete run exited successfully in 954.1 seconds:

- 1,140 original Signals and 172 original shared provenance parents.
- Forward manifest/integrity verification, new current Signal writes and a
  synthetic stored Action receipt (no provider call).
- Current-data inverse with typed comparisons, then actual predecessor cold
  reads and writes, including continuation of both sole/shared sources.
- Re-forward with 1,147 final Signals and identical required facts, identities,
  allocation watermarks, references and 48 unrelated table snapshots.
- Original FK finding set remains exactly `runs / 190042 / tasks / 1`; no Task
  was fabricated and no exception was silently deleted or repaired.
- Real provider calls: zero. This is storage/old-code acceptance, not a new real
  CLI or provider-delivery run. Current-head Quality and production readback
  remain separate gates.

## Native receipt callers — final 15-case proof

The current caller file uses native Task/Run CAS, native adopted-candidate
invalidation, native receipt/Run projections and existing retained-fact fixture
helpers. The failed-Audit assertion now selects the canonical binding to the exact
provider-body receipt, rather than accidentally selecting the Sent fact.

An intermediate test edit moved generation advancement after the ACK. Review
rejected that weaker sequence. The final test retains invalidation and generation
advancement **before** the late ACK. It independently reads back the ACK-owned
verified attempt/body/pointer/original Run; the generic current-candidate outcome
method still rejects its now-stale generation, while the already-dispatched old
execution can finish and the new Task remains pending. No runtime policy changed.

Final single-file run: `tests/test_action_receipt_callers.py`, 15 passed in
13.50 seconds. The first sandboxed attempt also hit process-table access denial
in the maintenance test; the authorized read-only process-table rerun passed.
Ruff and scoped diff checks pass. Independent final SPEC and QUALITY approved after the late-ACK sequencing
correction.

## Integration iterations

The complete 50d20cd candidate was pushed and checked by Quality run
`40259719674`. Lint, frontend and browser jobs passed; Python still failed in a
current artifact fixture using the historical Task20 container, two stale schema
fixture/inventory assertions, and two deterministic E2E cases. These findings are
not counted as green or hidden by a narrower test command. The artifact fixture
belongs to the root task; the two schema test files and E2E diagnosis belong to
the release-coordination task.

Current main then supplied the independently owned schema-read cache, fresh Store
and Email test templates, and fixed 2 GB / 5 minute process budgets. The root
integrated those commits normally, preserving the existing top-level MemoryLimit
import and the newer fixed limits. This does not change the Signal source/target
DDL. Main's deploy gate manifest and enabled/suspended state are used as committed;
this task does not bypass or redefine them. Final candidate Quality is still
required independently before publication.

## Native Task-bound artifact fixture

The current artifact/tool tests now install the actual native schema, use the
existing native Task seed with exact IDs 7/8 and the original `pending` status,
create real TaskInputs, bind current/claimed inputs and rotate the generation by
Task CAS. No runtime, tool-access or Audit behavior changed. Independent SPEC
comparison confirms all ten original test bodies are unchanged: eleven nodes,
28 original assertions and nine exception checks remain; five fixture-integrity
assertions were added. The final pending-status version passed all 11 nodes in
13.88 seconds under the current fixed 2 GB / 5 minute limits. Ruff/diff pass;
independent final QUALITY review approved.

## Metadata and role-catalog fixtures

Native hydration receives a physical Run row. Its lock-release regression now
compares committed `runs.status/session_id/lease_expires_at` against that exact
raw snapshot, without a removed record-type predicate or projected DTO key.
Public AgentRun session assertions remain unchanged. The role-catalog setup uses
one existing native reply Task with the original pending state and generation;
all Consumer/Audit tool-catalog expectations remain unchanged.

Final single-file results: metadata boundary 5 passed in1.94 seconds; effect guard
19 passed in1.48 seconds. Ruff/diff pass; final independent SPEC and QUALITY approved. No runtime or
permissions changed.

Quality37bd's worker zero-completion assertions were diagnosed with one exact
bundled-Skill test node. The Task was claimed once, then two Consumer attempts
failed with `codex_result_invalid` / `native result failed current Consumer wire
contract` before Audit. Zero completed work is not a zero-claim observation. A
synthetic protocol-fixture correction is being investigated; no production
worker, authorization or retry-policy change has been made.

## Historical tool and publication-window corrections

The release owner froze the explicit Store25.1/Email54→25.2/Email54 trigger tool
at 479 source objects (`eb0d83ce…`) and478 target objects (`7b4c9187…`). It verifies
its own historical target, never the later current manifest. The automatic
selector rejects25.1 and retains only the precise25.2/54→25.3 Signal transition.
The duplicated selector snapshot was removed; one read-only transaction still
reads marker and ledger together.

Independent review found an additional real pre-publication defect: the quiet
reader still selected25.1, then its first25.2 correction also inherited an Email53
exception. The final branch requires both Store25.2 and Email54 plus the full
frozen schema. The owner reproduced the Email53 hole with a failing negative,
then passed the positive0→1 in-flight check and missing-object/Email53/empty-ledger
no-write regressions (4). The explicit historical selector rejection and real
empty-file constructor-failure nodes each passed (1+1). These changes do not alter
admission, lease, generation or in-flight predicates. Final independent SPEC and
QUALITY approve the combined correction. The architecture/runtime and historical
trigger validation docs distinguish explicit history from current publication.

## Worker protocol fixture acceptance

Current native Consumer and Audit parsers require the native decision envelope;
the scripted worker fixture was still emitting historical domain output. Its
replacement uses the existing schema-aware Consumer fixture encoder, preserves
all explicit malformed purpose values for rejection, and emits the existing
Audit native envelope. Service-owned candidate binding remains absent from model
output and is injected by the service. No application/Audit/authorization/retry
policy changed.

New regressions cover flat→native→parser proposal round-trip, rejection of an
unchanged malformed explicit purpose, and nested needs-human option-plan plus
reason/basis preservation. Narrow Audit-binding/Consumer/auth checks passed4;
CI calendar/image representatives passed5; the needs-human round-trip passed1.
These counts overlap the file and are not summed as unique cases.

One full related-file run completed within the fixed budgets:99 passed,4 failed
in119.41 seconds. Persisted readback showed three remaining old Audit-turn fixture
discriminator failures and one test-subclass counter omission. Only the four
failed function families were rerun after the exact fixture corrections:8
parameterized cases passed in16.21 seconds. The original expected lifecycle,
Audit counts, external effects and retry limits remain unchanged. No second full
file run was performed; final candidate CI is still a distinct gate. Final independent SPEC
and QUALITY increment reviews approved.

## Main reconciliation and CI instruction input — 2026-10-26

Normal merge with main `7ec0f0cdb` preserves its separately authorized G1
provider-receipt-only document write contract: no automatic body readback.
The exact native `doc +list` pre/post identity lookup and already-applied
`doc.operation.v1` partial-success identity/error receipt remain covered; no
new Audit, authorization, retry or reconciliation policy was introduced.
Historical Business owner conversion/inverse tests remain because the explicit
conversion APIs still exist; the current Store import-owner test is separate.
The merge inventory/Action/Postfix/historical owner group passed82 tests.

Quality `40266574005` exposed a host dependency: CI has no shared AGENTS input.
An isolated HOME reproduced Consumer completed / Audit running before an Audit
runtime attempt existed. The required-file loader runs after claim and throws;
the existing orchestrator then defers on the active lease. Tests now supply their
own shared instruction file and patch only its test path. Application missing-file
behavior is unchanged and its negative test still passes. With missing HOME and
CI skills root,24 focused cases pass, including both deterministic E2E cases,
Worker cases, the exact missing-instruction failure, native lease expiry and all
17 document recovery cases. The native lease regression also no longer updates
retired Run record_type/legacy_key columns. The preceding combined run had69
passes and that one stale SQL failure; it is not reported as green.

Consumer manifest source digests were recalculated after preserving both parents'
skill changes. Weekly-report old hashes match installed main; WeChat already
matches its new source hash, so its old installed hash is advanced to that same
verified value for the publisher's strict preflight. Current-head remote Quality,
standard release, live readback and final artifact cleanup are still pending.

## Next Quality failures after CI HOME repair

Quality `40268307304` at `7623826d5` passed lint/frontend/browser and advanced past
the formerly blocked Worker/E2E cases. Its twelve Python shards then stopped on
current fixture/contract issues, not resource ceilings. File owners repair these
without changing application review or execution policy:

- Main4ab869's current Email/History/maintenance fixture corrections integrate by
  normal merge. Meeting retains exact target-key/receipt/no-backfill/no-resend
  checks; the public exception is MeetingDeliveryError, and76 cases pass.
- RuntimeAttempt fixture uses the real parent_id owner. Cross-line Basic uses an
  actual user:password credential, retaining the no-secret/redaction assertions;
  five focused tests including benign Basic-information handling pass.
- Runtime-turn fixtures use current Consumer purpose wire and the existing Codex
  native encoder.56 initial file cases pass; four stale DTO/physical Run checks
  then pass individually. Adopted business results remain available while absent
  duplicate envelope/body columns are asserted absent, rather than revived.
- CLI unavailable-original fixtures update the native TaskInput owner. All five
  affected DingTalk/email refusal cases pass with no enqueue or send.
- The original Action field-contract SQL exactly matched the retained25.2
  declaration, not the25.3 Signal declaration. Its explicit tooling renderer is
  frozen accordingly; only its two comment lines change. The separate full
  current-release SQL is regenerated at476 objects and its field-map SHA is
  updated.13 contract cases pass, and the new exact text+digest regression passes;
  the final two header/digest-sensitive cases also pass. Independent review
  identified and corrected the initially stale digest instead of accepting a
  misleading artifact. No app schema or migration mapping changes here.

Attention query-plan corrections and the next complete candidate Quality result
remain pending; these focused counts do not turn the failed run green.
