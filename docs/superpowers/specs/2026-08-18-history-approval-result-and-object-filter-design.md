# History Approval Result and Object Filter Design

## Goal

Make History answer two questions without opening an attempt:

1. What was the business result of an approval task?
2. Which single kind of History item is currently being viewed?

Approval results must describe the approval outcome rather than the Agent's internal execution status. Object-type filtering must use one dropdown instead of a row of checkboxes.

## Scope

This change covers the server-rendered `/history` page and its query-string behavior.

It includes:

- approval-result pills on approval History cards;
- a single-select object-type dropdown;
- result resolution from persisted structured execution records;
- focused regression tests and live History verification.

It does not change approval policy, execute or replay approvals, alter the approval detail page, or introduce a new persisted approval-result field.

## History Card Design

An approval card always shows its `审批` type badge. Next to it, one business-result pill shows exactly one of:

- `已同意`
- `已退回`
- `已拒绝`
- `已留言，仍待审批`
- `无需处理`
- `待你处理`
- `处理中`
- `处理失败`
- `结果未知`

Normal approval cards do not show `Completed` or `Skipped`. Those values describe Agent execution and can be mistaken for the approval decision.

Existing failure, automatic-recovery, and human-decision UI remains visible when action is required. In those cases the result pill and the existing reason/next-step section work together: the pill gives the business state, and the section explains the system state and available action.

The pill uses the existing semantic status colors:

- successful approval results use the success treatment;
- returned or pending-human results use the warning treatment;
- rejected and failed results use the failure treatment;
- no-action and unknown results use a neutral treatment;
- processing uses the in-progress treatment.

All styling uses the existing theme variables so the result remains readable in light and dark system modes.

## Object-Type Dropdown

Replace the `replay`, `wechat`, `审批`, `task`, and `meeting` checkboxes with one dropdown labeled for History object type.

Options are:

- `全部`
- `replay`
- `wechat`
- `审批`
- `task`
- `meeting`

Only one object type can be selected. `全部` is the default and omits `object_type` from the URL. A specific selection writes one `object_type=<value>` parameter. Search, status filtering, page size, and pagination preserve the selection. The `meeting` selection continues to be the only selection that includes similar Codex-session results.

## Structured Result Resolution

History resolves the pill at render time instead of duplicating the result in a new database column. This makes existing and future records use the same logic and avoids a data migration.

The resolver uses persisted structured evidence in this order:

1. For an executed Consumer/Audit run, require an Audit result whose outcome is executed and whose side effects are confirmed. Resolve the approval decision from the matching structured approval action in the Consumer proposal.
2. For the direct OA path, use the recorded OA action only when the attempt has a terminal successful result or a successful OA action receipt.
3. If no approval action was executed, use the structured workflow outcome: no action, needs human, processing/reconciliation, or failed.
4. If structured records are missing, malformed, ambiguous, or contradictory, return unknown.

The resolver never searches `audit_summary`, `codex_reason`, error messages, or other prose for decision words. A malformed record degrades to `结果未知`; it must not break the History page or invent a result.

The resolver is a pure presentation-domain function with a typed result value. The HTML renderer maps that value to the Chinese label and semantic CSS class. Structured record parsing and HTML rendering remain separate so both can be tested independently.

## Error Handling

- Invalid Consumer or Audit JSON yields `结果未知`.
- More than one conflicting approval action yields `结果未知`.
- A proposed action without confirmed Audit execution does not yield a successful business result.
- `needs_human`, reconciliation, and failed states override an unconfirmed proposed action.
- Non-approval History items retain their current status pills and behavior.

No resolution failure is exposed outside the local audit UI, and no external action is triggered while rendering History.

## Tests

Regression tests must be written before implementation and must fail against the current behavior.

Focused tests cover:

- confirmed approve, return, reject, and comment actions;
- no-action, needs-human, processing/reconciliation, and failed states;
- missing, malformed, ambiguous, and unconfirmed structured records;
- removal of normal `Completed` and `Skipped` pills from approval cards;
- unchanged pills for non-approval cards;
- dropdown rendering with `全部` selected by default;
- each single object-type selection;
- URL preservation across search, page-size changes, and pagination;
- `meeting`-only similar-session behavior;
- theme-compatible CSS classes without fixed light-only card colors.

Run the focused History tests first, then the complete test suite.

## Deployment and Acceptance

After the implementation commit is merged into the release checkout:

1. Restart `com.ceo-agent-service.main`.
2. Verify the launchd process has a new PID and is running.
3. Verify there is no unresolved failed or processing backlog.
4. Read back `/history?object_type=approval&limit=20` from the live service.
5. Confirm approval cards show business-result pills and do not show normal `Completed` or `Skipped` pills.
6. Confirm the toolbar contains one object-type dropdown and no object-type checkboxes.
7. Confirm selecting each dropdown option returns the expected subset and preserves other query parameters.

No approval, message, or other external side effect is performed as part of acceptance testing.
