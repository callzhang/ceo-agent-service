# Task Agent Source Provenance

For a decision marked `evidence_origin=current`, the service owns the source
identity. Before business validation, it binds `source_ref`, owner evidence,
and date evidence to the immutable `WorkItem.source.ref`; the model may supply
the excerpt and locator, but cannot replace the current source identity.

AI Minutes action items use `meeting_action_item` only when the source carries
the trusted `#todos-sha256=` marker. An AI Minutes payload must not be treated
as an external TODO merely because DingTalk also exposes a TODO-shaped field.
Session and memory evidence retain their original source reference and still
cannot authorize formal creation or acceptance.
