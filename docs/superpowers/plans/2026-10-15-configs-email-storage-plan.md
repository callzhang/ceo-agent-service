# Email Configs Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an unselected, source-shaped `configs` declaration and storage API for exactly seven existing Email configuration kinds and their 73 source fields.

**Architecture:** One typed `configs` table stores the four ordinary source rowids independently from its physical `id`, and stores canonical tagged TEXT-primary-key tuples for the three `WITHOUT ROWID` kinds. Source keys, binding and promotion relations, snapshot digest/freeze, and the six account/action blockers remain typed columns, indexes, FKs or finite triggers; the other fields use a closed per-kind tagged SQLite payload with generated typed projections and source checks. This unit creates no Store/Email selection, migration or new schema version.

**Tech Stack:** Python `sqlite3`, SQLite JSON functions and generated columns, pytest, Ruff. Run only focused tests in this live development tree.

---

## Authority, files and stop line

The final approved target is the ten-core-plus-ledger design in `docs/superpowers/specs/2026-10-11-database-redesign-design.md` and its field CSV. The immutable source authority is `docs/superpowers/specs/2026-10-15-database-redesign-config-state-native-source.json`: actual readonly metadata and published `7dadc492c1c62d5b0624f2f97dd0c89f4f358646` fresh declarations. `tests/configs_source_fixtures.py` and `tests/test_configs_source_contract.py` are the accepted 19-case bounded source oracle at `4c8a287d`. They are finite samples, not proof of all future DML or a copied production migration. The source artifact covers 15 config families/118 fields; this plan selects only the first seven Email families/73 fields for *unselected* storage. The eight Skill/schedule/WeChat families remain unsupported by this declaration. Do not add them to its kind CHECK before their own accepted extension.

| File | Responsibility |
| --- | --- |
| `app/configs_definitions.py` (new) | Seven literal field lists, source defaults/checks, one finite target table, indexes, typed FKs, source-shaped actual/fresh projections, and native blockers/immutability triggers. |
| `app/configs_storage.py` (new) | Caller-transactional insert/update/delete and exact source rowid/TEXT-key handling; never a second source writer. |
| `tests/test_configs_storage.py` (new) | Differential synthetic source/target values and SQLite storage classes, native rowid/PK edge behavior, seven-kind check matrix, typed FK/partial uniqueness, immutable and current-action blocker cases. |
| `docs/database-redesign-config-state-source-preparation.md` | Append verified local result and exact selection boundary. |
| `docs/agent-claims.md` | Claim these files before code work; stage only own hunks. |

The implementation task may create only these three new Python files plus the two scoped docs edits. Do not edit `tests/configs_source_fixtures.py`, `tests/test_configs_source_contract.py`, the captured JSON/CSV, any Store/Email caller, `app/action_email_storage.py`, migration/release code, provider/native CLI, the real database or production checkout. Public default constructors cannot select this table. There is no dual write, fallback, schema bump, source cleanup or partial publication. The next release plan must close all 15 config families plus `scheduled_tasks`/`runs` references, two receipt families routed to `actions`, empty training-observation retirement, and current caller conversions before dropping original config tables. Preserve existing `runtime_skill_load_receipts.loaded_json` in `actions` until an explicit later closure proves a replacement read contract.

## Literal source matrix

Use the complete source SQL in the frozen capture for comparison. The following facts are implementation inputs, not inferred from SQL text at runtime.

| Kind | Count | Identity | Native relation / distinct constraint |
| --- | ---: | --- | --- |
| `email_accounts` |22| ordinary rowid; nullable TEXT `account_id` PK | Six UPDATE/DELETE in-flight blockers, no unique email-address rule |
| `email_category_configs` |12| ordinary rowid; nullable TEXT `category_key` PK | JSON array/nonempty-first-element checks; no unique version rule |
| `email_category_description_revisions` |5| `WITHOUT ROWID` nonnull TEXT `revision_id` | Immutable UPDATE/DELETE and duplicate INSERT; no category FK |
| `email_category_folder_bindings` |6| ordinary rowid; composite nonnull TEXT account/category PK | Both parent FKs ON DELETE CASCADE, ON UPDATE NO ACTION; unique active account/folder |
| `email_model_mode_transitions` |10| `WITHOUT ROWID` nonnull TEXT `request_id` | Immutable; promotion config FK ON DELETE RESTRICT, ON UPDATE NO ACTION |
| `email_model_promotion_configs` |6| `WITHOUT ROWID` nonnull TEXT `config_version` | Immutable; finite p95 check |
| `email_training_snapshots` |12| ordinary rowid; nullable TEXT `snapshot_id` PK | Unique digest; immutable DELETE; only frozen 0→1 UPDATE with all other source fields `IS`-equal |

`email_address` case normalization, `allow_shared` checks, category/config-version relationships and promotion decisions are application behavior, not additional database constraints. Keep raw source `*_json` strings exact: `config_json`, `scan_folders_json`, category JSON and `manifest_json` have native `json_valid` checks, while any source TEXT field without such a check remains legal even when its text is invalid JSON. Do not decode/reformat source JSON bodies.

For ordinary rowid kinds, `legacy_key` is the exact signed decimal source rowid, including negative and zero; `configs.id` is a separate positive physical key. Implicit allocation uses native max+1, reuse after delete, and SQLite's max-int random candidate behavior. For the three `WITHOUT ROWID` kinds, `legacy_key` is the canonical JSON one-tuple of the **stored value after TEXT affinity**, using `_encoded_sqlite_value`: text `'7'` becomes `["7"]`, BLOB `X'37'` becomes `[{"t":"blob","v":"37"}]`, raw integer `7` becomes `["7"]`. No case-folding, trimming or Python numeric parser. The native source does not expose a rowid for these three.

### Task 1: Finite declaration and source projections

**Files:** Create `app/configs_definitions.py`; test in `tests/test_configs_storage.py`.

- [ ] **Step 1: Claim exactly the files in the file map** with one own row in `docs/agent-claims.md`. Read the frozen actual/fresh SQL again and use its `columns` order, `indexes`, `foreign_keys` and `triggers` as the acceptance source.
- [ ] **Step 2: Write RED declaration/projection tests first.** Create `tests/test_configs_storage.py` with the following first test; run `python -m pytest -q tests/test_configs_storage.py::test_seven_declared_fields_and_order`. Expected: import/attribute failure because `app.configs_definitions` does not exist.

```python
from contextlib import closing
import sqlite3

from app.configs_definitions import CONFIG_FIELDS, CONFIG_PROJECTIONS
from app.configs_storage import create_configs_schema
from tests.configs_source_fixtures import capture


def test_seven_declared_fields_and_order():
    actual = capture()["actual"]["tables"]
    assert len(CONFIG_FIELDS) == 7
    assert sum(map(len, CONFIG_FIELDS.values())) == 73
    with closing(sqlite3.connect(":memory:")) as db:
        create_configs_schema(db)
        assert [row[1] for row in db.execute("pragma table_xinfo(configs)")
                if row[6] == 0][:4] == ["id", "record_type", "legacy_key", "payload_json"]
        for kind, fields in CONFIG_FIELDS.items():
            assert fields == tuple(c["name"] for c in actual[kind]["columns"])
            assert tuple(col[0] for col in db.execute(CONFIG_PROJECTIONS[kind]).description) == fields
        assert db.execute("pragma quick_check").fetchone() == ("ok",)
```

- [ ] **Step 3: Implement the finite metadata and declaration.** The code in the next three blocks is the complete `app/configs_definitions.py`. It deliberately uses literal field/check sets; no SQL parsing, regex-derived DDL, source JSON load or open-ended kind registration occurs in runtime.

```python
"""Unselected seven-kind Email configs declaration and source projections."""

from app.action_email_definitions import _encoded_value, _encoded_value_check

CONFIG_FIELDS = {
    "email_accounts": tuple("""account_id display_name email_address imap_host imap_port
        imap_tls imap_username imap_secret_reference smtp_host smtp_port smtp_tls
        smtp_username smtp_secret_reference enabled scan_folders_json
        scan_interval_seconds created_at updated_at imap_move_mode agent_lookback_days
        scan_read_state model_lookback_days""".split()),
    "email_category_configs": tuple("""category_key display_name core_description
        include_json exclude_json threshold actions_json action_parameters_json enabled
        description_version config_version updated_at""".split()),
    "email_category_description_revisions": tuple(
        "revision_id category_key config_version config_json created_at".split()),
    "email_category_folder_bindings": tuple(
        "account_id category_key provider_folder_id provider_folder_name binding_status last_verified_at".split()),
    "email_model_mode_transitions": tuple("""request_id actor from_mode to_mode
        from_model_id target_model_id promotion_config_version status reason created_at""".split()),
    "email_model_promotion_configs": tuple("""config_version micro_f1_min category_precision_min
        category_validation_samples_min p95_latency_max_ms created_at""".split()),
    "email_training_snapshots": tuple("""snapshot_id snapshot_version description_version
        input_schema_version seed observed_at snapshot_digest manifest_json frozen created_at
        folder_label_watermark important_label_watermark""".split()),
}
CONFIG_FRESH_FIELDS = {
    **CONFIG_FIELDS,
    "email_accounts": tuple("""account_id display_name email_address imap_host imap_port
        imap_tls imap_username imap_secret_reference imap_move_mode smtp_host smtp_port
        smtp_tls smtp_username smtp_secret_reference enabled scan_folders_json
        scan_interval_seconds created_at updated_at agent_lookback_days
        model_lookback_days scan_read_state""".split()),
}
PREFIX = {
    "email_accounts": "account", "email_category_configs": "category",
    "email_category_description_revisions": "revision",
    "email_category_folder_bindings": "binding",
    "email_model_mode_transitions": "transition",
    "email_model_promotion_configs": "promotion",
    "email_training_snapshots": "snapshot",
}
ROWID_KINDS = (
    "email_accounts", "email_category_configs",
    "email_category_folder_bindings", "email_training_snapshots",
)
WITHOUT_ROWID_KINDS = (
    "email_category_description_revisions", "email_model_mode_transitions",
    "email_model_promotion_configs",
)
SCALAR_KEY = {
    "email_accounts": "account_id", "email_category_configs": "category_key",
    "email_category_description_revisions": "revision_id",
    "email_model_mode_transitions": "request_id",
    "email_model_promotion_configs": "config_version",
    "email_training_snapshots": "snapshot_id",
}
PHYSICAL = {
    "email_accounts": ("account_id",),
    "email_category_configs": ("category_key",),
    "email_category_description_revisions": (),
    "email_category_folder_bindings": ("account_id", "category_key"),
    "email_model_mode_transitions": ("promotion_config_version",),
    "email_model_promotion_configs": (),
    "email_training_snapshots": ("snapshot_id", "snapshot_digest", "frozen"),
}
DEFAULTS = {
    "email_accounts": {"imap_move_mode": "'move'", "agent_lookback_days": "30",
                       "scan_read_state": "'unread'", "model_lookback_days": "365"},
    "email_training_snapshots": {"frozen": "1", "folder_label_watermark": "0",
                                 "important_label_watermark": "0"},
}
INTEGERS = {
    "email_accounts": set("""imap_port imap_tls smtp_port smtp_tls enabled
        scan_interval_seconds agent_lookback_days model_lookback_days""".split()),
    "email_category_configs": {"enabled"},
    "email_model_promotion_configs": {"category_validation_samples_min"},
    "email_training_snapshots": {"seed", "frozen", "folder_label_watermark",
                                 "important_label_watermark"},
}
REALS = {
    "email_category_configs": {"threshold"},
    "email_model_promotion_configs": {"micro_f1_min", "category_precision_min",
                                       "p95_latency_max_ms"},
}
REQUIRED = {
    "email_accounts": set(CONFIG_FIELDS["email_accounts"]) - {"account_id"},
    "email_category_configs": set(CONFIG_FIELDS["email_category_configs"]) - {"category_key"},
    "email_category_description_revisions": set(CONFIG_FIELDS["email_category_description_revisions"]),
    "email_category_folder_bindings": set(CONFIG_FIELDS["email_category_folder_bindings"]),
    "email_model_mode_transitions": set(CONFIG_FIELDS["email_model_mode_transitions"]) -
                                    {"from_model_id", "target_model_id"},
    "email_model_promotion_configs": set(CONFIG_FIELDS["email_model_promotion_configs"]),
    "email_training_snapshots": set(CONFIG_FIELDS["email_training_snapshots"]) - {"snapshot_id"},
}


def config_column(kind: str, field: str) -> str:
    if field == SCALAR_KEY.get(kind):
        return "public_key"
    return PREFIX[kind] + "_" + field


def config_payload_fields(kind: str) -> tuple[str, ...]:
    return tuple(field for field in CONFIG_FIELDS[kind]
                 if field not in PHYSICAL[kind] and field != SCALAR_KEY.get(kind))
```

The next block is the finite source CHECK matrix. Every value is applied under its own `record_type`, so nullable ordinary TEXT PKs still admit multiple NULLs. A source `CHECK` that evaluates NULL remains passing, exactly as in SQLite. In particular, no uniqueness is inferred for category versions, no category FK is added to immutable revisions, and no validity rule is invented for arbitrary TEXT.

```python
RULES = {
    "email_accounts": (
        "account_imap_port between 1 and 65535",
        "account_imap_tls in (0,1)", "account_smtp_port between 1 and 65535",
        "account_smtp_tls in (0,1)", "account_enabled in (0,1)",
        "json_valid(account_scan_folders_json)",
        "account_scan_interval_seconds>0",
        "account_imap_move_mode in ('move','copy_as_move')",
        "account_agent_lookback_days between 1 and 365",
        "account_scan_read_state in ('unread','all')",
        "account_model_lookback_days between 1 and 3650",
    ),
    "email_category_configs": (
        "trim(public_key)!=''", "trim(category_display_name)!=''",
        "trim(category_core_description)!=''",
        "json_valid(category_include_json)",
        "json_type(category_include_json)='array'",
        "json_array_length(category_include_json)>0",
        "trim(json_extract(category_include_json,'$[0]'))!=''",
        "json_valid(category_exclude_json)",
        "json_type(category_exclude_json)='array'",
        "json_array_length(category_exclude_json)>0",
        "trim(json_extract(category_exclude_json,'$[0]'))!=''",
        "category_threshold>=0.0 and category_threshold<=1.0",
        "json_valid(category_actions_json)",
        "json_valid(category_action_parameters_json)",
        "category_enabled in (0,1)",
        "trim(category_description_version)!=''",
        "trim(category_config_version)!=''",
        "trim(category_updated_at)!=''",
    ),
    "email_category_description_revisions": (
        "trim(public_key)!=''", "trim(revision_category_key)!=''",
        "trim(revision_config_version)!=''", "json_valid(revision_config_json)",
        "trim(revision_created_at)!=''",
    ),
    "email_category_folder_bindings": (
        "trim(binding_account_id)!=''", "trim(binding_category_key)!=''",
        "binding_provider_folder_name!=''",
        "binding_binding_status in ('active','missing','ambiguous','error')",
        "trim(binding_last_verified_at)!=''",
        "binding_binding_status!='active' or binding_provider_folder_id!=''",
    ),
    "email_model_mode_transitions": (
        "trim(public_key)!=''", "trim(transition_actor)!=''",
        "transition_from_mode in ('agent_primary','model_primary')",
        "transition_to_mode in ('agent_primary','model_primary')",
        "transition_from_model_id is null or trim(transition_from_model_id)!=''",
        "transition_target_model_id is null or trim(transition_target_model_id)!=''",
        "trim(transition_promotion_config_version)!=''",
        "transition_status in ('applied','rejected','failed')",
        "trim(transition_created_at)!=''",
    ),
    "email_model_promotion_configs": (
        "trim(public_key)!=''",
        "promotion_micro_f1_min>=0 and promotion_micro_f1_min<=1",
        "promotion_category_precision_min>=0 and promotion_category_precision_min<=1",
        "typeof(promotion_category_validation_samples_min)='integer' "
        "and promotion_category_validation_samples_min>0",
        "promotion_p95_latency_max_ms>0 and "
        "(promotion_p95_latency_max_ms-promotion_p95_latency_max_ms) is 0.0",
        "trim(promotion_created_at)!=''",
    ),
    "email_training_snapshots": (
        "trim(public_key)!=''", "trim(snapshot_snapshot_version)!=''",
        "trim(snapshot_description_version)!=''",
        "trim(snapshot_input_schema_version)!=''",
        "snapshot_seed>=0", "trim(snapshot_observed_at)!=''",
        "length(snapshot_snapshot_digest)=64",
        "json_valid(snapshot_manifest_json)",
        "snapshot_frozen in (0,1)", "trim(snapshot_created_at)!=''",
        "snapshot_folder_label_watermark>=0",
        "snapshot_important_label_watermark>=0",
    ),
}
```

The final declaration block builds one table from the fixed metadata above. The generated columns give every source field its native SQLite affinity and `typeof`; their inactive values are NULL. The private payload must contain exactly its kind's listed keys, so a typo or an eighth kind cannot become silent data. The physical reference contains the parent `id` *and* its kind and public key; changing a referenced parent key remains a native NO ACTION failure. The six account blockers use the already-current typed `actions` fields and preserve the three distinct error strings.

```python
def _affinity(kind: str, field: str) -> str:
    if field in INTEGERS.get(kind, ()):
        return "integer"
    if field in REALS.get(kind, ()):
        return "real"
    return "text"


def _definitions():
    names = ",".join(repr(kind) for kind in CONFIG_FIELDS)
    wr = ",".join(repr(kind) for kind in WITHOUT_ROWID_KINDS)
    decoded_key = _encoded_value("legacy_key", "$[0]")
    columns = [
        "id integer primary key autoincrement check(id>0)",
        f"record_type text not null check(record_type in ({names}))",
        "legacy_key text not null",
        "payload_json text not null check(typeof(payload_json)='text' "
        "and json_valid(payload_json) and json_type(payload_json)='object')",
        "source_key text",
        "binding_account_id text", "binding_category_key text",
        "binding_account_ref integer", "binding_category_ref integer",
        "transition_promotion_config_version text", "transition_promotion_ref integer",
        "snapshot_snapshot_digest text", "snapshot_frozen integer",
        f"public_key text generated always as (case when record_type in ({wr}) "
        f"then {decoded_key} else source_key end) virtual",
        "binding_account_kind text generated always as "
        "(case when record_type='email_category_folder_bindings' then 'email_accounts' end) virtual",
        "binding_category_kind text generated always as "
        "(case when record_type='email_category_folder_bindings' then 'email_category_configs' end) virtual",
        "transition_promotion_kind text generated always as "
        "(case when record_type='email_model_mode_transitions' "
        "then 'email_model_promotion_configs' end) virtual",
    ]
    checks = [
        "check(case when record_type in (" +
        ",".join(repr(kind) for kind in ROWID_KINDS) +
        ") then legacy_key=cast(cast(legacy_key as integer) as text) "
        "else json_valid(legacy_key) and json_type(legacy_key)='array' "
        "and json_array_length(legacy_key)=1 end)",
        "check((record_type in ('email_accounts','email_category_configs',"
        "'email_training_snapshots')) or source_key is null)",
        "check(record_type='email_category_folder_bindings' "
        "or (binding_account_id is null and binding_category_key is null "
        "and binding_account_ref is null and binding_category_ref is null))",
        "check(record_type='email_model_mode_transitions' "
        "or (transition_promotion_config_version is null "
        "and transition_promotion_ref is null))",
        "check(record_type='email_training_snapshots' "
        "or (snapshot_snapshot_digest is null and snapshot_frozen is null))",
        "check(record_type<>'email_category_folder_bindings' or "
        "(binding_account_ref is not null and binding_category_ref is not null))",
        "check(record_type<>'email_model_mode_transitions' "
        "or transition_promotion_ref is not null)",
        "foreign key(binding_account_ref,binding_account_kind,binding_account_id) "
        "references configs(id,record_type,public_key) "
        "on update no action on delete cascade",
        "foreign key(binding_category_ref,binding_category_kind,binding_category_key) "
        "references configs(id,record_type,public_key) "
        "on update no action on delete cascade",
        "foreign key(transition_promotion_ref,transition_promotion_kind,"
        "transition_promotion_config_version) "
        "references configs(id,record_type,public_key) "
        "on update no action on delete restrict",
    ]
    for kind in CONFIG_FIELDS:
        for field in config_payload_fields(kind):
            name = config_column(kind, field)
            value = _encoded_value("payload_json", "$." + field)
            columns.append(f"{name} {_affinity(kind, field)} generated always as "
                           f"(case when record_type='{kind}' then {value} end) virtual")
        payload_fields = config_payload_fields(kind)
        paths = ",".join(repr("$." + field) for field in payload_fields)
        conditions = [
            f"json_type(payload_json,'$.{field}') is not null"
            for field in payload_fields
        ]
        conditions += [
            _encoded_value_check("payload_json", "$." + field)
            for field in payload_fields
        ]
        conditions.append(f"json_remove(payload_json,{paths})='{{}}'")
        conditions += [
            f"{config_column(kind, field)} is not null"
            for field in REQUIRED[kind]
        ]
        conditions += [f"({rule})" for rule in RULES[kind]]
        checks.append(f"check(record_type<>'{kind}' or "
                      + " and ".join(f"({condition})" for condition in conditions) + ")")
    table = "create table configs (" + ",".join(columns + checks) + ")"
    indexes = (
        "create unique index idx_configs_legacy on configs(record_type,legacy_key)",
        "create unique index idx_configs_public on configs(record_type,public_key)",
        "create unique index idx_configs_typed_public "
        "on configs(id,record_type,public_key)",
        "create unique index idx_configs_binding_identity on "
        "configs(binding_account_id,binding_category_key) "
        "where record_type='email_category_folder_bindings'",
        "create unique index idx_configs_binding_active_provider on "
        "configs(binding_account_id,binding_provider_folder_id) "
        "where record_type='email_category_folder_bindings' "
        "and binding_binding_status='active'",
        "create unique index idx_configs_snapshot_digest on "
        "configs(snapshot_snapshot_digest) "
        "where record_type='email_training_snapshots'",
    )
    immutable = WITHOUT_ROWID_KINDS
    triggers = []
    for kind in immutable:
        short = PREFIX[kind]
        triggers.extend((
            f"create trigger trg_configs_{short}_duplicate before insert on configs "
            f"when new.record_type='{kind}' and exists("
            f"select 1 from configs where record_type='{kind}' "
            "and public_key is new.public_key) "
            "begin select raise(abort,'immutable model control history'); end",
            f"create trigger trg_configs_{short}_update before update on configs "
            f"when old.record_type='{kind}' "
            "begin select raise(abort,'immutable model control history'); end",
            f"create trigger trg_configs_{short}_delete before delete on configs "
            f"when old.record_type='{kind}' "
            "begin select raise(abort,'immutable model control history'); end",
        ))
    frozen_same = " and ".join(
        f"old.{config_column('email_training_snapshots', field)} is "
        f"new.{config_column('email_training_snapshots', field)}"
        for field in CONFIG_FIELDS["email_training_snapshots"] if field != "frozen"
    )
    triggers.extend((
        "create trigger trg_configs_snapshot_delete before delete on configs "
        "when old.record_type='email_training_snapshots' "
        "begin select raise(abort,'email training snapshot is immutable'); end",
        "create trigger trg_configs_snapshot_update before update on configs "
        "when old.record_type='email_training_snapshots' and not "
        f"(old.snapshot_frozen=0 and new.snapshot_frozen=1 and {frozen_same}) "
        "begin select raise(abort,'email training snapshot is immutable'); end",
    ))
    blockers = (
        ("direct", "email_actions", "processing", "email_direct_action_in_flight"),
        ("reply", "email_reply_dispatch_claims", "dispatching",
         "email_reply_dispatch_in_flight"),
        ("unsubscribe", "email_unsubscribe_claims", "dispatching",
         "email_unsubscribe_write_in_flight"),
    )
    for short, kind, status, error in blockers:
        for event in ("update", "delete"):
            triggers.append(
                f"create trigger trg_configs_{short}_account_{event} "
                f"before {event} on configs when old.record_type='email_accounts' "
                "and exists(select 1 from actions a "
                f"where a.record_type='{kind}' and a.email_account_id=old.public_key "
                f"and a.email_status='{status}') "
                f"begin select raise(abort,'{error}'); end"
            )
    return table, indexes, tuple(triggers)


CONFIG_TABLE_DDL, CONFIG_INDEX_DDL, CONFIG_TRIGGER_DDL = _definitions()


def config_projection(kind: str, *, fresh=False, include_rowid=False) -> str:
    if kind not in CONFIG_FIELDS:
        raise ValueError("unsupported Email config kind")
    fields = CONFIG_FRESH_FIELDS[kind] if fresh else CONFIG_FIELDS[kind]
    selected = [f"{config_column(kind, field)} as {field}" for field in fields]
    if include_rowid:
        if kind not in ROWID_KINDS:
            raise ValueError("WITHOUT ROWID config has no source rowid")
        selected.insert(0, "cast(legacy_key as integer) as rowid")
    return "select " + ",".join(selected) + " from configs where record_type=" + repr(kind)


CONFIG_PROJECTIONS = {kind: config_projection(kind) for kind in CONFIG_FIELDS}
CONFIG_FRESH_PROJECTIONS = {
    kind: config_projection(kind, fresh=True) for kind in CONFIG_FIELDS
}
```

- [ ] **Step 4: Add only the declaration installer in `app/configs_storage.py` and run GREEN.** Use this exact initial content; Task 2 extends this same file:

```python
"""Unselected caller-transactional Email configs storage."""

import sqlite3

from app.configs_definitions import CONFIG_INDEX_DDL, CONFIG_TABLE_DDL, CONFIG_TRIGGER_DDL


def create_configs_schema(db: sqlite3.Connection) -> None:
    """Install declarations only; caller owns transaction and schema selection."""
    for sql in (CONFIG_TABLE_DDL, *CONFIG_INDEX_DDL, *CONFIG_TRIGGER_DDL):
        db.execute(sql)
```

Run `python -m pytest -q tests/test_configs_storage.py::test_seven_declared_fields_and_order`. Expected: one pass and `pragma quick_check` = `ok`. Run `ruff check app/configs_definitions.py app/configs_storage.py tests/test_configs_storage.py` and `git diff --check`. Commit only the Task 1 files and own claim row: `git add app/configs_definitions.py app/configs_storage.py tests/test_configs_storage.py docs/agent-claims.md` then `git commit -m "feat: declare unselected Email configs storage"`.

### Task 2: Source-shaped write API and native identities

**Files:** Extend `app/configs_storage.py`; extend `tests/test_configs_storage.py`. No caller or release selection.

- [ ] **Step 1: Add the first RED API test.** In `tests/test_configs_storage.py`, add `from app.configs_storage import insert_config, update_config, delete_config` and this test. Run `python -m pytest -q tests/test_configs_storage.py::test_signed_rowids_and_reuse`. Expected: import failure for the three APIs.

```python
from app.action_storage import create_action_schema


def test_signed_rowids_and_reuse():
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("pragma foreign_keys=on")
        create_action_schema(
            db, include_reply=True, reply_variant="fresh_memory_fk",
            include_email=True,
        )
        create_configs_schema(db)
        db.execute("begin")
        account = {
            "account_id": "base", "display_name": "A",
            "email_address": "a@example.invalid", "imap_host": "imap",
            "imap_port": 993, "imap_tls": 1, "imap_username": "a",
            "imap_secret_reference": "ref", "smtp_host": "smtp",
            "smtp_port": 465, "smtp_tls": 1, "smtp_username": "a",
            "smtp_secret_reference": "ref", "enabled": 1,
            "scan_folders_json": "[]", "scan_interval_seconds": 60,
            "created_at": "t", "updated_at": "t",
        }
        for source_rowid, key in ((-7, "minus"), (0, "zero")):
            result = insert_config(db, "email_accounts",
                                   account | {"account_id": key},
                                   source_rowid=source_rowid)
            assert (result.lastrowid, result.rowcount) == (source_rowid, 1)
        first = insert_config(db, "email_accounts",
                              account | {"account_id": "first"})
        second = insert_config(db, "email_accounts",
                               account | {"account_id": "second"})
        assert (first.lastrowid, second.lastrowid) == (1, 2)
        assert delete_config(db, "email_accounts", 2) == 1
        assert insert_config(db, "email_accounts",
                             account | {"account_id": "reused"}).lastrowid == 2
        assert [row[0] for row in db.execute(
            "select cast(legacy_key as integer) from configs "
            "where record_type='email_accounts' order by cast(legacy_key as integer)"
        )] == [-7, 0, 1, 2]
```

- [ ] **Step 2: Replace the initial `app/configs_storage.py` with this complete content.** Its only identity encoders are the existing tagged SQLite value codec and a scratch SQLite TEXT-affinity/rowid allocator. It does not parse source DDL. Each write requires an existing caller transaction; neither writes nor declarations call `commit`. Four ordinary kinds use source rowid as the locator; three `WITHOUT ROWID` kinds use the source public TEXT PK. `lastrowid` is source rowid only for an inserted ordinary row; on ignored insert or `WITHOUT ROWID` insert it is `None` because no current caller requires SQLite's connection-level stale-lastrowid behavior.

```python
"""Unselected caller-transactional Email configs storage."""

from contextlib import closing
from dataclasses import dataclass
import json
import math
import sqlite3

from app.action_storage import _encoded_sqlite_value, _native_integer_primary_key
from app.configs_definitions import (
    CONFIG_FIELDS, CONFIG_INDEX_DDL, CONFIG_TABLE_DDL, CONFIG_TRIGGER_DDL,
    DEFAULTS, ROWID_KINDS, SCALAR_KEY, config_column, config_payload_fields,
)


@dataclass(frozen=True)
class ConfigWriteResult:
    lastrowid: int | None
    rowcount: int


_UNSET = object()


def create_configs_schema(db: sqlite3.Connection) -> None:
    """Install declarations only; caller owns transaction and schema selection."""
    for sql in (CONFIG_TABLE_DDL, *CONFIG_INDEX_DDL, *CONFIG_TRIGGER_DDL):
        db.execute(sql)


def _check(kind: str, values: dict) -> None:
    if kind not in CONFIG_FIELDS or set(values) - set(CONFIG_FIELDS[kind]):
        raise ValueError("unsupported Email config kind or source field")
    for value in values.values():
        _encoded_sqlite_value(value)


def _lock(db: sqlite3.Connection) -> None:
    if not db.in_transaction:
        raise sqlite3.ProgrammingError("Email configs writes require a caller transaction")
    db.execute("update sqlite_sequence set seq=seq where 0")


def _source_bound_null(value) -> bool:
    return value is None or isinstance(value, float) and math.isnan(value)


def _text_affinity(value):
    with closing(sqlite3.connect(":memory:")) as scratch:
        scratch.execute("create table source_key(value text)")
        scratch.execute("insert into source_key(value) values(?)", (value,))
        return scratch.execute("select value from source_key").fetchone()[0]


def _canonical_without_rowid_key(value) -> str:
    stored = _text_affinity(value)
    return json.dumps([_encoded_sqlite_value(stored)], ensure_ascii=False,
                      allow_nan=False, separators=(",", ":"))


def _next_source_rowid(db: sqlite3.Connection, kind: str) -> int:
    maximum = db.execute(
        "select max(cast(legacy_key as integer)) from configs where record_type=?",
        (kind,),
    ).fetchone()[0]
    if maximum != 2**63 - 1:
        return 1 if maximum is None else maximum + 1
    with closing(sqlite3.connect(":memory:")) as scratch:
        scratch.execute("create table source_rows(id integer primary key)")
        scratch.executemany("insert into source_rows(id) values(?)", db.execute(
            "select cast(legacy_key as integer) from configs where record_type=?",
            (kind,),
        ))
        return scratch.execute("insert into source_rows default values").lastrowid


def _completed(db: sqlite3.Connection, kind: str, supplied: dict) -> dict:
    fields = CONFIG_FIELDS[kind]
    expressions = [
        "?" if field in supplied else DEFAULTS.get(kind, {}).get(field, "null")
        for field in fields
    ]
    bindings = tuple(supplied[field] for field in fields if field in supplied)
    values = db.execute("select " + ",".join(expressions), bindings).fetchone()
    return dict(zip(fields, values, strict=True))


def _parent_id(db: sqlite3.Connection, kind: str, public_key) -> int:
    row = db.execute(
        "select id from configs where record_type=? and public_key=?",
        (kind, public_key),
    ).fetchone()
    return row[0] if row else 0


def _stored(db: sqlite3.Connection, kind: str, complete: dict,
            legacy_key: str) -> dict:
    payload = {
        field: _encoded_sqlite_value(complete[field])
        for field in config_payload_fields(kind)
    }
    data = {
        "record_type": kind, "legacy_key": legacy_key,
        "payload_json": json.dumps(payload, ensure_ascii=False, allow_nan=False,
                                   separators=(",", ":")),
    }
    if kind in SCALAR_KEY and kind in ROWID_KINDS:
        data["source_key"] = complete[SCALAR_KEY[kind]]
    if kind == "email_category_folder_bindings":
        data["binding_account_id"] = complete["account_id"]
        data["binding_category_key"] = complete["category_key"]
        data["binding_account_ref"] = _parent_id(
            db, "email_accounts", complete["account_id"])
        data["binding_category_ref"] = _parent_id(
            db, "email_category_configs", complete["category_key"])
    if kind == "email_model_mode_transitions":
        version = complete["promotion_config_version"]
        data["transition_promotion_config_version"] = version
        data["transition_promotion_ref"] = _parent_id(
            db, "email_model_promotion_configs", version)
    if kind == "email_training_snapshots":
        data["snapshot_snapshot_digest"] = complete["snapshot_digest"]
        data["snapshot_frozen"] = complete["frozen"]
    return data


def _identity(kind: str, identity) -> str:
    if kind in ROWID_KINDS:
        return str(_native_integer_primary_key(identity, update=True))
    return _canonical_without_rowid_key(identity)


def insert_config(db: sqlite3.Connection, kind: str, values: dict, *,
                  source_rowid=_UNSET, ignore=False) -> ConfigWriteResult:
    _check(kind, values)
    _lock(db)
    if kind in ROWID_KINDS:
        identity = (_next_source_rowid(db, kind)
                    if source_rowid is _UNSET or _source_bound_null(source_rowid)
                    else _native_integer_primary_key(source_rowid))
        legacy_key = str(identity)
    else:
        if source_rowid is not _UNSET:
            raise ValueError("WITHOUT ROWID config has no source rowid")
        legacy_key = _canonical_without_rowid_key(values.get(SCALAR_KEY[kind]))
        identity = None
    complete = _completed(db, kind, values)
    data = _stored(db, kind, complete, legacy_key)
    sql = ("insert " + ("or ignore " if ignore else "") + "into configs(" +
           ",".join(data) + ") values(" + ",".join("?" for _ in data) + ")")
    cursor = db.execute(sql, tuple(data.values()))
    return ConfigWriteResult(identity if cursor.rowcount else None, cursor.rowcount)


def update_config(db: sqlite3.Connection, kind: str, identity, values: dict, *,
                  new_source_rowid=_UNSET, ignore=False) -> int:
    _check(kind, values)
    _lock(db)
    key = _identity(kind, identity)
    physical = db.execute(
        "select id from configs where record_type=? and legacy_key=?",
        (kind, key),
    ).fetchone()
    if physical is None:
        return 0
    fields = CONFIG_FIELDS[kind]
    expression = ",".join(
        config_column(kind, field) + " as " + field for field in fields
    )
    cursor = db.execute(
        "select " + expression + " from configs where id=?", (physical[0],)
    )
    row = cursor.fetchone()
    current = dict(zip(fields, row, strict=True))
    current.update(values)
    if kind in ROWID_KINDS:
        legacy = (key if new_source_rowid is _UNSET else
                  str(_native_integer_primary_key(new_source_rowid, update=True)))
    else:
        if new_source_rowid is not _UNSET:
            raise ValueError("WITHOUT ROWID config has no source rowid")
        legacy = _canonical_without_rowid_key(current[SCALAR_KEY[kind]])
    data = _stored(db, kind, current, legacy)
    return db.execute(
        "update " + ("or ignore " if ignore else "") +
        "configs set " + ",".join(f"{field}=?" for field in data) +
        " where id=?", (*data.values(), physical[0]),
    ).rowcount


def delete_config(db: sqlite3.Connection, kind: str, identity) -> int:
    _check(kind, {})
    _lock(db)
    return db.execute(
        "delete from configs where record_type=? and legacy_key=?",
        (kind, _identity(kind, identity)),
    ).rowcount
```

- [ ] **Step 3: Run GREEN.** Run `python -m pytest -q tests/test_configs_storage.py::test_signed_rowids_and_reuse`. Expected: one pass. Run `ruff check app/configs_storage.py tests/test_configs_storage.py` and `python -m compileall -q app/configs_definitions.py app/configs_storage.py`. Task 3 adds the differential cases before the next commit.

### Task 3: Bounded native parity for all seven kinds

**Files:** Extend `tests/test_configs_storage.py`. Keep `tests/configs_source_fixtures.py` unchanged.

- [ ] **Step 1: Add exact synthetic values and differential helper.** Append this code to `tests/test_configs_storage.py` after the first two tests. Add the shown imports at the top, without duplicating existing imports. The helper uses only the accepted literal source fixture and the new unselected target declaration. It does not read actual database rows.

```python
from contextlib import contextmanager
import pytest

from tests.configs_source_fixtures import insert as source_insert, source_db


VALID = {
    "email_accounts": {
        "account_id": "acct", "display_name": "A",
        "email_address": "A@Example.invalid", "imap_host": "imap",
        "imap_port": 993, "imap_tls": 1, "imap_username": "a",
        "imap_secret_reference": "secret-ref", "smtp_host": "smtp",
        "smtp_port": 465, "smtp_tls": 1, "smtp_username": "a",
        "smtp_secret_reference": "secret-ref", "enabled": 1,
        "scan_folders_json": "[]", "scan_interval_seconds": 60,
        "created_at": "t", "updated_at": "t",
    },
    "email_category_configs": {
        "category_key": "legal", "display_name": "Legal",
        "core_description": "Legal", "include_json": '["legal"]',
        "exclude_json": '["other"]', "threshold": 0.5,
        "actions_json": "[]", "action_parameters_json": "{}",
        "enabled": 1, "description_version": "d1",
        "config_version": "c1", "updated_at": "t",
    },
    "email_category_description_revisions": {
        "revision_id": "r1", "category_key": "legal",
        "config_version": "c1", "config_json": '{"raw": 1}',
        "created_at": "t",
    },
    "email_category_folder_bindings": {
        "account_id": "acct", "category_key": "legal",
        "provider_folder_id": "f1", "provider_folder_name": "Folder",
        "binding_status": "active", "last_verified_at": "t",
    },
    "email_model_mode_transitions": {
        "request_id": "t1", "actor": "actor",
        "from_mode": "agent_primary", "to_mode": "model_primary",
        "promotion_config_version": "p1", "status": "applied",
        "reason": "", "created_at": "t",
    },
    "email_model_promotion_configs": {
        "config_version": "p1", "micro_f1_min": 0.8,
        "category_precision_min": 0.7,
        "category_validation_samples_min": 5,
        "p95_latency_max_ms": 200.0, "created_at": "t",
    },
    "email_training_snapshots": {
        "snapshot_id": "s1", "snapshot_version": "v1",
        "description_version": "d1", "input_schema_version": "i1",
        "seed": 0, "observed_at": "t", "snapshot_digest": "a" * 64,
        "manifest_json": '{"raw": 1}', "created_at": "t",
    },
}
INSERT_ORDER = (
    "email_accounts", "email_category_configs",
    "email_model_promotion_configs", "email_category_description_revisions",
    "email_category_folder_bindings", "email_model_mode_transitions",
    "email_training_snapshots",
)


@contextmanager
def pair(variant):
    with source_db(variant) as source, closing(sqlite3.connect(":memory:")) as target:
        target.execute("pragma foreign_keys=on")
        create_action_schema(
            target, include_reply=True, reply_variant="fresh_memory_fk",
            include_email=True,
        )
        create_configs_schema(target)
        target.execute("begin")
        yield source, target


def insert_both(source, target, kind, values, *, source_rowid=None):
    args = {"source_rowid": source_rowid} if source_rowid is not None else {}
    columns = dict(values)
    if source_rowid is not None:
        columns = {"rowid": source_rowid, **columns}
    native = source_insert(source, kind, columns)
    stored = insert_config(target, kind, values, **args)
    assert stored.rowcount == native.rowcount == 1
    if kind in ("email_accounts", "email_category_configs",
                "email_category_folder_bindings", "email_training_snapshots"):
        assert stored.lastrowid == native.lastrowid
    return stored
```

- [ ] **Step 2: Add source-shaped projection, affinity and raw TEXT checks.** The test covers all 73 values and their SQLite storage classes in exact actual/fresh order, including source JSON text whitespace, default values, and secret references. Run `python -m pytest -q tests/test_configs_storage.py::test_all_seven_source_projections`. Expected: 2 parameterized passes.

```python
@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_all_seven_source_projections(variant):
    with pair(variant) as (source, target):
        for kind in INSERT_ORDER:
            insert_both(source, target, kind, VALID[kind])
        declarations = capture()[variant]["tables"]
        for kind in INSERT_ORDER:
            fields = tuple(c["name"] for c in declarations[kind]["columns"])
            source_sql = "select " + ",".join(fields) + " from " + kind
            projection = (CONFIG_FRESH_PROJECTIONS if variant == "fresh"
                          else CONFIG_PROJECTIONS)[kind]
            assert target.execute(projection).fetchall() == source.execute(
                source_sql).fetchall(), kind
            target_types = "select " + ",".join(
                f"typeof({field})" for field in fields) + " from (" + projection + ")"
            source_types = "select " + ",".join(
                f"typeof({field})" for field in fields) + " from " + kind
            assert target.execute(target_types).fetchall() == source.execute(
                source_types).fetchall(), kind
        assert target.execute("pragma foreign_key_check").fetchall() == []


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_source_affinity_nullable_keys_and_no_invented_rules(variant):
    with pair(variant) as (source, target):
        account = VALID["email_accounts"] | {
            "account_id": None, "imap_port": "993", "imap_tls": "1",
        }
        insert_both(source, target, "email_accounts", account)
        insert_both(source, target, "email_accounts",
                    account | {"email_address": "same@Example.invalid"})
        insert_both(source, target, "email_accounts",
                    account | {"account_id": "other",
                               "email_address": "same@Example.invalid"})
        category = VALID["email_category_configs"] | {"category_key": None}
        insert_both(source, target, "email_category_configs", category)
        insert_both(source, target, "email_category_configs",
                    category | {"config_version": "c2"})
        assert source.execute(
            "select count(*) from email_accounts where account_id is null"
        ).fetchone() == target.execute(
            "select count(*) from configs where record_type='email_accounts' "
            "and public_key is null"
        ).fetchone() == (2,)
        assert source.execute(
            "select typeof(imap_port),typeof(imap_tls) from email_accounts limit 1"
        ).fetchone() == target.execute(
            "select typeof(account_imap_port),typeof(account_imap_tls) "
            "from configs where record_type='email_accounts' limit 1"
        ).fetchone() == ("integer", "integer")
```

Add `CONFIG_FRESH_PROJECTIONS` to the definitions import at the top. Keep the test's two source `email_address` values deliberately equal: neither the database nor this target adds a uniqueness or lowercase rule. Add a second category `config_version` with the same `category_key=NULL`; no category-version uniqueness is invented.

- [ ] **Step 3: Add the complete representative seven-kind rejection matrix.** Every row names the source field and the actual native constraint it challenges. Seed only the exact parent kinds needed by the two dependent kinds. Run `python -m pytest -q tests/test_configs_storage.py::test_each_email_kind_native_rejection`. Expected: all parameterized cases pass on both variants.

```python
BAD = (
    ("email_accounts", {"imap_port": 0}),
    ("email_accounts", {"imap_tls": 2}),
    ("email_accounts", {"scan_folders_json": "invalid-json"}),
    ("email_accounts", {"scan_interval_seconds": 0}),
    ("email_accounts", {"imap_move_mode": "archive"}),
    ("email_accounts", {"scan_read_state": "flagged"}),
    ("email_category_configs", {"category_key": " "}),
    ("email_category_configs", {"include_json": "[]"}),
    ("email_category_configs", {"exclude_json": "{}"}),
    ("email_category_configs", {"threshold": 1.1}),
    ("email_category_configs", {"actions_json": "invalid-json"}),
    ("email_category_configs", {"config_version": " "}),
    ("email_category_description_revisions", {"revision_id": " "}),
    ("email_category_description_revisions", {"config_json": "invalid-json"}),
    ("email_category_folder_bindings", {"binding_status": "unknown"}),
    ("email_category_folder_bindings", {"provider_folder_id": ""}),
    ("email_model_mode_transitions", {"from_mode": "unknown"}),
    ("email_model_mode_transitions", {"status": "pending"}),
    ("email_model_mode_transitions", {"promotion_config_version": "missing"}),
    ("email_model_promotion_configs", {"micro_f1_min": 1.1}),
    ("email_model_promotion_configs", {"category_validation_samples_min": 0}),
    ("email_model_promotion_configs", {"p95_latency_max_ms": float("inf")}),
    ("email_training_snapshots", {"seed": -1}),
    ("email_training_snapshots", {"snapshot_digest": "short"}),
    ("email_training_snapshots", {"manifest_json": "invalid-json"}),
    ("email_training_snapshots", {"frozen": 2}),
)


@pytest.mark.parametrize("variant", ("actual", "fresh"))
@pytest.mark.parametrize("kind,change", BAD)
def test_each_email_kind_native_rejection(variant, kind, change):
    with pair(variant) as (source, target):
        if kind == "email_category_folder_bindings":
            insert_both(source, target, "email_accounts", VALID["email_accounts"])
            insert_both(source, target, "email_category_configs",
                        VALID["email_category_configs"])
        if kind == "email_model_mode_transitions":
            insert_both(source, target, "email_model_promotion_configs",
                        VALID["email_model_promotion_configs"])
        candidate = VALID[kind] | change
        with pytest.raises(sqlite3.Error):
            source_insert(source, kind, candidate)
        with pytest.raises(sqlite3.Error):
            insert_config(target, kind, candidate)
```

- [ ] **Step 4: Add identity, relation and immutability proofs.** Run the named test functions below and the source oracle `tests/test_configs_source_contract.py` together. The identity test checks stored TEXT affinity before tagged encoding; the negative-only rowid test checks native advance from `-7` to `-6`, reuse after deletion and the empty-table start at `1` before inserting any zero rowid; the max-rowid test keeps the native overflow allocator. The relation test checks **category deletion cascading to its binding in both the source and target**, plus a category-key update block; the snapshot test checks source-shaped 0→1 equality rather than freezing every field by policy. This test does not delete or update the account parent.

```python
@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_without_rowid_keys_distinguish_blob_and_text(variant):
    with pair(variant) as (source, target):
        kind = "email_model_promotion_configs"
        base = VALID[kind]
        insert_both(source, target, kind, base | {"config_version": 7})
        insert_both(source, target, kind, base | {"config_version": b"7"})
        assert target.execute(
            "select legacy_key,typeof(public_key),public_key from configs "
            "where record_type=? order by id", (kind,),
        ).fetchall() == [
            ('["7"]', "text", "7"),
            ('[{"t":"blob","v":"37"}]', "blob", b"7"),
        ]
        assert source.execute(
            "select typeof(config_version),config_version "
            "from email_model_promotion_configs order by typeof(config_version) desc"
        ).fetchall() == [("text", "7"), ("blob", b"7")]
        with pytest.raises(sqlite3.IntegrityError, match="immutable model control history"):
            source_insert(source, kind, base | {"config_version": "7"})
        with pytest.raises(sqlite3.IntegrityError, match="immutable model control history"):
            insert_config(target, kind, base | {"config_version": "7"})
        for connection, table in ((source, kind), (target, "configs")):
            with pytest.raises(sqlite3.OperationalError if table == kind
                               else sqlite3.IntegrityError):
                if table == kind:
                    connection.execute(f"select rowid from {table}").fetchall()
                else:
                    update_config(connection, kind, "7", {"created_at": "later"})


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_binding_typed_parent_update_and_cascade(variant):
    with pair(variant) as (source, target):
        for kind in ("email_accounts", "email_category_configs",
                     "email_category_folder_bindings"):
            insert_both(source, target, kind, VALID[kind])
        with pytest.raises(sqlite3.IntegrityError):
            source.execute(
                "update email_category_configs set category_key='changed' "
                "where category_key='legal'"
            )
        with pytest.raises(sqlite3.IntegrityError):
            update_config(target, "email_category_configs", 1,
                          {"category_key": "changed"})
        source.execute("delete from email_category_configs where category_key='legal'")
        assert delete_config(target, "email_category_configs", 1) == 1
        assert source.execute(
            "select count(*) from email_category_folder_bindings"
        ).fetchone() == target.execute(
            "select count(*) from configs "
            "where record_type='email_category_folder_bindings'"
        ).fetchone() == (0,)
        assert target.execute("pragma foreign_key_check").fetchall() == []


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_snapshot_only_freezes_with_all_source_fields_unchanged(variant):
    with pair(variant) as (source, target):
        values = VALID["email_training_snapshots"] | {"frozen": 0}
        insert_both(source, target, "email_training_snapshots", values,
                    source_rowid=-5)
        source.execute(
            "update email_training_snapshots set frozen=1,rowid=9 where rowid=-5"
        )
        assert update_config(target, "email_training_snapshots", -5,
                             {"frozen": 1}, new_source_rowid=9) == 1
        with pytest.raises(sqlite3.IntegrityError,
                           match="email training snapshot is immutable"):
            source.execute(
                "update email_training_snapshots set seed=1 where rowid=9")
        with pytest.raises(sqlite3.IntegrityError,
                           match="email training snapshot is immutable"):
            update_config(target, "email_training_snapshots", 9, {"seed": 1})
        assert source.execute(
            "select rowid,frozen from email_training_snapshots"
        ).fetchone() == target.execute(
            "select cast(legacy_key as integer),snapshot_frozen from configs "
            "where record_type='email_training_snapshots'"
        ).fetchone() == (9, 1)


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_active_binding_unique_and_parent_fk(variant):
    with pair(variant) as (source, target):
        insert_both(source, target, "email_accounts", VALID["email_accounts"])
        for category in ("legal", "finance"):
            insert_both(source, target, "email_category_configs",
                        VALID["email_category_configs"] | {"category_key": category})
        first = VALID["email_category_folder_bindings"]
        insert_both(source, target, "email_category_folder_bindings", first)
        second = first | {"category_key": "finance"}
        with pytest.raises(sqlite3.IntegrityError):
            source_insert(source, "email_category_folder_bindings", second)
        with pytest.raises(sqlite3.IntegrityError):
            insert_config(target, "email_category_folder_bindings", second)
        insert_both(source, target, "email_category_folder_bindings",
                    second | {"binding_status": "missing"})
        missing = first | {"account_id": "missing", "category_key": "finance"}
        with pytest.raises(sqlite3.IntegrityError):
            source_insert(source, "email_category_folder_bindings", missing)
        with pytest.raises(sqlite3.IntegrityError):
            insert_config(target, "email_category_folder_bindings", missing)


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_immutable_history_and_snapshot_delete(variant):
    with pair(variant) as (source, target):
        for kind in ("email_category_description_revisions",
                     "email_model_promotion_configs"):
            insert_both(source, target, kind, VALID[kind])
            key = VALID[kind][CONFIG_FIELDS[kind][0]]
            with pytest.raises(sqlite3.IntegrityError,
                               match="immutable model control history"):
                source.execute(f"update {kind} set created_at='later'")
            with pytest.raises(sqlite3.IntegrityError,
                               match="immutable model control history"):
                update_config(target, kind, key, {"created_at": "later"})
            with pytest.raises(sqlite3.IntegrityError,
                               match="immutable model control history"):
                source.execute(f"delete from {kind}")
            with pytest.raises(sqlite3.IntegrityError,
                               match="immutable model control history"):
                delete_config(target, kind, key)
        insert_both(source, target, "email_training_snapshots",
                    VALID["email_training_snapshots"])
        with pytest.raises(sqlite3.IntegrityError,
                           match="email training snapshot is immutable"):
            source.execute("delete from email_training_snapshots")
        with pytest.raises(sqlite3.IntegrityError,
                           match="email training snapshot is immutable"):
            delete_config(target, "email_training_snapshots", 1)


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_negative_only_rowid_advances_reuses_and_empty_starts_at_one(variant):
    with pair(variant) as (source, target):
        kind = "email_category_configs"
        insert_both(source, target, kind, VALID[kind], source_rowid=-7)
        first = VALID[kind] | {"category_key": "first"}
        native = source_insert(source, kind, first).lastrowid
        stored = insert_config(target, kind, first).lastrowid
        assert (native, stored) == (-6, -6)
        assert source.execute(
            "delete from email_category_configs where rowid=-6"
        ).rowcount == 1
        assert delete_config(target, kind, -6) == 1
        reused = VALID[kind] | {"category_key": "reused"}
        native = source_insert(source, kind, reused).lastrowid
        stored = insert_config(target, kind, reused).lastrowid
        assert (native, stored) == (-6, -6)
    with pair(variant) as (source, target):
        kind = "email_category_configs"
        native = source_insert(source, kind, VALID[kind]).lastrowid
        stored = insert_config(target, kind, VALID[kind]).lastrowid
        assert (native, stored) == (1, 1)


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_max_rowid_uses_native_overflow_allocator(variant):
    with pair(variant) as (source, target):
        kind = "email_accounts"
        maximum = 2**63 - 1
        insert_both(source, target, kind, VALID[kind], source_rowid=maximum)
        candidate = VALID[kind] | {"account_id": "overflow"}
        native = source_insert(source, kind, candidate).lastrowid
        stored = insert_config(target, kind, candidate).lastrowid
        assert 0 < native < maximum
        assert 0 < stored < maximum
        assert target.execute(
            "select count(*) from configs where record_type=?",
            (kind,),
        ).fetchone() == (2,)
```

For the native `WITHOUT ROWID` probe, the source raises `OperationalError` when selecting `rowid`. The target UPDATE probe must use `update_config`, which raises the same immutable-history `IntegrityError` the source raises for UPDATE. Keep those assertions separate if a local SQLite version reports a different exception subclass for the missing `rowid` name.

- [ ] **Step 5: Add the six current-action account blockers in a focused typed-action predicate fixture.** Reuse the exact synthetic `_databases` and `_cases` from `tests/test_email_action_writes.py` / `tests/test_email_action_storage.py`. `_databases` calls the real current `create_action_schema(... include_email=True)` but uses narrow classification/task/run stubs; it is not a full EmailStore constructor. The source-side old-table blocker comparison already lives in `tests/test_configs_source_contract.py::test_six_account_blockers_have_distinct_old_and_current_action_owners` and `tests/test_email_action_children_source.py::test_each_external_in_flight_trigger_blocks_source_mutation`. This new test proves only that each target trigger blocks UPDATE and DELETE for its corresponding typed in-flight action row.

```python
from app.action_storage import insert_email_action
from tests.test_email_action_storage import _cases
from tests.test_email_action_writes import _databases


@pytest.mark.parametrize("kind,status,error", (
    ("email_actions", "processing", "email_direct_action_in_flight"),
    ("email_reply_dispatch_claims", "dispatching",
     "email_reply_dispatch_in_flight"),
    ("email_unsubscribe_claims", "dispatching",
     "email_unsubscribe_write_in_flight"),
))
def test_account_blockers_use_current_typed_action_predicate_fixture(kind, status, error):
    old_email, current = _databases()
    with closing(old_email), closing(current):
        create_configs_schema(current)
        current.commit()
        current.execute("begin")
        insert_config(current, "email_accounts",
                      VALID["email_accounts"] | {"account_id": "account"})
        cases = _cases()
        insert_email_action(current, "email_action_plans",
                            cases["email_action_plans"])
        insert_email_action(current, kind, cases[kind] | {"status": status})
        with pytest.raises(sqlite3.IntegrityError, match=error):
            update_config(current, "email_accounts", 1, {"display_name": "B"})
        with pytest.raises(sqlite3.IntegrityError, match=error):
            delete_config(current, "email_accounts", 1)
```

The captured actual/fresh `email_accounts` triggers refer to predecessor action children that `tests/configs_source_fixtures.py` intentionally does not create. Under that config-only fixture, source account DELETE raises `OperationalError` for a missing `email_unsubscribe_claims` table; it is not evidence of cascade parity or a successful native account mutation. Before selecting this storage path, prove positive account DELETE→binding cascade and account UPDATE/DELETE behavior against genuine predecessor action declarations or a full published constructor, then compare with the typed target. Do not add a fake action table to this fixture or count a caught missing-table error as parity success. Keep the target account FK `ON DELETE CASCADE` declaration in Task 1.

- [ ] **Step 6: Consolidate the final test imports at the top.** The successive code blocks above add imports when first needed; the final `tests/test_configs_storage.py` header is exactly:

```python
from contextlib import closing, contextmanager
import sqlite3

import pytest

from app.action_storage import create_action_schema, insert_email_action
from app.configs_definitions import (
    CONFIG_FIELDS, CONFIG_FRESH_PROJECTIONS, CONFIG_PROJECTIONS,
)
from app.configs_storage import (
    create_configs_schema, delete_config, insert_config, update_config,
)
from tests.configs_source_fixtures import (
    capture, insert as source_insert, source_db,
)
from tests.test_email_action_storage import _cases
from tests.test_email_action_writes import _databases
```

Replace earlier import lines with this header, preserving the test bodies. Run `ruff check app/configs_definitions.py app/configs_storage.py tests/test_configs_storage.py`. Expected: all checks pass.

- [ ] **Step 7: Run the focused suite and commit only the owned code.** Run `python -m pytest -q tests/test_configs_storage.py tests/test_configs_source_contract.py tests/test_config_state_native_source.py tests/test_email_action_children_source.py::test_each_external_in_flight_trigger_blocks_source_mutation`. Expected: all cases pass; the existing source oracle still reports its accepted 19 cases. Run `ruff check app/configs_definitions.py app/configs_storage.py tests/test_configs_storage.py`, `python -m compileall -q app/configs_definitions.py app/configs_storage.py tests/test_configs_storage.py` and `git diff --check`. If any matrix case fails, compare exact frozen native SQL and the failing source/target `typeof` and constraint; fix only the target declaration/storage. No test expectation is weakened to manufacture parity. Commit with `git add app/configs_storage.py tests/test_configs_storage.py` then `git commit -m "feat: add unselected Email configs source-shaped writes"`.

### Task 4: Record the storage result without claiming selection

**Files:** Append `docs/database-redesign-config-state-source-preparation.md` and update the implementer's row in `docs/agent-claims.md`.

- [ ] **Step 1: Append only observed focused-test results.** State exact commit, test command and counts, Ruff/compile/diff outcome, and any still-open source/target mismatch. Use this wording only if the Task 3 gate actually passes: “The seven-kind Email `configs` declaration and storage API passed synthetic actual/fresh differential checks. This is unselected local storage; the final 15-kind target, caller conversion, actual copy, native execution, production publication and source retirement remain separate gates.” Do not edit `docs/architecture.md` or `docs/runtime-mechanism.md` here because no runtime path changed.
- [ ] **Step 2: Final self-review.** Compare each of the seven captured `sql` definitions, each named index and trigger, source `columns` type/default/order, and the accepted 19 source cases against the target code/tests. Confirm that every source field appears once in `CONFIG_FIELDS` and exactly once in its projection, and that the inactive seven-kind payload cannot take an eighth kind. Inspect `git diff -- app/configs_definitions.py app/configs_storage.py tests/test_configs_storage.py docs/database-redesign-config-state-source-preparation.md docs/agent-claims.md` and `git diff --check`. Run `python -c 'from pathlib import Path; p=Path("docs/superpowers/plans/2026-10-15-configs-email-storage-plan.md").read_text(); terms=("T"+"BD","TO"+"DO","implement "+"later","similar to "+"Task"); print([s for s in terms if s in p])'`; expected `[]`.
- [ ] **Step 3: Commit only Task 4's own documentation hunks.** Use `git add docs/database-redesign-config-state-source-preparation.md docs/agent-claims.md` and `git commit -m "docs: record unselected Email configs storage evidence"`. Do not stage another agent's appendix or any provider/native acceptance file.

## Release boundary after this plan

This plan is deliberately one small implementation slice. Its target table CHECK recognizes **exactly seven** Email kinds; it cannot be selected as the final `configs` table. The remaining eight accepted source families are `managed_skill_revisions`, `managed_skills`, `runtime_feedback_iteration_capabilities`, `runtime_skill_bindings`, `runtime_skill_configs`, `scheduled_task_config_versions`, `scheduled_task_skill_refs` and `wechat_reply_scopes`. They need their own finite declaration/storage extension and parity tests before a later pure migration, caller conversion and paired release plan.

The final publication must keep `scheduled_tasks` and `runs` relationships valid across `configs`/`schedules`, move `managed_skill_export_receipts` and `runtime_skill_load_receipts` as explicitly proven `actions` kinds, and decide the approved empty `email_training_snapshot_observations` retirement using the complete reference/trigger closure. It must verify actual copied-database forward/inverse and current native behavior before dropping any original config table. No step here runs a provider call, native Agent CLI, real database mutation, source-body cleanup, push, main merge or deploy.
