# Weekly scanner fixture projection validation, 2026-10-18

This test-only correction updates the existing scanner provenance test to query the current Tasks projection. The prior `WORK_SUMMARY_INPUT_SQL` came from the historical storage projection and referenced `t.source_type`, which does not exist on the current physical `tasks` table. The correct current helper is `task20_projection("work_summary_inputs")` from `app/task20_definitions.py`; `task12_projection` was an invalid intermediate choice because it accepts only the twelve new kinds, while this kind is validated by the full twenty-kind projection.

The original exact-node RED was run with:

```sh
pytest -q tests/test_task_report_scanner.py::test_scan_task_reports_is_idempotent_and_preserves_report_provenance --basetemp "$tmpdir/pytest" -o "cache_dir=$tmpdir/cache"
```

It failed after 42.38s at the old query with `sqlite3.OperationalError: no such column: t.source_type`. The first candidate replacement using `task12_projection("work_summary_inputs")` was also tried on that exact node and failed after 34.19s with `ValueError: unsupported native Tasks kind or source field`; this was a helper-scope mismatch, not a scanner failure.

After changing the test to `task20_projection("work_summary_inputs")`, the same exact node passed: **1 passed in 42.69s**. All existing scanner idempotency, page-size, source-kind, source-ref, title, full original pending payload, and business-state assertions remain. `ruff check tests/test_task_report_scanner.py` and `git diff --check` passed. No broader suite was run and no broader suite result is claimed.

The GREEN run used an owned `mktemp -d /tmp/weekly-scanner-fixture-green.XXXXXX` root with dedicated pytest basetemp and cache directories; that root was removed after the run. The earlier RED/intermediate candidate temp root was also removed. No provider, native CLI, source or production database, runtime code, commit, stage, push, or deploy was involved.

SHA-256 at validation: `tests/test_task_report_scanner.py` `c9afddd399cc5dc428666ccbe7ddc7589cc05143e3a0b0b73337193032d1ed36`; `docs/agent-claims.md` `92062451caa0d8a07fbe3f1322201eaf21a590b51411e9415f82cefdd2a197b9`.
