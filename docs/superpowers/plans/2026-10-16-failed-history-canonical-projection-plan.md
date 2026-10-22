# Canonical failed History selection

The published-main merge QUALITY probe reproduces a pre-existing P1: failed-only pagination duplicates the canonical OA status rules and hardcodes the hydrated status. It omits six OA labels that now project to failed and includes uncertain or pending items. This is a read projection defect; stored task/Attempt/receipt facts and execution policies must not change.

The complete executable Store change is `2026-10-16-failed-history-canonical-projection.patch`; base SHA256 `96805c510ee131b284ccb05f2d8ecc048dd2f1d985c5ec7ac044d817cebe01a6`, applied candidate `7e7dd5beb8a1193cb5512bcf43fa1e377d37bb9c3f474e8a04d2928f91d6ca73`. It changes only two existing methods: reply-Attempt candidate keys come from the existing canonical projection filtered by status; the existing private hydration helper calls that same projection with only page IDs. Other sources keep their existing selection. No new status list, fallback, field, policy or schema is introduced.

Root disposable preflight passes: the ten canonical regression cases in `2026-10-16-failed-history-regression.py` produce nine expected assertion failures/one pass before the two-method transplant, then ten pass in2.81s after it. Initial probe fixtures omitted required source title/sender/text fields and failed source constraints; only fixtures were corrected before this RED/GREEN result. Candidate compilation and Ruff pass. All new fixture content is synthetic.

Implementation is mechanical:

1. Apply the exact patch to app/store.py; copy the complete regression proposal to tests/test_failed_history_projection.py.
2. In test_store_history_pagination.py's failed-page hydration test, retain the private helper's page-size tracker and track the base projection's supplied ID sizes separately. Expect each to receive exactly the page's two IDs, then one ID for the smaller page. Remove the obsolete assertion that canonical reply-Attempt key selection is never called; retain the assertions that the full multisource projection is not used and source projections are individually scoped. Do not weaken expected result/count/order or large-body/page-hydration checks.
3. Run those ten RED tests against the unmodified Store before applying; then run the focused History/Attempt/detail/Task Agent matrix plus the ten new tests. Use pinned Python3.13.12/SQLite3.50.4, PYTHONDONTWRITEBYTECODE=1, pytest -p no:cacheprovider and an owned TemporaryDirectory basetemp under /tmp. No full suite.
4. Measure representative query work separately from correctness. Verify status selection does not materialize display bodies for all rows and page hydration stays ID-scoped. Report any cost increase explicitly; do not restore duplicated status predicates for speed.
5. After tests, update scoped behavior/performance docs. Root obtains independent SPEC then fresh QUALITY before accepting the merge/fix. No real database writes, provider/native CLI, sends, main/push/deploy. Exact State proposal method-byte rebase follows only after this issue is closed.

The merge preservation review itself found the grouped terminal lookup sound: the source set and identity are unchanged and SQLite materializes one grouped relation with indexed lookups. Preserve it.
