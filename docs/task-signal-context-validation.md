# Task source metadata projection

Only database dedupe keys are omitted from the Agent source reference projection.
Task-item keys use source kind/reference and semantic identity SHA256;
project-source and imported keys also remain internal persistence identifiers.
Signal IDs, document IDs, source reference/time/authors, context and evidence
remain separately available. Storage, dedupe lookups, retrieval selection, source
ranges, citations, order, routes, sessions and business validation are unchanged.
PR57 Skill delivery and PR31/78 native output changes are not included.

The new regression first fails because the internal key is in the Agent input.
After the one-line exclusion it compares every remaining source field and body
and reads the original persistent key back through the existing dedupe lookup.
The original test's lookup setup omitted the required transaction argument; that
setup was corrected after the projection assertion passed.

The exact production native compact JSON at session
01a135ff-083f-7d23-b64d-5abd845f7cc8, line158, is reproduced byte-for-byte by the
existing serializer. Omitting98 storage keys reduces314573 to293985 characters
(20588 fewer). All50 source-document projections and every other field are
identical. Independent read-only review reproduces both hashes and comparisons.
Historical sources already have visible ranges; no additional truncation occurs.
This is a lossless metadata projection check, not new model/business inference.

Numeric/hash evidence is retained in
`evals/trajectory_optimization/task_current_input_inventory.v1.json` and
`task_signal_metadata_projection.results.v1.json`. Private original facts remain
in the native session and are not copied. Independent publication/readback
evidence follows after the formal deployment.

Standalone main coverage:30 focused checks, Ruff and diff check pass. Read-only
review confirms this branch contains no Skill/native-schema/provider changes.

## Independent production publication

Commit ba8b5931 contains only this projection, regression and corresponding
doc/evidence changes. Formal deployment deploy-bd994189-6fe3-40b2-b6d1-17252dd9d508
reports succeeded, dc6abe2d -> ba8b5931. The saved backup passes the updater's
complete-backup validation. Production checkout is clean. New supervisor PID62926
and web PID62930 are confirmed; health, workers, Attention, History and Consumer
Settings rendered preview read200. Reply/Work/Classification/Meeting queues have
0 pending/processing; two existing Reply failures remain and three existing mail
actions remain pending. These unrelated items are not relabeled as resolved.

Loading the exact deployed source function read-only and projecting one real
Signal confirms the key is excluded, every other source metadata field is equal
and the database key is unchanged. Its original2785-character historical source
keeps the existing truncated ranges. This is production code/source readback,
not a new Agent business turn. Some HTTP observations timed out before and after
deploy, then succeeded without another restart; no cause or regression repair
is claimed. Compact receipt: `task_signal_metadata_release.v1.json`.
