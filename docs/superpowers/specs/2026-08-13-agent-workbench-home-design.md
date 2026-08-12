# Agent Workbench Home Design

Date: 2026-08-13

## Goal

Replace the current History-first home page with a real Agent workbench. A user can start a durable conversation, ask the Agent to investigate or complete work, watch progress, approve high-risk actions, inspect artifacts and statistics, stop a running turn, and continue the same task later.

The experience should resemble Codex: one conversation is one continuing task, execution is visible, page refreshes do not lose work, and the final answer distinguishes completed actions from analysis or blocked work.

## Confirmed Product Decisions

- The home page runs real Agent tasks; it is not a visual wrapper around History.
- Safe, reversible work may run without per-tool confirmation.
- External sends, approvals, deletion, destructive changes, and important overwrites require an explicit confirmation in the conversation.
- One conversation remains a durable task and reuses its selected runtime session across turns.
- The selected layout is a three-column workbench: task history, conversation and progress, then current-run details.
- The existing audit and operations pages remain available. History moves from `/` to `/history`.
- The workbench depends on a provider-neutral Agent Runtime contract rather than Codex-specific process or event shapes.
- Codex is the fully supported first runtime. Claude and Pi must fit the same contract, but production adapters for them are not required in the first release.
- The interactive workbench is a React and Vite frontend served by FastAPI. Existing audit and settings pages remain server-rendered until separately migrated.
- Multica informs the runtime and interaction design, but its UI source is not copied or imported.

## Alternatives Considered

### A. Three-column Codex workbench — selected

Task history, the active conversation, and current-run details are visible together. This layout best supports long-running tasks, frequent task switching, observable execution, and artifact inspection.

### B. Minimal task launcher

The home page emphasizes a large new-task prompt and recent tasks. Full execution detail appears only after entering a task. This is simpler for occasional use but adds navigation and makes concurrent work less visible.

### C. Operations dashboard

The home page emphasizes queue counts, completion rates, and parallel Agent activity. This is useful for automation operations but weakens the direct human-Agent conversation that defines the requested experience.

## Scope

### Included in the first release

- Create, open, rename, search, and archive conversation tasks.
- Submit a message to a task and run it through a resumable provider session when the selected runtime supports resume.
- Stream persisted progress to the browser.
- Show understandable tool activity, file changes, errors, artifacts, and a final result.
- Stop a running turn without deleting its task or history.
- Continue an existing task with another user message.
- Present high-risk actions as explicit confirmation cards.
- Confirm or cancel a pending high-risk action.
- Recover truthful state after a page refresh, browser disconnect, process termination, or service restart.
- Show verified per-turn, per-task, and global operational statistics.
- Preserve all current audit, task, worker, tutorial, notification, and settings routes.

### Excluded from the first release

- Multiple human users or remote access.
- Branching or forking a conversation.
- User-created Agent templates or personas.
- Cost accounting or estimated time saved.
- A second credentials system or browser-stored credentials.
- Replacing the existing DingTalk inbound reply pipeline.
- Production-ready Claude or Pi runtime adapters. The first release includes their shared interface, capability model, and adapter contract tests only.
- Rewriting existing History, Workers, Tasks, Tutorial, Notifications, or Settings pages in React.
- Copying Multica UI, view, daemon, or backend source code.

## Architecture

### Dedicated interactive task channel

The existing reply queue represents inbound DingTalk messages and delivery semantics. Interactive user tasks have a different lifecycle, so the new workbench must not encode them as synthetic DingTalk replies.

Add dedicated persisted records for:

- Conversation tasks: title, lifecycle state, runtime kind, opaque provider session reference, timestamps, and archive state.
- Execution turns: one user request and its background execution state.
- Progress events: ordered, append-only user-visible activity for reconnect and audit.
- Artifacts: files or durable outputs produced by a turn.
- Confirmation requests: the exact proposed action, target, impact summary, status, and decision.

Names and exact columns will follow existing store conventions during implementation. Records must use stable identifiers and explicit foreign keys. User-visible content must not contain credentials, private local paths, session identifiers, or raw sensitive tool output.

### Agent Runtime boundary

The workbench does not call `CodexRunner` directly. It depends on an `AgentRuntime` contract that owns:

- Runtime capability discovery.
- Starting and resuming a provider session.
- Streaming normalized execution events.
- Stopping an owned execution.
- Returning the final result and provider session reference.
- Reconciling interrupted or uncertain work where the provider supports it.

Provider-specific implementations translate their native command, session, and event formats into this contract. `CodexRuntime` is implemented in the first release by adapting the current Codex runner and transcript logic. `ClaudeRuntime` and `PiRuntime` are represented by contract fixtures and capability declarations until their production adapters are implemented.

The abstraction has two levels:

- A protocol-family adapter implements one actual execution and event protocol, such as Codex, Claude, or Pi.
- A runtime descriptor identifies a concrete executable and its capabilities. Compatible forks or wrappers may reuse a protocol-family adapter without duplicating the workbench integration.

This follows the useful boundary demonstrated by Multica without adopting its remote server, machine-registration daemon, PostgreSQL model, or source code. The CEO Agent Service remains a single-machine FastAPI, SQLite, and launchd application.

The design reference was the Multica repository and its documented daemon/runtime model as inspected on 2026-08-13. Multica uses a common streaming backend contract and provider-specific implementations, while compatible runtime identities can reuse a protocol family. Its custom license places additional conditions on derived UI, branding, hosted services, and commercial embedding. Therefore this project uses the architectural lesson only and retains independently authored MIT-licensed product code.

Runtime capabilities are explicit rather than inferred from the provider name. Initial capability fields cover session resume, streamed text, structured tool events, image input, model selection, MCP configuration, stoppable execution, and recoverable execution. The UI hides or explains unavailable controls based on these facts.

The interactive channel reuses local SQLite storage conventions, tool permission classification, run records, and History audit links. It does not create a second tool registry, permission system, or credentials store. Each runtime uses the authenticated local CLI environment already owned by the installation user.

The current Consumer/Audit pipeline remains responsible for inbound message handling. Shared low-level execution and effect-verification components may be reused, but interactive task state must remain separate from reply task state.

### Execution lifecycle

1. The browser creates a conversation task or opens an existing one.
2. A user message creates one queued execution turn.
3. A background worker atomically claims the turn and starts or resumes its selected runtime session through `AgentRuntime`.
4. Provider-native output is normalized and appended to SQLite as ordered user-visible events.
5. Safe, reversible tool work executes under the existing permission rules.
6. A high-risk effect creates a confirmation request and moves the turn to `waiting_confirmation`.
7. Confirmation resumes the same turn and reviewed action; cancellation records the decision and lets the Agent produce an accurate result.
8. Completion stores the final result, statistics, artifacts, and terminal state.

Only one turn may execute at a time for a given conversation task. Different tasks may run concurrently within a bounded worker limit.

## Data and State Model

User-visible turn states are:

- `queued`
- `running`
- `waiting_confirmation`
- `completed`
- `stopped`
- `failed`

Task state is derived from its active or most recent turn rather than maintained as an independent competing truth.

Progress events are append-only and monotonically ordered within a turn. The normalized event vocabulary includes:

- `text_delta`
- `thinking_summary`
- `tool_started`
- `tool_completed`
- `file_changed`
- `artifact_created`
- `confirmation_required`
- `status_changed`
- `turn_completed`
- `turn_failed`

The browser receives live updates through Server-Sent Events. It supplies its last observed event identifier when reconnecting, and the server replays missed persisted events before continuing live delivery. User commands such as send, stop, confirm, and cancel remain ordinary protected JSON requests; the local one-way event stream does not require a WebSocket.

The provider's native session transcript remains the detailed runtime source when one exists. The application database stores the runtime kind, opaque provider session reference, user-visible event projections, lifecycle state, confirmation decisions, artifact references, and summary statistics needed for the workbench. Provider session references are never exposed in browser payloads.

## Safety and Confirmation

The default policy is action-oriented:

- Reads, analysis, local searches, test runs, and safe reversible changes can proceed.
- External messages, approval decisions, deletion, destructive operations, and important data overwrites must pause for confirmation.

A confirmation card must show:

- What action will occur.
- The exact human-readable target.
- The expected effect and meaningful risk.
- Confirm and cancel controls.

Confirmation authorizes only that persisted reviewed action. It does not grant blanket authority to later actions. A cancelled request remains visible in the event history.

All mutating endpoints retain the existing loopback, origin, and JSON request protections. The browser never receives credentials or raw command environments.

## Interface Design

### Frontend boundary and reusable components

The interactive workbench is a focused React and TypeScript application built with Vite. FastAPI serves its compiled static assets and owns all API, SSE, persistence, execution, and authorization behavior. The frontend is not a second backend and does not hold credentials or authoritative task state.

The workbench uses independently licensed upstream primitives where useful, such as shadcn-style components, Base UI, React Virtuoso, and a safe Markdown renderer. Dependencies are added from their original projects under compatible licenses. Multica component source, branding, and internal packages are not copied, vendored, or imported.

Product components are owned by this repository and organized around stable workbench concepts:

- `TaskList`
- `ConversationTimeline`
- `UserMessage`
- `AgentMessage`
- `ExecutionStep`
- `ConfirmationCard`
- `ArtifactList`
- `TurnInspector`
- `Composer`

Components consume normalized API resources and runtime events. They never branch on raw Codex, Claude, or Pi event formats.

### Left column: tasks

- A prominent New Task action.
- Tasks grouped by Today, Yesterday, and Earlier.
- Title, truthful state, and recent activity time.
- Search, rename, and archive controls.
- Selecting a task changes the center and right columns without losing another task's background execution.

### Center column: conversation and work

- User messages and Agent answers form the primary timeline.
- Execution progress appears as compact, collapsible steps.
- Tool events state what was attempted and the meaningful outcome; raw logs are available through existing audit detail rather than dumped into chat.
- Assistant text appears incrementally from `text_delta` events. Completed Markdown blocks retain stable identity so streaming does not rerender the entire conversation.
- File changes show the file name and change summary.
- Confirmation requests render inline in chronological order.
- The composer supports text, file attachment, Stop while running, and follow-up after a terminal result.
- The completion response lists the outcome, produced artifacts, and anything still incomplete or blocked.

### Right column: current turn

- State and elapsed runtime.
- Tool call, changed-file, artifact, and error counts.
- A concise checklist of completed, active, and pending work.
- Artifact links.
- Current permission boundary and pending confirmation requests.
- The panel is collapsible on desktop and becomes a drawer on narrow screens.

### Global navigation

The header retains the Friday brand and service health. Existing destinations remain available: History, Tasks, Workers, Notifications, Tutorial when incomplete, and Settings. `/` becomes the workbench and the former root History experience moves to `/history` with its query parameters and behavior preserved.

## API Shape

The implementation should expose a small resource-oriented interface:

- List and create conversation tasks.
- Read, rename, and archive one task.
- Create a turn for one task.
- Read a task timeline and turn details.
- Stream ordered task events.
- Stop one running turn.
- Confirm or cancel one pending action.

All writes are JSON mutations protected by the current trusted-request checks. Creating a turn is idempotent under a client-generated request identifier so retries cannot enqueue duplicate work. Stop, confirm, and cancel operations are also idempotent and validate the current persisted state.

The event endpoint uses `text/event-stream`, stable event identifiers, keepalive comments, and disabled intermediary buffering. A reconnect with `Last-Event-ID` replays persisted events after that identifier before attaching to live events.

## Recovery and Idempotency

- A browser disconnect does not stop background execution.
- Reconnect replays persisted events after the last observed event.
- Stop changes the active turn to a terminal stopped state and terminates its owned process, but preserves the task, session, events, and artifacts.
- Service startup inspects non-terminal turns. It resumes work only when the prior step is known safe to resume.
- Before recovering a turn that proposed or began an external effect, the service reconciles the actual external state. It must not blindly replay an unknown write.
- Confirmed actions use the persisted reviewed action identity, target, and parameters. Repeated confirmation requests cannot duplicate the effect.
- A failed turn remains visible and may be retried as a new turn in the same task, preserving the failed evidence.

## Error Presentation

Errors are translated into a user-understandable cause and recovery condition. The workbench distinguishes:

- Temporary dependency or network failure.
- Authentication or permission required.
- User confirmation required.
- Agent result contract failure.
- Tool or process failure.
- External result requiring reconciliation.

The interface links to History for detailed diagnostics. It does not claim completion when only analysis or attempted execution occurred.

## Statistics

Statistics use persisted, auditable facts.

Per turn:

- Elapsed runtime.
- Tool call count.
- Changed file count.
- Artifact count.
- Error count.

Per task:

- Turn count.
- Cumulative runtime.
- Last activity time.

Global:

- Tasks completed today.
- Currently running turns.
- Failed turns today.
- Turns waiting for confirmation.

The first release does not display estimated time saved or monetary cost because no approved calculation basis exists.

## Testing and Acceptance

### Store and lifecycle tests

- Task creation, rename, search, archive, and retrieval.
- Agent Runtime contract tests using the same fixture suite for start, resume, streaming events, stop, capability reporting, terminal results, and redaction.
- A complete Codex adapter test suite plus Claude and Pi contract fixtures that prove the common interface does not depend on Codex event fields.
- Atomic turn claim and one-running-turn-per-task constraint.
- Allowed state transitions and rejection of invalid transitions.
- Ordered progress replay.
- Idempotent create, stop, confirm, and cancel operations.
- Recovery after simulated process termination.
- Unknown external effect reconciliation prevents duplicate action.

### API tests

- Trusted loopback access and mutation protection.
- Create task and turn, read timeline, stream events, stop execution, and decide confirmation.
- Cross-task identifiers cannot mutate the wrong resource.
- Sensitive runtime values are absent from responses.

### Rendering and browser tests

- Three-column desktop structure and narrow-screen behavior.
- Task switching while another task remains active.
- Queued, running, waiting, completed, stopped, and failed states.
- Progress replay after reconnect.
- Incremental assistant text rendering without duplicating deltas after reconnect.
- Runtime capability differences hide or explain unsupported controls without provider-name conditionals in UI components.
- Confirmation, cancellation, stop, retry, and continuation flows.
- Artifact links and History links.

### Live acceptance

After implementation and focused tests:

1. Run the relevant full test suite and formatting checks.
2. Restart `com.ceo-agent-service.main` so Python and route changes become live.
3. Verify launchd reports a new running process.
4. Open the real root page and complete a safe local task through the workbench.
5. Refresh during execution and confirm state and events recover.
6. Exercise a test confirmation flow without causing an unauthorized external effect.
7. Verify no unresolved failed or processing backlog was introduced.

## Delivery Sequence

Implementation should proceed in vertical slices:

1. Persisted task and turn lifecycle with tests.
2. Agent Runtime contract, capability model, and normalized event vocabulary.
3. Codex runtime adapter, background execution, and recovery with tests.
4. Persisted SSE event stream and API contract with tests.
5. React and Vite component foundation plus the three-column workbench.
6. Stop, confirmation, artifacts, statistics, and responsive behavior.
7. Browser verification, documentation updates, service restart, and live readback.
