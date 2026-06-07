# CEO Agent Task Summary Design

Date: 2026-06-07

## Goal

Add a task summary system that records company management matters, business
projects, and important company work as durable projects with TODOs. The system
should summarize work from DingTalk conversations, AI minutes, local workspace
files, and targeted memory recall, then keep project state, facts, TODOs, and
follow-up messages current.

This is not a replacement for the existing reply queue. Existing `reply_tasks`
remain the message-processing queue. The new task system stores business
projects and TODOs, linked back to reply attempts, messages, AI minutes, files,
and memory evidence.

## Current Context

The service already has:

- DingTalk message discovery and routing.
- `reply_tasks` and `reply_attempts` for message handling and audit.
- Codex decision sessions with `memory_connector` MCP access.
- Prompt rules that make `memory_recall` the first background source for
  business, people, project, customer, approval, calendar, or historical context.
- A local audit web UI.
- DWS access to messages, AI minutes, documents, calendar, org profiles, and
  message sending.

The new system should reuse those surfaces, but keep task management separate
from reply generation so project tracking does not make the main reply prompt
heavier or less reliable.

## Scope

In scope:

- Create and update projects from processed conversations.
- Daily scan newly available AI minutes.
- Daily scan new or updated files only under `CEO_WORKSPACE`.
- Use targeted `memory_recall` for existing project background and for new
  project exploration.
- Use BM25 retrieval over existing projects to provide candidates to the task
  agent.
- Create, update, close, and cancel project TODOs.
- Automatically mark TODOs done when messages, minutes, files, OKR updates, or
  documents clearly show completion.
- Generate owner follow-up drafts, and auto-send low-risk follow-ups.
- Add a `/tasks` page to review projects, TODOs, updates, and follow-up drafts.
- Add setup checks for `memory_connector` MCP availability in Codex and Claude
  MCP configuration.

Out of scope for the first implementation:

- Replacing existing CEO reply behavior.
- Scanning files outside `CEO_WORKSPACE`.
- Full-text duplication of sensitive local files into task tables.
- Treating memory as an enumerable daily feed. Memory is searched by project,
  owner, tag, facts, or task-agent exploration terms.

## Architecture

Use an independent `task agent`.

Flow:

1. Existing CEO reply processing runs normally.
2. After a conversation or specialized handler finishes, the service creates a
   `Work Item` from the processed result.
3. Daily scanners create `Work Item` records from new AI minutes and new or
   updated files under `CEO_WORKSPACE`.
4. The system uses `summary + project_name` to BM25-search existing projects.
5. The task agent receives the Work Item plus top project candidates.
6. The task agent decides whether to discard the item, update an existing
   project, create a project, change TODOs, update facts/background/current
   state, or schedule follow-up.
7. Follow-up scheduling creates a draft by default. Low-risk drafts can be sent
   automatically.
8. Owner replies continue through the existing CEO reply path. After that reply
   attempt completes, the task system processes the resulting Work Item and
   updates the project.

Code should only own deterministic infrastructure: queueing, source watermarks,
BM25 retrieval, context packaging, schema validation, persistence, sending, and
audit. The semantic choices belong to the task agent prompt: whether a Work Item
is important, whether it matches an existing project, how to update facts, and
which TODOs changed.

## Work Item

`Work Item` is intentionally small. It is a pending information fragment for the
task agent, not a pre-decided project update.

Shape:

```json
{
  "source": {
    "type": "reply_attempt | ai_minutes | local_file | memory_recall",
    "ref": "",
    "title": "",
    "conversation_id": "",
    "conversation_title": "",
    "created_at": ""
  },
  "summary": "Company management, business project, or important work content in this input.",
  "project_name": "Best stable project or matter name if present; empty if unclear.",
  "context": {
    "sender": "",
    "participants": [],
    "source_conversation_kind": "group | direct | file | minutes | memory",
    "source_conversation_title": ""
  }
}
```

Do not include `project_candidates`, `todo_candidates`, or extracted `facts` in
the Work Item. The task agent extracts facts and TODOs after seeing retrieved
candidate projects and any extra context it chooses to gather.

## Project Retrieval

Before a task agent run, the service searches existing projects using BM25.

Query:

- `Work Item.summary`
- `Work Item.project_name`

Candidate material passed to the task agent:

- project id, title, category, tags
- owner and related people
- goal and background
- facts
- current state, blocker, next step
- open TODOs
- source conversations
- recent updates

BM25 candidates are only initial hints. If BM25 finds no candidate, or candidates
exist but the task agent judges them mismatched, the task agent may recover more
context before deciding.

Recovery options:

- Use DWS to read more source conversation context, quoted messages, related
  group messages, or AI minutes details.
- Use `memory_recall` with project name, owner, tags, keywords, or similar
  historical matters.
- Use known project tags, facts, owners, or source groups for a more precise
  second retrieval.

This is a prompt guideline, not a hard requirement. If the Work Item is already
clear, the task agent can act directly. If retrieval cost is not justified, it
can create a follow-up question. If the item remains important but cannot be
classified stably, it should ask the sender or owner for the project/goal/owner
instead of creating a vague project.

## Task Agent Decisions

The task agent outputs validated JSON:

```json
{
  "action": "discard | create_project | update_project",
  "discard_reason": "",
  "project": {
    "id": null,
    "title": "",
    "category": "management | strategy | projects | marketing | research | dev | product | recruiting | sales | finance | admin | HR | other",
    "tags": [],
    "status": "active | waiting | done | archived",
    "priority": "P0 | P1 | P2 | none",
    "risk_level": "none | low | medium | high",
    "needs_derek_attention": false,
    "owner_user_id": "",
    "owner_name": "",
    "related_people": [],
    "goal": "",
    "background": "",
    "facts": [
      {
        "description": "",
        "source": "",
        "created": "",
        "updated": ""
      }
    ],
    "current_state": "",
    "blocker": "",
    "next_step": "",
    "next_follow_up_at": "",
    "follow_up_mode": "auto | draft | none",
    "source_conversations": []
  },
  "todo_changes": [
    {
      "action": "create | update | close | cancel",
      "todo_id": null,
      "title": "",
      "owner_user_id": "",
      "owner_name": "",
      "status": "open | waiting_owner | done | cancelled",
      "priority": "P0 | P1 | P2 | none",
      "deadline_at": "",
      "next_follow_up_at": "",
      "follow_up_question": "",
      "completion_evidence": null,
      "blocker": ""
    }
  ],
  "follow_up_drafts": [
    {
      "todo_id": null,
      "owner_user_id": "",
      "owner_name": "",
      "target_conversation_id": "",
      "target_kind": "group | direct",
      "question_text": "",
      "scheduled_at": "",
      "risk_check": {}
    }
  ],
  "update_summary": "",
  "merge_reason": "",
  "memory_recall_used": true,
  "confidence": 0.0
}
```

Merge guidance belongs in the task agent prompt:

- Match by stable project or matter name, not just surface wording.
- Compare owner, related people, source groups, tags, OKR/customer/project
  references, facts, and background.
- Treat project categories as fixed coarse buckets and put finer meanings in
  tags.
- New projects require memory recall when memory is available.
- Existing projects do not require recall for obvious incremental status
  updates, but recall is recommended for new owner, new risk, unclear history,
  or cross-project relationship.
- Discard items with no new fact, decision, risk, owner, deadline, blocker,
  next step, or state change.

## Data Model

### `work_projects`

Main project table.

- `id`
- `title`
- `category`: fixed enum
  - `management`
  - `strategy`
  - `projects`
  - `marketing`
  - `research`
  - `dev`
  - `product`
  - `recruiting`
  - `sales`
  - `finance`
  - `admin`
  - `HR`
  - `other`
- `tags_json`
- `status`: `active / waiting / done / archived`
- `priority`: `P0 / P1 / P2 / none`
- `risk_level`: `none / low / medium / high`
- `needs_derek_attention`
- `owner_user_id`
- `owner_name`
- `related_people_json`
- `goal`
- `background`
- `facts_json`: list of `{description, source, created, updated}`
- `current_state`
- `blocker`
- `next_step`
- `next_follow_up_at`
- `follow_up_mode`: `auto / draft / none`
- `source_conversations_json`
- `memory_context_json`
- `created_at`
- `updated_at`
- `last_activity_at`

`background` is stable context explaining why the project exists and what its
history is. The task agent may rewrite it into a clearer summary.

`facts_json` is the confirmed fact chain. Facts can be appended or updated with
source evidence. They should not be deleted without evidence. If a prior fact is
overturned, append or update a sourced fact that explains the change.

### `work_todos`

Action items under projects.

- `id`
- `project_id`
- `title`
- `owner_user_id`
- `owner_name`
- `status`: `open / waiting_owner / done / cancelled`
- `priority`: `P0 / P1 / P2 / none`
- `deadline_at`
- `next_follow_up_at`
- `follow_up_question`
- `blocker`
- `completion_evidence_json`
- `created_from_update_id`
- `created_at`
- `updated_at`
- `completed_at`

Completion can be automatic. If messages, AI minutes, files, OKR updates, or
documents clearly show completion, the task agent marks the TODO `done` and
writes completion evidence.

### `work_updates`

Project timeline.

- `id`
- `project_id`
- `source_type`: `reply_attempt / ai_minutes / local_file / memory_recall / manual`
- `source_ref`
- `summary`
- `changes_json`
- `merge_reason`
- `confidence`
- `created_at`

Every task-agent project or TODO change should create a timeline update.

### `work_summary_inputs`

Task-agent input queue.

- `id`
- `source_type`
- `source_ref`
- `payload_json`
- `status`: `pending / processing / done / failed / discarded`
- `attempts`
- `error`
- `created_at`
- `updated_at`

### `task_agent_runs`

Task-agent audit trail.

- `id`
- `summary_input_id`
- `codex_session_id`
- `decision_json`
- `audit_summary`
- `memory_recall_used`
- `created_at`

### `follow_up_drafts`

Follow-up drafts and delivery state.

- `id`
- `project_id`
- `todo_id`
- `owner_user_id`
- `owner_name`
- `target_conversation_id`
- `target_kind`: `group / direct`
- `question_text`
- `risk_check_json`
- `status`: `draft / approved / sent / skipped / failed / cancelled`
- `send_result_json`
- `scheduled_at`
- `sent_at`
- `created_at`

### `daily_scan_state`

Scanner watermarks.

- `scanner_name`
- `last_success_at`
- `cursor_json`
- `last_error`

## Source Processing

### Conversation completion

After the existing reply path finishes, create a Work Item from the structured
reply attempt and audit fields. This should not change the reply attempt status.

The Work Item summary should describe business-relevant changes only. It should
exclude greetings, pure acknowledgement, duplicate reminders with no new
information, and system status with no state change.

### AI minutes

Daily scanner reads all newly available AI minutes since the last successful
scan. It should inspect summary, todos, participants, and transcription preview
where available. Minutes with owner, action item, decision, risk, project status,
or management implication become Work Items.

### Local files

Daily scanner reads only files under `CEO_WORKSPACE`.

Rules:

- No full-disk scans.
- No repository scan unless the repo is explicitly under `CEO_WORKSPACE`.
- Use configurable include/exclude paths under the workspace.
- Prefer readable text, Markdown, and already extracted document text.
- Skip unknown binary or excluded sensitive files and record the reason.
- Store the file summary and source reference, not full sensitive body text, in
  task tables.

### Memory recall

Memory is searched, not enumerated.

For active or waiting projects, the daily process can run targeted recall using
project title, tags, owner, facts, and TODO terms. If new source material exposes
a new theme, the task agent can do exploratory recall to decide whether it is an
existing project, a new project, or an item that needs clarification.

## Follow-Up

TODO follow-up depends on both factual deadlines and management expectations.

Time concepts:

- `deadline_at`: explicit date from conversation, OKR, document, or TODO.
- `next_follow_up_at`: when the system should check progress or ask owner.

Priority expectations:

- `P0`: ask for result, blocker, and ETA today or the next workday.
- `P1`: ask for progress, risk, and next step within 3 days.
- `P2`: lightly confirm progress within the week.

Explicit deadlines are respected. If an explicit deadline conflicts with
priority expectations, set `needs_derek_attention=true` or route to confirmation.

Sending policy:

- Default is to create `follow_up_draft`.
- Auto-send only when low risk: owner is clear, follow-up time is clear, question
  text is clear, related conversation is clear, owner is in the selected group
  if group-send is used, and the matter is suitable for that audience.
- Otherwise keep the draft for review on `/tasks`.

Conversation selection:

- Track multiple source conversations per project.
- Prefer source groups where the matter has been discussed.
- Let the task agent choose based on group name, recent relevant updates, whether
  owner is in the group, whether the question is suitable for the group, and
  whether the matter historically progressed there.
- If owner is not in any suitable source group, or public follow-up is not
  appropriate, send direct message.

Owner replies require no special reply logic. They use the existing CEO reply
path, then produce a new Work Item.

## Memory Connector Setup

The task system depends on `memory_connector` for new project background and
unclear historical context.

Add a setup/doctor flow that checks whether `memory_connector` MCP is available
for Codex and Claude MCP configuration. If missing:

1. Back up existing MCP config files.
2. Install or update the Codex MCP config.
3. Install or update the Claude MCP config.
4. Run a Codex smoke test that can access `memory_connector.user_get`.
5. Validate the Claude config shape.

Do not silently edit MCP config during ordinary message processing. If task
agent execution requires memory and memory is unavailable, record setup-required
state and surface the repair path in the task page or CLI.

When calling memory tools, do not provide or invent `user_id`. The installed MCP
authorization identifies the authenticated user.

## Task Page

Add `/tasks` to the local audit web UI.

Primary view:

- Filters: category, status, priority, risk, Derek attention, owner, tag.
- Project list: title, category, priority, risk, owner, current state, next step,
  next follow-up, open TODO count, last activity.

Project detail:

- Background.
- Facts.
- Current state, blocker, next step.
- TODO list.
- Timeline updates.
- Source groups and direct chats.
- Related memory context.
- Recent task agent runs.
- Follow-up drafts and send history.

Review queue:

- Follow-up drafts waiting for approval.
- Unclassified important Work Items.
- Low-confidence merges.
- Memory connector setup-required errors.
- DWS/context read failures.

Actions:

- Approve/send follow-up.
- Edit follow-up text.
- Cancel or reschedule follow-up.
- Manually update project or TODO state.
- Merge projects.
- Archive projects.

## Error Handling

- Memory connector unavailable: block new low-confidence project creation,
  record setup-required, and surface repair instructions.
- BM25 has no good candidate or candidate mismatch: task agent may use DWS or
  memory recall to recover context. If still unclear and important, generate a
  follow-up question instead of creating a vague project.
- DWS context read failure: mark Work Item failed and retryable.
- File outside `CEO_WORKSPACE`: skip and record reason.
- File unreadable or excluded: skip and record reason.
- Follow-up delivery failed: keep TODO state unchanged, mark draft failed, and
  allow resend.

## Testing

Add focused tests for:

- Conversation completion enqueues a Work Item without changing reply attempt
  state.
- Work Item shape remains small and does not include pre-extracted candidates,
  TODOs, or facts.
- BM25 candidate projects are included in task-agent prompt.
- Task agent can update an existing project, create a project, discard, create
  TODOs, close TODOs, and create follow-up drafts.
- New project creation requires memory availability and recall when memory is
  configured.
- Missing or mismatched BM25 candidates can trigger DWS or memory recovery
  prompts.
- Important but unclassifiable Work Items create follow-up drafts rather than
  vague projects.
- Daily AI minutes scan enqueues Work Items.
- Daily local file scan only reads under `CEO_WORKSPACE`.
- Explicit completion evidence closes TODOs and records
  `completion_evidence_json`.
- P0, P1, and P2 overdue TODOs generate different follow-up questions.
- Owner not in any suitable source group routes follow-up to direct message.
- Follow-up draft review, edit, cancel, and auto-send paths are visible and
  audited.
- `/tasks` renders filters, project list, detail, facts, TODOs, updates, and
  pending drafts.

## Rollout

1. Add schema, store methods, and task-agent JSON schema.
2. Add Work Item creation after reply attempts.
3. Add BM25 project retrieval and task-agent prompt.
4. Add project/TODO/update persistence.
5. Add daily AI minutes scanner.
6. Add local file scanner restricted to `CEO_WORKSPACE`.
7. Add follow-up draft generation and review UI.
8. Add low-risk follow-up auto-send.
9. Add memory connector doctor/setup command.
10. Add `/tasks` page and audit details.

After runtime code is implemented and committed in a later phase, restart the
launchd service per project instructions before reporting the feature live.
