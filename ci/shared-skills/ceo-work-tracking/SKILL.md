---
name: ceo-work-tracking
description: Use when source context concerns business Projects, their facts, responsibilities or material risks, actual Tasks, display-only next-action suggestions, ownership, commitment, typed dates or completion. Aggregate original sources into real Project context, preserve standalone Tasks and return structured local decisions; external operations belong to their service workflows.
metadata:
  managed_by: ceo-agent-service
  version: 5
---

# CEO Work Tracking

One Task Agent reads new evidence, current Project context and existing Tasks.
Return one envelope with all three required lists: `project_decisions`,
`task_decisions`, `project_assessments` (each 0..N). Do not reply to the source.
Use tools only for read-only source/context discovery; do not write, send, delete
or complete external records through CLI, API or MCP. This is prompt-only
best-effort guidance, not an enforced permission boundary.
Do not use keyword routers, hardcoded business terms or people, or static branches
to make business judgments.

## Project context first, actual Tasks and suggestions distinguished

1. Read related original meetings, management/department/project weekly reports,
   messages and emails together with existing Project context and Tasks.
   Weekly reports are neither the sole information source nor a prerequisite.
   Source authority, authorship, exact version and time matter; repetition is not
   independent confirmation. Preserve conflicting facts and their evidence/times
   instead of silently choosing a convenient source.
2. A Project has a real goal and scope, not just a department, customer/topic,
   small Task, todo bundle or similarity cluster. Register only a confirmed
   report Project registry entry or explicit meeting Project decision; quote the
   defining passage in `registration.source_excerpt` and set its real authority.
   Preserve the exact source-defined title, reference and report period.
   Adopt the exact current authoritative Project definition with registration to register or reuse.
   A different stored name cannot replace that definition merely because the action uses its shorter name.
   Task/action sections are not Project registries. Chats/emails may supplement
   known Project facts but do not by themselves create official Project identity.
   Do not infer aliases or identity from title prefixes, rank or topical similarity.
3. `project_decisions` independently registers a Project or updates its existing
   active `anchor_id`. Its evidence is nonempty original proof. `context=null`
   only adds proof; otherwise return the complete current snapshot, not a partial
   patch. Project can have zero Tasks. Never create or update a Task just to carry
   Project facts, risk evidence, owner information or Attention.
   For a report containing multiple Projects, create a separate decision and
   assessment for each Project row; one Project's assessment does not cover
   another. A registration quote must be an exact contiguous excerpt from the
   immutable current source that defines that Project.
4. `ProjectContext` has goal, scope, one `overall_owner` with the responsible
   result, other `responsibilities`, and source-backed facts. Unknown overall
   owner is null (待明确), not a concatenation of people or several overall owners.
   A bare responsibility clause (a person being responsible for a business area)
   is ProjectContext only, not a source Task or candidate. Create a Task only
   for an explicitly stated, independently completable deliverable/action, or
   a separate actionable suggestion required by a sourced material Project risk.
   The clause `X负责Y` by itself remains a Project responsibility, even when Y is
   a distinct business deliverable (for example, 王五负责商务对账). Create a
   source Task only when the source also states a concrete action/expected result
   or provides a real action-item record; a duty-area description is not enough.
   If overall-owner evidence conflicts, keep `overall_owner` null and record the
   competing claims and challenge as sourced facts. Do not reclassify competing
   overall-owner candidates as responsibilities; that list contains only
   independently evidenced, distinct work duties. State plainly in a Project fact
   that the owner remains in conflict. Preserve unchanged separate deliverable
   owners and their citations.
   A person explicitly identified as the Project's overall accountable owner
   belongs in `overall_owner`, not `responsibilities`. For example, “张三总负责
   交付验收” identifies the overall role; keep “总” out of the person's name.
   Each person has a distinct responsibility and original evidence. Keep unchanged
   roles' and facts' historical references when updating a snapshot. Whenever new
   facts or roles are learned, read the saved context and return the complete current
   snapshot, retaining prior valid items with their citations; do not return only
   this source's delta. Each dated fact names
   both its date type and date value; ordinary facts leave both unset.
   Keep personal name separate from a trailing Chinese rank/honorific such as
   “总”; store the name only and express the rank/responsibility in the role.
5. Preserve genuine standalone Tasks without forcing a Project. Unknown Project
   clues retain their source names and evidence in assessments without registering
   fake Projects. Uncertain Task association remains proposed, not confirmed.
   `Task.project` selects an actual `anchor_id` or `project_decision_index`
   into the top-level Project list, not the Task list. `project_link_evidence`
   proves a new association. Reuse unchanged actual confirmed links.
6. Original human work and Agent suggestions are different:
   - Actual Task: a source-backed independently completable deliverable/action.
     Its assignee, authority, acceptance and dates are factual source claims.
     An ownerless or ambiguous human action stays a source-origin candidate.
     When the source itself states the concrete action and expected result, keep
     it source-origin even if metadata is insufficient for formal assignment;
     preserve a clearly named responsible person as source-reported owner evidence
     without inferring acceptance. Do not relabel that human-stated action as an
     Agent suggestion.
   - Display-only suggestion: infer a useful next action from Project facts plus
     sourced Project roles or organizational responsibilities. Use `suggestion`
     with `reason`, `suggested_owner_name/user_id`, `responsibility_evidence`
     and `basis_evidence`. The proposed person need not occur in the current
     message if original role evidence establishes the duty.
     Suggestions do not assert human assignment/acceptance or author an actual
     deadline. Keep actual owner fields, owner assignment metadata, formal basis,
     typed dates, status and relevance changes unset; also keep `owner_kind` and
     `owner_relation` unset, with empty actual-owner evidence. Put a proposed person
     only in `suggested_owner_name/user_id` and cite the Project responsibility.
     Record a candidate or update its existing ID; do not send TODOs, notifications
     or follow-ups.
   An unresolved material Project risk should have an actionable next step: create
   one Project-linked display-only suggestion and derive its suggested person from
   the saved responsibility that best matches the work. Do not create one merely
   for routine progress, a settled/resolved fact, or an ambiguous clue.
   Project roles are not Tasks; routine milestones and next steps are Project facts,
   not Task candidates. Never create a Task merely to fill or resolve a Project
   owner/responsibility field. Record that fact and assess its impact at Project
   level. Missing ownership alone is not a material risk: require sourced delivery/
   business impact or a required Gate for 需关注. Any suggestion must be an
   independently actionable step beyond editing the Project record.
   Before suggesting another next-step Task for a Project risk, check current
   linked Tasks. If an existing actionable Task already addresses that risk,
   use its existing ID as the supporting next step and do not add a duplicate
   monitoring/evaluation suggestion.
   Never quote your inferred action or earlier suggestion as a human instruction.
7. Later real human assignment promotes the same suggestion Task ID through the
   actual lifecycle; discovery origin and suggested rationale remain historical.
   Before later-source updates match and carry the real Task ID. Repeated proof,
   title similarity or changed reason wording alone does not establish identity.
   Merge only identical deliverables supported by explicit identity proof.
   Scope/content additions update the existing Task, not a duplicate deliverable.
   New Tasks need a nonblank title; only update_fields changes a provided title.
   Promotion, acceptance and merge preserve the stored title.
   Relations name the existing related_task_id and direction relative to this
   Task; never guess a newly created Task's ID or use unrelated endpoints.

## Human assignment, acceptance and dates

A formal Task requires explicit commitment, authorized assignment, an existing
formal external TODO or a concrete meeting action with an evidenced individual
owner. A team, department, participant, speaker, meeting host, sender, directory
match or contact lookup alone does not establish individual ownership or authority.
Owner evidence must cite the source reference and a literal sentence naming the
person and their action, not a paraphrase or a Task title.

For AI Minutes read the full meeting_summary, action items and transcript_excerpts.
The assigned person or person taking on the work is not automatically the speaker.
Quote the sentence (speaker label included when present) that establishes the
named owner, even if outside the narrow action excerpt. Generic speaker placeholders
are not people. Classify owner_kind and owner_relation: explicit_assignment,
self_commitment or meeting_summary_action_item may establish an individual owner;
speaker_only/unknown or a team remains candidate. Authoritative source identity
metadata, not an Agent-generated identity, establishes a stable owner_user_id.
When a live directory lookup is available, use it to verify the exact named person;
keep the name and leave the ID empty when identity is not established.
Treat a meeting action item as formal only when the source context identifies an
AI Minutes conversation and the current source reference carries its
`#todos-sha256=` action-item record marker. A non-meeting explicit assignment is
formal only when current source metadata explicitly says
`assignment_authorized=true`; otherwise preserve it as a display-only candidate.

An assignment or meeting action creates assigned_unaccepted, not accepted.
Only explicit identified-owner acceptance bound to exactly that existing Task
can apply_acceptance; cite the assignment Signal and verified reply_to_source_ref.
“收到” alone is receipt, not acceptance. External TODO existence or Agent/service
messages do not prove a human commitment. Generic updates cannot select commitment
status. Ambiguous replies do not choose a Task by rank.

Keep date meanings explicit and source-backed:
- created_at: when the system recorded the Task;
- assigned_at: trusted source timestamp of assignment;
- requested_deadline_at: requested by the assigner;
- external_deadline_at: recorded in an external system;
- committed_deadline_at: explicitly accepted or committed by the identified owner;
- estimated_deadline_at: sourced estimate, never the extracting Agent's estimate;
- next_check_at: explicit operational check time, not a due date or invented cadence.

Quote only the complete parseable current-source date phrase, with punctuation,
spaces and wording unchanged, and normalize without adding absent precision.
Use trusted sender identity for source-derived date actors; next_check_at uses
task-agent/CEO Agent. Reports/document names are not actors. AI Minutes has no
trusted speaker-to-identity date mapping: do not emit source-derived typed deadlines
there. Missing dates do not prevent a Task. A Project date is not a Task deadline;
a parseable accepted due date is still required for a DingTalk TODO mirror.
“尽快”, an estimate or a check schedule is not an owner-committed date.

## Project judgment and 需关注

Every relevant Project/clue in the current source and current Tasks' confirmed
Project links gets one assessment, whether or not this result emits a Project
update. Exact duplicate titles and repeated anchors share one judgment.
Use [] only when no relevant Project/clue exists and explain it in update_summary.
Assess bounded retrieved context, not the whole company or all history.

- needs_attention: original facts show material unresolved business impact,
  threatened accepted commitment, meaningful escalation/dispute, CEO decision/push
  or a required Gate. A first current risk may be watch without a previous card.
- not_needed: supported normal progress or a supported negative judgment.
  No reported risk or zero Tasks alone is not missing evidence.
- insufficient_evidence: genuinely unresolved identity or facts needed to judge;
  name the missing facts, do not invent a risk or an official Project.

A real Project can need Attention with zero Tasks. Put `attention_proposal` once
in its assessment, never copy it onto Task decisions. Create a display-only Task
suggestion only when a concrete next action is warranted and a saved, sourced
Project responsibility supports the suggested person and duty. If no responsibility
supports an actionable owner, keep the risk in Attention without inventing a task,
owner, monitoring item or deadline. Optional decision_indexes
index Task decisions; task_ids name real existing confirmed members, not guessed
IDs or all Project peers. A Task being linked to this Project is not enough:
include it only when it directly supports this specific Project assessment;
completed or unrelated Project Tasks are not members. Keep current_state as Project-level risk facts, not per-Task action summaries, why_attention as
inference and ceo_action as the relevant action or observation. For watch it may
say 当前无需你处理 and name what outcome to watch. 需关注 does not imply 需介入.

A retained existing_attention_id claims that card's original proof, not an update
target. Cite at least one stored assessment_json.evidence triple unchanged and
use actual current_project_attention membership, not other Project Tasks. For new
risk/membership use this assessment's proposal; its Project key reuses the card.
For an existing card, copy `task_ids` only from its actual stored member IDs in
`current_project_attention`; never add a same-Project peer Task. Unrelated
current-turn Task updates do not add membership to that card. If the
delivered card has no member IDs, keep both `task_ids` and `decision_indexes`
empty, including when another linked Task is completed or updated this turn. An
unresolved dispute over who holds the overall Project accountability (for example,
a claimed transfer that the prior owner says was not confirmed) is 需关注 even
before a separate operational impact is quantified. Record the conflict in
ProjectContext, without creating a Task just to resolve that field. A missing
overall owner without a stated impact or dispute is not a material risk; when the
other Project evidence is normal and complete, classify it as not_needed.
An assigned person not yet accepting is not a material risk by itself when
current evidence shows the work is progressing and no meaningful impact or
accountability dispute; preserve assigned_unaccepted without Attention solely
for that status.
A not_needed judgment, zero Tasks or one Task's completion does not resolve a
Project risk. Do not invent a Task update, next_check, owner or deadline to make
Attention possible. Routine progress, relevance, acceptance, labels or date
proximity alone are not a material trigger.

Return every list-valued field as a JSON array; use `[]` when empty and never
`null` (including `decision_indexes`, `task_ids`, `todo_changes`,
`follow_up_changes` and `search_trace`).

## Original evidence and shared session

All source_excerpt values are exact contiguous quotes, preserving punctuation,
spaces and line breaks. Do not join separate spans or paraphrase. Quote a raw
visible range or one decoded JSON string leaf; shared source_documents contain
the body once and source_signals retain real identity/version/actor metadata.

Current Project proof uses null signal_id and the current source_ref.
Historical Project/context/suggestion/Attention proof requires a real positive
observed Signal ID, matching reference and exact quote, not memory_provenance or
session_provenance. Never prove original facts with your own summary or suggestion.
Do not reconstruct an earlier quotation from a later summary. If the exact
original excerpt is unavailable, omit that historical claim, rely only on
verified current evidence, and state what cannot be confirmed.
current_observation asserts current facts; historical_comparison requires current
null-ID and positive original historical proof. When current text compares earlier
facts and originals are delivered, verify against them. A current retelling of
history is not original historical proof. When originals are unavailable, state
the comparison's uncertainty and assert only current facts.

The shared logical task-agent:work-tracking:v1 session retains context while each
Work Item has its own run/workload key. Runtime routes retain separate native
sessions; native CLI compaction manages history. Earlier session or Memory
provenance may refine a Task or record a candidate with the original reference,
exact quote and source link/location, but cannot authorize formal creation,
promotion, acceptance, identity merge, typed dates or official Project registration.
Memory is discovery/background, not observed source proof.

## Service boundary

The service persists originals, Project context revisions, actual Task lifecycle,
typed dates, explicit identity/association and application receipts. It does not
invent business conclusions. Only structured local results are applied.
todo_changes, follow_up_changes, search_trace and old completion-check source
enums are historical, not current operations. Newly observed DingTalk human
completion deterministically updates only its explicitly linked Task.

Actual corrections preserve history and suppress obsolete Task follow-up.
Mirrors/reminders belong to an existing actual Task and verified source/target;
refresh live status before a due reminder. Preserve due-time/local-work-hours and
idempotency rules. Never guess an audience, reroute a group to a direct message
or invent a commitment. Source-corrected or materially changed messages are new
versions, not blocked by old message history. Use specialist Skills for those
external workflows. Keep runtime paths, credentials and diagnostics out of
business fields.
