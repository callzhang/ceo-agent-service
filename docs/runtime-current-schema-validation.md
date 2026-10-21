# Current runtime schema cleanup validation

Candidate: Store `2026-10-19.12` / Email53. Production is unchanged until the standard deploy and live readback recorded below.

## Change

Runtime constructors accept a genuinely blank database or the exact current declaration only. They do not recognize historical declaration variants, add missing objects, or automatically backfill OA audit metadata. Missing/malformed current objects reject without mutation before journal changes. Email historical DDL, maps, old migrations and source validators are removed from EmailStore; necessary explicit 48→49 / 49→50 tools and inverse validation live in `email_schema_history.py`. Store dead source DDL/migrations and obsolete repair tests are retired. Current runtime queue/Memory readers select only the canonical declaration; explicit deploy quiet readers support exact current19.12 and immediate source19.11. Body-retirement source DDL required by the explicit inverse moved into `final_action_schema.py`.

The one current Actions declaration retains four real historical Attempt fields and the existing physical Memory parent FK. Current writes use production actual defaults. Frozen historical factories retain byte-identical default declarations. No business fields, action kinds, audit/fact-check tools or sending permissions were added.

## Production-copy proof

SQLite online backup copied the production database into a task temporary directory; source production was read only. Copy size 487,538,688 bytes; quick_check `ok`. One pre-existing FK finding: `runs` row190042 → `tasks`, FK1. It remains unchanged.

The proof compared 514,261 rows across all physical tables, reply public projections, and allocator rows. Python scalar type and value were hashed with length framing; nonreply payload BLOB bytes were compared exactly, all existing Attempt fields were projected in their original order, and Memory public parent IDs were compared independently of the physical-reference conversion. Only the Store version row and its clock were excluded from cross-version data comparison; inverse restores that original marker and clock explicitly.

Forward19.11→19.12, current-data inverse19.12→19.11, archived19.11 code cold open, and reforward19.11→19.12 all preserved these digests and original FK findings. This was a schema inverse applied to current data, not restoration of an old database snapshot. Full digest evidence: `validation/2026-10-21-runtime-current-schema.json` (422.35 seconds total).

## Focused regression evidence

The missing-current-index regression failed before implementation: the baseline constructor silently repaired the missing index instead of rejecting. Current constructor tests cover index/trigger loss with database dump unchanged, exact-current cold opens, Store-first/Email-first construction and fresh seeds. Canonical Actions tests include physical Memory references and all retained fields; historical factory declarations/layouts/projections were compared byte for byte against baseline.

Explicit migration tests cover both19.11 physical sources, new current writes surviving inverse/reforward, unresolvable Memory reference failing transactionally without mutation, and fresh-source inverse refusing to erase newly stored nonneutral facts. No real outbound send is part of these tests.

Two broader Email tests were independently rerun against untouched baseline `b6baf4ee5` and also fail there: training snapshot parent tamper readback and the old assertion that all29 historical Action types remain declared. The stale tamper fixture has now been corrected to address the existing object key, and the DDL assertion now checks the exact current manifest and retired-kind absence. Both tests exercise existing behavior; no new fact-check or safety policy was introduced. An obsolete confirmation fixture likewise failed on baseline because it attempted to migrate13.5 directly through current19.11; its existing historical nonempty-source guard is now tested directly against its exact mapped source rather than widening the current19.12 release.

Current acceptance batches: 139 publication/quality/updater/maintenance tests passed; 128 business/schema/meeting-memory cases passed with one obsolete confirmation fixture then corrected and rerun; seven exact confirmation/snapshot/fresh-schema cases passed; 19 semantic/frozen-record/schema cases passed; final five paired-reader cases passed after adding mixed-Email-version rejection. Canonical/current bootstrap suite had 11 passes. Necessary explicit Email release and runtime tests had 25 passes, with two additional fresh-seed cases.

The existing no-worker Console copy script traversed 15 API paths, including History pages, Attention, queues, Tasks, legacy Project/Todos/Updates, Meeting public/physical IDs, Email preview list and unavailable-original502. All persisted SQL values/types remained unchanged. Network connects were blocked, application lifespan was not started, the Email source read used a substitute, and real sends were zero. See `validation/2026-10-21-runtime-current-api.json`.

## Review and production

Pending focused business regression completion, independent spec/quality review, upstream reconciliation, standard deployment, and live readback. Tests use outbound substitutes only. This document does not claim deployment or external business outcomes yet.
