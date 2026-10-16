# Main-schema Tasks allocator qualification implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: subagent-driven-development for exact mechanical transfer after canonical SPEC then QUALITY. No caller or runtime selection is authorized by this prerequisite.

**Goal:** Preserve native Tasks allocation and current-fact inverse when an unrelated TEMP AUTOINCREMENT table creates `temp.sqlite_sequence`.

**Architecture:** Qualify exactly four non-trigger SQL statements in three existing functions with `main.sqlite_sequence`. Retain all native trigger bodies, signatures, affinity/scalar behavior, transaction ownership and declarations. Do not reject unrelated temporary tables or add a runtime guard.

**Tech Stack:** Python3.13.12, SQLite3.50.4, focused source-versus-candidate pytest on disposable code.

## Confirmed failure and complete canonical contents

QUALITY reproduced an actual/fresh source with Tasks watermark700: forward and inverse both returned normally, but an unrelated TEMP AUTO table redirected the published counter into TEMP; inverse restored absence instead of700. The same fixture without TEMP restored700. Root's original-helper run adds eight true behavioral failures, including `(physical_id, public_id)=(1,1)` or `(9001,7001)` instead of native/main `(701,100)`. Its native main-schema control itself succeeds.

Complete future file contents already exist before application changes:

- `2026-10-16-task20-main-sequence-definitions-candidate.py` → `app/task20_definitions.py`. Only `initialize_task8_sequence` changes these statements:

```python
db.execute("delete from main.sqlite_sequence where name='tasks:published'")
db.execute(
    "insert into main.sqlite_sequence(name,seq) values('tasks:published',?)",
    (source_water,),
)
```

- `2026-10-16-task20-main-sequence-storage-candidate.py` → `app/task12_storage.py`. Only `_physical_next` and `_auto_next` change their respective sequence reads:

```python
seq = db.execute("select seq from main.sqlite_sequence where name='tasks'").fetchone()
seq = db.execute(
    "select seq from main.sqlite_sequence where name=?", ("tasks:" + kind,)
).fetchone()
```

- `2026-10-16-task20-main-sequence-regression.py` → `tests/test_task20_main_sequence.py`, byte-identical. Four test bodies each run with an ordinary TEMP sequence and conflicting TEMP sequence labels. They check initialization, deleted-high physical/public IDs, unchanged TEMP facts, existing transaction ownership, native AUTO control, and MAIN-trigger schema-local resolution.
- Four new actual/fresh canonical release regressions exercise the complete source-bound forward/inverse with unrelated TEMP sequence and conflicting labels. All original82-column facts, source IDs and all six original counter streams stay exact.
- `2026-10-16-task20-main-sequence-preflight.py` copies code to owned `/tmp`, applies exact canonical files, runs eight prerequisite plus176 release tests and removes all owned code in `finally`. `--source` deliberately omits the fix and runs the eight failure regressions. It never edits source app files.

## Execute and review

- [x] Reproduce original helpers with the new executable regressions: eight assertion failures after successful source/native controls. No import/setup failure credited; owned temporary removed.
- [x] Apply the exact four SQL literal qualifications in complete canonical copies. Keep `_definitions` AST and resulting table/index/trigger SQL exactly unchanged; SQLite MAIN trigger bodies already resolve their own schema and cannot use qualified DML targets.
- [x] Root184/7.98s and fresh SPEC184/9.15s pass; run the complete updated184-case copied preflight and source/candidate AST/hash/Ruff checks.

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python -B docs/superpowers/plans/2026-10-16-task20-main-sequence-preflight.py
```

Expected:184pass and `OWNED_TEMP_REMOVED True`. Preserve the old172/copy receipts as their original scoped evidence; new helper hashes must be recorded for new proofs.

- [x] Fresh SPEC184/9.15s then fresh QUALITY184/7.81s accept exact prerequisite and component; four supplemental full actual/fresh TEMP counter roundtrips pass. Fresh independent SPEC then fresh QUALITY must accept these precise changes and complete component. Resolve findings canonically before application.
- [x] Exact unselected applicationdfbfc07bd transfers complete canonical helpers/component/tests. Mechanically copy both complete canonical files and the eight-case regression to the three exact destinations; verify byte hashes, only three changed function ASTs/four SQL literals, all signatures and generated declarations unchanged. Run the installed eight-case regression plus176 unselected canonical component cases.
- [x] Committed own hunks and both behavior paragraphs; fresh installed SPEC184/8.62s then QUALITY184/7.83s PASS. Refreshed actual copy56.058s with installed hashes and conflicting TEMP counters PASS.

After this prerequisite passes, refresh the frozen nonempty component proof with both exact corrected helper hashes and an unrelated TEMP AUTO witness. This remains separate from complete current constructor/caller/paired release selection. Do not turn184 tests or a standalone116→104→116 component copy into coupled55/final11, production, genuine archived predecessor or native CLI acceptance.

## Exact combined unselected application scope

Once both canonical reviews pass, application may be one coherent Tasks20 helper commit: the two exact qualified helper modules, new `app/task20_schema_release.py` copied from the complete release canonical, `tests/test_task20_main_sequence.py` copied byte-for-byte, and `tests/test_task20_schema_release.py` copied from `2026-10-16-task20-release-installed-regression-candidate.py`. The latter changes only test root depth and imports the installed release module; its test bodies and source fixtures remain the same176 cases. This is no current caller/constructor/release selection. Run the two installed files together (184cases), then fresh SPEC followed by QUALITY on the installed scope.

Add the following paragraph immediately after each of `docs/architecture.md` and `docs/runtime-mechanism.md`'s title in the same application commit:

> The unselected Tasks20 schema component converts the original eight-kind Tasks container and twelve native task tables under a caller-owned FK-off transaction. It preserves existing physical Tasks IDs, source public keys/ROWIDs, input pointers, scalar storage classes and independent source counters. Its current-fact inverse rebuilds all82 original columns, reconstructing unrelated padding as SQL NULL, plus exact native declarations and the retained feedback child's three endpoints. Inverse metadata contains declarations and counter-presence bits only. Non-trigger allocator accesses explicitly use main.sqlite_sequence, so unrelated TEMP allocators cannot redirect them; MAIN trigger SQL remains unchanged. Current Store/Email/Workbench constructors and the paired release do not yet select this component. The ten-core-plus-ledger target remains incomplete and production is unchanged.

No prior paragraph may be rewritten by the mechanical transfer. Root owns subsequent full constructor/caller/publication integration and proof.
