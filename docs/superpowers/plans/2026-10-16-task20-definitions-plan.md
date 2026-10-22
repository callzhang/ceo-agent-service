# Tasks20 finite declarations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development with one mechanical writer, then fresh SPEC and fresh QUALITY reviews. Steps use checkbox syntax.

**Goal:** Add the executable, unselected twenty-kind Tasks declaration from the independently checked267-field map, without changing historical source factories, current callers or constructors.

**Architecture:** One physical Tasks table has7writable structural columns and finite payload schemas for20existing source kinds. Declared-affinity generated getters implement source fields for constraints/query/projection; physical parent/input pointers stay single authoritative columns. Existing8global IDs remain untouched; declaration alone does not allocate, migrate, select or retire source data.

**Tech Stack:** Python3.13.12 / SQLite3.50.4, sqlite3, existing native scalar codec, pytest; all verification synthetic memory rows in an owned disposable /tmp code tree.

---

### Task 1: complete executable source transfer

**Create:** app/task20_definitions.py and tests/test_task20_definitions.py.
**Modify:** only own claim row; own plan checkboxes; exact acceptance paragraph in docs/database-redesign-tasks20-field-map-20261016.md and docs/superpowers/plans/2026-10-11-database-redesign-program.md.

The complete implementation and regression source are adjacent canonical artifacts, not pseudocode. Source bindings: field map2026-10-16-tasks20-field-map.json, Tasks12actual metadata SHA f4a7dca390f9a5b28865de73058e0ae66117fbea32280dfcbeeca67c64348f29, original published8factory exact DDL hash recorded there.

- [x] Root prepares complete declaration/test source. Native target schema creates with7writable+268generated columns,51indices,22triggers. Preflight325pass9.47s; all new owned temporary code/DB removed. Ruff clean after formatting.
- [x] Claim exactly the two new app/test paths and bounded documentation before copying.
- [x] Transfer the complete source byte-for-byte:

```python
from pathlib import Path
import hashlib
import shutil
root=Path.cwd()
for source,target,digest in (
    ('docs/superpowers/plans/2026-10-16-task20-definitions-candidate.py',
     'app/task20_definitions.py',
     '59938318c8dacb836b7601c6c2f63671901a0232f7f23881007ef59fcef0c2c5'),
    ('docs/superpowers/plans/2026-10-16-task20-definitions-regression.py',
     'tests/test_task20_definitions.py',
     'dea543a9867ca35ab49c14458dbe86c5c55df2496f2462d5d564be502715a215'),
):
    assert hashlib.sha256((root/source).read_bytes()).hexdigest()==digest
    shutil.copyfile(root/source,root/target)
```

Implementation contains the complete267source-field literal declarations (including the already-retired final_text projection only); finite payload checks/getters; all captured24native residual indexes plus published8kind uniqueness/query shapes; exact existing source CHECKs and current parent/input/decision/round triggers; typed decision-batch public/physical composite FK;3full UNIQUE source aliases for the currently retained decision-item child; installer and source-shaped projections. No runtime evidence-file loading or universal JSON-validity policy is added.

### Task 2: applied verification

- [x] Run the durable preflight against the actual applied modules:

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python docs/superpowers/plans/2026-10-16-task20-definitions-preflight.py --applied
```

Expected325pass and OWNED_TEMP_REMOVED True. The preflight copies app, original conftest, static config and test code into its owned /tmp directory, checks module ownership, and executes only synthetic memory SQLite writes. No actual database constructors/providers/native CLI/service controls are invoked.

- [x] Run Ruff, exact hashes and diff checks:

```sh
/Users/derek/.cache/uv/archive-v0/1vqK0HG0A7DbkHHlOjztN/bin/ruff check --no-cache app/task20_definitions.py tests/test_task20_definitions.py
git diff --check
```

The325cases cover source field inventory and7stored-column shape; all20source-shaped projections; unknown-field rejection with a successful exact-shape control; native scalar storage classes;243source-derived NOT NULL cases with successful control inserts; retained CHECKs;6typed parent insert/update paths; current mapping multi-key/timestamps; nullable/BLOB source keys; publicbatchkey NO ACTION and decision immutability;3independent retained-child FK endpoints; native explicit-ID decision REPLACE; physical input pointers versus public projection; and nullable publishedWorkbench public keys. They do not prove full writer/allocator/DAO/migration behavior.

### Task 3: exact bounded documentation and commit

- [x] Append exactly this paragraph to both named owned documents:

```text
Unselected Tasks20 executable declaration: app/task20_definitions.py compiles20source kinds/267source fields into one Tasks table with7writable structural columns and constrained generated source getters. tests/test_task20_definitions.py passes325synthetic-memory cases including source-derived NOT NULL success controls, existing CHECKs/uniques, physical/public parent relationships, current mapping aliases/times, nullable/BLOB keys,3retained feedback FK endpoints and native decision REPLACE. No historical factory/default/constructor/currentcaller/allocator/migration/source retirement is selected by this addition; final10core+ledger remains open.
```

- [x] Commit only the2new app/test modules, own claim row, the two exact doc paragraphs and own implementation checkboxes. No main/push/production/deploy.
- [x] Fresh independent SPEC325/9.52s and QUALITY completed. QUALITY P2 repaired without app changes; SPEC repair22/0.72s and QUALITY22/0.75s plus4constraint deletion mutants accept the repaired tests. Root acceptance: ../specs/2026-10-16-task20-definitions-acceptance.json.

### Following execution gates (not completed by this declaration)

Build source-kind allocators and transactional writers, preserving independent high waters and ignored inserts/FULL transaction behavior. New batch INSERT OR REPLACE must preserve the physical identity referenced by decision children while advancing its source hiddenROWID/public key semantics: a raw shared-table replacement that assigns a different physical ID would invalidate otherwise source-legal child references. This is a storage writer requirement, not new effect reconciliation or policy.

Convert all existing8andremaining12caller groups and coupled retained children; migrate on a copied DB with existing8IDs intact; execute current writes/inverse/true predecessor writes/reforward and verify exact original declarations/current facts/allocators. Complete real native CLI with fake outbound and UI workflows, remainingActions/Business/Emails/Provenance/retirements, final11ordinarytable inventory, CI and approved production publication. The complete approved design goal remains active.


### QUALITY regression repair

Fresh QUALITY found two shadowed Workbench negative paths: three source CHECK cases and the parent INSERT guard reused an existing client_request_id. No declaration defect was reproduced; original declarations reject the independent invalid values. The complete adjacent canonical regression now supplies a distinct request ID, completed status and sequence2; both tests first insert the exact valid control under a savepoint, roll back, then change only the bad field/parent. The repaired canonical325case preflight passes; transfer the exact new regression hash above to tests/test_task20_definitions.py, rerun applied325cases/Ruff/diff, and commit only applied regression plus ownclaim/this repair checkbox. Application/canonical declaration SHA stays59938318 unchanged.

- [x] Mechanical writer transfers repaired canonical regression and verifies applied325cases.
- [x] Root confirms app exactSHA unchanged; bounded SPEC repairPASS and QUALITY22/0.75s, all4original mutants caught. Source/allocator/caller/migration/native/final11 gates remain open.
