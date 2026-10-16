# Current Email State caller proposal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development with independent SPEC then QUALITY.

**Goal:** Replace the last nine current Email native State consumers with source-faithful projections/writes.
**Architecture:** The adjacent complete2026-10-16-state-email-current-proposal.patch modifies exactly nine existing method bodies plus two imports after the four-file constructor proposal. Keep received connections, immediate transactions, error handling, public six-field cursor dictionaries and all non-State statements. No new table/field/policy/helper/fallback.
**Tech Stack:** Python3.13.12/SQLite3.50.4.

Base EmailStore SHA9e02860962447adad7f62fcea9e74b2d9a1b244790b4dd02e95dba94c58d4e9d; candidatec7f80cd9607cc0b1d500a698f341065e3b22f41b73a536f0ab3c8af88db5ecc9; patch03580218eb9ee7ead234878ae6cced0b4a5ac0c90c4c0e55d5f6f072e950a5e4. Apply --unidiff-zero after DAO/lifecycle/mixed23/constructor proposals only in a disposable tree until the entire current consumer/version/progress/publication closure is ready. All nine complete method changes are executable code in the adjacent patch.

| Existing method | Preserved behavior |
|---|---|
| _advance_cursor | Same validation and missing/expected-generation conflicts; plain insert, existing same-generation max UPDATE, reset guarded by expected generation and rowcount exactly1. Caller alreadyowns BEGIN IMMEDIATE. Original encoded UID leaf wins/ties; new integer encoded only when it wins. |
| persist_empty_scan_cursor | Same stripping, shared advance, own immediate transaction and six-field result. |
| historical_traversal_cursor | Exact missing None/0 result and present integer conversion. |
| enqueue_historical_page | Non-State candidate upserts unchanged; traversal UPSERT uses native nullable generation and max/switch rule. No scan constraints imposed on history. Whole existing immediate transaction retained. |
| get_scan_cursor | None or six-field dictionary without hidden rowid. |
| record_scan_cursor | Existing Python clock, immediate transaction/shared advance/readback. |
| model_scan_progress | Same LIKE prefix, SQLite-error behavior, malformed JSON skip and sum/latest reduction. |
| email_processing_progress | Only cursor source replaced; config join, sorting, counts and separate throughput connection unchanged. |
| _unsubscribe_cancellation_decision | Same supplied connection and exact pending-key fence. Active leases remain only reply/scheduled_execution, same task ID, owner nonempty and terminal empty; no expiry/generation/PID filters. |

The complete test fixture adjustment is2026-10-16-state-email-current-test-proposal.patch: add State projection import and change only the atomic scan rollback test's final direct native cursor-table assertion to its State projection. The persisted business/plan/action rollback assertions remain unchanged. It is a test adjustment to the new physical target; no application alias is introduced.

- [x] Read-only finite inventory: nine current methods/thirteen direct SQL statements; archived factories and source schema dictionaries remain native. No obsolete helper proved removable.
- [x] Complete executable9-method proposal, compile and exactASTscope check. Root disposable actual constructors/currentcursor-CAS/modelprogress+scanrollback10 tests passed2.37s with the exact fixture assertion adaptation. Initial harness/obsolete-assert corrections are recorded in ../specs/2026-10-16-state-email-current-readiness.json.
- [ ] Complete historical queue/traversal, processing API and unsubscribe cancellation/fence/owner integration checks, including current complete module chain and actual/fresh SQL scalar/CAS differential edge cases. Existing useful files: test_email_worker historical queue cases, test_email_web_api processing-progress three cases, test_email_unsubscribe_direct principal cancellation and namespace/serialization cases; adapt physical seed statements explicitly in test proposals if they still name retired tables.
- [ ] Fresh independent SPEC then QUALITY of exactproposal, preserving every unrelated method/function AST and all native archived declarations.
- [ ] Select whole State currentconsumers, marker/version/progress/coordinator closure together; never activate this proposal alone.
- [ ] Actual DBcopy forward/current-factinverse/genuinecurrentpredecessorcoldreads+writes/reforward, native CLI with fake outbound and UI flow proof, then remaining sourcefamilies to final10core+ledger. No production/main publication without final approval.

Verification uses pinned /Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python with PYTHONDONTWRITEBYTECODE=1 and pytest -p no:cacheprovider --basetemp under an owned /tmp TemporaryDirectory. No whole-suite, actual DB or provider access is part of this proposal.

