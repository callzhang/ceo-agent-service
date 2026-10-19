# Bounded Task12 Email public-method evidence

The exact unselected Email caller proposal matches source at **79 of 79
checkpoints across 16 real public methods**: twelve classification-adapter
methods and four historical queue/traversal methods. No candidate divergence was
found in this bounded synthetic fresh run. This does not establish cold target
construction, nonempty migration, the frozen actual profile or complete caller
selection.

The [preflight](2026-10-16-task12-email-public-preflight.py) and
[metadata receipt](2026-10-16-task12-email-public-evidence.json) pin the proposal,
source modules, Task12 storage and Tasks20 declarations. The script asserts
unique checkpoint labels, identical source/target public method sets, exactly
79 checkpoints per worker, and expected success/error outcomes. Matching errors
cannot stand in for successful flows. The complete source/target comparison
digests match; the receipt retains concise decisive outputs and identity facts.

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python -B docs/superpowers/plans/2026-10-16-task12-email-public-preflight.py
```

The original source bootstrap invokes ordinary `AutoReplyStore` and `EmailStore`
constructors on a nonexistent owned database and asserts Tasks plus both native
Email queues are empty. The full source has 73 sqlite_schema table entries,
including SQLite/support entries, and no FK findings. This is not an ordinary
production table count. SQLite backup clones the empty source template. In the
target clone only, an FK-off caller transaction removes the two empty native
queues, replaces empty Tasks with `create_task20_schema`, and calls the accepted
`initialize_task8_sequence(..., 0)`. All other native twelve-kind/auxiliary
tables stay unchanged. This is explicit fixture preparation, not migration.

Only the exact two-module Email patch is applied to the owned target code;
**no companion projection or Store caller patch is applied**. Target uses
`EmailStore.__new__`, setting the actual constructor's `path` and
`training_run_id` attributes after preparation. Source uses its normal
constructor. The actual Email `_open_connection` and `_connect` implementations
remain in use, including FK setup, commit/rollback and closure; a wrapper adds
the exact scalar codec on target connections. Both processes use the same SQL
`current_timestamp` UDF, seconds-format Email `_now`, and aware datetime adapter
callback. No output clocks, public identities or source ROWIDs are normalized.

| Public paths | Decisive paired observations |
| --- | --- |
| Stable classification input | `from_message` builds synthetic inputs; ensure/replay, changed consumer context, immutable-input rejection, stable-record and provider UID reads, missing-task handling all match. |
| Claims, retry and completion | Blank owner, owner/generation CAS, delayed retry, failed exclusion from protected UIDs, explicit reviewed retry rowcount, done-only compaction, compact-input replay, stale completion and expiry-boundary recovery match. Non-done failure retains full input; done removes the message while preserving identity/hash references. |
| Offline rows | INSERT OR REPLACE changes the native source ROWID on replacement, retains public identity/owner behavior, guarded close leaves real done tasks untouched, bulk purge returns 2, repeated close/purge returns the original no-op results. |
| Nonempty bulk failure | A fixture-only native trigger observes both failed rows during `retry_failed_tasks` and aborts on the second. Both sides raise exact `IntegrityError: fixture bulk retry abort`, emit BEGIN IMMEDIATE/ROLLBACK and leave every committed logical fact unchanged. Removing the trigger gives native rowcount 2 for the successful retry. No helper or public DAO is replaced. |
| Historical enqueue and UPSERT | First page, empty page, monotone traversal cursor and UIDVALIDITY reset match. Reenqueue changes exactly normalized_text, provider_message_json, uidvalidity, uid and updated_at; source ROWID, deferred state/reason, attempted_at and next_retry_at survive. |
| Historical ordering/state/errors | UID ordering, deferred quota and due-time boundary, state reset, invalid state/limit and missing-row handling match. The missing-row state update deliberately raises after BEGIN/COMMIT. A page with one valid candidate followed by a wrong-folder candidate raises its original ValueError and rolls back both the first insert and traversal cursor. |

Every checkpoint compares actual public dataclass/collection/None results or
exact exception type/message; actual BEGIN/BEGIN IMMEDIATE/COMMIT/ROLLBACK;
source-shaped native fields with source ROWIDs, values and scalar types; exact
selected-State traversal facts; and FK findings. Target global physical IDs are
recorded separately from the preserved per-kind source ROWIDs. For example,
classification source ROWIDs 3/4 map to physical IDs 6/7, while historical source
ROWID 1 maps to physical ID 8. These distinct coordinates are asserted rather
than normalized into equality. Final FK findings are empty on both sides.

A separate negative control removes only the copied target done-branch compact
input assignment. It calls the real ensure/claim/complete/get methods. Source
completion stores `input_compacted=true` with no message (531 input bytes); the
mutated target stores the full message without compaction (687 bytes). The
script requires this semantic difference, proving the control detects loss of
done-only compaction even when both completions return successfully.

The first development run exposed a harness argument-name collision: the call
wrapper's receiver parameter was named owner, conflicting with the public
method's owner keyword. It was renamed receiver before the final paired run.
That run exited unsuccessfully with visible fixture-abort records; it is not
counted as evidence of caller equivalence.

Unproved scope includes actual/nonempty source migration and current-fact
inverse, all twenty-kind integration, cold constructor/schema validation,
`email_processing_progress` and other readers of old reply projections,
concurrent workers, every constraint/SQLite error boundary, broader Email action
or service behavior, provider/model/native CLI/browser execution, final eleven
tables and publication/deploy. No external actor API runs. No app/tests, real
database, runtime selector or service changes. Owned temporary source/database
copies are removed; independent root-owned review and rerun remain required.
