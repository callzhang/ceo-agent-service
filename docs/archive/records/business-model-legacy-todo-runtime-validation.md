# D1b legacy provider Todo runtime retirement — 2026-10-20

D1b removes the old provider Todo execution code and dispatcher adapter queue. It does not complete unit D,
change the physical schema, move or delete facts, promote historical Todos to
current Tasks, or cancel, complete or resend an external Todo. Base commit:
`4ebf69c6d460f97345f3e7321526b39e681b0b15`.

Derek's confirmed disposition is that the 540 recorded-active old Todo links
without a verifiable current Task identity remain historical. Original identities,
source/business facts, provider IDs, saved status and execution receipts remain
readable. A shared public integer ID is not a Task identity mapping. The count is
an upstream decision/preflight fact; this implementer did not read production.
Root owns the fresh copy, current-data inverse, actual predecessor cold-open,
reforward, independent SPEC then QUALITY review and formal release checks.

## Exact source boundary

- CLI retires only the old Todo consumer and adapter/consumer registration.
  Status retires only the old adapter's queue projection in
  `_dispatcher_queue_snapshots`. The `DingTalk Todos` aggregate in
  `_queue_status_snapshots` and old `_queue_retryable_count` branch remain for D1c.
- `TaskTodoSyncOutboxQueueAdapter` and its private dispatcher projection are
  removed. The BusinessTask adapter and all its claim, lease, retry, unknown,
  receipt and terminal semantics remain unchanged.
- `maybe_create_dingtalk_todo` requires `business_task_id`; its existing business
  branch is lifted intact. The `work_todo_id` branch and its exclusive creation,
  refresh/retry, completion, dispatch and context helpers are removed.
- `todo_completion` retains `complete_business_task_from_external_todo` unchanged;
  the old completion/follow-up helpers are removed.
- Store removes the old link create/update plus six old outbox enqueue/claim/
  finish/retry/reconcile/receipt-settlement writers. All old link/outbox readers,
  typed facts/projections/declarations, History readers and semantic-import
  WorkProject/WorkTodo writes remain.
- `scripts/reclassify_unmirrored_todo_outbox.py` and its exclusive test are removed
  after a bounded caller search found no other production/script callers.
- `quality_gate.py` is byte-identical to base. Its two legacy operational sources
  must retire with the old Status aggregate and retryable branch in separately
  approved D1c; D1b changes only historical test fixture
  seeding, preserving every existing quality assertion. D1b alone is not full
  removal of legacy operational health checks.

No provider/native command or production database was accessed. All effect tests
use provider fakes and task-owned test databases. No staging, commit, push or
deployment is performed by this implementer.

## Regression and preservation evidence

The actual dispatcher factory, actual Status dispatcher adapter queue projection and retired public
API surface first produced three expected assertion failures; the same-public-ID
historical readback case already passed. After code retirement all four pass.
The collision case checks that scan/reconciliation call no provider, leave the
current unrelated Task open and preserve complete Actions/Business row tuples,
old link/outbox evidence and receipt fields, and the original legacy-project URL.
Existing mixed legacy/current completion tests also retain their assertions.

- RED: 3 failed, 1 passed, 11.50 s.
- Retirement GREEN: 4 passed, 9.19 s.
- Initial core: 40 passed, 1 inherited test-read failure, 116.66 s.
- Completion plus full consumer-dispatcher regression after that read fix:
  73 passed, 152.65 s.
- Changed historical-reader fixtures, CLI dry-run and unchanged quality policy:
  13 passed, 91.00 s.

These batches overlap and must not be added as independent test counts. Current
business creation/completion/unknown receipts, current dispatcher ownership and
retry bounds, memory and ordinary claim assertions remain. Only old-feature tests
and old cases in shared parametrizations retire with their implementation.

The inherited failure is exactly
`tests/test_todo_completion.py::test_external_dingtalk_completion_closes_only_the_linked_business_task`.
Its current completion, sibling-open and event assertions passed before the
readback hit `sqlite3.OperationalError: no such table: business_task_signals`.
The query was updated from:

```sql
select s.source_ref from business_task_signals s
join business_task_evidence e on e.signal_id=s.id
where e.task_id=? and e.evidence_role='completion'
```

to the existing current projections:

```python
f"select s.source_ref from ({signal_projection()}) s "
f"join ({business_action_projection('business_task_evidence')}) e on e.signal_id=s.id "
"where e.task_id=? and e.evidence_role='completion'"
```

The `(first,)` parameters and all 5 original assertion ASTs are unchanged.
No runtime fallback, skip, relaxed assertion or alternative storage variant was
introduced. The retained-assertion SHA256 is `7e74692a2071ddda1ad0c787fe7b0ec35ac8b9d07f8519ab9bfd3537fd864503`.

`verify_retained_ast.py` compares the source against the exact base: 762 retained
Store methods, 37 retained adapter functions/classes, the current completion
function, and 18 retained Todo-sync functions are AST-identical. The entire
current business-create branch also matches statement for statement. CLI has
134 unchanged top-level definitions and Audit Web has 425; their only changed
functions are the named registry and queue snapshot. Current declarations,
`action_storage`, `storage_payload`, the current schema selector, task models,
`web_api/tasks`, semantic import and `quality_gate` are byte-identical.
`verify_test_retention.py` enumerates removed/changed/unchanged tests and proves
retained history and quality assertions unchanged, including the renamed
history-only Store read test.

## Reproduction and freeze boundary

Task artifacts remain at `/tmp/ceo-d1b-todo.iC2uXm` for root review; do not clean
that directory while review or copy verification is in progress. The original
artifacts are preserved unchanged at the root and in `v1/`. `v2/manifest.json`
records every changed path/status/base/new SHA256, exact prior commands and log hashes,
verification script hashes and the complete patch SHA256. Untracked new source
and documentation are included in the patch. The own claim row is removed before
freeze. No source change is allowed during root's subsequent review without a new
freeze.

Independent SPEC identified a P2 documentation overstatement in v1: the old
Status aggregate and retryable branch had not retired with the dispatcher adapter.
V2 corrects only README, architecture, runtime, this validation document and the
plan to identify that remaining D1c work. Application code, tests and script path
states are unchanged from v1. No pytest or runtime checks were rerun for this
documentation correction; the recorded results below remain the v1 evidence.
`v2/docs-only-verification.json` records the exact five-document delta and verifies
all other candidate and protected path hashes against v1.

Commands used `/Users/derek/miniforge3/bin/python`:

```text
-m pytest -q tests/test_legacy_todo_retirement.py tests/test_cli.py::test_agent_cron_dispatcher_owns_all_migrated_consumer_queues tests/test_audit_web.py::test_status_lists_the_outbox_and_memory_write_queues
-m pytest -q tests/test_todo_sync.py tests/test_todo_completion.py tests/test_sync_outbox_dispatcher_actions.py tests/test_dispatcher_idle_claims.py
-m pytest -q tests/test_todo_completion.py tests/test_consumer_dispatcher.py
-m pytest -q <the exact 13 nodes in adjacent-nodes.json>
/tmp/ceo-d1b-todo.iC2uXm/verify_retained_ast.py
/tmp/ceo-d1b-todo.iC2uXm/verify_test_retention.py
```

Raw pytest log SHA256:

- `red.log`: `08359f48c9208b3927d42d8dcaa0e19a1cb737553837d0363084d59546a104a2`
- `green-retirement.log`: `959715228649715bc182d679c358a4c5364b3c7dc9899546a3900728ba7f37ed`
- `green-core-initial.log`: `c25baabc163b570d95796c48ed8c3f68699dcc2130ae7210d8c31bd2741ee6b3`
- `green-consumer-completion.log`: `833503b3ac831258e53dc4ab7b04b66ee4aa1f10b09607a6922a4f030a67b4e2`
- `green-adjacent.log`: `77adb6b7ca2ba4ad03e3ef7fe4a6d2d113f1348f0171f34da0f332b02c1b5836`
