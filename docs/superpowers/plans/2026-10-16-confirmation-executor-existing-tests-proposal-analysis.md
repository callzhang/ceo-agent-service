# Existing executor test retirement — unselected proposal

The full current executor test module now collects and passes against the real package, current models and WorkbenchStore with root Store/schema and executor proposals. Combined full executor + six retirement regressions + runtime + native-event test files passed **76 cases in 16.10 seconds**, using pinned Python 3.13. No stubs, fake enum, extracted module, skip or xfail was used in this proof.

Patch: `2026-10-16-confirmation-executor-existing-tests-proposal.patch` — 41,076 bytes, SHA-256 `20a0d49353d54180d5ad0387539dde33074fee3259f706a43acabb560d493710`. This is a standalone patch against the original source test module. It includes the earlier executor-only Tasks20 fixture changes, so it supersedes that half of the tiny fixture patch. Apply the tiny fixture patch only for `tests/test_workbench_native_events.py`, then this complete module patch.

Exactly 21 tests solely covering the canceled Workbench confirmation feature and four feature helpers are removed. All 18 non-feature test functions remain; 15 function bodies have identical AST. All 80 ordinary assertion statements remain. Every retained assertion is identical except the recovery test's retired waiting-status control becomes a live RUNNING control with its original unexpired10-second lease. The JSON lists every removed name and preserved function, with per-function assertion counts.

The ordinary transient-lock test now monkeypatches `recover_expired_turns`, retaining its retry/count assertions and using its integer result. The ordinary expired-turn test removes only confirmation creation, still proves expired work requeues and unexpired work stays running, and preserves task existence. The failed-runtime test retains every assertion and uses `task8_set_parts` for its existing empty error_detail fixture write; the module uses the current `task20_projection`.

Pinned compile, Ruff and source patch application pass. Reverse application reproduces the original complete test module byte for byte; reapplication succeeds. Source/spec/prerequisite hashes are in the JSON. The prior analysis's full executor-module collection limitation is resolved by this additional proposal and its combined76-case proof.

No application/test working-tree edit, staging, commit, production access, migration, provider/native CLI/network invocation, push or deployment occurred. The task-owned temporary directory was removed.
