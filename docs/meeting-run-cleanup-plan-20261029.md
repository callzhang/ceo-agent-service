# Meeting result and retired Run entry cleanup

Approved scope: no DDL; no legacy runtime fallback; no audit, delivery, authorization, retry or identity policy change. Preserve all existing business facts and receipts. Tests use substitutes; live readback is read-only.

## Task 1: Meeting result storage
Replace default INSERT plus partial UPSERT in update_meeting_alignment_job with one explicitly targeted UPSERT. Insert the same defaults, update only supplied result fields, preserve lifecycle behavior. Test one result write, missing row initialization, existing-field preservation and repeated updates.

## Task 2: Retired logical Run entry
Delete app/logical_run_storage.py after removing its only test/script dependencies. Retire obsolete source-field writer assertions or move frozen historical obligations into test fixtures. Update scripts/validate_database_closeout_copy.py to native runs and run_provenance, including current identity/URL contracts; remove unrelated stale assumptions exposed in that script only as needed for current read-only verification. Add focused substitute tests. Do not edit app/store.py.

## Task 3: Validate and release
Focused tests, Ruff, schema/hash/FK baseline, SPEC/QUALITY review, standard exact-commit deploy after coordinating publisher; independent health/queues/Attention/History/Meeting detail readback. Validate then clean exact task-owned backup/temp generation. No production test business actions.
