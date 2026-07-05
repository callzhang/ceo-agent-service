# Reply worker reliability

## Failure visibility

`produce-once` and `consume-once` record top-level failures in the `errors` table
and raise a local macOS notification before exiting non-zero. Launchd keeps one
main service alive; that process runs the audit web server, producer loop, and
consumer loop. If any component stops unexpectedly, the process exits so launchd
can restart the whole service.

Per-conversation read failures are recorded and notified without blocking other
conversations in the same producer pass.

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

Handoff notifications use DING first so they can reach the operator inside
DingTalk. If DING is unavailable, for example because the DING server quota is
exhausted, the worker falls back to the same local notification path instead of
failing the reply attempt. The original chat acknowledgement remains the delivery
source of truth; the local notification only replaces the operator alert.

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

## DWS auth environment

The LaunchAgents keep work data under the configured `CEO_WORKSPACE` and use the
current user `HOME` unless `CEO_SERVICE_HOME` is explicitly set. The service
forces DWS file-backed auth with `DWS_DISABLE_KEYCHAIN=1` and defaults
`DWS_KEYCHAIN_DIR` to
`${CEO_WORKSPACE}/Library/Application Support/dws-cli`. This keeps the service's
refresh token exportable and restorable instead of depending on macOS Keychain
state. The diagnostic script `scripts/check-dws-auth-env.sh` verifies the
default user auth path first and then compares the file-keychain probe.

After successful DWS calls, the worker periodically writes a local auth archive
to `data/dws-auth-backup/dws-auth.tar.gz` by calling `dws auth export`. The DWS
export format contains the refresh token and keychain material needed for
recovery, but not token plaintext. The archive is a runtime secret and remains
under ignored `data/`; the worker sets file mode `0600` after export.

On macOS, DWS may store its encryption key in the system Keychain if launched
without the file-backed environment. In that mode, `dws auth export` can refuse
to create a portable auth archive. The worker does not read or decrypt Keychain
items itself; it records `dws_auth_backup=unsupported` and keeps using the
existing login state without blocking replies. Completing one DWS login in the
service's file-backed environment gives the worker an exportable auth state and
lets backup/restore become fully active.

When a DWS call reports a login/session-loss error, the worker first imports
that local archive with `dws auth import --force`, then retries the failed DWS
call once. It starts the interactive `dws auth login` flow only if no archive is
available or the archive restore does not recover a usable login state. Backup
cadence defaults to 30 minutes and can be adjusted with
`CEO_DWS_AUTH_BACKUP_INTERVAL`. The worker also checks `dws auth status` after
successful DWS calls and immediately writes a fresh archive when
`refresh_expires_at` changes, so refresh-token rotation is captured before the
next periodic backup window.

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
- OA approvals, because approval ownership, task state, comments, and action
  execution are handled by the OA handler. The handler uses the unified
  structured Codex runner with the OA skill injected, then performs the
  service-owned approval action or comment.

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

## Consumer retry behavior

Reply tasks move from `pending` to `processing` when claimed. If task processing
raises an exception, the consumer records a retry error, sends a local
notification, and moves the task back to `pending` until the task reaches the
maximum attempt count. The default maximum is three claimed attempts.

Delivery failures for an otherwise sendable reply are treated as task processing
failures after the reply attempt has recorded the failed send. This keeps the
original message retryable instead of completing the task with a failed attempt.

When the maximum is reached, the task is marked `failed`, the final error is
recorded, and a local notification is sent.

If the agent can prove that required material or a required tool result is
unavailable and continuing would guess at the answer, it must return
`stop_with_error` with a reason starting `critical_info_unavailable:`. The worker
treats that prefix as a non-retryable task failure: it records the failed
attempt, marks the queued `reply_tasks` row `failed`, and sends the normal
`CEO task failed` notification for human handling. Tool calls that are merely
discouraged, such as retrying a DWS detail command after an OpenAPI recovery,
stay as prompt guidance and audit evidence; they are not blocked by the runner.

Processing tasks older than the stale-task threshold are also moved back to
`pending`; this recovery path sends a local notification so the operator can see
that an interrupted task was retried.
