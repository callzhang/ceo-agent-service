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

These probes demonstrate model/schema interoperability on two fixed synthetic
cases. The persistent limits are established by controlled-clock SQLite tests;
no real three-day permission wait or production OA write was used as a test.

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

The controlled-clock tests cover the maximum count/deadline behavior.

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

## Historical incident: OA Attempt 15681

The production History detail for Attempt `15681` records 235 role runs: 233
failed Consumer runs, one completed Consumer run, and one completed Audit run.
Among the 233 failed Consumer runs, 232 were classified as
`runtime_business_result_failed` and one as `codex_result_invalid`. The
successful Consumer ran after those 233 failed Consumer runs; Audit then
approved the candidate. The System Execution record verifies exactly one OA comment, at
`2026-10-19T18:11:53.364Z`, asking the file owner or space administrator to
grant read-only access. The Attempt's terminal `done` status therefore means
the approved comment was delivered and verified; it does not mean the files
became readable or that the underlying OA review was completed.

The unbounded loop came from the pre-release dependency classification:
`dependency_read_unavailable` was included in
`EXTERNAL_DEPENDENCY_WAIT_ERRORS`. That path deferred the task and refunded
the worker attempt budget, so repeated Consumer failures did not reach the
persisted consecutive technical-failure limit. Commit `d9d326d63` removed
ordinary `dependency_read_unavailable` from that wait set. The fix now lets
these failures reach the existing six-consecutive-technical-turn terminal
limit; only service-side context refresh and the audited purpose `retry`
remain in the dependency wait set. For permission-request retries, the
separate persisted purpose budget remains capped at ten subsequent Consumer
starts within 72 hours. This was a historical run before the bounded behavior
was deployed; no historical Consumer or OA action was replayed to verify it.
