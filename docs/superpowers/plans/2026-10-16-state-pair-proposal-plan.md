# Paired State migration and inverse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development for independent SPEC then QUALITY after root's executable prototype. Steps use checkbox syntax.

**Goal:** Extend the existing unpublished Store19/Email51 paired release to carry exact State declarations, remove native State tables atomically and restore current State facts on inverse.

**Architecture:** State joins the existing Email/Business/Provider/Configs atomic transaction. Prepared metadata carries only the frozen State schema declarations, not State row snapshots. Forward converts State after Configs and before the shared Store/Email marker timestamp. Current verification explicitly reads the State service marker; inverse restores State first so subsequent original marker restoration operates on native service_state. Historical source verification stays native. This proposal is unselected until progress entrypoints and the complete current consumer closure are ready.

**Tech Stack:** Python3.13/SQLite3.50, existing State migration/inverse primitives, pytest.

---

### Task 1: exact paired coordinator proposal

**File:** `app/action_email_schema_release.py`, after the accepted four-file constructor proposal.

Base SHA25610119b5f9745b9c579d4cb9b40b5ae0dc794238b4ff071dca2ae05671eb2dd89.
Candidate SHA256c72b22ab4471a39567607b6842b50bc480aa03e37b8a888bc248170f1aa94f10.
Complete executable source delta: `2026-10-16-state-pair-proposal.patch`, SHA25698244c0e6e899dba59aa9de4c5f3c94790d8caa083ed4f93e6c17c1fa5a9bde0.

| Existing function | Complete intended change |
|---|---|
| _markers | Add explicit keyword current=False. Current role requires exact State declaration and reads state_projection(service_state); source role reads main native service_state. Ledger query is unchanged. No failed current read retries native. |
| _inverse_metadata | Carry source['state'] exact declaration metadata alongside existing metadata; no row bodies. Existing cold payload calls this same function so it includes State automatically. |
| ActionEmailSchemaRelease._verify_source | Capture current native State source declarations and compare in the same existing complete inverse-metadata comparison; original marker/ledger/integrity checks unchanged. |
| ActionEmailSchemaRelease._verify_target | Use explicit current marker role, which validates State before reading it. Existing action/business/provider/config/external-trigger/FK checks stay unchanged. |
| ActionEmailSchemaRelease.apply | After migrate_config_schema, call migrate_state_schema with prepared State metadata. Write existing Store marker through update_state_rows with original bound key/value/time. Shared Python now value remains the same timestamp used by the Email ledger insert. All original transaction boundaries and exception rollback remain. |
| ActionEmailSchemaRelease.rollback | Determine declared main State role before any native marker query. Native source role runs original complete source verification and commits idempotently. Current role verifies target, restores State first, then original Configs/Business/Email/Provider inverses and original native version/timestamp/ledger restoration. No inverse snapshot replay. |
| prepare_action_email_schema_release | Capture native State metadata in the existing read-only prepare transaction. Original target-pair validation, source markers and integrity remain. |

Only these seven existing functions and required State imports change. The dataclass fields, exposed release API, version constants, current constructor selection, cold subprocess mechanics and all unrelated functions remain unchanged. Pure State source fingerprints, target DDL, source eligibility and current-fact restoration are reused without modification.

- [x] Root exact source/protocol inspection and independent bounded fixture mapping.
- [x] Complete executable proposal before runtime application edit.
- [x] RED control: adapted source fixture and target/metadata assertions against original coordinator yield4 expected failures in8.48s (missing State target and missing captured State metadata).
- [x] Corrected prototype28PASS117.70s across both native variants, including forward/inverse failure injection, current State facts with signed ROWIDs/BLOBs through inverse/old writes/reforward, BLOB original marker timestamp/cold payload and existing repeatable inverse/target damage cases.
- [x] Final durable artifact32case reproduction including source/current State declaration drift:32PASS149.95s, owned temporary removed.
- [x] Independent fresh SPEC, then QUALITY: SPEC32/132.75s, QUALITY32/132.11s plus10confined cases51.41s.
- [ ] Implement/activate only with Store/Email/direct/publication callers and explicit progress bindings; never select this proposal alone.

### Task 2: complete test fixture and regression proposal

**File:** `tests/test_email_action_schema_release.py`.

Complete executable changes: `2026-10-16-state-pair-test-proposal.patch`, SHA2561f8e6b1446fc72b1ba1f4525eb4fdaac3c2eba849df4af567fba533058d65d9e.
Candidate SHA256bc52a38e7caa6f36ec6f451da59070545b3dc64d039506cd611447301f6b142e.

The synthetic source fixture now installs exact frozen actual/fresh State declarations for all15 retained kinds plus corpus_sources before its original Store marker insert. This replaces its incomplete service_state scaffold and the two cursor tables supplied by an archived factory. Frozen metadata and archived native factories remain untouched. The existing test marker helper explicitly reads source or current relations. Publication asserts exact State and retirement of all native State names. Prepare expected metadata includes captured State declarations. Target-only marker/tamper assertions use State; original source and inverse-restored marker reads/writes remain native. The target tamper fixture starts its own transaction before the State primitive; this corrects a test setup omission without weakening State's transaction contract.

New tests contain complete executable bodies in the adjacent patch:

- Inject failure after the full State forward conversion; compare complete source database image before and after.
- Inject failure after the full State inverse reconstruction; compare complete target image before and after.
- Write current State message/service facts with ROWIDs0/-7 and BLOB values, inverse, verify native storage classes and identities, write native old-schema facts at -8/0, reforward, and verify all current facts/identities/types.
- Preserve a BLOB original Store marker timestamp through serialized cold payload and apply/inverse/reforward; payload has declaration metadata only.
- Change native State source index or remove current State index; reject forward/inverse without changing the respective full database image.

Original unrelated pre-existing FK finding and all existing business/Email/provider/config assertions remain. These tests use synthetic data and the original conftest isolation; no provider or real CLI is called.

### Task 3: executable confined verification

Complete harness: `2026-10-16-state-pair-proposal-preflight.py`.
It creates one owned temporary copied app, carries original conftest and required fixture imports, applies six exact reviewed/proposed patches, runs only nine selected paired test functions (32parameterized instances), and verifies temporary cleanup.

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python docs/superpowers/plans/2026-10-16-state-pair-proposal-preflight.py
```

Root's earlier22instance prototype yielded20PASS plus2fixture transaction failures; those are not accepted as a green result. The corrected28instance run passed before adding the4 declaration drift instances. Final artifact result and review evidence are recorded separately.

### Task 4: subsequent required integration boundary

The existing deploy/updater/CLI progress DAO still needs explicit source/current binding at entrypoints and after committed child migration/inverse. Store coordinator current marker detection must also be converted. Those are separate code changes, not fulfilled by this paired component. After whole-State selection: actual-copy forward, current facts/inverse, real current production predecessor cold read/write/reforward, fake-outbound native CLI and UI flows, then remaining families to exact10core+Emailledger. No production/main merge or deploy without final authorization.

Final exact six-patch disposable harness passed32instances149.95s, including both source/current State declaration drift cases acrossactual/fresh. Fresh SPEC and QUALITY are accepted; exact evidence is ../specs/2026-10-16-state-pair-proposal-readiness.json. This is prototype evidence only, no actual-copy/runtime acceptance.
