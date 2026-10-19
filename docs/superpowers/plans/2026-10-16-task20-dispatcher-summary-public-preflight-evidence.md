# Dispatcher and summary: bounded synthetic public differential proof

The real source and exact proposed target pass **122/122 paired checkpoints across 28 public method routes**, with zero differences. This is an author proof for five queue adapters and the work-summary compression routine; it does not establish all eight external-writer modules or activate Tasks20. The [executable preflight](2026-10-16-task20-dispatcher-summary-public-preflight.py) and [metadata receipt](2026-10-16-task20-dispatcher-summary-public-preflight-evidence.json) pin source, helper and patch hashes. Task-owned code/database copies are removed after the run.

| Actual public path | Equal checkpoints | Behavior exercised |
| --- | ---: | --- |
| ReplyQueueAdapter | 26 | Claim, release, metrics, owner/generation checks, renew/expiry, physical input snapshot and three-attempt handler budget |
| ScheduledExecutionQueueAdapter | 26 | Same real inherited paths with scheduled-channel override |
| MeetingQueueAdapter | 20 | Claim/release, prior-attempt arithmetic, renew/expiry and original lease/status CAS |
| OkrReviewQueueAdapter | 20 | Claim/release, generation-based envelope attempt, renew/expiry and original status CAS |
| WorkSummaryQueueAdapter | 20 | Claim/release, prior-attempt arithmetic, renew/expiry and original status CAS |
| Actual channel membership | 4 | Reply claims DingTalk then WeChat; scheduled override claims scheduled; email remains excluded |
| Summary compression | 3 | Bounded compression, idempotent repeat and invalid batch size |
| Stale business leaf seam | 1 | Original native CAS returns zero after an explicit intervening fixture leaf write |
| Competing writer | 1 | Real second connection cannot write inside the routine's BEGIN IMMEDIATE |
| Summary second-batch ABORT | 1 | First batch remains committed; second batch rolls back and raises |

## Source, target and instrumentation boundary

The source uses the original complete fresh `AutoReplyStore` constructor: 65 tables, empty Tasks and no FK findings, with FK enforcement enabled. The target starts as an exact backup of that empty synthetic template. Preparation replaces **only EMPTY Tasks** with the accepted Tasks20 declaration and initializes its published-eight water to zero; every other table declaration remains identical. Native twelve-family tables remain present and are not retired.

The target code applies the exact Store8, Store-misc, Feedback4 and external-eight proposal patches sequentially. Every external candidate file and the cumulative Store candidate hash are checked. No unrelated application source is edited. The current Task12 helper is copied and hashed, but no Task12 public writer is exercised by this proof.

Target `_ensure_initialized` alone is bypassed **after** empty schema preparation. The actual constructor still supplies its original attributes and ContextVar; original `_open_connection`, `_connect` and immediate-write transaction bodies execute. A fixture wrapper adds the accepted scalar codec and a fixed SQLite `current_timestamp`. Source connections retain their normal schema and methods. This is not a cold-target constructor, migration or source-row preservation test.

Synthetic rows are seeded through the native source insert helper or accepted finite Task8 parts, outside recorded public calls. No Task12 rows are seeded, so both sides retain the exact same published-eight IDs and physical pointer coordinates without ID normalization. Python/native SQLite scalar classes, complete logical facts, physical Task rows, input parent/pointer rows, State leases, method results, exceptions, rowcounts and transaction traces compare exactly at every checkpoint. Persisted transition and rollback witnesses contain compact hashes, not fixture row bodies.

A native `sqlite3.Connection` subclass observes the actual cursor rowcount and trace. It delegates unchanged statements to SQLite. Its two explicit summary injection modes are described below; it does not simulate queue methods. `sys.setprofile` records entry into the copied actual adapter/summary functions. Public execution rejects socket connections, subprocesses and `os.system`; dependencies import before that audit hook because croniter's platform metadata check may invoke the local `file` utility. No Agent CLI, provider, worker, server, network operation or outbound action runs.

## Decisive native transitions and failure controls

Reply and scheduled fixtures begin with attempts 3, input version 7, previously claimed version 2, physical current-input pointer 9 and previous claimed pointer 8 on reply physical ID 7. The real first claim produces attempts 4, claimed version 7 and claimed physical pointer 9 while retaining current version/pointer. Expired reclaim produces attempts 5 and lease generation 2. Matching source/target old/new header, physical-pointer and lease hashes are retained under the labeled transition witnesses.

Renewal at second 2 moves expiry from second 10 to second 12. Claim at second 11 is empty; renewal exactly at expiry fails. An expired lease whose PID callback is alive remains protected. The same real claim reclaims it when that callback becomes false. The callbacks only consult fixture PID sets; they never query or signal a process.

For every adapter, wrong-owner and stale/forged-generation release errors preserve all committed facts. A source status changed to pending makes the real release's native Task UPDATE return **rowcount 0** after its lease update; the method raises and the whole transaction rolls back, including the lease. Matching before/after committed-fact hashes prove that result. Release after restoring processing succeeds. Release with fixture attempts zero retains the native floor at zero for Reply, ScheduledExecution, Meeting and WorkSummary; OKR continues using lease generation for its envelope attempt.

An explicit temporary BEFORE UPDATE IGNORE trigger makes the actual claim UPDATE return rowcount 0, and claim returns None without changing facts. A separate temporary lease-update ABORT trigger fires **after the real Task UPDATE returns rowcount 1**. Each adapter raises `IntegrityError: fixture lease abort`, traces BEGIN IMMEDIATE/ROLLBACK, and restores the entire pre-call Task/pointer/lease state. Removing that fixture trigger allows a subsequent real claim to succeed. These triggers are test fixtures; application policies are unchanged.

Reply and ScheduledExecution also call the actual handler-error method. Their first and second failures retain attempts 1 and 2 and the original 60/120-second backoff. The third claim reaches attempts 3; its failure leaves failed status. The recorded metrics and claim routes exercise the existing source-shaped and native channel predicates.

## Work-summary leaf, CAS and partial-commit evidence

The real `migrate_work_summary_payloads` runs through its own original raw `sqlite3.connect` path. With batch size 1 it scans four eligible rows and compresses two: **31,022 stored bytes become 228 bytes**. An already-compressed processing row is not wrapped again, a small payload is unchanged, and a done row is excluded. Repeating the routine compresses zero rows. Stored-byte hashes/types/lengths and decoded hashes compare exactly across both sides.

Every target outer payload remains a valid JSON object stored as text. All sibling fields retain their exact hash; only the typed business `payload_json` leaf changes. The raw target wrapper witnesses retain sibling and leaf JSON hashes, while native source-shaped readback independently verifies compressed BLOB storage and lossless decoding.

BEGIN IMMEDIATE prevents another connection from committing a change during the batch. The actual competing-writer control receives `OperationalError: database is locked`, then the original routine compresses successfully. The stale-leaf control is a **disclosed same-connection, same-transaction injection immediately before the original UPDATE**, not a claimed successful concurrent commit. It changes the business leaf while leaving the routine's selected `current` value stale; the unchanged original CAS returns rowcount 0, counts compressed=0 and preserves that intervening value and all siblings.

A second-row ABORT control uses the routine's actual independently committed batches. The first row becomes a compressed BLOB and remains committed; the second row retains its original text. The routine raises, with BEGIN IMMEDIATE/COMMIT/BEGIN IMMEDIATE/ROLLBACK. Equal errors alone are insufficient: both stored leaf outcomes and the earlier commit are asserted.

The negative control runs successful source and target native controls first: each scans and compresses one row (15,511 bytes to 114). It then changes **only the caller CAS column** from generated `work_summary_inputs__payload_json` to outer structural `payload_json` in a separate copied target. SET and the real routine remain unchanged. The mutant returns rowcount 0 and compressed=0; counters and stored leaf readback detect it. This mutant is never applied to application source.

## Verification and remaining work

Pinned Python 3.13.12 executes the fixed 122-checkpoint/28-route matrix. Ruff and compilation pass. Source/target record digests match; expected native failures, all fixed case counts, successful controls, killed mutant and owned-temp deletion are asserted. No application or test source is modified.

The evidence remains **synthetic fresh and prepared-empty target only**. It does not cover actual production profiles, preserved nonempty source rows, migration/copy, current-fact inverse, predecessor/reforward, cold target construction, native Agent trajectories, UI behavior or the final ten-core-plus-ledger release. Candidate wakeup, delivery repair, scheduled recovery, deferral, error migration and maintenance public methods remain separate acceptance work. Native scheduled-definition queues are outside this five-adapter Tasks slice. Fresh independent root review and rerun remain required before acceptance.

Run with:

```sh
/Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python -B docs/superpowers/plans/2026-10-16-task20-dispatcher-summary-public-preflight.py --output docs/superpowers/plans/2026-10-16-task20-dispatcher-summary-public-preflight-evidence.json
```

Harness SHA256: `a80bbd70007d467186b4909066668bbf009d5d78d965631be2ce6335c82019c4`.
