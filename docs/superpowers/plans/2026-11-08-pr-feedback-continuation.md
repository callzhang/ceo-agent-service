# Same-conversation PR feedback continuation

Derek explicitly authorizes implementation and deployment. Expected: CI failure or unresolved PR review automatically resumes the original repair conversation, preserves its native session and updates its existing PR; pending checks wait and new commits are verified independently. Actual: `pr_created` is terminal and feedback required a manual Workbench turn.

- [x] Regression tests for CI/review feedback, deduplication/restart, pending/green/current head, fairness and stop.
- [x] Read paginated GitHub checks, unresolved review threads and current review decisions; persist exact feedback identity and use deterministic native turn IDs.
- [x] Continue the existing conversation/PR, with the existing bounded attempts per unchanged feedback and a fresh cycle for new evidence. Keep monitoring open PRs after green to catch subsequent feedback. Never merge/deploy or replay business actions.
- [x] Update the shared card/procedure/contracts and verify targeted tests/build plus independent review.
- [ ] Push/deploy through the existing fixed gate; observe PR343 receive automatic feedback and updated checks; verify source links/health/queues.

Local verification: initial seven new scenarios failed against old coordinator; three independent-review regressions also failed before correction. After implementation, 34 focused backend tests and 55 frontend tests passed, production build succeeded, and the real PR343 readback returned all 53 checks plus review history. Independent bounded review found no remaining concrete blocker. Browser/live automatic continuation remain deployment acceptance steps.
