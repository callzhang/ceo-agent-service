"""Paired final Store/Email publication and its current-fact inverse."""

from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from functools import lru_cache
import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Callable

from app.action_email_storage import (
    EXTERNAL_TRIGGER_NAMES,
    SOURCE_TABLES,
    _target_external_sql,
    capture_email_action_source,
    migrate_email_action_schema,
    restore_email_action_schema,
)
from app.action_storage import action_schema_is_current, _encoded_sqlite_value
from app.final_action_schema import migrate_final_actions, restore_final_actions, final_action_schema_is_current
from app.final_source_migration import prepare_final_source_bindings, read_recovery_source_body
from app.business_source_reader import SourceVersionBinding, RequestSourceReader
from app.provenance_storage import signal_projection
from app.provenance_definitions import SOURCE_FIELDS
from app.deferral_receipt_storage import capture_native_receipts, migrate_native_receipts, restore_native_receipts
from app.action_followup_sync_storage import (
    SOURCE_TABLES as FOLLOWUP_SYNC_SOURCE_TABLES,
    capture_followup_sync_source,
    migrate_followup_sync_schema,
    restore_followup_sync_schema,
)
from app.action_task_delivery_definitions import TASK_DELIVERY_KINDS
from app.action_task_delivery_storage import (
    capture_task_delivery_source, migrate_task_delivery_schema,
    restore_task_delivery_schema,
)
from app.action_error_storage import (
    capture_action_error_source, migrate_action_error_schema,
    restore_action_error_schema,
)
from app.action_feedback_definitions import FEEDBACK_KINDS
from app.action_feedback_storage import (
    capture_action_feedback_source, migrate_action_feedback_schema,
    restore_action_feedback_schema,
)
from app.state_definitions import state_projection
from app.state_schema_release import (capture_state_source, migrate_state_schema,
    restore_state_schema, state_schema_is_current)
from app.state_storage import update_state_rows
from app.confirmation_schema_release import (
    capture_confirmation_source, retire_confirmation_schema,
    restore_confirmation_schema, verify_confirmation_retired,
)
from app.task20_schema_release import (
    capture_task20_source, migrate_task20_schema, restore_task20_schema,
)
from app.task20_current_schema import (
    task20_schema_is_current, task20_feedback_child_is_current,
)
from app import action_storage
from app.business_schema_release import (
    business_inverse_metadata,
    business_schema_is_current,
    capture_business_source,
    migrate_business_schema,
    restore_business_schema,
)
from app.business_candidate_storage import (
    capture_business_candidate_source, migrate_business_candidate_schema,
    restore_business_candidate_schema,
)
from app.business_candidate_definitions import CANDIDATE_SOURCE_TABLES
from app.action_business_definitions import BUSINESS_ACTION_KINDS
from app.action_business_storage import (
    capture_business_action_source, migrate_business_action_schema,
    restore_business_action_schema,
)
from app.action_email_history_definitions import HISTORY_KINDS
from app.action_email_history_storage import (
    capture_email_history_source, migrate_email_history_schema,
    restore_email_history_schema,
)
from app.email_canonical_storage import (
    capture_email_canonical_source, migrate_email_canonical_schema,
    restore_email_canonical_schema,
)
from app.business_legacy_owner_storage import (
    capture_legacy_owner_source, migrate_legacy_owner, restore_legacy_owner,
)
from app.email_current_schema import current_canonical_email_parent_is_current
from app.task_schema_release import _foreign_keys
from app.provider_state_schema_release import (
    SOURCE_TABLES as PROVIDER_SOURCE_TABLES,
    TARGET as PROVIDER_TARGET,
    apply_provider_state_migration,
    capture_provider_state_source,
    provider_state_schema_is_current,
    rollback_provider_state_migration,
)
from app.action_reply_storage import _fingerprint, _source_declaration
from app.configs_run_schema import _declarations as _config_run_declarations, _objects as _config_run_objects
from app.configs_schema_release import (
    _OBSERVATION_HASHES, _OBSERVATIONS, _config_metadata, _foreign_key_children,
    _no_temp_names, _state as _validate_config_metadata,
    capture_config_source, migrate_config_schema,
    restore_config_schema,
)
from app.configs_schema_transfer import (
    SOURCE_TABLES as CONFIG_SOURCE_TABLES, capture_config_declarations,
    _target_declarations as _config_target_declarations,
)
from app.runs11_current_schema import create_current_run11_schema, current_configs_schema_is_current, current_run11_objects, current_run11_schema_is_current
from app.runs11_schema_release import capture_run11_source, migrate_run11_schema, restore_run11_schema
from app.state_storage import create_state_schema
from app.state_schema_release import (
    SOURCE_TABLES as STATE_SOURCE_TABLES, TARGET_INDEXES as STATE_TARGET_INDEXES,
)


SOURCE_STORE_VERSION = "2026-10-13.5"
SOURCE_EMAIL_VERSION = 51
TARGET_STORE_VERSION = "2026-10-14.1"
TARGET_EMAIL_VERSION = 52
VERSION_KEY = "store_schema_version"
CURRENT_CONFIG_REPAIRABLE_TRIGGERS = frozenset({
    "trg_configs_skill_revision_update", "trg_configs_skill_revision_delete",
    "trg_configs_runtime_config_update", "trg_configs_runtime_config_delete",
    "trg_configs_skill_binding_update", "trg_configs_skill_binding_delete",
    "trg_configs_feedback_capability_update", "trg_configs_feedback_capability_delete",
})
CURRENT_CONFIG_REPAIRABLE_INDEXES = frozenset({
    "idx_configs_revision_number", "idx_configs_revision_identity",
    "idx_configs_schedule_ref_position", "idx_configs_binding_config_order",
})
CURRENT_SCHEDULE_REPAIRABLE_INDEXES = frozenset({
    "idx_schedules_migration_key", "idx_schedules_enabled",
})


def _repair_names_available(db: sqlite3.Connection, missing) -> bool:
    """A missing auxiliary name must be free in SQLite's matching namespace."""
    for object_type, name in missing:
        if object_type == "index":
            conflict = db.execute(
                "select 1 from main.sqlite_schema where name=? collate nocase "
                "and type in ('index','table','view')", (name,),
            ).fetchone()
        elif object_type == "trigger":
            conflict = db.execute(
                "select 1 from main.sqlite_schema where name=? collate nocase "
                "and type='trigger'", (name,),
            ).fetchone()
        else:
            return False
        if conflict is not None:
            return False
    return True


@lru_cache(maxsize=1)
def _canonical_run_object_types() -> dict[str, str]:
    with closing(sqlite3.connect(":memory:")) as reference:
        create_current_run11_schema(reference)
        return dict(reference.execute(
            "select name,type from sqlite_schema where tbl_name='runs' and sql is not null"
        ))


def _current_table_with_known_missing_objects(
    db: sqlite3.Connection, actual: dict, expected: dict, *, indexes: frozenset[str],
    triggers: frozenset[str] = frozenset(),
) -> bool:
    if any(actual[key] != expected[key] for key in ("sql", "columns", "foreign_keys")):
        return False
    actual_indexes = {item["name"]: item for item in actual["indexes"]}
    expected_indexes = {item["name"]: item for item in expected["indexes"]}
    if (not set(actual_indexes) <= set(expected_indexes)
            or not set(expected_indexes) - set(actual_indexes) <= indexes
            or not _repair_names_available(
                db, (("index", name) for name in set(expected_indexes) - set(actual_indexes))
            )
            or any({key: value for key, value in item.items() if key != "seq"}
                   != {key: value for key, value in expected_indexes[name].items()
                       if key != "seq"}
                   for name, item in actual_indexes.items())):
        return False
    actual_triggers = {item["name"]: item for item in actual["triggers"]}
    expected_triggers = {item["name"]: item for item in expected["triggers"]}
    return (set(actual_triggers) <= set(expected_triggers)
            and set(expected_triggers) - set(actual_triggers) <= triggers
            and _repair_names_available(
                db, (("trigger", name) for name in set(expected_triggers) - set(actual_triggers))
            )
            and all(item == expected_triggers[name]
                    for name, item in actual_triggers.items()))


CURRENT_STATE_REPAIRABLE_INDEXES = frozenset({
    "idx_state_dispatcher_terminal", "idx_state_dispatcher_expiry",
})


@lru_cache(maxsize=1)
def _canonical_state_declaration():
    with closing(sqlite3.connect(":memory:")) as reference:
        create_state_schema(reference)
        return _source_declaration(reference, "state")


def state_current_or_known_dispatcher_index_repair(db: sqlite3.Connection) -> bool:
    """Exact State target, allowing only the existing two lease-index repairs."""
    if state_schema_is_current(db):
        return True
    names = (*STATE_SOURCE_TABLES, "state", *STATE_TARGET_INDEXES)
    if db.execute(
        "select 1 from temp.sqlite_schema where name collate nocase in ("
        + ",".join("?" for _ in names) + ") limit 1", names,
    ).fetchone() is not None:
        return False
    if db.execute(
        "select 1 from main.sqlite_schema where name collate nocase in ("
        + ",".join("?" for _ in STATE_SOURCE_TABLES) + ") limit 1",
        STATE_SOURCE_TABLES,
    ).fetchone() is not None:
        return False
    return _current_table_with_known_missing_objects(
        db, _source_declaration(db, "state"), _canonical_state_declaration(),
        indexes=CURRENT_STATE_REPAIRABLE_INDEXES,
    )


def configs_current_or_known_skill_guard_repair(db: sqlite3.Connection, *, action_variant: str,
                                                include_business_actions: bool = False,
                                                include_email_history: bool = False,
                                                canonical_email: bool = False,
                                                inline_legacy_owner: bool = False,
                                                physical_provenance: bool = False,
                                                compact_payload: bool = False) -> bool:
    """Exact target, allowing only Store's known missing Skill guard repair."""
    if current_configs_schema_is_current(
        db,
        action_variant=action_variant,
        include_followup_sync=True,
        include_business_sync=True,
        include_task_delivery=True,
        include_errors=True,
        include_feedback=True,
        include_business_actions=include_business_actions,
        include_email_history=include_email_history,
        canonical_email=canonical_email,
        inline_legacy_owner=inline_legacy_owner,
        physical_provenance=physical_provenance, compact_payload=compact_payload,
    ):
        return True
    target = _config_target_declarations()
    if not _current_table_with_known_missing_objects(
        db, _source_declaration(db, "schedules"), target["schedules"],
        indexes=CURRENT_SCHEDULE_REPAIRABLE_INDEXES,
    ):
        return False
    if not _current_table_with_known_missing_objects(
        db, _source_declaration(db, "configs"), target["configs"],
        indexes=CURRENT_CONFIG_REPAIRABLE_INDEXES,
        triggers=CURRENT_CONFIG_REPAIRABLE_TRIGGERS,
    ):
        return False
    expected_runs = current_run11_objects()
    actual_runs = _config_run_objects(db)
    run_types = _canonical_run_object_types()
    return (action_schema_is_current(db, allow_missing_objects=True,
                                     include_reply=True, reply_variant=action_variant,
                                     include_email=True, include_skill_config_receipts=True,
                                     include_followup_sync=True, include_business_sync=True,
                                     include_task_delivery=True,
                                     include_errors=True, include_feedback=True,
                                     include_business_actions=include_business_actions,
                                     include_email_history=include_email_history,
                                     canonical_email=canonical_email,
                                     inline_legacy_owner=inline_legacy_owner,
                                     physical_provenance=physical_provenance, compact_payload=compact_payload)
            and action_storage.email_action_auxiliary_objects_are_current(
                db, reply_variant=action_variant, physical_provenance=physical_provenance, compact_payload=compact_payload)
            and current_run11_schema_is_current(db, allow_missing_objects=True)
            and actual_runs.get("runs") == expected_runs["runs"]
            and set(actual_runs) <= set(expected_runs)
            and set(expected_runs) == set(run_types)
            and _repair_names_available(db, ((run_types[name], name)
                for name in set(expected_runs) - set(actual_runs)))
            and all(actual_runs.get(name, sql) == sql
                    for name, sql in expected_runs.items())
            and all(_source_declaration(db, kind)["sql"] is None
                    for kind in (*CONFIG_SOURCE_TABLES, _OBSERVATIONS)))


def require_current_email_pair(path: Path, *, store_version: str, email_version: int) -> None:
    """Read-only source selection before constructor caches or journal changes."""
    path = Path(path).resolve()
    if not path.exists():
        return
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("begin")
        tables = {row[0] for row in db.execute("select name from main.sqlite_schema where type='table'")}
        names = {row[0] for row in db.execute(
            "select name from main.sqlite_schema where type in ('table','view')"
        )}
        email = db.execute("select coalesce(max(version),0) from main.email_schema_migrations").fetchone()[0] if "email_schema_migrations" in tables else None
        if email == SOURCE_EMAIL_VERSION:
            marker = db.execute("select value from main.service_state where key=?", (VERSION_KEY,)).fetchone() if "service_state" in tables else None
        elif "state" in tables:
            marker = db.execute(f"select value from ({state_projection('service_state')}) where key=?", (VERSION_KEY,)).fetchone()
        else:
            marker = None
        store = marker[0] if marker is not None else None
        if (store, email) == (SOURCE_STORE_VERSION, SOURCE_EMAIL_VERSION):
            raise sqlite3.IntegrityError("Store19/Email51 requires the paired schema release before target constructors")
        from app.email_current_schema import (
            _CURRENT_TABLE_SQL, current_canonical_email_parent_is_current,
        )
        email_tables = {"emails", "email_classifications", "email_category_configs", "email_category_folder_bindings", *(name for name, _ in _CURRENT_TABLE_SQL), *SOURCE_TABLES, *PROVIDER_SOURCE_TABLES, PROVIDER_TARGET}
        if store is None and email is None and not email_tables.intersection(names):
            # The Store's existing bootstrap-source check separately owns core
            # collisions and partial unversioned bootstrap refusal.
            return
        if store == store_version:
            if email == email_version:
                if (current_canonical_email_parent_is_current(db)
                        and state_current_or_known_dispatcher_index_repair(db)
                        and provider_state_schema_is_current(db) and any(
                    configs_current_or_known_skill_guard_repair(db, action_variant=variant,
                                                               include_business_actions=True,
                                                               include_email_history=True,
                                                               canonical_email=True,
                                                               inline_legacy_owner=True, physical_provenance=True, compact_payload=True)
                    for variant in ('actual_no_memory_fk', 'fresh_memory_fk')
                )):
                    return
                raise sqlite3.IntegrityError("malformed current provider/config declaration")
            if email is None and current_canonical_email_parent_is_current(db):
                if state_current_or_known_dispatcher_index_repair(db) and any(configs_current_or_known_skill_guard_repair(db, action_variant=variant,
                                                                                  include_business_actions=True,
                                                                                  include_email_history=True, canonical_email=True,
                                                                                  inline_legacy_owner=True, physical_provenance=True, compact_payload=True)
                       for variant in ('actual_no_memory_fk', 'fresh_memory_fk')):
                    return  # Complete Store-first, before Email's fresh transaction.
                raise sqlite3.IntegrityError("malformed current Store-first config declaration")
        raise sqlite3.IntegrityError("unsupported or mixed Store/Email source schema")


def _markers(db, *, current=False):
    if current and not state_schema_is_current(db):
        raise ValueError("Email paired State target declaration mismatch")
    source = state_projection("service_state") if current else "select * from main.service_state"
    version = db.execute(
        f"select value,updated_at from ({source}) where key=?", (VERSION_KEY,)
    ).fetchone()
    versions = tuple(db.execute(
        "select version,applied_at from main.email_schema_migrations order by version"
    ))
    return version, versions


def email_inverse_metadata(source):
    return {key: source[key] for key in (
        "action_variant", "email_variant", "source_actions", "source_tables", "external_triggers"
    )}


def _inverse_metadata(source):
    return {
        **email_inverse_metadata(source),
        "business": business_inverse_metadata(source["business"]),
        "provider": source["provider"],
        "configs": source["configs"],
        "state": source["state"],
        "deferral_receipts": source["deferral_receipts"],
        "tasks": source["tasks"],
        "runs11": source["runs11"],
        "confirmation": source["confirmation"],
    }


def _project_config_source(db, action_variant: str) -> dict:
    """Bind native declarations before Email replaces six account blockers.

    Preparation is a read transaction. It does not open a writable database,
    create a scratch copy, or read config and receipt bodies.
    """
    original = capture_config_declarations(db)
    if original['variant'] not in ('actual', 'fresh'):
        raise ValueError('unsupported pre-Email config source variant')
    if action_variant not in ('actual_no_memory_fk', 'fresh_memory_fk'):
        raise ValueError('unsupported Email action source variant')
    if _config_run_objects(db) != _config_run_declarations(mapped=False):
        raise ValueError('unsupported native runs declaration')
    observation = _source_declaration(db, _OBSERVATIONS)
    if _fingerprint(observation) not in _OBSERVATION_HASHES.values():
        raise ValueError('unsupported observation source declaration')
    if db.execute(f'select 1 from main.{_OBSERVATIONS} limit 1').fetchone():
        raise ValueError('nonempty training observations cannot be retired')
    if any(_source_declaration(db, name)['sql'] is not None for name in ('configs', 'schedules')):
        raise ValueError('partial config target already exists')
    _no_temp_names(db)
    _foreign_key_children(db, set(CONFIG_SOURCE_TABLES) | {_OBSERVATIONS})
    action_storage._no_sql_dependencies(db, set(CONFIG_SOURCE_TABLES) | {_OBSERVATIONS})
    projected = {**original, 'variant': original['variant'] + '_email52',
                 'tables': {**original['tables']}}
    accounts = {**original['tables']['email_accounts']}
    replacements = _target_external_sql()
    account_names = {item['name'] for item in accounts['triggers']}
    if not account_names <= replacements.keys():
        raise ValueError('unsupported account blocker declarations')
    accounts['triggers'] = sorted((
        {'name': item['name'], 'sql': replacements[item['name']]}
        if item['name'] in account_names else item
        for item in accounts['triggers']
    ), key=lambda item: item['name'])
    projected['tables']['email_accounts'] = accounts
    _config_metadata(projected)
    result = {'configs': projected, 'action_variant': action_variant,
              'observations': observation}
    _validate_config_metadata(result)
    return result


def _migrate_pair(release: "ActionEmailSchemaRelease") -> None:
    """Carry prepared metadata, never business rows, into the real cold opener."""
    payload = {
        "root": str(release.root), "database_path": str(release.database_path),
        "original_version": [release.original_version[0], _encoded_sqlite_value(release.original_version[1])],
        "versions": [[version, _encoded_sqlite_value(stamp)] for version, stamp in release.versions],
        "source_schema": _inverse_metadata(release.source_schema), "source_fk": release.source_fk,
        "target_store_version": release.target_store_version, "target_email_version": release.target_email_version,
        "source_bindings": {key: asdict(value) for key, value in release.source_bindings.items()},
        "recovery_source_path": str(release.recovery_source_path) if release.recovery_source_path else None,
    }
    # The child emits the complete inverse before COMMIT. Thus every committed
    # target has recoverable metadata even if verification/cold opening fails.
    try:
        completed = subprocess.run(
            [sys.executable, "-c", "from app.action_email_schema_release import _paired_initializer; _paired_initializer()"],
            input=json.dumps(payload, ensure_ascii=False, allow_nan=False), text=True,
            cwd=release.root, env={**os.environ, "PYTHONPATH": str(release.root)},
            check=True, capture_output=True, timeout=15 * 60,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        _receive_pair_inverse(release, error.stdout, required=False)
        raise
    _receive_pair_inverse(release, completed.stdout, required=True)


_PAIR_INVERSE = (
    ("FOLLOWUP_SYNC", "followup_sync"), ("TASK_DELIVERY", "task_delivery"),
    ("ERROR", "errors"), ("FEEDBACK", "feedback"),
    ("BUSINESS_CANDIDATES", "business_candidates"), ("BUSINESS_ACTIONS", "business_actions"),
    ("EMAIL_HISTORY", "email_history"), ("EMAIL_CANONICAL", "email_canonical"),
    ("LEGACY_OWNER", "legacy_owner"), ("FINAL_ACTIONS", "final_actions"),
)


def _emit_pair_inverse(release):
    for label, key in _PAIR_INVERSE:
        print(label + "_INVERSE=" + json.dumps(
            release.source_schema[key], ensure_ascii=False, allow_nan=False), flush=True)


def _receive_pair_inverse(release, output, *, required):
    if isinstance(output, bytes):
        output = output[:output.rfind(b"\n") + 1].decode("utf-8")
    # A timeout may interrupt an uncommitted child's write. Only full records
    # count, and only a complete set is published into the parent's metadata.
    lines = [line.rstrip("\n") for line in (output or "").splitlines(keepends=True)
             if line.endswith("\n")]
    received = {}
    for label, key in _PAIR_INVERSE:
        prefix = label + "_INVERSE="
        value = next((line[len(prefix):] for line in reversed(lines) if line.startswith(prefix)), None)
        if value is None:
            if required:
                raise ValueError("paired initializer omitted " + key + " inverse metadata")
            return
        received[key] = json.loads(value)
    release.source_schema.update(received)


@dataclass
class ActionEmailSchemaRelease:
    root: Path
    database_path: Path
    original_version: tuple[str, str | bytes]
    versions: tuple[tuple[int, str | bytes], ...]
    source_schema: dict
    source_fk: tuple[tuple, ...]
    target_store_version: str
    target_email_version: int
    migrate: Callable[["ActionEmailSchemaRelease"], None] = _migrate_pair
    source_bindings: dict[str, SourceVersionBinding] = field(default_factory=dict)
    recovery_source_path: Path | None = None

    def _verify_integrity(self, db):
        if _foreign_keys(db) != self.source_fk or db.execute("pragma quick_check").fetchone()[0] != "ok":
            raise ValueError("Email paired declaration integrity/FK mismatch")

    def _verify_source(self, db):
        if _markers(db) != (self.original_version, self.versions):
            raise ValueError("Email paired source markers changed")
        # Only declarations are compared: current business rows and allocator
        # waters are inputs to conversion, never a stored pre-publication copy.
        current = capture_email_action_source(db)
        current["business"] = capture_business_source(db)
        current["provider"] = capture_provider_state_source(db)
        current["configs"] = _project_config_source(db, current["action_variant"])
        current["state"] = capture_state_source(db)
        current["deferral_receipts"] = capture_native_receipts(db)
        current["confirmation"] = capture_confirmation_source(db)
        current["tasks"] = capture_task20_source(db)
        current["runs11"] = capture_run11_source(db)
        current_declarations = _inverse_metadata(current)
        source_declarations = _inverse_metadata(self.source_schema)
        # Counter presence is mutable state; retain it for inverse transport,
        # but compare only the Tasks declarations after native current writes.
        for declarations in (current_declarations, source_declarations):
            declarations["tasks"] = {
                key: declarations["tasks"][key] for key in ("variant", "tables")
            }
        if current_declarations != source_declarations:
            raise ValueError("paired Email/Business source declaration changed")
        self._verify_integrity(db)

    def _verify_target(self, db):
        verify_confirmation_retired(db)
        if capture_native_receipts(db) is not None:
            raise ValueError("paired target retains native deferral receipt table")
        if not final_action_schema_is_current(db):
            raise ValueError("paired final Actions/provenance declaration mismatch")
        if not current_canonical_email_parent_is_current(db):
            raise ValueError("paired canonical Email target declaration mismatch")
        if not task20_schema_is_current(db) or not task20_feedback_child_is_current(db, include_feedback=True):
            raise ValueError("paired Tasks20 target declaration mismatch")
        marker, versions = _markers(db, current=True)
        if marker is None or marker[0] != self.target_store_version or \
                versions[:-1] != self.versions or not versions or \
                versions[-1] != (self.target_email_version, marker[1]):
            raise ValueError("Email paired target markers mismatch")
        if not action_schema_is_current(
            db, include_reply=True, reply_variant=self.source_schema["action_variant"],
            include_email=True, include_skill_config_receipts=True,
            include_followup_sync=True, include_business_sync=True,
            include_task_delivery=True,
            include_errors=True,
            include_feedback=True,
            include_business_actions=True,
            include_email_history=True,
            canonical_email=True,
            inline_legacy_owner=True, physical_provenance=True, compact_payload=True,
        ) or db.execute(
            "select 1 from main.sqlite_schema where name in (" +
            ",".join("?" for _ in (*SOURCE_TABLES, *FOLLOWUP_SYNC_SOURCE_TABLES,
                                   *TASK_DELIVERY_KINDS, "errors", *FEEDBACK_KINDS,
                                   *CANDIDATE_SOURCE_TABLES.values(),
                                   *BUSINESS_ACTION_KINDS, *HISTORY_KINDS)) + ") limit 1",
            (*SOURCE_TABLES, *FOLLOWUP_SYNC_SOURCE_TABLES, *TASK_DELIVERY_KINDS,
             "errors", *FEEDBACK_KINDS, *CANDIDATE_SOURCE_TABLES.values(),
             *BUSINESS_ACTION_KINDS, *HISTORY_KINDS),
        ).fetchone():
            raise ValueError("Email paired target declaration mismatch")
        if not business_schema_is_current(
            db,
            self.source_schema["business"]["variant"],
            include_followup_sync=True,
            include_candidates=True,
            include_business_actions=True,
            inline_legacy_owner=True,
        ):
            raise ValueError("paired Business target declaration mismatch")
        if not provider_state_schema_is_current(db):
            raise ValueError("paired Provider target declaration mismatch")
        if not current_run11_schema_is_current(db):
            raise ValueError("paired Runs11 target declaration mismatch")
        if not current_configs_schema_is_current(
            db,
            action_variant=self.source_schema["action_variant"],
            include_followup_sync=True,
            include_business_sync=True,
            include_task_delivery=True,
            include_errors=True,
            include_feedback=True,
            include_business_actions=True,
            include_email_history=True,
            canonical_email=True,
            inline_legacy_owner=True, physical_provenance=True, compact_payload=True,
        ):
            raise ValueError("paired Configs target declaration mismatch")
        account_names = {item['name'] for item in self.source_schema['configs']
                         ['configs']['tables']['email_accounts']['triggers']}
        expected = {name: sql for name, sql in _target_external_sql().items()
                    if name not in account_names
                    and " on main.email_accounts" in sql.lower()}
        triggers = tuple(db.execute(
            "select name,sql from main.sqlite_schema where type='trigger' and name in (" +
            ",".join("?" for _ in EXTERNAL_TRIGGER_NAMES) + ") order by name", EXTERNAL_TRIGGER_NAMES
        ))
        if triggers != tuple(sorted(expected.items())):
            raise ValueError("Email paired external trigger mismatch")
        self._verify_integrity(db)

    def publish(self):
        self.migrate(self)
        self.verify_loaded()

    def apply(self, *, on_prepared: Callable[[], None] | None = None):
        """Apply the same captured atomic protocol, including both markers."""
        with closing(sqlite3.connect(self.database_path, isolation_level=None)) as db:
            db.execute("pragma foreign_keys=off")
            db.execute("begin immediate")
            try:
                self._verify_source(db)
                email_inverse = migrate_email_action_schema(db)
                business_inverse = migrate_business_schema(db)
                apply_provider_state_migration(db, self.source_schema["provider"])
                if email_inverse != email_inverse_metadata(self.source_schema):
                    raise ValueError("Email captured inverse declaration mismatch")
                if business_inverse != business_inverse_metadata(
                    self.source_schema["business"]
                ):
                    raise ValueError("Business captured inverse declaration mismatch")
                if not provider_state_schema_is_current(db):
                    raise ValueError("Provider target declaration mismatch")
                config_inverse = capture_config_source(db)
                if config_inverse != self.source_schema["configs"]:
                    raise ValueError("Config captured inverse declaration mismatch")
                migrate_config_schema(db, config_inverse)
                migrate_state_schema(db, self.source_schema["state"])
                migrate_native_receipts(db)
                migrate_task20_schema(db, self.source_schema["tasks"])
                retire_confirmation_schema(db, self.source_schema["confirmation"])
                migrate_run11_schema(db, self.source_schema["runs11"])
                followup_source = capture_followup_sync_source(db)
                followup_inverse = migrate_followup_sync_schema(db, followup_source)
                delivery_source = capture_task_delivery_source(db)
                delivery_inverse = migrate_task_delivery_schema(db, delivery_source)
                error_source = capture_action_error_source(db)
                error_inverse = migrate_action_error_schema(db, error_source)
                feedback_source = capture_action_feedback_source(db)
                feedback_inverse = migrate_action_feedback_schema(db, feedback_source)
                candidate_inverse = capture_business_candidate_source(db)
                migrate_business_candidate_schema(db, candidate_inverse)
                business_action_inverse = capture_business_action_source(db)
                migrate_business_action_schema(db, business_action_inverse)
                history_inverse = capture_email_history_source(db)
                migrate_email_history_schema(db, history_inverse)
                canonical_inverse = capture_email_canonical_source(db)
                migrate_email_canonical_schema(db, canonical_inverse)
                legacy_owner_inverse = capture_legacy_owner_source(db)
                migrate_legacy_owner(db, legacy_owner_inverse)
                final_inverse = migrate_final_actions(db, bindings=self.source_bindings)
                now = datetime.now(timezone.utc).isoformat()
                update_state_rows(db, "service_state",
                    {"value": self.target_store_version, "updated_at": now},
                    where_sql="key=?", parameters=(VERSION_KEY,))
                db.execute(
                    "insert into main.email_schema_migrations(version,applied_at) values(?,?)",
                    (self.target_email_version, now),
                )
                self._verify_target(db)
                self.source_schema["followup_sync"] = followup_inverse
                self.source_schema["task_delivery"] = delivery_inverse
                self.source_schema["errors"] = error_inverse
                self.source_schema["feedback"] = feedback_inverse
                self.source_schema["business_candidates"] = candidate_inverse
                self.source_schema["business_actions"] = business_action_inverse
                self.source_schema["email_history"] = history_inverse
                self.source_schema["email_canonical"] = canonical_inverse
                self.source_schema["legacy_owner"] = legacy_owner_inverse
                self.source_schema["final_actions"] = final_inverse
                if on_prepared is not None:
                    on_prepared()
                db.commit()
            except BaseException:
                db.rollback()
                raise
        self.verify_loaded()

    def verify_loaded(self):
        with closing(sqlite3.connect(self.database_path.as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("begin")
            self._verify_target(db)

    def rollback(self):
        original_deferral_source = self.source_schema["deferral_receipts"]
        from app.email_body_source import prepare_recovery_originals

        source_reader = None

        def read_message(account_id, stable_message_identity):
            nonlocal source_reader
            if source_reader is None:
                from app.email_store import EmailStore
                from app.email_source_reader import build_email_source_reader
                from app.config import effective_env_values

                source_reader = build_email_source_reader(EmailStore(self.database_path),
                    environment_factory=effective_env_values)
            return source_reader.read(account_id, stable_message_identity)

        # Originals are prepared before the inverse write lease. The backup
        # supplies bytes only; it never restores earlier business facts.
        email_originals = prepare_recovery_originals(self.database_path,
            backup_path=self.recovery_source_path, read_message=read_message)
        with closing(sqlite3.connect(self.database_path, isolation_level=None)) as db:
            db.execute("pragma foreign_keys=off")
            db.execute("begin immediate")
            try:
                current = db.execute(
                    "select 1 from main.sqlite_schema where name='state' collate nocase"
                ).fetchone() is not None
                if not current:
                    self._verify_source(db)
                    db.commit()
                    return
                self._verify_target(db)
                cursor = db.execute(signal_projection())
                names = tuple(item[0] for item in cursor.description)
                for row in cursor:
                    values = dict(zip(names, row, strict=True))
                    binding = SourceVersionBinding(**{name: values[name] for name in SOURCE_FIELDS})
                    self.source_bindings[binding.identity_key] = binding
                from app.dws_client import DwsClient
                from app.business_console_source_reader import retained_console_sources
                reader = RequestSourceReader(provider_reader=DwsClient(),
                    console_lookup=lambda source_ref: retained_console_sources(db, source_ref))
                restore_final_actions(db, self.source_schema["final_actions"], read_body=lambda binding:
                    read_recovery_source_body(binding, backup_path=self.recovery_source_path, reader=reader))
                restore_legacy_owner(db, self.source_schema["legacy_owner"])
                restore_email_canonical_schema(db, self.source_schema["email_canonical"],
                    read_body=lambda row, ref: email_originals[(row["account_id"], row["stable_message_identity"])])
                restore_email_history_schema(db, self.source_schema["email_history"])
                restore_business_action_schema(db, self.source_schema["business_actions"])
                restore_business_candidate_schema(db, self.source_schema["business_candidates"])
                restore_action_feedback_schema(db, self.source_schema["feedback"])
                restore_action_error_schema(db, self.source_schema["errors"])
                restore_task_delivery_schema(db, self.source_schema["task_delivery"])
                restore_followup_sync_schema(
                    db, self.source_schema["followup_sync"]
                )
                restore_confirmation_schema(db, self.source_schema["confirmation"])
                restore_task20_schema(db, self.source_schema["tasks"])
                self.source_schema["deferral_receipts"] = restore_native_receipts(
                    db, preserve_empty=original_deferral_source is not None)
                restore_state_schema(db, self.source_schema["state"])
                restore_run11_schema(db, self.source_schema["runs11"])
                restore_config_schema(db, self.source_schema["configs"])
                restore_business_schema(db, self.source_schema["business"])
                restore_email_action_schema(db, email_inverse_metadata(self.source_schema))
                rollback_provider_state_migration(db, self.source_schema["provider"])
                db.execute(
                    "update main.service_state set value=?,updated_at=? where key=?",
                    (*self.original_version, VERSION_KEY),
                )
                # Target acceptance required the complete exact source ledger
                # plus this one target row. Deleting it restores every original
                # version/timestamp without touching current business facts.
                db.execute("delete from main.email_schema_migrations where version=?", (self.target_email_version,))
                self._verify_source(db)
                db.commit()
            except BaseException:
                self.source_schema["deferral_receipts"] = original_deferral_source
                db.rollback()
                raise

    def finalize(self):
        self.verify_loaded()


def _timestamp_from_pipe(value):
    # TEXT affinity preserves ordinary scalars as text; native BLOB timestamps
    # are legal and accepted by the predecessor constructors.
    return bytes.fromhex(value["v"]) if isinstance(value, dict) and value.get("t") == "blob" else value


def _paired_initializer() -> None:
    payload = json.load(sys.stdin)
    release = ActionEmailSchemaRelease(
        Path(payload["root"]), Path(payload["database_path"]),
        (payload["original_version"][0], _timestamp_from_pipe(payload["original_version"][1])),
        tuple((version, _timestamp_from_pipe(stamp)) for version, stamp in payload["versions"]),
        payload["source_schema"], tuple(tuple(row) for row in payload["source_fk"]),
        payload["target_store_version"], payload["target_email_version"],
        source_bindings={key: SourceVersionBinding(**value) for key, value in payload["source_bindings"].items()},
        recovery_source_path=Path(payload["recovery_source_path"]) if payload["recovery_source_path"] else None,
    )
    release.apply(on_prepared=lambda: _emit_pair_inverse(release))
    from app.store import AutoReplyStore
    from app.email_store import EmailStore
    from app.email_training_migration import refresh_current_selected_baseline
    store = AutoReplyStore(release.database_path)
    store.compact_terminal_meeting_consumer_inputs()
    email_store = EmailStore(release.database_path)
    refresh_current_selected_baseline(email_store)


def prepare_action_email_schema_release(
    root: Path, path: Path, *, target_store_version: str, target_email_version: int,
    recovery_source_path: Path | None = None,
) -> ActionEmailSchemaRelease:
    """Capture the exact Store19/Email51 pair; callers must supply a target pair.

    The coordinator selects the real pair and subprocess. Explicit sentinel
    targets still exercise this same atomic protocol in confined tests.
    """
    root, path = root.resolve(), path.resolve()
    if type(target_store_version) is not str or not target_store_version.strip() or \
            target_store_version == SOURCE_STORE_VERSION or \
            type(target_email_version) is not int or target_email_version <= SOURCE_EMAIL_VERSION:
        raise ValueError("Email paired protocol requires an explicit different target pair")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("begin")
        version, versions = _markers(db)
        if version is None or version[0] != SOURCE_STORE_VERSION or \
                not versions or versions[-1][0] != SOURCE_EMAIL_VERSION:
            raise ValueError("Email paired protocol requires the exact Store19/Email51 source")
        source = capture_email_action_source(db)
        source["business"] = capture_business_source(db)
        source["provider"] = capture_provider_state_source(db)
        source["configs"] = _project_config_source(db, source["action_variant"])
        source["state"] = capture_state_source(db)
        source["deferral_receipts"] = capture_native_receipts(db)
        source["confirmation"] = capture_confirmation_source(db)
        source["tasks"] = capture_task20_source(db)
        source["runs11"] = capture_run11_source(db)
        release = ActionEmailSchemaRelease(
            root, path, version, versions, source, _foreign_keys(db),
            target_store_version, target_email_version,
            source_bindings=prepare_final_source_bindings(db, root),
            recovery_source_path=recovery_source_path,
        )
        release._verify_integrity(db)
        return release
