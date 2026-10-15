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
For existing Task updates, leave commitment_status unchanged unless the named
owner explicitly accepts, disputes, completes or cancels it. Work progress,
continued handling or receiving materials does not prove acceptance. Do not set
acceptance polarity except through apply_acceptance with verified owner/reply proof.

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

