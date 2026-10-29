# Canonical Email component: unselected synthetic candidate

This is an opt-in component only. It does not select a release or change
EmailStore/AutoReplyStore, Web, or training callers. The separate current12
integration owns those changes. No production or source database was modified
by this unit.
The accepted normal current13 copy receipt is
`6b8c310cf83694000ee671400f17136452452f0ae79edf4dbe39c0bd28a428fc`;
this component has **not** repeated an actual-data copy or proved capacity.

## Finite physical mapping

`emails` has one row per `stable_message_identity`. It has 46 physical columns:
45 retained source facts plus the structural physical `id`. Valid paired source
rows share only `account_id`, `stable_message_identity`, `uidvalidity`, and `uid`.
The other 26 classification facts use `classification_id` and `class_*` native
columns; the other 15 message facts use `message_id` and `message_*` native
columns. `message_normalized_text` and `class_model_text` are each stored once
in native TEXT-affinity columns. There is no stored JSON or generated body copy.
The raw classification/message folders, RFC/thread values, headers and separate
created/updated/received timestamps retain independent columns and SQLite
storage classes. The source projections return all 30 classification and 19
message fields under their original names.

`emails.id` is the physical key. Migration inserts paired rows first and prefers
their classification ID as physical ID; a collision receives the next available
physical ID. If the occupied maximum is INT64MAX, the structural allocator
chooses the lowest free positive ID so repeated migrations of identical source
rows have identical physical mappings. The separate public classification
allocator retains native ordinary random-at-INT64MAX behavior.
`classification_id` and `message_id` are distinct public aliases,
each with a full UNIQUE index. A message-only row has NULL classification ID,
and later classification fills that same row. The original Actions
`email_classification_id` field and values stay unchanged. Its one physical FK
now references `emails(classification_id)` with the original NO ACTION/RESTRICT
behavior, so a message-only row whose physical ID equals a classification number
cannot satisfy the wrong action reference. The seven classification-linked
Actions kinds retain that single FK and all existing type checks. Retained
Business/Legacy rows and the other five incoming Actions edges are not rebuilt.

Four source access indexes map to four target indexes: three classification
account/status/locator orders and one message account/locator order. Inline
UNIQUE indexes cover canonical stable identity and both public aliases. The
twelve source-owned triggers map to twelve projection-aware target triggers:
four status/source guards, one training-inclusion invalidation, three
classification plan-switch blockers, and four message delete/thread blockers.
Message-only updates do not enter classification plan-switch logic. All existing
blocker exception strings are retained; no new audit or policy check was added.

## Source profiles, current facts and inverse

The source declaration hashes are frozen from
`docs/superpowers/specs/2026-10-17-email-canonical-current13-source.json`:

| Profile | Classification | Message | Actions |
|---|---|---|---|
| actual | `e351befd…2835b` | `58c36f5a…f0a03` | `9b02487d…66658` |
| fresh | `0159086d…5a33` | `fabd6faa…114b1` | `b4ff6ce9…aa16` |

The actual classification profile differs in five trigger indentation bodies;
the actual Message DDL has historical ALTER-appended `in_reply_to` and
`references_json` columns. Source capture and inverse compare exact declarations
and project values in captured column order. The metadata is JSON-roundtrippable
and contains only declarations, three source table counts, the Message sequence
water and FK findings; it
does not include row or body backups. Inverse reconstructs **current target
facts**, including target writes after conversion, then restores the original
profile DDL, native public IDs, Message AUTO water and Actions declaration.
The single Actions FK target changes back to `email_classifications(id)`.
The Email declaration profile and Actions memory-FK variant are independent
native facts. Capture and inverse accept their four finite combinations while
still requiring each Email and Actions declaration to match its exact known
fingerprint. A mixed profile is not normalized to the paired default; its
original column order and Actions declaration are restored.

All conversion calls operate in the caller's transaction with foreign keys off,
matching the existing release helpers. A failed conversion is the caller's
rollback responsibility. Existing SQLite parser rename/drop probes reject
unknown MAIN/TEMP dependencies; only the exact known Actions parent FK is
masked in a disposable parser during that inspection. An unrepresentable
classification-only source or raw/type mismatch in the four shared facts is
reported before any table rebuild. Native message AUTOINCREMENT water is held
under the `email_messages` sequence name while the canonical table is active.
Successful UPSERT or INSERT OR IGNORE conflicts burn a candidate AUTO identity;
failed statements and outer rollback do not. INT64MAX has native SQLITE_FULL
rollback behavior, including the native DB-API `sqlite_errorcode` and
`sqlite_errorname` attributes. Ordinary classification IDs remain caller supplied or use
SQLite-style ordinary next/random-at-MAX allocation, including zero/negative
IDs and native integer-primary-key coercion. Read-then-write allocations and
locator checks acquire the existing email writer lock inside the caller's
transaction.

The component API is `capture_email_canonical_source`,
`migrate_email_canonical_schema`, `restore_email_canonical_schema`, source-named
`email_message_projection`/`email_classification_projection`, and insert,
upsert/ignore, update and delete primitives in `app/email_canonical_storage.py`.
They are available for the separate caller unit; no current13 caller selects
them here. A paired row cannot undergo a message-only deletion because a
classification-only canonical state has no representation. Its classification
must be removed first. Class-only raw SQL divergence of shared facts is likewise
outside the valid durable pair contract and is rejected instead of being
silently normalized. The source-shaped operation API covers the active Message
UPSERT and INSERT OR IGNORE conflict paths. Arbitrary raw SQL REPLACE against
the combined physical row is outside this component's API; there is no generic
SQL compatibility layer.

The existing EmailStore locator moves update Classification first and Message
second in one outer transaction. Their two intermediate source rows may briefly
disagree on `uidvalidity`/`uid`; a single canonical row cannot expose that
intermediate state. `update_email_paired_locator` applies the same final facts
in one UPDATE: one shared locator, independently supplied raw class/message
folders, optional class thread ID and class/message timestamps. Optional old
class/message folder and shared locator predicates represent the existing
conditional move; a failed match returns zero for the caller's current conflict
path. The other real path omits old predicates. Both preserve the caller's outer
rollback. The translated existing triggers remain active; locator-only updates
do not alter plan/status or Message thread identity, which are the fields their
in-flight blockers guard. A single-classification update that would diverge
from its Message still raises before mutation.

Shared values supplied to classification insert/update are bound through
SQLite TEXT/INTEGER affinities in a temporary in-memory table before their
value **and storage class** are compared with the canonical row. Thus native
equivalences such as UID `'20'` or `20.0` to stored INTEGER 20, and `True` to
INTEGER 1, succeed, while a genuinely different account, stable identity or
UID is rejected before mutation. The scratch table does not persist source
facts or add a generic SQL compatibility layer.

## Focused evidence and boundaries

TDD RED cases observed: missing component module; classification ID projection
name mismatch; inverse source-index order mismatch; missing classification-time
defaults; unknown dependency accepted; changed inverse metadata applied before
rejection; source actual Message column-order mismatch; nonrepresentable shared
value detected only after DDL; absent source-shaped upsert/update APIs. Each was
corrected against the same focused test file. A fresh SPEC review found two more
RED cases: four cloned source databases with a classification physical ID at
INT64MAX yielded four different physical IDs on collision, and the manual
overflow exception lacked native SQLITE_FULL attributes. Both were fixed in
the existing structural allocator and overflow path. A fresh QUALITY review
then exposed the real paired-locator sequence and pre-affinity Python
comparison. Retained RED runs showed the missing paired operation and rejection
of UID `'20'` beside native INTEGER 20; native source statements succeeded.
The new tests compare both conditional and unconditional source sequences,
distinct raw folders and timestamps, failed old-locator conditions, outer
rollback, native string/REAL/bool binding, and three genuinely different
shared-fact negatives. A later current12 integration exposed the diagonal-only
Email/Actions profile guard. The retained RED used exact known actual Email DDL
with exact known fresh Actions DDL and failed at capture. The four known
combinations now each pass metadata JSON roundtrip, target/current-fact inverse,
native DDL/column-order and allocator restoration, and retained Actions facts.
The focused fixture explicitly builds a historical current13 source from the
frozen native declarations over the current Store's non-Email graph; it no
longer relies on the selected EmailStore constructor to produce former parents.
Final focused run:
`PYTHONDONTWRITEBYTECODE=1 /Users/derek/miniforge3/bin/python -m pytest -q -x
tests/test_email_canonical_storage.py` → **28 passed in 56.26s** at that freeze.
The later selected-current12 integration exposed one row-factory representation
defect: `sqlite3.Row` did not compare equal to the affinity-normalized tuple in
`update_email_classification`, although both contained the same UID and SQLite
storage class. Retained RED: the new `sqlite3.Row` case rejected equivalent
`uid='20'`/`uidvalidity=10.0`. The comparison now uses `tuple(current)`; genuinely
different shared values still raise before mutation. Adjacent component reads
index or slice fetched rows, with no other Row-to-tuple equality comparison.
Focused GREEN: row-factory, affinity and four known source-profile cases **8
passed in 45.05s**; full component file **29 passed in 71.27s**. Runtime caller
tests belong to the paused integration author and resume only after review.
An independent R5 QUALITY probe then found the same row-factory boundary in
populated conversion: `_projection_snapshot` returned `sqlite3.Row` entries
while the source snapshot and `_restore_rows` use value tuples. Retained RED
captured one valid native pair with `sqlite3.Row`, then failed forward after
target DDL at the source-fact comparison. Projection snapshots now normalize
both data and `typeof` rows to tuples. The regression covers forward,
current-target body/model edits, inverse, public aliases, SQLite storage classes,
exact source declarations and counters with the Row factory retained throughout.
Targeted Row roundtrip, prior Row shared-update and four known source profiles:
**6 passed in 47.04s**. Full component file: **30 passed in 95.08s**.
`/Users/derek/miniforge3/bin/python -m ruff check` on the two component files
and focused test → **All checks passed**. This is synthetic/copy-only evidence,
not an actual-data or capacity result.

The present unit does not select current12, modify public callers, retire any
body or timestamp, rerun actual-data proof, make a capacity claim, commit, push
or deploy. The separate caller unit must update EmailStore, AutoReplyStore,
audit_web and training-labeler reads/writes and preserve the scan transaction.
Root-owned actual-copy, inverse, genuine-predecessor, capacity, native CLI and
UI gates remain later acceptance work.
