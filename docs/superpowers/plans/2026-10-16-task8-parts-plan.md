# Existing eight Tasks native SQL-parts implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development for mechanical exact transfer, then fresh SPEC and fresh QUALITY.

**Goal:** Supply exact native INSERT/SET/RETURNING parts for the eight currently published Tasks kinds, without selecting a new runtime schema or converting incomplete callers.

**Architecture:** The finite builder maps only existing source fields into the typed Tasks20 payload and existing physical identity/pointer columns. It leaves verb/IGNORE, CTE, UPSERT, target alias, FROM, WHERE, RETURNING, argument scope, cursor and transaction ownership with callers. Native scalar RHS expressions are encoded exactly in SQLite by one deterministic one-argument codec using the already accepted native scalar encoding; schema and read expressions remain builtin-only. No new business fields, tables, authorization/audit policy, transaction wrapper, SQL parser/regex, dual writer or fallback.

**Tech Stack:** Pinned Python3.13.12/SQLite3.50.4; exact frozen production/fresh wide Tasks source metadata; finite target Tasks20 declaration; pytest, Ruff.

## Source authority and resolved root cause

The Tasks8 metadata source at production44ee956d matches recreated historical source82columns/23indices/8triggers, including all original declarations and source projection SQL. Before target code, root froze original8per-kind insert default SQL from the unchanged historical task_storage source file, asserting the recorded source-file SHA. Artifact: ../specs/2026-10-16-task8-native-source.json. No source row/body was read. Source source-analysis: ../../database-redesign-task8-writer-analysis-20261016.md,116currentmutation sites plus14RETURNING obligations.

The first encoded-JSON SQL proposal lacked an exact encoder for computed SQL scalars. Native INTEGER-affinity attempts=1.2345678901234567 followed by +1 becomes2.234567890123457; json_quote rounded it to2.23456789012346. Committed regression RED:2fail/8pass/256deselected0.65s. Bounded independent research rejected printf('%!.26g') on29,985finite double controls (signed zero and nonzero1ULP differences). Existing native codec through a deterministic UDF preserved the same29,985values and additional scalar classes. Final builder accepts native scalar insert_sql/update_sql and automatically wraps each expression in ceo_native_json, eliminating hand-written encodedJSON CASE/arithmetic. Its small registration function only create_function; complete caller activation later must register at BOTH Store._open_connection and EmailStore._open_connection. Workbench inherits Store. Memory tests explicitly register. No constructor changes occur in this component.

## Complete canonical artifacts

The complete code and all test code are adjacent; preserve previous Tasks12 artifacts and earlier commits as historical evidence. This plan authorizes only new app/task8_storage.py and tests/test_task8_storage.py, own claim row and own implementation boxes, plus the exact two bounded doc paragraphs below. No current runtime caller/constructor/selector/migration/source factory/schema retirement changes.

Source metadata SHA256: 44f386b8d35acb23af0ef8597963ac9a67094a153f78dce02f61ba52a9f688e0.

### Task 1: exact complete application transfer

- [x] Root complete code, independent frozen source fixture/defaults, regression and disposable preflight before app edits.
- [x] Root final component suite599pass29.43s (274source-target+325declaration), plus2new work-summary business-leaf tests pass0.22s. All owned temporary trees removed. Full applied run must include all601cases. Earlier preparation failures were test fixture identity collisions/using an undeclared input field, and a repeated decoder placeholder in a new codec test; these were corrected against native controls before transfer. No application code was selected.
- [ ] Claim exact2new app/test paths and own bounded doc/claim paths before editing.
- [ ] Copy the exact complete adjacent code; assert hashes before copy:

```python
from pathlib import Path
import hashlib
import shutil
root=Path.cwd()
for source,target,digest in (
    ('docs/superpowers/plans/2026-10-16-task8-parts-candidate.py','app/task8_storage.py','bdc1809e75725d7364058b0e4e1c81a53cf9aa11621bda44ed2bce68823ff03f'),
    ('docs/superpowers/plans/2026-10-16-task8-parts-regression.py','tests/test_task8_storage.py','1e85b939d5f2a43e9f3f0bfaa1a476e82d669a1a711ccafb461081b903a45d36'),
 ):
    assert hashlib.sha256((root/source).read_bytes()).hexdigest()==digest
    shutil.copyfile(root/source,root/target)
```

The code contains Task8InsertParts(columns,values,parameters), Task8SetParts(sql,parameters), task8_insert_parts, task8_set_parts, task8_meeting_returning, register_task8_functions. These names are implementation-only interfaces, not new stored fields. Source field lists and physical pointer eligibility derive directly from existing TASK20_COLUMNS. Bound scalar values use existing native codec. Scalar expressions are trusted application SQL, evaluated once inside original statement; parameter tuples are emitted in SQL order. Omitted defaults use original native SQL in that statement; explicit NULL remains NULL. SET keeps physical pointers outside payload so UPDATE OF fires natively. Meeting RETURNING preserves physical id and legacy_key plus source business aliases.

### Task 2: applied focused proof

- [ ] Run exact pinned applied full component/declaration preflight:

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python docs/superpowers/plans/2026-10-16-task8-parts-preflight.py --applied --with-declarations
/Users/derek/.cache/uv/archive-v0/1vqK0HG0A7DbkHHlOjztN/bin/ruff check --no-cache app/task8_storage.py tests/test_task8_storage.py
git diff --check
```

Expected601pass (276source-target+325declaration) and OWNED_TEMP_REMOVED True. Only synthetic memory databases are written; preflight owns and removes its /tmp code/test directory. It loads original table/index/trigger SQL from frozen actual/fresh metadata, not target factory. Native source defaults are independently frozen; source typed projections are frozen too. Target value samples use existing test helpers, but all expected source behavior is executed against original declarations.

Coverage: all8complete/minimal insert fields, source clocks and nullable defaults, twelve scalar classes on INSERT/UPDATE with matching CHECK errors, IGNORE sequence water, old input RHS snapshots and physical/public pointer distinction, plain versus leading-WITH UPDATE RETURNING transaction/rowcount plus physical/public meeting IDs,3native conditionalUPSERTs and high-water consumption, UPDATE FROM receipt RHS, mixed bound/expression/structural argument order, arithmetic finite/Inf/subnormal results, single evaluation/lazyCASE, exact finite-double codec values from512seeded bit patterns plus explicit scalar boundaries and signzero, registration without commit/transaction ownership, multirow ABORT rollback and work-summary legacy business JSON leaf versus encoded outer container.

### Task 3: truthful documentation and scoped commit

- [ ] Append exactly this paragraph to docs/database-redesign-tasks20-field-map-20261016.md and docs/superpowers/plans/2026-10-11-database-redesign-program.md:

```text
Unselected existing8 Tasks native SQL parts: app/task8_storage.py compiles finite INSERT/SET/meetingRETURNING fragments and exposes explicit connection-local exact scalar codec registration, preserving native statement and caller transaction ownership.601focused cases (276frozen actual/fresh source-target +325declaration) pass, including native floating arithmetic, CTE/FROM/UPSERT, physical input/version snapshots, ignored/UPSERT allocator water and business-leaf copying. No current caller/constructor/selector/migration/source retirement/production activation occurs. Complete conversion must register the codec at both existing Store and Email _open_connection methods, translate116mutation sites plus direct readers and14RETURNING contracts, then perform copy/current-fact inverse/predecessor/native/UI/final11 acceptance.
```

- [ ] Commit only2app/test paths plus own claim, this plan transfer checkboxes and the two exact doc paragraphs (6paths); do not edit root-owned canonical/preflight/source metadata, other claims, historical factory or architecture/runtime behavior docs. They do not change because code is unselected.
- [ ] Root fresh SPEC checks exact source/defaults/scalar/parameter/pointer/transaction/selector contracts, then fresh QUALITY uses meaningful negative controls: replace scalar UDF with json_quote, re-evaluate RHS twice or eagerCASE, public instead of physical RETURNING, pointer snapshot reassignment, mixed parameter-order swap, business-container copy. Resolve concrete findings before acceptance.

## Required continuation before selecting Tasks20

This component is not116caller conversion. Prepare complete conversion artifacts against actual source; keep original CTE/FROM/UPSERT/CAS/RETURNING shapes and rebuild parameter order explicitly. Register one exact codec at Store._open_connection and EmailStore._open_connection at complete cutover, and explicit target supplied connections in tests. work_summary_inputs copied legacy body is excluded."work_summary_inputs__payload_json", not excluded.payload_json (outer container). Existing8after-insert public key behavior and old global identities remain; residual12counter semantics remain independent. Convert all20callers/source initializers and retained feedback endpoints; no partial selector or dual writer. Then copied forward/current-fact inverse/genuine predecessor writes/reforward, lease/filesystem recovery, real Codex CLI fake-outbound/UI/business result/receipt proof, remaining families/retirements/CI and final approved production publication. Final target remains10core+1Emailledger; latest prior integrated copy67ordinary tables is not final completion.
