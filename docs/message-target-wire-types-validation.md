# Fresh message identifier Wire validation

The full native trajectories and actual service inputs are retained on the separate
source-binding draft branch at commit `a31e33d3` (PR #71), in
`evals/trajectory_optimization/full_audit_candidate_inspection.native.results.v1.json`.
The corrected fixture produced an Audit approval for a numeric direct-message
recipient in the inline control, while the candidate-only arm returned it. This
single comparison is not a population estimate or a production send failure.

Offline inspection established the concrete boundary defect: the domain presence
check used `str(value).strip()` without changing the original value. The original
integer would remain an integer in the CLI argv in an offline construction. No message was dispatched
in this investigation.

The repair validates known message identifiers at the existing fresh Consumer Wire
conversion, including all executable human-option plans. Non-null values must be
strings containing a non-whitespace character. Exact strings, including leading
zeroes, are preserved; optional nulls remain permitted subject to existing target
requirements. Other capabilities retain generic JSON targets. The existing result
parse/correction flow receives validation failures; no Audit policy, routing,
recipient resolution, authorization or dispatch mechanism is added.

The historical domain Pydantic validator is unchanged, and a regression proves old
numeric targets remain readable. The shared generated output schema is stricter,
so historical readability does not imply that old values validate against the new
schema. Provider enforcement of conditional JSON Schema is not assumed: the Wire
parser enforces the rule locally. Native output schema work remains independent.

## Verification

- Initial regressions: 58 failed, 3 passed. 57 failures demonstrated missing type
  rejection; one was an overly specific expectation for an existing blank-value
  error, subsequently corrected.
- Affected integration tests: 516 passed, 4 skipped (contracts, Consumer,
  orchestrator, executor, attempt details and history).
- Added executable human-plan regression: the focused file passed all 62 tests.
- Independent readonly review identified missing non-whitespace schema semantics.
  An added schema assertion failed before the fix; the existing protocol-style
  non-whitespace schema constraint repaired it.
- Final focused contracts and identifier regressions: 164 passed. Ruff passed.

No live provider message, production deployment or broad prompt quality acceptance
is claimed by these tests. The current scalar Audit table remains unchanged.

## Current-main integration and correction lifecycle

Merged current main `31d95f4e` without rewriting either branch; the only conflict
was appended ownership rows, both retained. The affected integration group passed
517 tests with 4 skips after the merge. A further scripted real
ConsumerAgentRunner regression verifies a numeric target fails result parsing,
saves `codex_result_invalid` with the specific field detail and no final result,
and includes that detail in the next existing Result Correction input. This is
service lifecycle evidence with a scripted executor, not a native model outcome.
Its first assertion incorrectly expected the outer exception to carry the field;
the parser intentionally keeps the detail in its cause and persisted error. The
assertion was corrected without changing runtime error handling. Final contracts
and identifier/lifecycle checks passed 165 tests; Ruff passed.
