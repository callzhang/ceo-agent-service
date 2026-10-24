# Latest-main integration report

## Integration

Integrated `origin/main` at `218db0ad3c464ebf81739573d5105487dbcb1603` into the Task/Run branch whose pre-merge HEAD was `ee0a1fc511acbec1485e3a5f8b4a1050aa3b9504`. This is a normal two-parent merge; no reset, rebase, or history rewrite was used. The new merge commit is the integration result containing both those parents.

Resolved the six overlapping files against the native Task/Run owners and the latest main behavior:

- `app/audit_web.py` and `app/web_api/dashboard.py`: retained native Task/Run projections and upstream retirement of the Task Memory worker queue and its Attention/status rows.
- `app/cli.py`: retained the upstream Meeting scan behavior without the retired Memory enqueue path.
- `app/quality_gate.py`: combined upstream candidate metrics with the native candidate and execution/action-attempt projections.
- `app/store.py`: retained native Task/Run storage, upstream task-memory worker/protocol retirement, and the latest Meeting frozen-candidate audit-only recovery and exact delivery-receipt/CAS guards. Removed superseded source-shaped logical-run declarations that shadowed native aliases.
- `tests/test_meeting_storage_outer_readers.py`: kept native physical Meeting ownership, asserted retired Meeting Memory writes no longer enter Attention, and retained raw historical Action readability.

The supported explicit Meeting error-projection migration still had Task8 source SQL. Ported its eligible-row read and conditional write to native `tasks` and `update_task_cas`; updated its test fixtures to create native Meeting tasks and preserve the Run-history assertion. Ported the due-reply drain fixture to join native `tasks` and `task_inputs` rather than querying the retired Task8 `legacy_key` projection. No runtime source-schema fallback was added.

## Focused verification

- `pytest -q --assert=plain --tb=short -o faulthandler_timeout=20 tests/test_error_code_migration.py tests/test_meeting_storage_outer_readers.py::test_explicit_error_migration_changes_typed_projection_only tests/test_meeting_alignment.py::test_failed_frozen_meeting_candidate_reaudit_stops_after_prior_receipt tests/test_meeting_alignment.py::test_failed_frozen_meeting_audit_return_stays_failed tests/test_meeting_alignment.py::test_returned_frozen_candidate_can_be_explicitly_reaudited_without_changes tests/test_meeting_alignment.py::test_rejected_frozen_candidate_cannot_be_reaudited_as_a_return tests/test_meeting_alignment.py::test_failed_frozen_meeting_audit_recovery_requires_exact_digest tests/test_meeting_alignment.py::test_requeue_failed_meeting_delivery_preserves_review_and_key tests/test_meeting_alignment.py::test_requeue_failed_meeting_delivery_rejects_unreviewed_candidate tests/test_meeting_recovery_safety.py tests/test_meeting_storage_outer_readers.py::test_attention_status_and_memory_child_use_physical_meeting_parent` — **23 passed**.
- `pytest -q --assert=plain --tb=short -o faulthandler_timeout=20 tests/test_task_source_documents.py tests/test_audit_web.py::test_status_lists_the_outbox_and_memory_write_queues tests/test_deployment_drain.py::test_each_dispatcher_source_retains_its_due_item_during_drain tests/test_deployment_drain.py::test_quiet_reads_supported_pre_lifecycle_publication_source` — **31 passed**. This covers numbered source documents and Signal bindings, retired Task Memory queue reporting, all due-item drain selectors, and the supported pre-lifecycle source fixture.
- In `frontend/`, `npm test -- --run src/pages/StatusPage.test.tsx` — **2 passed**; `npm run build` — **passed** (`tsc --noEmit` and Vite production build).
- `git diff --check` and `git diff --cached --check` report whitespace already present in the incoming upstream diff (including whitespace-only blank lines); those unrelated lines were left untouched.

## Evidence boundary and remaining work

Task5's broad prior run recorded 316 passes, 59 failures, and 3 setup errors. Task5's author confirmed that the exact failing-node list was not persisted. The repository's accumulated pytest `lastfailed` cache is not a reliable substitute, so this integration did not relabel those historical failures as green or classify them by guessing. The affected native callers and the requirements above received focused checks only; this report makes no whole-suite claim.

The frozen source-copy evidence remains the Task5 report's evidence for its exact pinned source and is not refreshed here. The source metadata recorded there was 505 objects with catalog digest `920b7eedb6138439ab94d4fbf109e60258ce8842c212d3a2ee3eec56fb4ca290`. This code-only merge did not re-run the copy/model workflow. Production still runs the pre-Task/Run checkout; this integration is not pushed or deployed, and no live UI or production readback is claimed. Root's independent task-scoped and whole-branch reviews and the separately owned release/deploy gates remain outstanding.

## Pinned release/docs drift follow-up

After the first integration, `origin/main` advanced by exactly two commits (`218db0ad3c464ebf81739573d5105487dbcb1603..278431eabf2bd1903582d9a480c5f423bdd9ab15`, 7 files, 35 insertions/12 deletions). A normal merge produced `7b62e653dcc725248f31c64ed184f21c9ee2e361`, with parents `1fd657e91afe1c8f36bac67f8fe8c62249d94187` and `278431eabf2bd1903582d9a480c5f423bdd9ab15`. It contains only verified Skill target-baseline metadata and OA historical-evidence documentation/tests; no runtime owner or schema changed. The complete `tests/test_consumer_system_release.py` and `tests/test_oa_return_policy_skill.py` files passed: **33 passed**. No source-copy/model workflow or build was repeated. Existing dirty Task5 review claim rows were retained outside this follow-up commit. Whole-branch review and release/deployment remain open.
