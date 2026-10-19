"""Final Actions/Provenance transition; inverse consumes current facts and bodies.

The caller owns the FK-off schema transaction. Prepared bindings carry exact
source identity and coordinates, never originals. Inverse metadata preserves
retired source-container aliases/clocks only; a supplied exact reader rehydrates
bodies for the old schema. Neither direction restores a business-row snapshot.
"""

from collections import Counter
from collections.abc import Callable, Mapping
from contextlib import closing
from dataclasses import asdict, replace
from functools import lru_cache
import json
import math
import sqlite3
from types import SimpleNamespace

from app import action_storage as actions
from app.action_email_storage import _columns, _external_dependencies, _insert_rows, _sequence, _set_sequence
from app.business_candidate_storage import _collision
from app.business_source_reader import SourceVersionBinding
from app.email_canonical_storage import _action_objects
from app.email_canonical_definitions import EMAILS_TABLE_SQL, EMAILS_TRIGGER_SQL
from app.provenance_definitions import SOURCE_FIELDS, SIGNAL_FACT_FIELDS, SIGNAL_FIELDS
from app.provenance_storage import create_provenance_schema, signal_projection
from app.storage_payload import ACTION_RECORD_TYPES


_SIGNAL = "business_task_signals"
_IDENTITY_FIELDS = SOURCE_FIELDS[:-5]


@lru_cache(maxsize=4)
def _declarations(variant, final):
    return _action_objects(variant, target=True, inline_legacy_owner=True,
                           physical_provenance=final, compact_payload=final)


@lru_cache(maxsize=1)
def _document_objects():
    from app.store import BUSINESS_SOURCE_DOCUMENT_SCHEMA
    with closing(sqlite3.connect(":memory:")) as db:
        for sql in BUSINESS_SOURCE_DOCUMENT_SCHEMA:
            db.execute(sql)
        return actions._objects(db, "business_source_documents")


@lru_cache(maxsize=1)
def _provenance_objects():
    with closing(sqlite3.connect(":memory:")) as db:
        create_provenance_schema(db)
        return actions._objects(db, "provenance")


@lru_cache(maxsize=1)
def _email_guards():
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute(EMAILS_TABLE_SQL)
        for sql in EMAILS_TRIGGER_SQL:
            db.execute(sql)
        return {obj["name"]: obj["sql"] for obj in actions._objects(db, "emails")
                if obj["type"] == "trigger"}


def _require_transaction(db):
    if not db.in_transaction or db.execute("pragma foreign_keys").fetchone()[0]:
        raise ValueError("final Actions transition requires caller-owned FK-off transaction")
    _collision(db, ("actions", "provenance", "business_source_documents"))


def final_action_schema_is_current(db):
    variant = actions._reply_variant_for(db)
    return (actions._objects(db, "actions") == _declarations(variant, True)
            and actions._objects(db, "provenance") == _provenance_objects()
            and not actions._objects(db, "business_source_documents"))


def _raw_rows(db, columns, *, final):
    selected = ["json(payload_json)" if final and name == "payload_json" else
                "record_type" if name == "_kind" else '"' + name + '"' for name in columns]
    return [tuple(row) for row in db.execute("select " + ",".join(selected) + " from main.actions order by id")]


def _create_actions(db, variant, *, final):
    objects = _declarations(variant, final)
    db.execute(next(obj["sql"] for obj in objects if obj["type"] == "table"))
    return objects


def _finish_actions(db, objects):
    for category in ("index", "trigger"):
        for obj in objects:
            if obj["type"] == category:
                db.execute(obj["sql"])


def _sequences(db):
    return [tuple(row) for row in db.execute("select name,seq from main.sqlite_sequence")]


def _restore_sequences(db, rows, document_sequence):
    db.execute("delete from main.sqlite_sequence")
    _insert_rows(db, "sqlite_sequence", ("name", "seq"),
                 (row for row in rows if row[0] != "business_source_documents"))
    _set_sequence(db, "business_source_documents", document_sequence)


def _dependencies(db):
    # Configs' existing guards use the preserved generated Action endpoints.
    # They are not renamed or rewritten by this transition.
    allowed = tuple(row[0] for row in db.execute(
        "select name from main.sqlite_schema where type='trigger' and tbl_name='configs'"))
    email_guards = []
    for name, sql in db.execute("select name,sql from main.sqlite_schema where type='trigger' and tbl_name='emails'"):
        if name in _email_guards():
            if sql != _email_guards()[name]:
                raise ValueError("changed canonical Email Action dependency")
            email_guards.append(name)
    _external_dependencies(db, targets=("actions", "business_source_documents"),
                           allowed_trigger_names=(*allowed, *email_guards))
    for (table,) in db.execute("select name from main.sqlite_schema where type='table' and name not glob 'sqlite_*'"):
        if table != "actions" and any(row[2] == "business_source_documents" for row in
                                      db.execute('pragma main.foreign_key_list("' + table.replace('"', '""') + '")')):
            raise ValueError("unmigrated source-document child")


def migrate_final_actions(db, *, bindings: Mapping[str, SourceVersionBinding]):
    """Retire originals and convert the complete graph in one schema step."""
    _require_transaction(db)
    variant = actions._reply_variant_for(db)
    if (actions._objects(db, "actions") != _declarations(variant, False)
            or actions._objects(db, "business_source_documents") != _document_objects()
            or actions._objects(db, "provenance")):
        raise ValueError("unsupported final Actions source declaration")
    _dependencies(db)
    columns = _columns(db, "actions")
    rows = _raw_rows(db, columns, final=False)
    kind_at, payload_at = columns.index("record_type"), columns.index("payload_json")
    old_layouts = actions.action_payloads(variant).layouts
    new_layouts = actions.action_payloads(variant, physical_provenance=True).layouts
    documents = {row[0]: tuple(row) for row in db.execute(
        "select id,identity_key,body,created_at from main.business_source_documents")}
    signal_values = {}
    for row in rows:
        if row[kind_at] != _SIGNAL:
            continue
        values = json.loads(row[payload_at])
        # Existing scalar extraction applies the source's declared affinities.
        cursor = db.execute("select " + ",".join("ba_task_signals_" + name for name in
                            (*_IDENTITY_FIELDS, "source_document_id"))
                            + " from main.actions where id=?", (row[0],))
        logical = dict(zip((*_IDENTITY_FIELDS, "source_document_id"), cursor.fetchone(), strict=True))
        document = documents[logical.pop("source_document_id")]
        expected = SourceVersionBinding.from_signal(SimpleNamespace(**logical, evidence_text=document[2]))
        binding = bindings.get(document[1])
        if (not isinstance(binding, SourceVersionBinding) or expected.identity_key != document[1]
                or replace(binding, native_provenance_json=None, provider_locator_json=None) != expected):
            raise ValueError("missing or changed exact source binding")
        signal_values[row[0]] = (binding, values)
    used = {binding.identity_key for binding, _ in signal_values.values()}
    if used != {row[1] for row in documents.values()}:
        raise ValueError("source binding does not account for every retired document")
    metadata = {
        "reply_variant": variant,
        "source_document_aliases": [[row[0], row[1], actions._encoded_native_sqlite_value(row[3])]
                                    for row in sorted(documents.values())],
        "source_document_sequence": _sequence(db, "business_source_documents"),
    }
    counts = Counter(binding.identity_key for binding, _ in signal_values.values())
    shared = {key: index for index, key in enumerate(sorted(key for key, count in counts.items() if count > 1), 1)}
    current_sequences = _sequences(db)
    foreign_keys = [tuple(row) for row in db.execute("pragma foreign_key_check")]
    packed = []
    for row in rows:
        row = list(row)
        kind = row[kind_at]
        if kind == _SIGNAL:
            binding, values = signal_values[row[0]]
            fields = dict.fromkeys(SIGNAL_FIELDS)
            fields.update({name: values[name] for name in SIGNAL_FACT_FIELDS})
            if binding.identity_key in shared:
                fields["provenance_id"] = shared[binding.identity_key]
            else:
                fields.update(asdict(binding))
            # Existing encoded business facts must not be encoded a second time.
            for name in SOURCE_FIELDS:
                fields[name] = actions._encoded_native_sqlite_value(fields[name])
            row[payload_at] = new_layouts[kind].pack(fields, scalar_codec=lambda value: value)
        else:
            values = json.loads(row[payload_at])
            old_layouts[kind].pack(values, scalar_codec=lambda value: value)
            row[payload_at] = new_layouts[kind].pack(values, scalar_codec=lambda value: value)
        row[payload_at] = db.execute("select jsonb(?)", (row[payload_at],)).fetchone()[0]
        row[kind_at] = ACTION_RECORD_TYPES.code(kind)
        packed.append(tuple(row))
    create_provenance_schema(db)
    seen = {}
    for binding, _ in signal_values.values():
        if binding.identity_key in shared:
            seen[binding.identity_key] = binding
    _insert_rows(db, "provenance", ("id", *SOURCE_FIELDS),
                 ((shared[key], *(getattr(binding, field) for field in SOURCE_FIELDS))
                  for key, binding in sorted(seen.items())))
    db.execute("drop table main.actions")
    objects = _create_actions(db, variant, final=True)
    _insert_rows(db, "actions", tuple("_kind" if name == "record_type" else name for name in columns), packed)
    _finish_actions(db, objects)
    db.execute("drop table main.business_source_documents")
    _restore_sequences(db, current_sequences, None)
    if (not final_action_schema_is_current(db)
            or foreign_keys != [tuple(row) for row in db.execute("pragma foreign_key_check")]):
        raise ValueError("final Actions declaration or FK differs")
    return metadata


def _source_scalar_json(value):
    # The old Reply declaration accepts ordinary finite JSON numbers, whereas
    # the final codec uses an exact binary mantissa/exponent representation.
    if isinstance(value, dict) and value.get("t") == "real":
        value = math.ldexp(value["m"], value["e"])
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def restore_final_actions(db, metadata, *, read_body: Callable[[SourceVersionBinding], str]):
    """Rehydrate the old source schema from current rows, including new facts."""
    _require_transaction(db)
    if not final_action_schema_is_current(db):
        raise ValueError("unsupported final Actions inverse declaration")
    if set(metadata) != {"reply_variant", "source_document_aliases", "source_document_sequence"}:
        raise ValueError("unsupported final Actions inverse metadata")
    variant = actions._reply_variant_for(db)
    if variant != metadata["reply_variant"]:
        raise ValueError("final Actions inverse variant differs")
    columns = _columns(db, "actions")
    rows = _raw_rows(db, columns, final=True)
    kind_at, payload_at = columns.index("_kind"), columns.index("payload_json")
    layouts = actions.action_payloads(variant, physical_provenance=True).layouts
    cursor = db.execute(signal_projection())
    signals = {row[0]: dict(zip((item[0] for item in cursor.description), row, strict=True)) for row in cursor}
    aliases = {row[1]: row for row in metadata["source_document_aliases"]}
    water = max((row[0] for row in aliases.values()), default=0)
    water = max(water, metadata["source_document_sequence"] or 0)
    documents = {}
    old_payloads = {}
    for public_id, row in signals.items():
        binding = SourceVersionBinding(**{field: row[field] for field in SOURCE_FIELDS})
        if binding.identity_key not in documents:
            body = read_body(binding)
            if not isinstance(body, str) or not binding.matches_body(body):
                raise ValueError("inverse source body differs from exact binding")
            if binding.identity_key in aliases:
                alias_id, _, created_at = aliases[binding.identity_key]
                created_at = (bytes.fromhex(created_at["v"]) if isinstance(created_at, dict)
                              and created_at.get("t") == "blob" else created_at)
            else:
                water += 1
                alias_id, created_at = water, row["created_at"]
            documents[binding.identity_key] = (alias_id, binding.identity_key, body, created_at)
        values = {name: row[name] for name in (*_IDENTITY_FIELDS, *SIGNAL_FACT_FIELDS)}
        values["source_document_id"] = documents[binding.identity_key][0]
        old_payloads[public_id] = actions._native_payload(values)
    current_sequences = _sequences(db)
    foreign_keys = [tuple(row) for row in db.execute("pragma foreign_key_check")]
    unpacked = []
    for row in rows:
        row = list(row)
        kind = row[kind_at]
        values = layouts[kind].unpack(row[payload_at])
        if kind == _SIGNAL:
            row[payload_at] = old_payloads[int(row[columns.index("legacy_key")])]
        elif kind in actions.REPLY_ACTION_TYPES:
            row[payload_at] = "{" + ",".join(json.dumps(name) + ":" + _source_scalar_json(value)
                                             for name, value in values.items()) + "}"
        else:
            row[payload_at] = json.dumps(values, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        unpacked.append(tuple(row))
    for obj in _document_objects():
        if obj["type"] == "table":
            db.execute(obj["sql"])
    _insert_rows(db, "business_source_documents", ("id", "identity_key", "body", "created_at"), documents.values())
    for obj in _document_objects():
        if obj["type"] == "trigger":
            db.execute(obj["sql"])
    db.execute("drop table main.actions")
    objects = _create_actions(db, variant, final=False)
    _insert_rows(db, "actions", tuple("record_type" if name == "_kind" else name for name in columns), unpacked)
    _finish_actions(db, objects)
    db.execute("drop table main.provenance")
    _restore_sequences(db, current_sequences, max(water, metadata["source_document_sequence"] or 0)
                       if documents or metadata["source_document_sequence"] is not None else None)
    if (actions._objects(db, "actions") != _declarations(variant, False)
            or actions._objects(db, "business_source_documents") != _document_objects()
            or foreign_keys != [tuple(row) for row in db.execute("pragma foreign_key_check")]):
        raise ValueError("final Actions inverse declaration or FK differs")
