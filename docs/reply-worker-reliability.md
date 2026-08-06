# Reply worker reliability

## Failure visibility

`produce-once` and `consume-once` record top-level failures in the `errors` table
and raise a local macOS notification before exiting non-zero. Launchd keeps one
main service alive; that process runs the audit web server, producer loop, and
consumer loop. If any component stops unexpectedly, the process exits so launchd
can restart the whole service.

Per-conversation read failures are recorded and notified without blocking other
conversations in the same producer pass.

Service startup recovers durable work before any producer or consumer thread
starts. Claimed reply tasks return to `pending`, work-summary inputs return to
`pending`, meeting analysis jobs return to `retry`, and claimed meeting delivery
jobs are unlocked. Recovery subtracts the interrupted claim from each queue's
attempt counter because process termination is not a business execution failure.
Persisted Direct Agent run events and verified terminal receipts remain
unchanged, so recovery can reconcile completed external actions without sending
them again.

External dependencies use two retry levels. The call boundary performs a small
number of immediate retries, then raises a typed external-dependency failure
instead of flattening it into a generic runtime error. The reply, work-summary,
and meeting queues preserve that type and schedule a later retry without
exhausting the business task's attempt limit. This applies to Codex runners and
to DWS operations that the client has classified as retryable.

Authorization failures remain in the authorization recovery path. Local input,
target-binding, privacy, schema, and business validation failures remain
terminal or explicitly blocked. An external write with an unknown result is
never replayed merely because its transport failed; the service must reconcile
the existing operation first so retries cannot duplicate a message, approval,
or other visible side effect.

If a local result-envelope validation fails after an externally visible action,
the failed run is not replayed. After read-back confirms the action, the
audited manual-reconciliation command may transition that exact generation to
`completed` and create a reconciliation attempt. It accepts this path only for
`confirmed_occurred`; failed runs without verified evidence remain failed.

An idempotent DWS message send that exits without a structured error result is
also treated as a transient dependency failure. It is retried at the call
boundary, then deferred with the same UUID for later recovery. A structured
business rejection remains terminal; the service does not turn known validation
or permission failures into an endless retry.

Before starting an agent, the channel gate checks structured CLI auth status and
one authenticated live probe. Only an explicit `needs_login` state may ask the
login coordinator to launch the corresponding CLI login once. An unavailable
status check, network failure, or command error remains a dependency failure and
must not be guessed to mean that login is required.

Short DWS read-path token verification failures are treated like transient read
failures. The DWS client retries read-only commands such as message reads and
contact lookups once; if a read still fails with `TOKEN_VERIFIED_FAILED`, the
worker stores the rolling count under `service_state.dws_transient_error_count:*`
and only writes an `errors` row when the threshold is reached. Message sends,
calendar responses, approvals, and other write actions are not retried through
this path.

DWS may also return `PAT_AUTH_CALL_FAILED` for a temporary authenticated backend
failure even while the local profile remains valid. AI-minutes list and get
commands retry this code at the call boundary because they are reads and have no
external side effect. Approval actions and other writes do not use this retry
path, so an ambiguous write result cannot be duplicated.

DWS may return `PREPARE_CALL_TOOL_ERROR` while preparing an otherwise valid
authenticated read. Known message, calendar, contact, and AI-minutes read
commands retry this code at the call boundary. Sends, approvals, and other
mutations remain excluded because repeating an unknown write result could create
a duplicate visible action.

DWS message reads also treat server error codes ending in `_INVOKE_FAILED` as
transient dependency failures. This covers infrastructure-side validation or
gateway invocation changes without coupling recovery to one exact DWS error
name. The rule applies only to the existing message-read command allowlist. It
performs bounded immediate retries and preserves the worker's delayed transient
recovery state after exhaustion; OA actions, sends, and other mutations are not
replayed by this rule.

DWS JSON commands may print progress text before their final structured result.
The client uses `robust-json-parser` only to locate complete JSON objects or
arrays in that mixed stdout. It disables partial extraction, requires the chosen
JSON value to end at the end of stdout, and parses the extracted text again with
the standard JSON parser. It never repairs truncated or malformed DWS action
results.

All synchronous DWS CLI executions share one process-wide gate. The service has
separate producer, consumer, meeting, and task-maintenance threads, but only one
of them may launch and wait for a DWS child process at a time. This keeps a slow
macOS code-signing assessment from turning independent DWS retries into a burst
of concurrent Gatekeeper requests. The gate covers JSON, text, cache-refresh,
and resource-download commands; it does not change each command's timeout or
retry policy. Process starts are also paced at least one second apart by default;
`CEO_DWS_PROCESS_MIN_INTERVAL_SECONDS` may override that interval, or set it to
zero when pacing is intentionally disabled.

For single-chat recent-context reads, a missing direct-user mapping is treated as
unavailable context rather than a business failure. The worker returns an empty
recent-context list for that read and does not write an `errors` row; unread
message reads still surface failures because they can affect trigger discovery.

Local notifications first try the browser bridge exposed by the audit web
service. Keep any `http://127.0.0.1:8765/` audit page open in Chrome after
granting notification permission; the page keeps an SSE connection to 8765 and
displays incoming worker notifications with Chrome's Web Notification API.
`http://127.0.0.1:8765/notifications` remains available as a hidden
authorization and diagnostics page, but it is not required for normal operation.
Clicking the Chrome notification calls the local URL
`http://127.0.0.1:8765/open-dingtalk?conversation_id=...` in the background; the
audit web service then opens a DingTalk `page/link` bridge page inside the
desktop client. That bridge calls the current DingTalk JSAPI
`dd.openChatByConversationId` with the message's `openConversationId`. The click
handler does not open a new browser tab.

If no browser notification page is connected, the worker falls back to an
AppleScript `display notification` call. That fallback is only a visibility path:
it does not bind a click action to DingTalk, so conversation jump remains
available through the browser bridge when an audit page is open.

When `terminal-notifier` is available, its click action sends a `POST` request
to the same local `/open-dingtalk` bridge. The bridge owns the desktop
`dingtalk://` launch, so terminal-notifier and browser notifications use the
same conversation-opening route.

Handoff notifications use DING first so they can reach the operator inside
DingTalk. Every service-generated handoff alert begins with the exact
`【CEO Agent 转人工通知】` protocol marker. If that alert later appears in the
operator's single-chat inbox, the producer marks it seen and removes it before
candidate routing, so the service cannot treat its own alert as a new user
trigger or recursively include earlier handoff text.

If DING is unavailable, for example because the DING server quota is exhausted,
the worker tries the configured robot direct-message path and then falls back to
the local notification path instead of failing the reply attempt. The original
chat acknowledgement remains the delivery source of truth; these operator
alerts do not become reply candidates.

## No-reply side effects

A Direct Agent result may use `no_reply` after completing a non-chat action such
as a reaction, calendar/OA operation, or `memory_write`. The action must still
have a completed effectful tool event or an execution receipt. `no_reply` cannot
be combined with another formal chat reply, handoff, blocked, or stop-with-error
outcome because those results conflict at the conversation level.

## Human decisions

`needs_human` is a completed Direct Agent result that waits for a real operator
choice. It is not a `blocked` execution failure. The Direct Agent may use it
only when evidence leaves materially different actions or requires personal
judgment; established facts, targets, and explicit manual rerun choices must be
executed without asking the same question again.

The worker sends a local decision notification. Its audit-attempt page presents
three choices: continue using the available facts, ask one specific clarifying
question, or supply a custom instruction. Selecting a choice creates a durable
manual rerun and marks the source attempt `decision_selected`. The worker then
executes, verifies, and publishes through its normal delivery path. Because the
choice is stored before processing, launchd restart recovery resumes the pending
rerun without requiring the operator to select again.

## Scheduled follow-up delivery

Due follow-ups run in their own maintenance loop every 60 seconds and deliver
independent batches of up to 50. This loop is separate from daily task-source
scans, so a slow scan cannot delay an already due message. Its delivery budget
is deliberately separate from `CEO_MAX_BATCHES`, which only limits new
agent-task discovery. Delivery remains ordered, durable, and idempotent:
working-hour, completion, daily-cap, sensitive-routing, and existing-send checks
still run before each send, and a restart resumes from the persisted draft state.

## Memory MCP inheritance

The Direct Agent inherits the installed Codex `memory_connector` MCP
configuration and plugin login state. The reply worker does not create or
refresh a separate Memory OAuth client. Deferred `tool_search` discovery is a
read-only event; a claimed successful `memory_write` still requires its own
completed tool event and receipt.

Native Codex plugin OAuth credentials are stored outside `config.toml`. A
configured Memory MCP URL without a plaintext header or bearer environment
variable is therefore valid and must not be reported as missing transferable
authentication. The child `codex exec` process inherits the plugin login and
performs the authenticated MCP call itself.

If Memory is unavailable before a write starts, the run may fail or request
human action according to the returned error. If a write starts but its result
cannot be confirmed, the run becomes `unknown` and recovery is read-only. The
service does not replay the write from a cached action list.

## DWS upgrade check

The producer checks for `dws` updates inside the normal CEO system pass, once per
local day. It uses the existing producer loop cadence instead of adding a
separate system-level timer. If an update is available, the producer runs the
upgrade before reading DingTalk messages. Upgrade check failures are stored in
`service_state.dws_upgrade_check_result` so GitHub rate limits or short network
outages do not create CEO business errors. If an update is available but the
upgrade command fails, the failure is still recorded locally and notified. Either
case does not block message discovery for that producer pass.

## Org cache refresh

The producer refreshes the DingTalk organization cache inside the normal CEO
system pass when the last successful refresh is older than seven local days. The
refresh shares the same service state as the manual `refresh-org-cache` command,
so a manual refresh prevents an immediate duplicate refresh from the next
producer pass. Refresh failures are recorded locally and notified, but they do
not block message discovery for that producer pass.

## Task source maintenance

Task summary maintenance runs inside the main launchd service. It has three
independent steps:

- `scan-task-sources` finds new AI minutes and new Markdown/text files under the
  configured `CEO_WORKSPACE`.
- `process-work-items` lets the task agent merge Work Items into existing
  projects or create new projects.
- `process-follow-ups` processes due owner follow-up drafts.

The service consumes pending Work Items every
`CEO_TASK_WORK_ITEM_INTERVAL_SECONDS` seconds, defaulting to 60 seconds. It runs
the AI minutes, local file, and follow-up pass every
`CEO_TASK_DAILY_INTERVAL_SECONDS` seconds, defaulting to 86400 seconds. The
manual `daily-task-maintenance` command runs the same steps once and is intended
for backfills, smoke checks, and debugging.

AI minutes and local file cursors are kept in `daily_scan_state`, so scanner
failures are visible without forgetting the last successful cursor. Local file
identity includes path, size, mtime, and content hash so same-mtime edits can
still be reprocessed.

Follow-up dispatch is guarded separately from draft generation. Dry-run records
the draft state without sending. Live CLI sends require the same
`CEO_LIVE_SEND_BLOCKERS_ACCEPTED=1` override as normal DingTalk replies.

## CLI credential ownership

LaunchAgents run under the current macOS user and reuse the normal credential
stores maintained by DWS and Lark CLI. The service does not export, import,
copy, decrypt, restore, or maintain a second set of CLI credentials.

The channel gate performs structured status checks and a live authenticated
probe before starting the Direct Agent. The agent is forbidden from running
auth login, reset, or logout commands. When the gate explicitly reports
`needs_login`, the login coordinator may launch one interactive CLI login and
records the coordinator state in SQLite; concurrent processes and repeated
checks within the suppression window cannot open another login flow. Network
errors and unreadable auth status never trigger login.

The Config channel page shows the current gate state, the status and live-probe
commands that were executed, the most recent successful gate time, and whether
an interactive login request is active or suppressed. It deliberately omits
process IDs, session IDs, tokens, credential paths, and raw signed URLs.

History is outcome-oriented. Each row links to its complete audit detail and
shows the channel, conversation, sender, trigger, generated or sent text,
terminal state, and the safe Direct Agent summary. Planner labels, action
indexes, dependency graphs, confidence scores, and target-normalization details
are not user-facing runtime concepts.

The History homepage reads its count, rows, delivery state, feedback state, and
chart from one SQLite read-only snapshot. This keeps a single render internally
consistent and avoids accumulating lock waits while worker threads persist new
events.

## Processing acknowledgement

The worker no longer sends `收到，我正在处理（by 分身）` before a final reply. Final
reply delivery is usually close enough that the extra acknowledgement adds noise.
Historical acknowledgement messages are still recognized and filtered from prompt
context and unanswered-mention checks, so earlier processing messages do not hide
messages that still need a real reply.

## Reply quote fallback

Final replies include a short text quote built from the trigger message. Compact
assistant mentions such as `@明哥分身，请...` are stripped only up to the first
message punctuation, so the remaining request text is preserved in the quote
instead of producing an empty quote. If a non-text message has no readable text,
the quote uses a type-specific placeholder such as `[图片]`; if no useful context
can be inferred, the quote is omitted instead of falling back to `原消息`.

## Image attachments

When a message references an image, the worker attempts to download it before
calling Codex and passes successfully downloaded files through `image_paths`. If
DWS cannot return a usable image URL or the binary download fails, the worker
records an `image_download` error and still calls Codex. The prompt includes a
`图片读取状态` section with the failed image details and explicitly tells Codex not
to guess visual content when the question depends on the missing image.

## Material Reading Boundary

The worker does not pre-read DingTalk documents, AI minutes, or ordinary files
for ordinary reply decisions. It extracts material references and injects them
into the CEO agent prompt. The agent decides whether the message can be answered
from text context, whether to read one or more materials through DWS, and how to
respond after reading.

The worker still preprocesses:

- Calendar invites, because calendar responses and calendar-context failures are
  part of the service state machine.
- Images, because Codex receives local image paths rather than DingTalk media
  IDs.

For OA work, the service passes only the original process/task identifiers,
links, known form/card fields, and exact DWS read commands. The Direct Agent
performs live reads for approval detail, current-user task ownership, comments,
folders, documents, sheets, and attachments, then decides whether the evidence
is sufficient. The service does not recover a target by applicant/title match,
choose among ambiguous candidates, pre-read business content, or substitute a
same-named document. Approval and comment actions are executed by the agent and
must produce completed events or receipts.

## Mail review and reply

A quoted DingTalk mail card is treated as a locator, not as the complete mail
body. The decision agent lists the principal's mailboxes, searches by the quoted
sender and subject, reads the full original message, and opens linked review
materials before making a decision. If a required result or attachment is still
missing, the agent asks for that specific material instead of approving from the
preview.

When the trigger explicitly authorizes a reply and review is complete, the
Direct Agent replies through DWS. The mail tool event and receipt are persisted
before any DingTalk acknowledgement is considered complete. A retry reconciles
the existing mail operation instead of blindly sending it again.

If a historical calendar event id can no longer be read from DWS, the DWS client
returns no event detail instead of failing the whole producer pass. The worker
can then use an existing terminal attempt or the normal calendar-detail
unreadable branch without turning a stale event into recurring producer errors.

For DingTalk documents, AI minutes, and ordinary files, agent-side DWS calls must
be visible in `audit_tool_events_json`. Permission failures are missing material
context for the agent to reason about, not ordinary worker failures, unless the
agent cannot answer without the material.

## Mentioned arrangements

When a human mentions the configured principal in a group and shares an
arrangement, process, or decision that needs the principal to participate or
confirm, the agent should treat it as
reply-worthy even if the message is phrased as a statement rather than a
question. It should only skip when the later context shows the principal already
confirmed the arrangement.

Mention discovery starts from the recent global configured mention feed, not only from the
current unread conversation list. A mentioned group can therefore be processed
after the user opens the conversation and clears the unread badge. Later context
from the same conversation is used to decide whether the principal already gave a real
reply; rendered files, images, cards, calendar invites, and processing
acknowledgements do not count as a real reply.

Fast-path unread discovery has a short human-reply backoff before the consumer
can process a reply task. When the producer first sees an unread conversation,
it reads the unread messages, records the trigger in `reply_tasks` as `pending`,
and sets the task's availability to `FAST_PATH_UNREAD_BACKOFF` later. This makes
the pending item visible in history immediately without letting the consumer
reply while the principal may still be handling it. After the window, if the
original trigger was recalled or is no longer returned by DWS `list-by-ids`, the
task is completed and a `skipped` no-reply attempt is recorded. If the trigger is
still active but later context shows the principal already replied after it, the
task is also skipped. Otherwise the consumer can claim the task and move it to
`processing`, even if the unread badge has already cleared.

OA follow-ups preserve recent messages as raw conversation context. The service
does not inherit or bind an approval target from an earlier card. It passes each
available card URL, process/task identifier, and exact live-read command to the
Direct Agent. The Agent resolves the current task through DWS, verifies current
ownership, and stops without an approval write when the evidence is ambiguous,
completed, or belongs to another user.

Sender enrichment may replace `open_dingtalk_id` with `user_id` before Direct
Agent context is rendered, but it does not choose or validate an OA target.

## Consumer retry behavior

Reply tasks move from `pending` to `processing` when claimed. If task processing
raises an exception, the consumer records a retry error and moves the task back
to `pending` until the task reaches the maximum attempt count. Intermediate
attempts do not send failure notifications; the queue owns failure reporting so
transient Codex startup, model refresh, or provider errors cannot produce a
false alarm before a later attempt succeeds. The default maximum is three
claimed attempts.

The Direct Agent does not force Codex's optional reasoning-summary setting.
The summary capability is presentation metadata rather than a task requirement;
leaving it to the installed CLI avoids a model-cache schema update preventing a
durable task from starting.

Direct Agent and reconciliation invocations ignore personal Codex user config.
They receive the service-selected model and reviewed MCP configuration explicitly,
so interactive plugins or UI-only settings cannot prevent durable queue recovery.
They do not add disabled-server entries for personal MCPs: with user config
ignored, those entries would create incomplete transports instead of isolating
the service.

Delivery failures for an otherwise sendable reply are treated as task processing
failures after the reply attempt has recorded the failed send. This keeps the
original message retryable instead of completing the task with a failed attempt.

When the maximum is reached, the task is marked `failed`, the final error is
recorded, and a local notification is sent. A Codex runtime failure
(`codex_process_failed`, `codex_process_timeout`, or `codex_stream_invalid`)
with no external side effect receives one additional recovery claim after the
ordinary business-attempt limit. The persisted Agent run and generation make
that claim restart-resumable. If the additional claim also fails, the task
becomes terminal and sends the normal failure notification instead of remaining
in a permanent retry loop.

Codex CLI login failures, explicit selected-provider authentication failures,
and Codex Responses API transport failures are classified as wait states rather
than ordinary processing failures. For the built-in `openai` provider, a
Responses request that is missing its bearer/basic header is treated as a
transient native Codex authentication-propagation failure: the worker records
`codex_provider_unavailable`, sends a provider-recovery notification, and retries
after the normal short backoff. An invalid API key, a missing header from an
explicit non-OpenAI provider, or a rejected ChatGPT session remains an actual
authorization wait. Both paths move the task back to `pending` without burning
the business attempt budget. Work-summary inputs use the same classification and
remain pending after the normal transient retry limit when the blocker is Codex
authorization or provider availability.
If Codex returns a structured `stop_with_error` for one of these wait states,
the reply attempt is recorded as `blocked` with the same sanitized reason rather
than as a failed send.

If the agent can prove that required material or a required tool result is
unavailable and continuing would guess at the answer, it must return
`stop_with_error` with a reason starting `critical_info_unavailable:`. The worker
treats that prefix as a non-retryable task failure: it records the failed
attempt, marks the queued `reply_tasks` row `failed`, and sends the normal
`CEO task failed` notification for human handling. Tool calls that are merely
discouraged, such as retrying a DWS detail command after an OpenAPI recovery,
stay as prompt guidance and audit evidence; they are not blocked by the runner.

The Xiaoqing interview-call guard is classified from the current OA trigger and
the current approval detail only. Conversation history remains available to the
approval agent as supporting context, but unrelated historical hiring messages
must not turn a contract or other non-hiring approval into a Xiaoqing-dependent
task.

Processing tasks older than the stale-task threshold return to `pending` only
when their same-generation Direct Agent run has no live lease. The initial
lease covers the maximum process duration, the idle-read window, and a short
completion buffer; every valid streaming progress event renews it. A task with
a live lease remains `processing`, even when its original queue lock is older
than the stale threshold. This prevents a slow active run from being resumed
concurrently; an expired lease still allows an interrupted task to recover and
emits the local retry notification.
