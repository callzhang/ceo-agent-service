# Product Logic

The service treats DingTalk as the primary conversation surface and keeps
retrieval, generation, audit, and feedback local.

## Input

Each worker pass asks `dws` for unread conversations. For each conversation it
reads:

- recent context before the unread cursor
- unread messages after the cursor
- linked DingTalk documents when a message contains an Alidocs URL

Direct chats are treated as addressed to the configured principal. Group chats
must explicitly mention the principal before they become candidates.

## Decision

The worker sends one batch of unread messages to Codex. Codex decides whether
the batch needs one response, no response, clarification, human handoff, or an
error stop.

The batch may contain multiple direct-chat messages. These messages are a single
conversation turn, not independent tickets. Codex should decide whether the
latest unread batch as a whole requires a response and should cover every
important item in one reply when needed.

## Retrieval

Before answering substantive questions, Codex is instructed to:

1. Read `graphify-out/GRAPH_REPORT.md`.
2. Use `graphify query`, `graphify explain`, or `graphify path` to find related
   workspace knowledge.
3. Use `rg` and file reads to verify evidence.
4. Read DingTalk documents through `dws doc read` when document links appear.

Replies must not expose local file paths, source citations, session ids, or
tool output details.

## Privacy

The decision schema classifies each message as:

- `general`
- `internal_personnel`
- `external_candidate`

Internal personnel discussions are sensitive and must be refused unless the
operator has explicitly configured permission rules for that deployment.
External candidate discussions may be answered when the relevant role and
department context are available.

## Handoff

If the sender asks for the real human, rejects the automated response, or asks
the agent to claim a real-world action that only the human can perform, the
decision should be `handoff_to_human`.

Handoff sends a short acknowledgement in DingTalk and uses DING to notify the
operator. If DING is unavailable, it falls back to the local Chrome notification
bridge so the acknowledgement is not marked failed just because the operator
alert channel is exhausted. The handoff remains active until the worker observes
a real manual reply from the operator in the same conversation. Live runs send a
local pause notification when new unread messages arrive during active handoff;
dry-run checks suppress that pause notification because they intentionally do not
mark messages as seen.

## Audit

Every attempt is stored locally, including:

- trigger message
- action
- draft and final reply
- send status and send error
- audit summary
- documents and tool events used for review
- Codex session id and transcript line range when available
- reviewer feedback and corrected reply

The audit summary is a concise explanation of evidence and applied rules. It is
not hidden chain of thought.

## Safety Defaults

- `CEO_NOT_SEND_MESSAGE=1` by default. `CEO_DRY_RUN` remains a compatibility
  alias for older scripts.
- Runtime state lives under `data/` and is ignored by Git.
- Live sends require explicit opt-in.
- DingTalk media/calendar placeholders and DingTalk internal link-only cards are
  skipped before Codex, except approval/OA links.
- OA approval cards and reminders are routed to a dedicated OA approval agent.
  The service injects the `dingtalk-oa-approval` skill into that agent, records
  the Codex session, tool events, approval URL, approval action, approval remark,
  and action result on the existing reply attempt audit row, and does not create
  a separate OA audit page.
- The OA agent may use authorized DingTalk OA API detail reads when DWS does not
  return complete approval detail. Secrets and signed URLs must not be written
  to logs, SQLite, audit summaries, reports, or DingTalk replies.
- See `docs/message-routing-rules.md` for the full message-type inventory,
  implemented regexes, candidate regexes, and message types that should remain
  agent-reviewed.
