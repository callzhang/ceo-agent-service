# Reply worker reliability

## Failure visibility

`produce-once` and `consume-once` record top-level failures in the `errors` table
and raise a local macOS notification before exiting non-zero. Launchd keeps the
five-minute producer schedule, so transient producer failures are visible locally
and retried by the next scheduled run.

Per-conversation read failures are recorded and notified without blocking other
conversations in the same producer pass.

## DWS upgrade check

The producer checks for `dws` updates inside the normal CEO system pass, once per
local day. It uses the existing five-minute producer cadence instead of adding a
separate system-level timer. If an update is available, the producer runs the
upgrade before reading DingTalk messages. Upgrade check or install failures are
recorded locally and notified, but they do not block message discovery for that
producer pass.

## DWS auth environment

The LaunchAgents run with `HOME=/Users/derek/Documents/memory` and force DWS onto
its file-backed credential store with `DWS_DISABLE_KEYCHAIN=1` plus
`DWS_KEYCHAIN_DIR=/Users/derek/Documents/memory/Library/Application Support/dws-cli`.
Without those DWS variables, a process under the memory home can report
`not_authenticated` even when the user's interactive shell still has a valid DWS
login. The diagnostic script `scripts/check-dws-auth-env.sh` reproduces the safe
boundary without touching the macOS native keychain: the correct file keychain
dir succeeds, while an empty file keychain dir fails with `not_authenticated`.

## Processing acknowledgement

The worker no longer sends `收到，我正在处理（by 分身）` before a final reply. Final
reply delivery is usually close enough that the extra acknowledgement adds noise.
Historical acknowledgement messages are still recognized and filtered from prompt
context and unanswered-mention checks, so earlier processing messages do not hide
messages that still need a real reply.

## Mentioned arrangements

When a human mentions Derek in a group and shares an arrangement, process, or
decision that needs Derek to participate or confirm, the agent should treat it as
reply-worthy even if the message is phrased as a statement rather than a
question. It should only skip when the later context shows Derek already
confirmed the arrangement.

Mention discovery starts from the recent global `@Derek` feed, not only from the
current unread conversation list. A mentioned group can therefore be processed
after the user opens the conversation and clears the unread badge. Later context
from the same conversation is used to decide whether Derek already gave a real
reply; rendered files, images, cards, calendar invites, and processing
acknowledgements do not count as a real reply.

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
