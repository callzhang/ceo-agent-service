# Task-first semantic storage (Task 1)

This document describes the storage introduced by Task 1 of the approved
[implementation plan](superpowers/plans/2026-09-22-task-first-tasks.md). It does
not describe a deployed Task Agent cutover. The runtime, console, providers,
transition services, and legacy import workflow still belong to later tasks.

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
and transitions are part of Task 2.

Source observations attach through `business_task_evidence`, whose required
`task_id`, `signal_id`, and typed `evidence_role` form its composite key. There is
no redundant direct source or project pointer on a task. An explicitly assigned
task can remain `assigned_unaccepted` with no project membership.

Task events retain a typed transition, optional source signal, before/after JSON
objects, reason, and creation time. Attention events retain the same evidence
shape with a required signal. This task defines their records and storage only;
atomic transition/event creation and append-only event APIs remain Task 2 work.

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
  authority and relevance derivation are later resolution-service behavior.
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

The create/get methods return typed records; create methods accept only explicit
keyword fields and use fixed INSERT column lists. The Task 1 APIs are
`create_business_task_signal`, `get_business_task_signal`, `create_business_task`,
`get_business_task`, `list_business_tasks`, `link_business_task_evidence`, and
`list_business_task_evidence`. The original signal-list primitive is retained.

Task listing accepts enum members or their canonical strings for optional
`stages`, `statuses`, and `relevance` collections. Filters combine with AND;
an explicitly empty collection matches nothing. Pagination defaults to
`limit=100`, `offset=0`, ordered stably by `updated_at, id`.
`list_business_task_project_links` reads official projects through confirmed,
active task/anchor links to active project anchors, and returns an empty list
for a standalone task.

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

Final repair validation: `.venv/bin/pytest -q tests/test_task_semantic_store.py
tests/test_store.py tests/test_task_models.py` passed **627 tests**. Python
compilation of both production files, Ruff on both production files and both
specified test files, and `git diff --check` also passed. These are isolated
storage checks, not evidence of a deployed runtime or applied legacy import.
