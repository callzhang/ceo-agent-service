# Business candidates component implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. One implementation author, then fresh SPEC followed by fresh QUALITY; no caller activation in this component. Steps use checkbox syntax for tracking.

**Goal:** Add an unselected conversion of the existing attention, project-candidate and WeChat Memory-candidate tables into the existing Business table, preserving all46 source fields and exact native behavior.

**Architecture:** Extend the current finite Business declaration builder with an explicit default-false option. Keep the existing nine-kind default declarations byte-identical. A separate typed component captures exact current28 source profiles, migrates3 parents and rewrites5 existing incoming FKs across4 retained children, and restores current facts and declarations on inverse. Current service constructors and paired publisher continue to select accepted28 until the later caller unit passes its own reviews.

**Tech Stack:** Existing Python/sqlite3, finite Business and native-scalar helpers, pytest/xdist. Python is `/Users/derek/miniforge3/bin/python`.

---

## Source and explicit bounds

Work only in `/Users/derek/.codex/worktrees/database-redesign-provider-state/ceo-agent-service`. Read shared policy and claims before edits. Accepted predecessor is the Feedback23-file freeze532a5079 and actual copy receipt8226a7f5:116→28→116→28,14family scope, actual83c1. Production remains116 and must not be written. Wholecandidate space remains above source; this component is storage consolidation, not authorization for retention/codec/policy changes.

Source records: `docs/superpowers/specs/2026-10-17-business-three-current-source-metadata.json` SHA922f03417c477b17b0df1bf1541eb5c89d36799ce80ca4e2134c73ddb10c5cd1, plus original approved CSV and readonly29-scope caller inventory. Actual3 declarations come from the passed real28 copy; fresh declarations are directly observed from a current28 Store+Email constructor. Attention assessment_json and WeChat memory_write_error differ in actual/fresh column position. Project candidate order is equal. Legacy-child complete current declaration is derived through the installed Business **and** follow-up/sync migrations and equals observed fresh28; its earlier Business-only diagnostic is explicitly incomplete and must not be treated as a target profile. The next real source copy must verify all dependencies.

Source mapping (these are existing fields; no project deadline, no new policy/status, no new source/body archive):

```python
CANDIDATE_SOURCE_TABLES = {'attention_item': 'business_attention_items',
 'project_candidate': 'business_project_candidates',
 'wechat_memory_candidate': 'wechat_memory_candidates'}
CANDIDATE_FIELDS = {'attention_item': ('id',
                    'stable_key',
                    'category',
                    'status',
                    'title',
                    'business_area',
                    'why_attention',
                    'current_state',
                    'ceo_action',
                    'anchor_id',
                    'evidence_signal_id',
                    'resolution_signal_id',
                    'resolved_at',
                    'created_at',
                    'updated_at',
                    'assessment_json'),
 'project_candidate': ('id',
                       'cluster_id',
                       'title',
                       'reason',
                       'status',
                       'confirmed_project_id',
                       'confirmation_signal_id',
                       'created_at'),
 'wechat_memory_candidate': ('id',
                             'import_run_id',
                             'account_id',
                             'statement',
                             'edited_statement',
                             'category',
                             'confidence',
                             'sensitivity',
                             'source_conversation_ids_json',
                             'source_message_ids_json',
                             'source_time_start',
                             'source_time_end',
                             'evidence_excerpt',
                             'cleanup_notes',
                             'status',
                             'reviewer',
                             'reviewed_at',
                             'memory_write_status',
                             'memory_id',
                             'created_at',
                             'updated_at',
                             'memory_write_error')}
```

All3 use source INTEGER PRIMARY KEY AUTOINCREMENT. Original captured data11attention/10projectcandidate/0WeChat; zeroWeChat is not permission to remove the feature or constraints. Attention stable_key is unique, category/status and compound resolution checks remain exactly native, including existing Unicode trim. Candidate status/confirmation/project/signal compound checks remain. WeChat unique(import_run_id,statement), REAL affinity and NOT NULL remain, but its status/category/JSON-named text fields have no new enum/JSON checks. In particular import_run_id has **no FK**, so do not invent a Runs parent.

Existing outgoing FKs: attention→Business anchor_public_id plus two native business_task_signals IDs; candidate→Business work_cluster_public_id/project_public_id and native signal ID; WeChat none. Keep Signals/source documents untouched. Incoming retained children are business_attention_events/tasks/proposal_tasks (attention_item_id), and business_legacy_links (attention_item_id plus project_candidate_id). Rewrite only these5 existing endpoints to typed Business aliases; preserve every other FK/index/CHECK/trigger/column/order, current rows/types/hiddenROWIDs and native AUTO waters, including legacy work_update_id→Actions and its type guards.

## Task1: Establish the pre-edit oracle and exact source fixtures

**Files:** Create `tests/test_business_candidate_storage.py`; use frozen source metadata; do not modify runtime callers.

- [ ] Add the following pre-edit definition oracle before changing the builder:

```python
import hashlib
from app.business_definitions import _definitions

def test_default_business_definitions_equal_frozen_current28():
    assert hashlib.sha256(repr(_definitions()).encode()).hexdigest() == (
        "91fedd3da844ec56624767b78fac27210d342414de7b79c01f02de5f038d9d91"
    )
```

- [ ] Build actual/fresh source fixtures from the captured literal declarations in this test module. Reuse existing schema/row/sequence capture primitives; test fixtures may read committed metadata, runtime code may not. Seed synthetic valid Business anchor/cluster/project and native Signal parents, all three new source kinds, and all4 retained children including nonempty legacy/association rows. Use only synthetic data, never commit production rows.
- [ ] Run the oracle before editing with this managed wrapper; expected1pass. Capture the existing module SHA from metadata as the independent baseline; do not generate an expected hash from changed code.

```python
from pathlib import Path
from tempfile import TemporaryDirectory
import os, subprocess, sys
with TemporaryDirectory(prefix="business-candidates-component.", dir="/tmp") as task_root:
    subprocess.run([sys.executable, "-m", "pytest", "-q",
                    "tests/test_business_candidate_storage.py::test_default_business_definitions_equal_frozen_current28",
                    "--basetemp", str(Path(task_root) / "pytest")],
                   env={**os.environ, "TMPDIR": task_root}, check=True)
```


## Task2: Add opt-in finite declarations and source-shaped writers

**Files:** Create `app/business_candidate_definitions.py`, `app/business_candidate_storage.py`; narrowly modify `app/business_definitions.py` and only if necessary `app/business_storage.py`. No Store/Email/Quality/coordinator/current selector edits.

- [ ] Write RED tests importing the new component exports below; expected ModuleNotFoundError before implementation. The test contract names define the implementation interface:

```python
from app.business_candidate_definitions import candidate_projection
from app.business_candidate_storage import (
    capture_business_candidate_source, migrate_business_candidate_schema,
    restore_business_candidate_schema, insert_business_candidate,
    update_business_candidate, delete_business_candidate,
)
```

`candidate_projection(kind, source_variant="actual", with_rowid=False)` returns exact source field order and source public IDs. `capture_business_candidate_source(db)` returns JSON-roundtrippable declaration/counter/dependency metadata only. `migrate_business_candidate_schema(db, metadata)` and `restore_business_candidate_schema(db, metadata)` require FK-off caller-owned transaction and never commit/rollback a successful caller transaction. Writers use existing BusinessWriteResult/rowcount semantics and bound native values; preserve actual plain INSERT/lastrowid behavior. They may expose explicit source-field SQL parts only where one-statement timestamps/native caller behavior require it; no generic SQL parser or user-value interpolation.

- [ ] Implement default-false declaration assembly by extending the current structured builder, not patching SQL user values or mutating the old global field dictionaries. Freeze the46 fields/types/defaults/CHECKs/FKs/order in the new module; runtime never loads evidence JSON. Keep source public IDs separate from private physical IDs using existing Business patterns; create the real typed FK endpoints and partial indexes necessary for existing identities/query order. Do not introduce a new physical table, a new generic relation type, or a made-up deadline.
- [ ] Implement source-affinity writes with native NULL/NaN ID allocation, signed IDs, BLOB/TEXT/INTEGER/REAL, missing defaults, native IGNORE/unique behavior, PK update/delete constraints and independent AUTO waters. Candidate default timestamps and multi-field updates must be evaluated as one native statement; do not add per-field timestamp SELECTs. SQLITE_FULL must preserve native whole-caller rollback behavior for these plain-INSERT callers. Preserve the writer lock/transaction ownership expected by current Business helpers; the later caller wrapper must preserve native implicit transaction semantics rather than strengthen its public contract.
- [ ] Add a concrete WeChat source-vs-target control with required native fields plus fixed timestamps, unconstrained native status and non-JSON text. This is valid input, not a new negative policy:

```python
values = dict(import_run_id="synthetic-run", account_id="synthetic-account",
              statement="synthetic fact", category="native category",
              confidence=0.25, sensitivity="native sensitivity",
              status="native-unconstrained-status",
              source_conversation_ids_json="native text, not JSON",
              created_at="2026-10-17 00:00:00", updated_at="2026-10-17 00:00:00")
columns = tuple(values)
source_cursor = native.execute(
    "insert into wechat_memory_candidates(" + ",".join(columns) + ") values(" +
    ",".join("?" for _ in columns) + ")", tuple(values.values()))
result = insert_business_candidate(target, "wechat_memory_candidate", values)
assert result.lastrowid == source_cursor.lastrowid
assert native.execute("select * from wechat_memory_candidates").fetchall() == target.execute(
    candidate_projection("wechat_memory_candidate", source_variant=variant)).fetchall()
```

Run this within paired real native/target fixtures with explicit caller transactions. Add every NOT NULL, all native CHECK/uniqueness and6parent-FK controls for the three kinds, including absent/wrong-kind parents, late failure rollback, empty/Unicode/BLOB scalar controls, duplicate WeChat import/statement, deleted-high-water/ignored insert/updated ID/maxAUTO and counter absence. Assertions cover both data and transaction/counters; do not merely assert implementation internals.

## Task3: Convert and restore the exact storage graph

**Files:** Same new component module and test file. No publication activation.

- [ ] Before DDL, reject changed/missing/unsupported native3/baseBusiness/dependent declarations and conflicting reserved main/TEMP names with data/schema unchanged. Use finite actual/fresh profiles in the captured metadata; no permissive missing-table fallback or arbitrary external-DDL copying.
- [ ] Capture only schema/FK/index/trigger/counter data for inverse. Snapshot transient rows in memory/SQL working relations, not persistent original backups. Rebuild Business with12kinds preserving all old9 raw stored source values/types, existing physical IDs, references and per-kind waters; translate native3 into their new typed records and drop their physical tables. Preserve source private/global counter semantics when reconstructing source Business and current writes on inverse.
- [ ] Rebuild retained children with only the5 FK endpoint rewrites, preserving all current rows/types/ROWIDs/AUTO waters. Account for both triggers on rebuilt tables and external counterpart triggers referencing rebuilt tables, including Actions legacy-link work-update guards. Restore exact unchanged external SQL after the source/target graph exists. Do not add policy/type rules beyond the original FK semantics.
- [ ] Assert current-fact inverse, including target writes in all3 new kinds **and** ordinary old9 Business writes/deletes, mutated counters, and nonempty retained children; all source declarations, source-order projections/types, dependency facts and source allocator waters must survive. Roundtrip metadata through JSON before inverse. Reforward must preserve current+source facts without retaining a native-body/row backup.
- [ ] Add malformed-source and missing-FK/index/trigger negative controls before mutation. Compare source schema/counters/rows before and after each failure, not only exception text.

## Task4: Validation and bounded handoff

**Files:** Create scoped component paragraph in `docs/architecture.md` and `docs/runtime-mechanism.md` only after tests pass; own claim row. No runtime/caller activation.

- [ ] Run scoped new tests and existing Business storage/schema/release controls covering changed shared functions, with dedicated managed TMP and `--basetemp`; no full repository suite or production tests. Save concise counts/commands/hashes only. Do not repeat already-passing unaffected large suites without changed code or a specific concern.
- [ ] Recheck the default28 definition oracle91fedd3d and unchanged source definitions/caller ASTs. Record object allocation on the same input before/after; this cannot claim wholecandidate space savings.
- [ ] Stop edits and report exact file list/fullSHA/tests plus any limitations. Root freezes paths, requests fresh SPEC then fresh QUALITY; fixes return to the same author and reviewer. Component success is unselected storage only. The next unit, after both reviews, owns all29 inventoried literal scopes/other indirect callers, Store initializer and paired current selectors/publication/cold25 acceptance, plus actual current-production copy/old-code rollback. Final10cores+ledger/remaining Actions/Emails/provenance/bodyavailability/performance/nativeCLI/UI/release remain in the original program scope.

## Plan self-review

46field sets byte-match approved CSV by root check. Two source column-order profiles and5incoming FK endpoints are explicit. No original-body retirement or policy choice is hidden in this component. Default28 preservation has a pre-edit independently captured oracle. Every implementation step has a named file, source contract and behavioral validation; no current entrypoint selects partial25. Existing user authorization selects subagent-driven execution in this isolated worktree, so no repeated execution-choice question is needed.
