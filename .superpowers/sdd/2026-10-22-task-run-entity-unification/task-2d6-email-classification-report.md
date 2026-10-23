# Task2D6 Email classification native Task report

Status: the Email classification unit is implemented and verified against the
native Task/Run/Action assembly. Shared all-family UI and constructor/deploy
preflight activation remain separate work. This report does not claim release,
production, or full-suite completion.

## Runtime ownership

- The canonical public classifier key is the decimal native `tasks.id`. Native
  Task status and lifecycle columns own pending/running/done/failed, attempts,
  generation, availability, lease and error facts. `parameters_json` stays
  `{}`. The virtual `STATUS_MAP` is presentation only and does not own runtime
  state.
- `email_classification_inputs` owns `channel`, stable message identity and
  immutable/redactable input JSON. `task_results` owns only the classifier
  result. Classification business records, ActionPlans and action receipts
  retain their existing independent Email business owners.
- The adapter, classification producer, worker/backend route, runtime workload
  grammar and parent validation, offline wrapper, storage maintenance, quality
  aggregation, Email queue/Attention readers and deployment-drain Email count
  use native Task identity and lifecycle. Existing typed transient classifier
  retry classification and its four-attempt bound remain unchanged.
- Offline open persists the source child facts (`channel=email`, stable
  `offline:<incoming token>`, `{}` input, owner, attempt/generation 1, six-hour
  lease); reopening the same incoming token replaces the previous placeholder
  as source `INSERT OR REPLACE` did. Wrapper delegation uses the canonical Task
  ID. Finally-close and owner-scoped crash purge delete the exact empty-input
  Task and its result row.
- The close→inverse orphan-attempt risk is handled by the already reviewed
  Task3E5 v3 portable `runtime_attempt_orphan_workload_edges` handoff. It
  allocates a source-valid operation key at inverse time from the current
  canonical Task key, validates source Attempt identity/workload/key/parent and
  live Task-key collisions at forward time, and retains no runtime alias or
  Task tombstone. See `task-3e5-email-orphan-handoff-report.md` for its actual
  old-DAO and two-cycle evidence.

## Migration and lifecycle evidence

`test_source_migrated_email_tasks_keep_status_input_result_and_lifecycle_facts`
starts from an actual `EmailStore` source database and inserts classifier
records with the source Task writer. In one caller-owned FK-off transaction it
captures Run adoptions before changing owners, captures Task and Run source,
then runs Task forward, Run forward with the standard v3 `_run_handoff`, and
Run-adoption forward. It commits, turns FK enforcement on, and passes the actual
database-wide `foreign_key_check`. The source includes populated email business
schema; it is not a Task-only or fake-Store migration.

The source fixture contains pending, running, and done classifier Tasks with
real classifier payloads, input/result child rows, source clocks, attempts,
generation, owner and lease. The migrated producer reuses the existing done
Task idempotently without replacing its result; the pending Task is claimed and
finished, and the migrated running Task is finished through the native adapter.
The source done result remains unchanged. This exercises source-migrated data
through the actual adapter, producer, SQL child owners and lifecycle writes.

Task3E5's exact offline close/inverse/old-code/reforward evidence is accepted as
the converter prerequisite: two JSON-portable cycles, allocated source keys,
actual terminal Attempts, old DAO reads without replay, and collision/ownership
validation are covered in that task's report. Task2D6 does not alter converter
code.

## Verification

The Task2D6 focused regression group passed:

```text
pytest -q --tb=short --basetemp=/private/tmp/ceo-task-run-phase2-_ntbufyi/task2d6-focused-green \
  tests/test_email_task_adapter.py::test_email_classification_console_queue_reads_native_owner \
  tests/test_email_task_adapter.py::test_email_classification_adapter_uses_native_task_and_typed_result \
  tests/test_email_task_adapter.py::test_email_classification_expired_claim_uses_native_cas_and_canonical_runtime_key \
  tests/test_email_task_adapter.py::test_concurrent_native_classification_enqueue_has_one_stable_owner \
  tests/test_email_task_adapter.py::test_source_migrated_email_tasks_keep_status_input_result_and_lifecycle_facts \
  tests/test_email_worker.py::test_native_classification_worker_keeps_email_result_separate_from_task_result \
  tests/test_email_training_labeler.py::test_reopening_offline_classification_replaces_the_same_native_owner \
  tests/test_email_task_adapter.py::test_classifier_retryable_failure_stops_after_four_attempts \
  tests/test_email_task_adapter.py::test_classifier_repeated_expired_claims_stop_after_four_attempts \
  tests/test_email_task_adapter.py::test_classification_task_bootstrap_recovers_crash_interrupted_claim \
  tests/test_run_entity_schema.py::test_closed_offline_email_task_attempt_survives_current_task_run_inverse
11 passed in 9.13s
```

The complete adapter module reported `18 failed, 58 passed`. All 18 failures
are in existing Email reply/action-plan business-task flows (including producer,
redacted payload/context and reply identity cases); they fail before reaching
classifier behavior because `AutoReplyStore._ensure_business_reply_task`
queries the absent generic `task_inputs` table. Those paths are outside this
classifier-only change and depend on the separate Task input-reader cutover;
they were not rewritten, skipped, or represented as passing. The native
classifier tests, including schema/lifecycle/retry/claim/race and source
migration tests, pass.

For the fail-first evidence, the exact legacy Task12 projection used by the
BASE adapter was run against `task_run_entity_assembly_objects()` and failed on
the actual native `tasks` declaration (`no such column: record_type`). The
native adapter tests then pass using the same assembled target declarations.
`_native_email_store` explicitly binds the real EmailStore connection code to
assembled test DDL; it does not claim EmailStore's production constructor has
been switched to the assembled schema.

No provider, model, real CLI, production database, service, push, deploy,
cleanup, or full-suite operation was used. This local unit result does not
complete the shared constructor, all-family dashboard/detail/History, or
release preflight work.
