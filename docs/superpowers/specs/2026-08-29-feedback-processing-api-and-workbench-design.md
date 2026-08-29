# Feedback Processing API and Workbench Integration Design

Date: 2026-08-29  
Status: design approved in conversation; implementation not started

## 1. Goal and scope

The existing feedback record for attempt `8308` was not reliably surfaced as a
processable item. The service needs a local feedback resource API and a small
Workbench entry point so the user can select feedback and start an Agent
conversation that improves the service.

The requested operating model is deliberately hybrid:

1. The backend owns feedback facts, processing state, associations, and
   completion evidence through a local-only API.
2. A repository-level Skill teaches the Agent how to read feedback, diagnose
   the repository, modify and test code, commit, restart the service, verify
   runtime state, and update the feedback API.
3. Workbench adds only a lightweight right-side drawer and import action. It
   does not become a workflow engine or grow forms for coding, testing,
   committing, or restarting.

The feedback API follows the existing local service boundary (for example,
`127.0.0.1:8765`). It is not publicly exposed and has no feedback-specific
authentication layer. If the entire service later needs remote access, that
will be designed as a separate backend-wide change.

## 2. Responsibilities and data flow

```text
feedback_events (immutable user feedback facts)
          |
          v
local feedback API
  |-- list/get pending feedback
  |-- atomically claim a selected batch
  |-- store task/turn/attempt/run/commit/evidence associations
  `-- resolve only after completion evidence is present
          |
          |--------------------> Workbench right drawer
          |                       display + multi-select + import
          |
          `--------------------> repository-level Skill
                                  Agent diagnosis, implementation,
                                  verification, restart, and API writeback
```

`feedback_events` remains the source of the original user rating, comment,
token, received time, and provider payload. Processing records are separate so
the original event is never overwritten by an Agent summary or diagnosis.

Workbench uses the API for selection and claim. It does not inspect code
changes, interpret tool events, or infer that a task is complete. The Agent
uses the Skill and the API; it does not write SQLite directly.

The processing lifecycle is:

```text
pending -> processing -> resolved
              |
              `-- interruption/failure: remain processing and resume later
```

`processing` prevents duplicate ownership while preserving resumability. A
failed turn, failed test, failed restart, or closed browser must not delete the
feedback or silently return it to history.

## 3. Feedback API

### 3.1 Read operations

`GET /api/feedback?status=pending&limit=50` returns the fields required by the
Workbench drawer:

- stable `feedback_key` (the existing `feedback_events.key`);
- rating, comment, received time, and current processing state;
- an already-persisted summary, when one exists in the related attempt/audit
  projection;
- detail references for the feedback item, attempt, run, and task.

The summary is selected from existing persisted fields with a fixed
precedence. If no summary exists, the API returns an empty summary and the
detail references. It never invokes a model or synthesizes a new summary while
listing or importing feedback.

The list operation may perform the existing bounded feedback-event sync for
sent replies waiting for remote events before reading SQLite. Sync is bounded
and non-destructive: a provider timeout leaves existing local events intact and
surfaces the sync problem without preventing local reads. This makes the
attempt `8308` regression testable through the same path as newly received
feedback.

`GET /api/feedback/{feedback_key}` returns the complete original event,
related attempt/run/task data, persisted summary, detail references, and any
processing records.

### 3.2 Processing operations

`POST /api/feedback/batches` atomically claims a multi-selection. Its request
contains:

```json
{
  "feedback_keys": ["..."],
  "workbench_task_id": "...",
  "workbench_turn_id": "..."
}
```

`workbench_turn_id` is optional on the initial request because the batch must be
claimed before the deterministic startup turn can be created. The response
returns a stable `batch_id` and all claimed item keys. The transaction checks
that every item is still `pending`; if any item is already owned, the whole
request returns `409 feedback_already_processing` with no partial claim.

An idempotent batch update associates the created Workbench turn after the turn
is persisted. If turn creation fails, the batch remains `processing` and can be
resumed from the same task.

`PATCH /api/feedback/items/{feedback_key}` stores processing facts supplied by
the Skill, including associated attempt/run/task references, commit SHA,
test evidence, restart evidence, health evidence, and a concise note. It cannot
move a resolved item back to pending and repeated writes of the same evidence
are idempotent.

`GET /api/feedback/batches/{batch_id}` returns the batch and item-level state so
the Skill can recover after an interrupted conversation.

`POST /api/feedback/batches/{batch_id}/resolve` accepts a complete evidence
payload and performs the final transaction. Before changing state, the API
requires:

- every item in the batch has a commit SHA and complete associations;
- the commit SHA is valid and matches the current repository HEAD;
- each recorded test command has exit code `0`;
- restart evidence names the launchd label and includes before/after PIDs;
- health evidence records a successful local health readback;
- the batch is still processing.

The API may verify repository HEAD, launchd state, and local health directly;
the exact test command output remains an execution receipt supplied by the
Skill and is not silently replaced by a natural-language claim. On success,
all items are marked `resolved` in one transaction and the commit/evidence
receipts become immutable completion facts.

### 3.3 Error and consistency behavior

- Unknown feedback key: `404`.
- Malformed IDs or evidence: `400`/`422`.
- Any item already claimed: `409`, with no partial batch.
- Commit mismatch, non-zero test, unchanged PID, failed health readback, or
  incomplete associations: `409`, leaving items `processing`.
- Repeated claim/evidence/resolve calls are idempotent.
- A provider sync failure never deletes or rewrites a local feedback event.

The existing `用户反馈` page reads the same processing projection. Its badge
counts only unclaimed pending items; rows show pending, processing, or
resolved. Existing `resolved_at` and corrected-reply history remain immutable
history and are not re-claimed by migration.

## 4. Repository-level Skill

Add `skills/ceo-feedback-processing/SKILL.md` as a repository-level operating
contract. It is discovered by the Agent in the repository; Workbench does not
hard-code the procedure.

The Skill instructs the Agent to:

1. Read the batch and each item through the local API, using the supplied
   `feedback_key` as the stable identifier.
2. Open the referenced user-feedback, attempt, run, and task details as needed.
3. Confirm repository, branch, worktree, and existing user changes before
   editing; preserve unrelated uncommitted work.
4. Reproduce the problem and locate the root cause before changing code.
5. Add a regression test that fails before the fix and passes after it.
6. Run the focused test and the necessary broader tests, recording exact
   commands, exit codes, times, and short output receipts.
7. Commit the related implementation and tests with a traceable commit
   message, then read the current HEAD.
8. For runtime changes, restart
   `com.ceo-agent-service.main`, verify a new PID and `launchctl print`, poll
   the local health endpoint, and check for new failed or stuck backlog.
9. Patch each feedback item with commit and evidence, then call batch resolve
   only after every item satisfies the completion contract.

The Skill prohibits direct SQLite writes, claiming completion from ordinary
Agent prose, skipping regression tests, skipping restart/readback for runtime
changes, deleting difficult feedback, or merging multiple items into an
untraceable record. If work is incomplete, it records the blocker and leaves
the item processing so the same task can resume.

The Skill also directs the Agent to use the brainstorming skill for the
conversation and design phase. Importing feedback itself is deterministic and
does not call a model or synthesize a new summary.

## 5. Workbench UI and import behavior

The left task list receives one button immediately below `新任务`:

`处理反馈 · N`

where `N` is the count of pending, unclaimed feedback items. Clicking it opens
a right-side drawer while keeping the current task and conversation visible.

The drawer:

- loads pending items from the feedback API;
- shows rating, existing summary, received time, and detail paths;
- supports per-item checkboxes and select-all;
- shows the selected count;
- closes without mutation when cancelled;
- uses `导入并开始 brainstorm` as its sole primary action.

If a task is currently open, the import uses it. If no task is open, the UI
creates one with `runtime_kind=codex` and a feedback-oriented title. The UI
then claims the selected batch, creates one Workbench turn, associates the turn
with the batch, and lets the existing scheduler run it. The drawer closes and
the task becomes selected.

The startup turn is one deterministic message. It contains the batch ID, the
repository Skill path, each feedback key, the existing persisted summary, and
human-readable detail references such as `task#124`, `attempt#345`, and
`run#445`, together with local URLs where available. It explicitly asks the
Agent to use brainstorming for the conversation and to use the Skill for
implementation and API writeback. It does not contain a model-generated
summary or a second copy of the full feedback body.

The UI does not add coding, testing, commit, restart, or evidence forms. The
Agent and backend API own those operations.

## 6. Migration and compatibility

The migration is additive and idempotent. It creates the processing batch/item
tables and indexes, preserving all existing `feedback_events` rows. Existing
feedback rows, including the event associated with attempt `8308`, remain
visible in `用户反馈` and become claimable when they are not already resolved.

The current feedback sync path remains available from `同步最新反馈`; the new
API reuses its bounded implementation for local reads. No
`service_bugfix_candidates` table, route, or page is restored.

## 7. Verification and acceptance

The implementation is ready only when all of the following are demonstrated:

1. The attempt `8308` feedback event appears in `用户反馈` and in
   `GET /api/feedback?status=pending` when unresolved.
2. Selecting two or more items claims them atomically and creates one startup
   turn containing only persisted summaries and detail references.
3. A concurrent claim cannot partially take the same selection.
4. Interrupted turns leave the batch processing and resumable from the same
   task.
5. The Skill can read, modify, test, commit, restart, and write evidence using
   only the local API and repository operations.
6. Resolve rejects incomplete or inconsistent evidence and accepts a complete
   commit/test/restart/health receipt, marking every item resolved together.
7. Focused backend, Workbench frontend, migration, and Skill contract tests
   pass, followed by the required runtime restart and live readback.

## 8. Out of scope

- Public or remote feedback API access and authentication;
- a second feedback queue or independent conversation system;
- model-generated summaries during import;
- automatic code changes before the Agent conversation and Skill procedure;
- restoring the removed service-bugfix candidate workflow;
- changing the global Agent/Audit lifecycle contract.
