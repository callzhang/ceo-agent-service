# Paired migration timeout: source diagnosis

The current12 payload diagnostic failed after 915.576 seconds at the unchanged
900-second paired-child deadline. Its failure receipt is
`superpowers/specs/2026-10-18-current12-payload-aggregates-failure.json`
(SHA256 `3d0f93d2fe51a8facf20f04453eabaea834a41a1f6e26c37fa9287186c1f71d8`).
The last outer stage, `backup_ready`, cannot distinguish child apply, verification,
or cold constructors. No payload aggregate query ran. Source allocation increased
only 311296 bytes from the prior R3 proof. System contention is a lead, not a cause
established by this evidence. No unchanged retry or deadline increase is selected.

The existing *pre-canonical* source116-to13 diagnostic profile is
`superpowers/specs/2026-10-17-email-history-migration-profile.json`
(SHA256 `d24ca24be72ab4232b92ea1fa0ee76934748ac7beed5885f100716c575999cd6`).
It measured apply410.1289s, Email-history41.937112s including Actions restore
35.935607s/98242rows, target verification14.47919s and7.488226s, cold Store0.285992s
and Email2.135947s. Its overridden1200s diagnostic cap and explicit non-acceptance
status make it unsuitable as a runtime acceptance claim. These are not R3 timings.

Source review finds whole Actions snapshot/rebuild/restore/readback in Email history
and another snapshot/rebuild/readback in canonical Email conversion. These are
credible expensive stages, not a proven explanation of this timeout. Web Email
routes retain the Store obtained at route registration; the inspected path does
not instantiate EmailStore for every request.

For the next already-required actual selected11 copy proof, after caller review
and exact source freeze, retain the normal900s child deadline and add only
`faulthandler.dump_traceback_later(60, repeat=True, file=sys.stderr)` to its disposable
diagnostic launcher before the unchanged `_paired_initializer()`. Capture stack
frames, not locals/SQL parameters/payloads. On Python3.12 POSIX,
`TimeoutExpired.stderr` is bytes even under text=True, or None: decode explicitly.
This can locate the reached migration/verification/constructor stage if it fails.
Do not recreate the deleted old R3 code snapshot from guesses; current caller edits
mean the next selected11 proof has a different explicit code boundary. No runtime
optimization, weaker value/type checks, production writes or sends are authorized
by this diagnosis. This note records no new database run or successful measurement.

## Additional current-fixture CPU evidence

Root sampled the task-owned live pytest PID45429 for one second during the
BLOB transport test GREEN run. The795 observed samples were under
`pysqlite_connection_execute -> sqlite3_step -> sqlite3VdbeExec -> renameTableFunc`,
with deep `sqlite3WalkExprNN` traversal. This was the current synthetic
apply/inverse/apply fixture, **not** the failed R3 child. The sample contained
stack metadata only; its owned temporary directory was removed. It establishes
real schema-rewrite CPU work at that instant, not total stage attribution.

Source review confirms `action_email_storage._external_dependencies` clones
MAIN/TEMP schema and performs native per-target RENAME/DROP probes in savepoints;
`action_storage._no_sql_dependencies` cumulatively renames targets in a scratch
parser; Email-history builds another masked scratch schema before the generic
probe. The next diagnostic should distinguish those function boundaries from
Actions `_restore_rows`, emitting names/durations only. Preserve all existing
native checks and the900s deadline. No check weakening or optimization selected.
