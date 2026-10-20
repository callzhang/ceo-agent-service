# B1 Email metadata ownership validation

Local implementation: Store `2026-10-19.2` / Email52 to Store `2026-10-19.3` /
Email53. This record covers synthetic component and release checks. Root owns
independent SPEC/QUALITY review, the single fresh production-copy acceptance,
native acceptance, commit, publication, deployment and live readback. No
production database, real provider operation or real original-source read was
used by the implementer. The supplied accepted A2 baseline was left untouched.

The target removes exactly `class_sender`, `class_subject`,
`class_received_at`, `class_folder` and `class_preview`. Existing `message_*`
columns own these facts for both public projections. The one preview has a
1000-Unicode-codepoint TEXT constraint, including embedded NUL; it is never
substituted for a full body or model input. Classification, message and private
IDs; independent allocation water; RFC Message-IDs; thread fields; clocks;
labels; model inputs; plans and authorization versions remain distinct.

Forward preparation checks all source rows before DDL: four scalar pairs must
have equal SQLite values and `typeof`; classified `message_preview` must be
empty TEXT and `class_preview` bounded TEXT; message-only preview must be
bounded TEXT and all five retired classification slots must be NULL. No count
is hardcoded and no source value is normalized. Unknown declarations, inbound
FKs, external dependencies and TEMP shadows refuse before staging.

The release uses the existing prepare/publish/verify_loaded/rollback/finalize
protocol. Its inverse derives all five old classification fields from current
message facts, including new classified and message-only rows. It reconstructs
the old empty classified message preview and exact Email52 DDL/indexes/triggers,
preserves current row identities/storage classes/allocation water and the
original FK-finding multiset, and restores original marker clocks. The retained
metadata contains only declaration variant, markers and FK findings; no
business-row or body backup. Existing Actions still reference
`emails.classification_id`. Cold-start failure rollback is exercised separately.

The Email52 default declaration and historical A1/A2 factories/constants remain
frozen. Email53 is an explicit declaration option. Declaration SHA-256 values
(the repository's `_hash(_source_declaration(...))` representation):

| Declaration | SHA-256 |
| --- | --- |
| Frozen Email52 default | `ea47fb6f57d0a32fce49daa5982b44e1ef978d2ec0f2a167c81e77c21d425f15` |
| Explicit Email53 | `31e85fdabd1c49a89159da136caebcae04cdc00bfa547dbefa4acfa62f641eaf` |

RED evidence before runtime changes: fresh target retained all five retired
columns; classification writes/readers retained two owners; unequal paired
folder updates were accepted; the release was unregistered; preview remained
in `class_preview`. After the initial implementation, a new publication-reader
test exposed rejection of Email53 by the existing progress-store selector.
Additional guards exposed SQLite `length(TEXT)` stopping at NUL and an unknown
inbound FK omitted by the reused dependency probe. Each observed failure was
corrected and its focused test rerun; no failing assertion was removed.

Validation uses Python 3.12.11 / SQLite 3.53.4. Earlier checkpoints were 15
passing B1 tests (65.47s), 8 passing dependency/Unicode guard cases (8.38s),
95 passing current modules (157.09s), and 2 passing no-source-I/O/inverse
dependency cases (26.92s). These checkpoints overlap and are not additive.

Final focused results:

| Command / selection | Result |
| --- | --- |
| `python -m pytest -q -n 3 tests/test_email_metadata_schema.py tests/test_email_preview.py tests/test_email_classification_reader.py tests/test_retire_verified_email_inputs.py tests/test_email_canonical_current12.py tests/test_email_source_web_api.py` | 104 passed, 213.12s |
| `python -m pytest -q -n 3 tests/test_email_body_source.py tests/test_email_model_input_lifecycle.py -k 'not inverse and not recovery'` | 34 passed, 40.85s |
| Eleven exact `tests/test_email_store.py` nodes below | 11 passed, 29.12s |
| `python -m pytest -q -x tests/test_email_canonical_storage.py::test_exact_dual_source_profiles_roundtrip tests/test_email_canonical_storage.py::test_inverse_rejects_changed_source_metadata_without_schema_mutation tests/test_email_canonical_storage.py::test_inverse_refuses_unknown_target_dependency tests/test_email_body_source.py tests/test_email_model_input_lifecycle.py -k 'exact_dual_source or inverse or recovery'` | 18 passed, 34 deselected, 144.89s |

`ruff check` and Python compilation passed for all 29 changed/new Python files;
`git diff --check` passed. No full-suite result is claimed.

The initial implementer snapshot covered 29 changed source files, with 1,195-file
aggregate `e6041ee817c0be27ec5f30c6caba18d48bbbcb459ff20f3d21647e5e8c98a417`.
After the reviewed native-harness correction, the immutable pre-merge snapshot
[`2026-10-19-email-metadata-source-freeze.json`](superpowers/specs/2026-10-19-email-metadata-source-freeze.json)
covers 31 changed source files and aggregate
`125cc463541b7ca34bcfe7e2c70b5f6e1187b489377ef262ffbbe18f7921e210`;
its manifest file SHA-256 is
`5e6f5cc6385335fffe0a2a2fffac1b0193a0490f49a8c057c444541d4ae1a3ad`.
These are historical evidence boundaries, not the final merged app tree.

Root merged accepted A2 and upstream Consumer capability wording at `cb156951b`.
The only app/test/script differences from pre-merge `07738a55d` are
`app/consumer_agent.py` and `tests/test_consumer_agent.py`; B1 schema/runtime and
native helper remain byte-identical. The final merged source for parent acceptance
is recorded in
[`2026-10-19-email-metadata-merged-freeze.json`](superpowers/specs/2026-10-19-email-metadata-merged-freeze.json):
1,195 files, aggregate `9d4109905acea0be33e145056a14b6308a1d6a3c71291d0a1422dcbaf64a58be`.
The merged metadata-schema and native-harness tests passed 39/96.14s.
No source body or native trajectory is included in either manifest.

The eleven EmailStore nodes cover direct primary-key lookup, feedback, training
eligibility, pending-feedback persistence, both rescan locator paths,
move-then-important retry, locator refresh during a claim, atomic scan rollback,
counting pending training without text hydration, and terminal unsubscribe probes
without classification payload hydration. Their names are
`test_email_store_gets_one_classification_directly_by_primary_key`,
`test_feedback_moves_a_message_to_processed_and_records_user_source`,
`test_training_examples_exclude_pending_or_unconfirmed_user_rows_without_reopen`,
`test_pending_feedback_persists_message_and_cursor_without_plan_or_actions`,
`test_rescan_updates_locator_without_replacing_final_decision_or_plan`,
`test_rescan_only_updates_mutable_locator_and_preserves_business_snapshot`,
`test_move_then_important_persists_locator_and_retries_only_flag`,
`test_locator_refresh_does_not_invalidate_stable_claim_identity`,
`test_atomic_scan_persistence_rolls_back_message_plan_actions_and_cursor`,
`test_pending_training_count_preserves_eligibility_without_loading_texts`, and
`test_terminal_unsubscribe_probe_does_not_hydrate_classification_payloads`.

Current fixtures in preview, classification-reader, body/model retirement,
retirement-script and two EmailStore tests address `message_*` ownership. The
current-core inventory uses the existing `provenance` table. Original body,
model, authorization, eligibility, ordering and identity assertions remain.
The reader module itself needs no runtime rewrite because its public
classification projection now reads the message owner. The email API tests
exercise existing public shapes; no frontend implementation changed.

Historical mapping remains explicit. `tests/test_email_canonical_storage.py`
uses the independent `2026-10-17-email-canonical-current13-source.json`
declarations to test the historical native-to-Email52 conversion. Its runtime
tests intentionally permit unequal classification/message headers and folders;
their actual-code baseline is commit
`cd30f80f56768b90eda7a2b343fc9087b5589735` (Store19.2 / Email52), before B1. They
must run with that archived application and test code, not be relabelled as
Email53 tests. The current B1 runtime equivalents are
`test_runtime_class_and_message_writes_read_the_same_metadata_owner`,
`test_paired_locator_cas_writes_one_folder_and_keeps_two_clocks`,
`test_absent_classification_metadata_does_not_erase_message_owner`, and
`test_registered_release_inverse_uses_new_current_facts_and_exact_markers`.
Frozen-declaration and historical inverse tests remain explicit and unchanged.
`test_email_action_plan_direct_dao.py`'s archived Store19 / Email51 fixture is
outside B1's exact 19.2 / 52 source contract and was not run or bypassed here.

No full test suite, production-copy cycle or production service action was run
by this implementer. Root will append the distinct current-copy/native/deploy
evidence after independent review.

The native lifecycle harness initially failed on a fresh current database
because its Store/Email marker assertions imported historical Store14.1/Email52
release constants. The harness now compares against the selected
`STORE_SCHEMA_VERSION` and `EMAIL_SCHEMA_VERSION`; the existing Store and Email
constructors validate their selected declarations, while the test asserts the
returned markers match those current constants. RED: the focused fresh-current
test first failed at the historical Store marker assertion; replacing those
constants then exposed `final_action_schema_is_current()` rejecting the current
Actions declaration because it describes the previous declaration. That
historical-only assertion was removed; constructor initialization already
validates the selected declarations. GREEN: after the harness and regression
assertion changed, both tests in
`tests/test_native_copy_final_schema.py` passed; Ruff and `git diff --check`
passed. No application runtime code changed.

## Parent actual-copy and visible UI acceptance

Parent acceptance passed on frozen merged source `eadf628c8` (same app/tests/scripts
as `cb156951b`, 1,195-file aggregate9d410990...). The fresh live source was
Store19.1 while A2 waited for work to drain. Actual A2 code `25920bb11` first
published that consistent copy to19.2; B1 then published to19.3/Email53.
The source contained9,528 Emails. Every planned Email value/type mapping,
all unrelated table hashes, original FK multiset and allocator values passed.
The sole original Runs190042 FK finding remains; it is not reported as zero.

New current facts passed class→message and message→class metadata update,
paired-locator CAS with separate clocks, independent message/class IDs,
a message-only row, and the1,000-Unicode-character preview. Default local
search made zero original reads; explicit detail used the complete synthetic
original through a substitute. Actual browser interaction at copy port18767
searched the new subject, opened its detail and displayed the suffix beyond
the preview. No worker ran in this console and no outbound provider was called.

The inverse restored the exact complete source schema and all new facts.
Actual archived A2 `25920bb11` Store and EmailStore cold-opened the current
database and read the new metadata/message-only row. Re-publication preserved
every business-table hash and all source Email-ledger rows; only Store marker
time and newly recreated Email53 publication time changed. Full integrity_check
is ok, source SHA is unchanged and provider calls are0. Detailed body-free
evidence is `validation/2026-10-19-email-metadata-parent-copy.json`.

Two harness corrections are explicit: the new synthetic ID first used a
storage-valid negative value, which the positive-ID API correctly rejected;
UI acceptance then used a verified-unused positive JavaScript-safe ID. The
first re-publication comparison also incorrectly pinned the recreated Email53
applied_at timestamp. The corrected full inverse/reforward cycle was rerun;
no B1 application code changed for either harness issue.

The four duplicate header values sum to1,188,144 bytes. Preview content moves
to one owner and remains retained. Recorded page figures include two new
synthetic target rows, so they are not an identical-population savings claim;
the474,185,728-byte file was not VACUUMed. B1 is accepted locally but not yet
deployed. A2 separately reached production5a0cb051c, Store19.2/Email52, PID63376
with clean health, queues, Attention and History readback.
