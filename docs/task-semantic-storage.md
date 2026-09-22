# Task-first semantic storage and business resolution (Tasks 1–4)

This document describes the storage, atomic commands, decision rules, and business
resolution commands introduced by Tasks 1–4 of the approved
[implementation plan](superpowers/plans/2026-09-22-task-first-tasks.md). It does
not describe a deployed Task Agent cutover. The runtime, console, providers,
and legacy import workflow still belong to later tasks.

The schema version is `2026-09-22.1`, the single version assigned to the complete
new semantic schema. Initialization adds the 15 bounded tables to a pre-semantic
database without reclassifying, copying, or deleting its legacy work records.
The incomplete intermediate schema from the isolated development branch is not
a released migration source; no compatibility migration for that draft is added.

## Records and evidence

Every table has a frozen, extra-forbid Pydantic record in
`app/task_semantic_models.py`, with matching SQLite enum and state constraints.
The records keep timestamps and JSON documents as strings, matching the plan's
persisted contract. Object-valued JSON and the missing-evidence array are checked
for their required shapes. Keeping JSON as strings also avoids mutable nested
containers inside otherwise frozen records.

All required semantic text and the resolution time of a resolved attention item
use the same nonblank definition as Python's `str.strip()`. The shared SQLite
predicate uses native `trim(column, char(...)) <> ''` with all 29 Python whitespace
codepoints, including tabs, newlines, C0 separators, and Unicode spaces. It works
on independent SQLite connections without registering a Python function. The
predicate only validates: valid text, including its surrounding whitespace,
round-trips unchanged. Zero-width space and BOM are not Python strip whitespace
and remain valid nonblank content.

`business_task_signals` preserves source type/reference/time, conversation
identity/title, author identity/name, original evidence text, and context.
Unknown source context stays empty rather than being fabricated. Evidence text
and source strings are not trimmed or summarized when stored. The caller supplies
the source/content deduplication key; its unique constraint rejects duplicates.
SQLite triggers prohibit UPDATE, DELETE, and replacement of an existing signal.
The primitive create method raises on duplicate input; returning an existing
signal from a multi-row command is part of Task 2's transaction service.

`business_tasks` stores description, owner identity and supporting evidence,
deadline, missing evidence, last activity, and separate stage, lifecycle,
commitment, and relevance fields. Formal tasks require one of the four approved
bases; candidates cannot carry one. A merged task requires an existing, distinct
merge target, and other statuses cannot carry a merge target. Merge-chain checks
and transitions are enforced by Task 2's semantic service.

Source observations attach through `business_task_evidence`, whose required
`task_id`, `signal_id`, and typed `evidence_role` form its composite key. There is
no redundant direct source or project pointer on a task. An explicitly assigned
task can remain `assigned_unaccepted` with no project membership.

Task events retain a typed transition, optional source signal, before/after JSON
objects, reason, and creation time. Attention events retain the same evidence
shape with a required signal. Task 2 adds atomic Task transitions and append-only
Task event APIs; attention projections remain later work.

## Relationships and projections

- Task relations use `depends_on`, `blocks`, `supports`, `supersedes`, or
  `related_to`, with `proposed`, `confirmed`, or `rejected` status and a required
  supporting signal. Self-relations are invalid. Identity merging is separate.
- Cluster memberships preserve independent task records and use a unique
  cluster/task pair. Project candidates require an existing cluster and a reason.
  A confirmed candidate references an official project; a provisional or rejected
  candidate cannot claim that reference.
- Anchors have a registered type/reference, title, and active flag. The supported
  types represent the design's project, OKR, customer, product, revenue, financing,
  cash, key-hire, personnel, company-priority, and matter concepts. Registration
  authority and relevance derivation belong to `BusinessResolutionService`.
- Task/anchor links carry confirmation status, a separate active flag, and a
  required evidence signal. An official project references a unique canonical
  anchor using a composite foreign key that requires the anchor's type to be
  `project`; an existing customer anchor cannot masquerade as a project anchor.
- Attention items keep a unique stable key, category, active/resolved status,
  title, business area, why attention is needed, current state, CEO action,
  anchor, and supporting signal. Resolution requires a signal and resolution
  time; active items cannot carry those resolution fields. Reading is not a
  resolution status. Eligibility, aggregation, and recomputation remain later
  projection-service work.
- Legacy links use explicit nullable foreign-key columns, exactly one semantic
  endpoint and exactly one legacy endpoint (`work_projects`, `work_todos`, or
  `work_updates`). Each legacy row can be linked once; additional task evidence
  can point to the imported signal. This keeps both endpoints referentially
  constrained without a generic, unchecked entity ID. No import runs in Task 1.

All semantic references have foreign keys, membership links have unique keys,
and the required schema manifest covers all 15 tables, their columns, list
indexes, and source-immutability triggers.

## Primitive store API

`create_business_task_signal` and `create_business_task` return persisted integer
IDs. Callers pass these IDs directly to evidence linking and getters; the getters
return typed records. Create methods accept only explicit keyword fields and use
fixed INSERT column lists. The Task 1 APIs are
`create_business_task_signal`, `get_business_task_signal`, `create_business_task`,
`get_business_task`, `list_business_tasks`, `link_business_task_evidence`, and
`list_business_task_evidence`. The original signal-list primitive is retained.

Task listing accepts enum members or their canonical strings for optional
`stages`, `statuses`, and `relevance` collections. Filters combine with AND;
an explicitly empty collection matches nothing. Pagination defaults to
`limit=100`, `offset=0`, ordered stably by `updated_at, id`.
The default, unfiltered order is supported by
`idx_business_tasks_updated_id(updated_at, id)` without a temporary sort. The
stage/status and relevance filter indexes remain in place, and all three task
listing indexes are included in the required schema manifest.
`list_business_task_project_links` reads official projects through confirmed,
active task/anchor links to active project anchors, and returns an empty list
for a standalone task.

## Atomic semantic commands

`TaskSemanticService` records candidates/formal tasks, promotes candidates,
applies acceptance, updates Task state, and merges the same deliverable. Each
command commits its signal, Task changes, evidence links, and events together.
A signal previously collected through `create_business_task_signal` is reused
by deduplication key when its first semantic command is applied. Signal
existence alone does not mean that a command has already run; an existing Task
event identifies a replay and preserves the original result. Replays append no
new Task, signal, evidence, or event, including after a later merge.

A merged source remains historical. A fresh promotion, update, or acceptance
against it is rejected before signal persistence; it is not redirected to the
merge target. Merges remain one hop and reject already merged endpoints or a
source that itself has incoming merges.

Formal task creation and candidate promotion require structured
`FormalityEvidence`. The service derives the formal basis, commitment status,
and missing-owner evidence through `resolve_formality`; unauthorized
assignments and implicit deliverables are rejected before persistence. A
meeting action item with an explicit deliverable can be formal while its owner
is unresolved, in which case `owner` is recorded as missing evidence.

Same-deliverable merging requires structured `IdentityEvidence`. The service
merges only when the evidence identifies the same external task, cites an
explicit source reference, or establishes the full deliverable/owner/context/
time-window match. Weaker identity evidence is insufficient for merging.

## Business resolution commands

`BusinessResolutionService` supplies the transaction boundary for clusters,
typed Task relations, registered anchors, official Projects, Project candidates,
and Task relevance. It does not expose an API or run classification. Every
command operates on persisted IDs and validates referenced Tasks, anchors,
Projects, candidates, and evidence signals in the same write transaction.

A cluster groups existing Tasks through membership rows. It never rewrites a
member's owner, deadline, lifecycle status, or commitment status. Proposing a
Project for a cluster creates only a `business_project_candidates` row. An
official `business_projects` row can be registered only from an active canonical
anchor whose type is `project`, with a nonblank canonical registry source.
Confirming a Project candidate requires an already persisted official Project
and evidence signal; confirmation links the two existing rows and does not
create a Project.

Task relations require two existing, distinct Tasks and a persisted supporting
signal. They do not merge either Task. Proposed Task/anchor links also require a
persisted signal and leave Task relevance unchanged.

Anchor confirmation records the decision, links its evidence to the Task with
the typed `relevance` role, and recalculates the Task from every confirmed link
inside one transaction. A Task is `relevant` when at least one confirmed active
link points to an active registered anchor. An explicit `not_relevant` decision
is stored as a confirmed inactive link and applies only when no confirmed active
anchor remains. With neither condition, relevance is `unknown`. When the derived
value changes, the same transaction updates the Task timestamps and appends a
`relevance_changed` event containing the before and after Task snapshots. A
failure in any of these writes rolls the link, evidence, Task update, and event
back together.

The default projection input includes `unknown` and `relevant` Tasks. Confirmed
`not_relevant` Tasks remain available through ordinary filtered Task search but
are excluded from that projection input. No command treats free text, model
confidence, a cluster, or a Project candidate as authority for relevance or
official Project creation.

## Repair verification

The inherited three-file suite passed 433 tests before the repair, but lacked the
complete schema contract. The expanded semantic suite first failed with
`109 failed, 59 passed` using
`.venv/bin/pytest -q tests/test_task_semantic_store.py --tb=line`.
Missing columns, record classes, canonical enums, and primitive parameters were
the failures. After the schema/model/CRUD repair, the independent immutability
check remained red (`4 failed`, each `DID NOT RAISE IntegrityError`) for evidence
rewrite, provenance rewrite, deletion, and SQLite replacement. The corresponding
triggers made these checks green. Tests also exercise real dangling foreign-key
writes, canonical project-anchor type enforcement, valid record round trips,
invalid states, uniqueness, listing, source fidelity, and pre-semantic startup.

Initial contract repair validation: `.venv/bin/pytest -q tests/test_task_semantic_store.py
tests/test_store.py tests/test_task_models.py` passed **627 tests**. Python
compilation of both production files, Ruff on both production files and both
specified test files, and `git diff --check` also passed. These are isolated
storage checks, not evidence of a deployed runtime or applied legacy import.

The follow-up interface regression copies the approved standalone-task call
sequence unchanged: create signal ID, create task ID, link evidence, and get the
task. Before the return-value correction it failed with two Pydantic `int_type`
errors because `BusinessTask` and `BusinessTaskSignal` records reached the
integer evidence-link fields. Both create methods now return their persisted
integer IDs, and tests that inspect records use the getters. The same three-file
suite then passed **628 tests**, including this regression; compilation, Ruff,
and `git diff --check` also passed.

The nonblank/index quality regressions were run before the production correction:
`.venv/bin/pytest -q tests/test_task_semantic_store.py -k 'nonblank_text or
default_task_listing or list_indexes_are_present' --tb=line` produced
**135 failed, 76 passed, 202 deselected**. The 133 nonblank failures were direct
SQLite writes that did not raise `IntegrityError` after Pydantic had rejected the
same whitespace-only rows. The other failures were the absent default-list
index and the actual query plan's `USE TEMP B-TREE FOR ORDER BY`. Empty/plain-space
rejection and original-whitespace preservation already passed. The expanded
tests cover all 19 required text/resolution-time fields and capture the query
issued by the default listing API to inspect its plan.

After the correction, the same focused command passed **211 tests** with
202 deselected. The required three-file suite passed **838 tests**; Python
compilation, Ruff, and `git diff --check` also passed. These checks remain local
to the isolated Task 1 storage worktree; no runtime deployment was performed.
