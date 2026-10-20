# Receipt ownership: latest source, native CLI and current-data inverse

Status: A2 runtime acceptance passed on an owned database copy. Production publication and readback are separate and not claimed here.

The immutable source is the actual deployed b33f331d6492f8172c0c64570c13839add13e069 Store19.1/Email52 online backup, 474185728 bytes. Its SHA-256 is `67e841cea83f123233c7a05e0014af8dae8f25806d9f1100ff1b7d5b844d6d37`. The final merged app tree contains 417 tracked files; its body-free manifest SHA-256 is `2124c8c9385553723dc78b8ff13002f17110b6c71c36701479c3b23aee6512a7` and every file matched before/after acceptance. No source database, business-row archive or trajectory body is committed.

## Latest source transition

Real `prepare_database_schema_release` / publish converted the copy to Store19.2/Email52. Independent comparison proved all original receipts unchanged, non-target tables and Action facts preserved, SQLite types preserved, exactly the planned verified byte-equal result slots replaced by references, public attempt results unchanged, and only the Store marker changed. The original FK multiset remains `[('runs',190042,'tasks',1)]`; zero FK findings is not claimed.

## Real CLI and substitute effect

Task `4536372937125173`, candidate `1030`, Attempt `15707` used real native Codex Consumer and Audit on synthetic local material. The first Consumer result failed `codex_result_invalid`; the existing continuation completed in the same native session, followed by successful Audit. There were three actual calls, all returncode0 with no timeout. Failed and successful logical runs remain distinct.

Evidence is read from each actual runtime attempt's native bounds, not the aggregate run context range. Consumer attempt386175 is0–36, correction386177 is36–50 in session01a15696-9ab7-7563-ad72-fdbf477f73a7; Audit386179 is0–35 in session01a15697-61f7-7a73-b21d-1e1b6a61d78a. All ranges are readable. The only shell command reads canonical AGENTS.md; tools read the synthetic artifact and registered action contract. Audit also enumerated its available tool names. No other tool or external provider was called.

The copy Console was opened on port18766, lifespan disabled, outbound DwsClient calls blocked. Clicking the reviewed execute option persisted selection and pending state. The actual Worker/Orchestrator/SystemExecutor then used an in-memory provider, with Consumer/Audit absent during execution. It completed exactly one send and one verification with one verified attempt/receipt, no additional model call. The business result is3records and100units with a unique synthetic tag. Raw attempt result is SQL NULL; public attempt result equals the one authoritative receipt. The UI displayed done,1/1verified, and the expanded fake provider receipt. Source and clean baseline stayed unchanged; original rows in all11non-internal tables stayed unchanged.

## Current-data inverse and old cold open

After those new facts, the real ResultSchemaRelease inverse restored the complete source declaration. Actual archived b33 Store cold-open read the new done task, selected option, completed execution, all three logical runs, verified attempt, and fake provider receipt. The attempt body was reconstructed exactly from its current receipt. Re-publication/finalize preserved all current stored facts and allocator rows; only the expected marker clock changed. `integrity_check=ok`, original FK multiset unchanged, no copy Console provider call, immutable source hash unchanged. No source database restore was used.

## Tests and review boundary

Implementation frozen focused tests:34passed201.14s. Original five A1 integration cases ran in the exact archived c291 runtime:5passed134.44s; current A2 equivalents are mapped in `business-model-result-reference-validation.md`. Initial component/full-copy evidence and 420-file pre-merge manifest are recorded there. Fresh merged SPEC then QUALITY approved the integration; the current merged flagged-important query test passed1/10.99s and Ruff/diff checks pass.

An expanded historical Email DAO check was stopped after repeated fixture errors. A single-case diagnostic proves its archived Store19/Email51 source tries to jump directly to current19.2; the existing explicit source guard refuses before DAO behavior. The interrupted run (which also hit pytest interruption teardown) is not a green module result. No compatibility bypass or weakened behavior assertion was added; the actual merged query and the supported19.1→19.2 protocol are the checks used here.

## Capacity boundary

On the identical pre-fixture source/target, Actions and indexes occupy169304064→168607744bytes (696320bytes less); Action payload116254389→115824828bytes. File size remains474185728bytes; free pages291→461. No production VACUUM or large total-database reduction is claimed. The purpose is one result owner; A3/B1/B2/C/D remain separate obligations.

Full body-free results: [runtime copy evidence](validation/2026-10-19-business-model-a2-runtime-copy.json).
