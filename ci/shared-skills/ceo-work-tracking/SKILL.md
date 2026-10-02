---
name: ceo-work-tracking
description: Use when source context contains one or more trackable Tasks needing extraction, evidence, explicit ownership, commitment/date interpretation, Project clustering, follow-up, completion evidence, or closure. Tasks must be derived from source context; never invent tasks or assignees. Use ceo-message-triage when no durable work item is needed and ceo-meeting-work for meeting synthesis before actions are confirmed. Return structured local Task decisions; external operations belong to their service workflows.
metadata:
  managed_by: ceo-agent-service
  version: 3
---

# CEO Work Tracking

Treat extraction, creation, follow-up, replies, completion verification, and
closure as one lifecycle. Preserve identity, intent, evidence, and links across
every state change. Do not use keyword routers, hardcoded business terms,
person names, or static routing branches to make work decisions.

In this Task Agent turn, return structured local Task decisions only. Use connected tools for read-only source discovery; do not write, send, or complete external records through CLI, API, or MCP. This is prompt-only best-effort guidance, not an enforced tool permission boundary.

## Task-First Lifecycle Decision

1. Extract zero or more distinct Tasks from supplied source context. Every Task
   decision must cite its source: the reference, one sentence of the original
   text (an extract is fine, it need not be word for word), and where to find it
   (its link whenever it has one; otherwise a description, e.g. a DingTalk
   message is its group and the person who sent it). Never originate a Task, deliverable,
   owner, assignment, or date from Agent judgment alone.
   Project registration scope, objectives, and categories are not separate Tasks
   when concrete source actions already cover that work. Use registration text as
   Project evidence attached to those real actions, not as an additional umbrella Task.
   Keep genuine explicit actions wherever they occur in the source.
2. If the source contains no plausible action or decision, skip it. Retain
   source-backed low-impact work when its source workflow needs a record, but
   do not promote it to CEO attention merely because it was recorded.
3. Classify each source-backed action as candidate or formal. A formal Task
   needs an evidenced explicit commitment, authorized explicit assignment,
   formal external TODO, or concrete meeting action item, and the responsible
   owner/team must be explicitly identified by the source or authoritative
   metadata. If ownership is missing or ambiguous, retain it as a candidate or
   unmatched evidence; do not create a formal assignment with an invented or
   blank owner. An external TODO proves a formal record exists; it does not
   prove its assignee accepted it.
4. Use an assignee only when source text or authoritative metadata names the
   responsible person/team. Preserve owner identity, assignment evidence,
   assigner/creator, and actor provenance. A participant, speaker, host, sender,
   group member, contact lookup, or name match alone proves neither ownership
   nor authority to assign. If the owner is unclear, retain missing evidence;
   never guess.
5. Derive commitment status from evidence, not an Agent-selected status. An
   explicit assignment is `assigned_unaccepted`. `accepted` requires explicit
   acceptance/commitment evidence from the identified owner, uniquely bound to
   that existing Task. A named owner in a meeting action item or external TODO
   remains `assigned_unaccepted` unless that owner's acceptance is separately
   evidenced; the minutes author/meeting host or TODO creator is not the owner
   accepting it. “收到” alone confirms receipt, not acceptance of the
   deliverable or its date. If a reply could refer to multiple Tasks, do not
   select one by semantic rank. Agent-authored or service-created messages and
   TODOs are not evidence of the owner's personal acceptance.
6. Keep date meanings separate and source-backed:
   - `created_at`: when the system recorded the Task;
   - `assigned_at`: when the source shows the assignment occurred;
   - `requested_deadline_at`: an explicit due date requested by the assigner;
   - `external_deadline_at`: a due date recorded in an external system;
   - `committed_deadline_at`: a concrete date the owner explicitly accepted or
     committed to;
   - `estimated_deadline_at`: an estimate, never evidence of owner default;
   - `next_check_at`: the Agent's operational check time, never a due date.
   Preserve source and actor for every source-derived date. A week-level or
   otherwise non-parseable date phrase stays only in the linked original source
   signal; do not create a typed date fact or manufacture a timestamp from it.
   An estimate is attributed to the identified source speaker who made it,
   never to the extracting Agent. `next_check_at` is Agent-authored only when
   the source explicitly supplies a check date.
   Do not turn “尽快”, “应该这周可以”, a guessed date, or a next-check
   schedule into a committed deadline.
   In the Task 6 Task Agent path, record `next_check_at` only when a check date
   is explicit in the source. Do not invent a check cadence or convert a due
   date into a check date; a future scheduling policy needs separate definition.
   Missing dates do not prevent recording a Business Task. A concrete,
   parseable deadline is still required before mirroring a Task as a DingTalk
   TODO.
7. Keep a Task independent of Projects. Reports, meetings, and chats all supply
   Task and risk evidence; a weekly report is neither the sole risk source nor
   a prerequisite for Attention. Register an official Project only from a
   confirmed report's project registration or an explicit meeting registration
   decision. Prefer confirmed official weekly reports for Project definition and
   registry fields. Quote that basis separately in `ProjectProposal.source_excerpt`;
   the Task action excerpt is not registration evidence. Chat updates Task and
   risk evidence but cannot create a Project or silently overwrite official
   fields. Preserve report references and reporting periods. When newer meeting
   or chat evidence conflicts with official fields, preserve both cited sources
   and their times and mark the conflict pending verification. Similarity,
   labels, clusters, and candidates are not Project authority.
8. Merge only identical deliverables supported by explicit identity evidence.
   Distinct deliverables with a shared goal may be clustered or linked; they
   retain independent owners, dates, and completion. When identity is uncertain,
   link or keep separate rather than merge.
9. Propose CEO attention only for an existing confirmed official Project or a valid
   current-authority `project_proposal` in this same TaskDecision, resolved to its
   registered anchor this turn, with real supporting Tasks and source evidence
   of material business impact. First assessment of a source-observed unresolved
   material business risk may use watch; it does not require a prior card or a
   fresh delta against a nonexistent assessment. Explain the concrete unresolved
   business impact from the observed source, even when the report states the
   risk as a current fact. An existing card already reflecting the same facts
   does not need a new proposal; repeated facts alone are insufficient.
   Candidate Tasks may support Attention without a formal owner or accepted commitment;
   keep their stage and missing ownership evidence truthful. Explain
   the impact in `why_attention` as inference; keep `current_state` factual.
   Relevance, labels, acceptance, routine progress, and date proximity alone do
   not establish material impact. Quote risk evidence from the
   full current source or historical persisted original Signals, separately
   from Task action and Project registration excerpts. Current evidence uses
   null `signal_id` and the current `source_ref`; historical evidence requires
   a real positive persisted Signal ID, matching reference, and exact quote.
   Attention cannot use cited-only session/Memory provenance as observed truth.
   Use a real existing anchor, or null only with this same decision's new
   Project proposal. Return at most one unique assessment/card per Project per round.
   For multiple newly created Tasks supporting the same Project and risk, repeat the identical `attention_proposal`
   on each supporting TaskDecision. The service folds those identical proposals into
   one card and combines their Task membership. Keep the assessment fields and evidence
   identical across those decisions; anchor resolution and existing related IDs may differ.
   Use `related_task_ids` only for real existing Task IDs; never invent IDs for new decisions.
   Keep unrelated Project Tasks outside this assessment; conflicting proposal payloads are rejected.
   For watch, you may say 当前无需你处理; specify the observable outcome to watch. It does
   not imply 需介入. Do not invent work, owners, assignments, commitments, or
   dates to fill a card. Explain unproposed Tasks in `update_summary` when
   impact is insufficient, the Project is unconfirmed, or evidence is unverifiable.
10. Apply replies, corrections, disputes, owner changes, scope changes, and
    date changes to the existing Task when identity is explicit. Preserve new
    evidence and actor; record corrections/supersession instead of erasing
    history. Stop follow-up based on a disputed or superseded owner/date until
    current state is resolved.
11. A DingTalk TODO is an external mirror/operation for a source-backed Task,
    never a new source of owner acceptance. Before following up, read the
    current Task/TODO and external status using the relevant operation Skills.
    Bind reminders to the existing Task and linked TODO; never create a
    duplicate commitment or independent reminder.
12. Select an audience and schedule only from verified target/source evidence.
    Use an appropriate verified group that includes the intended owner, or a
    verified direct identity for sensitive content. If target evidence is
    missing, ask for it; do not let the service guess or reroute.

## Lifecycle Cases

- `source_context_can_produce_multiple_tasks`: preserve every distinct action
  from one meeting/message, each with its own source excerpt, owner, dates, and
  commitment evidence.
- `agent_does_not_invent_task_or_assignee`: no unsourced work or inferred owner
  is created; ambiguous ownership remains unresolved.
- `external_todo_is_not_owner_acceptance`: external existence proves a formal
  record only; the named owner must explicitly accept before commitment becomes
  accepted.
- `receipt_acknowledgement_is_not_acceptance`: “收到” alone does not accept a
  deliverable or due date.
- `date_types_are_never_interchanged`: created, assigned, requested/external
  DDL, owner-committed DDL, estimates, and next-check time remain distinct.
- `routine_work_is_not_attention`: retain sourced low-impact work where needed
  but keep it out of CEO attention absent a material trigger.
- `attention_requires_trigger`: anchor/relevance alone does not create FYI,
  WATCH, DECISION, or PUSH attention.
- `follow_up_cannot_exist_without_task`: bind follow-ups to an existing Task;
  any DingTalk TODO mirror must also be linked to that Task and have a valid due
  date.
- `participant_or_speaker_is_not_owner_evidence`: do not assign or contact a
  person merely because they participated, spoke, sent, reported, or appeared.
- `due_follow_up_refreshes_live_task_before_send`: require current Task, linked TODO, and
  external-status reads before deciding that a due reminder remains useful.
- `completed_task_suppresses_follow_up`: close or suppress all pending reminders
  when supported completion evidence exists.
- `owner_correction_updates_existing_task`: preserve correction evidence and
  stop the old owner/date follow-up before considering a new one.
- `follow_up_reply_updates_existing_task`: match a reply by explicit reference
  or one unique evidenced Task; do not create a second Task for the same work.
- `sensitive_follow_up_uses_verified_direct_target`: use only the verified
  direct identity selected in the decision; never convert a group target to a
  direct target in service code.

## Shared Source-Driven Task Session

One Task Agent extracts, updates, and completes Tasks from new source evidence
through one `TaskAgentDecision`. Every Work Item has its own run and workload
key, while the shared logical `task-agent:work-tracking:v1` session preserves
context; runtime routes keep separate native sessions and native CLI compaction.
Prioritize the current Work Item's authority and identity metadata.

Earlier session evidence and Memory provenance may refine an existing Task or
record a candidate when they cite the original source reference, excerpt, and
link or location description. Creating a formal Task, promotion, acceptance,
identity merges, and typed dates still require the current source authority and
identity evidence. Attention has a stricter source contract: historical quotes
must resolve to stored observed original Signals, not cited-only provenance.

`todo_completion_evidence_candidate`, `todo_completion_check`, and
`follow_up_completion_check` are retired historical enum values, not current
Task Agent inputs. The schema still contains `todo_changes`,
`follow_up_changes`, and `search_trace`, but the current service does not apply
those lifecycle fields. Do not orchestrate discovery turns or recommend their
use. Newly observed DingTalk human completion deterministically updates only
its explicitly linked Task through the existing service path.

## Memory And Evidence

Memory is how to find related information: recall it, follow its provenance to
the original source, and cite that source (with `evidence_origin: memory`) to
refine a Task. A memory summary without its original source is not evidence. It
cannot originate Tasks, establish acceptance, or authorize a Project; those need
the current Work Item's source and authority. Record a recall query/result only
in the designated context field; do not write it into a Project patch.

Use current source material and live systems as authority for owner, target, and
completion state. Load a specialist Skill when a tracked item belongs to a
specialized workflow instead of copying that workflow here.

## Service Boundary

The service owns source/evidence persistence, Task transition validation,
evidence-derived commitment state, typed date storage, explicit matching to
existing Tasks, scheduled wake-up, due-time and local-work-hours guards, the
parseable due-date gate for a DingTalk TODO mirror, live external-status refresh,
exact-message idempotency, and sent-result or
retry state. The Agent may extract and propose interpretations only from
supplied source context; the service rejects unsupported owners, acceptance,
dates, transitions, identity merges, or attention triggers. The Agent/service
must not create an unsourced Task or treat their own output as a human
commitment. Preserve exact-message idempotency. A corrected or materially
changed message is a new revision and is not blocked merely because an older
message was stored or sent.
