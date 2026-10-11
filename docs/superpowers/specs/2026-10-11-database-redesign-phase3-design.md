# Phase3 canonical reply inputs and bounded work-summary slimming

User approved Phase3 implementation, consistent-copy validation, matched schema inverse and then normal deployment. This instantiates the already-approved input/provenance design; no new history-retirement deadline, audit, authorization or effect-recovery policy.

## Actual baseline and scope

Consistent production copy captured at b998f225, main store schema2026-10-10.2, Email49. Production read-only sample:7261 reply tasks,7366 input rows; task body+compact source JSON about3.00MB and provenance3.92MB duplicated with inputs. 418 tasks have no exact original-body digest match in their input rows;422 lack a complete matching header/current snapshot. Three tasks have no input row; six have version watermarks differing from input-row counts. These are preservation cases, not permission to infer old values or repair business history.

9080 work-summary inputs are all done/skipped and their existing compact payload totals18160bytes. Preserve that completed retirement and all source/time/hash/bytes/state/run/evidence/receipt dependencies. A nullable-body/removed-marker redesign would create a wide rewrite for negligible savings and is rejected.

## Chosen structure

Keep the two reply tables. Add current_input_id and nullable claimed_input_id referencing existing reply_task_inputs.id; validate that each referenced row belongs to its task. Preserve input_version/claimed_input_version as existing CAS watermarks, never treat them as input IDs or derive old claims by ordinal. Remove reply_tasks.trigger_text,trigger_message_json,input_provenance_json,input_compacted. Necessary queue/identity/header projections remain.

During forward migration, use an existing row only when its complete actual current snapshot matches. Otherwise append an exact canonical snapshot from the existing task projection under a deterministic migration revision identity; preserve all original input IDs/revisions and task version watermarks. No source fetch, reconstructed original or old-row overwrite. Unprovable live old claimed input must block migration; formal rollout uses normal quiet window. Terminal differing watermarks retain their history and may have no claimed_input_id rather than invent one.

Future writes, including manual reruns/context refresh/card revision/single-chat merges, append or select exact canonical input and update its pointer atomically. Claim captures current_input_id. Execution-bound getters and frozen Skill readers use the snapshot of that queue claim with generation validation; a later reclaim captures the then-current input, preserving existing recovery and watermark behavior; public current projection uses current row. Existing deferral, completion and new-input watermarks remain meaningful.

Retired canonical input keeps provenance; persist no second compact-source JSON. Store trigger_text/trigger_message_json as empty strings when input_compacted=1 and derive the former compact API representation from provenance at read time. JSON source/subtype/history queries read real provenance source fields for retired input, original JSON for active input. Validate old compact projections before removing the redundant representation.

For work-summary creation only, omit scheduled_consumer.skill_materials, which Task Agent parses but never consumes or injects. Retain prompt, selected Skill names, IDs, protocol and protocol source needed by the existing context validator. Do not change shared Scheduled Consumer behavior or existing active/terminal envelopes. Preserve the b998f225 baseline’s lossless WSI codec and all plain-JSON application boundaries. New envelope digest is computed by the existing enqueue provenance writer; old digest is not re-labelled.

## Matched reverse release

Store schema2026-10-10.2→2026-10-11.1 receives its own tested inverse integrated with existing database publication. Email schema remains49. Before migration record old version marker and actual old table/index declarations; inverse uses the replacement's latest canonical rows to reconstruct the four old task columns and retired input compact representation. Restoring the old format can recreate its redundant cache as required for old-reader compatibility, just like Phase2; it must not move or retire active canonical inputs. Retain post-migration input rows/revisions, status, business records and receipts. Restore original store-version marker only after transactional inverse validation. No old backup overwrite.

Actual copied old b998f225 AutoReplyStore/EmailStore startup, current/claimed distinction, restart/requeue, new input/revision and execution receipt preservation must be demonstrated on copies. Old code's pre-existing claim-read behavior is not enhanced by downgrade; stop replacement first using the established deploy rollback, preserve exact inputs and generation/native facts, and verify old recovery rather than assuming arbitrary in-flight continuation.

## Acceptance

Focused tests cover enqueue/immutable replay/manual revisions, claim+new current, single-chat merges, exact frozen Skills, scheduled history, unsubscribe/receipts, terminal retirement, WSI active recovery and prompt equivalence, schema failure/rollback and old runtime. Production copy verifies original task/API snapshots, old input identities/meaningful fields, new canonical snapshots, all other tables/receipts and existing FK violation fingerprint. No provider replay and no live VACUUM. After independent review deploy normally and read PID/code/schema, health, queues, Attention, History and latest single verified backup.
