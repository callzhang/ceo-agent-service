# A3 reviewed-plan execution ownership: local implementation evidence

This is the first A3 slice on `codex/business-model-execution-owner-20261019`, based
on `81d9cb61b49ec89a345a35018a1c77cfa54a5191`. The explicit source is
Store `2026-10-19.3` / Email53; the target is Store `2026-10-19.4` / Email53.
No production database, provider, native model, message sender, Git index,
commit, push or deployment was used by this implementer. The parent owns the
full production-copy proof, independent specification/quality review and release.

## Source and current owners

The source ordinary `candidate_executions.id` is `INTEGER PRIMARY KEY`, without
AUTOINCREMENT. Its public key is separate from physical `actions.id` and the
candidate public/physical identities. Forward keeps the candidate row and both
old execution identities. Seven source scalar facts move to the candidate's
declared JSONB vector; adopted review/selection remain nullable physical FKs.
All nine are NULL for a candidate with no execution. Candidate creation and
invalidation facts and independent review history retain their original owners.

The original execution physical row becomes `candidate_execution_alias` at code
62 with its same legacy key and candidate FK. Its payload is `[]`; it has no
lifecycle or timestamps. Source code 5 remains frozen for inverse but is excluded
from the target active type set. The old-shaped execution projection reads the
alias identity and plan execution facts. Attempts still point to the alias's
physical row and expose the old execution public ID. Existing terminal, stale
candidate, lease and superseded-execution receipt behavior remains in the original
runtime methods. The two historical delivery-repair execution updates use the
same plan writer; external verification policy is unchanged.

## Real DDL and inverse

Two immediately checked reciprocal triggers would prevent the two writes needed
for a new execution. The implemented representation uses three generated technical
binding columns, a matching unique index and a reciprocal composite FK declared
`DEFERRABLE INITIALLY DEFERRED`. Every nonexecuted/unrelated row has NULL binding
values. Alias candidate uniqueness is independent of public ID. Two physical
review/selection FKs retain parent type guards, with composite FKs enforcing the
same candidate and selection's adopted review. Alias identity updates/deletes
are refused. None of these columns introduces a new business fact.
All unrelated physical columns are NULL on aliases in both variants. The fresh
variant's optional `memory_attempt_ref` now has an explicit alias NULL check;
forward preflight refuses any unused physical execution reference instead of
dropping or carrying it into the alias.

The source declaration, all source execution bindings, dependencies and integrity
are checked before staging. Forward and inverse use the existing caller-owned
FK-off transaction and temporary staging pattern. The final stored columns and
SQLite types are compared in both EXCEPT directions against staged target rows;
all execution facts, declarations, FK findings and `sqlite_sequence` row identities
are preserved. Temporary tables are removed before commit. Inverse takes only the
current execution block and alias, including new execution/attempt facts. Retained
release metadata contains variant, original markers and FK findings, without
business row bodies. Email53's existing ledger does not gain a row for A3.

The frozen Actions source hashes (the full ordered declaration object list) are:

| Variant | Source SHA-256 | Target SHA-256 |
|---|---|---|
| actual_no_memory_fk | `77ac6dff94126976212d1cd7bfda3b36531e4ee8a02839a9a73f88bee85401d4` | `829ed4f2f5e9fcb3d0eb3b780c6e5857e3e4e050255fbaf18a64ce6db5598e3e` |
| fresh_memory_fk | `f9ab43a6874aba1cc9fc8d6c19770a4834c6374850e29630a905dcc91379c3be` | `7862eb54c651e272bb8c917c9232d91ec3b3663200eeedaf865b3b72d26a0bc4` |

Writable Actions columns change 25→26 / 26→27: the old execution-only
`selection_id` is replaced by the plan's two physical FK columns. No persisted
binding column is added. Original type codes 1–61 retain their source meanings;
current Actions still has 61 active types after replacing old execution with alias.
The historical factory defaults, B1 release constants and A1/A2 factories remain
explicit source constructors. Current constructors/manifest validators opt in to
`execution_owner=True`.

## Local evidence

Tests use `/Users/derek/miniforge3/bin/python` with SQLite 3.53.4 and task-owned
`/tmp/ceo-a3-execution-owner.*` directories. No complete repository suite was run.

- Initial runtime/layout RED: two failures, respectively missing explicit target
  layout and a live old execution type. Both became GREEN after implementation.
- Migration RED: both source variants lacked the execution-owner converter.
- First complete migration/DDL/runtime suite: 18 passed in 409.32 seconds. It
  covers exact forward/inverse, untouched raw rows and waters, 0/1 execution,
  selected/no-selection execution, postmigration execution+attempt, renewed lease,
  terminal result and immutable alias, all wrong-binding commits and valid atomic
  creation with foreign keys ON.
- Existing reviewed candidate, SystemExecutor and uncertain Attempt-detail
  callers: 79 passed in 116.47 seconds. This retains stale/revision/superseded,
  receipt and terminal semantics with the current representation.
- Registered release with current post-forward execution+attempt, repeated
  rollback/publish and real target cold open passed. The first cold-failure test
  exposed the remaining hardcoded publication marker in dispatcher readers;
  those readers now accept the explicit old/new version pair.
- Source/mixed constructor no-write checks, cold failure/in-flight reader and
  exact native-history repair regression: 4 passed in 61.08 seconds after the
  marker and repair-writer conversions. The repair RED showed old execution
  writes left the new plan status unchanged; the existing repair policy was not
  modified.
- Actual frozen app code at `81d9cb61b` was extracted into task-owned temporary
  storage and cold-opened the inverse of a target database containing a newly
  created execution and attempt. Old `AutoReplyStore` and `EmailStore` both
  succeeded. Public IDs, current uncertain status and exact execution/attempt
  JSON were read using old APIs; FK findings were empty and quick_check was OK.

- Additional edge cases: 14 passed in 251.26 seconds, including pure
  block-without-alias commit refusal, invalid adopted review/selection, source
  refusal, independent multiple reviews and empty/negative/int64-exhausted
  ordinary ROWID allocation. Each invalid transaction is checked separately.
- Existing source/storage/repair regressions: 16 passed in 66.40 seconds,
  including frozen A1/A2 declarations, type-code ABI and historical native-history
  fixtures converted through the current inverse/forward path.
- Final alias-purity RED reproduced acceptance of an unrelated physical memory
  reference in the fresh variant. The final checks require source refusal before
  mutation and target rejection of every unrelated stored column in both variants.
  Fresh-variant source refusal, frozen source declarations and the four release
  tests passed after the fix. The first exhaustive-column assertions encountered
  the existing parent-type guard before CHECK; using an absent parent isolates
  the NULL checks. The corrected both-variant inverse/purity and valid atomic
  creation tests passed: 4 passed in 50.10 seconds. These also reject noncanonical
  public keys, duplicate-candidate aliases and alias identity changes, and verify
  NULL generated bindings on unrelated/nonexecuted rows.

## Frozen review input

The implementation is frozen at base HEAD
`81d9cb61b49ec89a345a35018a1c77cfa54a5191`, with unstaged changes on
`codex/business-model-execution-owner-20261019`. The 1,201 Git-tracked plus
unignored new files under `app/`, `tests/` and `scripts/` have aggregate SHA-256
`bca3a178bf3d0227e03648894ebb4cd77ed6f85b45006ef655a30fa34074b3b2`.
For reproducibility, sort the unique repository-relative paths returned by
`git ls-files -z --cached --others --exclude-standard -- app tests scripts`; hash
each path's UTF-8 bytes, NUL, raw SHA-256 digest of its bytes, then NUL into one
SHA-256 accumulator. This includes unchanged source files but excludes ignored
runtime/cache files. All changed Python files passed Ruff, and `git diff --check`
passed. The implementer left the index and HEAD unchanged. Independent SPEC,
QUALITY and parent full-copy acceptance remain separate gates.

This evidence establishes the bounded local A3 implementation and synthetic
inverse. It does not establish production migration capacity, live execution or
completion of the remaining Reply/Sent/Postfix, B2, C or D slices.

## Root merged acceptance source

Independent SPEC then QUALITY approved the frozen patch and the first merge.
Root committed the patch at68f9cb378 and merged B1/current upstream atf93727cd8,
then the later CI digest/docs update. A3 storage, DDL, projections, runtime
writers, factory selectors, release and tests remain byte-identical to the
approved patch. Eight changed source paths are the upstream Consumer/native/OA
app/tests and a Consumer-release test hash update. Root current native-harness,
frozen-declaration and execution runtime checks passed4/6.96s before the final
CI-only test update. The historical1201/bca3 review digest above is unchanged
and is not the final merged tree.

Parent acceptance source isbf6b71ebfa9e0e67cb4c5b9f56e9da59ec8a8052;1202 app/tests/scripts files, aggregate
423b5e5a2b81527ea63753a5fdc797fc138e1181771ffdb96a7673ba2e07597d, using the same path/NUL/raw-digest/NUL algorithm.
See superpowers/specs/2026-10-19-execution-owner-merged-freeze.json.
At this freeze checkpoint full-copy/native/current-data inverse and live acceptance were pending. Parent full-copy/native/current-data inverse/reforward acceptance subsequently passed as recorded below; production publication is still pending. No predecessor CLI proof is claimed as a CLI run of this later source.

## Parent merged-source full-copy and native acceptance

Accepted source bf6b71ebfa9e0e67cb4c5b9f56e9da59ec8a8052:1202 app/script/test files, NUL-format digest423b5e5a2b81527ea63753a5fdc797fc138e1181771ffdb96a7673ba2e07597d. Source bytes rechecked unchanged after later documentation-only mergee1b2e8cdd. Input was the immutable independently accepted B1 current-data copy: fresh production19.1 → actual A2 code → fully verified B1 forward/new facts/inverse/old-code cold/reforward. This is a source-bound staged-copy lineage, not a claim of a fresh production19.3 snapshot.

Forward mapped327 original execution records into their exact candidate owner plus empty identity alias. Independent comparisons include every old scalar and SQLite typeof, public/physical identity and adopted references, all unrelated records, candidate payloads with only seven execution slots removed, explicit marker19.3→19.4, alias writable-column purity, and no execution block on source candidates without execution. The first harness used a digest over differing physical columns and failed; corrected comparison reconstructs retired selection_id as NULL only after proving every unrelated source value is NULL, and compares new reference columns separately. SPEC independently checked the normalization and requested the added type/purity assertions; strengthened baseline check passes. No product code changed for this harness correction.

Real native Codex Consumer/Audit completed four calls: Consumer386364/attempt386365(session01a156e1-6665-72f3-9c24-48e1f59c8f19,0–38), Audit386366/386367(session01a156e1-ecfc-7453-8d03-eb2b51db9f92,0–31), Consumer revision386368/386369(same Consumer session,38–50), Audit386370/386371(session01a156e2-a964-71a3-afbd-526381417312,0–31). Actual RuntimeAttempt bounds were checked, not aggregate Run bounds. Only synthetic artifact/system contract tools plus one global-rules read command; native originals stay in~/.codex. Task reached needs_human; actual copy UI selected execute for candidate1031/review1021/Reply attempt15707. Actual worker/SystemExecutor with InMemoryProvider then produced exactly1send,1verification,1external receipt; execution328done, action attemptverified, authoritative receipt body reference, execution state owned only by plan, alias empty. UI showed done,1/1verified and the substitute receipt. No additional native turn on resumed execution;pre-existing rows in11 tables unchanged. Console had no background workers and blocked all real providers, then was stopped cleanly with0provider calls.

Current-data inverse restored the entire source SQL declaration, retained the original FK finding, and actual archived40666e521 Store cold-open read the new done task, selected branch, execution, action attempt and substitute receipt. Reforward retained every physical business-table hash (including Email ledger), with only Store marker clock changed; full integrity_check=ok, FK multiset unchanged, immutable input SHA unchanged. Body-free evidence: validation/2026-10-19-execution-owner-parent-copy.json.

Identical original-row capacity comparison is **+3813376bytes** for Actions data+indexes; new declarative reference indexes cost more than the removed duplicated execution payloads. This step simplifies state ownership and writers; no file-shrink or VACUUM saving is claimed. Before publication a fresh production19.3/Email53 backup474292224bytes passed full integrity and exact pair validation with the original Runs190042→Tasks FK finding retained; only after verification it atomically replaced the older daily backup, SHA9818c448e7bf857ba85443f14ac4f2d62bc185d6cde8076cabbc572af3d1ac1a.

## Final publication integration

Concurrent separately authorized upstreamd88c9395f was already deployed while A3 acceptance ran. Normal merge retains its Consumer/Audit native OCR guidance, exact read-tool allowlist entry, and corresponding role/prompt tests. A3 storage/release/writer files remain unchanged. The final1202-file publication digest is e191bdc1b0ae0c4c4becbf6ba0c593d3ac0e522bb65a20b1f8064f16f3a67cce (same NUL algorithm), separately frozen in superpowers/specs/2026-10-19-execution-owner-publication-freeze.json. Affected role-tool boundary plus prompt files pass57tests/2.71s. Native/full-copy evidence above remains bound to bf6/423b source; no CLI run of this later OCR source is claimed.
