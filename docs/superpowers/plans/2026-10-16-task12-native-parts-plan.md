# Task12 native INSERT and SET parts implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: subagent-driven-development for mechanical exact transfer, fresh SPEC then fresh QUALITY.

**Goal:** Preserve actual Email single native UPSERT, implicit DML transactions and whole-statement multirow SET/DELETE while moving remaining source families into Tasks.

**Architecture:** Extract existing insert preparation unchanged into Task12InsertParts; the existing point writer executes exactly those parts. Remove the unselected helper-only precondition requiring explicit BEGIN; retain the existing zero-row UPDATE allocator lock, which uses SQLite implicit DML BEGIN when needed and never commits. Add pure payload-only finite SET parts using the accepted exact scalar codec. Callers retain SQL verb, conflict clause, WHERE, CTE/FROM, cursor and context-owned commit/rollback. Physical identity/parent mutation remains explicit at existing point writer/caller, not silently interpreted as payload. No new ordinary table, business field, transaction wrapper, parser, audit/policy, fallback or dual writer.

**Tech Stack:** Pinned Python3.13.12/SQLite3.50.4, independent frozen actual/fresh source declarations, existing Task12 native allocator and Tasks20 declaration, pytest/Ruff.

## Source evidence and root cause

The complete evidence is 2026-10-16-task12-email-caller-proposal-analysis.md/.json at8f64fba87. Native historical setter and offline insert/close/purge/failure completion rely on implicit BEGIN; the helper-only guard rejected before SQL. The guard is not a production business rule. Zero-row UPDATE is already the native allocator lock, so removing the rejection keeps native DML transaction ownership with the existing connection context. Historical enqueue already owns BEGIN IMMEDIATE; a single UPSERT must keep hidden source ROWID and old retry state while updating exactly five existing fields. Point insert/update splitting or REPLACE loses that behavior. The caller does not consume its cursor/rowcount/lastrowid. Classification retry/purge are multirow, and claim updates counters using old-row RHS; pure fragments preserve those native statements.

## Complete canonical transfer

- `2026-10-16-task12-native-parts-candidate.py` → `app/task12_storage.py`; SHA256 `8a7a84db53006065697ae44a2e39dc27a2008ca2687b6ddfc54e23ff9cc1729a`.
- `2026-10-16-task12-native-parts-existing-regression.py` → `tests/test_task12_storage.py`; SHA256 `75010ec38557551986151ee70dd51671192accfce556f9b011ed103584eb50b7`.
- `2026-10-16-task12-native-parts-regression.py` → `tests/test_task12_native_parts.py`; SHA256 `8ca3da44a77e1d52e24b06ea17cba6537a3e4b965d0a655ffbaef35caeb9ecfa`.

Preflight 2026-10-16-task12-native-parts-preflight.py copies all code/tests under its owned /tmp tree then removes it. Default uses exact complete candidate and changed old regression; --applied uses installed app/tests. --with-declarations runs focused new44 +old239 Task12+276Task8+325declarations. --before-repair restores only the original _lock guard in the disposable candidate; meaningful RED selects implicit tests, no missing-API collection errors.

Application AST scope: existing _lock and insert_task12 only; new Task12InsertParts/Task12SetParts/task12_insert_parts/task12_set_parts. Every other original function/class AST identical. The old test-only explicit transaction rejection is replaced by native implicit transaction and caller rollback proof; no other old test changes. Source defaults/constraints/types/identity, native FULL semantics, public rowids, declaration fields and current callers remain unchanged.

### Task 1: complete source differential before application edits

- [x] Complete exact candidate, both regression artifacts, owned preflight before app edits.
- [x] New44actual/fresh cases pass3.47s, owned temp removed.
- [x] Original-lock regression RED28fail/16deselected1.84s; actual calls fail on old ProgrammingError after successful native controls, no collection/setup failures.
- [x] Full884focused candidate52.71s and OWNED_TEMP_REMOVED True; ASTdelta exact two existing/four new, Ruff clean. Final formatting AST-identical; applied transfer will repeat pinned suite.

### Task 2: mechanical exact implementation

- [ ] Claim only app/task12_storage.py, tests/test_task12_storage.py, tests/test_task12_native_parts.py, own claim and this plan implementation checkboxes.
- [ ] Assert hashes above; copy exact3canonical files. No code redesign or current caller/constructor/manifest/migration selection.
- [ ] Run pinned `PYTHONDONTWRITEBYTECODE=1 /Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python docs/superpowers/plans/2026-10-16-task12-native-parts-preflight.py --applied --with-declarations`.
- [ ] Ruff --no-cache on exact3paths, ASTscope check and git diff --check. Commit only own hunks/files. No production DB/provider/nativeCLI/network/main/push/deploy.

### Task 3: sequential independent review

- [ ] Fresh SPEC: original finite source declarations; old helper factor exact; native implicit transaction insert/update/delete all12 where applicable; no commit or new BEGIN; full bulk RHS/CAS/rowcount and statement ABORT rollback, prior work; source retry state/ROWID preserved by one actual UPSERT; no invented field or structural identity packing; omitted defaults final statement unchanged; no current runtime selection.
- [ ] Fresh QUALITY: control/mutant/control in owned copies. Catch original guard, eager commit, split/per-row bulk ABORT, changed old-RHS counters, wrong five-field conflict copy/payload outer container, sourceROWID replaced, exact float arithmetic json_quote rounding; valid controls before meaningful mutation. No setup failures counted as kills.
- [ ] Root records exact acceptance/hashes/remaining caller selection boundary; unselected application behavior does not change architecture/runtime contract docs.

## Required caller continuation

Email proposals must use exact task12_projection source shape (including hidden source ROWID where diagnostics require), preserve public task_id strings versus globalphysical IDs, binding order, owner/generation/status CAS, current JSON parsing/input immutability and done-only compaction. Historical native conflict target is generated key columns with exact partial predicate record_type='email_historical_candidates', SET RHS uses decoded excluded.generated source leaves encoded once by ceo_native_json. Only fields normalized_text/provider_message_json/uidvalidity/uid/updated_at change on conflict. INSERT parts expose structural physical id and source rowid separately; raw returned cursor.lastrowid is physical and must not be advertised as public source rowid. Existing point writer returns its existing source-shaped result.

Both Store and Email _open_connection registration remains part of COMPLETE Tasks20 cutover, inherited by Workbench; this component does not activate hooks. Source12 remaining caller groups, retained feedback endpoints, constructors/fresh/current manifest, copied nonempty forward/current-fact inverse/genuine predecessor writes/reforward, real CLI fake-outbound/UI/business results/receipts and final10core+Emailledger remain open. Latest actual-copy67tables is not final11. No main merge/push/production write/restart/deploy without final human approval.
