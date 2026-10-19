# State constructor proposal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development with sequential fresh SPEC then QUALITY.

**Goal:** Prepare exact Store/Email constructors for the accepted State declaration without recreating any retired native State table.
**Architecture:** The proposal after the70-method Store caller candidate converts only existing constructor/manifest/marker paths. It reuses the existing exact-table known-missing-object comparison for the two dispatcher indexes already repaired by the native constructor. Source selection reads the retained Email ledger first: Store19/Email51 remains a native source that must be migrated by the paired release. Fresh/current Store markers live in State. Archived native factories remain literal source contracts.
**Tech Stack:** Python3.13.12, SQLite3.50.4.

The complete executable candidate is the adjacent2026-10-16-state-constructor-proposal.patch, applied with git apply --unidiff-zero AFTER the DAO,lifecycle,mixed23 Store proposals. Each existing method/import/declaration delta is included there; no application file has been edited. Hashes and disposable root preflight are in ../specs/2026-10-16-state-constructor-proposal-readiness.json.

Files modified by that proposed patch:
- app/store.py: retire14 native State table DDL,4 indexes,4 ALTER blocks, legacy session synthesis and unused _migrate_known_task_schema; create State only for a validated fresh source; preserve current missing dispatcher index repair and all unrelated bootstrap behavior; convert constructor marker reads/writes, project-evidence marker and removed-runtime key deletion; require exact State in manifest/cache.
- app/action_email_schema_release.py: canonical State metadata reference, narrowly permitted existing index repair predicate and ledger-first constructor source selection only. Actual paired publication forward/inverse is NOT converted by this patch.
- app/email_current_schema.py: current runtime factory excludes the two State families; archived native _CURRENT_TABLE_SQL and create_current_email_non_action_schema remain unchanged.
- app/email_store.py: current shape validation requires State and excludes retired native State families from native required-table maps. Other current Email State consumers remain a separate required closure.

Root disposable preflight reconstructed the exact70-method base, applied all four candidates in an owned temporary app tree, ran real Store/Email constructors in both orders and reopened each pair. Each produced67 ordinary tables (72 including FTS virtual/shadow), no sixteen native State source names, and repaired a missing terminal index while retaining exact lease rows. No provider/native/network invocation occurred. This does not establish existing-data migration, publication, complete runtime caller behavior or final11-table completion.

- [x] Prepare complete four-file patch and compile all candidates.
- [x] Run owned synthetic fresh constructors in both orders/reopens and existing index-repair row preservation.
- [ ] Correct and independently accept State point INSERT/ORREPLACE default-clock foundation before treating caller closure as accepted.
- [x] Add complete executable regression code for every changed constructor/marker/manifest behavior before application edits, including wrong-owner index namespace, current malformed State/extra source names/TEMP shadows, native paired-source refusal before any journal/schema/data change, both known missing index repairs, nonrepairable missing State/index refusal and zero synthetic session backfill.
- [x] Independently SPEC then QUALITY review the exact proposal and regressions.
- [ ] Close current Email/direct/version/progress/publication entrypoints and activate the whole State unit together; do not apply these four files in isolation.
- [ ] Verify whole actual database-copy forward/current-fact inverse and current production predecessor cold reads/writes/reforward, then native CLI(fake outbound) and UI flows. Production/main deployment still requires final user approval.

The rooted complete fresh-constructor probe is:
```python
import json,pathlib,sqlite3
from app.store import AutoReplyStore,_INITIALIZED_STORE_PATHS
from app.email_store import EmailStore
from app.state_schema_release import SOURCE_TABLES,state_schema_is_current
from app.state_storage import insert_state
from app.action_email_schema_release import state_current_or_known_dispatcher_index_repair
for order in ("store-first","email-first"):
 path=pathlib.Path(order+".sqlite3")
 if order=="store-first":AutoReplyStore(path)
 EmailStore(path)
 AutoReplyStore(path);EmailStore(path)
 with sqlite3.connect(path) as db:
  tables={r[0] for r in db.execute("select name from sqlite_schema where type='table' and name not glob 'sqlite_*'")}
  assert not set(SOURCE_TABLES)&tables, set(SOURCE_TABLES)&tables
  assert state_schema_is_current(db)
  db.execute("begin")
  insert_state(db,"dispatcher_claim_leases",{"adapter_name":"scheduled","source_id":"42","owner":"owner","generation":2})
  db.commit()
  before=db.execute("select * from state where record_type='dispatcher_claim_leases'").fetchall()
  db.execute("drop index idx_state_dispatcher_terminal")
  assert not state_schema_is_current(db)
  assert state_current_or_known_dispatcher_index_repair(db)
 AutoReplyStore(path)
 with sqlite3.connect(path) as db:
  assert state_schema_is_current(db)
  assert db.execute("select * from state where record_type='dispatcher_claim_leases'").fetchall()==before
 print(json.dumps({"order":order,"tables":len(tables),"state_current":True,"native_state_absent":True,"existing_index_repair_preserves_rows":True}))

```

The recorded table count uses pragma_table_list type='table' with internal names excluded for67 ordinary tables. The probe's older len(tables) expression includes five additional FTS virtual/shadow entries and reports72.

Complete executable regression is the adjacent2026-10-16-state-constructor-regression.py (SHAd9714f726b4a345d334d52e83027cc26b7639978c4a4b8accbb7bdaf67542b0f), copied to tests/test_state_constructor_current.py only when selecting the whole closure. Root disposable four-file candidate with accepted-design point clock implementation passed20 tests in5.50s. Initial seed-commit fixture correction is recorded in the readiness receipt; no app constructor edits occurred.

Revision2 corrects independent QUALITY P2: bootstrap source names now include all State target index names in main, and reject TEMP collisions for all sixteen sources, State and all target index names before any journal/DDL mutation. The complete adjacentpatch and regression are updated. Root214pass5.09s: previous20 plus194 freshnamespace cases. Store candidate7170541ad442ae56253a0847fe15de323f54727f8973ef22fb1b409aa7060fed; patch ef63860c30e4d9d2acce919dcf32e6af1b35b1a5e2d2f1f51cac2590fc5c4849. Otherthreecandidatefilesunchanged. Fresh independent SPEC thenQUALITY re-review required before acceptance; no app constructor activation.

Revision2 accepted as an unselected constructor proposal: independentSPEC214/6.58s and QUALITY214/5.24s plus26 currentadversarial/5.19s and12 formerfresh repros allPASS. Complete currentcaller/progress/publication closure remains required beforeapp activation.
