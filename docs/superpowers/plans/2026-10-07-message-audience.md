# Message Audience Implementation Plan

> **For agentic workers:** Use executing-plans to implement task-by-task; retain red/green and native evaluation evidence.

**Goal:** Apply the approved general audience/split policy, including no self-delivery, to both production roles without a new sender.

**Architecture:** Add one shared fixed publication contract to rendered Audit Rules so persisted custom rules cannot omit it. Existing ConsumerProposal actions and SystemExecutor receipts remain the execution path.

**Tech Stack:** Python, pytest, native Codex role evaluation, existing SQLite execution ledger.

### Task 1: Fixed Contract Regression

- [ ] Add parametrized cases in tests/test_audit_rules.py for Consumer/Audit with saved and empty custom bodies.
- [ ] Assert the rendered prompt requires complete membership, current responsibilities, exact audience-specific actions and no principal/self fallback.
- [ ] Run `/Users/derek/miniforge3/bin/python -m pytest tests/test_audit_rules.py -k audience_split -q`; confirm failures identify missing instructions.
- [ ] Add MESSAGE_AUDIENCE_CONTRACT to app/audit_rules.py and include it in render_audit_rules alongside existing fixed publication contracts.
- [ ] Run audit_rules, consumer_agent, audit_agent and system_executor focused suites; retain the existing partial receipt reconciliation tests.

### Task 2: Native Judgment Comparison

- [ ] Freeze a separate message-audience corpus; preserve existing historical corpora and scores.
- [ ] Use existing native evaluator utilities against baseline 4750e186 and the exact candidate, with identical model/configuration and read-only synthetic facts.
- [ ] Inspect exact target identities, content distribution, no self-copy, no invented DM, no historical refusal bypass and Audit decisions. Lexical or contract pass alone is not acceptance.
- [ ] Keep failures/timeouts as failures and capture exact prompt/source identity; do not lower the oracle.

### Task 3: Review And Release

- [ ] Update runtime documentation and CHANGELOG after focused tests pass; independently commit this behavior change.
- [ ] Open a scoped PR with fixed comparison evidence, independent review and exact full CI.
- [ ] Only after all gates pass, use existing quiet deployment and read back installed SHA, role prompts, health, queues, Attention and History.
- [ ] Existing refused messages remain unreplayed; new candidates require fresh audience evidence and the formal proposal path.

OKR member isolation is an independent implementation following its approved spec; it must not be mixed into this change's acceptance results.
