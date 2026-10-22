# Task 6B current Dashboard reader fix

## Result

The two current Action reader regressions are fixed on the Task 6B target. Dashboard Email queue rows now read `DIRECT_EMAIL_ACTION_SQL` joined to the current Email classification projection. The Email Attention producer now serializes the physical Action ID as a string, matching `AttentionRow.id`. Queue filtering still uses the worker's existing retry predicate; IDs, statuses, titles, and empty detail URLs retain the current API contract.

## Regression evidence

The new focused tests create current direct-email Actions through `EmailStore.persist_scan_result` and record synthetic failed receipts through the existing `record_direct_email_action_outcome` lifecycle helper. No provider is called.

- Before the Dashboard change, the real `/api/console/dashboard/queues` TestClient request reached the current-target query and failed at `_append_auxiliary_queues` with `sqlite3.OperationalError: no such column: a.email_action_type`.
- Against the accepted final backup in read-only mode, compiling the exact retired `email_action_projection("email_actions")` produces the same `no such column: a.email_action_type` error.
- The Attention control uses the exact failed Action rows from that backup. The prior numeric output shape fails `AttentionRow.model_validate` with Pydantic `string_type` at `id`; the fixed `/api/console/status` response validates and returns IDs `134250` and `134221` as strings.
- On the populated current synthetic target, the queue endpoint returns the expected two rows: `email_actions:2` / `pending` / `archive` / empty `detail_url`, and `email_actions:4` / `failed` / `flag_important` / empty `detail_url`. The Email status snapshot remains `pending=1`, non-retryable `failed=1`, `retryable=1` for that fixture.

## Validation

Python environment: `/Users/derek/miniforge3/bin/pytest`, pytest 9.0.2, Python 3.12.11, SQLite 3.53.4.

- Regression-first current-target run of the two new nodes: `pytest -q tests/test_email_action_external_consumers.py::test_dashboard_queue_reads_current_actions_and_preserves_email_contract tests/test_email_action_external_consumers.py::test_email_attention_id_matches_strict_worker_status_contract` — before fix, the queue node reproduced the retired-column failure; after fix, 2 passed.
- Focused adjacent readers: `pytest -q tests/test_email_action_external_consumers.py::test_dashboard_queue_reads_current_actions_and_preserves_email_contract tests/test_email_action_external_consumers.py::test_email_attention_id_matches_strict_worker_status_contract tests/test_email_action_external_consumers.py::test_store_only_does_not_advertise_email_history_or_queue tests/test_console_dashboard.py -k 'not active_direct_email_retry_counts_completed_attempts' tests/test_console_status_response.py` — 24 passed, 1 deselected.
- The broader three-file run reported 7 failures and 3 errors. I ran those same ten existing nodes in a clean detached `b7867c829` worktree with the same Python/SQLite environment; all ten reproduce at BASE. The seven are the legacy Email writer/source CHECK failures (`test_history_keeps_native_signed_rowid_and_nullable_public_id`, `test_attention_and_status_keep_nullable_failed_actions`, `test_maintenance_reads_current_email_action_source`, `test_complete_status_and_maintenance_paths_accept_current_target`), the unversioned Store-only publication marker (`test_generic_publication_readers_accept_store_only_target`), the retired `record_type='email_actions'` test lookup (`test_current_plan_poll_and_retry_window_on_target`), and the retired Email transition origin (`test_active_direct_email_retry_counts_completed_attempts`). Three `converted_pair` setup errors stop at `prepare_database_schema_release` with `unsupported current Action source schema; explicit paired release required`. None is changed by this fix.
- `ruff check app/web_api/dashboard.py app/audit_web.py tests/test_email_action_external_consumers.py` and `git diff --check` — passed.

## Accepted-copy API check

Using the Task 6B final persisted-state copy at `/tmp/action-entity-task6b.vESxM3/accepted-final.sqlite3` (SHA-256 `7732ba9cac36047b19d497aeb5ad1959995e9f2d034119ccc9983920f21abdc5`), I called the registered queue and status routes with FastAPI TestClient. The copy was opened with SQLite `mode=ro` and `query_only=on`; the lightweight store wrapper starts no worker, and no provider client is instantiated or called.

- `GET /api/console/dashboard/queues?state=queued&page_size=100` — HTTP 200, 51 Email rows; common physical IDs are emitted with the existing `email_actions:` API prefix, and each Email row has an empty `detail_url` as before. The response included both pending and failed Email rows.
- `GET /api/console/status` — HTTP 200; Email Attention rows were `134250` and `134221`, both string IDs with `failed` status.
- The backup hash after both requests is unchanged.

This verifies the actual registered API responses against the accepted copy. It is not a visible browser Dashboard verification; the parent will do that after independent review. No native CLI, live service, provider, push, or deploy action occurred.
