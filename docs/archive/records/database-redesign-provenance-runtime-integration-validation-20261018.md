# Provenance fact runtime integration: local validation, 2026-10-18

This is a B/C/D runtime integration receipt for the unpublished development
worktree. It is not an independent SPEC or QUALITY acceptance, physical
provenance migration, actual-copy proof, provider availability proof, or
production deployment. The current selected Store still writes and retains
`business_source_documents.body`. The later decision to permanently remove
originals for 20 exceptional versions while keeping facts, sources and receipts
is authorized; no deletion occurred in this unit.

## Runtime boundary exercised

The four Store Signal read leaves return body-free facts with one immutable
source binding. The old selected backend derives its identity/SHA-256/UTF-8
byte checks transiently from its authoritative stored original. An explicit
request reader resolves the original after the writer transaction, once per
exact identity key, with no per-row backend fallback. Separate Signal IDs keep
their own title, context, dedupe and evidence links even when a body is shared.
Replay compares all prior source and independent Signal fields plus body
identity checks. Task Agent, ProjectContext, suggestion and source-bundle
historical quote checks use prepared per-Signal facts and in-memory originals;
the writer reselects bindings and relevant existing relationships. Current
WorkItem/Signal body is registered from its already supplied input. API views
carry a typed available-body or unavailable-null/reason state; the UI retains
saved quotations and source links and says “原文不可用” when needed. Source bundles
use the stable identity key and leave unknown original character counts null.
Minutes-owner backfill selects candidate meeting UUIDs and Task IDs, then reads
the exact current provider meeting record before creating a new WorkItem.

## Test chronology and results

- Five accepted fast reader/native test files were run first: **137 passed in
  0.60 s**. An initial command used a nonexistent `test_native_trajectory.py`
  filename and collected no tests; the corrected file was
  `tests/test_native_source_documents.py`. This was regression evidence for
  the accepted A1–A4 components, not runtime integration acceptance.
- After all runtime, locator, conflict and typed API changes, the same five
  files were rerun in a task-owned `TemporaryDirectory` with `--basetemp`:
  **140 passed in 1.63 s**, terminal exit **0**, subprocess wall time
  **3.35 s**. The temporary root was cleaned on exit. The count increased
  because new reader regressions were added after the initial run.
- The first seven-node cross-surface selection completed **2 passed, 5 failed
  in about 186 s**. The passing nodes were
  `test_task_semantic_service.py::test_reused_dedupe_key_cannot_change_persisted_source_identity`
  and `test_web_api_task_attention.py::test_detail_routes_show_source_evidence_and_confirmed_membership`.
  The five failures were existing fixture contract mismatches after body-free
  Signal reads: `test_task_semantic_store.py::test_signal_preserves_original_evidence_and_provenance`
  expected `.model_dump()` on a fact;
  `test_task_source_documents.py::test_shared_source_bundle_keeps_every_signal_and_one_body_per_exact_version`
  omitted the prepared map;
  `test_project_context_service.py::test_context_service_accepts_quote_in_one_decoded_structured_source_string`
  omitted the map;
  `test_task_retrieval.py::test_project_context_without_task_is_retrieved_from_its_own_sources`
  omitted the map; and
  `test_minutes_owner_backfill.py::test_a_meeting_with_an_ownerless_candidate_is_queued_once_with_the_conversation_attached`
  lacked fake `get_minutes_info`. Their assertions were migrated to prove
  body-free facts, exact resolved-body identity/bytes, quote and link behavior,
  and the real result wrapper. These five were not claimed as feature RED tests.
- New pure regressions exposed two genuine draft bugs before repair: a
  conflicting binding with the same identity key borrowed another Signal's
  available body, and `add_current` rejected an exact body with valid native
  locator metadata. Both were observed failing before fixes and now pass.
  New pure relationship tests cover changed Task evidence role, changed
  existing Attention membership and removed existing Project proof link.
- `tests/test_task_source_documents.py` completed **22 passed in 155.71 s**,
  including available bounded ranges, unavailable null character metrics,
  retained quotations and conflicting bindings. The eight selected
  cross-surface nodes later expanded through parametrization to **11 passed,
  1 failed in 694.60 s**. The sole failure was an old test's direct SQL query
  for the retired `business_task_todo_sync_outbox` table after its suggestion
  and replay assertions had passed. Replacing that fixture query with the
  current Store projection preserved its no-TODO/no-follow-up assertion; the
  exact failing node then passed **1 in 83.84 s**.
- `tests/test_business_source_reader.py`, the typed API availability case and
  the three pure relationship-race nodes completed **18 passed in 6.81 s**.
  After retaining previous enum/Nonblank/JSON metadata constraints on the
  typed API view, the malformed metadata/status case passed **1 in 4.44 s**.
  Selected fake-provider minutes backfill missing/wrong UUID and two-Task
  sharing cases passed **3 in 190.74 s**.
- A frontend run in a task-owned `/tmp` `TemporaryDirectory` used a copy of
  this worktree's `frontend/` plus APFS-cloned installed dependencies from the
  primary checkout. The primary and worktree lockfiles have matching SHA-256
  (`f3fd2da575f28633014dc4dcef48f134c344226553e143d086a89deeec237df8`);
  a separate inventory found 259 matching installed packages, no missing
  nonoptional package. The first focused Vitest run passed **55 tests** but
  `tsc` found one Project fixture missing the new availability fields. After
  updating that fixture, three focused test files passed **63 tests** and
  `npm run build` completed `tsc --noEmit && vite build`. Build output remained
  inside the temporary copy. Both temporary copies were cleaned in `finally`;
  `/tmp/provenance-*` had no remaining entries.

The selected cross-surface run covered Store Signal projection/replay,
ProjectContext historical quote, retrieval, backfill, direct Task/Project/
Attention API, suggestion replay and existing Attention proof. Python Ruff on
the edited runtime and tests and `git diff --check` passed. No whole suite was
run; the unselected physical provider readers, real provider/native CLI,
actual production data, full migration, and live service were not exercised.
No stage, commit, push or deploy occurred. Fresh independent SPEC and QUALITY
review should assess this integrated runtime before the physical transition.

## Exact selected commands

All Python commands below used `/Users/derek/miniforge3/bin/python` in the
development worktree. The final eight-node cross-surface selection, which
parametrized to twelve cases, was:

```sh
python -m pytest -q \
  tests/test_task_semantic_store.py::test_signal_preserves_original_evidence_and_provenance \
  tests/test_task_semantic_store.py::test_distinct_task_signals_share_one_exact_source_body \
  tests/test_project_context_service.py::test_context_service_accepts_quote_in_one_decoded_structured_source_string \
  tests/test_task_retrieval.py::test_project_context_without_task_is_retrieved_from_its_own_sources \
  tests/test_minutes_owner_backfill.py::test_a_meeting_with_an_ownerless_candidate_is_queued_once_with_the_conversation_attached \
  tests/test_web_api_task_attention.py::test_detail_routes_show_source_evidence_and_confirmed_membership \
  tests/test_task_agent.py::test_independent_project_suggests_from_historical_role_then_human_promotes_same_id \
  tests/test_task_agent.py::test_existing_attention_identity_and_original_proof_are_stored_facts
```

The 18-case pure reader/API/relationship selection was:

```sh
python -m pytest -q \
  tests/test_business_source_reader.py \
  tests/test_web_api_task_attention.py::test_signal_response_view_requires_honest_original_availability \
  tests/test_task_agent.py::test_prepared_historical_task_link_role_change_is_rejected_before_writer_mutation \
  tests/test_task_agent.py::test_prepared_existing_attention_membership_change_is_rejected_before_writer_mutation \
  tests/test_task_agent.py::test_prepared_existing_project_proof_link_removal_is_rejected_before_writer_mutation
```

After the API metadata annotation refinement, the single
`test_signal_response_view_requires_honest_original_availability` node was
rerun. The three-case fake-provider backfill selection was:

```sh
python -m pytest -q \
  tests/test_minutes_owner_backfill.py::test_missing_or_wrong_current_meeting_record_is_skipped_before_new_work_item \
  tests/test_minutes_owner_backfill.py::test_two_ownerless_tasks_read_the_same_original_meeting_body
```

The final frontend copy ran `vitest run` on
`src/api/console.test.ts`, `src/pages/TaskDetailPage.test.tsx`, and
`src/pages/TaskProjectDetailPage.test.tsx`, followed by `npm run build` in
the same task-owned temporary frontend directory. Its dependency tree was
cloned with `/bin/cp -cR` from the matching primary install; no install or
build ran in that primary checkout.

The final fast-reader command was:

```sh
python -m pytest -q --basetemp <task-owned temporary pytest directory> \
  tests/test_business_source_reader.py \
  tests/test_business_minutes_source_reader.py \
  tests/test_business_weekly_source_reader.py \
  tests/test_business_console_source_reader.py \
  tests/test_native_source_documents.py
```

## Same-author repair after R1 SPEC rejection

The immutable R1 freeze `9cf93f551f7364345cabfdf871985b2a6b47da0c292869a4c4c76400171a6100`
received `not_approved_two_C_gaps` in
`docs/superpowers/specs/2026-10-18-provenance-runtime-integration-spec-review.json`.
This section records the subsequent same-author C1/C2 repair; it does not
change the R1 receipt or assert independent acceptance of a new freeze.

C1 added a metadata-only `PreparedSources.require_fact` check. Historical
acceptance and all identity bases now compare the in-writer fact with its
prepared Signal ID, independent metadata and full source binding, without
requiring an original body. Standalone `apply_acceptance` prepares that fact
before opening its writer; a caller-supplied writer must provide the prepared
map. The Task Agent supplies its existing decision map. No other app caller
passes `_db` to `apply_acceptance`.

C2 rechecks only the relevant existing Attention card/member and Project
proof snapshots for each applied assessment after the domain commit. The
recheck and `BusinessAttentionProjection.upsert` now use the same projection
writer transaction. A caller-owned `_db` is optional, preserving standalone
`upsert`. New Task links created by the main decision are not replayed as old
snapshots; a prior proposal's card update for a different Project does not
block the next proposal. The decision model rejects duplicate structured
Project selectors and `_validate_stored_project_assessments` rejects duplicate
resolved canonical anchors, so one validated decision cannot project the
same card twice.

The narrow RED cases were observed before repair:

- `tests/test_business_source_reader.py::test_metadata_only_prepared_fact_rejects_a_changed_binding_without_original`
  and `tests/test_task_agent.py::test_metadata_only_identity_uses_prepared_facts_even_when_original_unavailable`:
  **2 failed in 1.29 s**; the first lacked `require_fact`, and the second
  accepted changed historical context.
- `tests/test_task_agent.py::test_post_commit_attention_rejects_changed_prepared_card_before_upsert`
  and `tests/test_task_attention_projection.py::test_upsert_reuses_caller_writer_for_validation_and_mutation`:
  **2 failed in 0.65 s**; the changed card was applied and `upsert` did not
  accept the validated writer.
- `tests/test_task_semantic_service.py::test_acceptance_metadata_requires_prepared_fact_but_not_old_original`:
  **1 failed in 26.35 s** because acceptance with a supplied `_db` proceeded
  without any prepared historical fact.

After repair, the four initial pure nodes passed **4 in 0.71 s** and the
acceptance node passed **1 in 27.79 s**. The expanded final six-node pure
selection passed **6 in 1.70 s**, including two distinct existing cards that
both apply after the first card's intentional update, and a relevant Project
proof-link removal that still rejects. Real Store selections passed:
`test_project_and_attention_apply_without_task_lifecycle_changes` plus
`test_stored_assessment_accepts_project_proposal_and_attention_on_same_canonical_project`
**2 in 60.29 s**;
`test_upsert_creates_aggregated_attention_and_category_transition_preserves_id`
**1 in 37.40 s**;
`test_exact_owner_reply_accepts_only_cited_assignment_and_records_committed_date[False]`
**1 in 78.26 s**; and the adapted
`test_attention_projection_runs_after_outer_domain_transaction_commit`
**1 in 92.48 s**, terminal exit **0**, task-owned pytest temporary directory
cleaned. That final test proves the domain changes were committed before the
separate Attention transaction and that the projection validated and wrote
with the same `_db`. Its historical fixture SQL was updated to the selected
Actions projections while preserving the existing committed-state assertions.
Ruff, Python compilation and `git diff --check` passed after repair. No
frontend API shape changed, so frontend tests were not repeated. No physical
schema, real provider/native CLI, production database, outbound send, stage,
commit, push or deploy occurred.

The same five fast reader/native files were rerun after the C1/C2 repair with
task-owned `TemporaryDirectory`/`--basetemp`: **141 passed in 2.91 s**, terminal
exit **0**, subprocess wall time **5.04 s**, temporary directory cleaned. This
supersedes the earlier 140-pass pre-repair fast count for current-code coverage.
