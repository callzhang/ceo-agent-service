---
name: ceo-feedback-processing
description: Use when an Agent is asked to diagnose and repair repository feedback recorded in the local feedback queue. The Skill owns the repository workflow and evidence receipt; the service owns persistence and state transitions.
metadata:
  managed_by: ceo-agent-service
  version: 1
---

# CEO Feedback Processing

Process each feedback item by its persisted `feedback_key`, which is the stable identity
for the item from import through resolution. Read the pending batch and
batch detail first. Use the supplied task, attempt, and run references; never
invent an association or replace a reference with a guessed ID. Start from the
persisted summaries and routes returned by the API, then inspect the repository
and the referenced execution evidence.

The Console API is local-only and requires no auth. Its base is
`http://127.0.0.1:8765/api/console/feedback`. Import formatting never calls a model:
it only formats persisted summaries and references for the processing turn. No new UI workflow
or `service_bugfix` route is allowed.

## Exact operations

Use the existing Console envelope and these operations (the path parameters are
literal placeholders):

| Purpose | Operation |
| --- | --- |
| list pending feedback | `GET /api/console/feedback` (use `?status=pending`) |
| read one feedback detail | `GET /api/console/feedback/{feedback_key}` |
| claim a batch atomically | `POST /api/console/feedback/batches` |
| read batch detail and item state | `GET /api/console/feedback/batches/{batch_id}` |
| associate the Workbench task and turn | `PATCH /api/console/feedback/batches/{batch_id}` |
| patch one item's evidence | `PATCH /api/console/feedback/items/{feedback_key}` |
| resolve a complete batch | `POST /api/console/feedback/batches/{batch_id}/resolve` |

Claim only the requested feedback keys in one batch. After claiming, associate
the supplied Workbench task/turn and preserve supplied attempt/run references on
each item. Read the batch detail again before editing so the current item state,
summary, and routes are authoritative.

## Repository workflow

1. Use the brainstorming skill for design dialogue when the repair scope or
   approach is unclear. Keep that dialogue tied to the persisted feedback and
   current repository evidence.
2. reproduce before editing: reproduce the reported behavior and capture the command, input,
   observed failure, and relevant task/attempt/run references.
3. Preserve all uncommitted user changes and the existing worktree. Inspect
   `git status` and avoid resetting, cleaning, or overwriting unrelated files.
4. Make the smallest permanent code or configuration change that addresses the
   reproduced cause. Add a regression test that fails before the change and
   passes after it; run focused tests first, then the relevant broad tests suite
   and lint/text checks.
5. Commit the related changes with a descriptive `git commit`. Record the commit
   SHA for the feedback receipt.
6. Restart the required launchd service after runtime changes:
   `launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main`.
   Verify `com.ceo-agent-service.main` has a new PID, query local
   `http://127.0.0.1:8765/healthz`, and read back that there is no failed or
   processing backlog (the **failed/processing backlog** check).

## Evidence and resolution

After verification, patch every item individually with per-item evidence via
`PATCH /api/console/feedback/items/{feedback_key}`. Include the commit SHA,
regression/focused/broad test evidence, restart evidence (including launchd
label and before/after PID), health evidence (the local `/healthz` response),
and a concise note. Keep each item's supplied task, turn, attempt, and run
associations intact. Do not claim completion from a command exit code alone;
the evidence must be persisted and read back from the item and batch detail.

Call batch resolve only after all items complete and every item has complete,
consistent evidence. An evidence-free resolve is forbidden. incomplete evidence
leaves items `processing`; repair or continue the item instead of forcing a
resolved status. Direct SQLite writes are forbidden: all reads, claims,
associations, evidence patches, and resolution go through the local Console API.
