## Result field contract

For project_assessments, use exactly one selector: anchor_id for a known registered
Project when this output has no matching Project decision, project_decision_index
for the matching Project decision in this output, or neither only for an unresolved
Project clue with insufficient_evidence. Never provide both.

Similar deliverables are linked/clustered, not identity-merged.
status and business_relevance may only change through update_fields: set
transition=update_fields when either field changes. They remain top-level fields,
not a nested update_fields object. New, record_candidate and skip decisions must
leave status and business_relevance unset. When promoting a suggestion, omit the
suggestion field; saved suggestion history remains. A suggestion field is valid
only when recording or updating a candidate.
Actual owner_evidence uses {"source_ref": "the source reference", "excerpt": "the literal owner-action sentence"}.
Never include a skip decision in decision_indexes; supporting indexes may identify
only a real candidate, create or update Task decision belonging to the Project
and directly supporting that assessment. An existing card already reflecting the
same facts does not need a new proposal.

