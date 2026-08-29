# Changelog

- 2026-08-29: Settings → Connectors → WeChat now embeds the reply-scope
  editor. Saved targets, target search, unsaved-change state, and explicit
  save feedback are rendered in the React page without navigating to a
  separate conversation page. Added JSON target-list and reply-scope command
  endpoints; empty WeChat display names fall back to stable target IDs.

- 2026-08-28: when all runtime snapshots reject a task, force one fresh route
  probe before returning `runtime_route_unavailable`, allowing a recovered
  provider to be used before its previous unhealthy snapshot expires.

- 2026-08-28: prevent the audit console from sending the synthetic
  `oa_pending_scan` conversation id to DingTalk's chat-opening API. Service
  tasks now expose their stored OA detail URL instead of a broken chat-jump
  action.

- 2026-08-28: preserve live OA applicant identity mappings in
  `org_user_profiles` and pass a resolved `openDingTalkId` through the
  applicant-notification context when DWS provides one, avoiding name-based
  target resolution.

- 2026-08-28: make the Consumer and Audit prompts expose only the typed wire
  contract. The application result model is an internal persistence shape and
  is no longer rendered beside the wire schema, preventing Codex from
  returning nested `error` results that the strict transport parser rejects.

- 2026-08-28: recover a typed Agent result from the bound Codex session when
  stdout closes before the terminal assistant response is streamed. Recovery
  is limited to the current attempt's session and transcript range, then uses
  the existing typed-result parser and validation; unrelated sessions and
  malformed assistant messages remain failures.

- 2026-08-27: enabled the explicitly installed WeChat reader/sender channel
  workers in the production launchd environment (`reader=1`, `sender=1`,
  `send_mode=auto`). Workers now remain present while the dedicated Reader app
  is still publishing its ready-account state, rather than being omitted for
  the entire service lifetime because of a startup race. A missing direct-chat
  target is treated as an empty context in strict queued-task reads, so an
  unavailable historical target does not create a false service error or abort
  the worker.

- 2026-08-27: removed the hardcoded `HTTP_PROXY`, `HTTPS_PROXY`, and
  `ALL_PROXY` values from the CEO launchd service, and explicitly clear both
  uppercase and lowercase proxy variables inherited from the login shell.
  Launchd no longer routes the service through the unavailable local endpoint
  at `127.0.0.1:7897`; the service uses the direct network path.

- 2026-08-27: add the `friday_runtime` Agent Runtime route. Friday owns provider,
  model, credential, and protocol selection (including MiniMax Chat Completions),
  while CEO Agent uses the Thread/turn/operation/Artifact HTTP contract. A route
  fallback stays in the same Agent run and preserves its task, generation, and
  proposal/revision relation. Document the required project/auth configuration,
  default synthetic failover E2E versus opt-in live provider E2E, and the stable
  `friday_runtime_unreachable`, `friday_runtime_auth_failed`,
  `friday_runtime_result_invalid`, `friday_runtime_failed`, and
  `friday_runtime_unavailable` failure codes.

- 2026-08-26: aligned task-agent timeout ceilings with launchd (`900s` total,
  `300s` idle) after Codex/DWS reads exceeded the previous `180s` idle bound;
  added recovery for orphaned task-agent runs whose parent input is no longer
  processing.
- 2026-08-26: deferred the typed `external_boundary` field until the autonomy
  policy is stable. Bounded external replies still state the four risk-control
  elements in natural language and remain subject to Audit's full-context model
  review, without blocking ordinary proposals on an extra structured field.

## 2026-08-26

- Require autonomous external-action replies to carry their own risk controls:
  state what the Agent may do now, the concrete risk, what the recipient must
  not do, and what still requires Derek's decision. Audit preserves and
  verifies these boundaries in the exact message body instead of accepting a
  risk only in an internal summary.

- Preserve local evidence paths only in explicit task-result `source` and
  `source_ref` fields; continue rejecting runtime paths elsewhere.
- Keep `dingokr.dingteam.com` in launchd `NO_PROXY` so live Dingteam OKR reads
  use a direct network path even when an ambient proxy is unavailable.

- Remove the Audit-side mechanical CLI contract gate. Audit now reviews the
  business proposal and target semantics; external writes still require the
  execution authorization, typed target, confirmation, and live readback
  controls at the execution boundary.

- Reopen historical direct-message deliveries only when the exact trigger is
  absent from the delivery ledger; group messages now require the canonical
  executable `payload.argv` contract and no legacy capability or payload
  normalization is performed. Add an atomic repair for terminal Audit runs that were persisted
  as `completed` with `side_effect_state=unknown` after an exact absent
  readback, rotating the task into a fresh Consumer generation without
  replaying the old external write.

- Close terminal legacy Audit runs whose trigger was later resolved by a newer
  reply, recording a no-effect supersession instead of leaving
  `side_effect_state=unknown` indefinitely.

- Close the legacy `reply_attempts` row paired with a repaired absent Audit
  run, so the History/attempt view no longer surfaces the stale
  `audit_recovery_ambiguous` decision card after the run and task are settled.

- Allow a strictly read-only Audit reconciliation to fail over from a crashed
  or unclassified provider process to another configured runtime even when the
  original unknown action has only an `item.started` marker and no receipt.

  Recovery skips already-attempted routes when an alternative is available,
  preserves the underlying runtime failure code, and ignores obsolete
  unreviewed commands while reading old session evidence instead of trapping
  historical unknown runs as `audit_recovery_result_invalid`.

- Stop treating provider-specific CLI serialization as an Audit business-rule
  failure. External writes still require the existing effect registry and
  one-shot execution authorization, while exact service-owned feedback links
  remain valid inside a DingTalk message body.

- Fix reaction-only acknowledgments being rejected by Audit when the live DWS
  schema does not expose the registered reaction command. The configured
  readback relation now supplies the reviewed write contract, preventing
  unnecessary Consumer/Audit revision runs. Bump the store schema to
  `2026-08-23.2` for the removed-runtime table migration and align Workbench
  upgrade assertions.

- Restore the configured Agent signature and counterparty feedback links on
  DingTalk messages proposed by Consumer Agent A. The service now prepares the
  executable message text after validating the Agent result and before Audit B
  reviews or persists it, so review, execution, recovery digests, and delivery
  readback all use the same final body. Confirmed delivery rows also retain the
  generated feedback token so later feedback synchronization works again.

- Close expired, effect-free Codex task-runtime leases whose parent task run is
  already terminal. Startup and routine work-item processing now recover these
  abandoned attempts, so a historical `runtime_attempt_active` cannot remain
  permanently open after its input retries under a new task-run ID. Attempts
  that crossed the external-effect boundary remain untouched for reconciliation.

- Add the Agent-run recovery lookup index used by the periodic legacy-delivery
  scan.  The service no longer repeatedly scans the large reply-attempt history
  for every completed Audit run, so an outstanding unknown Audit reconciliation
  cannot be starved by SQLite read/write contention.

- Keep a claimed `processing` task under its active Consumer worker while an
  unknown Audit effect is being reconciled.  A second Consumer loop no longer
  moves it back to `pending` and starves the recovery; true orphaned processing
  tasks remain covered by startup and stale-task recovery.

- Replay every legacy, terminal `audit_recovery_session_missing` delivery by
  cloning its completed Consumer decision into a fresh execution generation.
  The recovered Audit freezes that decision and executes its authorized external
  action with target-matched readback, without requesting a new Consumer
  proposal. Delayed text replays append `原消息生成于 YYYY-M-D HH:MM` and rotate
  an existing idempotency key so the retry is an explicit new delivery. A
  preserved revision-1/2 proposal now remains at that exact revision; a stale
  Audit revision request is retried against the same saved action, and a replay
  without a confirmed Audit effect is reopened rather than being mistaken for
  successful delivery. The delayed-message annotation now updates both
  `payload.content` and `payload.text` contracts, including their executable
  argv and expected readback text. Replay eligibility is independent of action
  type, but an existing `sent_replies` entry or safe persisted execution receipt
  for the same trigger is treated as successful delivery and is not resent.

- Automatically reopen a historical Audit failure exactly once when its
  Consumer parent has a durable proposal and the whole execution generation is
  proven effect-free. The retry reuses that immutable proposal rather than
  regenerating a decision, but still runs Audit's current authorization and
  verification gates before any write. Unknown, started, delivered, or
  receipt-bearing effects remain excluded from this path.

- Update effectful Codex command construction for Codex CLI 0.149: use its
  supported `on-failure` approval policy instead of the removed `untrusted`
  value. OAuth and service-API Audit execution can now start normally while
  retaining automatic review, controlled MCP tools, and one-shot write
  authorization.

- Give isolated Task and Meeting background decisions the same explicit
  preloaded-context boundary as Consumer and Audit turns. They no longer try
  to reopen `AGENT.md` or Skill files through generic `exec`, which had been
  correctly rejected as an unreviewed runtime effect before a decision could
  be produced.

- Permit an unknown Audit run to start its fresh, read-only reconciliation when
  the original provider attempt durably crossed the runtime effect boundary but
  crashed before emitting a normalized tool event. This closes the prior
  `unknown recovery agent run is not safely claimed` dead end without allowing
  any replay or new external write.

- Continue an unknown Audit through its fresh, isolated read-only
  reconciliation even when the interrupted run did not persist a Codex session
  ID. The original unknown effect remains unknown unless readback proves it;
  absence of an old session can no longer prematurely create
  `audit_recovery_session_missing` or authorize a write replay.

- Show a recovered terminal task on the detail page for historical
  `runtime_route_unavailable` and `runtime_unclassified` attempts. The original
  failed attempt remains immutable and visible, while its later completed task
  is shown as the authoritative recovery outcome.

- Let a healthy service-owned Agent CLI route accept an Audit turn that names
  a concrete, persisted Skill receipt. Route selection now advertises receipt
  revalidation rather than pretending every installed Skill is a static
  provider capability; the turn still verifies the exact path, name, and
  content digest before it can act.

- Accept the documented action-free WeChat no-reply result
  (`user_mode: no_reply`, `reply: null`) in strict Codex result validation.
  The parser remains closed to that exact no-side-effect shape, so a reply,
  additional fields, or another mode still requires the normal Agent envelope.

- Schedule an unknown Audit run's bounded, read-only reconciliation from its
  own persisted due time, rather than holding it behind an unrelated ordinary
  reply retry delay. Only the matching current-generation unknown Audit run
  may bypass that delay, and only after its lease is absent or expired; normal
  retries and all write recovery remain unchanged.

- Reconcile unknown DingTalk chat writes from a bounded, target-scoped message
  history read. The service now hashes the approved full message text, retains
  only matching text hashes and completeness/window facts from DWS, and
  deterministically confirms `present` without trusting the model's label.
  Missing content proves `absent` only for a complete query no wider than two
  hours that covers the original Audit start; partial, unbounded, stale, or
  mismatched reads remain `ambiguous` and never authorize a replay.

- Continue unknown Audit reconciliation with the existing capped fifteen-minute
  read-only backoff after a historical attempt or event window is exhausted.
  The worker never replays the original external action; it rolls old
  reconciliation-event detail forward instead of permanently suspending the
  target-matched confirmation loop.

- Avoid full DWS-schema preloading during Agent runtime health checks. Route
  selection now declares the service-owned `agent_cli` transport directly;
  each actual command retains its existing metadata and read/write validation.
  Installed Skills are read through the existing authorized Skill path and
  receipt checks instead of a static route capability list.
- Close an active runtime-attempt record when startup recovery has already
  proved its parent Agent run failed before any external effect. Unknown or
  effectful runs remain untouched for reconciliation.

- Require candidate and hiring reviews to read Xiaoqing's authoritative job,
  resume, interview, and assessment context before escalating the remaining
  sensitive hiring decision to Derek.
- Inject the validated service MCP manifest into Consumer and Audit Codex
  commands while retaining the authenticated Codex OAuth session, and disable
  only user-configured servers not owned by that manifest.
- Keep Xiaoqing in the generated service MCP manifest through its stable OAuth
  endpoint, so candidate and resume requests can read the authoritative
  interview package instead of being escalated because an optional local
  transport command was absent.

- Restore SQLite WAL mode on every service process startup even when the schema
  is already current, preventing a database restored in rollback-journal mode
  from serializing readers behind long-running writers.

- Retry isolated SQLite lock contention in DingTalk reads, meeting consumption,
  and follow-up delivery; record an error only when the same loop remains locked
  for three consecutive attempts.

- Retry transient SQLite contention in WeChat loops and report it only when the lock persists for three consecutive iterations.
- Keep valid unread DingTalk messages when DWS returns an unsupported row in the same unread window, without promoting older read-overlap rows.

- Add an opt-in, synthetic-only live verification suite and staged operator
  runbook for Codex OAuth-to-API failover. Runtime probes and pauses are
  route-scoped; the API credential is process-environment-only and is checked
  against captured output, SQLite, and rendered History. Failover remains
  bounded to proven read-only attempts, while unknown or started writes stop
  for reconciliation instead of switching providers.

- Add a guarded failed-task settlement path for stale or superseded replies. It records an explicit skipped attempt and refuses to close tasks with active runs, delivery receipts, or external side effects, preventing unsafe replay without hiding the audit trail.

- Isolate background Consumer and Audit Codex turns from desktop plugins,
  browser features, session memory, and unrelated user MCP servers while
  retaining the configured model provider and existing Codex login.
- Treat the canonical shared rules embedded in role prompts as already read, so
  background agents cannot fail by reopening `AGENT.md` through native shell.
- Anchor task-agent follow-up rescheduling to the current execution time so a
  delayed or retried work-summary input cannot emit an already-expired due time.

- Reconcile unknown direct follow-up sends with a complete, exact DingTalk
  conversation readback. A matching outbound message now finalizes delivery;
  a complete absence releases the same revision for an idempotent retry, while
  partial or failed reads remain unknown and are never replayed.

- Keep Codex capacity exhaustion recoverable across days. Reply, work-summary,
  and meeting queues no longer spend their business retry limit while waiting
  for capacity; probes back off from 30 minutes to a four-hour ceiling, retain
  the capacity incident through generic process disconnects, and reset after a
  successful Codex turn.

- Resolve the weekly OKR recipient through DingTalk's paginated group search
  instead of the recent-conversation feed. Exact-name uniqueness is now checked
  across every search page, so an inactive but valid management group is not
  misreported as missing.

- Allow a complete weekly OKR manager review up to 15 minutes. The structured
  Codex CLI emits only on completion, so its no-output deadline matches the
  total limit instead of terminating a valid evidence-heavy review early.

- Recover a missed weekly OKR report after the scheduled Sunday window. The
  retry retains the missed Sunday as the report date and week end, rather than
  publishing a partial report for the recovery day.

- Associate work-item execution errors with their exact input and close them
  once that input reaches a terminal state, instead of retaining a generic
  four-hour quality-gate failure after verified recovery.
- Settle legacy unknown Audit runs when an exact channel, conversation, and
  trigger match already has a persisted direct-delivery receipt. Completed
  tasks no longer retain an unresolvable unknown-side-effect warning.
- Atomically finish a pending unknown Audit when its delivery ledger names the
  exact Agent run and operation, while rejecting older-generation or unrelated
  sends. Bound Agent CLI command output below the Codex MCP transport limit so
  oversized reads return a valid `agent_cli_output_limit_exceeded` receipt
  instead of truncated JSON that appears to have no receipt; operators may then
  reopen an effect-free failed Audit turn without rotating or replaying its
  proposal.
- Recognize `doc info` on the same document node as the live readback for a
  controlled `doc +move`, so a completed document move can reconcile from its
  persisted digest instead of exhausting retries as unrelated evidence.
- Fix meeting-alignment jobs created during the settling window so they become
  claimable when their eligibility time arrives, even if discovery does not
  encounter the same meeting again.

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

- Keep each approval History card anchored to its newest persisted Attempt,
  ordered by creation time and ID, so filtering, pagination, attention, and
  recovery reflect the current workflow state. Resolve the separate business
  outcome from confirmed evidence across every Attempt in that OA process,
  without N+1 reads or trusting generated action labels. Add a system dark-mode
  palette for History text, approval pills, and filter controls.
- Require both Consumer and Audit `needs_human` results to include two to four
  actionable choices. Persist those choices on the History item itself so
  post-completion conflicts and non-Agent recovery paths remain actionable even
  when their original Agent run was already successful; missing legacy choices
  fall back to safe re-verification or stop-without-action options.
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
