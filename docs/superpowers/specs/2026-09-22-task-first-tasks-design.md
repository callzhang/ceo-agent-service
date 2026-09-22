# Task-first Tasks and CEO Attention Design

**Date:** 2026-09-22

**Status:** Approved for implementation planning

**Scope:** CEO Agent Service Tasks domain model, derivation flow, migration boundary, and console information architecture

## 1. Outcome

Replace the current Project-first interpretation of work with a Task-first semantic layer. The service will first identify possible tasks, promote evidence-backed tasks independently of projects, merge only true duplicates, cluster related work, resolve official project membership separately, and derive a small CEO-facing list of **需关注事项**.

The Tasks homepage will default to 需关注. 全部任务 and 正式项目 remain available as secondary views. A CEO attention item is not assumed to require intervention: it may be 仅需知晓, 持续观察, 需要决策, or 需要推动.

## 2. Current Problem

The current contract structurally forces work into projects:

- `WorkItem` carries `project_name` before the task decision is made.
- `TaskAgentDecision.action` can only `skip`, `create_project`, or `update_project`.
- `WorkTodo.project_id` is mandatory.
- The Tasks API exposes each `work_project` as a task summary and nests TODOs beneath it.

As a result, `work_projects` contains a mixture of official projects, tasks, TODO-like items, milestones, matters, and temporary work containers. Filtering or restyling the current page would preserve that category error.

The production data also contains a large historical backlog. Existing rows cannot be bulk-promoted into the new model because their original authority, granularity, project identity, and business relevance are not consistently proven.

## 3. Design Principles

1. **Task existence is independent of project membership.** A valid task may be standalone.
2. **Task existence is independent of commitment acceptance.** An explicit assignment is a task even when acceptance is not yet confirmed.
3. **Same work and related work are different relations.** Same work may merge; related work may only cluster or link.
4. **Official projects are canonical business objects.** Semantic similarity cannot silently create one.
5. **CEO attention is a projection, not a second task database.** One attention item may summarize several tasks.
6. **Business relevance is evidence-backed.** Small tasks unrelated to the company’s main business lines remain searchable but do not surface by default.
7. **History remains traceable.** Legacy records and source evidence are preserved even when their current interpretation changes.
8. **This change adds no incidental authorization, audit, or external-action safety policy.** Such behavior remains outside this design.

## 4. Chosen Approach

Three approaches were considered:

- **A. Filter and restyle the current Project-first page.** Fast, but it leaves the mixed entity model intact.
- **B. Add a Task-first semantic layer over preserved historical evidence.** Corrects the model without requiring a full event-system rewrite.
- **C. Rebuild the subsystem as a complete event and relationship graph.** Strongest long-term history model, but too broad for this iteration.

Approach **B** is selected. The new semantic model becomes the source for new Tasks views. Existing `work_projects` and `work_todos` remain historical evidence during migration, not the authority for what counts as an official project.

## 5. Domain Boundaries

### 5.1 Source signal

A source signal is an observed message, meeting action item, email, external TODO, document statement, or existing legacy record. It is immutable evidence about possible work; it is not itself a task.

Each signal retains its source type, external reference, source time, conversation or document context, author or assigner when known, and original evidence text.

### 5.2 Task Candidate

A Task Candidate is a plausible action that has not yet met the formal-task threshold. Discussion, suggestions, ambiguous requests, and incomplete action language remain candidates.

Example:

> 美国报价可以研究一下。

This is a candidate because the deliverable, authority, and commitment are unclear.

### 5.3 Formal Task

A candidate becomes a formal Task when at least one of the following is evidenced:

- a person explicitly commits to a deliverable or next action;
- an authorized participant explicitly assigns a deliverable or next action;
- an external system contains a formal TODO;
- a meeting produces an explicit action item with a deliverable or next action.

Example:

> 王明，周五前提交美国客户报价第一版。

This is a formal Task with commitment state `assigned_unaccepted`. A later reply such as “收到，我周五提交” changes the commitment state to `accepted`; it does not create another Task.

Task existence and commitment state are separate fields. Missing project membership does not block Task creation.

### 5.4 Work cluster

A work cluster groups distinct tasks that share a real business goal or context. The member tasks retain independent completion and ownership.

For example, preparing a quote, scheduling a demo, preparing a contract, and confirming payment terms may cluster under “美国客户成交”. They must not merge because each has its own deliverable.

### 5.5 Official Project and Project Candidate

An official Project comes from the canonical project registry or an explicit confirmation flow. A task or cluster may match an existing Project when the evidence is sufficiently specific.

When related work appears durable but does not match the registry, the service may propose a Project Candidate. A Project Candidate remains provisional until confirmed. The Agent cannot silently create an official Project from clustering alone.

### 5.6 Matter and standalone Task

Not all sustained work is a Project. Recruiting a role, handling a case, or managing an ongoing relationship may be a Matter. One-time actions may remain standalone Tasks. Neither state is an error or an incomplete Project assignment.

### 5.7 需关注事项

A 需关注事项 is a CEO-facing projection derived from one or more business-relevant Tasks, clusters, or official Projects. It answers:

- what changed or may go wrong;
- why the CEO should care;
- what the current state is;
- whether the CEO has an action now;
- which underlying Tasks and evidence support the projection.

It does not replace or duplicate its underlying Tasks.

## 6. Relationships and Identity

The semantic layer supports three distinct operations:

### Merge

Merge only when evidence establishes that two observations refer to the same real-world deliverable. Strong evidence includes the same external task ID, an explicit source reference or reply chain, or a highly specific match across deliverable, owner, context, and time.

The resulting Task keeps every source as evidence. Merge never discards provenance.

### Cluster

Cluster tasks that are distinct but support the same business goal. Cluster membership does not transfer completion, ownership, deadlines, or commitment state between Tasks.

### Link

Use typed links for relationships that are neither identity nor simple membership, including `depends_on`, `blocks`, `supports`, `supersedes`, and `related_to`.

When identity is uncertain, the service must link or cluster rather than merge.

## 7. Business Relevance

All formal Tasks may be retained and searched, but only business-relevant work is eligible for the default Tasks experience and attention projection.

Hard business anchors include:

- an official Project or OKR;
- a key customer or customer commitment;
- core product delivery;
- revenue, financing, or material cash impact;
- a key hire or material personnel decision;
- another explicitly registered company priority.

Agent semantic inference may propose a relevance candidate, but inference alone cannot promote an unrelated small task into a CEO-facing item.

Examples that remain out of the default CEO view include routine reimbursements, office supplies, ordinary meeting-room coordination, and isolated low-impact bugs. They remain available in 全部任务 when retained by the source workflow.

## 8. Attention Categories and Lifecycle

Each active attention item has one category:

- **仅需知晓:** material context the CEO should know, with no current action;
- **持续观察:** an active risk or uncertainty being handled by the team;
- **需要决策:** a concrete choice requires CEO judgment;
- **需要推动:** progress requires CEO coordination, escalation, or sponsorship.

An item enters 需关注 when new evidence establishes a material risk, a threatened key commitment, an important change, a required decision or push, or a meaningful risk escalation.

Reading an item does not resolve it. The item leaves the active list only when evidence shows that the risk disappeared, the decision completed, the requested push completed, or the issue is no longer material. It remains in history with its evidence and lifecycle.

Category changes update the same attention item. For example, a delivery risk may move from 需要决策 to 持续观察 after the CEO chooses a path and the team begins executing it.

## 9. End-to-End Data Flow

```text
Messages / email / meetings / external TODOs / legacy records
                         ↓
                    Source signals
                         ↓
                   Task Candidates
                         ↓
            Formal-task threshold evaluation
                         ↓
                     Formal Tasks
                         ↓
       Identity merge / relationship link / clustering
                         ↓
 Official Project match / Matter / standalone / Project Candidate
                         ↓
              Business relevance resolution
                         ↓
               需关注事项 projection
                         ↓
          Tasks console and attention-item history
```

The projection is recomputable from persisted semantic objects and evidence. A projection error must not mutate or erase the underlying Task, relationship, project resolution, or evidence.

## 10. Console Information Architecture

The Tasks section has three views:

1. **需关注** — default view; a small, ranked set of current CEO attention items.
2. **全部任务** — searchable Task ledger, including business and retained non-business Tasks.
3. **正式项目** — canonical official Projects and their linked work; provisional Project Candidates remain visibly distinct.

The default view may be filtered by the four attention categories. It is not a generic workflow board and does not mix routine in-progress Tasks into the attention feed.

### Attention card

The approved card uses balanced information density. Its default visible fields are:

- attention category and business area;
- concise title;
- 为什么关注;
- 当前状态;
- 你的动作, including “当前无需处理” when applicable;
- official Project or business anchor when present;
- linked Task count and last meaningful update time.

The detail view contains the complete evidence, source history, linked Tasks, ownership, deadlines, commitment states, and relationship graph. Evidence detail stays out of the default card so the homepage remains scannable.

## 11. Uncertainty and Failure Handling

- **Uncertain task status:** retain a Task Candidate and record what evidence is missing.
- **Uncertain identity:** do not merge; create a suggested relationship or cluster.
- **Uncertain Project match:** retain the Task as standalone, Matter-linked, or attached to a Project Candidate.
- **Uncertain business relevance:** retain the Task outside the default attention view and allow later evidence to reclassify it.
- **Conflicting owner, deadline, or commitment evidence:** preserve both sources, mark the field disputed, and avoid silently selecting a value.
- **Attention projection failure:** preserve semantic truth and evidence; expose the projection failure operationally without fabricating an empty or successful attention state.
- **Legacy ambiguity:** do not bulk-promote ambiguous `work_projects` or TODOs. A legacy row is imported only through the same evidence-backed classification rules as new signals.

No failure mode should create an official Project merely to satisfy a foreign key or UI grouping requirement.

## 12. Migration and Cutover

Migration proceeds in four bounded stages:

1. **Introduce semantic objects and read projections.** New Tasks views can be evaluated without changing historical records.
2. **Classify new inputs Task-first.** Remove the requirement that every new task decision name or create a Project.
3. **Evidence-backed legacy import.** Process recent and high-value legacy records first; preserve unresolved rows as legacy evidence instead of guessing.
4. **Cut over the Tasks console and retire the old Project-first decision path.** Avoid a permanent dual-write compatibility layer.

Historical records remain reachable throughout cutover. No destructive bulk rewrite is part of this design.

## 13. Verification Strategy

### Domain tests

- explicit commitment, assignment, formal TODO, and meeting action item promote a candidate;
- discussion and suggestions remain candidates;
- assignment and acceptance produce one Task with different commitment states;
- a standalone Task is valid without a Project;
- only same-deliverable evidence merges;
- related deliverables cluster without sharing completion state;
- clustering cannot create an official Project;
- business-irrelevant small Tasks do not enter the default attention projection.

### Projection tests

- one attention item may aggregate several Tasks while preserving links to each;
- category transitions update the same item;
- read or acknowledgement does not resolve an active risk;
- resolution requires supporting evidence and preserves history;
- a projection failure leaves source Tasks and evidence intact.

### Migration tests

- every imported semantic object links back to its legacy source row;
- ambiguous legacy rows remain unpromoted rather than becoming fabricated Tasks or Projects;
- rerunning import is idempotent;
- no existing provider effect, follow-up, or completion evidence is lost.

### API and UI tests

- 需关注 is the default Tasks view;
- 全部任务 and 正式项目 remain independently reachable;
- attention cards expose category, reason, current state, CEO action, anchor, Task count, and update time;
- dark and light themes preserve readable contrast;
- desktop and narrow layouts preserve information order and do not hide CEO action text.

### Product acceptance sample

Review the latest 100 eligible source inputs, or all inputs when fewer than 100 exist. Every surfaced attention item must have a hard business anchor, a human-readable reason, source evidence, and a correct action category. Confirmed duplicates must produce one Task with multiple evidence sources, and no reviewed cluster may become an official Project without canonical match or explicit confirmation.

## 14. Success Criteria

The design is successful when:

- new Tasks can exist without a Project;
- the service no longer uses Project creation as the default response to a detected action;
- official Projects align with the canonical company definition;
- routine small work does not crowd the CEO view;
- attention items explain why they matter and whether the CEO must act;
- the homepage remains a short attention surface rather than a complete task database;
- every merge, Project match, business-relevance decision, and attention item remains traceable to evidence;
- legacy data is preserved without being treated as confirmed new semantic truth.

## 15. Explicit Non-goals

- A full event-sourced or general-purpose work graph rewrite.
- Automatic creation of official Projects from semantic clusters.
- Turning the Tasks homepage into a team-wide workflow board.
- Surfacing every personal or administrative task to the CEO.
- Replacing the existing execution-Agent and audit-Agent lifecycle.
- Adding incidental authorization, confirmation, or external-action safety gates.
