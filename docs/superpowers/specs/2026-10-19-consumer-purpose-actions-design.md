# Consumer purpose actions and bounded permission retries

Approved by Derek on 2026-10-19 in the OA repair conversation.

## Contract

Each purpose returns the common `action`, `reason`, `target`, `payload`, and
`evidence_refs` fields. Its registered action definitions generate the allowed
action branches and strict target/payload schemas. `return` is a business
return: the OA definition maps it to the existing `revert_task` executor.
The service does not interpret natural-language reason strings for routing.
Existing scoring, stage lineage and durable-memory fields remain metadata.
The existing internal reviewed-candidate model remains the storage and Audit
contract; fresh purpose output is normalized into that model before review.
General multi-operation work uses a typed `plan` payload; purpose-specific
single operations use direct action names. No unrecognized action executes.

## Retry lifecycle

`retry` is an ordinary purpose action reviewed by Audit. Permission requests
are separate reviewed actions, executed once through the existing executor.
A permission retry references a verified request receipt; mere model claims
and uncertain actions cannot start its clock. The retry state is persisted
against the business object and receipt, independent of native sessions,
proposal revisions, worker attempts and process restarts. The verified request
time anchors an immutable 72-hour deadline. At most ten subsequent Consumer
attempts may begin. Retries use exponential backoff; the last scheduled wake
never exceeds the deadline. An expired deadline or exhausted retry count runs
no additional source read or Consumer invocation. It reports a bounded failure
without executing an unreviewed OA return. Consumer can choose business return
when access is genuinely denied and no request mechanism exists. OCR/parser
failures alone do not establish access denial or applicant fault.

## Recovery and release

Reuse the existing persisted state store and reviewed action receipts; add no
database migration or credential transport. Keep external actions and exact
receipt reconciliation under the current executor. Persist reservations before
starting retries so crashes cannot reset the budget. Update operation Skills,
architecture/runtime documentation and the release manifest. Validate strict
schemas, receipt eligibility, counters across revisions/restarts, count and
time boundaries, audit rejection and ordinary OA return. Publish and deploy
through `python -m app.deploy`, then read live version, PID, health, queues,
Attention and History. Do not replay any historical OA action for validation.
