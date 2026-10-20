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
