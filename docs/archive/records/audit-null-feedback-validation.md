# Audit approve result with omitted feedback

## Problem and acceptance

Two separate saved Audit turns (runs 389212 and 389889) completed their
read-only source checks and returned `outcome="approve"` with the other wire
fields present. Both omitted `feedback`; canonical Pydantic validation found
exactly one error, `feedback: Field required`, and retried the whole turn.
The business model already requires `feedback=None` on approval and requires
feedback on return or rejection.

The bounded repair accepts an omitted Audit wire `feedback` as `None` for
approval or failure. Return and reject still require complete feedback, and
approval with non-null feedback remains invalid. Candidate binding, business
validation, tool calls, review policy, external actions, route and session
behavior must remain unchanged.

## Verification

The focused regression failed before the change with one missing-field error.
After adding the wire default, it and both return/reject negative cases pass.
Read-only replay of the two saved final JSON objects now yields a valid
`AuditAgentResult` with `outcome=approve` and `feedback=None`, without changing
their business text or evidence references. This replay proves format
compatibility, not that the underlying business approvals were correct.

The broader affected `tests/test_agent_contracts.py` and
`tests/test_audit_agent.py` passed (137 tests). Ruff, Python compilation and
`git diff --check` passed.

## Production readback

PR #208 merged as `58f104aa3a45ca3d847c85b6e34989f5deb7ac10`.
The standard `python -m app.deploy` waited for in-flight work to drain, then
advanced production from `c444162e1d585087b113a2f71e883e43b2cfac17`
to that commit. The supervisor PID changed from 72101 to 77057.
`/healthz` returned HTTP 200 with `ok=true`; queue, Attention and History
APIs all returned HTTP 200. The dashboard showed one active and 22 queued
items, Attention showed 13 existing items and History reported 36,874
entries. Production's `app/agent_wire_contracts.py` SHA-256 matched the
deployed source, and its working tree was clean. No new Audit turn had run
after deployment at readback, so this is release and health evidence rather
than a natural post-release business outcome. The PR's GitHub test job was
still queued; the 137 focused local tests and saved-output replay above are
the completed behavior checks.
