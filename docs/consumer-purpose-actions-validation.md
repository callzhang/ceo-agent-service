# Consumer purpose actions validation — 2026-10-19

## Scope and evidence

The approved purpose output uses action/reason/target/payload/evidence_refs.
OA return is a business return through the existing revert_task executor.
Permission retries reserve at most ten subsequent Consumer attempts and stop
starting new reads at 72 hours from the original verified request dispatch.
The tests use synthetic records and fake providers; no historical OA action
was replayed.

The new regression files cover strict purpose schemas, exact string IDs,
required return parameters, rejection of unsupported actions, verified request
receipt binding, duplicate request prevention, delayed new revisions, real
business-return normalization, restart persistence, immutable deadlines and
counters, replacement-receipt rejection, expiration before source refresh,
and avoiding reservations for turns that the worker pass will never start.
Completing a purpose closes its active wait without erasing its original
counter/deadline. Historical candidates omit absent purpose metadata during
serialization so their immutable bytes and digests remain stable.

## Checks

- Full affected Consumer/contract/schema/purpose run: 312 passed and 4 skipped;
  one obsolete prompt-text assertion failed, was updated to the new action
  wording and passed in the focused rerun.
- Final contract/schema/purpose-output group: 142 passed.
- After merging current origin/main, the full purpose/contract/schema/release/document-Skill group passed 184 tests.
- Purpose budgets, orchestration, release manifest and document Skill group:
  183 passed before the final retry-error-state constraint; the final schema
  group above covers that constraint.
- Ordinary six-turn limits, remaining context-refresh backoff, Worker
  permission wait and Email deferral group: 15 passed.
- Consumer retry/schema/guard targeted groups passed, including the explicit
  request -> verified action -> wait -> retry -> audited business return path.
- Ruff and scoped git diff checks passed.
- Read-only publication preflight matched all 16 installed release assets.

## Native Consumer probe

Using the configured local OAuth service model gpt-5.6-luna, ephemeral native
Consumer commands were given anonymous synthetic facts and the actual OA wire
schema. Business MCP servers were disabled and the controlled namespace was
an empty synthetic server. The accepted retry and return probes made zero
tool calls and passed the service parser. Retry normalized to outcome retry;
return normalized to a proposal with operation revert_task.

The first executable retry probe exposed a schema inconsistency: required
technical error fields invited the model to duplicate the waiting reason as
error_code, which the domain correctly rejected. A new red/green regression
now makes technical error fields optional for Consumer, and constrains retry
to empty code/false technical flags in both JSON Schema and Pydantic. The two
accepted native probes ran after that repair. Earlier harness startup/argument
failures are not counted as model or application acceptance.

A later rerun exposed a receipt-reference representation mismatch: the Consumer
returned `receipt:<external_action_key>`, while retry eligibility compared it
directly with the unlabeled key persisted on the verified action. The retry
validator now removes only one leading `receipt:` label before exact matching;
task identity, unique verified-action selection and `request_access` candidate
checks remain required. A focused regression reproduces the previously rejected
canonical reference. This parser fix does not broaden which receipts are trusted.
After this change, the focused purpose retry/orchestration/output/action-contract
group passed 39 tests; Ruff and the scoped diff check passed.

The first production rerun after the `receipt:` fix exposed another emitted
reference label: `prior_receipt_id=<external_action_key>`. The Consumer's retry
decision was otherwise valid, but the service rejected this label as unbound.
The validator now strips exactly one of the two observed labels (`receipt:` or
`prior_receipt_id=`) before exact key matching; unknown or repeated labels remain
invalid. This second regression was observed failing before the alias fix. The
rerun did not reach Audit or System Execution and generated no external action.

These probes demonstrate model/schema interoperability on two fixed synthetic
cases. The persistent limits are established by controlled-clock SQLite tests;
no real three-day permission wait or production OA write was used as a test.

## Legacy verified OA permission-request receipt compatibility — 2026-10-20

Production Attempts 15681 and 15707 exposed a second compatibility boundary.
Attempt 15681's existing OA comment had a verified `comment` receipt and the
stable `material-read-access-block` identity, but its candidate predated
`purpose_output`. A later retry referenced the exact receipt correctly; the
validator still rejected it because it only recognized current-schema
`request_access` candidates. The failure occurred before Audit/System execution,
and production readback showed no action from Attempt 15707.

The regression was observed failing before the code change. The narrow repair
accepts a receipt as a legacy permission request only when the record is already
verified for the same task, operation is `comment`, the receipt and the
candidate's sole comment have the exact same `material-read-access-block`
identity, and the candidate has no `purpose_output`. Existing exact key,
uniqueness, task binding, retry-budget, Audit and execution requirements remain
in force; ordinary comments remain ineligible. The focused retry tests passed
(10 tests) on 2026-10-20. No historical attempt was replayed.

The fix commit `012a1fc3f61146bd1ebedea6f44480cacfb6fd64` is an ancestor of the
production checkout `63504af9f1b7c456010cc5a808722054dfffe780`. Production
readback showed service PID `7444` and `/healthz` returned `ok=true,status=ok`.
Attempt 15681 retains its verified comment receipt; neither Attempt 15681 nor
15707 was replayed to validate this release.

## Release

Publish code and the reviewed Skill manifest through
`python -m app.deploy --publish-consumer-system-contracts`. Deployment evidence
is the installed commit, new process, health, queues, Attention/History
readback, and exact installed Skill digests. Record the outcome in the owning
agent claim; a code push alone is not deployment.

## Production readback

Published and deployed runtime commit `d6a1b024c` on 2026-10-19. The formal
publication operation `deploy-3391d6c1-ec65-4401-81d2-80d1910ede88` finished
`succeeded` at 21:44:36 UTC; its Consumer-system publication receipt is
`verified` and runtime Skill configuration 347 is active. The installed OA
and document-review Skill SHA-256 values exactly match the release manifest.

The replacement launchd process PID is 63580 and `/healthz` returned
`ok=true,status=ok`. Console readback at 21:45 UTC showed Attention 5 and
summary failed 10, unchanged from the pre-release baseline. Existing reply
failures remain 5; unsubscribe failures remain 4. One new email-provider
action was processing, without a new failure; History returned processing,
done and skipped as its latest three rows. No OA write or historical retry
was started for this verification. These observations establish publication
and runtime loading; the maximum count/deadline behavior is covered by the
controlled-clock regression tests above, not a real three-day wait.

## Receipt-prefix repair production readback — 2026-10-20

The canonical `receipt:<external_action_key>` matching repair was published in
`ba3a26639b52cc8b9456767e9856fc4dbbe9793c` and deployed with
`python -m app.deploy`. The production checkout read back the same commit, the
replacement service PID was `3957`, and `/healthz` returned
`{"ok":true,"status":"ok"}`. Status readback showed 13 queues, zero current
quality-gate violations, two Attention rows and no processing History rows.

Historical Attempt 15681 remains failed with its original receipt-validation
error; its System Execution already records the OA comment as verified. Attempt
15707 also remains failed with the same pre-fix validation error. Neither
historical task was replayed after deployment, so no new OA comment or other
external action was generated as release verification. The repair is covered
by the scoped 38-test group above; the 10-retry / 72-hour budget is unchanged.

## Attempt 15760: request-stage continuation flag repair

Root cause: the Consumer wire adapter changed
`continue_after_execution=false` to `true` whenever `action=request_access`.
Audit had correctly approved the permission request as a stop point, but the
adapter overrode that decision. The orchestrator also tied permission waiting
to the continuation flag, so removing the coercion alone would have skipped
the approved wait.

The fix preserves the Consumer/Audit value exactly. After the audited
`request_access` action is verified executed, the orchestrator starts the
existing bounded permission wait independently of immediate continuation. It
does the same when recovering a completed request execution after restart and
advances the candidate stage lineage for the next Consumer attempt. It does
not treat request delivery as permission restoration, relax Audit, or change
the ten-read/72-hour backoff budget.

Focused regressions in
`tests/test_consumer_access_request_continuation.py` check that a false flag
stays false and that a verified request still starts the wait. Focused test and
production deployment evidence must be added here after those steps complete.
