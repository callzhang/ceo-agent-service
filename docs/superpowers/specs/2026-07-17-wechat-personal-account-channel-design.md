# WeChat Personal Account Channel Design

Date: 2026-07-17

## Decision Summary

Add a WeChat channel for Derek's existing personal-account contacts by splitting
the transport into two independently guarded capabilities:

- read incoming and historical messages from read-only snapshots of the local
  Mac WeChat databases;
- send text through the signed, installed WeChat application's visible user
  interface by macOS Accessibility automation.

The channel reuses the existing CEO Agent reply queue, decision engine, Memory
retrieval, handoff controls, and audit history. It does not create a second
reply agent.

Tencent iLink is not part of the first release. It exposes a separate Bot
identity and does not provide Derek's existing personal contacts, personal
chat history, or personal-account sending. Keep the iLink research as a future
channel option only.

## Goals

- Receive new messages from Derek's existing WeChat friends and reply as
  Derek's personal WeChat account.
- Support arbitrary existing one-to-one contacts, subject to explicit runtime
  allow/deny and handoff rules rather than a hard-coded test contact.
- Use recent local chat context, approved durable Memory, and Derek's existing
  reply style when deciding whether and how to respond.
- Make automatic operation idempotent, observable, and fail-closed when either
  reading or sending is uncertain.
- Provide a separate one-shot import that scans historical messages for useful
  information, cleans the candidates, and writes a local review table. Only
  human-approved candidates may enter durable Memory.
- Never modify the original WeChat databases or persist their decryption key.

## Non-Goals

- Using iLink Bot as a substitute for Derek's personal account.
- Implementing a private WeChat network protocol, process hook, injected send
  function, or background sender that bypasses the visible WeChat application.
- Automatically importing raw conversations into durable Memory.
- Automatically writing runtime reply conversations into durable Memory.
- Fully supporting group replies, voice, images, files, calls, reactions,
  mini-programs, or message recall in the first release.
- Guaranteeing operation across future WeChat releases without a validated
  version-specific reader capability.

## Release Scope

Version one supports text in existing one-to-one personal-account chats.
Incoming group, system, unsupported-media, or ambiguously parsed records may be
recorded for bounded audit, but they cannot trigger an automatic send.

The first live proof uses File Transfer Assistant or another contact explicitly
approved for testing. After that proof passes, the same adapter may cover other
existing one-to-one contacts. Contact-level policy can still force `ignore`,
`draft`, or `handoff` without changing the transport.

## Architecture

```text
Mac WeChat DB files
  -> WeChatReadAdapter
  -> normalized message event + watermark
  -> existing reply_tasks queue
  -> existing CEO decision worker + approved Memory retrieval
  -> existing send/handoff policy
  -> WeChatAccessibilitySender
  -> visible personal-account WeChat chat

Historical DB snapshots
  -> WeChatMemoryImportJob (manual one-shot)
  -> cleaned local review table
  -> human approve/edit/reject
  -> approved-only Memory writer
```

### `WeChatReadAdapter`

The reader owns local account discovery, capability detection, snapshotting,
decryption, schema parsing, normalization, and read watermarks. It emits
channel-neutral events and has no access to the reply model or sender.

Its capability contract is explicit:

- `ready`: the installed WeChat version, key provider, database cipher
  parameters, and schema parser have all passed validation;
- `blocked`: a required version-specific capability is unavailable;
- `failed`: a previously supported capability encountered corrupt or
  inconsistent evidence.

The current target is Mac WeChat `4.1.10.80`. Static inspection proves that its
framework contains SQLCipher/WCDB interfaces, and an isolated re-signed debug
copy can be attached for diagnostics, but the database key has not yet been
recovered by the tested legacy scanners. Therefore the reader starts
`blocked`, not partially enabled, until a current-version key provider and
schema probe pass the acceptance tests.

The key provider is an interchangeable version-specific boundary. It may
inspect the running, user-authorized WeChat process when necessary, but it must
return key material only to the in-memory reader operation. Re-signing the
installed production WeChat application is not a runtime dependency. Any
diagnostic re-signed copy remains isolated from the user's installed app and is
not used to send real messages.

### Snapshot and decryption contract

For each scan, the reader:

1. discovers only the configured local account directory;
2. copies the selected database together with matching WAL and SHM state into
   a restricted temporary snapshot;
3. opens only the snapshot, never the original file;
4. applies the in-memory key and validated cipher parameters;
5. verifies database integrity and expected schema before reading messages;
6. normalizes supported records and discards the temporary snapshot and key
   material after the operation.

An inconsistent snapshot is retried from the beginning. The reader must not
repair, migrate, checkpoint, or otherwise write to a source WeChat database.
Temporary artifacts are permission-restricted and removed on success, failure,
and startup cleanup.

### Normalized message contract

Each supported message becomes an internal event with at least:

```json
{
  "channel": "wechat",
  "account_id": "stable-local-account-id",
  "conversation_id": "stable-conversation-id",
  "message_id": "stable-source-message-id",
  "sender_id": "stable-source-sender-id",
  "sender_display_name": "display name at read time",
  "direction": "inbound",
  "sent_at": "2026-07-17T10:00:00+08:00",
  "kind": "text",
  "text": "message body",
  "source_version": "4.1.10.80"
}
```

`message_id` plus the channel/account identity is the idempotency key. A
per-database high-water mark accelerates incremental scans but never replaces
message-level deduplication. Outbound records update context and delivery
reconciliation; they do not create reply tasks.

### `WeChatReplyProducer`

The producer converts eligible inbound events into the existing `reply_tasks`
contract with `channel=wechat`. It preserves the existing task lifecycle,
decision schema, work-profile routing, notification behavior, and immutable run
history.

Before queue creation it applies deterministic gates:

- direction is inbound;
- conversation is an identified one-to-one personal chat;
- content is supported text;
- message is newer than the activation watermark unless selected by an
  explicit bounded replay;
- the source event has not already produced a task;
- the conversation is not paused, denied, handed off, or still awaiting a
  prior uncertain send outcome.

The agent then uses bounded recent context from the same conversation and
approved durable Memory. The transport does not itself decide what to say.

### `WeChatAccessibilitySender`

The sender controls only the official installed WeChat application through
macOS Accessibility. It sends as the logged-in personal account because it
operates the same visible composer Derek would use manually. No AppleScript
dictionary or public WeChat send API is assumed; AppleScript may orchestrate
System Events, while Accessibility supplies the application UI interaction.

For every send, the sender:

1. brings WeChat to the foreground and verifies the expected application and
   logged-in local account;
2. resolves the exact target using the stable conversation metadata and the
   visible chat identity, never display-name-only best effort;
3. verifies that the selected conversation matches the intended recipient;
4. enters the prepared text without interpreting it as keyboard automation;
5. performs one send action;
6. confirms success from both visible conversation evidence and the next local
   database scan when available;
7. persists delivery evidence before marking the task `sent`.

If target resolution, selection, composer state, or post-send evidence is
ambiguous, the sender stops. An action that might have sent becomes
`send_unknown` and is never blindly retried. It must be reconciled from the
conversation or handled manually. Only a failure proven to have occurred
before the send action is eligible for automatic retry.

Because Accessibility is UI automation, the Mac user session must be unlocked,
WeChat must be running and logged in, and the service host must retain the
required Accessibility permission. Loss of these conditions blocks delivery
without changing the reply decision.

## Runtime Flow

1. The reader polls validated local snapshots for records newer than its saved
   watermark, with a small overlap for late WAL visibility.
2. It normalizes and deduplicates records.
3. The producer creates one reply task per eligible inbound message.
4. The existing worker gathers recent conversation context and approved
   Memory, then returns the existing structured decision: ignore, reply,
   handoff, or failure.
5. Existing live-send gates determine whether a reply remains a draft or may
   enter `ready_to_send`.
6. The sender moves one task to `sending`, resolves and verifies the target,
   sends once, then stores confirmation as `sent` or ambiguity as
   `send_unknown`.
7. The next read pass observes the outbound record and associates it with the
   delivery evidence without creating a new reply task.

The channel must preserve ordering within a conversation. A later reply cannot
overtake a task in `sending`, `send_unknown`, retry, or handoff for the same
conversation.

## Persistence and Data Minimization

Use the existing service SQLite database for operational state. Add only the
minimum channel-specific state needed for safe operation:

- `wechat_read_state`: account/database identity, source version, watermark,
  last successful scan, and capability status;
- source identifiers on existing reply tasks/runs for deduplication and audit;
- WeChat delivery evidence on the existing delivery/run model;
- `wechat_memory_candidates` and review events for the one-shot import.

Do not store the database key, decrypted database snapshots, passwords,
verification codes, attachment bodies, or full historical transcripts in the
service database. Runtime reply tasks may retain only the existing bounded
message/context content required for decision audit and must follow the
service's retention policy.

## One-Shot Memory Import

Memory import is a manual, bounded CLI job, not a background daemon feature and
not part of normal reply processing. It accepts an account, conversation/date
scope, and maximum record count. It requires a `ready` reader and operates on
read-only snapshots.

The import pipeline is:

1. select messages within the explicit scope;
2. remove unsupported payloads, credentials, verification codes, and obvious
   non-durable conversational noise;
3. extract concise candidate facts such as confirmed decisions, commitments,
   project state, deadlines, working preferences, relationships needed for
   work, and reusable experience;
4. deduplicate candidates against each other and approved Memory;
5. merge compatible updates and flag contradictions rather than choosing one
   silently;
6. classify confidence and sensitivity;
7. write candidates to the local review table with status `pending`.

The review table contains:

- candidate ID and canonical cleaned statement;
- category, confidence, and sensitivity;
- conversation identity, source message IDs, and source time range;
- a minimal redacted evidence excerpt when necessary for review;
- duplicate, merge, contradiction, and redaction notes;
- reviewer-editable statement;
- status `pending`, `approved`, `rejected`, or `revoked`;
- reviewer, review timestamp, Memory write status, and resulting Memory ID.

The review surface supports bulk reject but not bulk approve by default. A
separate explicit command writes only `approved` rows to Memory, records the
returned Memory identifier, and is idempotent. Editing or approving a candidate
never writes its raw source transcript. Revocation records an audit event and,
where the Memory backend supports deletion or supersession, applies the matching
operation; otherwise it marks the limitation visibly for manual handling.

## Configuration and Operational Controls

Channel configuration must expose:

- enabled/disabled reader and sender capabilities independently;
- exact local WeChat account directory and expected account identity;
- polling interval and bounded context window;
- activation watermark;
- global live-send gate plus contact-level allow, draft, deny, and handoff
  policy;
- temporary snapshot location and retention cleanup interval;
- current reader capability and last verified WeChat version.

Safe defaults are reader disabled, sender disabled, and no historical replay.
Enabling the reader does not enable sending. Enabling sending still requires
the project's existing live-send acknowledgement and successful Accessibility
preflight.

## Failure Handling

- Unsupported WeChat version, missing key provider, or schema mismatch sets the
  reader to `blocked` and creates no reply tasks.
- Snapshot inconsistency retries from a fresh copy without advancing the
  watermark.
- A malformed record is quarantined with source identifiers; it does not block
  unrelated valid records unless ordering for the same conversation is unsafe.
- Agent and Memory failures use existing bounded retry and handoff behavior.
- Missing Accessibility permission, locked session, logged-out WeChat, or
  unverified target blocks the send before the action.
- Uncertain post-action delivery becomes `send_unknown`, pauses that
  conversation, and requires reconciliation; it is not retried automatically.
- A confirmed `sent` task is immutable and never replayed.
- Restart recovery reconciles `sending` tasks before performing any new send.

All failures remain visible in History or channel health with enough evidence
to distinguish read capability, parsing, decision, policy, UI automation, and
delivery-confirmation failures without exposing key material or full private
messages.

## Testing

### Reader tests

- discovery selects only the configured account;
- snapshot handling includes matching DB, WAL, and SHM state and never opens
  the source database for writing;
- key material is absent from logs, exceptions, persistence, and fixtures;
- wrong key/cipher parameters fail integrity checks without emitting messages;
- current-version schema fixtures normalize direction, timestamps,
  conversation IDs, message IDs, sender identities, and text correctly;
- overlapping scans and restarts produce no duplicate events;
- unsupported versions and schema drift become `blocked`.

### Producer and decision tests

- only inbound one-to-one text creates a v1 reply task;
- outbound, group, system, unsupported-media, pre-activation, denied, and
  duplicate events do not create automatic reply tasks;
- recent context is restricted to the same conversation;
- WeChat tasks use the existing decision, handoff, audit, and live-send gates;
- later messages cannot overtake unresolved work for the same conversation.

### Sender tests

- exact target and selected-chat verification are required before input;
- ambiguous or duplicate display names fail closed;
- prepared text is inserted as text and cannot trigger extra UI actions;
- pre-action failure may retry, while post-action ambiguity becomes
  `send_unknown`;
- confirmed sends persist evidence and are never duplicated after restart;
- Accessibility permission loss, locked session, logout, and unexpected UI
  layout are reported distinctly.

### Memory import tests

- an explicit bounded scope is required;
- credentials and verification codes are rejected;
- duplicates merge, contradictions remain visible, and sensitive candidates are
  flagged;
- no raw transcript is written to Memory;
- pending, rejected, and revoked rows cannot be written as new Memory;
- approved writes are idempotent and retain source/audit linkage locally.

### Runtime verification

1. Validate a current-version key provider against a temporary snapshot and
   prove that no key or decrypted snapshot remains afterward.
2. Read and manually compare the most recent 100 messages from File Transfer
   Assistant or an approved test conversation, including order, direction,
   timestamps, text, and repeated-scan deduplication.
3. Run the entire incoming-message-to-decision pipeline in dry-run mode and
   inspect the persisted History result without sending.
4. Run Accessibility preflight and send one fixed test message to File Transfer
   Assistant; verify visible receipt and the matching outbound database record.
5. Trigger one controlled incoming test and confirm that it produces exactly
   one automatic reply through the normal queue and sender.
6. Run a bounded one-shot Memory import in dry-run/review mode, approve one
   non-sensitive test candidate, write it once, and prove that rerunning does
   not duplicate it.
7. After runtime-code commits, restart `com.ceo-agent-service.main`, verify a
   new running process, and confirm no unresolved `failed`, `processing`,
   `sending`, or `send_unknown` backlog before broadening contact scope.

## Rollout

1. **Reader capability:** implement the adapter boundary and validate a
   current-version `4.1.10.80` key provider, snapshot, integrity probe, and
   schema parser. Stop here if the capability remains blocked.
2. **Bounded read proof:** read and verify the latest 100 messages from the
   approved test conversation without creating reply tasks.
3. **Dry-run integration:** connect normalized events to the existing reply
   producer and decision worker with sending disabled.
4. **Controlled send proof:** enable the Accessibility sender only for File
   Transfer Assistant or the approved test contact and complete one exact-once
   end-to-end reply.
5. **One-to-one rollout:** enable other existing direct contacts under explicit
   policy, initially with monitored low volume and immediate handoff controls.
6. **Memory import:** run the separately invoked historical scan, review the
   cleaned local table, and write only approved candidates.

Each phase has its own enable flag and can be rolled back without changing the
source WeChat database or deleting audit history.

## Acceptance Criteria

- The current installed WeChat version can be classified accurately as
  `ready`, `blocked`, or `failed`; automatic processing never starts from a
  guessed capability.
- A validated read pass returns the latest 100 approved-test messages with
  correct order, direction, timestamp, conversation, sender, and text, and a
  repeated pass emits no duplicates.
- Source WeChat databases remain byte-for-byte untouched by the service, and no
  database key or decrypted snapshot persists after the operation.
- One controlled incoming message creates exactly one existing-style reply task
  and one auditable decision.
- One approved automatic reply is sent exactly once to the intended existing
  personal-account contact and is confirmed both visibly and from local
  outbound evidence.
- An ambiguous target or uncertain send produces no blind retry and pauses the
  affected conversation.
- Existing handoff, live-send, History, notification, and Memory-retrieval
  behavior continues to apply to WeChat tasks.
- The one-shot import produces a cleaned, deduplicated, source-linked local
  review table, and only explicitly approved rows can be written to durable
  Memory.
- iLink credentials, contacts, and messages are not required for or mixed into
  the personal-account channel.
- Completion leaves the launchd service healthy and no unresolved channel or
  reply backlog.
