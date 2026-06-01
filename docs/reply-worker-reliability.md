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
`http://127.0.0.1:8765/open-dingtalk?cid=...` in the background; the audit web
service then runs `/usr/bin/open dingtalk://dingtalkclient/page/conversation?...`.
The click handler does not open a new browser tab.

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
upgrade before reading DingTalk messages. Upgrade check or install failures are
recorded locally and notified, but they do not block message discovery for that
producer pass.

## Org cache refresh

The producer refreshes the DingTalk organization cache inside the normal CEO
system pass when the last successful refresh is older than seven local days. The
refresh shares the same service state as the manual `refresh-org-cache` command,
so a manual refresh prevents an immediate duplicate refresh from the next
producer pass. Refresh failures are recorded locally and notified, but they do
not block message discovery for that producer pass.

## DWS auth environment

The LaunchAgents keep work data under the configured `CEO_WORKSPACE` and use the
current user `HOME` unless `CEO_SERVICE_HOME` is explicitly set, so DWS can
decrypt the authenticated file-backed credential store selected by
`DWS_DISABLE_KEYCHAIN=1` plus `DWS_KEYCHAIN_DIR`. The keychain directory defaults
to `$CEO_WORKSPACE/Library/Application Support/dws-cli`.
Using the memory directory as `HOME` can report `not_authenticated` even when
the same token directory is valid from the normal user home. The diagnostic
script `scripts/check-dws-auth-env.sh` reproduces the boundary: the correct
service home plus file keychain dir succeeds, while an empty file keychain dir
fails with `not_authenticated`.

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
conversation is no longer unread, the task is completed and a `skipped` no-reply
attempt is recorded. If it is still unread, the consumer can claim the task and
move it to `processing`.

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

Processing tasks older than the stale-task threshold are also moved back to
`pending`; this recovery path sends a local notification so the operator can see
that an interrupted task was retried.
