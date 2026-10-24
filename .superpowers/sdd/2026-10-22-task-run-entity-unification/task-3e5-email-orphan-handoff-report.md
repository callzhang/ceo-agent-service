# Task3E5 — Email orphan RuntimeAttempt handoff

## Result

The migration-only Run handoff is now version 3. It explicitly preserves a
closed offline Email operation after Task inverse removes its Task owner. The
portable, digest-bound `runtime_attempt_orphan_workload_edges` records the
exact source physical Attempt ID, predecessor-format source operation key,
workload kind, and current canonical native Task key. Multiple Attempts for
the same deleted Task share one deterministically allocated operation key.

Inverse classifies an Email Attempt from current Task owner facts. A live
Email Task follows normal typed identity resolution; a live wrong-kind Task
fails. Only a Task actually absent from the complete Task inverse owner map can
become an orphan, and only when its Attempt has NULL `parent_id`. The
operation key is derived from the canonical native Task key and passes the
predecessor Email operation grammar. Attempt identity allocation happens
before the handoff edge is serialized, so each edge names its final source
physical ID. No Task, business field, runtime alias, placeholder owner, or
runtime-history capture was added.

Forward conversion validates the exact edge shape, ordering, uniqueness,
positive canonical native key, predecessor key grammar, exact source Attempt
row, NULL parent, source Task-key collision, and live native Task collision
before replacing `runs`. A validated edge restores the unowned native key
directly; ordinary Task workload references still use the strict resolver.
The adoption inverse consumes the v3 handoff and checks source Attempt and
source Task-key collisions too. New live Tasks absent from sparse captured Task
maps are checked against actual `main.tasks` first, so they remain normal live
owners.

## Implementation and documentation

- `app/run_entity_schema.py`: v3 digest and exact handoff shape; inverse orphan
  identity allocation; pre-replacement forward validation; direct native-key
  restoration.
- `app/run_adoption_inverse.py`: exact v3 consumption and orphan validation
  against current source Attempt and Email Task facts.
- `tests/test_run_entity_schema.py`: two orphan Attempts from the real offline
  Email adapter, repeated portable Task/Run inverse and forward cycles, current
  field preservation, no Task replay, Task sequence high-water, hermetic source
  row readback, sparse-map/new-live-Task regression, and malformed/duplicate/
  wrong-kind/source-collision/parent/workload/digest rejections before Run
  schema replacement.
- `tools/validate_email_orphan_old_dao.py`: explicit operator acceptance that
  invokes the actual predecessor DAO against the same two-cycle fixture.
- `tests/test_run_adoption_inverse.py`: adoption-inverse source-key collision
  rejection.
- `docs/architecture.md` and `docs/runtime-mechanism.md`: current v3 converter
  contract and unactivated boundary.

The standard pytest regression is hermetic. It checks the restored source rows
directly and does not require or resolve any predecessor checkout. The actual
old DAO read remains a separate operator acceptance check, run against the
same shared two-cycle fixture by passing an explicit predecessor root:

```sh
python tools/validate_email_orphan_old_dao.py \
  --predecessor-root /path/to/active-predecessor \
  --work-dir /private/tmp/ceo-task-run-phase2-_ntbufyi/task3e5-old-dao-fix1
```

The acceptance script calls the actual predecessor
`AutoReplyStore.list_runtime_operation_attempts` and
`runtime_operation_parent_is_runnable` methods against the restored source
SQLite database. It reads both terminal Attempts at their allocated source
physical IDs, verifies no Agent Run parent, and confirms the parent is not
runnable. It preserves the SQLite database in the supplied work directory for
readback. The earlier `f141b8a^` checkout is Phase2/3E4 unactivated code, not
the deployed predecessor for this proof.

## Verification

- Baseline regression was observed RED before this change:
  `tests/test_run_entity_schema.py::test_closed_offline_email_task_attempt_survives_current_task_run_inverse`
  raised `ValueError: native Task public identity is unresolved` for the closed
  Email Task.
- Final scoped run: `pytest -q -x --basetemp=/private/tmp/ceo-task-run-phase2-_ntbufyi/task3e5-final2 tests/test_run_entity_schema.py tests/test_run_adoption_inverse.py tests/test_run_adoption_inverse_rows.py`
  — **85 passed in 65.19s**.
- Focused adversarial/old-DAO run:
  `pytest -q --basetemp=/private/tmp/ceo-task-run-phase2-_ntbufyi/task3e5-adoption tests/test_run_adoption_inverse.py::test_adoption_inverse_rejects_orphan_email_source_key_collision tests/test_run_entity_schema.py::test_email_orphan_handoff_rejections_precede_run_schema_replacement tests/test_run_entity_schema.py::test_closed_offline_email_task_attempt_survives_current_task_run_inverse`
  — **9 passed in 5.82s** (the final offline two-cycle regression was rerun
  with the final suite and passed there as well).
- `ruff check app/run_entity_schema.py app/run_adoption_inverse.py tests/test_run_entity_schema.py tests/test_run_adoption_inverse.py tests/test_run_adoption_inverse_rows.py` — passed.
- `python -m py_compile app/run_entity_schema.py app/run_adoption_inverse.py` — passed.

## Portability repair (Fix1)

The predecessor source path is no longer referenced by the pytest module. The
same two-cycle fixture now has a normal hermetic test that checks source rows
directly. The separate `tools/validate_email_orphan_old_dao.py` acceptance
script requires `--predecessor-root`, runs the same fixture with the actual old
DAO reader, and preserves the resulting database under `--work-dir`.

- Hermetic check:
  `pytest -q --basetemp=/private/tmp/ceo-task-run-phase2-_ntbufyi/task3e5-fix1-hermetic tests/test_run_entity_schema.py::test_closed_offline_email_task_attempt_survives_current_task_run_inverse`
  — **1 passed in 1.59s**, with no predecessor path configured.
- Explicit old-DAO acceptance:
  `python tools/validate_email_orphan_old_dao.py --predecessor-root /private/tmp/ceo-task-run-phase2-_ntbufyi/predecessor-c38ce12cf --work-dir /private/tmp/ceo-task-run-phase2-_ntbufyi/task3e5-old-dao-fix1`
  — passed; **2 old DAO reads**, 2 Attempts per read, `parent_runnable=false`;
  database preserved at `/private/tmp/ceo-task-run-phase2-_ntbufyi/task3e5-old-dao-fix1/task2d6-offline-inverse.sqlite3`.
- Re-run scoped suite:
  `pytest -q -x --basetemp=/private/tmp/ceo-task-run-phase2-_ntbufyi/task3e5-fix1-suite tests/test_run_entity_schema.py tests/test_run_adoption_inverse.py tests/test_run_adoption_inverse_rows.py`
  — **85 passed in 63.30s**.
- Portability-fix Ruff/compile/diff checks passed.

## Boundary

This is an unactivated converter change only. No constructor, full cold
application, provider, production, deployment, or full-suite validation was
performed. Full cold-constructor validation remains part of the later Task5
review. Original Email runtime files were not changed.
