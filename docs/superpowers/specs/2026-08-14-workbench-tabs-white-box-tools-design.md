# Agent Tab and White-Box Tool Events

**Date:** 2026-08-14

## Goal

Keep Agent Workbench as the default page at `/`, while restoring visible access to the existing audit-console features through a shared top navigation. Replace generic tool cards such as `本地命令` and `MCP 工具` with a truthful, white-box record of what the agent invoked and what the provider returned.

## Confirmed Product Decisions

- `/` remains the Agent homepage.
- The shared navigation contains, in order: `Agent`, `History`, `Tasks`, `用户反馈`, `服务修复`, and `Settings`.
- The Agent page marks `Agent` active. Every existing audit page adds an `Agent` link, while retaining its current active item and route.
- Tool execution is white-box because this is a local, single-user application. The application does not replace commands, paths, arguments, or tool names with generic labels.
- The public event contract includes the actual action and provider result. It does not include unrelated process environment variables or non-tool provider protocol records because those are not part of the action being explained.

## Current Failure

The root route serves only the React Workbench bundle. That bundle has no links to the server-rendered audit console, even though `/history`, `/tasks`, `/user-feedback`, `/service-bugfix-candidates`, and `/settings` still exist. Users entering `/` therefore cannot discover the original features.

Tool events lose meaning at three layers:

1. The Codex adapter converts every shell execution to `本地命令` and unknown MCP calls to `MCP 工具`.
2. The public API permits only `tool`, `summary`, `status`, and `tool_call_id`, then removes paths and other values.
3. The frontend accepts only string payload fields and replaces local paths or credential-shaped text before rendering.

Changing only the visible label would leave the system black-box. The event producer, public contract, reducer, and tool card must change together.

## Shared Navigation

### Agent page

Add a `GlobalNav` above the existing three-column Workbench shell. It uses normal same-origin links rather than client-side route emulation:

| Tab | Route |
| --- | --- |
| Agent | `/` |
| History | `/history` |
| Tasks | `/tasks` |
| 用户反馈 | `/user-feedback` |
| 服务修复 | `/service-bugfix-candidates` |
| Settings | `/settings` |

`Agent` is rendered with `aria-current="page"`. The workbench task query parameter remains unchanged, so a selected task continues to use `/?task=<id>`.

### Existing audit pages

Add `Agent` as the first entry in the server-rendered `_top_nav`. Existing pages keep their current active state and routes. The brand continues to link to History; navigation to Agent is explicit through the new tab.

### Responsive behavior

On narrow screens, the navigation remains a single row with horizontal scrolling. It must not wrap into the conversation area or reduce the existing mobile task/conversation switching behavior.

## White-Box Tool Event Contract

### Common fields

Every new `tool_started` and `tool_completed` event contains:

- `tool_call_id`: stable correlation ID owned by Workbench.
- `kind`: `command` or `mcp`.
- `name`: exact command executable when available, otherwise exact `server.tool` identity.
- `native_id`: provider item ID for diagnosis.
- `status`: `running`, `completed`, or `failed`.

The completion event repeats the action identity and arguments so it remains self-describing when pagination loads it without the start event.

### Command execution

Command events copy the provider's action fields without semantic replacement:

- `command`: exact command text supplied by Codex.
- `cwd`: exact working directory when the provider supplies it.
- `exit_code`: numeric exit status when supplied.
- `output`: exact aggregated output when supplied.
- `provider_item`: the complete command item object for expandable raw inspection.

The UI header uses the command text, not `本地命令`.

### MCP execution

MCP events copy:

- `server`: exact server name.
- `tool`: exact tool name.
- `arguments`: the provider-supplied JSON arguments.
- `result`: the provider-supplied JSON result on completion.
- `provider_item`: the complete MCP item object for expandable raw inspection.

The UI header uses `server.tool`. It never falls back to `MCP 工具` when either exact identifier is available.

### Data boundaries

- Tool fields accept JSON objects, arrays, strings, numbers, booleans, and null.
- Existing provider line-size and event-size limits remain resource-safety boundaries. Rejected oversized or malformed provider records continue to fail closed.
- Exact local paths and provider-supplied action data remain visible. Recognizable credential values are replaced in place while their field names remain visible, so one credential-bearing leaf cannot erase or fail the whole diagnostic event.
- The adapter does not dump the process environment, Codex session files, reasoning items, or unrelated provider records into tool events.
- Confirmation authorization remains a separate capability. Making a proposed write visible does not bypass the existing confirmation or exactly-once execution rules.

### Historical events

Existing persisted events cannot be reconstructed because the discarded command and MCP fields were never stored. They remain readable, but the card explicitly says `历史事件未记录命令详情` rather than pretending the generic label is complete. Only tool calls created after deployment receive the white-box fields.

## Frontend Presentation

Each tool execution remains a collapsible card.

The collapsed header shows:

- exact command text, or exact `server.tool`;
- current state;
- elapsed duration when both event timestamps are available.

The expanded body shows labeled sections in this order:

1. command and working directory, or MCP server/tool;
2. arguments as formatted JSON;
3. output or result in a scrollable monospace block;
4. raw `provider_item` JSON;
5. Workbench call ID, provider item ID, start time, completion time, and duration.

The reducer merges start and completion events by `tool_call_id` instead of replacing the start payload with the completion payload. A completion event is nevertheless independently renderable because it repeats the action fields.

Failed, completed, running, and terminally aborted states remain visually distinct. Terminal synthesis changes only the displayed state to `已中止`; it preserves the action, arguments, and any recorded output.

## Validation and Error Handling

- Backend normalization accepts only documented command and MCP item shapes and bounded JSON values.
- The frontend recursively validates white-box JSON payloads before accepting API or SSE events.
- Invalid new payloads are ignored rather than partially rendered.
- An uncorrelated completion remains a runtime failure; transparency does not weaken correlation rules.
- Unsupported non-tool provider items remain ignored.

## Testing

### Backend

- A command start/completion pair persists exact command, cwd, output, exit code, native ID, and provider item.
- An MCP pair persists exact server, tool, arguments, result, native ID, and provider item.
- Unknown MCP tools show their exact identity.
- A completion event is self-describing.
- Correlation, size limits, confirmation handling, and malformed-record failures remain unchanged.
- Public timeline and SSE endpoints return the white-box fields unchanged for new tool events.
- `/` still serves the Agent bundle; missing-build and static-asset behavior remain unchanged.
- Every server-rendered audit page exposes the Agent navigation entry.

### Frontend

- Agent navigation exposes all six routes and marks Agent active.
- Nested JSON payload validation works for both initial timeline loads and SSE updates.
- Tool cards show exact command/MCP identity, arguments, output/result, raw item, timestamps, and duration.
- Completion merges without losing start-only data.
- Failed and terminally aborted tools retain their details.
- Historical generic events show the explicit missing-detail notice.
- Desktop and narrow viewport navigation remain usable.

### Live acceptance

After build and deployment:

1. Open `/` and switch from Agent to each original feature through the top tabs.
2. Return to Agent from an audit page.
3. Run a harmless command-oriented task and verify the exact command, cwd, output, and raw item.
4. Run a read-only MCP task and verify exact server/tool, arguments, and result.
5. Refresh during and after execution and confirm details remain identical from persistence.
6. Verify no new queued, running, waiting, failed, or stuck Workbench work remains.

## Non-Goals

- Migrating the server-rendered audit console into React.
- Using iframes for old pages.
- Reconstructing tool details that were discarded by older versions.
- Changing confirmation policy, runtime selection, task semantics, or the three-column Workbench layout.
