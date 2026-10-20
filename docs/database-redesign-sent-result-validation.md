# A4 Sent provider-result ownership: local implementation evidence

This bounded slice is on `codex/business-model-sent-owner-20261019`, based on
`10c1baa14a5f9f6b7447ec7b18bb86fb106e7ef3` (A3 source plus the preimplementation
claim). The explicit source is Store `2026-10-19.4` / Email53; the target is
Store `2026-10-19.5` / Email53. The implementer used only synthetic databases and
substitute receipts. Parent-owned production-copy, native CLI, independent SPEC
then QUALITY, integration and live acceptance are separate evidence.

## Ownership and source behavior

The existing `sent_replies.send_result_json` slot becomes NULL only when its
existing nonempty external key has a receipt with the exact same SQLite value
and type. The current public projection joins that owner in one query snapshot.
It does not parse, normalize or reserialize provider text. Different, unmatched
and blank-key bodies remain local. Empty strings, JSON scalar strings, raw
non-JSON text, embedded NUL and Unicode retain their exact projected text values.
Source Sent bodies and receipt bodies still reject SQL NULL, numbers and BLOB values.
Target Sent NULLs need a valid receipt at COMMIT; a receipt created later in the
same transaction is allowed. Existing body-first, receipt-later writes deduplicate
after the receipt arrives.

Two virtual technical keys derive only existing external keys. A deferred FK
requires the referenced external receipt. Existing public identity indexes are
partial or have another key shape and cannot act as this FK's unique parent.
One full unique parent index is therefore added; unrelated Actions contribute
NULL entries. A partial child reference index avoids full Actions scans on parent
mutation. On 20,001 unrelated synthetic error Actions with 4,096-byte pages, the
parent used 46 pages / 188,416 bytes (about 9.42 bytes per row; 99,877 payload bytes).
The empty child index used one 4,096-byte page. `EXPLAIN QUERY PLAN` uses
`SEARCH actions USING INDEX idx_actions_sent_result_owner`. This ownership change
does not establish a net physical database-size saving.

Target-only AFTER triggers materialize the prior receipt into untouched Sent rows
when an owner body or key changes or the owner is deleted. AFTER is necessary:
a BEFORE materialization would immediately deduplicate against the still-old
owner. Key-only Sent changes likewise retain the prior body. An existing delivery
repair that explicitly changes the receipt and Sent in one transaction adopts
the new outcome and deduplicates again. Candidate/attempt A2 preservation remains
unchanged. Tests use one Sent plus shared candidate attempts and another independent
Sent; two Sent references to the same nonempty key remain impossible under the
unchanged source unique index, and duplicate insertion is tested as a refusal.

Sent physical IDs, public IDs, AUTOINCREMENT waters, trigger/message data,
timestamps, recall fields, feedback token, channel and run identity retain their
source semantics. Observer physical FKs and public pairs remain intact. Source
factories, type codes, payload field order and defaults are unchanged.

## Explicit release and inverse

The existing prepare / publish / verify_loaded / rollback / finalize coordinator
selects the exact 19.4/53 to 19.5/53 transition. Constructors refuse previous,
mixed and unknown declarations before writes. Publication readers use the same
current selector and accepted predecessor list, including the dispatcher and
scheduled-config readers. No new deployer or compatibility write path was added.

Forward and inverse rebuild Actions inside the existing caller-owned FK-off
transaction, with temporary current-row staging removed before commit. They
verify stored values and SQLite types in both EXCEPT directions, complete
declarations, FK findings and `sqlite_sequence` row IDs and values. Inverse
materializes from current Sent/receipt ownership, including newly added Sent and
receipts, later recall changes and owner corrections. Retained release metadata
contains only source variant, original version/time markers and FK findings.
It contains no archived business bodies. Email53's ledger gains no row.

| Actions variant | Frozen source declaration SHA-256 | Target declaration SHA-256 |
|---|---|---|
| actual_no_memory_fk | `829ed4f2f5e9fcb3d0eb3b780c6e5857e3e4e050255fbaf18a64ce6db5598e3e` | `25790ebc80564660e643ed3ac6ea5d157912fe1e5f8cfb169423c797df619373` |
| fresh_memory_fk | `7862eb54c651e272bb8c917c9232d91ec3b3663200eeedaf865b3b72d26a0bc4` | `c9da6469aa1eccc80d6f2e74feb642c2b9470b02e5439f25651465dc6b10f6e7` |

## Retired writers and retained business assertions

An application-source search reconfirmed no callers of
`record_agent_message_delivery` or `record_reply_attempt_for_trigger` and no
application caller supplying the optional Sent arguments to
`finalize_orchestrated_reply_task`. Both methods, both optional parameters and
the finalize Sent insertion branch are removed. Worker, cron and Email callers
already omit the parameters, so their behavior is unchanged.

`record_sent_reply` remains the active OKR writer. `record_reply_attempt`,
direct-unsubscribe history, finalize Attempt/task writes, historical types and
readers remain. `complete_agent_run` only updates its run and was not removed.
The two API-specific attempt-reuse tests were retired with that API. Historical
delivery/recovery assertions use a typed fixture that creates the original Sent
and observer facts without inventing an external receipt. Current delivery tests
use `record_completed_agent_message_delivery`. The finalize test proves that
independently recorded delivery and its feedback survive task finalization.

## Focused evidence

All tests used `/Users/derek/miniforge3/bin/python`, SQLite 3.53.4, and the
task-owned `/tmp/ceo-sent-owner.0nD3LT` root. No whole repository suite was run.

- Initial RED: two expected failures, duplicate current Sent provider text and
  still-present retired APIs. Both passed after the ownership/API changes.
- First migration/runtime group: 18 passed in 328.29 seconds, covering both
  source variants, public/raw values, observer FKs, owner correction and deletion,
  new facts, exact inverse, allocator continuation and declaration refusal.
- Key-only mutation RED exposed adoption of an unrelated new owner's body;
  the Sent key-change materialization fixes that behavior. Index RED exposed a
  full reference scan; the partial child index changes it to an indexed seek.
- Release plus final key/index tests: 6 passed in 82.49 seconds. This includes
  current-fact inverse, repeated publication and rollback, current constructors,
  original BLOB marker time and cold-load failure rollback. The first cold-load
  failure exposed a stale scheduled/dispatcher source selector, now corrected.
- Raw text/type, shared candidate/attempt correction and both existing native
  delivery-repair paths: 4 passed in 102.92 seconds. The substitute handler's
  send count remains one after repair; the new result is projected and shared.
- Affected delivery/receipt/Attention callers and three Store history/finalize
  tests: 32 passed in 130.22 seconds.
- Final exact-source/inverse, deferred reference, unknown-source and existing
  provider ambiguity/failure checks: 10 passed in 148.82 seconds.
- Invalid source NULL/numeric result tests: 4 passed in 64.90 seconds. They
  deliberately bypass source CHECKs, then require refusal before any mutation in
  both variants. Unsupported target receipt types also remain refused.

The exact base app source was extracted with `git archive` into task-owned
temporary storage: 408 Python modules, path/NUL/raw-digest/NUL SHA-256
`828661f7f07262aff72d97d9ca172bd043d7eaa7df0bcf593183a09e95dcca5b`.
Its actual `AutoReplyStore` and `EmailStore` cold-opened the inverse containing
two Sent rows, one added after publication, a current recall update and an owner
correction that preserved the historical Sent body. Public Sent rows, physical
IDs and all allocator water rows matched digest
`5fed3157d76286e89523d02bc0a8d71694713f4965b2c368b2ea70d7f9d9929d`.
The old Store version was 19.4, original marker time remained BLOB, FK findings
were empty, and full integrity_check was OK. The first probe's final timestamp
query mistakenly addressed the retired physical `service_state` table; changing
only the probe to the frozen State projection resolved that harness failure.
The inverse database and app source were retained unchanged while diagnosing it.

That same database then re-forwarded, cold-opened with current Store19.5/Email53,
verified and finalized. It retained one referenced new Sent and one materialized
historical Sent, with zero FK findings and integrity_check OK. All 28 changed
Python files passed Ruff and `git diff --check` passed. The entire fixed
task-owned temporary root, including all test databases and archived app code,
was removed after its runs completed; existence was checked false. No production
database, real provider or production service was mutated, and no stage, commit,
push or deploy was performed.

## Frozen review input

The 1,208 Git-tracked plus unignored new files under `app/`, `tests/` and
`scripts/` have aggregate SHA-256
`3a1495a0fba960a0d04851c19b00a514ad8ee1a08ceaa525f2edb49347396413`.
Sort unique paths returned by
`git ls-files -z --cached --others --exclude-standard -- app tests scripts`;
hash each path's UTF-8 bytes, NUL, raw SHA-256 digest of its bytes, then NUL into
one SHA-256 accumulator. This includes unchanged source and excludes ignored
runtime/cache files. Source count increased by six files from the 1,202-path
base. The implementer leaves HEAD and index unchanged for parent review.
The machine-readable freeze is
[`2026-10-19-sent-result-owner-freeze.json`](superpowers/specs/2026-10-19-sent-result-owner-freeze.json).

This slice completes local Sent exact-result ownership and the three confirmed
unused writer paths. Full A remains incomplete: Postfix preparation/first-accepted
proof cannot universally derive from external results; active OKR and Attempt
retirement, historical unowned fields, B2, C and D are outside this slice.
