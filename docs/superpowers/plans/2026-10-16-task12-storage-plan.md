# Native Tasks12 transactional writers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development with one mechanical writer, followed by fresh SPEC and fresh QUALITY. Steps use checkbox syntax.

**Goal:** Implement real insert/update/delete/source-ROWID projection and independent allocation for the twelve residual source kinds against the unselected Tasks20 target.

**Architecture:** Reuse the exact finite source fields and existing native scalar codec. Caller transactions retain write ownership; source identities remain separate from physical Tasks ids. Typed current batch endpoints survive source-key replacement, sequence metadata lives in SQLite's existing sqlite_sequence, and no constructor/current caller/migration/source-retirement switch occurs in this step.

**Tech Stack:** Python3.13.12, SQLite3.50.4, sqlite3, pytest; actual/fresh native source fixtures plus synthetic memory target rows in an owned disposable /tmp code tree.

---

### Task 1: exact complete implementation transfer

**Create:** app/task12_storage.py and tests/test_task12_storage.py.
**Modify:** app/task20_definitions.py; own claim row/this plan's implementation checkboxes; exact paragraph in docs/database-redesign-tasks20-field-map-20261016.md and docs/superpowers/plans/2026-10-11-database-redesign-program.md.

- [x] Root complete canonical source/regressions/preflight. Initial differential10failures identified8REAL scalar-type differences and2testfield-selection omissions, corrected before transfer. Source standalone probe confirms SQLite3.50.4 drops REAL integer-storage reads in ordered flattened subqueries. Generic REAL source-value projection preserves native numeric floating type and leaves text/BLOB values unchanged. All532focused cases pass26.64s; new owned temporary code/DB removed. Ruff clean.
- [x] Claim exactly the three app/test paths and bounded documentation before editing.
- [x] Copy all complete adjacent canonical source files byte-for-byte:

```python
from pathlib import Path
import hashlib
import shutil
root=Path.cwd()
for source,target,digest in (
 ('docs/superpowers/plans/2026-10-16-task12-task20-definitions-candidate.py',
  'app/task20_definitions.py','8b776a0943e5d5aaaf6b827975677fb5a7b8f2db3fe75d7a2219b8bf90cad67f'),
 ('docs/superpowers/plans/2026-10-16-task12-storage-candidate.py',
  'app/task12_storage.py','950d56b7de5989b3da2bf0db324d981177eb38407c8aff486f88658ef18985f4'),
 ('docs/superpowers/plans/2026-10-16-task12-storage-regression.py',
  'tests/test_task12_storage.py','b796f36623913003b67c76d6b22cb84a324806b9bbf1cb16128c59f64940ee5d'),
):
    assert hashlib.sha256((root/source).read_bytes()).hexdigest()==digest
    shutil.copyfile(root/source,root/target)
```

These adjacent artifacts contain all code. Storage exposes insert_task12/update_task12/delete_task12/task12_projection and Task12WriteResult, accepting exactly the twelve captured native kinds and their fields. It never commits; native source FULL exceptions intentionally retain round-only statement rollback versus four whole-transaction rollbacks. Physical identity allocation and public/hidden source identities are independent. Source PK replacement can reuse the referenced physical endpoint while its hidden source ROWID advances; UPDATE OR REPLACE can preserve the other conflicted public-key endpoint. Existing native default SQL executes in the insertion/update statement, including source REPLACE-NOT-NULL/default semantics. Existing enum/JSON/immutable/typed FK contracts remain database-enforced.

The declaration update exports12exact source PKs/5AUTO kinds, adds5source-kind sequence triggers and3after-insert type guards, and introduces task20_value for correct native REAL projections. The3after-insert guards translate an existing source guarantee: separate source child tables cannot create a missing parent by receiving the same shared physical ID themselves. Before-insert guards alone cannot see that new self row. No new business field, audit policy, expiry, recovery or authorization rule is added.

### Task 2: verify applied code

- [x] Run the full focused source/target plus declaration check:

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python docs/superpowers/plans/2026-10-16-task12-storage-preflight.py --applied --with-declarations
```

Expected532pass (207new source/target checks +325existing declaration checks) and OWNED_TEMP_REMOVED True. Only memory SQLite rows are written. The preflight copies the actual/fresh native oracle and existing declaration helpers into one owned /tmp code directory; original conftest and two static configs remain in use. It asserts app import ownership. It invokes no real DB constructor/provider/native CLI/service API.

Coverage includes both actual and fresh source layouts, all12source schemas/defaults/scalar classes, signed ROWIDs/reuse,5interleaved independent AUTO streams, ignored conflict/NULL water consumption, source-native FULL scope and round BEFORE-trigger precedence, nullable/BLOB source PKs and hidden identities, INSERT/UPDATE REPLACE with valid linked batch children plus failed replacement rollback, source-NOT-NULL default replacement, owner/generation CAS, source identity update/next allocation, guarded delete including sourceROWID, immutable decisions, source legal REAL/text/BLOB/nonfinite/subnormal projections, source-parent self-alias rejection, and indexed source-counter/CAS reads. Existing8kind declarations/projections remain covered by325cases; their transactional writers still require conversion.

- [x] Run exact hashes, Ruff and whitespace checks:

```sh
/Users/derek/.cache/uv/archive-v0/1vqK0HG0A7DbkHHlOjztN/bin/ruff check --no-cache app/task20_definitions.py app/task12_storage.py tests/test_task12_storage.py
git diff --check
```

### Task 3: bounded docs and commit

- [x] Append exactly this paragraph to the two named documents:

```text
Unselected Tasks12 transactional storage: app/task12_storage.py supplies source-shaped insert/update/delete/ROWID projection for12residual kinds using the Tasks20 declaration.532focused synthetic cases (207actual/fresh source-target +325declaration) pass, covering5independent AUTO streams,7hiddenROWID families, native ignored/FULL semantics, owner-generation CAS, source PK/ROWID replacement and referencedbatch physical identity. The declaration adds5sequence triggers,3source-parent self-alias guards and native REAL projection preservation; no currentcaller/constructor/migration/source retirement/production selection occurs. Existing8writers, allcaller conversions andcopy/inverse/native/UI/final11publication remain required.
```

- [x] Commit exactly the3app/test paths, ownclaim, this plan's implementation checkboxes and the two exact doc paragraphs (7paths). Do not include root-owned canonical/preflight artifacts or touch other claims.
- [ ] Fresh independent SPEC verifies finite source semantics/identity/constraint/transaction/selector boundaries, then fresh QUALITY tests meaningful adversarial controls, including REAL projection deletion, self-parent guard deletion, sequence consumption and referenced batch replacement. Resolve findings before root accepts.

### Full-goal continuation

This component is not the full Tasks20 runtime. Implement existing8kind target writers; convert all8+12current consumers, callback and source-ID lookups, coupled retained child FKs and source initializers without dual writers. Validate copied-DB forward/current writes/current-fact inverse/true predecessor writes/reforward, exact current rows/schema/allocator baselines, lease/CAS recovery, Workbench filesystem and feedback lifecycles. Real Codex CLI uses fake outbound only, with UI/business-result/receipt proof. Complete remainingActions/Business/Emails/Provenance/retirements to10core+1Emailledger, CI and approved production publication. Keep the full approved design goal active until all those facts are true and verified.
