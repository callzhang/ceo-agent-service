# Consumer purpose actions implementation plan

> **For agentic workers:** Use executing-plans inline; preserve shared checkout changes.

**Goal:** Deploy the approved common purpose output and enforce ten retries / 72 hours after a verified permission request.

**Architecture:** A small purpose-contract module generates strict action branches and normalizes fresh wire output into the current reviewed-candidate model. A separate retry module uses transactional service state and immutable action receipts; the orchestrator reviews retry decisions and reserves bounded invocations before calling Consumer.

**Tech Stack:** Python 3.12, Pydantic, SQLite state storage, pytest, native service deployment.

## Tasks

- [x] Add regression cases in `tests/test_consumer_purpose_output.py`: unsupported action, strict OA return target/payload, generic typed plan, retry evidence and purpose isolation. Run `python -m pytest -q tests/test_consumer_purpose_output.py` to observe missing-contract failure.
- [x] Implement `app/consumer_purpose.py`; connect generated schema/parser in `app/agent_wire_contracts.py` and `app/consumer_agent.py`. Preserve internal proposal/Audit fields and existing executor identities.
- [x] Add `tests/test_consumer_purpose_retry.py` with a controlled UTC clock and temporary Store. Cover ten reservations, 72-hour cutoff, restart/readback, same-review idempotency, unverified receipt and concurrency.
- [x] Implement `app/consumer_purpose_retry.py` using existing state SQL transactions; connect pre-invocation reservations and reviewed retry outcomes in `app/agent_orchestrator.py`, and honor deferral in Worker/scheduled paths.
- [x] Add focused orchestrator integration cases: request -> verified execution -> reviewed retry -> delayed new Consumer; Audit correction; business return; no replay of verified request.
- [x] Update `ci/shared-skills/dingtalk-oa-approval/SKILL.md`, `ci/shared-skills/ceo-document-review/SKILL.md`, architecture/runtime docs, action contracts and release digests. Run focused affected tests, Ruff and diff checks.
- [ ] Commit only this worktree's changes. Reconcile current origin/main by fast-forward/cherry-pick into a clean release branch, run affected checks, push and deploy with `python -m app.deploy --publish-consumer-system-contracts`.
- [ ] Read back live version/PID, health, queues, Attention and History. Record exact verification boundary and retire the claim.
