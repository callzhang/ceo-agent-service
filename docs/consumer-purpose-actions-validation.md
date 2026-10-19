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
