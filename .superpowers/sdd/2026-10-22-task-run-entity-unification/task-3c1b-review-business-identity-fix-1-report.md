# C1B Fix1/5 report

## Change

`current_reviewed_candidate` now orders an approved candidate's Reviews by `run_adopted_reviews.id DESC`. This preserves the existing source Store's Review-ID ordering when Audit Runs are created in a different order from when their Review results are adopted. No other identity, selection, or review behavior changed.

The source evidence in the independent review points to original `d590dd4e` behavior: `candidate_reviews.id` is the Review business key and current-review selection ordered by that ID. The fix uses the already preserved native Review key; it does not add a new ordering field or policy.

## Regression evidence

Added `test_current_review_projection_follows_review_id_when_audit_adoption_is_reversed` using the actual unactivated native DDL and Store. It creates two Audit Runs in ascending ID order, adopts two approved reviews in reverse order, and checks that both returned fields identify the most recently adopted Review and its distinct Audit Run owner.

RED before the fix:

```text
pytest -q tests/test_native_reviewed_candidate_store.py -k current_review_projection_follows_review_id_when_audit_adoption_is_reversed
```

Output: `1 failed, 3 deselected`; the projection returned Review ID `1` instead of the newest Review ID `2`.

GREEN after the fix:

```text
pytest -q tests/test_native_reviewed_candidate_store.py
```

Output: `4 passed in 2.01s`.

```text
ruff check app/reviewed_candidate_store.py tests/test_native_reviewed_candidate_store.py
```

Output: `All checks passed!`.

`python -m compileall -q app/reviewed_candidate_store.py` and `git diff --check` passed. No full suite was run.
