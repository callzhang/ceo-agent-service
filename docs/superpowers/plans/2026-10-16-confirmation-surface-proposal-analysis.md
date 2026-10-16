# Legacy Workbench confirmation API/frontend retirement proposal

Status: **COMPLETE UNSELECTED PROPOSAL**, pinned to `7111ea47546ac7cb242ff7267993868eacec6561`. Only the two patch artifacts, this analysis and the companion JSON are written; no app/frontend/tests files, runtime, database, provider, CLI, network, index or commit changed.

The approved design at `docs/superpowers/specs/2026-10-11-database-redesign-design.md:108` cancels the obsolete table and old confirmation API. Apply this surface together with the root Store/schema and executor/model/runtime proposals. The required Store snapshot is exactly `(task, turns, events, attachments, artifacts, page)`; its confirmation argument/page fields and statistics key are removed. This patch deliberately does not support the old seven-element snapshot.

## Concrete changes

The runtime patch covers twelve owners: API, TypeScript types/client/event handling, App resource pagination/SSE handling, conversation/inspector/composer presentation, status labels/styles, and deletion of unused `ConfirmationCard.tsx`. It deletes both POST confirm/cancel routes and their DTO/lookup/formatter; ordinary unmatched route404 replaces the existing lookup-plus410 behavior. It removes the confirmation timeline list, pagination/cursor/query, statistics and own legacy progress metadata. No empty list, compatibility stub or replacement authorization mechanism remains.

Remaining resources retain their fields, cursors and query arguments. All58 other API class/function AST owners are exact, including `_owned_turn`, request/mutation/stream guards, upload/download ownership and the SSE loop. Four owners change (`PublicTimeline`, `WorkbenchStatsResponse`, route registry and its timeline handler); five old owners disappear. No new API owner or transaction layer is added. Native CLI/service runtime/ExecutionStep and both historical source factories retain exact source hashes in the JSON.

## Focused regression proposal and evidence

The separate test patch has eight owners: six existing frontend test files, narrow conversions in `tests/test_workbench_api.py`, and new database-free `tests/test_workbench_confirmation_retirement_api.py`. Legacy card/confirmation-progress fixtures are retired; ordinary pagination, state labels and task-switch stale-refresh coverage remain. Converted artifact coalescing uses two distinct resource IDs and unique conversation-scoped links with exact download URLs. Existing evidence-preservation assertions for task, turn, attachment and artifact strings remain; removed confirmation-only fixture strings leave with that fixture.

* Pinned3.13 API RED: **5 failures,1 passing native-tool control** on original API. Candidate GREEN: **6 passing**. Tests cover standard404/OpenAPI deletion, the six-element snapshot, exact surviving fields/cursors/call arguments, statistics without confirmations, old progress metadata removal and unchanged native tool/auto-review payloads. The fake Store opens no database.
* Frontend RED: **5 failures** for the new contract on original runtime. Candidate GREEN: **135 passed in seven focused files**, including unchanged Composer tests. No whole frontend suite ran.
* Current `npm run build` passed: full `tsc --noEmit` and Vite, with existing dependencies copied into owned `/tmp`; no install/network. API and both proposed Python test files passed Ruff and pinned3.13 compilation.
* Both patches reversed together to exact original hashes for all20 owners, passed whitespace-error-all application checks and reapplied to exact final hashes. The actual worktree runtime source hashes remained exact throughout. Converted database-backed API tests are supplied for the combined root candidate and were not executed against an incompatible unretired Store.

## Artifacts and exact hashes

| Artifact | Bytes | SHA256 |
| --- | ---: | --- |
| `2026-10-16-confirmation-surface-proposal.patch` | 37971 | `0d437d6586e5f35d07cdf3a5c4bd98845885e29c0aaa884987fd4292799d71c6` |
| `2026-10-16-confirmation-surface-proposal-tests.patch` | 44521 | `e83cfac91542872f5b6ef2e3be2952fac74594990fea1fa0c7331878e2ee7125` |

The JSON records all changed-owner before/after hashes, preserved native/historical source hashes, full command exits/output tails and finite API AST owners. Root companion Store/schema patch is pinned to SHA256 `a52dad8ca09acf72dc37c012c2959241d289d799e0d1c4609a5083a3a63f45c4`.

## Remaining references and root integration boundary

Candidate API has zero legacy confirmation references. Candidate frontend runtime retains only unrelated Attempt rerun dialogs, CRM `needs_confirmation`, email-category terminology and ordinary historical native tool labels. They are not the canceled Workbench feature. Negative native Codex tests and former confirmation-tool-as-ordinary-MCP tests remain valid; browser tests contain only inert confirm/cancel test-double methods. Store/schema/facade/executor/model/native-event references and their tests remain root/companion owners. Historical `task_storage` and `workbench_storage` remain unchanged source oracles.

Root must update behavior docs in the same eventual selected commit: both opening paragraphs must remove `confirmation/` from Workbench auxiliaries; replace architecture's former382-383 read-only410 paragraph with actual API/resource/state removal and unchanged native CLI approval; remove `或进入等待确认` from architecture/runtime's native trajectory cache-release sentence (former2054/2898). Exact replacement prose is in the JSON. This proposal has not modified either behavior doc.

No constructor/schema/migration/publication, real browser, real native CLI, provider or production result is claimed. Root still owns combined current constructor/API tests, ordinary lifecycle/native behavior, source-bound conversion/inverse and fresh SPEC then QUALITY review before selection.

Owned temporary proof tree: `/tmp/confirmation-surface-proposal.6gMwtn`; removed after final artifact validation; absence verified.
