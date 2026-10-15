---
name: ceo-work-tracking
description: Use for the Task Agent's structured Project/Task extraction and assessment. This reader returns local decisions without replying to the source. Consumer source replies use ceo-document-review or ceo-message-triage; external operations use their service workflows.
metadata:
  managed_by: ceo-agent-service
  version: 7
---

# CEO Work Tracking

One Task Agent reads new evidence, current Project context and existing Tasks.
Return one envelope with all three required lists: `project_decisions`,
`task_decisions`, `project_assessments` (each 0..N). Do not reply to the source.
Write durable, material Project-level risks and progress/status updates supported
by current evidence using `memory_connector.memory_write`. Keep each update
concise, identify the Project, cite the original source and source time (created_at, type="text"), include
Project entity and source provenance metadata when available, and
distinguish reported facts from evidence-based risk inference. Do not write
individual Task/TODO records, routine activity, temporary discussion, unsupported
guesses, secrets, raw transcripts or duplicate updates. Without a durable Project
change, do not write Memory. This is prompt guidance, not an enforced
permission boundary; no general read-only mode is imposed here.
Do not use keyword routers, hardcoded business terms or people, or static branches
to make business judgments.

## Read details when relevant

References below are relative to this Skill directory. Read the relevant complete
reference before using its detailed business contract; unrelated references need
not enter the current context.

- [Project registration](references/project-registration.md): source authority,
  Project identity, registration and independent Project decisions.
- [Project context](references/project-context.md): complete snapshots, goals,
  overall owner, distinct responsibilities and conflicting facts.
- [Project links](references/project-links.md): standalone Tasks and sourced
  Task-to-Project associations.
- [Suggestions](references/suggestions.md): human actions versus display-only
  suggestions, proposed people and material-risk next steps.
- [Task updates](references/task-updates.md): later assignment, identity proof,
  merging, titles and Task relations.
- [Assignment](references/assignment.md): human assignment, acceptance, owners,
  dates, Task transitions and promotion.
- [Assessment](references/assessment.md): Project judgment, material risks,
  existing Attention and candidate Task proposals.
- [Result fields](references/result.md): exact result-envelope field semantics.
- [Evidence](references/evidence.md): original citations, historical evidence,
  shared sessions and Memory source provenance.
- [Service boundary](references/service.md): local decision application,
  authorized business scope and downstream service behavior.
