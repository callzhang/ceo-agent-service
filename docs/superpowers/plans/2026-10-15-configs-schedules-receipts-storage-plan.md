# Configs, schedules, and Skill receipts storage implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an unselected native SQLite declaration and caller-transactional storage for all fifteen `configs` families, the single `schedules` family, and two Skill receipt `actions` kinds, deriving successful load bodies from existing configuration relations.

**Architecture:** Extend the accepted seven-kind `configs` table and source-shaped storage with the remaining eight kinds, typed physical references, and independent source allocators. Add a real one-kind `schedules` table and an explicit opt-in extension of the current Email `actions` declaration for two immutable Skill receipts. No Store/Email constructor or runtime reader selects these declarations in this unit.

**Tech Stack:** Python 3, `sqlite3` with SQLite JSON functions and generated columns, pytest, Ruff. All tests use synthetic, owned temporary databases; the accepted literal source metadata is a test oracle, never a runtime schema input.

---

## Scope and source authority

The approved destination remains ten core tables plus the migration ledger in `docs/superpowers/specs/2026-10-11-database-redesign-design.md`. Current production readback is 121 application tables at Store 2026-10-13.5/Email51. The paired Provider/Business/Email candidate is a separate, unselected 104-table branch state. This unit declares the target side of **18 source families → `configs`, `schedules`, and existing `actions`**. Its eventual net count would be 16 fewer tables, hence 88 from that candidate, only after a separate complete caller, copy, inverse, release and publication closure. Neither this plan nor its implementation claims that reduction now.

The authority for all 15 config source families (118 columns) and one schedule source family (17 columns) is `docs/superpowers/specs/2026-10-15-database-redesign-config-state-native-source.json`, captured from actual source and the genuine published `7dadc492` fresh constructor. The two receipt families (11 source columns) are frozen in `docs/superpowers/specs/2026-10-15-configs-load-receipt-dedup-probe.json`; accepted source behavior is `tests/test_configs_load_receipt_source.py` at `ee350913` with reader correction `8021df4e`, fresh sequential SPEC/QUALITY and four QUALITY cases in 33.25 seconds. These are bounded source evidence, not target or production-copy proof. The seven Email kinds already have 75 target cases and positive published account closure. Preserve that implementation and its test meaning.

The only CSV change in this planning unit is existing `runtime_skill_load_receipts.loaded_json` row 1223. It now says derive the public TEXT from `actions.config_id` → existing config bindings/revisions; it does **not** add a canonical body column. The approved design line 120 says one config representation plus referencing actions. Actual 4,105 successful receipts (3,678,270 UTF-8 bytes, 17 bodies) reconstructed byte-exactly on a frozen copy. Native SQL still permits malformed/noncanonical/BLOB bodies and late binding INSERT; that is a recorded parity exception, not grounds for a new policy or fallback body store.

### Exact file ownership for implementation

| File | Responsibility |
|---|---|
| `app/configs_definitions.py` | Expand finite 7-kind declaration to 15; 45 remaining columns, affinity/default/CHECK, typed relation columns, indexes and exact native mutation triggers. |
| `app/configs_storage.py` | Source-shaped reads/writes and source ID allocation for eight new kinds; maintain caller transaction. |
| `app/schedules_definitions.py` | One real 17-column `schedules` declaration with source constraints and indexes. |
| `app/schedules_storage.py` | One-kind source-shaped schedule insert/update/delete/projection; caller transaction. |
| `app/action_skill_config_definitions.py` | Two receipt kinds' generated fields, typed config FKs, checks, indexes, immutable and sequence triggers. |
| `app/action_skill_config_storage.py` | Source-shaped receipt insert/read, exact Python body reconstruction, and per-row source comparison function. |
| `app/action_storage.py` | Add a **default-false** opt-in parameter and compose the two action kinds only with the current Email action scaffold. Preserve default declaration bytes. |
| `tests/test_configs_storage.py`, `tests/test_configs_account_source.py` | Existing seven-kind and positive account fixtures remain meaningful; add complete opt-in scaffold only where new relations require it. |
| `tests/test_configs_skill_storage.py`, `tests/test_schedules_storage.py`, `tests/test_action_skill_config_storage.py` | New bounded actual/fresh differential, DML and source-shaped reader tests. |
| `docs/architecture.md`, `docs/runtime-mechanism.md` | In this unselected unit, state that new declarations are unselected. Update behavior only in the future caller/publication commit. |

No source JSON, accepted source fixture, Store/Email caller, default constructor, schema marker, migration, provider, native Agent CLI, production checkout or real database changes. Do not retain old config/schedule/receipt tables or views as a runtime compatibility layer. Their retirement requires the later atomic release, not this storage unit.

## Task 1: Finite eight-kind config source map

**Files:** Modify `app/configs_definitions.py`; create `tests/test_configs_skill_storage.py`.

- [ ] **Step 1: RED.** Add the test below; run `python -m pytest -q tests/test_configs_skill_storage.py::test_all_fifteen_kind_columns`. Expected failure: missing eight kinds.

```python
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from app.configs_definitions import CONFIG_FIELDS, CONFIG_PROJECTIONS

SOURCE = Path("docs/superpowers/specs/2026-10-15-database-redesign-config-state-native-source.json")

def test_all_fifteen_kind_columns():
    frozen = json.loads(SOURCE.read_text())
    assert set(CONFIG_FIELDS) == set(frozen["groups"]["configs"])
    assert sum(map(len, CONFIG_FIELDS.values())) == 118
    for kind, fields in CONFIG_FIELDS.items():
        assert fields == tuple(c["name"] for c in frozen["actual"]["tables"][kind]["columns"])
        assert kind in CONFIG_PROJECTIONS
```

- [ ] **Step 2: GREEN metadata.** Append the following **literal** entries to the existing dictionaries; do not load source JSON at runtime or loosen accepted seven-kind checks. `CONFIG_FRESH_FIELDS` inherits the eight entries from `CONFIG_FIELDS`; the only field-order actual/fresh difference in these sixteen families is already handled for `email_accounts` and the new schedule module handles `scheduled_tasks`.

```python
# Append to CONFIG_FIELDS (keep existing seven entries verbatim).
"managed_skills": tuple("id name display_name created_at".split()),
"managed_skill_revisions": tuple("id skill_id revision_number content sha256 parent_revision_id source created_at".split()),
"runtime_feedback_iteration_capabilities": tuple("config_id enabled created_at".split()),
"runtime_skill_bindings": tuple("config_id skill_id revision_id enabled load_order purpose".split()),
"runtime_skill_configs": tuple("id parent_id status created_at".split()),
"scheduled_task_config_versions": tuple("id snapshot_json".split()),
"scheduled_task_skill_refs": tuple("scheduled_task_id skill_source skill_name managed_skill_id managed_revision_id position".split()),
"wechat_reply_scopes": tuple("account_id target_type target_id conversation_id display_name trigger_mode enabled binding_status binding_evidence_json disabled_reason last_discovered_at updated_at".split()),

# Append to PREFIX.
"managed_skills": "skill", "managed_skill_revisions": "skill_revision",
"runtime_feedback_iteration_capabilities": "feedback_capability",
"runtime_skill_bindings": "skill_binding", "runtime_skill_configs": "runtime_config",
"scheduled_task_config_versions": "schedule_config",
"scheduled_task_skill_refs": "schedule_skill_ref", "wechat_reply_scopes": "wechat_scope",

# Append to ROWID_KINDS.
"managed_skills", "managed_skill_revisions", "runtime_feedback_iteration_capabilities",
"runtime_skill_bindings", "runtime_skill_configs", "scheduled_task_config_versions",
"scheduled_task_skill_refs", "wechat_reply_scopes",

# Append to SCALAR_KEY. The three composite-key families have no scalar public key.
"managed_skills": "id", "managed_skill_revisions": "id",
"runtime_feedback_iteration_capabilities": "config_id", "runtime_skill_configs": "id",
"scheduled_task_config_versions": "id",

# Append to PHYSICAL. These fields are stored as typed columns or numeric source
# aliases, not copied into payload_json.
"managed_skills": ("id",),
"managed_skill_revisions": ("id",),
"runtime_feedback_iteration_capabilities": ("config_id",),
"runtime_skill_bindings": (),
"runtime_skill_configs": ("id", "status"),
"scheduled_task_config_versions": ("id",),
"scheduled_task_skill_refs": (),
"wechat_reply_scopes": (),

# Append to DEFAULTS.
"managed_skills": {"created_at": "current_timestamp"},
"managed_skill_revisions": {"created_at": "current_timestamp"},
"runtime_feedback_iteration_capabilities": {"created_at": "current_timestamp"},
"runtime_skill_bindings": {"purpose": "''"},
"runtime_skill_configs": {"created_at": "current_timestamp"},
"wechat_reply_scopes": {"conversation_id": "''", "enabled": "1",
    "binding_status": "'unverified'", "binding_evidence_json": "'{}'",
    "disabled_reason": "''", "last_discovered_at": "''",
    "updated_at": "current_timestamp"},

# Append to INTEGERS.
"managed_skills": {"id"},
"managed_skill_revisions": {"id", "skill_id", "revision_number", "parent_revision_id"},
"runtime_feedback_iteration_capabilities": {"config_id", "enabled"},
"runtime_skill_bindings": {"config_id", "skill_id", "revision_id", "enabled", "load_order"},
"runtime_skill_configs": {"id", "parent_id"},
"scheduled_task_config_versions": {"id"},
"scheduled_task_skill_refs": {"scheduled_task_id", "managed_skill_id", "managed_revision_id", "position"},
"wechat_reply_scopes": {"enabled"},

# Append to REQUIRED; source INTEGER PRIMARY KEY aliases are allowed to be
# omitted on INSERT because SQLite allocates them. Every other required source
# column remains required after source defaults are applied.
"managed_skills": {"name", "display_name", "created_at"},
"managed_skill_revisions": {"skill_id", "revision_number", "content", "sha256", "source", "created_at"},
"runtime_feedback_iteration_capabilities": {"config_id", "enabled", "created_at"},
"runtime_skill_bindings": set(CONFIG_FIELDS["runtime_skill_bindings"]),
"runtime_skill_configs": {"status", "created_at"},
"scheduled_task_config_versions": {"snapshot_json"},
"scheduled_task_skill_refs": {"scheduled_task_id", "skill_source", "skill_name", "position"},
"wechat_reply_scopes": set(CONFIG_FIELDS["wechat_reply_scopes"]),

# Append to RULES. No imagined enum/JSON validity/uniqueness checks.
"managed_skills": (),
"managed_skill_revisions": ("skill_revision_revision_number>0",),
"runtime_feedback_iteration_capabilities": ("feedback_capability_enabled in (0,1)",),
"runtime_skill_bindings": ("skill_binding_enabled in (0,1)", "skill_binding_load_order>=0"),
"runtime_skill_configs": ("runtime_config_status in ('pending_restart','active','load_failed')",),
"scheduled_task_config_versions": (),
"scheduled_task_skill_refs": ("schedule_skill_ref_position>=0",),
"wechat_reply_scopes": (),
```

The existing `config_column` must return a generated numeric alias for the five `INTEGER PRIMARY KEY` source columns; `public_key` stays TEXT for typed composite FK matching, while source projections return INTEGER. Add this complete replacement and the generated declaration:

```python
NUMERIC_SCALAR_KINDS = frozenset({
    "managed_skills", "managed_skill_revisions",
    "runtime_feedback_iteration_capabilities", "runtime_skill_configs",
    "scheduled_task_config_versions",
})
AUTOINCREMENT_KINDS = frozenset({
    "managed_skills", "managed_skill_revisions", "runtime_skill_configs",
})

def config_column(kind: str, field: str) -> str:
    if (kind, field) == ("runtime_skill_configs", "status"):
        return "runtime_config_status"
    if field == SCALAR_KEY.get(kind):
        return "numeric_public_key" if kind in NUMERIC_SCALAR_KINDS else "public_key"
    return PREFIX[kind] + "_" + field

# In _definitions(), immediately after the existing public_key generated column:
"numeric_public_key integer generated always as "
"(case when record_type in ('managed_skills','managed_skill_revisions',"
"'runtime_feedback_iteration_capabilities','runtime_skill_configs',"
"'scheduled_task_config_versions') then cast(source_key as integer) end) virtual",
```

The new `PHYSICAL` entries omit only numeric source aliases and the mutable runtime-config status, each supplied by an explicit target column. Source FK fields remain in the tagged payload so their affinity and `typeof` stay visible to source projections; separate physical target refs enforce their relations.

- [ ] **Step 3: Test.** Run `python -m pytest -q tests/test_configs_skill_storage.py::test_all_fifteen_kind_columns tests/test_configs_storage.py`. Expected: all existing seven-kind cases plus this metadata case pass after the full Task 2 declaration is applied. Commit Tasks 1–2 together because the finite declaration must remain importable.

## Task 2: Eight-kind physical declaration and source relations

**Files:** Modify `app/configs_definitions.py`; extend `tests/test_configs_skill_storage.py`.

The remaining source identities are three independent AUTOINCREMENT aliases (`managed_skills.id`, `managed_skill_revisions.id`, `runtime_skill_configs.id`), two non-AUTOINCREMENT aliases (`runtime_feedback_iteration_capabilities.config_id`, `scheduled_task_config_versions.id`), and three hidden ROWID families (`runtime_skill_bindings`, `scheduled_task_skill_refs`, `wechat_reply_scopes`). Existing seven kinds have four hidden ROWID and three immutable WITHOUT ROWID layouts. Preserve source allocator behavior: hidden/non-auto aliases use source max+1, including negative-only `-7 → -6`, empty → 1, and max-int random candidate; the three auto aliases use independent `sqlite_sequence` names `configs:<kind>` with `max(seq,maxrowid,0)+1`. The 700/901/901 high-water values are synthetic source-oracle insert cases, not production sequences or constants baked into target.

Typed relation contract, all native NO ACTION unless named: revision → Skill; revision → parent revision; feedback capability → runtime config; binding → runtime config, Skill, revision; runtime config → parent config; schedule Skill ref → real `schedules`, Skill, revision. `runtime_skill_bindings` source has unique `(config_id,skill_id)` and `(config_id,load_order)`; schedule refs unique `(scheduled_task_id,position)`; managed revision unique `(skill_id,sha256,source)` and `(skill_id,revision_number)`; Skill `name` unique; snapshot `snapshot_json` unique TEXT NOT NULL without `json_valid`; WeChat scope key `(account_id,target_type,target_id)` only. No one-active-config singleton. Source app checks that a revision belongs to its Skill, and binds a matching revision; those are **not** new SQL FK constraints.

- [ ] **Step 1: RED relational cases.** Add the following focused synthetic checks and run them. Expected: missing kind/typed columns or absent real schedule parent makes them fail. A missing-parent-table `OperationalError` is *not* a passing parity test.

```python
import pytest
from app.configs_storage import create_configs_schema, insert_config
from app.schedules_storage import create_schedules_schema

def test_real_schedule_parent_and_wrong_kind_fk():
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("pragma foreign_keys=on")
        create_schedules_schema(db)
        create_configs_schema(db)
        db.execute("begin")
        with pytest.raises(sqlite3.IntegrityError):
            insert_config(db, "scheduled_task_skill_refs", {
                "scheduled_task_id": 98765, "skill_source": "managed",
                "skill_name": "x", "position": 0,
            })
        db.rollback()

def test_snapshot_raw_text_unique_and_no_json_check():
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("pragma foreign_keys=on")
        create_schedules_schema(db)
        create_configs_schema(db)
        db.execute("begin")
        first = insert_config(db, "scheduled_task_config_versions", {"snapshot_json": "not-json"})
        assert first.lastrowid == 1
        with pytest.raises(sqlite3.IntegrityError):
            insert_config(db, "scheduled_task_config_versions", {"snapshot_json": "not-json"})
        db.rollback()
```

- [ ] **Step 2: Add the finite typed relation declaration.** Use this table as the single literal wiring input; each `ref` is a physical INTEGER column. A generated parent-kind and generated source public-key column accompany each ref. An FK on `(ref, kind, public-key)` references `configs(id,record_type,public_key)`; `scheduled_task_id` alone references `schedules(id)` because schedules has one kind and direct source ID. The source field lives in payload unless it is its own key/status. These tuples are code, not runtime-loaded metadata:

```python
# (child kind, source field, physical ref column, parent kind, delete action)
CONFIG_REFS = (
    ("managed_skill_revisions", "skill_id", "skill_revision_skill_ref", "managed_skills", "no action"),
    ("managed_skill_revisions", "parent_revision_id", "skill_revision_parent_ref", "managed_skill_revisions", "no action"),
    ("runtime_feedback_iteration_capabilities", "config_id", "feedback_capability_config_ref", "runtime_skill_configs", "no action"),
    ("runtime_skill_bindings", "config_id", "skill_binding_config_ref", "runtime_skill_configs", "no action"),
    ("runtime_skill_bindings", "skill_id", "skill_binding_skill_ref", "managed_skills", "no action"),
    ("runtime_skill_bindings", "revision_id", "skill_binding_revision_ref", "managed_skill_revisions", "no action"),
    ("runtime_skill_configs", "parent_id", "runtime_config_parent_ref", "runtime_skill_configs", "no action"),
    ("scheduled_task_skill_refs", "managed_skill_id", "schedule_skill_ref_skill_ref", "managed_skills", "no action"),
    ("scheduled_task_skill_refs", "managed_revision_id", "schedule_skill_ref_revision_ref", "managed_skill_revisions", "no action"),
)
SCHEDULE_REF = ("scheduled_task_skill_refs", "scheduled_task_id", "schedule_skill_ref_schedule_ref")
```

The complete relation compiler is below. Place it after all generated source columns have been appended and before final `table` assembly inside `_definitions()`. `json_type`/source affinity checks remain the finite per-kind checks from Task 1. `config_column()` must project all source fields, including each physical source field; for a referenced field use the payload-generated column, so `PHYSICAL` must **not** omit it from `config_payload_fields`. Replace `config_payload_fields` accordingly:

```python
def config_payload_fields(kind: str) -> tuple[str, ...]:
    # Physical target references supplement, rather than replace, original
    # source fields. Only source scalar keys and existing seven-kind direct
    # physical columns are absent from the tagged payload.
    return tuple(field for field in CONFIG_FIELDS[kind]
                 if field not in PHYSICAL[kind] and field != SCALAR_KEY.get(kind))

# In _definitions():
for child, field, ref, parent, on_delete in CONFIG_REFS:
    source = config_column(child, field)
    # The scalar config_id of feedback capability is numeric_public_key.
    kind_col, key_col = ref + "_kind", ref + "_public_key"
    columns.extend((
        f"{ref} integer",
        f"{kind_col} text generated always as "
        f"(case when record_type='{child}' then '{parent}' end) virtual",
        f"{key_col} text generated always as "
        f"(case when record_type='{child}' then cast({source} as text) end) virtual",
    ))
    checks.append(f"check(record_type='{child}' or {ref} is null)")
    checks.append(f"check(record_type<>'{child}' or "
                  f"(({source} is null and {ref} is null) or "
                  f"({source} is not null and {ref} is not null)))")
    checks.append(f"foreign key({ref},{kind_col},{key_col}) "
                  f"references configs(id,record_type,public_key) "
                  f"on update no action on delete {on_delete}")
child, field, ref = SCHEDULE_REF
source = config_column(child, field)
columns.append(f"{ref} integer references schedules(id) on update no action on delete no action")
checks += (f"check(record_type='{child}' or {ref} is null)",
           f"check(record_type<>'{child}' or ({source} is not null and {ref}={source}))")
```

`runtime_skill_configs.status` is a mutable physical TEXT column. Extend `_definitions()` with `"runtime_config_status text"`, and make `config_column()` return `"runtime_config_status"` for `(kind, field)==("runtime_skill_configs", "status")`. The native status trigger below then reads a real column; no JSON body duplicate exists. Extend the existing `source_key` exclusivity CHECK to permit exactly the five numeric scalar kinds as well as the three original Email scalar kinds; no composite-key kind may set it. Use this exact replacement inside `checks`:

```python
"check((record_type in ('email_accounts','email_category_configs',"
"'email_training_snapshots','managed_skills','managed_skill_revisions',"
"'runtime_feedback_iteration_capabilities','runtime_skill_configs',"
"'scheduled_task_config_versions')) or source_key is null)",
"check(record_type='runtime_skill_configs' or runtime_config_status is null)",
"check(record_type not in ('managed_skills','managed_skill_revisions',"
"'runtime_feedback_iteration_capabilities','runtime_skill_configs',"
"'scheduled_task_config_versions') or (source_key is not null and source_key=legacy_key))",
```

`CONFIG_REFS` source fields project from generated payload fields. Add finite indexes and triggers after existing seven-kind indexes/triggers:

```python
indexes += (
    "create unique index idx_configs_skill_name on configs(skill_name) where record_type='managed_skills'",
    "create unique index idx_configs_revision_identity on configs(skill_revision_skill_id,skill_revision_sha256,skill_revision_source) where record_type='managed_skill_revisions'",
    "create unique index idx_configs_revision_number on configs(skill_revision_skill_id,skill_revision_revision_number) where record_type='managed_skill_revisions'",
    "create unique index idx_configs_binding_skill on configs(skill_binding_config_id,skill_binding_skill_id) where record_type='runtime_skill_bindings'",
    "create unique index idx_configs_binding_order on configs(skill_binding_config_id,skill_binding_load_order) where record_type='runtime_skill_bindings'",
    "create unique index idx_configs_schedule_snapshot on configs(schedule_config_snapshot_json) where record_type='scheduled_task_config_versions'",
    "create unique index idx_configs_schedule_ref_position on configs(schedule_skill_ref_scheduled_task_id,schedule_skill_ref_position) where record_type='scheduled_task_skill_refs'",
    "create unique index idx_configs_wechat_scope on configs(wechat_scope_account_id,wechat_scope_target_type,wechat_scope_target_id) where record_type='wechat_reply_scopes'",
    "create index idx_configs_binding_config_order on configs(skill_binding_config_id,skill_binding_load_order) where record_type='runtime_skill_bindings'",
)
for kind, error in (
    ("managed_skill_revisions", "managed Skill revisions are immutable"),
    ("runtime_feedback_iteration_capabilities", "feedback iteration capability records are immutable"),
    ("runtime_skill_bindings", "runtime Skill bindings are immutable"),
):
    for event in ("update", "delete"):
        triggers.append(f"create trigger trg_configs_{PREFIX[kind]}_{event} "
                        f"before {event} on configs when old.record_type='{kind}' "
                        f"begin select raise(abort,'{error}'); end")
triggers += (
    "create trigger trg_configs_runtime_config_delete before delete on configs "
    "when old.record_type='runtime_skill_configs' "
    "begin select raise(abort,'runtime Skill configurations are immutable'); end",
    "create trigger trg_configs_runtime_config_update before update on configs "
    "when old.record_type='runtime_skill_configs' and ("
    "new.id<>old.id or new.legacy_key<>old.legacy_key or "
    "new.runtime_config_parent_ref is not old.runtime_config_parent_ref or "
    "new.runtime_config_created_at<>old.runtime_config_created_at or "
    "old.runtime_config_status<>'pending_restart' or "
    "new.runtime_config_status not in ('active','load_failed') or "
    "(new.runtime_config_status='active' and not exists("
    "select 1 from actions a where a.record_type='runtime_skill_load_receipts' "
    "and a.runtime_config_ref=old.id and a.skill_error='' )) or "
    "(new.runtime_config_status='load_failed' and not exists("
    "select 1 from actions a where a.record_type='runtime_skill_load_receipts' "
    "and a.runtime_config_ref=old.id and a.skill_error<>'' ))) "
    "begin select raise(abort,'runtime Skill activation requires matching load receipt'); end",
)
```

For direct SQLite inserts as well as helper inserts, append the following three source allocator triggers. They preserve independent AUTOINCREMENT watermarks without a new user table; an explicit negative/zero source ID does not raise the watermark. The helper's `_reserve_auto` is idempotent with these triggers.

```python
for kind in AUTOINCREMENT_KINDS:
    name = "configs:" + kind
    triggers.append(
        f"create trigger trg_configs_{kind}_sequence before insert on configs "
        f"when new.record_type='{kind}' begin "
        f"insert into sqlite_sequence(name,seq) select '{name}',0 "
        f"where not exists(select 1 from sqlite_sequence where name='{name}'); "
        f"update sqlite_sequence set seq=max(seq,cast(new.legacy_key as integer)) "
        f"where name='{name}'; end"
    )
```

The active trigger checks receipt existence, while the source checks body/member match. In this target the body is derived from immutable bindings and revisions, and the target insert helper validates a supplied source-shaped success body against that exact map before storing no body. This is equivalent for supported app histories; the native late-binding and malformed-body exceptions remain explicitly outside parity. To prevent accepted successes changing *after* insertion, no new SQL late-binding ban is introduced in this unselected unit. Future publication must gate every row and close the actual writer path in one transaction.

**Important:** `managed_skill_revisions` and `runtime_feedback_iteration_capabilities` source aliases must be projected as INTEGER, `runtime_skill_configs.status` must remain mutable only through its native transition, and immutable revisions/bindings must reject UPDATE/DELETE. If these snippets conflict with existing generated names, the test must fail; resolve against actual `pragma table_xinfo` before committing. Do not call an absent generated column a parity success.

- [ ] **Step 3: GREEN with actual/fresh oracle.** Build source tables from the frozen literal SQL in two private in-memory connections; build target using `create_schedules_schema`, the complete opt-in `actions` scaffold from Task 5, then `create_configs_schema`. Compare every source column value and `typeof` from `config_projection(kind, fresh=variant)` after bounded inserts/updates. Include key, defaults, NULL/BLOB/REAL affinity, every UNIQUE and CHECK, FK failure and success, immutable errors, and all three allocator streams. Use the following assertion core:

```python
def assert_source_projection(source, target, kind, order):
    actual = source.execute(f"select * from {kind} order by {order}").fetchall()
    projected = target.execute(CONFIG_PROJECTIONS[kind] + f" order by {order}").fetchall()
    assert projected == actual
    for field in CONFIG_FIELDS[kind]:
        assert [r[0] for r in source.execute(f"select typeof({field}) from {kind} order by {order}")] == [
            r[0] for r in target.execute(
                "select typeof(" + config_column(kind, field) + ") from configs "
                "where record_type=? order by " + order, (kind,)
            )
        ]
```

Use these exact synthetic rows for the eight new families. Insert `managed_skills`, `managed_skill_revisions`, `runtime_skill_configs`, `scheduled_task_config_versions`, and one real schedule first; then insert the dependent rows. For `scheduled_task_skill_refs`, use the `schedules` row with `id=500`. The row dictionaries mention all **45** new source columns (including nullable and defaulted fields); the test must compare source and target source projections and `typeof` for each. Explicit timestamps make the comparison deterministic.

```python
SYNTHETIC_CONFIG_ROWS = {
    "managed_skills": {"id": 20, "name": "skill-20", "display_name": "Skill 20", "created_at": "2026-10-15 00:00:00"},
    "managed_skill_revisions": {"id": 200, "skill_id": 20, "revision_number": 1,
        "content": "# Skill 20", "sha256": "digest-é", "parent_revision_id": None,
        "source": "test", "created_at": "2026-10-15 00:00:00"},
    "runtime_skill_configs": {"id": 300, "parent_id": None, "status": "pending_restart",
        "created_at": "2026-10-15 00:00:00"},
    "scheduled_task_config_versions": {"id": 400, "snapshot_json": "not-json"},
    "runtime_feedback_iteration_capabilities": {"config_id": 300, "enabled": 1,
        "created_at": "2026-10-15 00:00:00"},
    "runtime_skill_bindings": {"config_id": 300, "skill_id": 20, "revision_id": 200,
        "enabled": 1, "load_order": 0, "purpose": ""},
    "scheduled_task_skill_refs": {"scheduled_task_id": 500, "skill_source": "managed",
        "skill_name": "skill-20", "managed_skill_id": 20, "managed_revision_id": 200,
        "position": 0},
    "wechat_reply_scopes": {"account_id": "wx", "target_type": "group",
        "target_id": "group-1", "conversation_id": "", "display_name": "Group",
        "trigger_mode": "always", "enabled": 1, "binding_status": "unverified",
        "binding_evidence_json": "{}", "disabled_reason": "", "last_discovered_at": "",
        "updated_at": "2026-10-15 00:00:00"},
}
assert sum(map(len, SYNTHETIC_CONFIG_ROWS.values())) == 45
```

Keep the accepted seven-kind cases as independent tests: change their declaration-count assertion from 7/73 to 15/118, and separately assert the original seven kinds/73 fields still project unchanged. Commit the complete declaration and its focused cases after green.

## Task 3: Source-shaped config writes and independent IDs

**Files:** Modify `app/configs_storage.py`; extend `tests/test_configs_skill_storage.py`.

- [ ] **Step 1: RED allocator tests.** In one target transaction, insert explicit 700/901/901 IDs into the three auto families and assert their next IDs are 701/902/902. Insert `-7` then implicit in each non-auto alias and hidden ROWID family; assert `-6`; delete all in a mutable family and assert next 1. Test max-int random candidate against native SQLite via a bounded same-seed independent source connection; do not assert a hardcoded random value. All source/target IDs are independent per kind.

- [ ] **Step 2: Implement finite allocation and typed relation writes.** The following functions replace the one-size `_next_source_rowid` call and extend `_stored`. Keep `_lock` and caller transaction semantics unchanged. `sqlite_sequence` exists because `configs.id` is AUTOINCREMENT. Explicit auto source IDs update their own sequence water. Sequence names are internal `sqlite_sequence` rows, not additional user tables.

```python
from app.configs_definitions import AUTOINCREMENT_KINDS, CONFIG_REFS, SCHEDULE_REF

MAX_ROWID = 2**63 - 1

def _auto_sequence(db: sqlite3.Connection, kind: str) -> int:
    row = db.execute("select seq from sqlite_sequence where name=?", ("configs:" + kind,)).fetchone()
    return row[0] if row else 0

def _reserve_auto(db: sqlite3.Connection, kind: str, value: int) -> None:
    name = "configs:" + kind
    db.execute("insert into sqlite_sequence(name,seq) select ?,0 "
               "where not exists(select 1 from sqlite_sequence where name=?)", (name, name))
    db.execute("update sqlite_sequence set seq=max(seq,?) where name=?", (value, name))

def _next_auto_rowid(db: sqlite3.Connection, kind: str) -> int:
    maximum = db.execute("select max(cast(legacy_key as integer)) from configs "
                         "where record_type=?", (kind,)).fetchone()[0]
    value = max(_auto_sequence(db, kind), maximum or 0, 0)
    if value == MAX_ROWID:
        raise sqlite3.OperationalError("database or disk is full")
    return value + 1

def _source_identity(db: sqlite3.Connection, kind: str, source_rowid) -> int:
    if source_rowid is _UNSET or _source_bound_null(source_rowid):
        return (_next_auto_rowid(db, kind) if kind in AUTOINCREMENT_KINDS
                else _next_source_rowid(db, kind))
    return _native_integer_primary_key(source_rowid)

# In insert_config, before the ROWID branch, reconcile the source alias with the
# existing source_rowid argument. Both spellings must denote the same source ID.
if kind in NUMERIC_SCALAR_KINDS and SCALAR_KEY[kind] in values:
    supplied_id = values[SCALAR_KEY[kind]]
    if source_rowid is not _UNSET and source_rowid != supplied_id:
        raise ValueError("conflicting source integer primary key")
    source_rowid = supplied_id
# Replace the ROWID branch's identity expression with:
identity = _source_identity(db, kind, source_rowid)
# Before _completed for numeric scalar kinds:
values = {**values, SCALAR_KEY[kind]: identity}
# After a successful insert (cursor.rowcount==1):
if kind in AUTOINCREMENT_KINDS and cursor.rowcount:
    _reserve_auto(db, kind, identity)

# In _stored, after existing seven-kind physical columns:
if kind in NUMERIC_SCALAR_KINDS:
    data["source_key"] = str(complete[SCALAR_KEY[kind]])
if kind == "runtime_skill_configs":
    data["runtime_config_status"] = complete["status"]
for child, field, ref, parent, _ in CONFIG_REFS:
    if kind == child:
        key = complete[field]
        data[ref] = None if key is None else _parent_id(db, parent, str(key))
if kind == SCHEDULE_REF[0]:
    key = complete[SCHEDULE_REF[1]]
    data[SCHEDULE_REF[2]] = key if db.execute(
        "select 1 from schedules where id=?", (key,)
    ).fetchone() else 0
```

The scalar source ID is implicit in `complete` only after identity is chosen: add `values = {**values, SCALAR_KEY[kind]: identity}` for numeric scalar kinds immediately before `_completed`; for `runtime_feedback_iteration_capabilities.config_id`, the alias is simultaneously the source FK. An omitted key uses native rowid allocation and succeeds only if that generated key already names a runtime config parent. `scheduled_task_config_versions.id` may also be omitted. For typed FK lookup, `_parent_id` uses `public_key` TEXT; `str(key)` is correct after SQLite integer alias normalization. A missing non-null parent becomes ref `0` and fails the declared composite FK. Source `NULL` optional parent stays `NULL`.

`_next_source_rowid` currently computes max+1 and handles max-int with an owned scratch SQLite table; preserve that for the five non-auto new kinds. For new composite-key kinds, include all key fields in the payload and use `legacy_key` as the signed source hidden ROWID. Preserve mutable rekey for `managed_skills.id` and `scheduled_task_config_versions.id`; reject changes to immutable aliases through source triggers. When `update_config` changes a numeric scalar ID, recompute both `source_key` and `legacy_key` from the new alias; use the exact replacement below after `current.update(values)` and before `_stored`. When it changes a referenced parent key, physicalid+kind+publickey FKs must return native NO ACTION. No silent reparenting.

```python
if kind in NUMERIC_SCALAR_KINDS and SCALAR_KEY[kind] in values:
    new_alias = _native_integer_primary_key(values[SCALAR_KEY[kind]], update=True)
    if new_source_rowid is not _UNSET and new_source_rowid != new_alias:
        raise ValueError("conflicting source integer primary key update")
    legacy = str(new_alias)
```

Place this after the current `legacy` assignment and before `_stored` in the ROWID branch. Referenced parent keys retain native NO ACTION FK behavior.

- [ ] **Step 3: GREEN.** Run focused cases and the existing 75 seven-kind tests. Expected all pass with an explicit complete target fixture. Commit storage separately from declaration only if the test package remains importable and green at each commit.

## Task 4: Real single-kind schedules declaration and storage

**Files:** Create `app/schedules_definitions.py`, `app/schedules_storage.py`, `tests/test_schedules_storage.py`.

`scheduled_tasks` is the **only** unselected real FK parent for `scheduled_task_skill_refs`; a stub is not enough. Its 17 actual fields are `id,migration_key,name,prompt,cron_expression,timezone,runtime_id,runtime_options_json,working_directory,enabled,version,created_at,updated_at,deleted_at,required_runtime_capabilities_json,command,description`. Genuine fresh DDL puts `description` and `command` earlier, but column set/default/CHECK/index semantics are the same. Use one target `schedules` table with direct `id integer primary key autoincrement`; no invented second public-ID column. Source `id` is already the target physical ID. Preserve two indexes, `enabled in (0,1)`, `version>0`, unique non-null `migration_key`, defaults, nullable `deleted_at`, and source CAS as used by the Store caller. There is no JSON validity CHECK on runtime options or capabilities.

- [ ] **Step 1: RED.** Add exact 17-column/order/default and CAS tests against genuine frozen actual/fresh source declarations. Expected import failure before the new modules exist.

```python
from app.schedules_definitions import SCHEDULE_FIELDS
from app.schedules_storage import create_schedules_schema, insert_schedule, update_schedule

def test_schedule_17_fields_and_cas():
    assert len(SCHEDULE_FIELDS) == 17
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("pragma foreign_keys=on")
        create_schedules_schema(db)
        db.execute("begin")
        key = insert_schedule(db, {"name": "n", "prompt": "p", "cron_expression": "* * * * *",
            "timezone": "UTC", "runtime_id": "claude", "enabled": 1})
        assert key == 1
        assert update_schedule(db, key, 1, {"name": "changed"}) == 1
        assert update_schedule(db, key, 1, {"name": "stale"}) == 0
        assert db.execute("select version,name,runtime_options_json,description from schedules").fetchone() == (2,"changed","{}","")
        db.rollback()
```

- [ ] **Step 2: Implement declaration.** This is the complete target DDL. The `actual` and `fresh` source projections choose their respective field order from the frozen metadata; do not infer layout from `select *` in later copy logic.

```python
"""Unselected one-kind schedules declaration."""
SCHEDULE_FIELDS = tuple("id migration_key name prompt cron_expression timezone runtime_id runtime_options_json working_directory enabled version created_at updated_at deleted_at required_runtime_capabilities_json command description".split())
SCHEDULE_FRESH_FIELDS = tuple("id migration_key name description prompt command cron_expression timezone runtime_id runtime_options_json required_runtime_capabilities_json working_directory enabled version created_at updated_at deleted_at".split())
SCHEDULE_DDL = """create table schedules (
 id integer primary key autoincrement,
 migration_key text, name text not null, prompt text not null,
 cron_expression text not null, timezone text not null, runtime_id text not null,
 runtime_options_json text not null default '{}',
 working_directory text not null default '',
 enabled integer not null check(enabled in (0,1)),
 version integer not null default 1 check(version>0),
 created_at text not null default current_timestamp,
 updated_at text not null default current_timestamp,
 deleted_at text,
 required_runtime_capabilities_json text not null default '[]',
 command text not null default '', description text not null default ''
)"""
SCHEDULE_INDEXES = (
    "create index idx_schedules_enabled on schedules(enabled,deleted_at,id)",
    "create unique index idx_schedules_migration_key on schedules(migration_key) where migration_key is not null",
)

def schedule_projection(*, fresh: bool = False) -> str:
    fields = SCHEDULE_FRESH_FIELDS if fresh else SCHEDULE_FIELDS
    return "select " + ",".join(fields) + " from schedules"
```

- [ ] **Step 3: Implement caller-transactional storage.** This is the complete module; source defaults are evaluated by SQLite. The source Store's 17-field full update path and `version` CAS must be tested against this helper; later caller conversion may use a narrower method, but may not change this table's source semantics.

```python
"""Unselected caller-transactional schedules storage."""
import sqlite3
from app.schedules_definitions import SCHEDULE_DDL, SCHEDULE_FIELDS, SCHEDULE_INDEXES

def create_schedules_schema(db: sqlite3.Connection) -> None:
    db.execute(SCHEDULE_DDL)
    for sql in SCHEDULE_INDEXES:
        db.execute(sql)

def _write_ready(db: sqlite3.Connection, values: dict) -> None:
    if not db.in_transaction:
        raise sqlite3.ProgrammingError("schedule writes require caller transaction")
    if set(values) - set(SCHEDULE_FIELDS):
        raise ValueError("unknown schedule field")

def insert_schedule(db: sqlite3.Connection, values: dict) -> int:
    _write_ready(db, values)
    keys = tuple(values)
    sql = ("insert into schedules(" + ",".join(keys) + ") values(" +
           ",".join("?" for _ in keys) + ")")
    return db.execute(sql, tuple(values.values())).lastrowid

def update_schedule(db: sqlite3.Connection, identity: int, expected_version: int,
                    values: dict) -> int:
    _write_ready(db, values)
    if not values or "id" in values or "version" in values:
        raise ValueError("CAS update needs mutable fields only")
    keys = tuple(values)
    sql = ("update schedules set " + ",".join(k + "=?" for k in keys) +
           ",version=version+1 where id=? and version=? and deleted_at is null")
    return db.execute(sql, (*values.values(), identity, expected_version)).rowcount

def delete_schedule(db: sqlite3.Connection, identity: int, expected_version: int,
                    deleted_at: str) -> int:
    _write_ready(db, {"deleted_at": deleted_at})
    return db.execute("update schedules set deleted_at=?,enabled=0,version=version+1 "
                      "where id=? and version=? and deleted_at is null",
                      (deleted_at, identity, expected_version)).rowcount
```

- [ ] **Step 4: GREEN.** Compare source/target all 17 values and storage classes after insert/default, duplicate migration key, explicit IDs and sequence, enabled/version CHECK, CAS success/stale/deleted, and schedule Skill-ref FK. Run `python -m pytest -q tests/test_schedules_storage.py`; commit.

## Task 5: Two immutable Skill receipt action kinds

**Files:** Create `app/action_skill_config_definitions.py`, `app/action_skill_config_storage.py`, `tests/test_action_skill_config_storage.py`; modify `app/action_storage.py` behind a default-false `include_skill_config_receipts` parameter.

Both receipts keep source ID/ordering and independent AUTOINCREMENT high-water streams, typed FK to revision/config, PID/error/path/SHA/time, and native immutable UPDATE/DELETE. `loaded_json` is **not stored**. The source writer's success body is exactly `json.dumps(dict[int,str], sort_keys=True)` with default `ensure_ascii=True` and spaces; failures return literal `'{}'`. Numeric keys 20 before 100 test a real byte difference from sorting string keys. Reconstruct within one SQLite read transaction from the receipt's config, enabled bindings and revision SHA. SQL `json_group_object` is not byte-compatible.

- [ ] **Step 1: RED.** New test inserts complete config/bindings/revisions, two successes and a failure through the target helper. Assert exact DTO `(id,config_id,pid,loaded_json,error,created_at)`, UTF-8 bytes, list/latest order, independent explicit-high-ID allocators, immutable UPDATE/DELETE, wrong-kind FK, and rollback. The test must fail before adding the opt-in kind.

```python
def test_success_body_uses_integer_sort_and_original_json_defaults():
    expected = json.dumps({20: "dígést", 100: "alpha"}, sort_keys=True)
    assert expected == '{"20": "d\\u00edg\\u00e9st", "100": "alpha"}'
    assert expected != json.dumps({"20": "dígést", "100": "alpha"}, sort_keys=True)
    # After inserting the real typed target parents/bindings/receipt:
    assert receipt.loaded_json.encode("utf-8") == expected.encode("utf-8")
    assert db.execute("select json_type(payload_json,'$.loaded_json') from actions "
                      "where record_type='runtime_skill_load_receipts'").fetchone() == (None,)
```

- [ ] **Step 2: Extend only an explicit current Email action scaffold.** The complete two-kind schema parts belong in `app/action_skill_config_definitions.py`; import and add them in `_action_definitions` only when `include_skill_config_receipts=True`. Require `include_followup_sync=True`, `include_email=True`, `include_reply=True`, and the current `reply_variant`; never alter any default `_action_definitions()` output or static `ACTION_TABLE_DDL`. Propagate the flag through `_current_objects`, `_create_action_objects`, `create_action_schema`, `action_schema_is_current`, and their arguments consistently. Refuse a mixed existing action table rather than repairing it as the opt-in variant. Caller installs **real** `schedules` and complete 15-kind `configs` before first target receipt DML; the existing seven-kind tests keep their original fixture unless they exercise the new family.

```python
from app.action_email_definitions import _encoded_value, _encoded_value_check

SKILL_RECEIPT_KINDS = ("managed_skill_export_receipts", "runtime_skill_load_receipts")

def skill_receipt_schema_parts():
    value = lambda field: _encoded_value("payload_json", "$." + field)
    generated = (
        f"skill_revision_id integer generated always as (case when record_type='managed_skill_export_receipts' then {value('revision_id')} end) virtual",
        f"skill_sha256 text generated always as (case when record_type='managed_skill_export_receipts' then {value('sha256')} end) virtual",
        f"skill_path text generated always as (case when record_type='managed_skill_export_receipts' then {value('path')} end) virtual",
        f"skill_config_id integer generated always as (case when record_type='runtime_skill_load_receipts' then {value('config_id')} end) virtual",
        f"skill_pid integer generated always as (case when record_type='runtime_skill_load_receipts' then {value('pid')} end) virtual",
        f"skill_error text generated always as (case when record_type='runtime_skill_load_receipts' then {value('error')} end) virtual",
        f"skill_created_at text generated always as ({value('created_at')}) virtual",
        "skill_revision_kind text generated always as (case when record_type='managed_skill_export_receipts' then 'managed_skill_revisions' end) virtual",
        "skill_config_kind text generated always as (case when record_type='runtime_skill_load_receipts' then 'runtime_skill_configs' end) virtual",
        "skill_revision_public_key text generated always as (cast(skill_revision_id as text)) virtual",
        "skill_config_public_key text generated always as (cast(skill_config_id as text)) virtual",
    )
    physical = ("skill_revision_ref integer", "runtime_config_ref integer")
    checks = (
        "check(record_type<>'managed_skill_export_receipts' or (skill_revision_ref is not null and skill_revision_id is not null and skill_sha256 is not null and skill_path is not null and skill_created_at is not null and json_remove(payload_json,'$.revision_id','$.sha256','$.path','$.created_at')='{}'))",
        "check(record_type<>'runtime_skill_load_receipts' or (runtime_config_ref is not null and skill_config_id is not null and skill_pid>0 and skill_error is not null and skill_created_at is not null and json_remove(payload_json,'$.config_id','$.pid','$.error','$.created_at')='{}'))",
        "check(record_type in ('managed_skill_export_receipts','runtime_skill_load_receipts') or (skill_revision_ref is null and runtime_config_ref is null))",
        "check(record_type<>'managed_skill_export_receipts' or runtime_config_ref is null)",
        "check(record_type<>'runtime_skill_load_receipts' or skill_revision_ref is null)",
        "foreign key(skill_revision_ref,skill_revision_kind,skill_revision_public_key) references configs(id,record_type,public_key)",
        "foreign key(runtime_config_ref,skill_config_kind,skill_config_public_key) references configs(id,record_type,public_key)",
    )
    indexes = (
        "create index idx_actions_skill_export_revision on actions(skill_revision_ref,cast(legacy_key as integer)) where record_type='managed_skill_export_receipts'",
        "create index idx_actions_skill_load_config on actions(runtime_config_ref,cast(legacy_key as integer)) where record_type='runtime_skill_load_receipts'",
        "create index idx_actions_skill_load_pid on actions(skill_pid,cast(legacy_key as integer)) where record_type='runtime_skill_load_receipts'",
    )
    triggers = []
    for kind, message in (("managed_skill_export_receipts", "managed Skill export receipts are append-only"),
                          ("runtime_skill_load_receipts", "runtime Skill load receipts are append-only")):
        for event in ("update", "delete"):
            triggers.append(f"create trigger trg_actions_{kind}_{event} before {event} on actions "
                            f"when old.record_type='{kind}' begin select raise(abort,'{message}'); end")
        name = "actions:" + kind
        triggers.append(f"create trigger trg_actions_{kind}_sequence before insert on actions "
                        f"when new.record_type='{kind}' begin "
                        f"insert into sqlite_sequence(name,seq) select '{name}',0 "
                        f"where not exists(select 1 from sqlite_sequence where name='{name}'); "
                        f"update sqlite_sequence set seq=max(seq,cast(new.legacy_key as integer)) "
                        f"where name='{name}'; end")
    return generated, physical, checks, indexes, tuple(triggers)
```

Wire `include_skill_config_receipts: bool = False` through `_action_definitions`, `_create_action_objects`, `_current_objects`, `action_schema_is_current`, and `create_action_schema` as the last named parameter. Every recursive call to one of these five functions forwards it unchanged; retain all existing default arguments and constants. Add this exact conditional structure to `_action_definitions` at the indicated existing assembly points:

```python
from app.action_skill_config_definitions import SKILL_RECEIPT_KINDS, skill_receipt_schema_parts

# Immediately after the existing include_followup_sync validation:
if include_skill_config_receipts and not include_followup_sync:
    raise ValueError("Skill receipts require current Email action scaffold")
# Immediately after kinds += SYNC_TYPES_ORDER:
if include_skill_config_receipts:
    kinds += SKILL_RECEIPT_KINDS
    skill_generated, skill_physical, skill_checks, skill_indexes, skill_triggers = (
        skill_receipt_schema_parts()
    )
# In table assembly, immediately before the payload_json column:
+ ("," + ",".join((*skill_physical, *skill_generated))
   if include_skill_config_receipts else "")
# Immediately before table += ")":
if include_skill_config_receipts:
    table += "," + ",".join(skill_checks)
    unused = tuple(references) + (("memory_attempt_ref",) if reply_variant == "fresh_memory_fk" else ())
    scope = ",".join(repr(kind) for kind in SKILL_RECEIPT_KINDS)
    table += ",check(record_type not in (" + scope + ") or (" + " and ".join(
        f"{field} is null" for field in unused
    ) + "))"
# After existing follow-up sync indexes are added:
if include_skill_config_receipts:
    indexes += skill_indexes
# After existing follow-up sync triggers are added:
if include_skill_config_receipts:
    triggers += skill_triggers
```

The `+ (...)` expression is a new term in the existing parenthesized `table = (...)` expression; it is not a unary standalone statement. Change each five-function signature and its internal calls in the same edit, then compare `ACTION_TABLE_DDL`, `RECEIPT_ACTION_TABLE_DDL`, and the default `_current_objects()` before/after byte-for-byte. `create_action_schema(... include_reply=True, reply_variant="fresh_memory_fk", include_email=True, include_followup_sync=True, include_skill_config_receipts=True)` is the only new target fixture installer call. For both kinds numeric `legacy_key` is already covered by the existing numeric public index; their two `actions:<kind>` streams are advanced by the new trigger and read by the exact helper allocator. The two payloads contain source fields only: export `{revision_id,sha256,path,created_at}`; load `{config_id,pid,error,created_at}`. The existing action declaration projects `created_at` from its tagged payload; it is not a physical actions column. This target **does not** store `loaded_json` in action payload, config payload, a shadow table or any new body field.

- [ ] **Step 3: Implement exact body materializer and source-shaped insert.** Use one transaction for parent/binding/revision read and receipt DTO; do not assemble across separate connections or outside the reader's snapshot. The writer may accept source-shaped `loaded_json` only to validate it, then drops it. Error receipts return `'{}'`. Direct native-SQL BLOB/noncanonical bodies are a recorded source exception; do not accept them as equivalent target history.

```python
"""Unselected typed Skill receipt action storage and exact public projection."""
from dataclasses import dataclass
import json
import sqlite3

@dataclass(frozen=True)
class SkillLoadReceipt:
    id: int
    config_id: int
    pid: int
    loaded_json: str
    error: str
    created_at: str

def loaded_body(db: sqlite3.Connection, config_ref: int, error: str) -> str:
    if not db.in_transaction:
        raise sqlite3.ProgrammingError("Skill receipt read requires caller snapshot")
    if error:
        return "{}"
    rows = db.execute(
        "select b.skill_binding_skill_id,r.skill_revision_sha256 from configs b "
        "join configs r on r.id=b.skill_binding_revision_ref "
        "where b.record_type='runtime_skill_bindings' and "
        "b.skill_binding_config_ref=? and b.skill_binding_enabled=1",
        (config_ref,),
    ).fetchall()
    return json.dumps({int(skill_id): digest for skill_id, digest in rows}, sort_keys=True)

def load_receipt_projection(db: sqlite3.Connection, action_id: int) -> SkillLoadReceipt:
    if not db.in_transaction:
        raise sqlite3.ProgrammingError("Skill receipt read requires caller snapshot")
    row = db.execute(
        "select cast(a.legacy_key as integer),a.skill_config_id,a.skill_pid,"
        "a.runtime_config_ref,a.skill_error,a.skill_created_at from actions a "
        "where a.id=? and a.record_type='runtime_skill_load_receipts'",
        (action_id,),
    ).fetchone()
    if row is None:
        raise LookupError(action_id)
    public_id, config_id, pid, config_ref, error, created_at = row
    return SkillLoadReceipt(public_id, config_id, pid,
                            loaded_body(db, config_ref, error), error, created_at)

def compare_source_load_body(db: sqlite3.Connection, action_id: int, source_body) -> None:
    projected = load_receipt_projection(db, action_id)
    if not isinstance(source_body, str) or source_body.encode("utf-8") != projected.loaded_json.encode("utf-8"):
        raise ValueError("source load body is not byte-exactly reconstructable")
```

Add `insert_skill_receipt` with a closed field set and the existing action physical-ID allocator pattern: resolve parent by `(record_type,public_key)`, set the appropriate physical ref, serialize payload with the existing `_encoded_sqlite_value` rules, reserve the independent `actions:<kind>` sequence in the same caller transaction, and return source public ID. For a load receipt call `compare_source_load_body` before commit; on mismatch caller rolls back. Do not copy the source body. The following is its complete public signature and field contract:

```python
def insert_skill_receipt(db: sqlite3.Connection, kind: str, values: dict,
                         *, source_id: int | None = None) -> int:
    """Caller transaction required; closed source columns, no stored load body.

    export values: revision_id, sha256, path, optional created_at.
    load values: config_id, pid, error (default ''), loaded_json (validation input),
    optional created_at. An explicit source_id preserves source IDs on a later copy.
    """
```

The source ID remains `legacy_key` and the independent sequence metadata, while `actions.id` is the target physical ID. Do not conflate them. Replace the signature above with this complete implementation and add the reader functions in the same module:

```python
from app.action_storage import _encoded_sqlite_value, _native_integer_primary_key
from app.action_skill_config_definitions import SKILL_RECEIPT_KINDS

MAX_ROWID = 2**63 - 1

def _skill_parent(db: sqlite3.Connection, kind: str, key: int) -> int:
    row = db.execute("select id from configs where record_type=? and public_key=?",
                     (kind, str(key))).fetchone()
    if row is None:
        raise sqlite3.IntegrityError("Skill receipt parent missing")
    return row[0]

def next_skill_receipt_id(db: sqlite3.Connection, kind: str) -> int:
    if kind not in SKILL_RECEIPT_KINDS:
        raise ValueError(kind)
    name = "actions:" + kind
    row = db.execute("select seq from sqlite_sequence where name=?", (name,)).fetchone()
    watermark = row[0] if row else 0
    maximum = db.execute("select max(cast(legacy_key as integer)) from actions "
                         "where record_type=?", (kind,)).fetchone()[0]
    last = max(watermark, maximum or 0, 0)
    if last == MAX_ROWID:
        raise sqlite3.OperationalError("database or disk is full")
    return last + 1

def insert_skill_receipt(db: sqlite3.Connection, kind: str, values: dict,
                         *, source_id: int | None = None) -> int:
    if not db.in_transaction:
        raise sqlite3.ProgrammingError("Skill receipt writes require caller transaction")
    if kind not in SKILL_RECEIPT_KINDS:
        raise ValueError(kind)
    export = kind == "managed_skill_export_receipts"
    allowed = ({"revision_id", "sha256", "path", "created_at"} if export else
               {"config_id", "pid", "error", "loaded_json", "created_at"})
    if set(values) - allowed:
        raise ValueError("unknown Skill receipt source field")
    required = {"revision_id", "sha256", "path"} if export else {"config_id", "pid"}
    if not required <= set(values):
        raise ValueError("missing Skill receipt source field")
    identity = (next_skill_receipt_id(db, kind) if source_id is None else
                _native_integer_primary_key(source_id))
    payload = dict(values)
    source_body = payload.pop("loaded_json", "{}")
    payload.setdefault("created_at", db.execute("select current_timestamp").fetchone()[0])
    if export:
        ref = _skill_parent(db, "managed_skill_revisions", payload["revision_id"])
        recorded_sha = db.execute("select skill_revision_sha256 from configs where id=?", (ref,)).fetchone()[0]
        if payload["sha256"] != recorded_sha:
            raise ValueError("managed Skill export receipt SHA does not match revision")
        ref_col = "skill_revision_ref"
    else:
        payload.setdefault("error", "")
        ref = _skill_parent(db, "runtime_skill_configs", payload["config_id"])
        ref_col = "runtime_config_ref"
        exact = loaded_body(db, ref, payload["error"])
        if not isinstance(source_body, str) or source_body.encode("utf-8") != exact.encode("utf-8"):
            raise ValueError("source load body is not byte-exactly reconstructable")
    sql = ("insert into actions(record_type,legacy_key,payload_json," + ref_col +
           ") values(?,?,?,?)")
    encoded = {field: _encoded_sqlite_value(value) for field, value in payload.items()}
    db.execute(sql, (kind, str(identity), json.dumps(encoded, ensure_ascii=False,
               separators=(",", ":")), ref))
    return identity

def list_skill_load_receipts(db: sqlite3.Connection, config_id: int) -> list[SkillLoadReceipt]:
    if not db.in_transaction:
        raise sqlite3.ProgrammingError("Skill receipt read requires caller snapshot")
    rows = db.execute("select id from actions where record_type='runtime_skill_load_receipts' "
                      "and skill_config_id=? order by cast(legacy_key as integer)",
                      (config_id,)).fetchall()
    return [load_receipt_projection(db, row[0]) for row in rows]

def latest_skill_load_receipt_for_pid(db: sqlite3.Connection, pid: int) -> SkillLoadReceipt | None:
    if not db.in_transaction:
        raise sqlite3.ProgrammingError("Skill receipt read requires caller snapshot")
    row = db.execute("select id from actions where record_type='runtime_skill_load_receipts' "
                     "and skill_pid=? order by cast(legacy_key as integer) desc limit 1",
                     (pid,)).fetchone()
    return load_receipt_projection(db, row[0]) if row else None

@dataclass(frozen=True)
class SkillExportReceipt:
    id: int
    revision_id: int
    sha256: str
    path: str
    created_at: str

def export_receipt_projection(db: sqlite3.Connection, action_id: int) -> SkillExportReceipt:
    if not db.in_transaction:
        raise sqlite3.ProgrammingError("Skill receipt read requires caller snapshot")
    row = db.execute("select cast(legacy_key as integer),skill_revision_id,skill_sha256,"
                     "skill_path,skill_created_at from actions where id=? and "
                     "record_type='managed_skill_export_receipts'", (action_id,)).fetchone()
    if row is None:
        raise LookupError(action_id)
    return SkillExportReceipt(*row)

def latest_managed_skill_export_receipt(db: sqlite3.Connection,
                                         revision_id: int) -> SkillExportReceipt | None:
    if not db.in_transaction:
        raise sqlite3.ProgrammingError("Skill receipt read requires caller snapshot")
    row = db.execute("select id from actions where record_type='managed_skill_export_receipts' "
                     "and skill_revision_id=? order by cast(legacy_key as integer) desc limit 1",
                     (revision_id,)).fetchone()
    return export_receipt_projection(db, row[0]) if row else None
```

The source insert helper compares a supplied `loaded_json` *before* inserting, and never writes it. The separately defined `compare_source_load_body` checks a copied source row against an inserted target action in the later release-copy gate. Both functions reject BLOB/noncanonical values instead of fabricating equivalent history. The returned ID is the public source ID; use `actions.id` only to address the target physical row.

- [ ] **Step 4: GREEN.** Run `python -m pytest -q tests/test_action_skill_config_storage.py tests/test_configs_load_receipt_source.py tests/test_configs_storage.py tests/test_configs_account_source.py`; verify exact reader DTO/UTF-8 bytes, 20-before-100, failure `'{}'`, wrong digest rejection/rollback, duplicate successes, native immutable errors, independent sequences and all 11 receipt source fields. Commit new unselected modules and opted-in action declaration together.

## Task 6: Source/target validation and bounded handoff

**Files:** Test files above; append to `docs/database-redesign-config-state-source-preparation.md`; update `docs/architecture.md` and `docs/runtime-mechanism.md` only for the unselected declaration boundary.

- [ ] **Step 1: Run source-shaped tests in owned `/tmp` parent.** Create a new task-owned `TemporaryDirectory(prefix="configs-schedules-receipts-", dir="/tmp")`, pass its child as `pytest --basetemp`, close it, then assert parent absent. Run only the new three test files plus the existing seven-kind/account/source oracle files; do not run ~9,500 tests or touch production. Run Ruff and `compileall` on changed Python files and `git diff --check`.

- [ ] **Step 2: Verify actual/fresh coverage.** Assert each of 118+17+11 source columns has exactly one target disposition in the approved field map; all 15 config target projections yield native source columns; both receipt readers yield all source fields. Assert `loaded_json` appears only as a **derived DTO/input validation**, never in `pragma table_xinfo(actions/configs/schedules)` or stored payload. The four source oracle counterexamples remain counterexamples: noncanonical `' { } '`, malformed TEXT, BLOB, and late binding INSERT. No test may claim all arbitrary native SQL DML parity.

- [ ] **Step 3: Record exact evidence.** Document passing focused case count, actual/fresh variants, synthetic-data scope, and source exceptions. Do not describe a target declaration as a copied production migration, an inverse, or live behavior. Commit implementation by coherent files/hunks, stage only own changes, then request sequential SPEC and QUALITY review if that workflow is invoked by the parent. No push/main/deploy.

### Required later closure before selection or retirement

The next plan must convert all Store/Email and scheduled runner readers/writers, `runs.snapshot_id` → the typed config-version FK, schedule Skill refs → real schedule/Skill/revision refs, two receipt writer/list/latest DTOs, config activation and account action blockers, and the retired empty training-observation FK/trigger. It must add a source/target copy and inverse with per-row exact receipt bytes, type/NULL/class/FK/order/sequence comparison, stop on any nonreconstructable body or unresolved native-SQL exception, then select one coherent schema version and verify copied production readback. Only that later unit may remove 18 old families and count the resulting table reduction. It must not introduce old tables/views as a runtime compatibility layer, a new body archive, an unsupported singleton rule, or safety gates unrelated to this requested storage behavior.

## Self-review gate for the implementer

1. Search every step for placeholders and undefined functions. Task 5 insertion/list/latest helpers are defined in its complete appendix and must use the same signatures in tests and integration.
2. Confirm actual versus published-fresh schedule order, actual/fresh `email_accounts` order, all 15 config kinds/118 fields, 17 schedules fields and 11 receipt fields from literal artifacts.
3. Confirm all target fixtures create the real `schedules` table and complete flagged `actions` scaffold before new config DML; missing tables/columns are failures, never accepted parity.
4. Confirm no default Store/Email constructor or marker selects the new schemas; no real DB, provider, native CLI, network, push, main, deploy or unrelated retention/safety work.
