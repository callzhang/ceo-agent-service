# Task-first semantic storage, business resolution, and attention projection (Tasks 1–6)

This document describes the storage, atomic commands, decision rules, and business
resolution commands introduced by Tasks 1–5, plus the Task 6 owner, acceptance,
and typed-date semantic contract of the approved
[implementation plan](superpowers/plans/2026-09-22-task-first-tasks.md). It does
not describe a deployed Task Agent cutover. The runtime, console, providers,
and legacy import workflow still belong to later tasks.

The schema version is `2026-09-23.1`. Initialization adds 16 bounded semantic
tables to a pre-semantic database without reclassifying, copying, or deleting
legacy work records. An existing `2026-09-22.1` semantic database gains the
append-only date-evidence table and signal actor kind; its Task events retain
their IDs and history while their constraint gains `date_evidence_recorded`.
The migration leaves every old, untyped `business_tasks.deadline_at` value
unchanged and unclassified. Event-table rename, creation, copy, and index
recreation run in one SQLite savepoint. A failed copy rolls the table rename
back; initialization also recovers an older interrupted migration that left
the renamed source table beside an incomplete replacement, after checking
that overlapping event rows agree.

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
identity/title, author identity/name/kind (`human`, `system`, `agent`, or
`unknown`), original evidence text, and context.
Unknown source context stays empty rather than being fabricated. Evidence text
and source strings are not trimmed or summarized when stored. The caller supplies
the source/content deduplication key; its unique constraint rejects duplicates.
SQLite triggers prohibit UPDATE, DELETE, and replacement of an existing signal.
The primitive create method raises on duplicate input; returning an existing
signal from a multi-row command is part of Task 2's transaction service.
Reuse by deduplication key requires every persisted source and actor field to
match the submitted signal, including on a replay; a matching key cannot
turn an unknown or system observation into a human statement.

`business_tasks` stores description, owner identity and supporting evidence,
an opaque legacy deadline field, missing evidence, last activity, and separate stage, lifecycle,
commitment, and relevance fields. Formal tasks require one of the four approved
bases; candidates cannot carry one. A merged task requires an existing, distinct
merge target, and other statuses cannot carry a merge target. Merge-chain checks
and transitions are enforced by Task 2's semantic service.

Source observations attach through `business_task_evidence`, whose required
`task_id`, `signal_id`, and typed `evidence_role` form its composite key. There is
no redundant direct source or project pointer on a task. An explicitly assigned
Task or external TODO remains `assigned_unaccepted` without owner acceptance
or project membership. A formal Task requires an identified owner and an exact
source citation. An ownerless action stays candidate or unmatched.
An owner ID with a name must match the source's human author identity or its
structured `context_json.owner_identity` user ID/name pair. Co-occurrence of
two people's names and IDs in prose does not establish a mapping. An ID without
a name may be cited directly in the excerpt. A source-backed name without a verified ID can be retained,
but that owner cannot satisfy the owner-ID acceptance requirement. Owner
changes through the generic update command also need a fresh matching source
excerpt and source reference. If a source-backed reassignment changes the
identified owner of an accepted Task, the same `owner_changed` event records
the prior accepted state and the new `assigned_unaccepted` state. The former
owner's acceptance does not transfer to the new owner.

`business_task_date_evidence` holds append-only typed facts: `assigned_at`,
`requested_deadline_at`, `external_deadline_at`, `committed_deadline_at`,
`estimated_deadline_at`, and `next_check_at`. Each fact stores its source signal,
actor kind/identity, original phrase, optional parsed ISO date or datetime,
and creation time. Non-committed date language may remain raw with an empty
parsed value. `committed_deadline_at` requires a concrete parsed value and the
identified human owner's actor ID; only an owner-authored explicit commitment
or dedicated acceptance transition can record it. Task `created_at` remains
the system-recorded creation time. Business Tasks may have no date. A concrete
parseable due date is required separately for a later legacy/DingTalk TODO mirror.
Each date phrase must occur verbatim in its source signal. Source-derived date
actor kind, ID, and name must match that signal's author. An operational
`next_check_at` may instead carry an explicit Agent actor ID while quoting the
human source phrase that supplied the timing.
Date facts attributed to a different human speaker in meeting minutes require
structured speaker identity tied to the quoted source span. The current Task
Agent input does not supply that mapping, so those facts remain unrecorded
instead of assigning the minutes system author or an inferred participant.

Task events retain a typed transition, optional source signal, before/after JSON
objects, reason, and creation time. Attention events retain the same evidence
shape with a required signal. Task 2 adds atomic Task transitions and append-only
Task event APIs; attention projections remain later work.

## Relationships and projections

- Task relations use `depends_on`, `blocks`, `supports`, `supersedes`, or
  `related_to`, with `proposed`, `confirmed`, or `rejected` status and a required
  supporting signal. Their composite identity keeps one stable SQLite row. An
  exact status/evidence replay is a no-op; a proposal may become confirmed or
  rejected using new persisted evidence; a later proposal cannot downgrade a
  terminal decision; conflicting terminal decisions are rejected. Self-relations
  are invalid. Identity merging is separate.
- Cluster memberships preserve independent task records and use a unique
  cluster/task pair. Project candidates require an existing cluster and a reason.
  A confirmed candidate references an official project and the persisted signal
  that authorized confirmation; a provisional or rejected candidate can claim
  neither reference.
- Anchors have a registered type/reference, title, and active flag. The supported
  types represent the design's project, OKR, customer, product, revenue, financing,
  cash, key-hire, personnel, company-priority, and matter concepts. Registration
  authority and relevance derivation belong to `BusinessResolutionService`.
- Task/anchor links have stable persisted IDs and carry confirmation status, a
  separate active flag, and a required evidence signal. An official project
  references a unique canonical anchor and persists its nonblank
  `registry_source`. The composite foreign key requires the anchor's type to be
  `project`; an existing customer anchor cannot masquerade as a project anchor.
- Attention items keep a unique stable key, category, active/resolved status,
  title, business area, why attention is needed, current state, CEO action,
  anchor, and supporting signal. Resolution requires a signal and resolution
  time; active items cannot carry those resolution fields. Reading is not a
  resolution status. `BusinessAttentionProjection` owns the persisted
  eligibility, aggregation, update, resolution, and idempotent recomputation
  commands described below.
- Legacy links use explicit nullable foreign-key columns, exactly one semantic
  endpoint and exactly one legacy endpoint (`work_projects`, `work_todos`, or
  `work_updates`). Each legacy row can be linked once; additional task evidence
  can point to the imported signal. This keeps both endpoints referentially
  constrained without a generic, unchecked entity ID. No import runs in Task 1.

All semantic references have foreign keys, membership links have unique keys,
and the required schema manifest covers all 16 tables, their columns, list
indexes, and source/date-immutability triggers.

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
for a standalone task. `list_business_tasks_for_projection` reads the complete
eligible set in one snapshot and therefore does not silently truncate the
projection source at the ordinary 100-row Task listing default.
`list_business_task_date_evidence` returns one Task's complete typed date
history in insertion order. Readers do not classify the old `deadline_at`.

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
`FormalityEvidence`, an identified owner, and an exact owner excerpt linked to
a source signal. The service derives formal basis and commitment status through
`resolve_formality`; unauthorized assignments, implicit deliverables, and
ownerless formalization are rejected before persistence. An external TODO is
formal but never proves the human owner accepted it. `ApplyAcceptance` requires
an explicit source excerpt, a human author ID equal to the Task owner, and a
prior source signal uniquely linked to that one unmerged formal Task. The
command must supply `AcceptancePolarity.ACCEPTED`; a declined or ambiguous
semantic finding rejects the transition even when `acceptance_is_explicit` is
true. The exact acceptance excerpt must occur in the human owner's source,
and that source's `context_json.reply_to_source_ref` must match the persisted
referenced signal's source reference. This binds a short reply such as
“我来做。” to the uniquely linked Task without expecting an opaque source ID or
full Task title in natural speech. The eventual source adapter must preserve
and verify reply metadata from the provider; caller-written context is not
independent proof of a provider reply. The
acceptance signal and role commit together. Generic `UpdateBusinessTask`
cannot set commitment status or write the old untyped deadline; date inputs
create typed evidence rows instead.

Same-deliverable merging requires structured `IdentityEvidence`. The service
merges only when the evidence identifies the same external task, cites an
explicit source reference, or establishes the full deliverable/owner/context/
time-window match. Weaker identity evidence is insufficient for merging.
The merge also carries append-only typed date facts to the surviving Task,
preserving each fact's original signal, actor, phrase, parsed value, and
creation time. Replays or an already-present identical fact do not duplicate it.

## Business resolution commands

`BusinessResolutionService` supplies the transaction boundary for clusters,
typed Task relations, registered anchors, official Projects, Project candidates,
and Task relevance. It does not expose an API or run classification. Every
command operates on persisted IDs and validates referenced Tasks, anchors,
Projects, candidates, and evidence signals in the same write transaction.

A cluster groups existing Tasks through membership rows. It never rewrites a
member's owner, deadline, lifecycle status, or commitment status. Proposing a
Project for a cluster creates only a `business_project_candidates` row. An
official `business_projects` row can be registered from an active canonical
anchor whose type is `project`, with a nonblank canonical registry source. The
other authority path is an explicit candidate confirmation backed by a persisted
signal: it may create the official Project from an already registered active
project anchor and records `explicit_confirmation:<signal_id>` as the Project's
registration source while the candidate retains the signal foreign key. It
confirms the candidate in the same transaction. A cluster or
candidate proposal alone never creates an official Project. Confirmation may
also target an already persisted Project. Replaying the same candidate, Project,
and confirmation signal returns the existing result; a different Project or
signal is rejected.

Task relations require two existing, distinct Tasks and a persisted supporting
signal. They do not merge either Task. Proposed Task/anchor links also require a
persisted signal, return their stable link ID, and leave Task relevance unchanged.

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

## CEO attention projection

`BusinessAttentionProjection` accepts a typed `AttentionProposal` and uses its
`stable_key` as the durable attention identity. A proposal requires nonblank
title, why, current-state, and CEO-action text; all linked Tasks must exist and
be unmerged; at least one must be relevant; and its registered anchor must be
confirmed and active for at least one linked Task. Its supporting signal must
exist and already be linked to an underlying Task. A proposal may say
`当前无需处理`, while still recording material information, risk, decision, or
push context through its category and explanatory fields.

Proposal Task IDs are unique and stored in ascending ID order before comparison
or persistence, so equivalent input orderings cannot create membership updates
or events. Current eligible members remain a separate ordered set. A proposal
may include a terminal Task when it has valid relevance and anchor evidence;
it is absent from current membership but remains in desired proposal membership
for a later evidence-backed resolution.

The command creates an `opened` event for a new item and keeps the same item ID
when fields or category change. It records `updated`, `category_changed`, or
`reopened` events with before/after snapshots only when the semantic item fields
change. Attention Task links are idempotent membership facts, so one item can
aggregate several independently open Tasks. Resolution requires a persisted
signal already linked to an underlying Task and appends one `resolved` event.
`record_viewed` performs no authoritative write and cannot resolve an item.

`recompute_for_tasks` never creates attention from a relevant anchor alone. It
refreshes only existing, explicitly proposed attention items that reference the
requested Tasks. It retains each item's category and active/resolved state, and
uses only relevant `open` or `waiting` Tasks with a confirmed active anchor
link as current membership. A membership change removes stale links, appends an
`updated` event with Task IDs in its before/after snapshots, and leaves an item
active even when no eligible member remains because resolution still requires
evidence. The latest explicit proposal membership is persisted separately from
current eligible membership. Recompute locates items through that desired set,
so a completed, cancelled, non-relevant, or inactive-anchor Task can return to
the same attention item when eligibility returns; a newer explicit proposal
removal changes the desired set and prevents resurrection. Every immutable
attention lifecycle snapshot records both `task_ids` (the current eligible
members) and `proposal_task_ids` (the explicit desired proposal members).
Historical resolution lineage reads both snapshot sets as well as the current
desired set, so an explicit proposal removal changes only future membership and
does not erase evidence needed to resolve an older item. Resolution snapshots
record the current member IDs on both sides of the state transition. Recompute
preserves proposal-owned
`current_state` text and changes it only through a later explicit proposal.
When current membership changes, recompute updates the attention item's
`updated_at` in the same transaction as its membership event. Repeating
unchanged recomputation adds no attention event, link, or timestamp update.

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

## Task 6 retrieval context (read side)

Task-first retrieval ranks candidate and existing formal Tasks as context for
the Agent, never as authority to assign an owner, infer acceptance, confirm an
anchor, or create an official Project. A legacy row marked `formal` is returned
under `unverified_formal_tasks`, not `existing_formal_tasks`, unless its owner
identity and source quote are present and the quoted source signal is attached
to that Task. This preserves old rows for comparison without promoting
unsupported ownership to a verified task fact.

The context includes the raw source signals cited by selected Task evidence,
relations, and anchor links. Relations and anchor links are capped per selected
Task, not globally, so an earlier Task cannot consume a later Task's entire
evidence window. Anchors reached through selected links, and official Project
registry entries whose canonical anchor is among those links, remain in context
regardless of their title's lexical rank; link status still governs whether the
match is merely proposed or confirmed. The Store provides paginated
`list_business_work_cluster_tasks` reads by cluster and/or Task. Retrieval
includes selected Tasks' cluster links and bounded members of the returned
clusters, so a cluster ID is grounded in actual membership rather than its
title alone. This is a read-side contract only; it does not cut over the
Task Agent caller or alter semantic writes.
