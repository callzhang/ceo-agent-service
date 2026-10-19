# Bounded Task8 Workbench public-method evidence

The exact unselected Store and Workbench caller proposals produce equal results
at **81 of 81 checkpoints across 26 real public Workbench methods** on the
synthetic fresh fixture. No candidate divergence was found with the required companion projection installed.
This is public-method execution evidence, not cold constructor, migration,
actual-source-profile, complete caller conversion or runtime acceptance.

The executable [preflight](2026-10-16-task8-workbench-public-preflight.py) and
[metadata receipt](2026-10-16-task8-workbench-public-evidence.json) pin both
proposal patches, the source module hashes, target definitions and scalar codec.
The script asserts unique checkpoint labels, an equal source/target method set,
exactly 81 checkpoints per worker, and expected success/error outcomes. The
source and target complete-record comparison digests match. It runs only owned
temporary code/database copies and removes those trees before exiting.

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python -B docs/superpowers/plans/2026-10-16-task8-workbench-public-preflight.py
```

The source worker first invokes the unmodified real `WorkbenchStore` constructor
against a nonexistent temporary database. The resulting full fresh schema has
65 **sqlite_schema table entries** (including SQLite/internal support entries),
zero Tasks rows and no FK findings. This count is not an ordinary-table inventory
and is not comparable to the actual production ordinary-table count. SQLite
backup clones this empty source for the target. In a separate target process,
an FK-off caller transaction replaces only the empty Tasks table with
`create_task20_schema`, then initializes `tasks:published` to zero with the
accepted initializer. Source twelve-kind/auxiliary tables remain unchanged;
this is an explicit bounded fixture, not a genuine source-to-target migration.

Only the owned target code receives the two proposal patches, applied with zero
fuzz, plus the necessary `native_events` import substitution to
`task20_projection('workbench_turns')` while retaining `task_id_expression`.
The actual `_open_connection` still creates/configures every connection; a
wrapper adds the exact scalar codec on target connections and a common SQL clock
on both sides. Target `_ensure_initialized` alone is bypassed **after** template
preparation. Both sides instantiate the actual class normally, retaining the
instance attributes, connection contexts and attachment reconciliation. No
lease/transition/event/DTO/hydration method is replaced. The source runs in a
separate process/module tree to prevent global projection/cache contamination.

| Executed paths | Decisive paired observations |
| --- | --- |
| Task and turn lifecycle | Whitespace validation, title derivation/rename, queued turn, replay/conflicting request, active/archive conflict, two-turn sequence, terminal latest state, successful archive and archived replay/new-turn rejection. |
| Lease and recovery | Idle claim, successful claim/renew, wrong owner, renewal exactly at expiry, equality-boundary recovery/reclaim with preserved started_at, stale append rejection, running stop, stopped expiry recovery and repeated recovery. |
| Native event paths | A real text_delta populates the live cache; SQL trace proves `terminal_cached_turn_ids` runs after append/complete and agent-event status hydration runs on live/terminal reads. Running reads return the full synthetic live text. Terminal reads return the existing native-record-unavailable sentinel, with no native attempt/file/CLI/provider call. |
| Lists and timeline | Live/archived/archived-only lists, invalid limit, tied timestamp/public-ID cursor boundary, empty and terminal snapshots, before_sequence/event_before pagination, events_has_more/events_next_cursor and latest task state. |
| Confirmation cancellation | Real running proposer creates a retained pending confirmation; cancellation first records intent, quiescence writes the runtime marker, cancellation requeues once, replay remains idempotent, resumed owner reads the private resume context. |
| Failed confirmation receipt | Real quiesced proposer/claimed execution, wrong-owner rejection, failed receipt and replay, cleared claims, redacted public arguments, retained result, queued resume and existing no-replay error fields. No external action runs. |

Every checkpoint compares public DTOs or exact exception type/message/conflict
code, actual BEGIN/BEGIN IMMEDIATE/COMMIT/ROLLBACK boundaries, source-shaped Task
field values and scalar types, raw physical Task IDs/parents, all runs and
retained confirmation/attachment/artifact rows, effective source-eight allocator
water and FK findings. Public UUIDs differ from physical integer parent IDs;
those identities and final raw links match. Final FK findings are empty on both
sides. The single declared normalization treats absent fresh-source sequence
metadata and explicit target zero as the same effective allocation water before
the first insert; the fixture setup distinction is retained in the receipt.
The common deterministic UUID stream and SQL clock avoid output normalization.


A separate executable **negative control omits only the native_events companion**
while retaining the same two caller patches and prepared target. After creating
and claiming one turn, the real public call
`append_event(turn.id, sequence=2, event_type="text_delta", payload={"text": "control-live-text"}, owner="control", now="2040-01-01 00:00:00")`
returns event 2 on source. The target without the companion raises exactly
`OperationalError: no such column: t.client_request_id` from the populated-cache
cleanup projection. Both caches contain one live entry. The source commits two
events; target commits only the initial control event because the failed cleanup
rolls back the new event. The script asserts this exact named error and raw event
count difference. This confirms the already identified outside-patch dependency
is exercised; the two proposals alone do not constitute a complete cutover.

During harness development the first probe exposed fixture mistakes: native
`list_turns` sorts descending, so indexing its last row selected the wrong turn;
the corrected sequence uses the `create_turn` return. Initial absent-versus-zero
sequence metadata also needed the explicit effective-water comparison described
above. Neither required a candidate change. Final expected-outcome assertions
prevent matching source/target errors from being counted as successful flows.

This run does not cover the frozen actual profile, migrated nonempty Tasks,
Task12-in-Tasks writes, the other Task8 Store public caller workflows, all
constraints/error/cursor combinations, concurrent races, injected filesystem or
transaction faults, attachment/artifact contents, an executed confirmation with
consumed authorization, native CLI/provider/browser behavior, the complete
current-fact inverse, final eleven-table reconciliation, publication or deploy.
The patches remain proposals; no app/test/current database or service changed.
Independent parent-owned specification and quality review remains required.
