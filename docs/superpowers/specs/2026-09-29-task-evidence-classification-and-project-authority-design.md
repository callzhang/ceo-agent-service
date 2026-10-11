# Task Evidence Classification and Project Authority Design

**Date:** 2026-09-29

**Status:** Historical draft, superseded by the Project-centered implementation

This pre-sync draft is retained for provenance. Its proposed precedence and
source-authority rules are historical proposals. Current service behavior is
defined in `docs/architecture.md` and `docs/runtime-mechanism.md`.

**Amends:** `docs/superpowers/specs/2026-09-22-task-first-tasks-design.md`

**Scope:** Task Agent evidence classification, management-report authority, Task-to-Project resolution, candidate projection, and the Tasks console.

This document narrows the Task-first design after a read-only review of the
live service data and recent meeting evidence. Where this document conflicts
with an earlier generic rule, these source-classification and projection rules
take precedence. It does not reintroduce periodic completion checks or a
completion Agent: Tasks still change only when new evidence is scanned.

## 1. Problem statement

The current Task-first pipeline is producing a flat list dominated by
candidates:

- the reviewed snapshot contained 87 business Tasks, of which 2 were formal
  and 85 were candidates;
- the newest batch contained 18 candidates and 2 formal Tasks;
- the business Task signals behind the current data were AI Minutes signals,
  without management-weekly-report or department-weekly-report inputs;
- no active business Anchor, Work Cluster, Project Candidate, or official
  Project existed in the reviewed snapshot;
- the `需关注` projection was empty because no Task had a confirmed business
  Anchor.

This is not one defect. Four layers are currently mixed:

1. a source-backed discovery candidate;
2. a formal Task with an assignment or action-item basis;
3. a commitment state such as assigned-but-not-accepted or accepted;
4. a canonical Project or Project Candidate.

The console's “全部任务” view intentionally returns every Task stage, so
candidate records appear beside formal Tasks. Separately, the Task Agent is
often treating missing enrichment fields—stable directory ID, normalized date,
or completion standard—as reasons to keep an otherwise explicit meeting action
as a candidate. Finally, the source scanner does not yet feed management weekly
reports into the canonical Project/Task resolution path.

## 2. Goals

1. Classify explicit meeting action items consistently as formal Tasks without
   requiring optional enrichment fields.
2. Keep commitment acceptance separate from Task existence.
3. Make management weekly reports the primary Project and current-state
   authority, with department/project reports and confirmed meeting evidence
   following in a defined order.
4. Make Task-to-Cluster-to-Project resolution deterministic and evidence-backed.
5. Keep candidates useful but out of the default formal-task experience.
6. Prevent unrelated low-impact work from becoming default CEO-facing work.
7. Preserve source provenance, raw evidence, and historical records during
   reclassification.
8. Make the decision boundary testable with replayable recent-evidence cases.

## 3. Non-goals

- No separate completion Agent, completion task, or periodic “is this done?”
  check.
- No automatic creation of DingTalk TODOs or other external records from the
  Task Agent.
- No automatic promotion of every candidate into a formal Task.
- No official Project created from semantic similarity, a chat message, or an
  unconfirmed cluster alone.
- No deletion of existing candidate records or source evidence.
- No incidental authorization, audit, or external-action safety policy.
- No assumption that every sustained work item is a Project; standalone Tasks
  and Matters remain valid outcomes.

## 4. Terminology and state model

### 4.1 Source signal

A source signal is an observed item from a meeting, management report,
department report, project report, chat, email, external TODO status, or
legacy record. It is immutable evidence, not a Task.

The signal retains:

- source type and stable source reference;
- source timestamp and reporting period where applicable;
- source link or bounded source description;
- author/assigner identity when authoritative;
- exact evidence excerpt;
- source-specific context such as meeting title, document ID, or conversation.

### 4.2 Candidate

A Candidate is a plausible source-backed action that does not yet meet the
formal-Task threshold. It remains discoverable and may later be promoted by a
new source signal.

Candidate examples:

- a discussion with no concrete deliverable;
- a proposal with no identified owner;
- a time window that still needs confirmation;
- a team-level action where no individual responsibility can be established;
- conflicting source evidence that cannot yet be resolved.

### 4.3 Formal Task

A Formal Task is a source-backed deliverable or next action with an explicit
owner and a valid formal basis. A deadline, stable directory ID, completion
standard, Project, or owner acceptance is not required for Task existence.

The allowed formal bases are:

- `explicit_commitment`;
- `explicit_assignment`;
- `external_todo` for a trusted external TODO source;
- `meeting_action_item` for a sourced meeting action-item record.

The formal Task's `commitment_status` is independent:

- `assigned_unaccepted` for an assignment or meeting action item with no
  explicit owner acceptance;
- `accepted` only when the identified owner explicitly accepts or commits;
- other existing commitment states remain governed by the Task-first design.

### 4.4 Cluster and Project

A Cluster groups distinct Tasks that serve a common goal. It never merges task
identity and never copies owner, deadline, status, or commitment state between
members.

An official Project is a canonical business object backed by a registered
Project Anchor and an authoritative registry source. A Cluster may produce a
Project Candidate, but cannot create an official Project by itself.

### 4.5 需关注

`需关注` is a projection over formal Tasks, Clusters, Matters, and Projects.
It requires both:

1. a confirmed active business Anchor; and
2. a material trigger such as a threatened accepted commitment, material risk
   change, dispute, CEO decision, required Gate, or meaningful escalation.

A candidate with no Anchor is not silently upgraded into `需关注`; it remains
visible through candidate diagnostics.

## 5. Source authority order

The service must resolve conflicting sources in this order:

1. the latest confirmed management weekly report;
2. a confirmed project or department weekly report with explicit project
   fields;
3. a confirmed meeting, transcript, or meeting action item;
4. chat, email, and other supplementary messages;
5. model inference, similarity, and memory context.

Lower-ranked evidence may add context, owner evidence, status evidence, or a
link, but it cannot override an explicit higher-ranked field.

Every report-derived Project or Task update must preserve its exact document
reference and reporting period. A report may aggregate meeting and project
communications, but the report itself remains the canonical source for fields
it explicitly states.

## 6. Formal-Task classification rules

### 6.1 Minimum threshold

The service must treat a source item as formal when all of the following hold:

1. the source contains a concrete deliverable or next action;
2. the responsible individual is explicitly named by source text or
   authoritative metadata;
3. the formal basis is valid for the source type.

The service must not require all of the following before formalization:

- a stable directory/user ID;
- a parseable absolute deadline;
- a completion location;
- an independent acceptance message;
- an existing Project.

Those are separate evidence fields. If absent, they are recorded as missing
enrichment evidence while the Task remains formal.

### 6.2 AI Minutes rule

For an AI Minutes Work Item whose source contains a DingTalk action-item
record (`#todos-sha256=`):

```text
explicit deliverable
+ identifiable individual owner from transcript or meeting_summary
=> formal Task with basis meeting_action_item
=> commitment_status assigned_unaccepted unless explicit owner acceptance exists
```

The action item's empty `executorList` must not by itself force candidate
status. The scanner already includes transcript excerpts and the complete
meeting summary; the Agent must use them to cite the owner.

If the owner is not an individual, is a DingTalk placeholder, or remains
ambiguous after both sources are read, the result stays a candidate.

If the item is only a discussion, an unconfirmed proposal, or a time window
without a concrete action, it stays a candidate.

### 6.3 Basis normalization

An AI Minutes Work Item must never be validated as `external_todo`. Before
formal-basis validation:

- `ai_minutes` plus `#todos-sha256=` is normalized to `meeting_action_item`;
- `external_todo` remains reserved for trusted external TODO input types;
- source-reference normalization occurs before validation and repair prompts.

This prevents an AI Minutes decision from entering an external-TODO validation
branch and then being conservatively rewritten as a candidate.

### 6.4 Owner evidence

The owner may be persisted as a source-backed name without a stable directory
ID. `owner_user_id` is populated only when an exact directory lookup maps the
source-named individual.

An owner citation must include:

- the original source reference;
- an excerpt containing the person's name or an unambiguous source statement;
- no DingTalk placeholder such as “speaker N”;
- no team or department name in place of an individual.

### 6.5 Dates

Dates retain their semantic type and source phrase. “Tomorrow”, “after the
holiday”, or “October 3–5” may remain raw evidence when they cannot be safely
normalized. Missing or non-parseable dates do not block Task creation.

The existing meanings remain separate:

- `created_at` — when the service recorded the Task;
- `assigned_at` — when the source shows assignment;
- `requested_deadline_at` — an assigner's requested date;
- `external_deadline_at` — a date from an external system;
- `committed_deadline_at` — a concrete date explicitly accepted by the owner;
- `estimated_deadline_at` — an estimate;
- `next_check_at` — an Agent-authored operational check time.

## 7. Agent/service contract

The Task Agent remains responsible for reading context and returning a
structured `TaskAgentDecision`. The service remains responsible for validating
and applying it.

The Agent must:

- extract zero or more source-backed decisions;
- cite the original source and exact excerpt;
- identify the owner from transcript/summary evidence;
- select `meeting_action_item` for eligible AI Minutes action items;
- distinguish `assigned_unaccepted` from `accepted`;
- omit unsupported date facts rather than inventing them;
- propose relations and project matches only with persisted supporting evidence.

The service must:

- normalize source provenance before validation;
- reject invalid basis/source combinations;
- apply the formal-Task threshold independently of optional enrichment;
- preserve candidate evidence when a candidate is not promoted;
- avoid allowing generic updates to mutate commitment status;
- record the classification reason and missing enrichment fields.

The service must not silently convert a model validation failure into a
candidate without recording why the formal decision was rejected. A repair turn
may correct a malformed decision, but it must not lower a valid formal action
item merely because optional evidence is absent.

## 8. Management-report ingestion and Project authority

### 8.1 New source classes

The Task source pipeline must accept:

- `management_weekly_report`;
- `project_weekly_report`;
- `department_weekly_report`;
- existing `ai_minutes` and message sources.

Each report source must be indexed with document ID, URL, reporting period, and
bounded content excerpts. The importer must be idempotent by source reference
and content digest.

### 8.2 Report extraction

For each report, extract only explicit fields:

- project or workstream name;
- owner or responsible team/individual;
- target or goal;
- deadline/period;
- current status;
- next tasks;
- risk or decision needed;
- exact report citation.

The importer must not turn every heading or sentence into a Project. A Project
Anchor requires an explicit project/workstream identity and a canonical report
reference.

### 8.3 Meeting reconciliation

When a new meeting action item arrives:

1. match it to existing Project Anchors using explicit names and aliases;
2. apply the meeting as a new Task or Task update;
3. preserve the weekly report's current-state fields unless the meeting is a
   newer confirmed decision;
4. record the meeting as supporting evidence;
5. flag unresolved source conflicts instead of overwriting silently.

Chat may add context but cannot create an official Project alone.

## 9. Deterministic Task-to-Project flow

```text
source evidence
    ↓
Task candidate or formal Task
    ↓
identity merge only for identical deliverables
    ↓
match existing Project Anchor
    ├─ match: link Task to Project
    └─ no match: create/extend Work Cluster proposal
                       ↓
              Project Candidate proposal
                       ↓
       weekly-report authority or explicit confirmation
                       ↓
                official Project registration
```

The model may suggest a Cluster or Project match, but the semantic service
must validate the referenced Task, source signal, Anchor, and Project IDs in one
transaction. A similarity score alone is never authority.

## 10. Candidate and low-impact projection

Candidates remain searchable history, but the default formal-task view must
not treat them as ordinary Tasks.

The default candidate projection includes only candidates that satisfy at least
one of these conditions:

- source is a management, project, or confirmed department report;
- source is a confirmed meeting with an identifiable business workstream;
- candidate has a named owner and material business relevance;
- candidate has a material risk, decision, or missing authority that needs
  review.

Small unrelated work remains stored as evidence or a candidate record when the
source workflow requires it, but it is excluded from the default CEO-facing
list. It must not be auto-labelled `not_relevant` without evidence.

## 11. Console information architecture

The Tasks console must expose separate views:

1. `需关注` — active attention projections only;
2. `正式任务` — formal Tasks, including assigned-but-not-accepted Tasks;
3. `待确认线索` — candidates needing evidence or human confirmation;
4. `正式项目` — official Projects and their linked Tasks.

If a diagnostic “all records” view remains, it must be labelled `全部记录`,
not `全部任务`, and must display stage clearly.

Counts must be separate:

```text
正式任务：N
待确认线索：M
正式项目：P
需关注：Q
```

An empty `需关注` view must explain that no material anchored trigger is
currently active. It must provide links to formal Tasks and pending evidence,
not imply that no work exists.

## 12. Migration and reclassification

Existing records are not rewritten blindly.

### 12.1 Dry-run inventory

Before any data change, produce a manifest containing:

- Task ID and current stage;
- source signal and source type;
- proposed stage/formal basis;
- owner evidence;
- missing enrichment fields;
- Project/Anchor match;
- reason and confidence;
- whether a human review is required.

### 12.2 Safe automatic reclassification

The service may promote an existing candidate only when its original source
signal satisfies the formal threshold and the promotion is idempotent. The
promotion must preserve the original discovery evidence and append a
classification event.

The first replay batch must contain:

- an explicit individual assignment with ambiguous/non-parseable date;
- a clear self-commitment;
- a time-window-only proposal;
- an ownerless team action;
- an existing formal meeting action item.

No broad bulk promotion is allowed until this batch's event and API readback
match the expected results.

### 12.3 Historical-only candidates

Candidates that are unsupported, duplicated, or unrelated to a business line
remain in history. They are not deleted and are not shown in the default
formal-task or attention projections.

## 13. Acceptance criteria

### Classification

1. A meeting action item with an explicit deliverable and named individual is
   persisted as `formal / meeting_action_item` even when the owner has no
   stable directory ID or the date is not parseable.
2. The same item is `assigned_unaccepted` unless the owner explicitly accepts
   or commits.
3. A discussion, ownerless team action, or unconfirmed time window remains a
   candidate.
4. AI Minutes never enters the `external_todo` validation branch.
5. A validation repair cannot silently downgrade a valid formal action item to
   candidate status.

### Authority and projects

6. A management weekly report creates or updates a canonical Project Anchor
   only when it explicitly identifies a project/workstream.
7. A meeting action item that explicitly starts, approves, or establishes a
   named project may create a Project Anchor with `meeting_decision` provenance
   and link the Task to it in the same transaction.
8. A confirmed meeting action item links to an existing Anchor without
   overwriting newer/higher-authority report fields.
9. A chat-only source cannot create an official Project.
10. A Cluster or Project Candidate is never rendered as an official Project.
11. A Task with no Anchor remains a valid Task and has `business_relevance`
    separate from its lifecycle stage.

### Console

11. The formal-task view excludes candidates by default.
12. The candidate view displays the missing-evidence reason and source link.
13. The Project view shows official Projects separately from Project
    Candidates.
14. The empty Attention state explains the missing material anchored trigger.
15. Candidate and formal counts are not combined in the primary Task metric.

### Evidence and operations

16. Every reclassification retains original source evidence and produces an
    auditable event.
17. Replaying the same source digest is idempotent.
18. The focused replay suite covers the five classification fixtures above.
19. Metrics report formal/candidate ratios, authority-source coverage, Anchor
    coverage, Project matches, Attention projection, and repair counts.

## 14. Implementation boundaries

The implementation should be split into independently testable changes:

1. **Classification contract:** Task Agent prompt, source-basis normalization,
   formal threshold, and focused Task Agent/service tests.
2. **Report authority ingestion:** report source adapter, idempotent indexing,
   Anchor extraction, and report-source tests.
3. **Reconciliation:** deterministic Anchor matching, Cluster/Project proposal
   wiring, and semantic-service tests.
4. **Projection/UI:** formal-task and candidate endpoints, console labels,
   counts, and responsive UI tests.
5. **Migration:** dry-run manifest, bounded replay, readback, and repair
   documentation.

Each change must update the relevant architecture/runtime document in the same
commit. No deployment or data migration is implied by this design document.
