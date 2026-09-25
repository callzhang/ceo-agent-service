# Task Project Growth and Corruption Repair Design

## Problem statement

The production task database contains two related failures:

1. Project updates can overwrite preserved fields with schema defaults such as
   an empty `title`, `goal`, `background`, or owner. The Tasks API then displays
   an empty title as `Project <id>`.
2. Automatic local-file ingestion has promoted historical meeting exports into
   active projects. Candidate retrieval considers only the 500 most recently
   active projects, so older matching projects become invisible and later
   material is more likely to create another project.

The 2026-09-13 production snapshot has 962 projects. A 2026-07-30 snapshot had
681. Of the projects created afterward, 250 came from local files, predominantly
historical AI-minutes exports. Of those 250, 234 have no TODO and 247 remain
active or waiting. Thirty pre-existing projects lost non-empty titles relative
to the July snapshot, and the current database has 37 empty titles.

## Goals

- A project update must never clear preserved project metadata merely because
  an agent emitted an empty schema default.
- An automatically scanned local file may enrich an existing project but may
  not autonomously create a new project.
- Candidate retrieval must consider every active or waiting project before
  selecting the small prompt-facing candidate set.
- Existing corrupted fields must be restored only from traceable prior values.
- Historical-material-only projects must leave the active Tasks workspace
  without deleting their audit history.
- The repair must be restart-safe, idempotent, backed up, dry-runnable, and
  verifiable through the database, HTTP API, and Tasks UI.

## Non-goals

- Deleting project, TODO, update, follow-up, or task-agent history.
- Automatically merging semantically related projects without reviewable,
  deterministic evidence.
- Reclassifying genuine current projects solely because they have no TODO.
- Replacing the task-retrieval algorithm with a new external search service.

## Considered approaches

### A. UI-only filtering

Hide untitled and historical-looking rows in Tasks. This makes the page look
better but leaves corrupted data and continued autonomous creation untouched.
Rejected.

### B. One-time database cleanup

Restore titles and archive the current excess projects without changing the
write path. This produces a temporary improvement but the same workers can
recreate the failure. Rejected.

### C. Enforced mutation contracts plus deterministic recovery

Prevent unsafe writes, restrict autonomous local-file behavior, remove the
retrieval blind spot, then restore and archive using an auditable repair plan.
Selected because it fixes both causes and preserves recovery evidence.

## Prevention design

### Action-specific project mutations

Creation and update must no longer share a model whose business fields all
default to empty values.

- `create_project` uses a creation payload with a required, trimmed, non-empty
  title and explicit status.
- `update_project` uses a patch payload whose fields are absent unless the
  decision actually changes them.
- Empty values for preserved metadata (`title`, `goal`, `background`, owner,
  facts, source conversations, category, priority, risk, and status) are
  rejected rather than interpreted as changes.
- Fields that legitimately support clearing use an explicit clearing operation;
  ordinary empty defaults are never a clearing instruction.
- The store receives a validated mutation map rather than a full model dump.

Completion checks have a narrower policy (superseded 2026-09-25: periodic completion checks were removed; see the amendment at the top of `2026-09-22-task-first-tasks-design.md`):

- No new evidence and no TODO/follow-up state transition means `skip`.
- A completion check may mutate the linked TODO or follow-up and may update the
  project's current-state projection, blocker, next step, and memory context.
- It may not change identity or descriptive metadata such as title, owner,
  goal, background, category, tags, risk, or source conversations.
- A completion check that violates this policy is rejected before any database
  write, so validation repair cannot turn a no-op into project churn.

### Local-file creation boundary

Automatic `local_file` inputs are evidence sources, not authority to create a
new business project.

- They may update an existing project selected from current task state.
- If no stable project ID can be established, the decision must be `skip`.
- New projects remain available to explicit user-directed task creation and to
  current live sources whose contract permits creation.
- The scanner continues using path and content digest for ingestion
  idempotency, but digest changes do not grant project-creation authority.

This preserves useful file-driven updates while preventing an imported archive
from becoming one active project per document.

### Full active-project candidate retrieval

Candidate scoring must inspect all active and waiting projects rather than the
500 most recently active rows. Only the final top-ranked candidates remain in
the agent prompt, so prompt size stays bounded.

Exact project IDs and source-conversation matches remain stronger than text
similarity. The focused regression case places the correct project outside the
old 500-row window and verifies that it is still returned.

### Lifecycle guardrails

Creation requires a current execution signal, not merely a historical action
sentence. The accepted signals are a currently valid commitment, current
progress change, current accountable owner, future next step, or explicit user
instruction to track the work. Historical dates and expired plans without
current confirmation are stored as evidence or skipped, not created as active
projects.

Production monitoring reports project creation counts by source and flags an
unexpected local-file create attempt as a rejected mutation, not a successful
project.

## Data recovery design

### Backup and inventory

Before any data mutation:

1. Create a SQLite online backup of the production database at an explicit,
   timestamped path.
2. Run `PRAGMA integrity_check` on the backup.
3. Record production counts for projects, blank protected fields, statuses,
   TODOs, follow-ups, work updates, and task-agent runs.
4. Generate a dry-run repair manifest with one row per proposed field or status
   change and its evidence source.

### Corrupted-field restoration

The repair only fills a currently empty protected field. It never overwrites a
current non-empty value.

For each field, choose the latest traceable non-empty value from, in priority
order:

1. a prior persisted task-agent decision for the same stable project ID;
2. a verified pre-corruption SQLite snapshot;
3. the original project-creation decision or work-update evidence.

If sources disagree without an unambiguous temporal order, or no prior value
exists, the manifest marks the field unresolved and performs no write. Every
applied restoration records project ID, field, old value, new value, evidence
source, evidence timestamp, and repair timestamp.

The operation is idempotent because rerunning it finds no currently empty field
for an already restored value.

### Historical-material-only project archival

No project is deleted. A project is eligible for automatic archival only when
all of the following are true:

- its creation source is an automatic `local_file` input;
- the source material predates ingestion and does not establish a current
  execution signal at ingestion time;
- it has no TODO, DingTalk TODO link, follow-up, or non-local live update;
- it has no explicit user-directed creation evidence;
- its audit history does not show a later current commitment.

Eligible rows move to `archived` with a repair update explaining the exact
criteria and source evidence. Ambiguous rows remain unchanged and appear in a
manual-review section of the manifest.

The migration first applies ten eligible rows, verifies their detail pages and
aggregate counts, and only then applies the remaining eligible rows. A rollback
manifest records original statuses even though the verified SQLite backup is
the primary recovery point.

## User-interface behavior

The `Project <id>` fallback must not make corrupt data look like a normal title.
If an unresolved empty title remains after recovery, Tasks displays an explicit
data-quality label such as `标题缺失（Project 791）` and exposes it to integrity
monitoring. Archived historical-material projects are excluded from the default
active Tasks view but remain accessible by status filter and direct URL.

## Tests

Regression tests are written and observed failing before implementation:

1. An update decision containing empty schema defaults cannot erase an existing
   title, owner, goal, background, facts, or source conversations.
2. A completion check without new evidence produces no project mutation and
   does not bump project activity ordering.
3. A completion check with a valid TODO transition cannot mutate protected
   project metadata.
4. An automatic local-file decision cannot create a project without a stable
   existing project ID.
5. Candidate retrieval finds a relevant project beyond the former 500-row
   recency window.
6. The recovery planner fills only currently empty fields from the latest
   traceable non-empty evidence and is idempotent.
7. The archival planner accepts only projects satisfying every deterministic
   historical-material-only criterion and leaves ambiguous rows unchanged.
8. The Tasks API renders any unresolved missing title as a data-quality error,
   not a normal synthetic project name.

Focused tests are followed by the complete Python suite, web tests and build,
static checks, and `git diff --check`.

## Deployment and production verification

Before restart, verify that claimed reply tasks, work-summary inputs, meeting
jobs, and persisted external actions remain resumable and idempotent. Apply the
database migration only after the code that blocks recurrence is ready.

After the focused ten-row archive batch and full repair:

- restart `com.ceo-agent-service.main`;
- verify the new supervisor, service, audit-web, and email-worker processes;
- verify recovered queue states and external-action reconciliation;
- confirm there are no new failed or stuck work items;
- read back aggregate database counts and a sample of restored projects;
- verify `/api/console/tasks`, exact task-management detail endpoints, and the
  Tasks page in the headless browser;
- run one safe synthetic update proving preserved fields remain unchanged;
- run one safe scanner decision proving a historical local file cannot create a
  project.

## Documentation and commits

Update the README task semantics and CHANGELOG after regression tests pass.
Commit separately by coherent feature: prevention contract and tests, recovery
tool and tests, production repair evidence/documentation. Stage only files owned
by this repair and leave pre-existing user changes untouched.
