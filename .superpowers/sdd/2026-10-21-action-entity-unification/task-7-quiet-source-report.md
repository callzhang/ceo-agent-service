# Task 7 deployment quiet source repair

## Finding

At base `bf07de8d6210709cd76887cc14ebb9ef23fa5600`,
`app/repository_updater.py::in_flight_work` counted Email actions using the
retired `record_type='email_actions'` / `email_status` fields. A current direct
Email claim is a common Action whose processing status is exposed by the
existing `app.email_publication_sources.publication_email_action_source`
selector. The deployment quiet reader therefore returned zero while a public
EmailStore claim was processing.

## Change

The existing Email contribution now counts `status='processing'` from that
publication selector, preserving its exact old/current schema selection. When
the optional `email_schema_migrations` ledger is absent, the Email contribution
is zero, matching the existing Store-only current-database contract. No other
in-flight counts, timeout, recovery, or deployment behavior changed.

The contract note was added to `docs/architecture.md` and
`docs/runtime-mechanism.md`.

## Evidence

RED command:

```text
pytest -q tests/test_repository_updater.py -k 'quiet_counts_processing_current_direct_email_action or quiet_store_only_database_has_no_email_action_contribution'
```

Result before the implementation: 1 failed, 1 passed. A real
`EmailStore.claim_direct_action` returned a processing claim, but
`in_flight_work` returned 0; the Store-only control returned 0.

GREEN command:

```text
pytest -q tests/test_repository_updater.py -k quiet
```

Result after the implementation: 19 passed, 39 deselected. This includes the
processing current direct Action; the selector returns one processing row and
`in_flight_work` now returns 1. A fresh Store-only database with no Email ledger
still returns zero Email work.

Static checks:

```text
ruff check app/repository_updater.py tests/test_repository_updater.py
git diff --check
```

Both passed.

An exploratory attempt to run the existing old-pair and current-source tests in
`tests/test_email_action_external_consumers.py` did not provide valid
verification on this checkout: the current-source fixture inserts a retired
Email record kind and fails its schema CHECK, while the old-pair fixture's
release setup rejects its source marker as unsupported. These failures occur
during fixture construction before the quiet reader is called; those unrelated
fixtures were not modified. The implementation delegates old/current source
selection to the existing publication selector without changing that contract.

## Release boundary

No service process, provider, production database, push, or deploy action was
performed by this worker. Parent owns independent review, push, and the already
running standard deployment/readback.
