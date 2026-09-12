# Changelog

- 2026-09-11: a runtime health probe no longer requires the provider's reply to
  be nothing but the canonical object. MiniMax-M3 returns it inside a
  `<think>...</think>` block, so `friday_runtime` was marked unhealthy and
  skipped even though the business path would have succeeded on the same reply -
  `parse_typed_agent_result` reads through fences, prose and reasoning blocks
  via `_first_balanced_json_object`. A probe that gates a route must not be
  stricter than the work it gates, so all three probe checks (the Friday result,
  `_parse_probe_result`, and the Claude event grammar) now share that extraction
  through the new `extract_first_json_object`. The extracted object still has to
  be exactly `{"ok":true}`, and the Claude grammar still requires the terminal
  result to equal the assistant message.

- 2026-09-11: email unsubscribe tasks with durable browser steps but no
  terminal receipt now stop at an explicit `needs_human` decision instead of
  remaining failed or being replayed. The choices are bounded to read-only
  reconciliation or stopping without another browser write.

- 2026-09-11: runtime/provider `confirmation_required` failures are now kept as
  explicit technical failures instead of being misclassified as `needs_human`;
  current unresolved `needs_human` reply attempts are also included in the
  Attention projection so the UI and API cannot hide genuine management
  decisions.

- 2026-09-11: calendar-backed meeting summaries now keep the calendar creator as
  the stable organizer identity when the Minutes list item only supplies a weak
  creator name. A later producer pass could pass the weak Minutes creator into
  `read_meeting_source`, which replaced the calendar creator's stable user id
  and made the Consumer/Audit loop fail again with the same target schema
  mismatch even after the delivery-side organizer lookup was fixed. Calendar
  evidence is now the source of truth for organizer identity whenever the
  meeting is calendar-backed; transcript-only sources keep the previous Minutes
  creator fallback.

- 2026-09-11: meeting alignment now gives source-aware target failures one
  structured correction turn before failing the meeting job. The existing
  output-schema retry only covered malformed JSON and cross-field schema
  errors; once the model returned a syntactically valid decision, roster-aware
  checks such as "business direct fallback requires a stable calendar
  organizer identity" and "sensitive private target must identify one meeting
  participant" failed after parsing with no feedback loop. The retry prompt
  now includes the exact target error, the previous decision, and the original
  meeting source so the next decision can switch to a valid business group,
  stable organizer fallback, or valid private-recipient target without sending
  anything during the repair turn. If DingTalk's calendar row names an organizer
  but omits stable ids, delivery now performs the same exact-profile resolution
  used for participant mentions before treating the organizer as unresolved.
  When that exact lookup succeeds, a direct fallback whose user id was empty in
  the Agent result now sends to the resolved organizer id; a non-empty mismatched
  id is still rejected.

- 2026-09-11: an unusable Claude credential is classified as
  `claude_credentials_unavailable` instead of `claude_runtime_unclassified`. The provider
  reports it in its terminal result (`is_error: true` while the subtype is still
  `success`) rather than on stderr, so neither the stderr scan nor
  `_trusted_error_subtypes` saw it. The route therefore stayed unpaused and spent
  another CLI invocation every probe cycle on a credential the service cannot
  repair, and the stored snapshot kept no trace of the one remedy that works.
  Only a result the provider itself marked an error is read. The code and detail
  stay neutral about the cause, which this consumer cannot verify: the quota
  guard refills the access token from the auth pool, and only repeated failure
  means the owner must sign in. Naming `claude /login` as *the* remedy would have
  been right today by accident and wrong in the ordinary case.

- 2026-09-11: the service checkout moves to `/Users/derek/Projects/ceo-agent-service`.
  `~/Documents` and `~/Desktop` are synced by iCloud Drive, which races with Git's
  rewrites of `.git/index` and `.git/refs`: it produced `name 2` conflict copies
  throughout the tree, twice replaced a branch ref (`main`, `codex/agent-cron`)
  with an unusable copy, and evicted Git objects to the cloud as `.icloud`
  placeholders in a neighbouring repository. Deleting the copies is not enough -
  `.git/index 2` reappeared within the hour - so the checkout leaves the synced
  scope instead. `launchd/com.ceo-agent-service.main.plist` carried the old path
  twice, in the `service_root` default inside the CDATA block and in
  `WorkingDirectory`; both are updated here so a later
  `scripts/install-auto-reply-agents.sh` cannot reinstate it. The database
  (`~/Library/Application Support/ceo-agent-service`) and the workspace
  (`~/Documents/memory`) are unaffected, and `CEO_CORPUS_DIR` derives from the
  service root.

- 2026-09-11: unsubscribe discovery now gives an explicit already-unsubscribed
  terminal page precedence over a coexisting sign-in prompt. Some providers
  render both strings for an account that is already unsubscribed; classifying
  the login marker first incorrectly projected a completed skip as
  `needs_human`.

- 2026-09-11: the delivery-projection repair scan no longer requires the
  decision-quality judgement in order to rebuild a delivery fact.
  `_repair_completed_message_delivery_projections` validated each stored result
  against the *current* `AuditAgentResult` / `ConsumerAgentResult` and swallowed
  the failure with `except ValidationError: continue`, so every contract change
  silently removed more stored results from the scan - even though
  `_sent_reply_projection_from_result` reads only `external_result` and the
  proposal's actions. The two coverage fields are now defaulted at that read
  boundary, matching the policy already used in `app/quality_gate.py`; risk and
  confidence stay mandatory so an old opaque result cannot acquire a fabricated
  judgement.

  Scope, measured rather than assumed: the scan's input is
  `list_completed_audit_runs_missing_delivery_projection()`, which currently
  holds **2 rows**, not the 1454 stored audit results. One of the two is
  repaired by this change; the other additionally carries pre-contract extra
  fields (`external_result.verification_summary`,
  `proposal.actions[].expected_verification`) and would need legacy-shape
  compatibility code, which this repository forbids.

- 2026-09-11: SQLite failures out of the store now name their extended result
  code. `disk I/O error` is the primary code SQLITE_IOERR and says nothing about
  which operation failed; the extended name separates a failing read
  (`SQLITE_IOERR_READ`) from a shared-memory map (`SQLITE_IOERR_SHMMAP`) or an
  fsync, which is what tells a damaged page apart from a filesystem or locking
  problem. The error is re-raised unchanged - this names it, it does not handle
  it.

- 2026-09-11: the store's schema-currency fast path no longer reads
  `scheduled_task_runs` rows. That check ran on **every** `AutoReplyStore()`
  construction, so one damaged page in a table that grows with every scheduled
  run was fatal at startup for every CLI subprocess and crash-looped the cron
  dispatcher; `database disk image is malformed` also arrives as
  `sqlite3.DatabaseError`, which the callers' `except sqlite3.OperationalError`
  never caught. The row check moves to the post-migration verification, where a
  migration has just claimed to have written those fields.

  This needs the version to carry the guarantee instead, so
  `STORE_SCHEMA_VERSION` is bumped to `2026-09-11.1`: `e35e4dad` introduced the
  snapshot fields **without** a bump, so databases migrated by builds between
  the two carry a current version beside legacy snapshots, and dropping the
  fast-path read would have left them unrepaired forever. On a copy of the
  962 MB live database (7753 runs) the resulting migration takes 1.3s.

  One guarantee is deliberately traded away: a database that carries the
  current version *and* legacy snapshots no longer self-heals. After this bump
  that state can only arise if a later change alters the persisted snapshot
  shape without bumping again, which the repository already forbids.

- 2026-09-11: the schema-currency check no longer scans `scheduled_task_runs`
  in full on every `AutoReplyStore()` construction. It asks SQLite for the
  first snapshot that predates the command columns and stops there
  (`json_valid` skips corrupt rows, `json_type` distinguishes an absent key
  from a JSON null value, so the semantics are unchanged). The table grows
  with every scheduled run (7,618 rows), and every CLI subprocess ran the
  scan at startup, which is why one damaged page in that single table took
  down `agent-cron-dispatcher` with `database disk image is malformed`
  instead of keeping the damage local to the row that reads it.

- 2026-09-10: documented the decision-quality contract and projection gate: Consumer/Audit
  results now share required `risk`, `confidence`, `rule_coverage`, and
  `information_completeness` fields; information-incomplete results use the existing
  ask-back path, while only the exact risk/coverage predicate may produce `needs_human`.
  Technical/provider/read/route/schema/Audit/retry failures remain `failed`, feedback may
  combine one-time and Skill updates on the same attempt, and quality/Attention counts are
  fail-closed against the current latest projection only. Controlled legacy hydration and
  preservation of historical runs are explicit.

- 2026-09-10 (round 6): the remaining live email and meeting failure classes.
  - A terminal unsubscribe **skip** now ends the task where the lifecycle
    already put it instead of as a technical failure. The audited tool decides
    the outcome before the model answers (`disposition_for_unsubscribe_outcome`
    gives every `SKIPPED_*` a non-retryable `skipped` disposition and returns
    `status="done"` with the typed outcome), but that decision was dropped at
    the Audit boundary, so the free-text error code the model chose for an
    operation the service is not allowed to complete became the task's
    terminal state (tasks 383232 and 383234 ended `failed` with
    `login_required` while their receipts read `skipped_login_required`).
    `_finalize_email_task` now projects the persisted receipt:
    `skipped_login_required` / `skipped_captcha` / `skipped_payment` close
    done/needs_human as the user handoff the runtime doc requires for a
    password, MFA or CAPTCHA wall the service must never pass itself, and
    `skipped_no_reliable_entry` / `already_unsubscribed` close done/skipped
    with an empty error. A genuine needs_human or authorization boundary is
    never rewritten by the projection.
  - `MeetingAlignmentDecision`'s 19 cross-field rules no longer live only
    inside `raise ValueError(...)`: they are constants
    (`MEETING_ALIGNMENT_CROSS_FIELD_RULES`) rendered into the main prompt, the
    repair prompt and `model_json_schema()` (so the committed
    `--output-schema` carries them too). The repair prompt appends the
    requirement for the rule that fired and relists every rule, because a
    pydantic after-validator short-circuits on the first one while the
    correction turn happens once — six live `result_validation_correction`
    attempts had each fixed one rule and broken the next.
  - Browser failures carry a fixed internal category
    (`UnsubscribeBrowserFailure`) beside the coarse task-level code, so a
    generic `email_unsubscribe_browser_failed` is diagnosable without ever
    recording page text or a URL. The code deliberately stays coarse: the
    explicit-retry release path in `app/email_store.py` matches
    `email_unsubscribe_browser_failed` exactly, and a test now pins that
    cross-module dependency. A settled page whose controls none of the model
    reached reports `page_controls_unmodelled` (this browser's own limit)
    rather than an undetermined page state.

- 2026-09-10: the Claude event grammar recognizes the two telemetry events the
  live transport emits around a turn — the subscription quota window
  (`rate_limit_event`, which can arrive before session init) and the
  extended-thinking budget notice (`system/thinking_tokens`, raised by
  `--effort`). Both carry no turn item and map to no runtime event, while every
  other event shape stays a grammar violation. Without this a real
  `claude_oauth` turn failed with `claude_init_missing` or
  `claude_event_unrecognized` before reaching its result.

- 2026-09-10: the console's Agent Runtime panel carries the Claude route. The
  SPA settings bridge (`/api/console/settings/agent-runtime`) whitelists the
  fields it forwards to the legacy save handler, so a save from the console
  would have dropped `claude_oauth` from `CEO_AGENT_RUNTIME_ROUTES`; it now
  derives the route from the stored value the same way `codex_api` already did
  and carries `CEO_CLAUDE_MODEL` / `CEO_CLAUDE_MODEL_REASONING_EFFORT` in both
  directions. A blank or omitted Claude field keeps the configured value
  instead of failing the save.

- 2026-09-10: `claude_oauth` joins `codex_oauth`, `codex_api`, `claude_api` and
  `friday_runtime` as an Agent Runtime route. It runs the host's `claude` CLI
  against the machine's existing login, so it needs no Anthropic API key, and
  defaults to model `sonnet` with `--effort medium`
  (`CEO_CLAUDE_MODEL` / `CEO_CLAUDE_MODEL_REASONING_EFFORT`, shared with
  `claude_api`); a scheduled task's thinking option overrides the effort per
  run, as it already did for Codex. Two facts of the CLI shaped the command:
  `--bare` resolves Anthropic auth strictly from `ANTHROPIC_API_KEY`, so the
  subscription route drops it, and `--safe-mode` would also drop the service's
  own `--mcp-config` servers, so neither route uses it. Isolation still comes
  from `--setting-sources ""`, `--settings` and
  `--strict-mcp-config --mcp-config`, which keep the caller's CLAUDE.md,
  skills, plugins and hooks out of a service run. Two defects that only a live
  Claude route could surface are fixed with it: the stdio MCP proxy buffered a
  whole 64 KB `read()` before forwarding, so every service stdio MCP server
  (`agent_cli`) failed its handshake while the client held stdin open, and the
  event normalizer rejected the subscription transport's `rate_limit_event` as
  an unrecognized event, which failed the route's health probe.

- 2026-09-10: a work item whose bounded decision repair rounds are exhausted
  (`TaskDecisionRepairExhausted`) is retried on a later pass within the
  work-item attempt budget instead of being terminalized on the structural
  validation error.

- 2026-09-10: `AutoReplyStore.complete_superseded_failed_weekly_okr_analysis_jobs`
  extends the documented supersede recovery to analysis jobs that ended with
  a technical failure (`runtime_route_unavailable`, `runtime_lease_expired`):
  when the same manager has a completed analysis for a later week, or a
  completed analysis started after the failed one (the rerun that shipped
  that report), the job is closed as `superseded_by_later_completed_week`.
  The existing recovery only handled still-`running` jobs, so 12 outage-era
  jobs from late August and 3 lease-expired jobs from 2026-09-08 (whose
  managers' 2026-09-06 analyses completed the same morning) stayed in the
  failed set indefinitely; all 15 were closed with it.

- 2026-09-10 (email queue unblocked at the root):
  - A process whose capability registry lacks a route (every service child
    starts empty) now adopts a sibling process's fresh, healthy snapshot from
    the shared store instead of probing the provider again
    (`RuntimeCapabilityRefresher._shared_healthy_snapshot`). The email
    worker had been failing its own probes while the same routes served the
    service process, so its Consumer turns saw `codex_api=snapshot_missing`
    / `snapshot_unhealthy` and the whole queue sat in deferrals; adoption
    also removes the duplicate probe load on rate-limited providers.
  - A turn that finds every route paused or unprobed no longer leaves a
    failed Consumer/Audit run behind: `AgentTurnProcess` discards the
    unstarted run (`AutoReplyStore.discard_unstarted_agent_run`, only for
    the claiming owner and only while the run has no runtime attempt, tool
    event, effect intent or receipt) and the orchestrator defers the task
    without a run. One email task had accumulated 458 failed Consumer runs
    (3,342 across 29 tasks in one hour) purely from outage polls; those
    rows inflated `agent_runs`, `reply_attempts` and the History failed
    projection and pushed `turn_attempt` into the hundreds.

- 2026-09-10 (round 5 + email worker stability, subagent-verified):
  - `EmailStore._connect` is now a closing context manager (split into
    `_open_connection` + `_connect`, mirroring `AutoReplyStore`): the bare
    connection it used to return was only committed/rolled back by `with`,
    and a reference cycle through the statement cache kept ~90 call sites'
    connections (2 fds each: db + WAL) alive until cyclic GC. The
    email-worker process climbed to launchd's 256-fd soft limit, then
    `Too many open files` / `unable to open database file` killed its
    threads (`email worker component exited unexpectedly`, 60 child
    restarts), and every restart began with an empty capability registry so
    `codex_api` showed `snapshot_missing` and the whole email queue sat in
    `runtime_provider_unreachable` deferrals. The launchd plist template
    now sets `SoftResourceLimits/NumberOfFiles=4096` (re-install with
    `scripts/install-auto-reply-agents.sh`; `kickstart -k` does not re-read
    it). A regression test asserts the fd count stays flat under repeated
    store calls.
  - Meeting alignment shares the workers' outage gate
    (`app.worker._is_runtime_outage_error`): a no-route outage or a
    capacity/transport failure of the last live route defers the job with a
    capped per-turn backoff (attempt handed back, no Attention row) instead
    of ending it `failed` on the first pass (jobs 2675/2695/2717/2743).
  - An Audit result's `proposal_revision` is bound from the Audit run
    instead of hard-failing the turn when the model echoes a different
    number (`audit_proposal_revision_mismatch`; MiniMax wrote the revision it
    was requesting). The Audit rules now state that the echoed value names
    the candidate reviewed, never the requested revision.
  - Task-agent decision repair runs up to `TASK_DECISION_REPAIR_ROUNDS` (2)
    repair turns, so a repaired decision that trips another repairable rule
    (live pattern: `update_project requires project`, then
    `project.memory_context`) is repaired again instead of escaping as a
    terminal work-item failure; exhaustion raises the typed
    `TaskDecisionRepairExhausted`. The repair prompt's garbled memory_recall
    sentence is fixed and it now states the `project.memory_context`
    contract (query + summary/memories, the "no relevant memory found"
    sentence, the `memory_connector_runtime_unavailable` escape hatch, and
    `skip` when nothing changes).

- 2026-09-10: keep domain-specific email unsubscribe and other provider/policy
  rejections as explicit failures instead of converting them to `needs_human`.
  `needs_human` now remains reserved for the generic `authorization_required`
  boundary after the common `risk`/`confidence` and actionable-options checks;
  the architecture and runtime documents now state this rule for every task
  type, not only email.

- 2026-09-10 (round 4, owner decisions, subagent-verified): three contract
  decisions from Derek.
  - `StrictTaskModel` (app/task_models.py) treats JSON `null` on any optional
    field with a non-null default (`owner_evidence`, `blocker`,
    `evidence_check`, `tags`, `memory_context`, `todo_changes`, ...) as the
    field being omitted: a before-validator drops the key so downstream code
    keeps seeing the declared default, and
    `TaskAgentDecision.model_json_schema()` — embedded in every task prompt —
    renders those fields as nullable (`anyOf` with `null`). Required fields
    and the evidence objects an action needs stay non-nullable, and the
    correction prompt no longer claims `null` is forbidden everywhere.
    MiniMax's habitual `owner_evidence: null` had cost a correction turn and
    then failed the run (47 failures since 2026-09-09).
  - No legacy model-output compatibility left: `parse_codex_json` loses its
    `allow_legacy` mode (pre-envelope `{action, reply_text}` objects, the
    lenient envelope-like fallback that invented audit summaries, the legacy
    `okr_review` + `request_id/result` conversion, the OKR audit normaliser
    and the action-free `no_reply` shorthand are gone). A wrong shape gets
    the single same-session correction naming the pydantic field paths;
    otherwise the task fails and is rerun.
  - After the one same-session correction turn, a capacity/transport failure
    of that turn is waited out up to `CAPACITY_WAITS_BEFORE_FAILOVER` (3)
    times before the workload leaves the route: each failure raises the
    retryable `runtime_execution_failed` (`correction_capacity_wait:<n>`)
    that the workers defer, a persisted correction with a capacity/transport
    failure resumes on its own route while the wait budget lasts
    (`persisted_correction_route_unavailable` while that route is paused),
    and the 4th failure fails over to a successor route with a fresh session,
    the original prompt and its own correction budget. Non-outage failures of
    the correction turn stay terminal.

- 2026-09-10: two `tests/test_agent_runtime_worker.py` baselines
  (`test_worker_stops_retryable_orchestration_at_attempt_limit`,
  `test_nonzero_native_write_uses_failed_retry_path_in_real_runner_protocol`)
  had been stale since 86060e4b (2026-09-08, "enforce durable role retry
  ceiling"), which never got a changelog entry: a Consumer/Audit turn that
  exhausts the in-process role retry ceiling (`MAX_ROLE_ATTEMPTS_PER_PROCESS`,
  two turns per proposal revision) with a genuinely retryable failure
  (`audit_dependency_unavailable`, `native_write_failed`) ends the
  orchestration `failed_terminal`, so the DingTalk reply task ends `failed`
  in the same pass with the last run's typed code instead of going back to
  `pending` for another pass of Audit turns (which let the scheduled-task
  consumer and the active-recovery path re-enter the same generation without
  bound). Outage, authorization and deferred codes are excluded from that
  ceiling and keep deferring without spending the budget. Test-only change.

- 2026-09-10: scheduled-tasks page no longer shows fabricated configuration.
  The service-command branch had a hardcoded "Consumer Agent 系统提示词",
  an unsaved "Consumer Agent 自定义描述" textarea, hardcoded Skill chips and a
  "Consumer Agent Runtime" select with literal `codex_api`/`codex_oauth`
  options, and the Agent branch showed a system prompt the service does not
  have. Command tasks now show one read-only note about downstream
  consumption; Agent tasks keep the real 任务描述, Runtime and structured
  Skills; the execution-type selector is disabled for repository-seeded
  tasks, matching the API's immutable-command rule. Console status tests:
  the four fakes left behind by the strict WorkerStatus contract are
  complete again, the email-account fixture no longer sends the removed
  `scan_interval_seconds`, and the four contract tests dropped by merge
  2d8f17cb are restored. The Agent Cron spec is revised in place for the
  service-command execution form and the paused-route Attention rule.

- 2026-09-10 (round 3, subagent-verified): the WeChat reply path and a test
  regression.
  - The WeChat/Codex decision parser (`app/codex_decision.py`, used by
    `WechatDecisionRunner`) extracts the `AgentEnvelope` from fenced or
    prose-wrapped agent messages through the shared
    `agent_message_json_objects` extractor (last object first); a candidate
    that looks like an envelope but violates the schema raises the
    field-level `ValidationError` and gets the single same-session correction
    turn instead of being accepted by the lenient envelope-like fallback
    (now reachable only with `allow_legacy=True`). `_decide_routed` attaches
    `raw_output` to `RoutedResultValidationError`, so the correction prompt
    can list `- <field.path>: <msg>` problems (paths and validator messages
    only, capped) and render the contract from
    `AgentEnvelope.model_json_schema()` instead of a hand-copied hint that
    had drifted (missing `oa_approval`, `reply_text_ref`, OA action types).
    MiniMax's fenced but valid correction turns (agent runs 9576/9581) had
    been rejected for the fence alone, and one envelope lacking
    `audit.confidence` (9586) had been accepted.
  - The WeChat consumer (`app/wechat/consumer.py`) shares the DingTalk
    worker's outage gate (`app.worker._is_runtime_outage_error`): a decision
    turn that could not enter a route, or whose last live route failed on
    capacity/transport with every other route paused, defers as
    `runtime_provider_unreachable` with a per-turn backoff, hands the attempt
    back, writes no `reply_attempts` row and no Attention item.
    Authentication, result, process and `STOP_WITH_ERROR` failures stay
    bounded by `max_task_attempts`. Decision turns are numbered from the
    persisted Consumer runs of the generation (any terminal run advances the
    turn; only a running run keeps it), which also removes a silent requeue
    loop where a retryable `STOP_WITH_ERROR` or a stale-claim recovery
    re-derived the same turn forever.
  - `tests/test_agent_cron_seeds.py::test_seed_is_disabled_when_exact_repository_revision_is_not_loaded`
    had a stale baseline since merge 2d8f17cb (repository import now adds a
    child config with the email-classifier binding); the test compares
    against the post-import config. No runtime change.
  - Three `tests/test_codex_decision.py` command-shape assertions assumed the
    codex command ends with `--cd <dir> -` / `--image <img> <session> -`;
    since the service MCP manifest is spliced into every routed command
    (2026-09-09) they check the option pairs instead of the tail.

- 2026-09-10 (round 2, subagent-verified): five more Attention root causes.
  - A provider outage discovered only after entering the last live route
    (that route fails on capacity/transport, e.g. `codex_provider_overloaded`
    on `codex_oauth` while `codex_api` is paused) is now treated exactly like
    `runtime_unavailable`: work items and DingTalk reply tasks defer with
    backoff under `runtime_provider_unreachable`, attempts are handed back
    and no per-item Service error is recorded. The classification lives in
    `app.worker._is_runtime_outage_error` and is shared by `app/cli.py` and
    `app/worker.py`; authentication, result, process and capability failures
    stay bounded. Previously 54 `task_agent` Attention rows and reply tasks
    383510-383512 ended failed this way.
  - The task-agent parser distinguishes "no `TaskAgentDecision` JSON" from
    "decision object found but schema-invalid" and reports the pydantic
    field errors of the last candidate (path + message only, never the
    model's values). The correction prompt lists those problems plus the
    rules MiniMax breaks most (`null` for string/object fields, project
    fields outside `project`, `skip` when nothing changes, `update_project`
    needs a stable id and `project.memory_context`). Runs 7460/7472/7475/7491
    had returned complete decisions with `owner_evidence: null` and were
    reported as "no JSON found", so the correction turn resent the same
    object.
  - Meeting alignment's schema correction turn now actually fires:
    `parse_meeting_alignment_decision` raises the typed
    `RoutedResultValidationError` instead of a plain `ValueError` (which the
    router recorded as terminal `runtime_result_invalid`; 15 MiniMax runs,
    zero correction attempts). The repair prompt collapses per-index topic
    errors and de-duplicates before its cap, and both prompts embed the
    `MeetingAlignmentDecision` JSON schema derived from the model, because
    third-party providers ignore `--output-schema`.
  - The OKR-review (`structured`) `AgentEnvelope` correction prompt names the
    concrete field problems of the last JSON candidate and restates the
    contract from `AgentEnvelope.model_json_schema()` instead of quoting a
    4000-character excerpt. (The WeChat reply path parses in
    `app/codex_decision.py`, not here — fixed separately.)
  - The scheduled-task scheduler no longer records a `Service error` when an
    event is skipped because the task's runtime route is merely paused or
    unprobed (typed per-route reason classified with the router's
    `route_unavailable_code`); the `skipped` run row stays and a warning is
    logged. Missing capabilities, authentication pauses and an unconfigured
    runtime id still enter Attention. Task 4 had produced one row per hour
    during the outage.

- 2026-09-10: the email consumer loop defers a task whose context load hit
  a transient mailbox/network failure (IMAP TLS handshake timeout,
  connection reset, imaplib transport errors) with backoff for up to five
  attempts (`email_provider_transient:<type>`) instead of failing it
  terminally as `email_consumer_runtime_error:TimeoutError`; a task
  superseded mid-turn by a rerun or stale-claim recovery is left to its new
  generation instead of being failed.

- 2026-09-10: meeting alignment gets one same-session schema correction
  turn (third-party providers ignore Codex's output schema); the correction
  prompt lists the exact `MeetingAlignmentDecision` field errors of the last
  candidate. The WeChat `AgentEnvelope` parser skips non-JSON lines around
  the Codex JSONL stream instead of failing the whole result, and the router
  logs the reason behind every `runtime_result_validation_failed` (never the
  raw output).

- 2026-09-10: an evidence-backed `executed` Audit result now takes its
  `external_result.operation_id` from the Audit run itself. The tool receipt
  bound to the run is the evidence; the opaque operation id is service-owned,
  and a model that retyped it (e.g. wrote the action identity instead) used
  to end the task in `domain_continuation_state_invalid`. `executed` without
  any `external_result` is a `codex_result_invalid` correction instead.

- 2026-09-10: email schema v36 finishes the network-policy removal: v35
  rewrote `reply_tasks.trigger_message_json` but left the immutable
  `reply_task_inputs` copy untouched, so every pre-v35 unsubscribe task that
  ran again failed with `email_consumer_runtime_error:EmailAgentTaskConflict`
  (the adapter re-derives the payload and compares it with the input row).
  The email consumer loop now logs the exception message and traceback for
  every `email_consumer_runtime_error:*` (the task row keeps only the type).
  The same loop now reclaims its own orphaned claims before each pass (an
  email task left in `processing` for 10 minutes without a live Agent run,
  or for 60 minutes in total, is requeued as `stale_email_task_recovery`) and
  claims 5 tasks per pass instead of 50: tasks run one at a time there, so a
  restart mid-batch used to strand the unstarted tail in `processing`
  indefinitely, invisible to Attention (61 such tasks were found).

- 2026-09-10: five Attention root causes fixed after the ChatGPT/MiniMax
  outage review.
  - Work items no longer spend their bounded retry budget while every
    runtime route is paused or unprobed: the router marks that state as
    `runtime_unavailable`, and the work-item worker defers the item with
    backoff (attempts untouched, no per-item Service error). Previously each
    outage pass counted as a failed attempt, so items ended in
    `runtime_execution_failed` after three passes.
  - The task-agent, WeChat (`AgentEnvelope`) and meeting-alignment parsers
    share one extractor (`agent_message_json_objects`) that finds every
    top-level JSON object inside an agent message, through Markdown fences
    and prose, and validates the last complete one. MiniMax answers of that
    shape were reported as `No TaskAgentDecision JSON found`,
    `runtime_result_validation_failed` (WeChat) or `runtime_result_invalid`
    (meetings) even when the JSON itself was valid.
  - `execute_audited_email_unsubscribe` binds the Audit's acceptance by
    `action_identity` and executes the Consumer's persisted proposal. The
    model no longer has to retype the proposal byte for byte (missing
    `description`, a truncated digest, or the whole proposal instead of one
    action used to be `unsubscribe_operation_rejected:ValueError`), and a
    rejection now carries its reason in the tool summary.
  - The unsubscribe browser waits up to 5 s for script-rendered page text
    before reporting `email_unsubscribe_page_state_missing`; navigation
    returns at `domcontentloaded`, when such pages still have an empty body.
  - Codex's generic "We're currently experiencing high demand" wrapper
    (a provider 429/5xx, e.g. an exhausted MiniMax token plan) is classified
    as `codex_provider_overloaded` (capacity, failover, route pause) instead
    of `codex_transport_disconnected`.

- 2026-09-09: when every runtime route is paused or unprobed at the start
  of a routed workload (task agent, meeting, classification, workbench), the
  router now reports the outage as a retryable external dependency, so the
  work item is deferred with backoff instead of being failed and surfaced in
  Attention. Authentication pauses and missing capabilities stay terminal.
  The route-unavailability classifier moved into the router and is shared
  with the Agent turn runner.

- 2026-09-09: an audited email unsubscribe Audit turn may report `executed`
  only when `execute_audited_email_unsubscribe` actually ran for that turn
  (a claim or effect bound to the Audit run id). An unbacked `executed` is
  now a `codex_result_invalid` result whose next turn carries the
  `## Result Correction` block, instead of a task that ends in
  `domain_continuation_state_invalid`; the orchestration guard stays as the
  final backstop. The Audit prompt states the rule explicitly.

- 2026-09-09: add Settings → MCP. The page lists every MCP server Codex would
  load for a run (from `codex mcp list --json`, URLs shown without query
  tokens) with a per-server "available to background agents" switch, plus the
  service manifest's own servers with add/remove. The switch writes
  `disabled_servers` into the selected service manifest; service Codex
  commands disable those servers with a whole-table `enabled = false`
  override, effective from the next Agent turn. `cua_repl` (the desktop
  computer-use REPL, which had leaked into audited email turns) is disabled by
  default.
  The manifest is now applied by the runtime adapter to every routed Codex
  command (task, meeting, classification, workbench, probe turns included),
  not only to Consumer/Audit role commands.

- 2026-09-09: remove the dead WeChat producer/consumer loop roles. The
  internal loop now only runs the sender (`_run_wechat_sender_loop`); reading
  is the `wechat-produce-once` scheduled service command and replies are
  consumed through the unified Dispatcher, so the loop no longer marks the
  reader healthy or maps consumer failures. `wechat_loop_names` is gone. Loop
  failures are recorded as `wechat_sender_loop_error`.

- 2026-09-09: run Codex turns on `service_api` routes without the automatic
  reviewer. The reviewer is a call to the `codex-auto-review` model on the
  route's provider; MiniMax rejects it as an unknown model, so every reviewed
  action on the fallback route (the audited unsubscribe tool included) was
  refused "due to unacceptable risk" and surfaced as model-invented error
  codes. `service_api` turns now use `approval_policy="never"` with the
  sandbox intact; `codex_oauth` keeps `auto_review`.

- 2026-09-09: run the WeChat message check as a service command too. The
  catalog gains `wechat-produce-once` (the same pass as
  `app.wechat.cli produce-once`), bound in-process with the legacy loop's
  reader-health semantics: no ready account or an unreachable Reader app
  returns a summary, reports once through the `wechat.reader` health
  component and the error log, requests one Reader restart after three IPC
  failures, and clears on the next successful pass; only other exceptions
  fail the trigger. The seeded `wechat-message-check-v1` task is converted in
  place; an untouched legacy seed (version 1) becomes enabled because its
  disabled state only reflected the Agent form's missing Skill revision,
  while an edited task keeps the user's enabled state. The command returns a
  skipped summary without touching the Reader while
  `CEO_WECHAT_READER_ENABLED` is off. README, the WeChat operations guide,
  the user guide, and the error catalog now describe service command tasks
  and the surviving sender-only loop.

- 2026-09-09: run the DingTalk message check as a service command instead of an
  Agent task. A scheduled task now has a `command` field (schema adds
  `scheduled_tasks.command` and backfills persisted run snapshots). A command
  task names one entry of the service command catalog (`produce-once`, the same
  operation as `app.cli produce-once`); the Dispatcher runs it in-process inside
  the trigger claim and links `service_command` as the trigger execution
  (`dispatched`), or ends the trigger `failed` with
  `scheduled_task_service_command_failed` in Attention. Command tasks create no
  reply task, agent run, or reply_attempt, need no Runtime, Skills, or working
  directory, and are gated before dispatch only by
  `scheduled_task_service_command_unavailable`. The seeded
  `dingtalk-message-check-v1` task is converted in place on startup (name,
  Cron, timezone, and enabled state kept) and its command is immutable through
  the Console API, which now also lists `service_command_options`. This
  replaces the migration-key special case that ran the prompt's backtick
  command as a subprocess and recorded success as `skipped`.

- 2026-09-09: remove the email unsubscribe browser network policy. The
  exact-origin allowlist, private/loopback address resolution checks, provider
  redirect/resource family rules and WebSocket rejection are gone; the browser
  only requires absolute http(s) navigation targets. The policy reference and
  origin references leave the effect identity, task payload, proposal target,
  continuation record and audit binding; email schema v35 re-hashes persisted
  effects without them (updating the claims, continuations, steps and receipts
  that point at those digests) and strips the two policy keys from queued
  email task payloads.

- 2026-09-09: accept `null` as the wire `error_code` for results without an
  error (normalized to the empty string). The fallback model kept returning
  `"error_code": null` even after a correction turn, so the strict string
  sentinel was rejecting otherwise valid proposals.

- 2026-09-09: derive the route-unavailable failure code from the router's
  typed per-route reasons instead of substring matching on the display
  string (the route name `codex_oauth` used to classify a missing probe
  snapshot as an authentication failure). Missing/expired snapshots and
  non-authentication pauses defer the turn as `runtime_provider_unreachable`;
  the Email task loop now requeues deferred orchestration results with
  backoff like the DingTalk worker instead of failing them as
  `email_consumer_runtime_error`.

- 2026-09-09: classify Codex `server_overloaded` ("Selected model is at
  capacity") as a `capacity` runtime failure (`codex_provider_overloaded`).
  The turn now fails over to the next configured route inside the same Agent
  run, pauses the overloaded route until the probe sees it healthy, and defers
  the task when every route is overloaded instead of exhausting same-route
  retries into `codex_process_failed`.

- 2026-09-09: report a schema-violating typed result as `codex_result_invalid`
  with its field locations instead of `codex_result_missing`, and feed those
  locations back to the same role's next turn as `## Result Correction` so the
  retry can repair the wire result rather than repeat it. A turn that returned
  prose without any JSON object (`codex_result_missing`) gets the same
  correction block.

- 2026-09-08: register the installed Fxiaoke `sharecrm` CLI in Settings →
  Connectors. The page now reports executable/authentication readiness, current
  CRM user, CLI version, and the read-only status check used by the service.

- 2026-09-08: route main-page Agent turns through the shared Service Runtime.
  Main-page work now shares route selection, runtime-attempt persistence,
  session continuity, failover, cancellation, and native CLI `auto_review`
  with other service Agents; the production path no longer starts the separate
  Workbench Codex runtime or uses approval bypass. Legacy Workbench confirmations
  remain visible as read-only history and cannot execute from the UI or API.

- 2026-09-08: allow `send-attempt` to reopen a `needs_human` result only when
  an explicit reviewed instruction is supplied. The instruction creates a new
  Consumer/Audit revision while preserving the original decision record; sent
  or completed attempts remain non-replayable.

- 2026-09-07: remove the foreground-capable WeChat Sender preparation RPC.
  Actual delivery now owns the one bounded UI activation it needs; an unavailable
  window can no longer leave a delivery queued and repeatedly activate WeChat
  on each sender poll.

- 2026-09-07: align hourly quality-check `needs_human` attention and the
  Workers reply-attempt summary with the stable-business-object current
  projection. Historical attempts remain queryable in History but stop
  contributing to current counts after `business_object_tasks` points to a
  different task.

- 2026-09-07: keep exhausted Audit feedback loops as explicit failures instead
  of synthesizing `needs_human`; human escalation still requires Audit's valid
  high-risk, low-confidence, Skill-gap result and actionable choices.

- 2026-09-07: keep each Agent DingTalk feedback revision's corrected prepared
  message body distinct from earlier revisions, while retaining the stable
  external action identity that prevents duplicate provider effects.

- 2026-09-07: keep an unresolved `needs_human` result visible after its queue
  task reaches `done`. The queue task is closed so workers do not re-execute it,
  while the latest reply-attempt projection remains actionable in Attention and
  in the hourly quality report until a decision or revision resolves it.

- 2026-09-07: make DingTalk meeting-summary delivery resumable across a service
  restart. A prepared delivery now has a stable provider UUID and durable receipt;
  a recovered worker continues that delivery rather than sending a second message.

- 2026-09-06: require every meeting-alignment run to produce one delivered
  summary. The structured decision contract now permits only `send`; business
  group-discovery and delivery problems remain retryable or failed work rather
  than terminal `no_action`; the sole exception is a confirmed deleted Minutes
  source, which has no recoverable content and is terminal `no_action`.

- 2026-09-06: split WeChat Sender readiness into two distinct operations:
  passive `check_readiness` for Tutorial and service checks, and
  foreground-capable `prepare_delivery` reserved for an actual delivery.
  Tutorial verification now reuses its initial passive result instead of
  checking the same Accessibility state twice.

- 2026-09-06: stop the idle WeChat Sender loop from actively preflighting the
  desktop client. It now reads the local `ready_to_send` queue first and only
  performs the foreground-capable accessibility check for an actual delivery.

- 2026-09-06: add sender-local foreground telemetry for WeChat. The dedicated
  Sender records only foreground transitions and its own activation call site,
  with no contact or message content, so any unexpected desktop switch can be
  attributed without relying on operator observation.

- 2026-09-06: make Tutorial's WeChat connection check passive. Connecting now
  verifies the dedicated Reader and Sender without activating the WeChat app;
  its Accessibility permission prompt also reports permission only, rather than
  running a second window check. Only an actual queued message delivery is
  allowed to bring WeChat forward.

- 2026-09-06: make WeChat Sender window checks delivery-scoped. Status polling
  now verifies only the dedicated Reader and Sender helpers; it never probes or
  foregrounds the WeChat window. Accessibility/window preflight may activate
  WeChat only for an actual queued delivery.

- 2026-09-06: stop appending web feedback callbacks to WeChat replies. Personal
  WeChat text messages do not render Markdown links, so new WeChat deliveries
  now keep the configured assistant signature without exposing long internal
  feedback URLs; DingTalk feedback links and local History feedback remain
  available.

- 2026-09-06: restore WeChat preflight activation before retrying an empty AX
  window tree. The Sender now uses the same cross-Space activation path during
  health checks as it does before delivery, preventing Tutorial and Status from
  blocking while WeChat is open on another Mission Control Space.

- 2026-08-31: restore the configured assistant postfix for DingTalk messages
  sent by meeting-alignment delivery and weekly OKR group summaries. These
  two system-generated send paths now use the same idempotent signature
  formatter as ordinary replies, so the postfix is added exactly once.

- 2026-08-30: WeChat Accessibility preflight now opens the running WeChat app
  before raising its process, which switches a window from another Mission
  Control Space into the active Space. Preflight retries activation up to three
  times with one-second settling intervals before reporting the window as
  unavailable.

- 2026-08-31: when a conversation has an unread marker but the unread-message
  response lacks message rows, the producer now reads that conversation's
  recent messages as the context fallback instead of treating the conversation
  as empty. The original read error remains auditable and the fallback uses the
  source message ordering and timestamps.

- 2026-08-30: normalize follow-up times in `Asia/Shanghai` before applying
  work-hour rules, so timezone-qualified model output cannot bypass the same
  business-window validation used when persisting a task-agent decision.

- 2026-08-30: recover two deterministic background-agent validation failures.
  Candidate-interview `no_action` now explicitly overrides the 1:1 meeting
  delivery-target contract, so analysis produces `target=null` and never enters
  message delivery. Task-agent decisions that select `update_project` without
  a stable integer project ID now receive one bounded structured correction
  turn instead of immediately terminalizing the work item; if the ID still
  cannot be established, the corrected decision can skip without creating a
  duplicate project.

- 2026-08-30: allow a resolved user-feedback item to be reopened through the
  existing local, unauthenticated Feedback API with an exact operator-supplied
  reason. Reopen returns only the current projection to pending; the next claim
  creates a new immutable batch and processing round, while prior associations
  and receipts remain readable and cannot satisfy the new round. Resolution
  now accepts only current-round evidence proving the implementation, tests,
  commit ancestry, service restart and new PID, health, authoritative zero
  processing/failed/retryable backlog, and persisted API readback. The change
  adds no new Agent workflow or remote-authentication surface.

- 2026-08-29: ordinary Consumer and Audit turns now receive the configured
  work profile, and profile changes rotate stale Consumer sessions. Audit asks
  Consumer for a replacement when a safe receipt-only candidate does not
  genuinely engage with substantive input; the response itself remains guided
  by the current profile, conversation, and inspected evidence rather than a
  domain template or keyword classifier.

- 2026-08-29: add the local user-feedback processing workflow. Pending
  feedback can be claimed as a deterministic batch, imported into a Workbench
  brainstorm conversation with attempt/run/task references, and resolved only
  after matching commit, test, launchd restart, and `/healthz` receipts are
  persisted. Original feedback comments remain unchanged. See the [approved
  design spec](docs/superpowers/specs/2026-08-29-feedback-processing-api-and-workbench-design.md)
  and [implementation plan](docs/superpowers/plans/2026-08-29-feedback-processing-api-and-workbench.md).

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
