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
`git diff --check` passed. Production release remains a separate gate; do not
treat this as deployed until its version and service readback are recorded.
