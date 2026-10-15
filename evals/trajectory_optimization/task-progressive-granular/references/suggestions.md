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
