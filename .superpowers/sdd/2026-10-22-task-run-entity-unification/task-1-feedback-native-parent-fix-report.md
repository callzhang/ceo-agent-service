# Task 1 Feedback native-parent repair report — 2026-10-22

## Result

The unactivated assembled target now permits a newly created `feedback_iteration_decision_items` Action to reference actual native Feedback decision, item, and round owners without fabricated `task_run_identity_map` records. Six source-only Feedback discriminator triggers are omitted from the assembled target; the translated typed foreign keys remain the authority for parent existence and domain type. No runtime reader/writer activation, migration, data copy, provider/CLI operation, production access/write, push, or deploy occurred.

## Root cause and change

The source-shaped Action declaration emits six insert/update triggers that check whether Feedback parent values belong to particular source kinds in `task_run_identity_map`. The Task1 assembly separately translates the three Action columns to real native foreign keys. A new Feedback owner correctly has no migration-map row, so the retained source trigger rejected a valid native write before the native foreign keys could establish the actual parent relationships.

The assembly now explicitly retires those six source-only triggers and no longer defines identity-map-backed replacements. It preserves the other Action parent guards, feedback Action checks/indexes, decision-item append-only triggers, and the mapping table used as migration metadata. The complete assembled SQL artifact has been regenerated and contains 473 objects. The adoption-only SQL artifact was regenerated from its existing serializer and remained byte-identical because it does not include Action parent triggers.

## Evidence

- RED: after removing the test's hand-seeded identity-map rows, the real assembled-schema native Action insert failed with `feedback parent type mismatch`.
- GREEN: the native regression creates a feedback-processing Task and actual batch, decision, item and round rows, then inserts the Action with no identity-map rows. Its decision and round IDs intentionally equal the unrelated Task ID. The valid insert succeeds; missing decision, item key and round cases each fail with a foreign-key error.
- The target regression inspects assembled Action parent-trigger SQL and asserts it contains neither `task_run_identity_map` nor `source_kind`.
- Existing source-shaped Feedback parent tests still exercise valid source parents, missing foreign keys and wrong-kind discriminator collisions using source declarations and `insert_feedback_action`.
- Focused verification: 47 tests passed across Task/Run assembly, adoption and definition tests, plus the source Feedback parent test. `compileall`, Ruff and `git diff --check` pass. The artifact determinism/catalog checks run in the focused assembly suite.

## Remaining boundary

This is only local unactivated schema-contract evidence. Independent SPEC and QUALITY review is pending. Runtime caller conversion, migration/current-fact inverse, actual-copy proof, old-code cold open/write/reforward, and any runtime release decision remain separate gates. No claim is made about current production schema or behavior.
