# Workbench Runtime Truth and Readability Design

Date: 2026-08-13

## Goal

Make new Agent Workbench turns complete reliably when provider tools return harmless internal metadata, and make the timeline accurately explain what happened without exposing provider payloads, credentials, raw commands, or private paths.

This change is a focused correctness and readability improvement. It does not redesign the three-column layout, rerun historical failed turns, or add a retry workflow.

## Confirmed Product Decisions

- Fix functional correctness and information truthfulness before visual redesign.
- Use a boundary-normalization design rather than adding a one-off exception for `next_page_token`.
- Keep raw provider results inside the runtime adapter. They must not enter persisted Workbench events, SSE responses, or frontend state.
- Continue to reject credentials or other protected values in content that would actually become public.
- Display tools left open by a terminal turn as aborted, not still running.
- Localize user-visible statuses and safe error summaries into Chinese.
- Display browser-local time consistently across the task list and inspector.
- Derive a deterministic title from the first user message when the task still has the default title.
- Do not retry or mutate the outcome of the existing failed turn. Historical rendering may become more truthful, but execution history remains append-only.

## Problem Statement

The current Codex adapter applies the general credential detector to an entire native provider record before it projects that record into a public Workbench event. A successful Google Calendar MCP response included an internal pagination field named `next_page_token`. The detector treats any field name ending in `token` as sensitive, so the adapter rejected the whole runtime record and failed the turn with `sensitive_provider_output`, even though that cursor would never have been displayed or persisted.

The terminal failure also exposed several presentation defects:

- Tools with a start event but no completion event remained labeled as running after the turn failed.
- Generic `command` and `Tool failed` labels did not explain the operation or result.
- The task list used local time while the inspector displayed raw UTC storage time.
- Machine status values such as `failed` appeared alongside Chinese interface copy.
- A first user request left the task titled `新任务`.

## Alternatives Considered

### A. Normalize at the public boundary — selected

Parse supported provider record shapes and build the smallest public event first. Validate only the resulting public payload and other content that will cross the Workbench boundary. Native result bodies and pagination metadata remain private and are discarded.

This addresses the class of false positive without weakening credential detection for assistant text, confirmations, artifacts, or other public fields.

### B. Exempt `next_page_token`

Add a credential-detector exception for this exact key. This is small but brittle: another provider may use `cursor_token`, `pageToken`, or a different harmless opaque cursor. It also weakens a shared security primitive for one provider-specific incident.

### C. Recursively redact native provider records

Retain each raw record but replace values under sensitive-looking keys. This preserves more diagnostics, but expands the stored and streamed attack surface, makes correctness depend on a growing redaction policy, and may silently hide a genuine provider leak. The Workbench does not need raw native records, so retaining them has no product benefit.

## Security Boundary

### Native provider input

The Codex adapter may inspect a native record only long enough to recognize its type and extract fields required for a normalized event. It must not serialize or persist the original record.

For tool events, the public projection is limited to:

- Stable Workbench event and item identifiers.
- A normalized tool category and safe display label.
- Lifecycle state such as started, completed, failed, or aborted.
- A bounded, adapter-authored summary selected from known-safe status information.
- A safe internal error code when one exists.

The projection excludes native result objects, `structuredContent`, pagination cursors, command arguments, environments, provider session identifiers, absolute local paths, and arbitrary tool output.

### Public-content validation

The credential detector remains mandatory for content that can cross into persistence or the browser, including:

- Assistant text and final answers.
- Confirmation summaries, canonical targets, and public risk descriptions.
- Artifact labels and other browser-visible metadata.
- Every normalized event payload immediately before it is appended.

If public assistant content contains a real credential or protected value, the turn still fails closed with the existing safe error contract. The design changes the location of validation, not the sensitivity policy.

### Unknown provider shapes

Unknown or malformed provider records must not be copied through. The adapter either ignores a record that has no public meaning or emits a bounded safe diagnostic using an application-owned code. Raw provider content is never used as the fallback summary.

## Runtime Event Normalization

Provider-specific event parsing stays in `CodexRuntime`. Workbench storage and the frontend continue to consume provider-neutral event types.

Tool labels are derived from recognized native event metadata through a closed mapping. Initial labels cover the categories already supported by the runtime, such as Google Calendar queries, email queries, and local command execution. If a safe specific label cannot be established, the fallback is a localized generic category rather than the raw provider name or arguments.

Tool completion summaries describe only application-known state:

- `已完成`
- `执行失败`
- `已中止`

They do not reproduce native output. A safe error code may be shown separately so an operator can correlate the failure without exposing the response body.

## Terminal Turn Semantics

Persisted events remain factual. The backend does not invent `tool_completed` records for tools whose provider completion was never received.

The frontend derives presentation state using both the event stream and the authoritative turn state:

1. A `tool_started` event creates an active tool item.
2. A matching `tool_completed` event supplies its persisted terminal state.
3. If the turn reaches `completed`, `failed`, or `stopped` while a tool remains active, the rendered tool state becomes `aborted`.
4. The card explains: `任务已结束，未收到工具完成事件。`

This rule also corrects the display of historical terminal turns after refresh. It does not change their stored events or trigger execution.

## Task Title Transaction

The first user message supplies a deterministic task title when all of these conditions are true:

- The task title is still exactly the default title.
- The task has no earlier turn.
- The message and turn are being accepted successfully.

The store performs the conditional title update in the same transaction that creates the first turn. This prevents a client-side rename race and ensures a refresh cannot restore the placeholder title.

Title generation is local and deterministic:

- Normalize consecutive whitespace.
- Trim leading and trailing whitespace.
- Use a bounded prefix suitable for the task list.
- Preserve the user's language.
- Do not call a model.

An empty normalized message is already rejected by the turn API. A title changed by the user is never overwritten.

## Localization and Time Display

Machine values remain unchanged in API contracts and reducer state. Rendering uses shared frontend helpers for Chinese labels:

- `queued` -> `排队中`
- `running` -> `执行中`
- `waiting_confirmation` -> `等待确认`
- `completed` -> `已完成`
- `stopped` -> `已停止`
- `failed` -> `失败`

Tool lifecycle labels use the same approach. Unknown values receive a safe Chinese fallback while the original machine value remains available to tests and diagnostics.

Backend timestamps are UTC even when stored as `YYYY-MM-DD HH:MM:SS` without an offset. The frontend reuses one strict UTC parser and formats all user-visible task and inspector timestamps in the browser's local timezone. Invalid values render a neutral unavailable label rather than a misleading date.

## API and Data Compatibility

- No provider-native result fields are added to the public API.
- Existing timeline and SSE resource shapes remain compatible; safe display fields may become more specific within their existing bounded string fields.
- Existing machine status values do not change.
- No migration is required for the derived aborted-tool presentation.
- If the atomic title update needs a store query adjustment, it uses the existing task and turn tables without adding competing title state.
- Existing failed turns, confirmations, and artifacts are not rewritten.

## Test Strategy

Implementation follows test-driven development. Each behavior begins with a failing regression test.

Backend coverage:

- A successful MCP completion containing `structuredContent.next_page_token` produces a safe normalized completion and does not fail the turn.
- The cursor value and raw native result are absent from persisted events and public responses.
- Assistant text containing a credential-like protected value still fails closed.
- Unknown or malformed provider result shapes cannot leak raw content.
- First-turn creation atomically replaces the default task title.
- A user-renamed task and a task with prior turns retain their title.

Frontend coverage:

- A terminal turn renders unmatched active tools as `已中止` with the explanatory text.
- A genuinely active turn still renders those tools as `执行中`.
- Statuses and safe tool outcomes use Chinese labels.
- Inspector timestamps interpret backend storage values as UTC and render local time.
- Invalid timestamps remain safe.

Verification gates:

- Focused Workbench backend tests.
- Full frontend unit tests.
- TypeScript and Vite production build.
- Broader relevant backend regression suite.
- Credential and path privacy checks.
- `git diff --check` and scoped linting.

## Deployment and Live Acceptance

After the implementation is reviewed and integrated into the production checkout:

1. Confirm no conflicting uncommitted production changes will be overwritten.
2. Build the Workbench production assets.
3. Record the current launchd process and check Workbench/backlog state.
4. Restart `com.ceo-agent-service.main` because Python and static runtime behavior are not hot-reloaded.
5. Verify a new process is running and the root page, hashed assets, Workbench APIs, and SSE endpoint respond correctly.
6. Confirm there is no unresolved failed or processing backlog introduced by deployment.
7. Run a new harmless calendar or email query that can return pagination metadata and verify it reaches a truthful terminal state.
8. Inspect the browser for localized status, consistent local time, a derived task title, safe tool labels, and no terminal tool left as running.

The previously failed task remains failed and is not retried. Its unmatched tools may render as aborted under the new presentation rule.

## Out of Scope

- A retry button or automatic rerun for historical turns.
- Mutation of existing failed records.
- A new layout, theme, or responsive redesign.
- Displaying raw tool output, raw command arguments, provider payloads, or private paths.
- Changes to shared credential-field heuristics for a provider-specific cursor.
- New Claude or Pi adapters.
