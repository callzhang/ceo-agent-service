# B2b specialized Email actions: local evidence

This bounded implementation is on
`codex/business-model-specialized-actions-20261019`, based on reviewed merge
`af9e265597f847c7a289b3022415050dbc2b5d53`. It includes the parent's separately
reviewed B2a temporary staging index correction. The exact transition is Store
`2026-10-19.6` / Email53 to Store `2026-10-19.7` / Email53.

The implementer used synthetic databases and fake providers only. Independent
SPEC then QUALITY review, parent-owned production-copy/native acceptance,
integration, and live deployment remain separate. No production database, native
Agent CLI, SMTP, IMAP, browser, or unsubscribe provider was invoked by this work.
This report contains source metadata and validation results, without message,
provider, or decision bodies.

## Actual owners and removal

Both claim families now use the existing shared effect-step owner. The migration
retains every original field and changes their physical discriminator in place.

| Source claim | Shared typed variant | Old → new code | Source fields retained |
|---|---|---|---:|
| `email_reply_dispatch_claims` | `effect_step_email_reply` | 28 → 68 | 17 |
| `email_unsubscribe_claims` | `effect_step_email_unsubscribe` | 21 → 69 | 19 |

Reply retains `action_identity`, `effect_digest`, `action_plan_id`,
`classification_id`, `account_id`, `stable_message_identity`,
`outgoing_message_id`, `owner_id`, `owner_generation`, `lease_token`, `sender`,
`thread_identity`, `account_updated_at`, `account_snapshot_json`, `status`,
`claimed_at`, and `updated_at`.

Unsubscribe retains `action_identity`, `effect_digest`, `action_plan_id`,
`action_plan_version`, `classification_id`, `account_id`,
`stable_message_identity`, `thread_identity`, `entry_reference`,
`operations_json`, `owner_id`, `owner_generation`, `lease_token`,
`account_updated_at`, `status`, `phase`, `claimed_at`, `updated_at`, and
`audit_agent_run_id`.

`action_family=effect_steps` and origins `email_reply` / `email_unsubscribe`
describe existing source identity. `record_type` still projects the original
read-only name, preserving the exact Emails/Configs guards and public queries.
The old codes, default declaration factories, and B2a's five-kind mapping remain
frozen. Email53 and Configs do not get a new version for these projections.

`shared_action_lifecycle.transition` performs the actual allocation, insertion,
CAS update, retirement, result insertion, and result amendment. The specialized
EmailStore entry points keep their original source validation, provider proof
checks, transaction boundary, return shape, and conflict behavior. Their
independent per-row claim/receipt mutation loops are removed. The common writer
does not call the old specialized mutation helpers. A final AST scan found no
application calls to the old insert/update/delete helpers for these four claim
and result families. All 285 EmailStore methods remain; changes are limited to
the two schema-selection methods and 17 specialized mutation methods.

CAS resolves the existing source-shaped predicate to physical Actions IDs within
the same SQL statement. The tested Reply query uses its original identity index,
then `SEARCH actions USING INTEGER PRIMARY KEY (rowid=?)`; no new permanent index
or second claim owner is introduced.

Reply's complete 13-field receipt remains one typed effect-result owner, including
the outgoing ID and exact provider receipt JSON. Unsubscribe's complete 20-field
receipt remains one typed effect-result owner, including the outcome, receipt ID,
evidence, bounded result/truncation/digests, observation digest, entry URL, clocks,
and bindings. The shared outcome API directly writes those owners. A claim
status never manufactures provider success.

Unsubscribe effect history keeps its composite `(action_identity,effect_digest)`
identity, previous digest, operations, Audit run, and time. Ordered observation
steps keep their separate integer public IDs and AUTOINCREMENT water.
Continuations keep unfinished observation/control/clock facts. Explicit
cancellations keep their principal decision JSON. Feedback version FKs and
immutable authorization/configuration snapshots retain their existing owners.
These facts are not copied into generic attempts. The reviewed external ledger's
non-null first AgentRun constraint remains unchanged; direct Email does not gain
an invented AgentRun or provider body. Postfix accepted-but-unverified receipts
remain outside that verified ledger.

## Finite declarations and current-data inverse

The existing release coordinator accepts exactly Store19.6/Email53 for this
transition. Constructors reject the old or mixed pair before a write. The
current publication selectors recognize 19.7 and the unchanged historical pairs.

Forward and inverse run in the caller-owned FK-off transaction. They stage
stored Actions columns and all `sqlite_sequence` row IDs/names/values, translate
only the two exact claim codes in one bounded pass, rebuild the exact declaration,
and compare both EXCEPT directions including `typeof`. Physical Actions IDs,
legacy keys, source public IDs, payload bytes, native SQLite classes, constraints,
and the original FK finding multiset are preserved. Existing FK findings need
not be zero. Temporary rows and waters are removed before commit.

Inverse reads the current target claims, results, and independent proof families,
including writes after forward migration. Release metadata contains the source
variant, original markers, and FK findings; it contains no archived business rows
or source database. The original Store marker clock, including a BLOB fixture,
is restored exactly. Email53's migration ledger remains unchanged.

| Actions variant | Frozen source19.6 declaration SHA-256 | Target19.7 declaration SHA-256 |
|---|---|---|
| `actual_no_memory_fk` | `d7f73a0780b35b5e8ce816f6701d6d9000214d8c3821d33c720490af98b5e498` | `6d1ef5c24cf7ee555ff7c00ddc11ce8280d7ffd6bff178ac0837b8561cb19236` |
| `fresh_memory_fk` | `986267b56a49e75c52a53e44bbc0ef64df38b9863d66b8d8f9b2df94416ff1c7` | `2feb33a2bce7c966dd32f1c3473d40e4408c004d680959f7f63549da6da489d5` |

These hashes use sorted-key compact JSON of the exact SQLite objects. Both
variants passed exact declaration, raw stored value/type, negative/zero source
ROWID, BLOB claim clock, unchanged nonzero FK findings, and current-fact inverse
tests. No specialized source field was dropped.

## Actual archived-code and focused validation

Actual commit `a76dc8072e5bc19f46310c721375002ec16d031f` was extracted with
`git archive` for app/tests/scripts. Its real AutoReplyStore and EmailStore
constructors produced the source fixtures and cold-opened the inverse database.
Those executable cold-open fixtures use `fresh_memory_fk`; the other exact
declaration variant is covered separately by the schema/value/type tests above.

Seven scenarios compare the original DAO operations under that archived
executable with the current code:

1. Reply owner claim, stale-owner rejection, uncertainty, and recovery.
2. Reply receipt, exact replay, conflict, and no-replay behavior from an empty
   historical claim/receipt family.
3. Reply retry and account-snapshot behavior from a source claim with public
   ROWID -7.
4. Unsubscribe continuation, a second effect version, ordered observations,
   exact terminal proof, and receipt replay.
5. Unsubscribe release, uncertainty, recovery, and explicit retry behavior.
6. An unfinished unsubscribe continuation with controls, observation reference,
   clocks, and no terminal receipt.
7. An explicit unsubscribe cancellation, decision replay, and claim/recovery
   refusal after cancellation.

Every scenario compares the complete public Email action-family tuples and
SQLite scalar classes, physical Actions rows/IDs/legacy keys/payload bytes,
canonical Emails, allocator row IDs/waters, Email ledger, and FK findings.
After post-forward writes, current facts match archived execution. The inverse
then cold-opens with actual old Store/Email code, and the same database is
re-forwarded and finalized under 19.7/53 with those facts unchanged. The ordered
unsubscribe-step namespace starts at a source water of 700 in the claimed cases.
An additional injected target cold-load failure checks source/target publication
readers, repeated rollback, and exact original BLOB marker restoration.

There are **136 distinct passing focused cases**: 20 new cases (five lifecycle,
seven schema, eight release) and 116 existing fake-provider cases. This is the
union of the batches below, not a claim that one fresh 136-case run was made.
The whole repository suite was not run.

| Batch | Result | Seconds |
|---|---|---:|
| Existing Reply delivery, unsubscribe evidence, and unsubscribe direct files | Initially 114 passed, 2 fixture failures | 394.28 |
| Five new lifecycle cases plus the two repaired provider cases | 7 passed | 115.19 |
| Initial five schema cases plus six archived-source release cases | 11 passed | 832.17 |
| Raw payload/class/nonzero-FK tests, both source variants | 2 passed | 400.91 |
| Unfinished continuation and explicit cancellation inverse cases | 2 passed, 6 deselected | 121.77 |

The initial two provider failures came from preexisting thread-mutation fixtures
issuing SQL against the retired `email_messages` table. They now mutate the
current `emails.message_thread_identity`; the original expected status/error
assertions remain unchanged. No runtime authorization or recovery policy was
changed to pass them. Both repaired cases passed in the focused rerun.

The initial common-owner tests failed before implementation because the old
specialized path did not call the shared transition writer and retained old
claim codes. The implemented path passed those cases. Concurrent Reply and
unsubscribe tests each produce exactly one claim winner; a losing-owner receipt
attempt leaves no receipt, then the winner completes and subsequent claims do
not replay. Outgoing message ID uniqueness is also preserved.

All 27 changed Python files passed Ruff, and `git diff --check` passed. All tests
used `/Users/derek/miniforge3/bin/python` and the fixed owned temporary root
`/tmp/ceo-b2b-specialized.rSyFcK`. The temporary databases, archived code, logs, and
diagnostic files were removed after process completion and open-handle checks;
only this body-free report, regression code, and source manifest are retained.
No physical database-size saving is claimed.

## Source freeze and parent interface

The exact current code manifest is
[the B2b source freeze](superpowers/specs/2026-10-19-specialized-actions-freeze.json).
It covers all 1,221 tracked and unignored new files under app/tests/scripts,
including their per-file hashes. Its aggregate SHA-256 is
`85d60dd1ae03a9adc7dd4c18351035f00e53ea1ee162df1ba58490f5dceb63c0`,
computed as sorted UTF-8 path + NUL + raw file SHA-256 + NUL.

The actual archived source has 1,216 files and aggregate SHA-256
`201756cb4812572741a79a0e03f9670ca7ec9af87ab65381656b7a39a7584170`
under the same digest convention. The reviewed B2a migration implementation is
unchanged relative to the base merge.

The parent can use the existing coordinator on its exact 19.6/53 copy:

```python
from pathlib import Path
from app.store_schema_release import prepare_database_schema_release

release = prepare_database_schema_release(Path(ROOT), Path(COPY))
release.publish()  # 19.7/53 and current Store/Email cold open
# Parent performs its authorized native/substitute-outbound acceptance case.
release.verify_loaded()
release.rollback()  # CURRENT facts and original source marker clock
# Cold-open COPY with actual archived a76dc8072 Store/Email code.
release.publish()
release.finalize()
```

The lower-level APIs are `migrate_specialized_actions(db)` and
`restore_specialized_actions(db, metadata)` in
`app.specialized_action_schema`. Metadata is exactly `{'reply_variant': ...}`;
the existing caller owns the transaction, FK setting, and original State marker.
No additional deployment or runtime compatibility entry point is added.

Independent read-only SPEC and then QUALITY both approved the frozen source.
SPEC independently verified all source/archived/declaration hashes and field lists;
QUALITY found no concrete correctness, performance or transaction blocker. Neither
review reran the adequate focused batches. Root verified the frozen per-file hashes
before staging. Parent real-copy/native acceptance, integration and live release
remain pending. A5's Postfix/OKR/Attempt ownership and business units C/D
remain outside this implementation.

## Parent normal integration before real-copy acceptance

Root committed the independently SPEC/QUALITY-approved source at211eabb87, then
normally merged reviewed B2a publication7e033bd65 atf82a63dc0. The append-only
claim conflict retained both complete sides. Every one of the27 specialized
source/test files remains byte-identical to the original approved freeze. The
merged tree retains B2a native/current-data inverse evidence and the separately
authored OA runtime/Skill changes; B2b does not author their policy or replay an
OA action. The separate merged freeze also pins both CI runtime inputs. The
exact upstream controls already passed14+1 at B2a; no specialized source is used
by those mocked controls, so they are not counted again as B2b verification.
Parent real-copy/native/substitute provider/current-data inverse/actual published
19.6 old-code cold/reforward and live publication remain pending.


## Parent copy, native chain and current-fact inverse acceptance

Frozen source773468fe0 has independent merged SPEC then QUALITY approval. Parent
used the accepted B2a current-data19.6/53 working copy (including earlier synthetic
native/direct outcomes), not a raw production19.6 snapshot. Its immutable SHA is
f715ca67fc6a44d3c3843d00e2ceeb65e0107ac2d7b44322cc9cfa743c930e16.
The pristine19.7/53 forward passed complete public tuples/types, stored
payloads/IDs, unrelated facts, allocator waters and original FK-multiset checks.
It migrated1109unsubscribe claims and0Reply claims. Same-population Actions+
indexes and payload deltas are both0bytes; no storage reduction is claimed.

The first real native fixture failed after four Consumer/Audit cycles, before
any provider call. Each raw Consumer execute option imposed exact body text;
existing Consumer preparation appended(via agent), conflicting with that option
instruction. Actual native session/line provenance and candidate/body hashes are
recorded in the copy JSON; preparation source is byte-identical to predecessor7e.
This failure and all its candidate/review history remain preserved. No Audit rule
was relaxed. An independent new fixture explicitly permitting existing formatting
then passed actual Consumer/Audit, real copy-console choice, one InMemoryProvider
send, one readback and one verified receipt. Task5131569106897310/Attempt15719,
Candidate1046/Review1036, execution333 reached done. Its Sent and attempt results
remain references with exact public bodies. Only those observed pages are
counted; screenshots and test databases remain disposable working artifacts.
The second success does not fix the first exact-body/Postfix contract issue;
A5 remains open.

Real domain code with substitute providers additionally preserved successful
Reply replay (one SMTP call/acceptance), uncertain accepted Reply (no terminal
receipt, no second dispatch), retryable definitely-not-dispatched failure,
verified unsubscribe/replay (one browser call), and unfinished awaiting_audit
continuation without receipt. No synthetic AgentRun/global verified receipt was
created. Automatic SMTP Reply enqueue remains disabled in the source, so these
SMTP outcomes cover its existing legacy/recovery domain, not a reactivated task
producer. Disposable fixture enum/string and duplicate-address assembly mistakes
were corrected in the harness only; incomplete fixture accounts remain facts in
the copy rather than being hidden by restoring a baseline.

Current-data inverse restored exact source declaration and original FK findings.
Actual predecessor7e033bd65120a623c6a1eaaeaece3afc359d25d8 Store and strict
EmailStore cold-opened the new native selection/execution/receipt/Sent and all
specialized current outcomes, including unfinished control and prior failure,
without replay or provider calls. Re-forward/finalize preserved all current
physical facts and SQLite classes except the one expected Store marker clock;
full integrity is ok and immutable input SHA is unchanged. Test console closed
cleanly with0provider calls. See validation/2026-10-19-specialized-actions-copy.json
for exact source pins, task/run/session/range identities and evidence boundaries.
Live preflight/publication/readback remain pending; C/A5/D are incomplete.

Independent final acceptance SPEC then QUALITY both APPROVE after checking the
underlying disposable procedures, artifacts and final evidence JSON. Neither
review reran tests or providers; source remains the approved773468fe0 freeze.


## Publication-source refresh after B2a live

Normal merge4fe745ec5 preserves actual B2a publicationcbf95112, including its
independent OA compacted-rerun repair. Only audit_web and its existing test file
changed within app/tests/scripts relative to the accepted773source; all27B2b
core paths are byte-identical to211eabb87. The separate publication freeze is
1222files/af007868dd0100d449d383d26c85bfacf0137ebf939f574b24b3f14da37c0912.
Both prior freeze files and CI runtime pins remain intact. Exact upstream OA
controls4passed at B2a after restart; they are not counted again as specialized
verification. Parent copy/native/current-fact inverse evidence remains bound to
773468fe0; it does not silently claim a re-execution on this merged source.
B2b production19.6preflight/verified-backup and publication remain separate.

Final publication-refresh SPEC then QUALITY both APPROVE. Fresh current
production19.6/53 SQLite backup passed full integrity, originalFK, complete
application marker, and exact19.7migration eligibility before atomic promotion
under the existing backup-directory lock. Latest backup is479956992bytes/SHA
a0c888db30ef341dd5904bb230576f29a80b6f0b2cc0bf900d3c39bee7b9f170.
Only after verification did it replace this workflow's prior19.5 daily backup;
no unrelated backup or temporary artifact was removed. B2b live remainspending.
