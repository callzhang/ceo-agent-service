# Unified Service Message Postfix Design

**Status:** approved design, pending implementation

## Purpose

Every text message that CEO Agent Service actually sends to a person through
DingTalk or WeChat must carry one service-owned postfix.  Today signatures and
feedback links are attached by several unrelated code paths, and structured
Agent actions can bypass a command-shaped-only formatter.  A new sender must
not be able to omit the postfix by accident.

Email is explicitly out of scope.  The current Email lifecycle does not send
mail; this design must not enable SMTP, mail replies, or `mailto` as a side
effect.

## Invariants

1. A DingTalk or WeChat human-facing text delivery never reaches its provider
   adapter as a raw body.  It is represented first as a prepared outbound
   message.
2. The prepared message contains the canonical service signature and, when the
   configured feedback service is enabled, exactly one complete 👍/👎 feedback
   link pair.
3. The postfix is idempotent.  Retrying, resuming, quoting, or reading a
   previously prepared body does not append a second signature or link pair.
4. One logical delivery has one stable feedback token and one immutable final
   body.  A retry reuses both; it does not generate a new link or mutate the
   body that was sent or is about to be sent.
5. Consumer/Audit business boundaries do not change.  Postfix construction is
   mechanical transport preparation, not a business rewrite, additional audit
   gate, or new task state.
6. A new service-owned DingTalk or WeChat sender that bypasses the unified
   boundary fails an architecture test.

## Architecture

```text
business / Agent proposal / follow-up / meeting / report / WeChat delivery
                                |
                                v
                  ServiceMessageSender.prepare(...)
                                |
                                v
                    OutboundPostfixPolicy
                  (signature + feedback pair)
                                |
                                v
                immutable outbound delivery record
                                |
                  +-------------+-------------+
                  |                           |
                  v                           v
           DingTalk provider adapter     WeChat provider adapter
```

`DwsClient` and the WeChat IPC/Accessibility sender remain provider adapters.
They do not decide whether to append a signature or a feedback link.  Business
code must use `ServiceMessageSender`; it cannot hand arbitrary text directly to
those adapters.

### Prepared message contract

Introduce a small, transport-neutral model owned by the service, conceptually:

```text
OutboundMessage
  channel: dingtalk | wechat
  delivery_key: stable, caller-owned logical-delivery identity
  body: candidate text
  original_text: optional triggering text for the feedback callback
  references: optional non-secret task/attempt/run identity

PreparedOutboundMessage
  channel
  delivery_key
  final_body
  feedback_token
  postfix_version
```

`ServiceMessageSender.prepare` is the only constructor for a
`PreparedOutboundMessage`.  It first loads any existing prepared record for
`(channel, delivery_key)`.  If one exists, the stored final body and token are
returned verbatim.  Otherwise the policy appends the signature and the
configured feedback pair once, then persists the immutable record before a
provider call is made.

The record stores only the final body, token, channel, delivery key, postfix
version, timestamps, and non-secret references.  It never stores provider
credentials, full callback query strings as separate fields, or copied private
message material beyond the existing delivery body already needed for retry and
readback.

This record supplies stable identity where an existing subsystem does not
already persist final outbound text.  Existing `sent_replies` and
`wechat_deliveries` continue to be projections/receipts; their final text must
equal the prepared body.

### DingTalk integration

All service-owned DingTalk text paths use the same sender facade:

- Consumer/Audit accepted message actions, including old command-shaped actions
  and typed direct, group, and quoted replies;
- scheduled follow-ups;
- meeting-alignment delivery;
- weekly OKR group summaries;
- the feedback-spike CLI's real send path.

The stable `delivery_key` is derived from the path's existing idempotency
identity: task execution-generation/proposal action for Agent messages,
follow-up revision UUID, meeting delivery identity, weekly report identity, or
the CLI's explicit idempotency identity.  A path without a stable identity must
not send; it must first create one through its existing persistence contract.

Agent output is prepared before the Audit Agent receives the candidate.  Thus
Audit sees the literal final body that the provider adapter will send.  The
postfix is service-owned and not subject to Agent rewriting; no extra audit
round is introduced.

### WeChat integration

WeChat delivery preparation occurs when the service creates or updates a
`ready_to_send` delivery, before it is claimed by automatic mode or approved in
confirm mode.  Its existing delivery ID is the stable `delivery_key`.

The persisted `wechat_deliveries.reply_text`, sender IPC request, Accessibility
readback comparison, and recall lookup all use `PreparedOutboundMessage.final_body`.
This keeps the body seen by the user, the body used to detect the same message,
and the body eligible for recall identical.  A delivery that was already
prepared before a restart must reuse its stored final body rather than append a
new postfix.

## Enforcement against bypasses

The implementation enforces the boundary in two layers:

1. Provider adapters expose raw provider operations only to the unified sender
   module and their narrow test/infrastructure allowlist.  First-party business
   modules call the facade, not `DwsClient.send_message`, `DwsClient.reply_message`,
   `WechatSender.send`, or direct IPC send methods.
2. An architecture test scans first-party application modules and rejects newly
   introduced direct calls to those provider send methods outside the explicit
   adapter allowlist.  It also verifies that every supported current sender
   path has a focused behavior test showing its final text contains one
   signature and one feedback pair.

Runtime guardrails remain defensive: the provider-facing facade requires a
`PreparedOutboundMessage`, so ordinary strings cannot pass through its public
send method.  The architecture test is the change-time protection for future
code additions.

## Error, retry, and compatibility behavior

- Preparing is atomic and idempotent by `(channel, delivery_key)`.
- A provider error after preparation leaves the immutable prepared record for
  the ordinary existing retry/recovery lifecycle; retries reuse it exactly.
- A missing feedback base URL still applies the signature but leaves the
  feedback token and link pair empty.  The prepared record makes that decision
  stable for its delivery rather than changing mid-retry if configuration later
  changes.
- Existing messages are not retroactively edited.  New deliveries after the
  deployment use the unified policy.
- Non-human technical probes, tests, previews, and read-only provider checks
  do not create outbound messages and do not receive a postfix.

## Test plan

Focused tests will cover:

1. policy composition with configured and disabled feedback URLs;
2. atomic preparation and stable retry results for the same delivery key;
3. DingTalk typed direct, group, quoted reply, old command-shaped Agent action,
   follow-up, meeting, weekly report, and feedback-spike sender paths;
4. WeChat automatic and manually approved delivery, readback, retry, and recall
   using one persisted final body;
5. no Email delivery behavior changes;
6. the no-bypass architecture scan and its allowlist.

Runtime acceptance requires a restart of `com.ceo-agent-service.main`, a new
PID, healthy local HTTP response, and zero `processing`/`failed` backlog before
the related feedback item is resolved.  No production message will be sent
merely to test this behavior; unit and controlled local delivery tests prove
composition without creating unsolicited external messages.
