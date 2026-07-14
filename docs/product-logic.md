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

## Meeting Alignment

Meeting follow-up is an independent producer/consumer pipeline inside the same
`com.ceo-agent-service.main` process. There is no separate cron job or launchd
plist. The producer combines AI Minutes metadata with one uniquely matching
calendar event, so a job is eligible only when Derek attended and the meeting
has explicitly ended for at least ten minutes.

The agent remains silent unless it finds a material viewpoint disagreement or a
need for `Derek 的观点输出解读`. An aligned disagreement requires explicit
agreement, commitment, or consistent restatement by the relevant sides. For an
unresolved disagreement the output includes the parties' views and reasons plus
one or more minimum-sufficient tradeoff questions whose answers can directly
produce alignment. A disagreement that becomes aligned still produces the one
meeting follow-up; final alignment does not convert it to `no_action`. Derek's
explanation may use historical cases and the work profile only to clarify a view
that was actually expressed in the meeting. Work-profile source identifiers must
remain exact so the service can audit them.

The service persists a discovery activation timestamp. Before startup recovery,
all pre-activation jobs with no send evidence are baselined to terminal
`no_action`, including work that an older process had already claimed. This
prevents deployment-time recovery from analyzing or sending historical meetings.

Multi-party meetings are sent to the highest-ranked sendable group with real
mentions. They never fall back to a direct message. A 1:1 meeting sends directly
to the other participant. No DING or reaction is added by this workflow.

The queue persists analysis before delivery. `no_action` is terminal;
`ready_to_send` is persisted before any external send; `sent` records delivery.
Retryable failures use `retry` plus `available_at`; invalid persisted source or
delivery evidence and queue invariant violations use `failed`. Codex decision
schema and historical-source protocol violations are treated as retryable
model-output failures before the bounded attempt limit. An ambiguous send with
an `openTaskId` is reconciled by status lookup only, with bounded backoff and no
resend. In dry-run mode the consumer may analyze a job but does not claim
`ready_to_send` delivery.

Every Codex invocation appends an immutable `meeting_alignment_run`. The History
page merges these runs with reply attempts in one globally chronological feed,
including common search, status filters, pagination, event chart, detail view,
and Codex-session related history.

## Task Summary

The task summary system is project-centered, not inbox-centered. It records
company management items, business projects, important operating matters, and
action items that need owner attention. Obvious one-off conversations should not
be promoted into durable projects.

Each processed conversation can enqueue a compact Work Item with:

- `summary`
- `project_name`
- source conversation or document metadata
- owner hints
- timestamps

The task agent owns fact extraction. It receives BM25 project candidates built
from the Work Item summary and project name, then decides whether to update an
existing `work_project` or create a new one. If retrieval finds no stable
candidate, or candidates are present but the agent judges them mismatched, the
prompt allows the agent to recover context through DWS conversation reads or
Memory Connector. New projects should use `memory_recall` for historical
background before creation. If a stable project name still cannot be recovered,
the agent should generate a clarification follow-up instead of creating a vague
project.

`work_projects` store:

- title and category
- `background`
- owner, status, priority, risk level, source conversation
- `next_step`
- `facts`: a list of `description`, `source`, `created`, and `updated`

Supported categories are `management`, `strategy`, `projects`, `marketing`,
`research`, `dev`, `product`, `recruiting`, `sales`, `finance`, `admin`, `HR`,
and `other`.

TODOs live under projects. Due dates and priority are inferred from the concrete
context and OKR pressure rather than copied mechanically. P0/P1/P2 work should
normally become same-day, three-day, or same-week follow-up pressure when the
source material does not give a clearer deadline.

The `/tasks` audit UI is project-first. The list page shows the active project
queue, project status, category filtering, Priority/Risk sorting, TODO checklist
preview, open TODO ratio, real-time full-text search over project and TODO
context, and paginated navigation. Each project links to `/tasks/{project_id}`,
where the detail page shows project background, facts, all TODOs with DDL and
owner, project updates, and follow-up records.

Completion can be inferred automatically from later messages, meetings, or
documents when the evidence is explicit. If an item is due and still open, the
task follow-up path sends the drafted question when live sending is enabled. It
uses the originating group only when that group conversation is known; otherwise
it sends a direct message to the resolved owner. Risk annotations on the draft
are audit context only and do not create a separate approval gate. Drafts more
than seven days past due are skipped instead of sent, because their context is
too stale for a useful reminder. Owner replies then enter the existing CEO reply
path, so follow-up does not need a separate reply engine.

## Safety Defaults

- `CEO_NOT_SEND_MESSAGE=1` by default. `CEO_DRY_RUN` remains a compatibility
  alias for older scripts.
- Runtime state lives under `data/` and is ignored by Git.
- Live sends require explicit opt-in.
- Task follow-up commands are send-capable commands and therefore use the same
  live-send guard as normal reply delivery.
- Local task source scanning is limited to the configured `CEO_WORKSPACE` path.
- DingTalk media/calendar placeholders and DingTalk internal link-only cards are
  skipped before Codex, except approval/OA links.
- OA approval cards and reminders are routed to the OA handler. The handler uses
  the unified structured Codex runner with `dingtalk-oa-approval` injected,
  records the Codex session, tool events, approval URL, approval action,
  approval remark, and action result on the existing reply attempt audit row,
  and does not create a separate OA audit page.
- The OA handler may use authorized DingTalk OA API detail reads when DWS does
  not return complete approval detail. Secrets and signed URLs must not be
  written to logs, SQLite, audit summaries, reports, or DingTalk replies.
- See `docs/message-routing-rules.md` for the full message-type inventory,
  implemented regexes, candidate regexes, and message types that should remain
  agent-reviewed.
