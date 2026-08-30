# Feedback Reopen Processing Rounds Design

## Purpose

Allow an incorrectly or prematurely resolved user-feedback item to return to
the existing feedback-processing workflow without overwriting its prior batch,
evidence, or completion time. The feature stays inside the local Feedback API
and the existing React feedback page; it does not add a second Agent workflow,
remote authentication, or model-generated import/reopen content.

## Confirmed product semantics

The existing three persisted states remain authoritative:

```text
pending -> processing -> resolved -> pending
```

- `pending` means unfinished and not currently claimed.
- `processing` means unfinished and atomically claimed by one batch.
- `resolved` means the current processing round completed with a valid receipt.
- “Open” is the user-facing set `{pending, processing}`, not a fourth stored
  state.
- Reopen is the only legal `resolved -> pending` transition.
- Reopen never sends the item directly to `processing`; a later atomic batch
  claim performs `pending -> processing`.

Already-correct items remain `resolved`. Reopening one item does not roll back
other items from the same historical batch.

## Processing-round model

A processing round is one complete attempt to handle the same stable
`feedback_key`. Each new claim after reopen creates a new round and a new
batch. The previous batch stays resolved and immutable.

Example:

```text
manual:8308
  round 1 / batch A / resolved / old receipt
  reopened with reason
  round 2 / batch B / processing -> resolved / new receipt
```

The current feedback row remains a projection for list and compatibility
reads. Historical receipts live on round rows, so stale evidence cannot satisfy
a later resolution.

## Persistence

### Existing tables

- `feedback_events` remains the source feedback and current resolved/open
  projection.
- `feedback_processing_items` remains the current state for each stable
  `feedback_key`.
- `feedback_processing_batches` remains one immutable batch execution.

`feedback_processing_items` gains nullable `current_round_id`. Existing current
association and evidence columns remain as compatibility projections during
this change; every current-round write updates both the round and projection.

### `feedback_processing_rounds`

One row represents one claimed processing round:

- `id` integer primary key
- `feedback_key` text
- `round_number` positive integer
- `batch_id` text
- `status` constrained to `processing` or `resolved`
- `workbench_task_id`, `workbench_turn_id`
- `attempt_id`, `agent_run_id`
- `commit_sha`
- `test_evidence_json`, `restart_evidence_json`, `health_evidence_json`
- `note`
- `started_at`, `resolved_at`, `reopened_at`, `reopen_reason`
- `created_at`, `updated_at`

Uniqueness is enforced for `(feedback_key, round_number)` and
`(feedback_key, batch_id)`. A resolved round is never patched again.

### `feedback_processing_transitions`

Append-only state history contains:

- `id` integer primary key
- `feedback_key`
- nullable `round_id` and `batch_id`
- `from_status`, `to_status`
- `reason`
- current Workbench task/turn identifiers when present
- `created_at`

State mutation and transition insertion occur in the same immediate
transaction. Evidence patches do not create transitions.

### Additive backfill

Initialization creates the new tables and indexes and adds
`current_round_id`. It then idempotently backfills round 1 for existing items
that already belong to a batch or contain processing evidence:

- existing `processing` items become round 1 `processing`;
- existing `resolved` items become round 1 `resolved`;
- never-claimed `pending` items do not receive a synthetic round;
- current association/evidence/timestamps copy verbatim into round 1;
- no reopen history is guessed;
- source feedback text and existing completion timestamps do not change.

Unique constraints make repeated initialization safe.

Before applying the additive migration to the production SQLite database, the
workflow creates and verifies an online backup, checks the backup can be
opened, and compares key feedback table counts. After restart it reads back
counts, state distribution, current-round referential consistency, and orphan
round count.

## Local API

### Reopen

```http
POST /api/console/feedback/items/{feedback_key}/reopen
Content-Type: application/json

{"reason":"The earlier resolution preceded the completed repair."}
```

`reason` is a required non-empty persisted string. The service does not
generate, rewrite, or default it.

For a resolved item, one immediate transaction:

1. verifies the item and current round are resolved;
2. records `resolved -> pending` with the supplied reason;
3. marks the prior round with `reopened_at` and `reopen_reason`;
4. changes the current item and source event projection to pending/open;
5. clears current batch, round, Workbench association, commit, and evidence
   projections;
6. preserves the old batch and round unchanged except for reopen metadata.

Calling reopen while already `pending` is an idempotent no-op and does not add
a duplicate transition. Calling it during `processing` returns conflict and
does not disturb the active claim. Missing feedback returns not found. An
incomplete historical round causes the entire transaction to fail.

Stable error codes are:

- `not_found`
- `feedback_reopen_invalid`
- `feedback_reopen_processing`
- `feedback_reopen_history_incomplete`

### Claim

The existing `POST /api/console/feedback/batches` stays authoritative. Claiming
a reopened pending item creates a new batch and the next round number. It does
not copy prior evidence or prior Workbench associations. Same-batch claim retry
remains idempotent; another batch receives the existing processing conflict.

### Evidence patch

The existing item PATCH writes association and evidence only to the current
`processing` round and mirrors them to the item projection. It rejects pending,
resolved, missing, mismatched-batch, or non-current rounds. Old resolved rounds
are immutable.

### Resolve

The existing batch resolve validates only current processing rounds belonging
to that batch. Resolution stays atomic across all requested items.

Each current round requires:

- complete Workbench task/turn and persisted attempt/run association;
- a valid 40-character commit that exists and is an ancestor of local `main`;
- non-empty test evidence with every explicit `exit_code` equal to zero;
- launchd label `com.ceo-agent-service.main` and distinct positive before/after
  PIDs;
- local `http://127.0.0.1:8765/healthz` (or localhost equivalent), HTTP 200,
  and `ok=true`;
- backlog evidence showing required processing, failed, and retryable counts
  are zero;
- per-round evidence consistent with the batch receipt.

Any failed check returns `409 feedback_resolution_incomplete` and leaves the
entire batch processing. Success resolves the rounds, current items, batch, and
source projections and appends transitions in one transaction. Historical
rounds and batches never move backward.

### Reads

Feedback detail adds `current_processing` and `processing_history`. History is
ordered newest first and summarizes round, batch, state, associations, commit,
test/restart/health receipt, completion time, and reopen reason. Batch detail
reads round rows for that exact batch, so an old resolved batch stays readable
after its feedback is reopened. List APIs continue to return one row per
feedback item and only the current state.

## React UI

The existing User Feedback page remains the only feedback page.

- Only resolved items show `重新打开`.
- The action opens a small reason dialog with cancel and confirm.
- Empty reason cannot submit.
- Submit shows a visible loading state and disables duplicate submission.
- Success refreshes the row to pending and displays `反馈已重新打开`.
- Failure preserves the typed reason and displays the backend error.
- Expanded detail shows round summaries newest first; full receipt remains
  available through batch detail links.

No new flow controls are added to the Agent page. Reopened items naturally
return to the existing pending selection and claim flow.

## Repository Skill

`skills/ceo-feedback-processing/SKILL.md` documents the reopen route and the
round rules:

- reopen requires a factual reason;
- reopen returns to pending and must be claimed again;
- each reprocessing uses a new batch/round;
- prior evidence must not be copied;
- resolution uses only the current round receipt;
- all state writes continue through the local API, never direct SQLite.

## Testing and verification

Implementation follows RED-GREEN TDD.

Store tests cover additive/idempotent schema, backfill, atomic reopen,
idempotent pending reopen, processing conflict, rollback on failure, new-round
claim, old evidence isolation, old-round immutability, and atomic batch resolve.

API tests cover request validation, stable errors, item/batch history reads,
pending list projection, new claim, stale evidence rejection, commit ancestry,
health/restart/backlog validation, and second resolution.

React tests cover button visibility, required reason, loading, success refresh,
error preservation, history rendering, and existing detail links.

The local E2E creates a non-delivering test feedback and verifies:

```text
pending -> claim -> round 1 -> resolve -> reopen -> pending
        -> new batch/round 2 -> reject stale receipt -> resolve
```

Final verification includes focused and related broad Python tests, frontend
tests, lint, build, `git diff --check`, database backup/migration readback,
launchd restart with PID change, `/healthz`, clean required queues, API readback,
and rendered UI behavior. No external message is sent by the E2E.

## Isolation and delivery

Development occurs in `.worktrees/feedback-reopen-rounds` on
`codex/feedback-reopen-rounds`, separate from the dirty email feature checkout.
Feature commits are kept scoped so they can be reviewed and integrated after
the currently committed email dependency baseline becomes consistent. No
uncommitted file from another worktree is copied into this branch.
