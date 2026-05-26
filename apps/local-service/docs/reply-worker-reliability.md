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

## Processing acknowledgement

The worker may send `收到，我正在处理（by 分身）` before a final reply, but only
after Codex has returned a decision that will actually attempt a reply. `no_reply`,
`stop_with_error`, blocked, and dry-run outcomes do not send the acknowledgement,
so a conversation is not left with a processing message and no follow-up.

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

When the maximum is reached, the task is marked `failed`, the final error is
recorded, and a local notification is sent.

Processing tasks older than the stale-task threshold are also moved back to
`pending`; this recovery path sends a local notification so the operator can see
that an interrupted task was retried.
