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
group passed 38 tests; Ruff and the scoped diff check passed.

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
controlled-clock regression tests above, not a real three-day wait.
