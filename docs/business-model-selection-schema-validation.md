# A1 selection references: unselected schema component

This component removes the duplicate selected branch from the complete compact
Actions declaration. It is not selected by Store, Email, a release coordinator,
or a runtime caller. The existing public source factories and default runtime
declaration still include the old selection branch. No production database,
provider, outbound action, or source backup was used by this component's tests.

The source was independently checked in `action_storage.py`,
`email_canonical_storage.py`, `final_action_schema.py`, and
`reviewed_candidate_store.py`. The latter stores `_json(branch)` from the exact
candidate's `decision_options`, with sorted keys, UTF-8 text, and compact JSON
separators. The ownership research was used for navigation, not as an executable
source declaration.

## Physical declaration and API

`action_payloads(..., selection_references=True)` and the matching explicit
keyword on the declaration/create/current-check factories select the new
layout. The keyword defaults to `False`; it is only valid for compact Actions
declarations. `SOURCE_ACTION_DDL`, `SOURCE_COLUMNS`, `PAYLOAD_FIELDS`, default
`action_payloads`, and default `_current_objects` retain their source behavior.

The complete factory is
`model_action_schema.selection_action_objects(reply_variant,
selection_references=True)`. Passing `False` selects the exact frozen compact
source. Both `actual_no_memory_fk` and `fresh_memory_fk` are supported.

| Item | Frozen source | Explicit A1 target |
|---|---|---|
| Selection payload slots | `option_key`, `branch_json`, `status`, `created_at` | `option_key`, `status`, `created_at` |
| Generated `branch_json` column | Present | Absent |
| Physical candidate/review foreign keys | Present | Same |
| Kind codes, public IDs, physical IDs | Existing values | Same |
| Other 60 kind layouts | Existing slots | Same |

The payload CHECK and generated fields read the selected layout. The target
rejects a four-element selection payload; it does not preserve an empty or
reserved branch slot. Owned indexes and triggers retain their exact declarations.

`candidate_option(candidate_json, option_key)` returns the unique matching
object from an already-bound candidate. `selected_branch(db, selection_id)`
takes a public selection ID and follows its physical `candidate_id` foreign key.
It never selects a newer candidate by task or generation. The helper returns an
in-memory branch object and adds no stored branch body.

## Forward, inverse, and byte preservation

`migrate_selection_references(db)` requires an open caller-owned transaction
with foreign keys disabled. It first matches the complete Actions table, index,
and trigger declarations to one supported source. Before rebuilding, every
selection must resolve exactly one option in its bound candidate, and the old
`branch_json` text must equal the existing `_json(option)` representation.
Missing candidates, missing/duplicate options, differing branch content, and
semantically equal but noncanonical branch text all refuse before mutation.

Semantic JSON equality alone is insufficient for byte-preserving inverse:
whitespace, key order, and escaped characters could otherwise disappear.
Noncanonical historical branch text therefore needs its own explicit
disposition; this component neither silently canonicalizes it nor retains a
hidden row/body backup. For accepted rows, the inverse reconstructs the exact
old branch text from the immutable candidate. Only selection JSONB arrays are
repacked; preservation of arbitrary internal JSONB encodings of those arrays
is not claimed. Every other Action's original payload BLOB is copied unchanged.

The rebuild uses transaction-local SQLite TEMP staging. It copies all writable
columns, changes only selection payloads, recreates the exact target table and
owned indexes/triggers, restores all current `sqlite_sequence` entries and their
row IDs, and compares stored values plus SQLite types in both directions. The
preexisting FK findings must be preserved as a multiset: complete report tuples
are sorted before comparison, retaining duplicate finding counts while allowing
SQLite's table enumeration order to change. An unrelated preexisting finding
is neither hidden nor repaired. Existing dependency and TEMP collision
checks are reused. A configs or Email trigger is exempted from the dependency
probe only when its name and SQL exactly match the published declaration;
other external objects undergo SQLite's dependency probe. The caller retains
commit/rollback ownership; a rebuild exception must roll back that schema
transaction.

The returned inverse metadata is exactly `{"reply_variant": "..."}`. It carries
no business rows, branch bodies, selection IDs, historical watermarks, or source
database snapshot. `restore_selection_references(db, metadata)` accepts the
exact target declaration and derives old branch text for **all current
selections**, including those inserted after forward migration. It rebuilds
the frozen source declaration and preserves the current allocator state.
Forward → new selection → inverse → forward is tested for current-fact equality.

## Verification

The default factory fingerprints were captured before implementation using the
repository's existing `_fingerprint` function and remain pinned in the test:

| Declaration | SHA-256 |
|---|---|
| Default six-kind factory | `21a82e3a36ed0cc4043cb52f91346b9be3aa116d086d8e28b0dcc8b03f98a5ec` |
| Complete compact actual source | `a72296e1cf7c55dda83b63cff6e1760bfe6ffeb09226450225f100f40501ec66` |
| Complete compact fresh source | `e57ae120f24d6ab8aa6e8b5d9825101f438222bfbbbf14060a851d2242fe4245` |

The first test run was RED: 21 failures from the missing target keyword/module.
The first executable run exposed a synthetic run identity collision, which was
corrected by assigning distinct proposal revisions. A later test incorrectly
expected negative zero to survive an existing writer's INTEGER affinity; its
fixture now injects a valid encoded negative zero and asserts raw encoded-byte
preservation. These were fixture corrections, not migration behavior changes.
The corrected target-constructor and forward test subset passed 4/4.

Final focused verification uses `/Users/derek/miniforge3/bin/python`, SQLite
3.53.4, and pytest 9.0.2:

```sh
/Users/derek/miniforge3/bin/python -m pytest -q -n 4 \
  tests/test_model_action_schema.py tests/test_action_storage.py \
  tests/test_final_action_schema.py::test_public_constructor_selects_complete_final_declaration
```

The component tests use explicitly constructed in-memory old-source databases,
not a Store that might choose the target implicitly. They cover both source
variants, exact source mismatch with no mutation, approve and stop choices,
an older bound candidate with a newer candidate present, malformed selection
bindings, noncanonical old branch bytes, new current selections, physical and
public IDs, execution/selection FKs, unrelated BLOB/encoded scalars, all sequence
watermarks, target array shape, caller transaction ownership, quick-check, and
preservation of an unrelated existing FK finding.

Before the SPEC dependency correction below, the focused command passed
**78 tests in 158.83 seconds**, exit 0. This
includes all 25 new component cases, the frozen historical Actions regressions,
and both existing complete-constructor variants. Ruff, AST parsing, and
`git diff --check` also passed for the component.

SPEC review reproduced an external-dependency hole: the original component
exempted every trigger owned by `configs`, including an added trigger reading
`actions.branch_json`. Such a trigger survived forward migration with a missing
column. The correction now obtains the published configs trigger declarations
from the existing `configs_schema_transfer._target_declarations` factory and
matches both name and SQL, as the Email guard handling already does. No runtime
policy or declaration changed.

Ten new cases cover both source variants: an unknown dependent trigger and a
known trigger name with changed SQL must refuse before mutation in both forward
and inverse; exact published guards and an unrelated extra trigger must survive
both directions unchanged. The refusal checks compare Actions, configs objects,
sequence state, and the absence of TEMP staging. Their first run was **8 failed,
2 passed in 97.50 seconds**; all eight failures were `DID NOT RAISE ValueError`.
After correction, these ten cases, the frozen source fingerprint case, and the
affected historical dependency/namespace/preservation cases passed **26 tests
in 165.54 seconds**, exit 0. Ruff, AST parsing, and scoped diff checks also passed.
The earlier 78-case suite was not repeated for this narrow dependency correction.

QUALITY review found that comparing the ordered `foreign_key_check` output could
reject unchanged facts after rebuilding Actions. Four new cases cover forward
and inverse under both source variants. Each retains one Actions finding and
two identical findings from an unrelated `WITHOUT ROWID` table, and proves that
SQLite actually reorders its reports while the complete multiset, duplicate
counts, and stored rows remain unchanged. Before the correction, all four failed
at the migration's ordered comparison in **30.71 seconds**. Comparing sorted
tuples now preserves every finding without depending on report order.

After this correction, the four new cases, both existing unrelated-FK cases,
and the frozen factory fingerprint case passed **7 tests in 33.38 seconds**,
exit 0. Ruff, AST parsing, and scoped diff checks also passed. The earlier
78-case and 26-case commands were not repeated for this two-comparison change.
Declarations, default factories, migration APIs, and inverse metadata did not
change in either review correction.

## Remaining activation work

The caller stage must explicitly choose this target in the versioned paired
release and cold/fresh constructors, replace selection writes with the shorter
layout, and derive branch objects at existing read boundaries. The current
`CANDIDATE_SELECTION_SQL`, runtime writers/readers, and Store/Email versions still
select the old contract. Verified production-copy forward/inverse and release
readback belong to the parent integration stage. No deployment or live
behavior claim follows from this unselected component.
