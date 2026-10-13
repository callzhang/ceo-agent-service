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

## Production release

PR74 merged as `978f825dd21329e4db8faa62968ddf025a671435` after the automatic
Quality test passed. The Vercel preview failed with its build-rate limit;
this change contains no frontend edit. Formal quiet deployment operation
`deploy-cca9956c-7219-4c67-936a-6b6eac694a80` succeeded at
2026-10-13T13:26:31Z, moving production31d95f4e to978f825d. PID changed75681→7889;
production checkout is clean. The retained backup is471,347,200 bytes,
quick_check=ok, with the current actions table present.

Health, Status, Attention and History return HTTP200. Installed contracts,
Wire validator and generated schema match the merged commit byte-for-byte.
Current public Consumer Settings preview is available and includes the new
message identifier constraint. It is a current common-input preview, not a new
saved task input or a visual browser acceptance claim. Reply task/attempt failed
counts remain two each, two views of the existing failures; there is no increase.
Three Email provider actions remain pending and two scheduled dispatcher jobs
are active. No Consumer/Audit run has started after this deployment at the
readback checkpoint, so this is installed-runtime evidence, not new business
execution. No provider replay or validation message was dispatched.

Receipt:evals/trajectory_optimization/message_target_wire_types.release.v1.json.
Source binding PR71 and native output-schema PR31 remain separate unreleased
work; this release neither enables native schema nor removes the scalar table.

Independent readonly review directly verifies production commit/clean tree, installed blobs/hashes, SQLite deployment receipt, backup quick_check and the two failed trigger pairs. Its sandbox refused the localhost API GET, so API preview/PID are root live readback evidence, not independently re-fetched responses. No blocking finding was reported.
