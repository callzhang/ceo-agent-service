# Integrated State runtime selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development with one implementation writer and fresh SPEC then QUALITY. Steps use checkbox syntax.

**Goal:** Select all independently accepted State callers, constructors, publication, paired inverse and progress bindings together in the isolated feature branch.

**Architecture:** Apply the exact ten application and four test proposals in the order frozen by the whole-State harness. Copy the two canonical regression modules into tests. Replace the bounded State behavior documentation with the complete selected-runtime description. No production/main/push/deploy or real provider operation is part of this task.

**Tech Stack:** Python3.13/SQLite3.50, existing State/API declarations, pytest.

---

### Task 1: exact source selection

**Files:** the20 app paths and9 test paths named by the14 patches in `2026-10-16-state-whole-integration-preflight.py`; new tests/test_state_constructor_integration.py and tests/test_state_progress_binding.py; docs/architecture.md and docs/runtime-mechanism.md. Source schema/data fields and pure State declarations are unchanged.

- [x] Independent component SPEC/QUALITY completed for Store70, Dispatcher19, four-file constructor, Email9, direct13/publication10, paired7 and committed-progress binding. Exact receipts and hashes are adjacent.
- [x] Root whole composition: all350 application modules compile;493tests passed188.73s using exact14patches and original conftest/static registries. Owned temp removed.
- [x] Claim exact app/test/document files before editing; verify no unrelated app changes. The committed source is a58a926d plus docs-only06518d42 before these source changes.
- [x] Apply this complete command from the isolated worktree:

```python
import ast
from pathlib import Path
import shutil
import subprocess

root = Path.cwd()
plan = root / "docs/superpowers/plans/2026-10-16-state-whole-integration-preflight.py"
tree = ast.parse(plan.read_text())
patches = ast.literal_eval(next(
    node.value for node in tree.body
    if isinstance(node, ast.Assign)
    and any(isinstance(target, ast.Name) and target.id == "PATCHES" for target in node.targets)
))
assert len(patches) == 14
for name in patches:
    subprocess.run(["git", "apply", "--unidiff-zero",
                    str(root / "docs/superpowers/plans" / name)], check=True)
for source, target in (
    ("2026-10-16-state-constructor-regression.py", "test_state_constructor_integration.py"),
    ("2026-10-16-state-progress-binding-regression.py", "test_state_progress_binding.py"),
):
    shutil.copyfile(root / "docs/superpowers/plans" / source, root / "tests" / target)
subprocess.run(["python3", str(root / "docs/superpowers/plans/2026-10-16-state-integration-documentation.py")],
               check=True)
```

The publication marker regression is created by its test patch. All other test changes are exact accepted fixture adjustments. The progress patch deliberately has3contextlines; it must remain contextual so its insertion follows publication's import offset correctly. Do not regenerate any proposal or improvise fixes during mechanical transfer.

### Task 2: documentation

- [x] Execute the complete adjacent `2026-10-16-state-integration-documentation.py` in the command above. It replaces only each bounded State subsection and updates the explicit Reply selector paragraph; unrelated behavior stays intact. Full replacement text is embedded in that script.
- [x] Confirm no remaining unselected/unconverted claim in those State sections, while historical acceptance/source-preparation records keep their original evidence boundaries. This task selects the isolated branch only; production remains unmodified.

### Task 3: verification and exact transfer

- [x] Compile all application modules without bytecode creation.
- [x] Run the root whole-State harness in applied-source mode after root adds that switch, with pinned Python, original conftest and owned temporary pytest databases. It must copy the now selected code and never apply the patches twice.
- [x] Run Ruff on modified Python files. Do not introduce global formatting or alter frozen source SQL to silence a lint message.
- [x] Compare selected app/test module bytes with a disposable reconstruction of the exact14patches plus canonical regressions. Constructor patch preserves28 pre-existing proposal blank-line indentation warnings; report these honestly if git diff --check flags them. They are not a runtime/code change.
- [x] Commit only this task's files and exact behavior docs. Preserve root's simultaneous plan/acceptance artifacts and claims in shared files. No main merge, push or production action.

### Task 4: review and next required evidence

- [x] Fresh independent SPEC for exact transfer, docs and selected493matrix; then fresh QUALITY for integration-specific failures and behavior.
- [ ] Root actual DBcopy forward/current-fact inverse, genuine latest predecessor cold read/write/reforward, native Codex CLI with fake outbound and UI flows. These are subsequent required acceptance; current493 tests do not prove them.
- [ ] Continue remaining source families until exact10core+Emailledger; State selection alone is not final table-count completion.


Implementation selected at `7ef171fecc82ba76e8edadc55da0289db7c13622`: applied-source493passed200.15s;350modules compiled and Ruff31passed. The only exact-transfer deviations are the three authorized import cleanups. Fresh independent SPEC reconstructed all33paths and passed77focused cases39.31s. Fresh QUALITY and current production-copy/genuine adf0ee88 predecessor proof are in progress. Known28 constructor blank-line whitespace warnings remain cosmetic and explicitly recorded. Production remains unchanged.

Fresh independent QUALITY accepts51existing cases56.62s plus4independent constructor/cache/index-history probes3.57s. No actionable finding. Exact selected-source acceptance is `../specs/2026-10-16-state-runtime-integration-acceptance.json`. Actual-copy proof is separate and still running at this record.
