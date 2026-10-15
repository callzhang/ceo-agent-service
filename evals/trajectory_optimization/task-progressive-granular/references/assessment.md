## Project judgment and 需关注

Every relevant Project/clue in the current source and current Tasks' confirmed
Project links gets one assessment, whether or not this result emits a Project
update. Exact duplicate titles and repeated anchors share one judgment.
Include one matching assessment for every project_decisions index:
every `project_decisions[i]` must have exactly one matching assessment with
`project_decision_index: i`. Do not use `anchor_id` when a matching
`project_decisions` entry exists. Do not omit one report row because another
Project is also assessed.
Selector shapes are exclusive: a new or re-registered Project uses `registration`
only: omit `anchor_id`; a known existing Project uses `anchor_id` only: omit
`registration`. If a matching Project decision is in this output, its assessment
cites the decision index only: omit `anchor_id`; use an existing `anchor_id` only
when there is no matching decision in this output.
Every assessment has current null-ID evidence: at least one exact current-source
quote with `signal_id: null` and the current `source_ref`, even for
`historical_comparison`; that basis additionally requires verified positive-ID
original evidence.
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
Project responsibility supports the suggested person and duty, or the single
overall_owner is suitable for a project-wide coordination action. If no Project
role supports an actionable owner, keep the risk in Attention without inventing a task,
owner, monitoring item or deadline. Optional decision_indexes
index Task decisions; task_ids name real existing confirmed members, not guessed
IDs or all Project peers. A Task being linked to this Project is not enough:
include it only when it directly supports this specific Project assessment.
When a Task decision updates the same concrete Project work described by the
assessment, include that decision index as support, including for a not_needed
progress assessment. An existing Task belongs in task_ids only when it directly
supports the assessment;
completed or unrelated Project Tasks are not members. Keep current_state as Project-level risk facts, not per-Task action summaries, why_attention as
inference and ceo_action as the relevant action or observation. For watch it may
say 当前无需你处理 and name what outcome to watch. 需关注 does not imply 需介入.
When a Project needs Attention because of a material unresolved impact and a
directly relevant saved Project responsibility exists, create one display-only
candidate next-step Task for that role even if the current message does not spell
out an assignment or action. Infer the concrete action from the risk. An existing
Task suppresses this suggestion only if it addresses the same unresolved risk; a
completed or unrelated Project deliverable does not. Prefer one suggestion for one
material Project risk: do not create separate candidates for substeps or for each
role involved. When one next step is a stated prerequisite or upstream gate for
another, treat them as sequentially dependent next steps and create one candidate
for the nearest blocking outcome; include the downstream impact in its description
instead of creating a second candidate for the dependent confirmation. Create
multiple suggestions only when current evidence identifies separately completable,
non-dependent actions with distinct outcomes. For example, acceptance-material
confirmation and payment-date confirmation in one delivery/collection chain are
not two candidates just because different Project responsibilities match them;
choose the nearest blocking outcome and mention the payment impact in its
description. A Task title must include the decisive unresolved Project outcome
as well as any upstream gate needed to reach it. For a payment-risk suggestion,
name the payment date/schedule in the title even when confirming an upstream
acceptance condition is the first step; do not hide the key outcome behind a
broad title such as “update the plan”.

A retained existing_attention_id claims that card's original proof, not an update
target. Cite at least one stored assessment_json.evidence triple unchanged and
use actual current_project_attention membership, not other Project Tasks. For new
risk/membership use this assessment's proposal; its Project key reuses the card.
For an existing card, copy `task_ids` only from its actual stored member IDs in
`current_project_attention`; never add a same-Project peer Task. Unrelated
current-turn Task updates do not add membership to that card. If the
delivered card has no member IDs, keep both `task_ids` and `decision_indexes`
empty, including when another linked Task is completed or updated this turn. Do
not add to the card even a same-risk candidate suggested on an earlier turn; that
candidate remains a separate Project-linked Task. An
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

