# Task3C1C historical adoption reader report

## Contract and source evidence

The bounded contract is `task-3c1c-historical-adoption-reader-brief.md`. The approved behavior is source parity for `adopted_candidate_for_consumer_run` and `get_review_candidate`: return the stored adopted row after Task generation advancement, source/trajectory unavailability, or candidate invalidation. Current projection and mutation eligibility remain generation-bound and invalidation-aware.

At base `ac9284d949694fbaf6eb98dbde57145eda94601a`, the source implementation at `d590dd4e:app/reviewed_candidate_store.py` lines 176-190 selected directly from the candidate projection in both getters and returned the stored row or `None`; neither getter used current-generation validation. In the native implementation, both getters called `_candidate_context`, which raised `candidate generation mismatch` when the Task advanced. The separate `reviewed_candidate_for_audit_run` keeps Review business/protocol `id` distinct from its Audit Run owner.

## Change

`_adopted_candidate_row` now performs the shared neutral native row lookup. The two historical getters return that stored row directly. `_candidate_context` calls the same lookup and retains its existing missing-row and current-generation validation for mutations. `current_reviewed_candidate` continues its existing current generation, approval, latest-run, and invalidation filters. No schema, Store constructor, execution writer, receipt writer, source-copy converter, or audit policy changed.

The focused regression creates a real native Consumer adoption and approved Review, advances Task generation, then verifies both getters preserve the stored JSON, digest, and original generation. It checks unknown IDs return `None`, confirms the distinct Review/Audit identities, and verifies current projection and option selection still reject stale actionability. Existing coverage in the same file verifies invalidated candidate facts remain readable after supplement invalidation and that historical reads work when the native trajectory source file is unavailable.

## RED/GREEN evidence

RED, before implementation:

```text
pytest -q tests/test_native_reviewed_candidate_store.py::test_native_store_rejects_stale_generation_and_rolls_back_child_rowids
F
ValueError: candidate generation mismatch
1 failed in 0.91s
```

GREEN, after implementation (unique child `--basetemp` under `/tmp`):

```text
pytest -q --basetemp "$basetmp" tests/test_native_reviewed_candidate_store.py
....                                                                     [100%]
4 passed in 1.66s
```

## Scope and remaining gates

Updated the Task3C1C behavior paragraphs in `docs/architecture.md` and `docs/runtime-mechanism.md`. No provider, production, main branch, push, deploy, constructor activation, or broader runtime cutover occurred. The native Store remains unactivated; full current-fact inverse, constructor activation, and runtime cutover remain outside this bounded step. Concurrent cleanup-only edits to the Task3C2A report were preserved and not staged.
