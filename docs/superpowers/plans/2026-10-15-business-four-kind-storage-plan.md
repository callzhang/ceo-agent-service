# Four-kind Business Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one unselected, source-shaped read/write storage unit for all 42 existing Task, Project, Anchor and Project context revision fields, with their four independent native public-ID allocation streams.

**Architecture:** Two finite modules declare `business` and implement source-shaped single-row insert/update/delete under the caller's transaction. Common actual columns remain typed physical values, other source values use the existing tagged SQLite JSON codec and typed generated columns, and native composite FKs couple each existing public source FK to its new physical typed endpoint. No constructor, caller, version, migration or runtime flag selects these declarations.

**Tech Stack:** Python `sqlite3`, existing `app.action_storage` and `app.action_email_definitions` codecs/ID helpers, SQLite generated columns/composite foreign keys/partial indexes, pytest, Ruff.

---

## Accepted boundary and evidence

This plan is based on clean worktree HEAD `42e15275` and the accepted
`docs/database-redesign-business-core-target-field-map-20261015.md` sections 5
and 7. Literal source authority is
`docs/superpowers/specs/2026-10-15-database-redesign-business-core-native-source.json`,
with the existing native oracle in `tests/test_business_core_source.py`. Its
published source binding remains `7dadc492c1c62d5b0624f2f97dd0c89f4f358646`,
Store19/Email51. This unselected unit requires no fresh production read or
published-predecessor rebinding; that is a later release task.

The coherent unit is exactly `business_tasks` (19), `business_projects` (12),
`business_anchors` (6), and `business_project_context_revisions` (5). Implement
all declarations, reads, writes and allocation together before committing the
functional unit. Source has 23 CHECK expressions, seven indexes, zero owned
triggers, and three internal FK groups/four source FK rows. The target has ten
indexes: seven translated logical indexes plus three structural public/typed
identity indexes. Four target BEFORE INSERT triggers implement public allocator
mechanics, not a new business policy or a source immutability rule.

`app/task_business_resolution.py:178-205,326-421` and
`app/task_agent.py:1713-1812,2532-2536` establish why these four kinds belong in
one unit. Actual plain INSERT/`lastrowid` writers are
`app/store.py:5835` (Task), `app/store.py:6525` (Anchor),
`app/store.py:6571` (Project), and `app/project_context_service.py:93`
(context revision). The existing list/revision reads use public IDs for
ordering, including Project revisions ordered by `project_id,id DESC`.
No current four-family `INSERT OR REPLACE` use was found by
`rg -n -i 'insert or (ignore|replace).*business_(tasks|projects|anchors|project_context_revisions)|update or ignore.*business_' app`.

The supported interface is finite: insert with optional IGNORE, source-shaped
single-row update with optional IGNORE, delete, and actual/fresh projection
constants. It does not translate arbitrary source SQL, expose REPLACE/upsert,
or promise raw target-table SQL compatibility with the retired source layout.
Internal target `RETURNING` obtains the successful public identity only; the
API follows the actual plain source writer's allocation/transaction behavior.
Ignored inserts return `BusinessWriteResult(None,0)`, avoiding stale native
cursor `lastrowid`; successful inserts return the source public integer.

### Settled value and identity representation

- `business.id` is a positive physical AUTOINCREMENT identity. It is independent
  of each source public namespace. `legacy_key` is the canonical decimal text
  of the normalized signed 64-bit public integer; generated `public_id` is its
  INTEGER projection. `(record_type,public_id)` is unique. Explicit zero,
  negative, numeric string/float/bool assignment, NULL/NaN allocation and
  deleted high water follow the native source controls.
- `title`, `status`, `created_at`, `updated_at` are the only physical common
  source-value columns. An absent common field for another kind holds empty
  structural text and is absent from that kind's source projection. No Project
  deadline, Task project_id, denormalized title identity or new business fact
  is introduced.
- Every remaining source field occupies its exact fixed payload key. This
  includes existing source public FK values; separate physical references are
  approved structural remaps, not replacement business facts. Existing
  `*_json` values stay exact SQLite TEXT/BLOB values, not recursively parsed
  objects. Reuse `_encoded_sqlite_value`, `_encoded_value` and
  `_encoded_value_check` unchanged. Their tags preserve BLOB, infinities,
  SQL NULL/NaN and bindable scalar values. Do not narrow native legal storage
  to naive JSON scalar types or require context/evidence revisions to be JSON.
- Each noncommon source field has a generated column with the native source
  affinity. Native NOT NULL is a generated-column NOT NULL constraint, with
  an inactive neutral value outside that kind. The 23 original CHECK
  expressions retain their literal enums, Unicode trim characters, JSON-text
  checks and SQL three-valued behavior after explicit identifier translation.
  Common values receive affinity through their physical source types.
- Task merge: `(merged_task_ref, 'task', task_merged_into_task_id)` references
  `(id,record_type,public_id)`. Revision Project uses the corresponding
  three-column coupling. Project Anchor uses
  `(canonical_anchor_ref,'anchor',project_canonical_anchor_id,project_anchor_type)`
  referencing `(id,record_type,public_id,anchor_anchor_type)`. A structural
  CHECK pairs source-NULL with physical-NULL and requires unused pointers
  to be NULL. Non-NULL unresolved parents receive physical zero; positive
  physical IDs make zero unavailable, so native FK checking rejects it after
  ordinary CHECK/NOT NULL/UNIQUE handling. No premature Python FK exception
  or custom FK trigger changes IGNORE or statement-end semantics.
- The public FK value is necessary to enforce native NO ACTION when a parent
  public ID changes even though its physical ID stays fixed. Composite native
  constraints also permit a legal single-statement coordinated Task parent
  ID/child-FK update. Task self-merge compares source public IDs before FK
  checking, so an ignored self-merge is skipped as the source CHECK requires.
- Public allocator names are `business:task`, `business:project`,
  `business:anchor`, `business:project_context_revision` in existing
  `sqlite_sequence`. Fresh public waters are absent. BEFORE INSERT records
  zero for first explicit negative/zero identities, advances on ignored rows,
  and rolls back with a failed ABORT/FK statement. A public PK UPDATE does
  not itself advance the water; the next insert consults both current MAX
  and stored deleted high water. The physical `business` water is separate.
- Callers must begin a transaction before writes. `_lock` takes the SQLite
  writer lock with a no-row sequence update before reading locators/waters.
  Each insert evaluates all omitted defaults in one SQLite SELECT, under that
  lock. Installers and normal writes never commit, release the caller's
  transaction or own a context manager. Caller rollback restores rows and
  public waters together. There is one source-required rollback exception:
  plain source INSERT's public AUTOINCREMENT exhaustion, described next.

### Native exhaustion evidence and statement-shape limit

Planning preflight on Python's SQLite **3.53.4**, with reconstructed accepted
actual and fresh source schemas and `foreign_keys=ON`, observed this complete
16-case matrix. Every entry applies to all four kinds:

| Source variant | Statement shape | Cases | Error | Caller transaction | Earlier uncommitted Task |
| --- | --- | ---: | --- | --- | --- |
| actual | plain INSERT / lastrowid | 4 | SQLITE_FULL | rolled back | absent |
| fresh | plain INSERT / lastrowid | 4 | SQLITE_FULL | rolled back | absent |
| actual | INSERT RETURNING id | 4 | SQLITE_FULL | still active | retained |
| fresh | INSERT RETURNING id | 4 | SQLITE_FULL | still active | retained |

An initial RETURNING-shaped native probe was therefore not a valid control for
these actual callers. The plan's primary native helper uses plain INSERT, and
its independent statement-shape control preserves the distinction. On public
stream exhaustion, `_next_id` rolls back the actual caller transaction then
reuses `_raise_autoincrement_full` to raise SQLite's actual SQLITE_FULL error.
Do not generalize this evidence to another SQLite version, source family or
arbitrary conflict/statement mode; do not add a runtime mode/version fallback.

### Files and responsibility

| File | Action and responsibility |
| --- | --- |
| `app/business_definitions.py` | New finite source constants, generated declaration, seven logical indexes plus structural keys, four allocator triggers, actual/fresh source projections |
| `app/business_storage.py` | New unselected schema installer and caller-owned source-shaped insert/update/delete; reuse existing codec and native ID helpers |
| `tests/test_business_storage.py` | New in-memory exact source-versus-target functional tests and independent native exhaustion controls |
| `docs/architecture.md` | Add the exact unselected-unit paragraph from Task 4 below |
| `docs/runtime-mechanism.md` | Add the same exact unselected-unit paragraph from Task 4 below |
| `docs/agent-claims.md` | Claim these implementation paths before edits; finish only the implementer's own row |

Do not modify the existing codec modules, constructors, source oracle, source
capture, Store/email versions, migrations, release coordinator, provider state,
body readers or existing Task/Project callers. This plan itself does not claim
implementation paths; the executing agent claims them when it starts.

### Future release boundary

The six other captured source families—Signal, document, Task evidence, date,
event and Project evidence—keep their existing physical tables and endpoints.
The future caller/child remap must cover 19 incoming FK groups from 15 retained
children, including four groups in retained Task evidence/date/event and Project
evidence plus 15 external groups. The other eight external groups target Signals.
This unit implements none of that remap and cannot select Business at runtime.
The source artifact's 19 affected incoming endpoints are exactly:

| Retained child | Existing source FK(s) into this unit |
| --- | --- |
| business_attention_items | anchor_id |
| business_attention_proposal_tasks | task_id |
| business_attention_tasks | task_id |
| business_legacy_links | project_id, anchor_id, task_id |
| business_project_candidates | confirmed_project_id |
| business_project_evidence | project_id |
| business_task_anchor_links | anchor_id, task_id |
| business_task_date_evidence | task_id |
| business_task_dingtalk_links | business_task_id |
| business_task_events | task_id |
| business_task_evidence | task_id |
| business_task_follow_ups | business_task_id |
| business_task_relations | to_task_id, from_task_id |
| business_task_todo_sync_outbox | business_task_id |
| business_work_cluster_tasks | task_id |

The accepted native source body helper proved only 30/188 bodies; 158 remain
unproved. No source body is duplicated, backed up, read or retired by this unit.
The whole approved ten-core-plus-ledger redesign remains incomplete. Passing
this unit is unselected storage acceptance only, never readiness to release.

## Task 1: Add the complete synthetic functional oracle

**Files:** Create `tests/test_business_storage.py`.

- [ ] Read the shared AGENTS, repository AGENTS, `docs/agent-claims.md`, current
  architecture/runtime documents and accepted field-map sections 5/7; claim only
  the six paths listed above in a new own row. Work in this isolated worktree.
- [ ] Create the test file with the complete code below. The native source side
  reconstructs accepted literal table/index/trigger SQL in `:memory:`. It never
  opens a real, copied or backup database, native session file or provider.

```python
"""Compare unselected four-kind Business storage to accepted native SQLite SQL."""

from contextlib import ExitStack, closing
import json
from pathlib import Path
import sqlite3

import pytest

from app.business_definitions import (
    BUSINESS_FIELDS, BUSINESS_FRESH_PROJECTIONS, BUSINESS_PROJECTIONS,
    BUSINESS_SOURCE_TABLES,
)
from app.business_storage import (
    create_business_schema, delete_business, insert_business, update_business,
)

CAPTURE = json.loads((Path(__file__).parents[1] / "docs/superpowers/specs/2026-10-15-database-redesign-business-core-native-source.json").read_text())
KINDS = ("task", "project", "anchor", "project_context_revision")
BASE = {
    "task": {"title": "synthetic", "stage": "candidate"},
    "project": {"canonical_anchor_id": -16, "title": "synthetic", "registry_source": "synthetic"},
    "anchor": {"anchor_type": "project", "anchor_ref": "new", "title": "synthetic"},
    "project_context_revision": {"project_id": -15, "context_json": "not JSON", "evidence_json": "not JSON"},
}
INVALID = (
    ("task", {"title": "\u00a0"}), ("task", {"stage": "unknown"}),
    ("task", {"status": "unknown"}), ("task", {"commitment_status": "unknown"}),
    ("task", {"stage": "formal", "formal_basis": "unknown"}),
    ("task", {"business_relevance": "unknown2"}), ("task", {"owner_evidence_json": "[]"}),
    ("task", {"missing_evidence_json": "{}"}), ("task", {"origin": "unknown"}),
    ("task", {"suggestion_json": "[]"}), ("task", {"stage": "formal"}),
    ("task", {"status": "merged"}), ("task", {"id": 8, "status": "merged", "merged_into_task_id": 8}),
    ("project", {"anchor_type": "customer"}), ("project", {"title": "\u3000"}),
    ("project", {"registry_source": "\t"}), ("project", {"crm_customer_lookup_status": "unknown"}),
    ("project", {"crm_customer_candidates_json": "{}"}), ("project", {"crm_customer_evidence_json": "[]"}),
    ("anchor", {"anchor_type": "unknown"}), ("anchor", {"anchor_ref": "\n"}),
    ("anchor", {"title": "\u2000"}), ("anchor", {"active": 2}),
)
NOT_NULL = tuple((kind, c["name"]) for kind, table in BUSINESS_SOURCE_TABLES.items()
                 for c in CAPTURE["actual"]["tables"][table]["columns"] if c["notnull"])


@pytest.fixture(params=("actual", "fresh"))
def pair(request):
    with ExitStack() as stack:
        source = stack.enter_context(closing(sqlite3.connect(":memory:")))
        target = stack.enter_context(closing(sqlite3.connect(":memory:")))
        for db in (source, target):
            db.execute("pragma foreign_keys=on")
        tables = CAPTURE[request.param]["tables"]
        for table in tables.values():
            source.execute(table["sql"])
        for table in tables.values():
            for index in reversed(table["indexes"]):
                if index["sql"]:
                    source.execute(index["sql"])
            for trigger in table["triggers"]:
                source.execute(trigger["sql"])
        target.execute("begin")
        create_business_schema(target)
        for db in (source, target):
            db.commit()
            db.execute("begin")
        yield source, target, request.param


def native_insert(db, kind, values, *, ignore=False):
    table = BUSINESS_SOURCE_TABLES[kind]
    cursor = db.execute("insert " + ("or ignore " if ignore else "") + "into " + table +
                        "(" + ",".join(values) + ") values(" + ",".join("?" for _ in values) +
                        ")", tuple(values.values()))
    return cursor.lastrowid if cursor.rowcount else None, cursor.rowcount


def both_insert(pair, kind, values, *, ignore=False, defaults=False):
    if not defaults:
        times = {c["name"]: "stamp" for c in CAPTURE["actual"]["tables"][BUSINESS_SOURCE_TABLES[kind]]["columns"] if c["dflt_value"] == "current_timestamp"}
        values = times | values
    native = native_insert(pair[0], kind, values, ignore=ignore)
    target = insert_business(pair[1], kind, values, ignore=ignore)
    assert (target.lastrowid, target.rowcount) == native
    return native[0]


def native_update(db, kind, identity, values, *, ignore=False):
    return db.execute("update " + ("or ignore " if ignore else "") + BUSINESS_SOURCE_TABLES[kind] +
                      " set " + ",".join(f"{f}=?" for f in values) + " where id=?",
                      (*values.values(), identity)).rowcount


def both_update(pair, kind, identity, values, *, ignore=False):
    assert update_business(pair[1], kind, identity, values, ignore=ignore) == native_update(
        pair[0], kind, identity, values, ignore=ignore)


def rows(pair, target, kind):
    db = pair[1 if target else 0]
    projection = BUSINESS_FRESH_PROJECTIONS if pair[2] == "fresh" else BUSINESS_PROJECTIONS
    sql = projection[kind] if target else "select * from " + BUSINESS_SOURCE_TABLES[kind]
    cursor = db.execute("select * from (" + sql + ") order by id")
    return tuple(c[0] for c in cursor.description), [tuple(r) for r in cursor]


def waters(pair, target):
    return {kind: (row[0] if (row := pair[1 if target else 0].execute(
        "select seq from sqlite_sequence where name=?",
        (("business:" + kind) if target else table,)).fetchone()) else None)
        for kind, table in BUSINESS_SOURCE_TABLES.items()}


def assert_same(pair):
    for kind in KINDS:
        assert rows(pair, False, kind) == rows(pair, True, kind)
    assert waters(pair, False) == waters(pair, True)
    assert pair[0].execute("pragma foreign_key_check").fetchall() == []
    assert pair[1].execute("pragma foreign_key_check").fetchall() == []


def seed(pair):
    both_insert(pair, "task", {"id": -11, **BASE["task"]})
    both_insert(pair, "anchor", {"id": -14, **BASE["anchor"], "anchor_ref": "seed"})
    both_insert(pair, "anchor", {"id": -16, **BASE["anchor"], "anchor_ref": "unused"})
    both_insert(pair, "project", {"id": -15, **BASE["project"], "canonical_anchor_id": -14})
    for db in pair[:2]:
        db.commit()
        db.execute("begin")


def failure(call):
    with pytest.raises(sqlite3.IntegrityError) as error:
        call()
    return error.value.sqlite_errorname


def test_exact_columns_defaults_and_source_ordinals(pair):
    assert tuple(len(BUSINESS_FIELDS[k]) for k in KINDS) == (19, 12, 6, 5)
    assert len(INVALID) == 23
    assert all(v is None for v in waters(pair, True).values())
    seed(pair)
    both_insert(pair, "task", BASE["task"], defaults=True)
    both_insert(pair, "anchor", BASE["anchor"] | {"anchor_ref": "defaults"}, defaults=True)
    both_insert(pair, "project", BASE["project"], defaults=True)
    both_insert(pair, "project_context_revision", BASE["project_context_revision"], defaults=True)
    for kind in KINDS:
        expected = tuple(c["name"] for c in CAPTURE[pair[2]]["tables"][BUSINESS_SOURCE_TABLES[kind]]["columns"])
        assert rows(pair, True, kind)[0] == expected
    # Remove independently evaluated wall-clock seconds from cross-connection comparison.
    for kind in KINDS:
        fields, native = rows(pair, False, kind)
        _, target = rows(pair, True, kind)
        time_fields = {f for f, default in ((c["name"], c["dflt_value"]) for c in CAPTURE[pair[2]]["tables"][BUSINESS_SOURCE_TABLES[kind]]["columns"]) if default == "current_timestamp"}
        for row in native + target:
            times = [row[fields.index(f)] for f in time_fields]
            assert len(set(times)) == 1
            assert all(isinstance(t, str) and (len(t) == 19 or t == "stamp") for t in times)
        keep = [i for i, f in enumerate(fields) if f not in time_fields]
        assert [tuple(r[i] for i in keep) for r in native] == [tuple(r[i] for i in keep) for r in target]


@pytest.mark.parametrize("kind,overrides", INVALID)
@pytest.mark.parametrize("ignore", (False, True))
def test_all_23_source_checks_and_ignore_order(pair, kind, overrides, ignore):
    seed(pair)
    values = BASE[kind] | overrides
    # Every invalid row also has a missing parent when that kind owns an edge.
    if kind == "project":
        values["canonical_anchor_id"] = 999
    if ignore:
        assert both_insert(pair, kind, values, ignore=True) is None
    else:
        assert failure(lambda: native_insert(pair[0], kind, values)) == failure(
            lambda: insert_business(pair[1], kind, values)) == "SQLITE_CONSTRAINT_CHECK"
    assert waters(pair, False) == waters(pair, True)


@pytest.mark.parametrize("kind,field", NOT_NULL)
@pytest.mark.parametrize("ignore", (False, True))
def test_every_native_not_null_and_ignore(pair, kind, field, ignore):
    seed(pair)
    values = BASE[kind] | {field: None}
    if ignore:
        assert both_insert(pair, kind, values, ignore=True) is None
    else:
        assert failure(lambda: native_insert(pair[0], kind, values)) == failure(
            lambda: insert_business(pair[1], kind, values)) == "SQLITE_CONSTRAINT_NOTNULL"
    assert waters(pair, False) == waters(pair, True)


@pytest.mark.parametrize("kind,overrides", (
    ("task", {"description": 1.25, "owner_name": b"\x00\xff", "deadline_at": float("inf")}),
    ("task", {"description": True, "owner_user_id": memoryview(b"A"), "deadline_at": float("-inf")}),
    ("task", {"description": " a\n\u0000b ", "owner_evidence_json": b'{"x": 1}', "missing_evidence_json": b'[1]'}),
    ("project", {"crm_customer_id": b"\x00\xff", "crm_customer_name": 42, "crm_customer_evidence_json": '{ "b":2, "a":1 }'}),
    ("anchor", {"anchor_ref": bytearray(b"blob"), "active": "1e0"}),
    ("project_context_revision", {"context_json": b"\x00\xff", "evidence_json": float("inf")}),
    ("project_context_revision", {"context_json": "{ bad JSON", "evidence_json": "  not JSON \n"}),
))
def test_native_storage_classes_exact_text_and_affinity(pair, kind, overrides):
    seed(pair)
    both_insert(pair, kind, BASE[kind] | overrides | {"created_at": "stamp", **({"updated_at": "stamp", "last_activity_at": "stamp"} if kind == "task" else {})})
    fields, native = rows(pair, False, kind)
    _, target = rows(pair, True, kind)
    assert native[-1] == target[-1]
    for field in overrides:
        assert type(native[-1][fields.index(field)]) is type(target[-1][fields.index(field)])


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("identity", (-9, 0, "2.0", 3.0, True))
def test_signed_explicit_public_ids_and_ordering(pair, kind, identity):
    seed(pair)
    both_insert(pair, kind, BASE[kind] | {"id": identity, "created_at": "stamp"})
    assert waters(pair, False) == waters(pair, True)
    assert rows(pair, False, kind) == rows(pair, True, kind)
    assert [r[0] for r in rows(pair, True, kind)[1]] == sorted(r[0] for r in rows(pair, True, kind)[1])


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("identity", ("junk", b"1", 1.5))
def test_native_integer_pk_rejection(pair, kind, identity):
    seed(pair)
    assert failure(lambda: native_insert(pair[0], kind, BASE[kind] | {"id": identity})) == failure(
        lambda: insert_business(pair[1], kind, BASE[kind] | {"id": identity})) == "SQLITE_MISMATCH"


@pytest.mark.parametrize("kind", KINDS)
def test_independent_waters_deleted_high_update_and_ignore(pair, kind):
    seed(pair)
    if kind == "project":
        both_insert(pair, "anchor", BASE["anchor"] | {"id": -20, "anchor_ref": "second"})
    base = BASE[kind] | ({"canonical_anchor_id": -20} if kind == "project" else {})
    both_insert(pair, kind, base | {"id": 1000})
    before_other = {k: v for k, v in waters(pair, True).items() if k != kind}
    assert delete_business(pair[1], kind, 1000) == pair[0].execute(
        "delete from " + BUSINESS_SOURCE_TABLES[kind] + " where id=1000").rowcount == 1
    assert both_insert(pair, kind, base) == 1001
    both_update(pair, kind, 1001, {"id": 3000})
    assert waters(pair, True)[kind] == 1001
    assert delete_business(pair[1], kind, 3000) == pair[0].execute(
        "delete from " + BUSINESS_SOURCE_TABLES[kind] + " where id=3000").rowcount == 1
    assert both_insert(pair, kind, base) == 1002
    # Explicit duplicate PK is ignored, advances no further than that supplied key.
    assert both_insert(pair, kind, base | {"id": 1002}, ignore=True) is None
    assert {k: v for k, v in waters(pair, True).items() if k != kind} == before_other
    assert waters(pair, False) == waters(pair, True)


@pytest.mark.parametrize("kind,values", (
    ("task", {"status": "merged", "merged_into_task_id": 999}),
    ("project", {"canonical_anchor_id": 999}),
    ("project_context_revision", {"project_id": 999}),
))
@pytest.mark.parametrize("ignore", (False, True))
def test_real_fk_after_row_checks_and_ignore(pair, kind, values, ignore):
    seed(pair)
    before = waters(pair, True)
    assert failure(lambda: native_insert(pair[0], kind, BASE[kind] | values, ignore=ignore)) == failure(
        lambda: insert_business(pair[1], kind, BASE[kind] | values, ignore=ignore)) == "SQLITE_CONSTRAINT_FOREIGNKEY"
    assert waters(pair, False) == waters(pair, True) == before


def test_binary_unique_and_typed_composite_anchor_parent(pair):
    seed(pair)
    assert failure(lambda: native_insert(pair[0], "anchor", BASE["anchor"] | {"anchor_ref": "seed"})) == failure(
        lambda: insert_business(pair[1], "anchor", BASE["anchor"] | {"anchor_ref": "seed"})) == "SQLITE_CONSTRAINT_UNIQUE"
    assert both_insert(pair, "anchor", BASE["anchor"] | {"anchor_ref": "seed"}, ignore=True) is None
    both_insert(pair, "anchor", BASE["anchor"] | {"anchor_ref": "SEED"})
    customer = both_insert(pair, "anchor", BASE["anchor"] | {"anchor_type": "customer", "anchor_ref": "other"})
    assert failure(lambda: native_insert(pair[0], "project", BASE["project"] | {"canonical_anchor_id": customer})) == failure(
        lambda: insert_business(pair[1], "project", BASE["project"] | {"canonical_anchor_id": customer})) == "SQLITE_CONSTRAINT_FOREIGNKEY"
    assert failure(lambda: native_insert(pair[0], "project", BASE["project"] | {"canonical_anchor_id": -14})) == failure(
        lambda: insert_business(pair[1], "project", BASE["project"] | {"canonical_anchor_id": -14})) == "SQLITE_CONSTRAINT_UNIQUE"


def test_public_parent_updates_delete_and_native_statement_end_fk(pair):
    seed(pair)
    both_insert(pair, "task", BASE["task"] | {"id": 10})
    both_insert(pair, "task", BASE["task"] | {"id": 11, "status": "merged", "merged_into_task_id": 10})
    both_insert(pair, "project_context_revision", BASE["project_context_revision"])
    for kind, identity in (("task", 10), ("anchor", -14), ("project", -15)):
        for ignore in (False, True):
            assert failure(lambda: native_update(pair[0], kind, identity, {"id": 88}, ignore=ignore)) == failure(
                lambda: update_business(pair[1], kind, identity, {"id": 88}, ignore=ignore)) == "SQLITE_CONSTRAINT_FOREIGNKEY"
        assert failure(lambda: pair[0].execute("delete from " + BUSINESS_SOURCE_TABLES[kind] + " where id=?", (identity,))) == failure(
            lambda: delete_business(pair[1], kind, identity)) == "SQLITE_CONSTRAINT_FOREIGNKEY"
    assert failure(lambda: native_update(pair[0], "anchor", -14, {"anchor_type": "matter"})) == failure(
        lambda: update_business(pair[1], "anchor", -14, {"anchor_type": "matter"})) == "SQLITE_CONSTRAINT_FOREIGNKEY"
    # One native Task UPDATE may coordinate a parent's public key and child FK.
    pair[0].execute("update business_tasks set id=case id when 10 then 20 else id end, merged_into_task_id=case id when 11 then 20 else merged_into_task_id end where id in (10,11)")
    pair[1].execute("update business set legacy_key=case public_id when 10 then '20' else legacy_key end, payload_json=case public_id when 11 then json_set(payload_json,'$.merged_into_task_id',20) else payload_json end where record_type='task' and public_id in (10,11)")
    assert rows(pair, False, "task") == rows(pair, True, "task")
    assert pair[1].execute("pragma foreign_key_check").fetchall() == []


def test_late_fk_failure_caller_rolls_back_rows_and_all_four_waters(pair):
    seed(pair)
    before_rows = {k: rows(pair, True, k) for k in KINDS}
    before_water = waters(pair, True)
    with pytest.raises(sqlite3.IntegrityError):
        with pair[0], pair[1]:
            both_insert(pair, "task", BASE["task"])
            anchor = both_insert(pair, "anchor", BASE["anchor"] | {"anchor_ref": "late"})
            project = both_insert(pair, "project", BASE["project"] | {"canonical_anchor_id": anchor})
            both_insert(pair, "project_context_revision", BASE["project_context_revision"] | {"project_id": project})
            # Execute independently so both connections reach the same late failure.
            assert failure(lambda: native_insert(pair[0], "project_context_revision", BASE["project_context_revision"] | {"project_id": 999})) == "SQLITE_CONSTRAINT_FOREIGNKEY"
            insert_business(pair[1], "project_context_revision", BASE["project_context_revision"] | {"project_id": 999})
    assert {k: rows(pair, True, k) for k in KINDS} == before_rows
    assert waters(pair, False) == waters(pair, True) == before_water
    assert not pair[0].in_transaction and not pair[1].in_transaction


@pytest.mark.parametrize("kind", KINDS)
def test_native_auto_exhaustion_rolls_back_plain_source_caller_transaction(pair, kind):
    seed(pair)
    base = dict(BASE[kind])
    if kind == "project":
        both_insert(pair, "anchor", BASE["anchor"] | {"id": -20, "anchor_ref": "limit"})
        base["canonical_anchor_id"] = -20
    both_insert(pair, kind, base | {"id": 2**63 - 1})
    for db in pair[:2]:
        db.commit()
        db.execute("begin")
    before = waters(pair, True)
    both_insert(pair, "task", BASE["task"] | {"id": -99})
    for call in (lambda: native_insert(pair[0], kind, base), lambda: insert_business(pair[1], kind, base)):
        with pytest.raises(sqlite3.OperationalError) as error:
            call()
        assert error.value.sqlite_errorname == "SQLITE_FULL"
    assert waters(pair, False) == waters(pair, True) == before
    assert not pair[0].in_transaction and not pair[1].in_transaction
    assert pair[1].execute("select count(*) from business where record_type='task' and public_id=-99").fetchone()[0] == 0


def test_no_self_commit_read_mutation_or_schema_selection(pair):
    seed(pair)
    both_update(pair, "task", -11, {"description": "changed", "owner_name": b"new"})
    both_update(pair, "task", 999, {"title": "absent"})
    assert delete_business(pair[1], "task", 999) == 0
    both_insert(pair, "project_context_revision", BASE["project_context_revision"])
    before = pair[1].total_changes
    for kind in KINDS:
        rows(pair, True, kind)
    assert pair[1].total_changes == before
    assert pair[1].in_transaction
    for db in pair[:2]:
        db.rollback()
    assert rows(pair, False, "task") == rows(pair, True, "task")
    assert rows(pair, True, "project_context_revision")[1] == []
    with pytest.raises(sqlite3.ProgrammingError, match="caller transaction"):
        insert_business(pair[1], "task", BASE["task"])
    assert pair[1].execute("select name from sqlite_schema where name like 'business_%' and type='table'").fetchall() == []


def test_installer_ddl_is_caller_transactional():
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("begin")
        create_business_schema(db)
        assert db.in_transaction
        db.rollback()
        assert db.execute("select name from sqlite_schema where name='business'").fetchone() is None


def test_retained_children_are_future_release_closure_only():
    for variant in ("actual", "fresh"):
        source = CAPTURE[variant]
        selected = set(BUSINESS_SOURCE_TABLES.values())
        incoming = [r for r in source["incoming_foreign_keys"] if r["table"] in selected and r["child_table"] not in selected]
        assert len({(r["child_table"], r["id"]) for r in incoming}) == 19
        assert len({r["child_table"] for r in incoming}) == 15
        assert all(r["on_delete"] == r["on_update"] == "NO ACTION" for r in incoming)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("returning", (False, True))
def test_native_exhaustion_statement_shape_control(pair, kind, returning):
    seed(pair)
    base = dict(BASE[kind])
    native_insert(pair[0], kind, base | {"id": 2**63 - 1})
    pair[0].commit()
    pair[0].execute("begin")
    native_insert(pair[0], "task", BASE["task"] | {"id": -99})
    sql = "insert into " + BUSINESS_SOURCE_TABLES[kind] + "(" + ",".join(base) + ") values(" + ",".join("?" for _ in base) + ")"
    with pytest.raises(sqlite3.OperationalError) as error:
        pair[0].execute(sql + (" returning id" if returning else ""), tuple(base.values())).fetchall()
    assert error.value.sqlite_errorname == "SQLITE_FULL"
    assert pair[0].in_transaction is returning
    assert pair[0].execute("select count(*) from business_tasks where id=-99").fetchone()[0] == int(returning)


def test_omitted_task_timestamps_share_statement_time(pair):
    # Synthetic clock changes at every SQLite statement, never by wall clock.
    # Native INSERT is the control; three per-field default SELECTs would fail.
    for db in pair[:2]:
        ticks = [0]
        def trace(sql, ticks=ticks):
            ticks[0] += 1
        db.set_trace_callback(trace)
        db.create_function("current_timestamp", 0, lambda ticks=ticks: "clock-" + str(ticks[0]))
    both_insert(pair, "task", BASE["task"], defaults=True)
    for target in (False, True):
        fields, result = rows(pair, target, "task")
        assert len({result[0][fields.index(f)] for f in ("created_at", "updated_at", "last_activity_at")}) == 1


def test_structural_physical_public_coupling_cannot_be_bypassed(pair):
    seed(pair)
    task_physical = pair[1].execute("select id from business where record_type='task' and public_id=-11").fetchone()[0]
    project_physical = pair[1].execute("select id from business where record_type='project' and public_id=-15").fetchone()[0]
    child = both_insert(pair, "task", BASE["task"] | {"status": "merged", "merged_into_task_id": -11})
    assert failure(lambda: pair[1].execute("update business set merged_task_ref=? where record_type='task' and public_id=?", (project_physical, child))) == "SQLITE_CONSTRAINT_FOREIGNKEY"
    assert failure(lambda: pair[1].execute("update business set merged_task_ref=? where record_type='task' and public_id=-11", (task_physical,))) == "SQLITE_CONSTRAINT_CHECK"
    assert pair[1].execute("pragma foreign_key_check").fetchall() == []


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("identity", (None, float("nan")))
def test_null_and_nan_public_ids_allocate_natively(pair, kind, identity):
    seed(pair)
    assert both_insert(pair, kind, BASE[kind] | {"id": identity}) == 1
    assert rows(pair, False, kind) == rows(pair, True, kind)
    assert waters(pair, False) == waters(pair, True)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("identity", (2**63, -(2**63) - 1))
def test_bound_integer_overflow_is_native_and_keeps_transaction(pair, kind, identity):
    seed(pair)
    for call in (lambda: native_insert(pair[0], kind, BASE[kind] | {"id": identity}),
                 lambda: insert_business(pair[1], kind, BASE[kind] | {"id": identity})):
        with pytest.raises(OverflowError):
            call()
    assert pair[0].in_transaction and pair[1].in_transaction
    assert waters(pair, False) == waters(pair, True)


@pytest.mark.parametrize("kind", KINDS)
def test_id_update_affinity_null_rejection_and_ignored_not_null(pair, kind):
    seed(pair)
    both_insert(pair, kind, BASE[kind] | {"id": 7})
    both_update(pair, kind, 7, {"id": "8.0"})
    assert rows(pair, False, kind) == rows(pair, True, kind)
    assert failure(lambda: native_update(pair[0], kind, 8, {"id": None})) == failure(
        lambda: update_business(pair[1], kind, 8, {"id": None})) == "SQLITE_MISMATCH"
    field = "context_json" if kind == "project_context_revision" else "title"
    both_update(pair, kind, 8, {field: None}, ignore=True)
    assert rows(pair, False, kind) == rows(pair, True, kind)
    assert waters(pair, False) == waters(pair, True)
```

- [ ] Run `python -m pytest tests/test_business_storage.py -q --maxfail=1`.
  Expected RED: collection fails with `ModuleNotFoundError` for the new
  `app.business_definitions` module. Do not change the tests to avoid the
  missing interface. Keep these uncommitted while building the coherent unit.

## Task 2: Declare all four kinds and their source projections

**Files:** Create `app/business_definitions.py`; tests are
`tests/test_business_storage.py`.

- [ ] Create the complete finite declaration module below. These literals are
  frozen accepted metadata; runtime must not load the capture JSON or discover
  a schema from a live database. The source CHECK strings below include their
  original whitespace with only source identifiers explicitly remapped.

```python
"""Unselected four-kind Business declarations; source metadata is frozen here."""

from app.action_email_definitions import _encoded_value, _encoded_value_check

BUSINESS_SOURCE_TABLES = {'task': 'business_tasks',
 'project': 'business_projects',
 'anchor': 'business_anchors',
 'project_context_revision': 'business_project_context_revisions'}

BUSINESS_FIELDS = {'task': ('id',
          'title',
          'description',
          'stage',
          'status',
          'commitment_status',
          'formal_basis',
          'business_relevance',
          'merged_into_task_id',
          'owner_user_id',
          'owner_name',
          'owner_evidence_json',
          'deadline_at',
          'missing_evidence_json',
          'last_activity_at',
          'created_at',
          'updated_at',
          'origin',
          'suggestion_json'),
 'project': ('id',
             'canonical_anchor_id',
             'anchor_type',
             'title',
             'registry_source',
             'created_at',
             'crm_customer_id',
             'crm_customer_name',
             'crm_customer_lookup_status',
             'crm_customer_candidates_json',
             'crm_customer_label',
             'crm_customer_evidence_json'),
 'anchor': ('id', 'anchor_type', 'anchor_ref', 'title', 'active', 'created_at'),
 'project_context_revision': ('id', 'project_id', 'context_json', 'evidence_json', 'created_at')}

BUSINESS_FRESH_FIELDS = {'task': ('id',
          'title',
          'description',
          'stage',
          'origin',
          'suggestion_json',
          'status',
          'commitment_status',
          'formal_basis',
          'business_relevance',
          'merged_into_task_id',
          'owner_user_id',
          'owner_name',
          'owner_evidence_json',
          'deadline_at',
          'missing_evidence_json',
          'last_activity_at',
          'created_at',
          'updated_at'),
 'project': ('id',
             'canonical_anchor_id',
             'anchor_type',
             'title',
             'registry_source',
             'crm_customer_id',
             'crm_customer_name',
             'crm_customer_lookup_status',
             'crm_customer_candidates_json',
             'crm_customer_label',
             'crm_customer_evidence_json',
             'created_at'),
 'anchor': ('id', 'anchor_type', 'anchor_ref', 'title', 'active', 'created_at'),
 'project_context_revision': ('id', 'project_id', 'context_json', 'evidence_json', 'created_at')}

BUSINESS_TYPES = {'task': {'id': 'integer',
          'title': 'text',
          'description': 'text',
          'stage': 'text',
          'status': 'text',
          'commitment_status': 'text',
          'formal_basis': 'text',
          'business_relevance': 'text',
          'merged_into_task_id': 'integer',
          'owner_user_id': 'text',
          'owner_name': 'text',
          'owner_evidence_json': 'text',
          'deadline_at': 'text',
          'missing_evidence_json': 'text',
          'last_activity_at': 'text',
          'created_at': 'text',
          'updated_at': 'text',
          'origin': 'text',
          'suggestion_json': 'text'},
 'project': {'id': 'integer',
             'canonical_anchor_id': 'integer',
             'anchor_type': 'text',
             'title': 'text',
             'registry_source': 'text',
             'created_at': 'text',
             'crm_customer_id': 'text',
             'crm_customer_name': 'text',
             'crm_customer_lookup_status': 'text',
             'crm_customer_candidates_json': 'text',
             'crm_customer_label': 'text',
             'crm_customer_evidence_json': 'text'},
 'anchor': {'id': 'integer',
            'anchor_type': 'text',
            'anchor_ref': 'text',
            'title': 'text',
            'active': 'integer',
            'created_at': 'text'},
 'project_context_revision': {'id': 'integer',
                              'project_id': 'integer',
                              'context_json': 'text',
                              'evidence_json': 'text',
                              'created_at': 'text'}}

BUSINESS_NOT_NULL = {'task': ('title',
          'description',
          'stage',
          'status',
          'commitment_status',
          'business_relevance',
          'owner_user_id',
          'owner_name',
          'owner_evidence_json',
          'deadline_at',
          'missing_evidence_json',
          'last_activity_at',
          'created_at',
          'updated_at',
          'origin',
          'suggestion_json'),
 'project': ('canonical_anchor_id',
             'anchor_type',
             'title',
             'registry_source',
             'created_at',
             'crm_customer_id',
             'crm_customer_name',
             'crm_customer_lookup_status',
             'crm_customer_candidates_json',
             'crm_customer_label',
             'crm_customer_evidence_json'),
 'anchor': ('anchor_type', 'anchor_ref', 'title', 'active', 'created_at'),
 'project_context_revision': ('project_id', 'context_json', 'evidence_json', 'created_at')}

BUSINESS_DEFAULTS = {'task': {'description': "''",
          'status': "'open'",
          'commitment_status': "'none'",
          'business_relevance': "'unknown'",
          'owner_user_id': "''",
          'owner_name': "''",
          'owner_evidence_json': "'{}'",
          'deadline_at': "''",
          'missing_evidence_json': "'[]'",
          'last_activity_at': 'current_timestamp',
          'created_at': 'current_timestamp',
          'updated_at': 'current_timestamp',
          'origin': "'source'",
          'suggestion_json': "'{}'"},
 'project': {'anchor_type': "'project'",
             'created_at': 'current_timestamp',
             'crm_customer_id': "''",
             'crm_customer_name': "''",
             'crm_customer_lookup_status': "'not_requested'",
             'crm_customer_candidates_json': "'[]'",
             'crm_customer_label': "''",
             'crm_customer_evidence_json': "'{}'"},
 'anchor': {'active': '1', 'created_at': 'current_timestamp'},
 'project_context_revision': {'created_at': 'current_timestamp'}}

BUSINESS_CHECKS = {'task': ('trim(title, '
          'char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)) '
          "<> ''",
          "task_stage in ('candidate', 'formal')",
          'status in (\n'
          "                        'open', 'waiting', 'done', 'cancelled', 'merged'\n"
          '                    )',
          'task_commitment_status in (\n'
          "                        'none', 'assigned_unaccepted', 'accepted', 'disputed', "
          "'completed', 'cancelled'\n"
          '                    )',
          'task_formal_basis in (\n'
          "                        'explicit_assignment', 'explicit_commitment', 'external_todo', "
          "'meeting_action_item'\n"
          '                    )',
          'task_business_relevance in (\n'
          "                        'unknown', 'not_relevant', 'relevant'\n"
          '                    )',
          "json_valid(task_owner_evidence_json) and json_type(task_owner_evidence_json) = 'object'",
          'json_valid(task_missing_evidence_json) and json_type(task_missing_evidence_json) = '
          "'array'",
          "task_origin in ('source','agent_suggestion')",
          "json_valid(task_suggestion_json) and json_type(task_suggestion_json)='object'",
          '\n'
          "                        (task_stage = 'formal' and task_formal_basis is not null)\n"
          "                        or (task_stage = 'candidate' and task_formal_basis is null)\n"
          '                    ',
          '\n'
          "                        (status = 'merged' and task_merged_into_task_id is not null)\n"
          "                        or (status <> 'merged' and task_merged_into_task_id is null)\n"
          '                    ',
          'task_merged_into_task_id is null or task_merged_into_task_id <> public_id'),
 'project': ("project_anchor_type = 'project'",
             'trim(title, '
             'char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)) '
             "<> ''",
             'trim(project_registry_source, '
             'char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)) '
             "<> ''",
             "project_crm_customer_lookup_status in ('not_requested', 'matched', 'ambiguous', "
             "'needs_confirmation', 'no_match', 'unavailable', 'conflict')",
             'json_valid(project_crm_customer_candidates_json) and '
             "json_type(project_crm_customer_candidates_json) = 'array'",
             'json_valid(project_crm_customer_evidence_json) and '
             "json_type(project_crm_customer_evidence_json) = 'object'"),
 'anchor': ('anchor_anchor_type in (\n'
            "                        'project', 'okr', 'customer', 'product', 'revenue', "
            "'financing',\n"
            "                        'cash', 'key_hire', 'personnel', 'company_priority', "
            "'matter'\n"
            '                    )',
            'trim(anchor_anchor_ref, '
            'char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)) '
            "<> ''",
            'trim(title, '
            'char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)) '
            "<> ''",
            'anchor_active in (0, 1)'),
 'project_context_revision': ()}

BUSINESS_COMMON = ("title", "status", "created_at", "updated_at")
BUSINESS_REFERENCES = {
    "task": ("merged_into_task_id", "merged_task_ref", "task"),
    "project": ("canonical_anchor_id", "canonical_anchor_ref", "anchor"),
    "project_context_revision": ("project_id", "context_project_ref", "project"),
}


def business_column(kind, field):
    return "public_id" if field == "id" else field if field in BUSINESS_COMMON else kind + "_" + field


def business_payload_fields(kind):
    return tuple(f for f in BUSINESS_FIELDS[kind] if f != "id" and f not in BUSINESS_COMMON)


def _definitions():
    columns = [
        "id integer primary key autoincrement check(id>0)",
        "record_type text not null check(record_type in ('task','project','anchor','project_context_revision'))",
        "legacy_key text not null check(legacy_key=cast(cast(legacy_key as integer) as text))",
        "public_id integer generated always as (cast(legacy_key as integer)) virtual",
        "payload_json text not null check(json_valid(payload_json) and json_type(payload_json)='object')",
        "title text not null default ''", "status text not null default ''",
        "created_at text not null default current_timestamp", "updated_at text not null default ''",
        "merged_task_ref integer", "canonical_anchor_ref integer", "context_project_ref integer",
    ]
    constraints = []
    for kind in BUSINESS_FIELDS:
        for field in business_payload_fields(kind):
            affinity = BUSINESS_TYPES[kind][field]
            required = field in BUSINESS_NOT_NULL[kind]
            inactive = "''" if required and affinity == "text" else "0" if required else "null"
            value = _encoded_value("payload_json", "$." + field)
            columns.append(f"{business_column(kind, field)} {affinity} generated always as "
                           f"(case when record_type='{kind}' then {value} else {inactive} end) virtual"
                           + (" not null" if required else ""))
        payload_fields = business_payload_fields(kind)
        paths = ",".join(repr("$." + f) for f in payload_fields)
        valid = [f"({_encoded_value_check('payload_json', '$.' + f)})" for f in payload_fields]
        valid.append(f"json_remove(payload_json,{paths})='{{}}'")
        valid.extend(f"({expr})" for expr in BUSINESS_CHECKS[kind])
        constraints.append(f"check(record_type<>'{kind}' or ({' and '.join(valid)}))")
    for kind, (field, physical, parent) in BUSINESS_REFERENCES.items():
        source = business_column(kind, field)
        discriminator = physical + "_type"
        columns.append(f"{discriminator} text generated always as "
                       f"(case when record_type='{kind}' then '{parent}' end) virtual")
        constraints.append(f"check(case when record_type='{kind}' then "
                       f"({source} is null)=({physical} is null) else {physical} is null end)")
        extra_child = ",project_anchor_type" if kind == "project" else ""
        extra_parent = ",anchor_anchor_type" if kind == "project" else ""
        constraints.append(f"foreign key({physical},{discriminator},{source}{extra_child}) "
                       f"references business(id,record_type,public_id{extra_parent}) "
                       "on update no action on delete no action")
    table = "create table business (" + ",".join(columns + constraints) + ")"
    indexes = (
        "create unique index idx_business_public_id on business(record_type,public_id)",
        "create unique index idx_business_typed_id on business(id,record_type,public_id)",
        "create unique index idx_business_typed_anchor on business(id,record_type,public_id,anchor_anchor_type)",
        "create index idx_business_task_relevance on business(task_business_relevance,updated_at,public_id) where record_type='task'",
        "create index idx_business_task_stage on business(task_stage,status,updated_at,public_id) where record_type='task'",
        "create index idx_business_task_updated on business(updated_at,public_id) where record_type='task'",
        "create unique index idx_business_project_anchor on business(project_canonical_anchor_id) where record_type='project'",
        "create unique index idx_business_anchor_identity on business(anchor_anchor_type,anchor_anchor_ref) where record_type='anchor'",
        "create unique index idx_business_anchor_public_type on business(public_id,anchor_anchor_type) where record_type='anchor'",
        "create index idx_business_context_project on business(project_context_revision_project_id,public_id desc) where record_type='project_context_revision'",
    )
    triggers = tuple(
        f"create trigger trg_business_{kind}_sequence before insert on business "
        f"when new.record_type='{kind}' begin "
        f"insert into sqlite_sequence(name,seq) select 'business:{kind}',0 where not exists "
        f"(select 1 from sqlite_sequence where name='business:{kind}'); "
        f"update sqlite_sequence set seq=max(seq,cast(new.legacy_key as integer)) "
        f"where name='business:{kind}'; end"
        for kind in BUSINESS_FIELDS
    )
    return table, indexes, triggers


BUSINESS_TABLE_DDL, BUSINESS_INDEX_DDL, BUSINESS_TRIGGER_DDL = _definitions()


def _projection(kind, fields):
    return "select " + ",".join(business_column(kind, f) + " as " + f for f in fields) +         f" from business where record_type='{kind}'"


BUSINESS_PROJECTIONS = {k: _projection(k, fields) for k, fields in BUSINESS_FIELDS.items()}
BUSINESS_FRESH_PROJECTIONS = {k: _projection(k, fields) for k, fields in BUSINESS_FRESH_FIELDS.items()}
```

- [ ] Run `python -m pytest tests/test_business_storage.py -q --maxfail=1`.
  Expected RED: collection now fails for missing `app.business_storage`.
  Do not commit a declaration-only or Anchor-only scaffold.
- [ ] Run `python -m ruff check app/business_definitions.py`.
  Expected: `All checks passed!`.

## Task 3: Add the complete source-shaped writer unit

**Files:** Create `app/business_storage.py`; tests are
`tests/test_business_storage.py`.

- [ ] Create the complete module below. Preserve explicit source keys in the
  payload and the native typed physical/public composite FK coupling. Do not
  turn a missing parent into an early lookup exception or add a savepoint/
  commit that ends the caller's transaction. The only explicit rollback is
  public allocator SQLITE_FULL, matching the verified plain source INSERT.

```python
"""Source-shaped, caller-transactional writes for unselected Business storage."""

from dataclasses import dataclass
import json
import sqlite3

from app.action_storage import (
    _encoded_sqlite_value, _native_integer_primary_key, _raise_autoincrement_full,
    _source_bound_null,
)
from app.business_definitions import (
    BUSINESS_COMMON, BUSINESS_DEFAULTS, BUSINESS_FIELDS, BUSINESS_INDEX_DDL,
    BUSINESS_PROJECTIONS, BUSINESS_REFERENCES, BUSINESS_TABLE_DDL,
    BUSINESS_TRIGGER_DDL, business_payload_fields,
)


@dataclass(frozen=True)
class BusinessWriteResult:
    lastrowid: int | None
    rowcount: int


def create_business_schema(db: sqlite3.Connection):
    """Install declarations only; caller owns BEGIN/commit/rollback."""
    for sql in (BUSINESS_TABLE_DDL, *BUSINESS_INDEX_DDL, *BUSINESS_TRIGGER_DDL):
        db.execute(sql)


def _check_values(kind, values):
    if kind not in BUSINESS_FIELDS or set(values) - set(BUSINESS_FIELDS[kind]):
        raise ValueError("unsupported Business source fields")
    for value in values.values():
        _encoded_sqlite_value(value)


def _lock(db):
    if not db.in_transaction:
        raise sqlite3.ProgrammingError("Business writes require a caller transaction")
    db.execute("update sqlite_sequence set seq=seq where 0")


def _next_id(db, kind):
    water = db.execute("select seq from sqlite_sequence where name=?", ("business:" + kind,)).fetchone()
    maximum = db.execute("select max(public_id) from business where record_type=?", (kind,)).fetchone()[0]
    high = max(water[0] if water else 0, maximum or 0, 0)
    if high == 2**63 - 1:
        # Actual callers use plain INSERT/lastrowid. Their captured native
        # families roll back the whole transaction on SQLITE_FULL; a native
        # INSERT RETURNING control behaves differently and is not this API.
        db.rollback()
        _raise_autoincrement_full()
    return high + 1


def _complete_values(db, kind, supplied):
    fields = BUSINESS_FIELDS[kind][1:]
    expressions = ["?" if f in supplied else BUSINESS_DEFAULTS[kind].get(f, "null") for f in fields]
    bindings = tuple(supplied[f] for f in fields if f in supplied)
    row = db.execute("select " + ",".join(expressions), bindings).fetchone()
    return dict(zip(fields, row, strict=True))


def _stored_values(db, kind, values):
    payload = json.dumps({f: _encoded_sqlite_value(values[f]) for f in business_payload_fields(kind)},
                         ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    physical = {ref[1]: None for ref in BUSINESS_REFERENCES.values()}
    if kind in BUSINESS_REFERENCES:
        field, target, parent = BUSINESS_REFERENCES[kind]
        raw = values[field]
        if not _source_bound_null(raw):
            row = db.execute("select id from business where record_type=? and public_id=?", (parent, raw)).fetchone()
            # Zero cannot be a physical Business ID. Defer its missing-parent
            # failure to the native composite FK, after IGNORE's row checks.
            physical[target] = row[0] if row else 0
    common = {f: values[f] if f in values else "" for f in BUSINESS_COMMON}
    return {"payload_json": payload, **common, **physical}


def insert_business(db: sqlite3.Connection, kind: str, values: dict, *, ignore=False) -> BusinessWriteResult:
    _check_values(kind, values)
    _lock(db)
    public_id = (_next_id(db, kind) if _source_bound_null(values.get("id"))
                 else _native_integer_primary_key(values["id"]))
    stored = _stored_values(db, kind, _complete_values(db, kind, values))
    data = {"record_type": kind, "legacy_key": str(public_id), **stored}
    cursor = db.execute("insert " + ("or ignore " if ignore else "") + "into business(" +
                        ",".join(data) + ") values(" + ",".join("?" for _ in data) + ") returning public_id",
                        tuple(data.values()))
    row = cursor.fetchone()
    cursor.fetchall()
    return BusinessWriteResult(row[0] if row else None, cursor.rowcount)


def update_business(db: sqlite3.Connection, kind: str, public_id, values: dict, *, ignore=False) -> int:
    _check_values(kind, values)
    _lock(db)
    cursor = db.execute("select * from (" + BUSINESS_PROJECTIONS[kind] + ") where id=?", (public_id,))
    row = cursor.fetchone()
    if row is None:
        return 0
    current = dict(zip((c[0] for c in cursor.description), row, strict=True))
    physical_id = db.execute("select id from business where record_type=? and public_id=?", (kind, public_id)).fetchone()[0]
    current.update(values)
    new_id = _native_integer_primary_key(current["id"], update=True)
    stored = {"legacy_key": str(new_id), **_stored_values(db, kind, current)}
    result = db.execute("update " + ("or ignore " if ignore else "") + "business set " +
                        ",".join(f"{field}=?" for field in stored) + " where id=?",
                        (*stored.values(), physical_id))
    return result.rowcount


def delete_business(db: sqlite3.Connection, kind: str, public_id) -> int:
    _check_values(kind, {})
    _lock(db)
    return db.execute("delete from business where record_type=? and public_id=?", (kind, public_id)).rowcount
```

- [ ] Run `python -m pytest tests/test_business_storage.py -q`.
  Expected GREEN: **414 passed** on the planning SQLite 3.53.4 environment,
  including 16 native-only statement-shape controls. If a native control changes
  on the executing environment, report its exact statement/schema/version
  evidence; do not paper it over with a version branch or changed expected value.
- [ ] Run `python -m ruff check app/business_definitions.py app/business_storage.py tests/test_business_storage.py`.
  Expected: `All checks passed!`.
- [ ] Keep this one coherent four-kind implementation uncommitted until its
  behavior documents and focused adjacent source tests pass in Task 4.

## Task 4: Keep behavior documents true and verify the coherent unit

**Files:** Modify `docs/architecture.md`, `docs/runtime-mechanism.md`, own
`docs/agent-claims.md` row; all three new Python files from Tasks 1–3.

- [ ] Insert this exact paragraph immediately after the top heading in each
  behavior document, preserving every existing paragraph:

```markdown
An unselected four-kind Business storage unit now declares Task, Project,
Anchor and Project context revision together, preserving all 42 existing source
fields. Source-shaped reads retain actual/fresh source column order and public
signed IDs. Writes use the existing tagged SQLite value codec, native affinities,
NOT NULL/CHECK/unique constraints and typed composite physical/public FKs;
Project retains its actual Anchor-type composite edge. Four public allocator
streams remain separate from physical Business IDs. Ordinary writes and schema
installation leave commit/rollback to an already active caller transaction;
all omitted row defaults share one SQLite statement. Public allocator exhaustion
matches the actual plain INSERT/lastrowid source writers: SQLITE_FULL rolls back
the caller transaction. Native INSERT RETURNING has a different demonstrated
exhaustion outcome and is not the source API contract. No constructor or caller
selects this unit. Existing Signal/document/evidence/date/event tables, all 19
incoming FK groups from 15 retained children, versions, migrations and providers
remain at their existing endpoints. Full ten-core-plus-ledger implementation
and source-body retirement are still incomplete. See
`docs/superpowers/plans/2026-10-15-business-four-kind-storage-plan.md`.
```

- [ ] Run `python -m pytest tests/test_business_storage.py tests/test_business_core_source.py -q`.
  Expected: the new **414 cases** and every existing native source case pass.
  Do not run the whole repository suite or tests in the production checkout.
- [ ] Run `python -m ruff check app/business_definitions.py app/business_storage.py tests/test_business_storage.py`.
  Expected: `All checks passed!`.
- [ ] Run `python -m compileall -q app/business_definitions.py app/business_storage.py tests/test_business_storage.py`.
  Expected: exit 0, no output. This compiles only the three owned files.
- [ ] Run `git diff --check`.
  Expected: exit 0, no whitespace errors.
- [ ] Run `git diff --name-only` and `git status --short`.
  Expected: only the three new Python files, two scoped behavior-document
  changes and the implementer's own claim row. Review the full own diff; do not
  stage someone else's claim edits or unrelated hunks.
- [ ] Mark the own claim locally verified, with the actual focused test counts,
  and explicit unselected/no-migration/no-provider/no-production boundaries.
- [ ] Stage and commit this coherent unit, after confirming only own changes:

```bash
git add app/business_definitions.py app/business_storage.py tests/test_business_storage.py docs/architecture.md docs/runtime-mechanism.md
git add -p docs/agent-claims.md
git diff --cached --stat
git commit -m "feat: add unselected four-kind Business storage"
```

- [ ] Report the exact commit, focused tests, settled representation and
  remaining release/body/endpoints boundary to the root. Root performs fresh
  sequential SPEC then QUALITY review before accepting the implementation.
  Do not push, activate, migrate, deploy, read production, invoke native CLI/
  provider flows or ask Derek to choose an execution method for this bounded
  already-authorized next unit.

## Plan self-review and planning-only validation

The code blocks define every introduced symbol, every field/default/type and
all 23 translated source CHECKs. The only imported private helpers are already
present in the existing finite action modules; no generic framework or unrelated
refactor is proposed. Four-kind native fixtures are reconstructed solely from
the accepted metadata. Tests cover both source variants, actual/fresh ordinals,
all original NOT NULL fields, all 23 CHECK probes, exact JSON text, BLOB/NULL/
NaN/infinity/native affinity, binary uniqueness, independent signed public IDs,
ignored/check/FK order, public-parent changes, statement-end FK updates,
allocator deletion/update/overflow, and caller rollback of rows plus waters.
The controlled synthetic statement clock catches per-field default SELECT drift
without sleeping. Physical/public coupling and NULL pairing have direct target
constraint probes, separate from native source parity assertions.

Planning validation loaded the proposed code blocks into temporary in-memory
Python modules and ran their tests against only `:memory:` SQLite databases:
**414 passed in 5.79s**, including the 16-case source statement-shape matrix.
All three planned files passed Ruff through stdin. This is executable-plan
preflight, not committed implementation acceptance. No `app/` or `tests/` file
was created or edited by the planning task. Temporary source/test scaffolding
under `/tmp/business-four-kind-plan.pv8E1R` is task-owned and removed before the
plan task ends. Root's fresh SPEC and QUALITY review remain required.
