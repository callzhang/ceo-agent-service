# Changelog

## Unreleased

### Runtime deployment

- Persist the production release checkout as `CEO_SERVICE_ROOT` in the main
  LaunchAgent. Reloading the job can no longer silently fall back to the
  developer checkout after inherited shell variables are cleared.
- Recover terminal native-Codex authentication failures only after a fresh
  `codex login status` succeeds, and only when the original task has no delivery
  ledger, unknown effect, execution receipt, or started WeChat delivery. The
  original message is requeued without replaying a prior external action.
- Keep group sends out of the direct-message recovery path. A group-send ledger
  can no longer close an unknown Audit run through a direct-delivery shortcut.
- Allow a suspended Audit run to be closed from its requeued `pending` task
  after a structured live reconciliation confirms the original effect. The
  closure remains generation-bound and rejects active work.

### Weekly OKR reporting

- Bound each manager score to the configured deadline or five minutes,
  whichever is sooner, and stop waiting after 90 seconds without Codex
  output. A failed score now cancels queued managers instead of allowing the
  executor to continue launching the full roster; valid per-manager caches
  remain available for the next deduplicated run.
- Run scoring with the dedicated weekly-OKR prompt and schema only. It no
  longer inherits the interactive message-consumer contract, which requires
  unrelated memory and Audit steps before it can return a report.
- Use an isolated Codex CLI configuration for scoring, so an unavailable MCP
  plugin cannot prevent a read-only report from returning its final JSON.

### Material reading and local parsing

- Treat URL-only Markdown images, including presentation avatars in quoted or
  coalesced messages, as unreadable text metadata rather than required image
  attachments. Native DingTalk media and download-code images still require an
  authenticated local file and fail closed when one is unavailable.

### Audit visibility

- Show an active work item's persisted summary in Workers Attention instead of
  its opaque internal source reference, while retaining the source type and
  underlying error state for diagnosis.
- Fall back to an active work item's persisted title before its source
  reference, so queued AI minutes never expose raw structured payloads in
  Workers Attention.
- Include failed WeChat deliveries in Workers Attention. Pre-send failures now
  state that no message left the service and require a fresh target check,
  rather than exposing only an internal error code.

### Task-agent recovery

- Align task-agent follow-up prompts with the persisted participant contract:
  every draft now requires a non-empty set of live-resolved stable user IDs,
  and unsupported drafts are omitted instead of failing the complete work
  summary before any external write. Owner evidence now repeats the assigned
  stable ID and name, so valid first-pass decisions do not need a broad repair
  read merely to satisfy persisted identity validation. The prompt names the
  project, TODO, and follow-up evidence paths explicitly, rather than relying
  on the model to generalize that contract.
- Allow a required live DWS read up to three minutes without Codex JSONL output
  while retaining the five-minute total task-agent cap. This avoids falsely
  terminating material-backed decisions during a slow read.
- Keep task-agent output on its native `TaskAgentDecision` contract instead of
  injecting the message-consumer envelope. Bound a stalled task run to five
  minutes total or 90 seconds without output, and stop automatic requeueing
  after the configured transient retry limit so unavailable Codex runs cannot
  consume the work queue indefinitely.
- Preserve the initial task-agent receipt when a proposed owner lacks a stable
  identity, then make one bounded repair pass using live directory evidence.
  If the owner remains unresolved, retain only an unassigned project update and
  omit owner-dependent TODOs and follow-ups instead of failing the work item or
  sending a message.
- Require every non-discard task decision to call `memory_recall` when it is
  available, while retaining live DWS reads as the proof of current state.
- Define the task-agent's memory-tool-unavailable receipt value in the prompt,
  so a completed tool-discovery check can continue with recorded live evidence
  instead of failing on an undocumented output-contract mismatch.
- Route a stable owner whose evidence omits the same identity fields through
  the bounded owner-repair decision, rather than failing before persistence;
  unrecoverable owners still remove owner-dependent TODOs and follow-ups.

### Test reliability

- Isolate CLI default and task-maintenance tests from developer environment
  variables and the current calendar date, so local full-suite results do not
  change when a batch limit is configured or the weekly OKR window is open.

### Audit reconciliation

- Treat `chat-id` and the read-side conversation identifiers as the same
  DingTalk conversation during controlled Audit readback matching. Event-limited
  unknown runs now receive one evidence-only recovery pass before suspension,
  preventing a completed multi-action approval from accumulating retries when
  its source-chat readback uses a different canonical identifier label.

- Redact every failed outbound chat-send command before persistence, and record
  direct-recipient rejection as an explicit no-delivery terminal result so an
  inactive recipient is not retried or reported as an ambiguous service error.

- Close delivery-ledger-backed native `chat +dm` Audit recoveries without a
  duplicate external send, and terminate authorization waits immediately when
  DingTalk supplies no actionable scope to request. Authorization waits with a
  valid scope retain their retry accounting and bounded retry limit.
- Restrict that recovery to the original single-chat recipient and reject
  duplicate controlled writes in one Audit turn. Ordinary transient deferrals
  still refund their claim attempt; authorization deferrals do not.

- Close an unknown Audit run directly when every approved controlled write has
  a completed lifecycle and a target-matched post-write readback. This avoids
  indefinite reconciliation caused by a malformed recovery summary while
  retaining command, target, and readback checks before any terminal state.
- Register DingTalk reply and TODO-update readback pairs in the effect contract,
  so verified `messages-reply` and `todo task update` operations can close a
  recovery without weakening target matching for other commands.
- Require Consumer A to resolve low-consequence operating choices from focused
  memory context, the applicable Skills, and live evidence before escalating.
  The Consumer session contract now fingerprints this instruction so prior
  sessions cannot silently continue under the old escalation rule.
- Retry an Agent-run write transaction when SQLite reports a short-lived lock,
  so lease heartbeats do not interrupt a no-effect Audit turn during concurrent
  queue activity.
- Treat a bounded internal preparation action for an already-confirmed event or
  tracked commitment as low consequence when it cannot alter the work's scope,
  timing, owner, or business meaning.
- Make Audit B honor the authorized minimum reversible path for low-consequence
  choices, so it does not require an earlier message that repeats the same
  routine decision.
- Fix audit validation so a valid parsed native command is normalized to its
  controlled CLI contract instead of being rejected because the Consumer used
  a non-canonical capability label.
- Include every current `needs_human` trigger in hourly quality attention, so
  the repair heartbeat must inspect and explain its concrete action instead of
  reporting only an aggregate count.

- Registered the current direct-message send/read pair for audit reconciliation,
  while retaining target-scoped matching so a read from another recipient cannot
  confirm or suppress a pending delivery.
- Applied the same delivery-ledger recovery to persisted legacy direct-message
  actions, so a missing local delivery record requeues a new generation instead
  of attempting an unnecessary external reconciliation.
- Restored omitted direct-delivery ledger rows from the original Audit run's
  validated controlled receipt, while keeping recovery-only authorization
  checks restricted to recovery execution.
- Finalize an unknown Audit run from that restored ledger when it contains one
  matching direct-message action, avoiding a second model reconciliation or
  duplicate delivery; multi-action and non-direct work still requires normal
  reconciliation evidence.

### Material reading and local parsing

- Made the default downloaded-material reader detect OOXML workbooks by their
  file content, so extensionless downloads no longer fail after being treated
  as UTF-8 text.
- Added bounded PPTX text previews and fixed Audit parent validation after a
  retrying Consumer run, so a successful retry can continue into Audit.

### Skill-first Agent runtime

- Added seven distributable CEO business Skills for message triage, calendar invitations, document review,
  meeting work, mail review, personnel communication, and the combined task extraction/follow-up lifecycle.
- Consumer A now discovers and reads business and operation Skills dynamically. Audit B rereads the same
  Skills from verified completed tool-event receipts before reviewing or executing a candidate.
- The exact installed business-Skill catalog is now part of Consumer A's developer-level protocol, and a
  Consumer session rotates when that protocol changes. This prevents a turn from returning before any
  business Skill was read without introducing a service-side domain router.
- Corrected Consumer/Audit instructions to use the current nested proposal, feedback, external-result, and
  reconciliation fields while retaining the wire contract's top-level error fields. Audit results now fail
  closed when the required Consumer Skill receipts were not reread.
- Removed business keyword routing and service-side material interpretation from the documented architecture.
  The service transports references and exact read commands; Agents decide what evidence to read and how it
  affects the task.
- Kept OA, interview, and OKR logic in their existing specialist Skills instead of copying those rules into
  the CEO general prompt.
- Kept detailed Skill/tool audit in native Codex session JSONL and existing run/attempt/receipt persistence;
  no parallel Skill audit database is introduced.
- Added ownership-safe installation of the seven managed Skills under `~/.agents/skills`. User Skills under
  the same root are preserved, and no user Skills are installed under `~/.codex/skills`.
- Updated the native Skill-runtime fixtures to advertise their two MCP tools as read-only and non-destructive,
  and added live coverage for a three-message mail thread plus a full Consumer/Audit calendar dry-run.
- Expanded the sanitized Skill-runtime comparison matrix from 11 to 19 cases. The added cases cover
  authorized personnel delivery, the create/follow-up/complete work lifecycle, OA approve/return/reject
  decisions with applicant notification, and read-only recovery when an external side effect is unknown.
