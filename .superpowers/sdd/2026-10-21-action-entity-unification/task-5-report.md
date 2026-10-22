# Task 5 partial implementation report

## Status

Task 5 is incomplete. This branch contains a reviewed-schema/runtime integration in progress and a release API skeleton, but it does not yet convert populated legacy Action families to the common Action/receipt model. It must not be deployed or treated as an acceptance-ready migration.

## Implemented in this WIP

- Added `prepare_action_entity_release`, `publish_action_entity_release`, and `invert_action_entity_release` in `app/action_entity_schema_release.py`, plus the `ActionEntitySchemaRelease` paired-release wrapper. The preflight validates the exact Store 19.12 / Email 53 predecessor declaration, marker/version, source schema fingerprint, and FK findings. It is read-only.
- Current source and target factories now retain the distinct predecessor versus current direct-email guard declarations, allowing authentic Store19.12/Email53 preflight to pass without changing the frozen predecessor SQL.
- The prepared outbound receipt has a nullable owner-local `prepared_receipt_id` Action field, with DDL owner guards and a current writer/read projection. This pins the prepared provider body independently from canonical and Sent receipts. The field map records its source and technical purpose.
- Added a lifecycle regression that keeps distinct prepared, Sent, and canonical provider bodies independently readable and rejects a cross-Action prepared-receipt pointer.
- Added release registration hooks and mechanism-document changes in the Task 5 owned files.

## Not implemented / acceptance still open

- Forward conversion of populated source facts from `candidate_action_attempts`, `external_action_results`, `outbound_postfixes` and its receipt, `sent_replies` / observers, `email_actions`, and `email_action_attempts` into Actions, Action Receipts, and Action Observers.
- Typed source identity mappings, common-Action identity conflict detection, direct-email `email_historical_operations.action_ids_json` physical-ID rewrite/inverse, preservation of existing and post-publication provider receipt lineage, and current-data inverse reconstruction.
- Rewriting or retiring proven-unused covered legacy layouts, generated columns, and indexes. Out-of-scope unsubscribe and plan records must remain. No retirement completion is claimed.
- A genuine active old-source forward fixture and a genuine Task3-writer post-forward fact fixture. Existing release tests are empty-source placeholders except for a fabricated Action case; that case is not valid acceptance evidence and needs replacement with real Task3 writer APIs and parent/receipt facts.
- Authentic-source forward/inverse/old-cold-open/reforward execution. The authoritative input remains read-only at `/tmp/ceo-action-entity-validation.8rUAvK/source.sqlite3` (SHA256 `799307664c58aeb2163eb44fa18a3261fadeb36fabf9e8534e905fdfe966f1a1`, Store 19.12 / Email 53). No migration was run against it or against production.

## Verification evidence available

- `python -m pytest -q tests/test_action_entity_contract.py tests/test_current_action_schema.py tests/test_runtime_current_schema.py --basetemp=/tmp/ceo-action-entity-task5-field3` — 24 passed.
- `python -m pytest -q tests/test_shared_action_lifecycle.py::test_prepared_receipt_is_pinned_separately_from_sent_and_canonical_bodies --basetemp=/tmp/ceo-action-entity-task5-field6` — 1 passed.
- `python -m pytest -q tests/test_action_entity_schema_release.py --basetemp=/tmp/ceo-action-entity-task5-next4` — 4 passed, 2 failed:
  - `test_conflicting_identity_aborts_without_mutation` has no populated conflicting identity fixture, so publish does not raise the asserted conflict.
  - `test_new_action_and_recall_survive_inverse` fabricates an invalid body-only Action; the inverse rebuild fails because the legacy row requires `actions.legacy_key` (`NOT NULL constraint failed`). Replace the fixture with genuine Task 3 writer calls and real parent facts; do not fix it by inventing business fields.
- Authentic read-only `prepare_action_entity_release` passed for the supplied source copy, identifying Store19.12 / Email53 and one existing FK finding. No populated forward test has passed yet.
- `python -m pytest -q tests/test_outbound_postfix.py tests/test_action_receipt_callers.py` had 14 passed / 7 failed in a broader investigation. The failures concern other current-owner/publication/candidate paths and are not evidence of a passing Task 5 release.
- No full suite, production DB access, provider/native CLI call, push, or deploy was performed.

## Source-backed risk for the next implementation pass

The current prepared-message, reviewed-action, and Sent code paths need a real same-effect identity test. `prepare_outbound_action` owns a delivery-key Action whose external key is NULL; `begin_reviewed_action` looks up by external key and may create a separate Action; `record_sent_action` also joins by external key. `SystemMessageHandler._prepared` derives the key from the business object, action identity, option key, generation, and revision, so a legitimate binding may be available. This is a named risk only; no runtime one-effect split has been demonstrated. Verify it with a real SystemExecutor flow and a fake provider before deciding how migration/adoption should bind it. Do not use body or token equality as identity.

## Handoff boundary

The next pass should start with populated source-writer tests and actual Task3 post-forward writer facts, then implement conversion and current-data inverse. Keep the authentic DB and old-code archive immutable. The exact old-code archive available is `/tmp/ceo-action-entity-validation.8rUAvK/old-code-897227b`; the previously named `d0ce` source/artifact is unavailable, and its cleanup cause is unverified.

This report describes an implementation checkpoint, not Task 5 completion.
