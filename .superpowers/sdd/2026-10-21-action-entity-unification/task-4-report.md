# Task 4 implementation report

## Result and scope

Task 4 now reads current delivery identity and receipt facts through the shared Action owner. History operation-log discovery, History rows, queue Attention, direct-email queue snapshots, classification-attempt observability, Attempt detail, recall status, and feedback-token lookup no longer infer the current direct-email Action from its legacy source DTO. The email source label remains in the operation-log query as presentation/filter metadata. Specialized unsubscribe workflows remain on their specialized records. No audit, authorization, retry, reconciliation, or other policy behavior changed.

### Changed source files

- `app/action_storage.py`: add Action delivery-receipt facts, provider receipt identity/body reference, and provider result ID to the shared Sent Action query; Sent time is the current Action receipt's `observed_at`.
- `app/store.py`: extend the read model with nullable source facts; switch failed-operation candidate discovery and History's direct-email union to `DIRECT_EMAIL_ACTION_SQL`, using the common Action ID.
- `app/audit_web.py`: switch the direct-email queue snapshot and failed-email Attention projection to the current Action projection and Action ID.
- `app/email_store.py`: switch classification observability's current attempt query to `DIRECT_EMAIL_ATTEMPT_SQL`.
- `app/web_api/registration.py`: map current email History presentation/detail behavior by semantic `history_type`, not `source_table` DTO dispatch.
- `app/web_api/attempts.py`: expose a delivery presentation from the Action-owned body, exact Action receipt, provider receipt, external message ID, and same-Action recall state; resolve feedback from the Action token and original conversation/trigger identity. Missing operation/target remain null and render as “来源事实未提供”.
- `frontend/src/api/attempts.ts`, `frontend/src/pages/AttemptDetailPage.tsx`, and `frontend/src/pages/AttemptDetailPage.test.tsx`: type and render the Action receipt and recall facts, with explicit unavailable provenance.
- `tests/test_action_entity_views.py`: add the five requested reader/observer/recall/feedback/provenance cases.
- `docs/architecture.md` and `docs/runtime-mechanism.md`: record the Task 4 read behavior and its unchanged specialized-workflow/policy boundary.

## Reader conversion / retirement evidence

Five current runtime source-shaped direct-email reader queries were converted: the failed-operation key query, History operation-log union, queue snapshot, failed-email Attention selection, and classification-attempt observability. The two History API branches that selected detail URL/presentation based on `source_table == "email_actions"` now use the semantic History type. Sent detail/list readers already converge on `SENT_REPLY_SQL`; that projection now exposes Action ID, Action receipt ID and exact timestamp, provider receipt ID/body, and nullable source facts. Feedback-token and recall lookup therefore continue to use the same Action ID and its original conversation/trigger identity.

`EMAIL_ATTEMPT_SQL` remains exported only as the frozen source-shaped projection used by historical source-contract/migration tests; `rg -n 'EMAIL_ATTEMPT_SQL' app` finds its definition/comment only, while all current application readers use `DIRECT_EMAIL_ATTEMPT_SQL`. `email_publication_sources.py` retains its explicit old-schema source selection for migration/quiet-check boundaries. These are not runtime fallbacks for current Action reads. Current History still carries the stable `email_actions` source label for query filtering and legacy source explanation, but its identity and data come from the Action projection.

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
1 passed in 5.65s

python -m pytest -q tests/test_action_entity_callers.py tests/test_native_reply_reconciliation.py tests/test_shared_action_lifecycle.py tests/test_outbound_postfix.py
38 passed in 58.07s (final rerun after removing the unused legacy projection import)

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
```

The full prescribed Task 3 caller group and the unmodified mandatory node both pass with their original History, Attention, queue snapshot, and important-listing assertions intact. No full-suite run was made.

## Visible console readback

The source copy was not available to open before Task 5, so the isolated local console used a synthetic fixture created through the real Task 3 `AutoReplyStore.record_sent_reply` writer against a fresh target-schema SQLite file under `/tmp/ceo-action-entity-d0ce4814`. Uvicorn ran with `lifespan="off"`; it did not start workbench workers. A test-only `FakeDws` replaced the provider client and wrote its recall call to a task-owned fixture file; no external provider was called. A temporary Vite proxy served the actual frontend against this isolated API.

In Chrome at `http://127.0.0.1:5198/history`, the visible History row showed “Task 4 synthetic source conversation”, and opening it rendered `/attempts/1`. The detail page visibly showed:

- “Synthetic Action-owned message body” as the generated reply;
- Action `#2`, external message `fixture-provider-message-1`, and stored sent time `2026-10-22 00:05:42`;
- explicit unavailable operation/target provenance (“来源事实未提供” / “未提供”);
- provider receipt `#1` with its exact JSON body `{"message_id":"fixture-provider-message-1"}`;
- recall status `recalled` on the same delivery; and
- the original trigger and source-linked feedback text “Synthetic feedback linked to original trigger.”

The UI began at `未撤回`. I activated its “撤回发送” control once, confirmed the UI prompt, and the fake provider recorded one call with exactly `fixture-conversation-1` plus `fixture-provider-message-1`. The detail page then read back `recalled` on the same Action (`#2`) while preserving receipt ID `#1`, provider body `{"message_id":"fixture-provider-message-1"}`, and sent timestamp (`2026-10-22 00:09:44`). I also entered `synthetic Task 4 feedback entrypoint verification` in the visible internal-feedback form and submitted it to the isolated API; the UI returned “反馈已保存” and retained the text. The provider message, initial feedback event, and submitted internal feedback were synthetic fixture data only.

## Boundaries and self-review

No production database or checkout, workers, real providers, production writes, migration, push, or deploy were used. The UI evidence is synthetic target-schema evidence, not an authentic source-copy readback. The existing Task 3 receipt writer/reconciliation was not modified. Missing operation/target stays explicit and no timestamp, operation, target, result digest, or external message ID is synthesized beyond the existing source-aware receipt-ID extraction behavior. The temporary Vite config, fake-provider API server, Vite server, and fixture database/files are task-owned and are cleaned up before commit.

Self-review found no changes to the original mixed lifecycle assertions or to policy behavior. The temporary fixture DB/server directory and temporary Vite config were removed after visible readback. The committed file list matches the changed source files and tests listed above.
