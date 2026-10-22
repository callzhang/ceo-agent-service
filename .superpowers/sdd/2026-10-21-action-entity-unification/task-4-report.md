# Task 4 implementation report

## Result and scope

Task 4 now reads current delivery identity and receipt facts through the shared Action owner. History operation-log discovery, History rows, queue Attention, direct-email queue snapshots, classification-attempt observability, Attempt detail, recall status, and feedback-token lookup no longer infer the current direct-email Action from its legacy source DTO. Direct-email History rows now use the common Action source/ID while retaining the semantic `email_action` business type, status behavior, and detail route. Specialized unsubscribe workflows remain on their specialized records. No audit, authorization, retry, reconciliation, or other policy behavior changed.

### Changed source files

- `app/action_storage.py`: add Action delivery-receipt facts, provider receipt identity/body reference, and provider result ID to the shared Sent Action query; Sent time is the current Action receipt's `observed_at`.
- `app/store.py`: extend the read model with nullable source facts; switch failed-operation candidate discovery and History's direct-email union to `DIRECT_EMAIL_ACTION_SQL`, using `source_table='actions'`, `source_id=actions.id`, and `id='action:' || actions.id` rather than the old runtime `email_actions` / `email-action:<source-id>` aliases.
- `app/audit_web.py`: switch the direct-email queue snapshot and failed-email Attention projection to the current Action projection and Action ID.
- `app/email_store.py`: switch classification observability's current attempt query to `DIRECT_EMAIL_ATTEMPT_SQL`.
- `app/history_types.py`: point `HISTORY_SOURCE_TABLES` at the current owner `actions`; the visible history filter remains `history_type='email_action'`.
- `app/web_api/registration.py`: map current email History presentation/detail behavior by semantic `history_type`, not `source_table` DTO dispatch.
- `app/web_api/attempts.py`: expose a delivery presentation from the Action-owned body, exact Action receipt, provider receipt, external message ID, and same-Action recall state; resolve feedback from the Action token and original conversation/trigger identity. Missing operation/target remain null and render as “来源事实未提供”.
- `frontend/src/api/attempts.ts`, `frontend/src/pages/AttemptDetailPage.tsx`, and `frontend/src/pages/AttemptDetailPage.test.tsx`: type and render the Action receipt and recall facts, with explicit unavailable provenance.
- `tests/test_action_entity_views.py`: add the five requested reader/observer/recall/feedback/provenance cases and a real console History API case for common Action identity plus semantic email-action filtering/detail URL.
- `tests/test_shared_action_lifecycle.py`: retain the mixed History/Attention/queue/important assertions and assert common Action identity and offset paging.
- `docs/architecture.md` and `docs/runtime-mechanism.md`: record the Task 4 read behavior, current History source identity, semantic email-action filter, and unchanged specialized-workflow/policy boundary.

## Reader conversion / retirement evidence

Five current runtime source-shaped direct-email reader queries were converted: the failed-operation key query, History operation-log union, queue snapshot, failed-email Attention selection, and classification-attempt observability. The History union, failed-key branch selector, source filter, and pagination keys now use the common Action source and `actions.id`; the API maps `history_type='email_action'` to the same user-facing kind, filter and `/email` detail URL. This retires the old runtime aliases `source_table='email_actions'` and `id='email-action:<source-id>'`. Sent detail/list readers already converge on `SENT_REPLY_SQL`; that projection now exposes Action ID, Action receipt ID and exact timestamp, provider receipt ID/body, and nullable source facts. Feedback-token and recall lookup therefore continue to use the same Action ID and its original conversation/trigger identity.

### Task 1 source-consumer disposition matrix

The frozen field map has 202 repeated consumer references and 19 unique consumer anchors across the seven covered source families. The matrix below accounts for all 19 anchors: 16 source methods now reach the common owner, and three shared helpers are partial exits or remaining dependencies rather than wholly converted helpers. `Store.record_completed_agent_message_delivery` is shared by the Sent and observer source families and is listed once. This is a bounded cross-check of the Task 1 inventory, not a new repository-wide codec crawl.

| Status | Named source consumers |
| --- | --- |
| Converted source methods (16) | `email_actions`: `EmailStore._next_direct_action_row`, `_claimed_direct_action`, `complete_direct_action_attempt`; `email_action_attempts`: `EmailStore.append_action_attempt`, `list_action_attempts`; `candidate_action_attempts`: `ReviewedCandidateStoreMixin.list_candidate_action_attempts`, `record_candidate_action_outcome`; `external_action_results`: `ReviewedCandidateStoreMixin.record_candidate_external_action`, `get_verified_action_source`; `outbound_postfixes`: `Store.prepare_outbound_postfix`, `get_outbound_postfix`, `record_outbound_postfix_receipt`; `sent_replies`: `Store.record_completed_agent_message_delivery`, `get_sent_reply`, `list_sent_replies_after`; `sent_reply_observers`: `Store.list_sent_replies_for_attempts`. |
| Partial helper exits / remaining dependencies (3) | `shared_action_definitions.email_projection`: not converted; it still exposes the old public tuple for `email_actions`, and `email_publication_sources.publication_email_action_source` calls it for the current target quiet/source path (`app/email_publication_sources.py:42`). Current publication/quiet registration and old-layout index retirement remain Task 5/7 gates. `shared_action_lifecycle.transition`: covered direct/reviewed/prepared paths now use named Action-owner methods, but specialized `STEP_KINDS` origin transitions remain; this is a partial covered exit, not wholesale conversion. `action_storage._projection`: the generic source projection helper still supports old source namespaces; covered current Action reads bypass it through `SENT_REPLY_SQL`, `DIRECT_EMAIL_ACTION_SQL`, and `DIRECT_EMAIL_ATTEMPT_SQL`. |
| Inspected; no edit required | `app/history.py`: `HistoryItem` is a presentation contract and `safe_observability_error` is a formatting/redaction helper called by `web_api.attempts`, `audit_web`, and `native_cli_metadata`; it does not select or own Action identity. `app/history_actions.py`: `reply_history_attention` receives the already-loaded attempt/task/decision options from `web_api.attempts` and `audit_web`; meeting/task/follow-up helpers likewise consume passed projections. It contains no SQL/source selection, so editing it would be cosmetic or an unapproved policy change. |
| Out of scope / inverse-only | Specialized unsubscribe workflows retain their dedicated source records and policy. `email_publication_sources.publication_email_action_source` is also an explicit source/migration/quiet-check path, not a current History DTO fallback. `EMAIL_ATTEMPT_SQL` remains only as a frozen source-shaped projection for historical source-contract/migration tests; it is not a current runtime History or Attempt reader. |

Task 3/4 tests and reviews cover the 16 current source methods listed above. The three partial helpers have explicit remaining consumers/boundaries as stated; they are not counted as converted. These dispositions do not change schema or behavior in this fix round.

### Review fix round 2 (documentation-only)

The prior matrix overstated the status of three shared helpers. The corrected classification follows the source anchors: `shared_action_definitions.email_projection` at line 82 still projects the legacy public Email tuple and is called by the current-target quiet/source path at `email_publication_sources.py:42`; `shared_action_lifecycle.transition` at line 818 retains specialized `STEP_KINDS` origin branches after covered direct/reviewed/prepared paths moved to named owners; and `action_storage._projection` at line 838 remains a generic old-source projection helper that the covered current Action queries bypass. Current publication/quiet registration and old-layout index retirement remain Task 5/7 gates. The 16 methods reached by current Action owner paths remain covered by Task 3/4 tests/reviews. No runtime or test files changed and no tests were rerun in this round.

These are the field-map methods and the finite current History call path reviewed for this fix. The `history.py`/`history_actions.py` absence from the runtime diff is intentional and now explicitly distinguished in the brief and plan as inspection-only.

`EMAIL_ATTEMPT_SQL` remains exported only as the frozen source-shaped projection used by historical source-contract/migration tests; `rg -n 'EMAIL_ATTEMPT_SQL' app` finds its definition/comment only, while covered current Attempt readers use `DIRECT_EMAIL_ATTEMPT_SQL`. `email_publication_sources.py` retains its explicit old-schema source selection and current-target quiet projection through the legacy public tuple; those publication contracts remain pending Task 5/7. These are not fallbacks for current History/Action reads. Current History no longer emits the legacy `email_actions` storage label or `email-action:<source-id>` row identity.

## Test-driven evidence

The first focused run was intentionally red:

```text
python -m pytest -q tests/test_action_entity_views.py
3 failed, 2 passed
```

Two detail assertions failed because the API did not yet expose an Action delivery record. The feedback-token test fixture also lacked its original ReplyAttempt, so that case initially failed to resolve the source message; the fixture now records the original attempt before creating the Action/token. The original Task 3 mixed-history failure recorded in `task-3-report.md` was `list_operation_logs_with_count` returning 0 instead of the expected 1 failed email-action log; the converted query now passes the complete preserved mixed-lifecycle case.

Passing focused commands and captured output:

```text
python -m pytest -q tests/test_action_entity_views.py tests/test_history_actions.py
20 passed in 14.04s

python -m pytest -q tests/test_shared_action_lifecycle.py::test_direct_email_owner_and_important_listing_read_current_attempt_fields
1 passed (included in the post-fix full caller group below)

python -m pytest -q tests/test_action_entity_callers.py tests/test_native_reply_reconciliation.py tests/test_shared_action_lifecycle.py tests/test_outbound_postfix.py
38 passed in 253.16s (0:04:13), rerun after the common Action History identity correction

python -m pytest -q tests/test_action_entity_views.py tests/test_history_actions.py tests/test_history_types.py::test_every_history_type_the_query_writes_for_a_visible_source_is_listed
22 passed in 25.41s

cd frontend && npm run test -- --run src/pages/AttemptDetailPage.test.tsx
Test Files  1 passed (1)
Tests       45 passed (45)

cd frontend && npm run build
tsc --noEmit passed; Vite transformed 2699 modules and completed the production build.

git diff --check
passed

python -m ruff check app/action_storage.py app/store.py app/audit_web.py app/email_store.py app/web_api/attempts.py app/web_api/registration.py tests/test_action_entity_views.py
All checks passed!

python -m compileall -q app/action_storage.py app/store.py app/audit_web.py app/email_store.py app/web_api/attempts.py app/web_api/registration.py tests/test_action_entity_views.py
passed

python -m ruff check app/store.py app/history_types.py tests/test_action_entity_views.py
All checks passed!

python -m compileall -q app/store.py app/history_types.py tests/test_shared_action_lifecycle.py tests/test_action_entity_views.py
passed

git diff --check
passed
```

The post-fix prescribed Task 3 caller group and its mandatory mixed History/Attention/queue/important node pass with all original assertions intact plus common Action identity, semantic `email_action`, and second-page assertions. No full-suite run was made.

Review-fix RED was recorded before the source-key change:

```text
python -m pytest -q tests/test_shared_action_lifecycle.py::test_direct_email_owner_and_important_listing_read_current_attempt_fields
1 failed in 8.98s: expected total 1, observed total 0 for source_tables=('actions',)
```

After the fix, the same node passes inside the post-fix 38-case prescribed caller group. The new API case separately passes within the 22-case focused run and checks the public `/api/console/history` item identity/type/status/detail route using an Action created by the current synthetic EmailStore writer.

The exploratory command `python -m pytest -q tests/test_shared_action_lifecycle.py::test_direct_email_owner_and_important_listing_read_current_attempt_fields tests/test_history_types.py::test_every_history_type_the_query_writes_for_a_visible_source_is_listed tests/test_history_types.py::test_history_without_email_tables_still_answers` returned `2 passed, 1 failed in 16.80s`. The failure occurred in `test_history_without_email_tables_still_answers`' old-schema setup helper before History assertions: `sqlite3.OperationalError: no such table: email_classifications` after current `EmailStore` initialization. This records the observed fixture failure only; no frozen baseline comparison is claimed, and that unrelated fixture was not broadened here.

Two exploratory current-external-consumer tests were attempted with `python -m pytest -q tests/test_email_action_external_consumers.py::test_history_uses_common_action_identity_and_keeps_action_pagination tests/test_email_action_external_consumers.py::test_store_only_does_not_advertise_email_history_or_queue`; output was `2 failed in 5.25s`. Both failed before their History assertions because this fixture module creates the preceding Email schema (`_kind` constraint rejects `email_action_plans`; the store-only case rejects the unknown/mixed State publication marker). No frozen baseline comparison is claimed. An attempted test-only update was reverted; these historical fixtures are not part of this fix's acceptance evidence.

A wider Ruff invocation that included the whole modified `tests/test_shared_action_lifecycle.py` reported F401 for its unmodified local import `EmailActionAttemptConflict` at line 89. The scoped Ruff command above covers the changed implementation and new API regression and passes. The unrelated import was not edited.

## Visible console readback

The source copy was not available to open before Task 5, so the isolated local console used synthetic fixtures against fresh target-schema SQLite under `/tmp/ceo-action-entity-d0ce4814`; the visible detail was written through the real Task 3 `AutoReplyStore.record_sent_reply` writer. Uvicorn ran with `lifespan="off"`; it did not start workbench workers. A test-only `FakeDws` replaced the provider client and wrote its recall call to a task-owned fixture file; no external provider was called. A temporary Vite proxy served the actual frontend against this isolated API. That console proof covers a Sent Action's detail/recall/feedback workflow; direct-email History identity/type mapping is separately covered by the focused API regression and mixed reader node. Authentic migrated-copy UI evidence remains a Task 6 gate.

There were two distinct fixture runs, so their sent timestamps are different data points. At `00:05:42`, the first UI read only displayed a preseeded `recalled` state. No recall control was clicked and no provider call or feedback submission occurred in that run; it is not the click-workflow acceptance evidence.

In Chrome at `http://127.0.0.1:5198/history`, the visible History row showed “Task 4 synthetic source conversation”, and opening it rendered `/attempts/1`. The detail page visibly showed:

- “Synthetic Action-owned message body” as the generated reply;
- Action `#2`, external message `fixture-provider-message-1`, and stored sent time `2026-10-22 00:09:44`;
- explicit unavailable operation/target provenance (“来源事实未提供” / “未提供”);
- provider receipt `#1` with its exact JSON body `{"message_id":"fixture-provider-message-1"}`;
- initial recall status `未撤回` before the click; and
- the original trigger and source-linked feedback text “Synthetic feedback linked to original trigger.”

The second UI run at `00:09:44` began at `未撤回`. I activated its “撤回发送” control once and confirmed the prompt. The fake provider call count was exactly one, with arguments exactly `fixture-conversation-1` and `fixture-provider-message-1`. Readback showed `recalled` on the same Action (`#2`) while its original provider receipt `#1`, body `{"message_id":"fixture-provider-message-1"}`, and sent timestamp `2026-10-22 00:09:44` remained unchanged. I entered `synthetic Task 4 feedback entrypoint verification` in the visible internal-feedback form and submitted it to the isolated API; the UI returned “反馈已保存” and retained the saved text. The provider message, initial feedback event, and submitted internal feedback were synthetic fixture data only. This second run supersedes the first display-only observation for recall and feedback entrypoint proof.

## Boundaries and self-review

No production database or checkout, workers, real providers, production writes, migration, push, or deploy were used. The UI evidence is synthetic target-schema evidence, not an authentic source-copy readback. The existing Task 3 receipt writer/reconciliation was not modified. Missing operation/target stays explicit and no timestamp, operation, target, result digest, or external message ID is synthesized beyond the existing source-aware receipt-ID extraction behavior. The temporary Vite config, fake-provider API server, Vite server, and fixture database/files are task-owned and were cleaned up before commit. Authentic migrated-copy History/detail readback remains explicitly deferred to Task 6.

Self-review found no changes to the original mixed lifecycle assertions or to policy behavior. The temporary fixture DB/server directory and temporary Vite config were removed after visible readback. The committed file list matches the changed source files and tests listed above.
