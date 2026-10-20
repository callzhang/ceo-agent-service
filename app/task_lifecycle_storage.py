"""One source-shaped compiler for current Task domain/lifecycle mutations.

The caller owns the connection, domain CAS, transaction, and side-effect order.
All source fields are evaluated once, in their original parameter order.
"""
from dataclasses import dataclass
import json

from app.action_storage import _encoded_native_sqlite_value
from app import task20_definitions as source
from app.task_lifecycle_definitions import SOURCE_FIELDS, TASK20_LAYOUTS
from app.task_lifecycle_definitions import DISPATCH_KINDS
from app.task_lifecycle_jsonb import restore_sql
from app.state_storage import upsert_state, update_state, update_state_rows, state_json_field


@dataclass(frozen=True)
class TaskSet:
    sql: str
    parameters: tuple


def encoded(value):
    return json.dumps(_encoded_native_sqlite_value(value), ensure_ascii=False,
                      allow_nan=False, separators=(",", ":"))


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def source_assignments(kind, values, expressions, *, payload_source="tasks.payload_json"):
    """SQL and parameters for all former payload fields, at their sole owner."""
    if (set(values) | set(expressions)) - set(source.TASK20_PAYLOAD_FIELDS[kind]):
        raise ValueError("unknown Task source field")
    if set(values) & set(expressions):
        raise ValueError("Task source field supplied twice")
    inputs = [("?", f) for f in values]
    inputs.extend(("ceo_native_json(" + sql + ")", f) for f, sql in expressions.items())
    if not inputs:
        return TaskSet("", ())
    owners = SOURCE_FIELDS.get(kind, {})
    slots = {f: "_task_value_" + str(i) for i, (_, f) in enumerate(inputs)}
    original = restore_sql(kind, payload_source) if owners else payload_source
    updated = source.TASK20_LAYOUTS[kind].assignments_sql(original, slots)
    columns = ["payload_json", *owners.values()]
    outputs = (["ceo_task_domain(" + repr(kind) + ",_task_source)"] + [
        "ceo_task_cell(_task_source," + str(source.TASK20_PAYLOAD_FIELDS[kind].index(f)) + ")"
        for f in owners
    ]) if owners else ["_task_source"]
    # OFFSET is a flattening barrier: expressions such as current_timestamp,
    # source version reads and parameterized CASEs execute once per source row.
    sql = "(" + ",".join(map(_quote, columns)) + ")=(select " + ",".join(outputs)
    sql += " from(select " + updated + " as _task_source from(select " + ",".join(
        expression + " as " + slots[f] for expression, f in inputs
    ) + " limit -1 offset 0) limit -1 offset 0))"
    return TaskSet(sql, tuple(encoded(v) for v in values.values()))


def _expression_bindings(expressions, parameters):
    """Partition anonymous SQLite binds without counting quoted question marks."""
    offset, result = 0, {}
    for field, expression in expressions.items():
        quote, count, index = None, 0, 0
        while index < len(expression):
            char = expression[index]
            if quote:
                if char == quote:
                    if index + 1 < len(expression) and expression[index + 1] == quote:
                        index += 1
                    else:
                        quote = None
            elif char in ("'", '"', "`"):
                quote = char
            elif char == "[":
                quote = "]"
            elif char == "?":
                count += 1
            index += 1
        result[field] = tuple(parameters[offset:offset + count])
        offset += count
    if offset != len(parameters):
        raise ValueError("Task expression bindings differ from anonymous parameters")
    return result


def source_insert(kind, values, expressions, parameters=(), *, replace=False):
    """Return columns, VALUES expressions, bindings for source payload fields."""
    owners = SOURCE_FIELDS.get(kind, {})
    required = {c[0] for c in source.TASK20_COLUMNS[kind] if c[2]}
    bound = dict(values)
    if replace:
        from app.action_storage import _source_bound_null
        bound = {f: v for f, v in bound.items() if not (
            f in required and f in source.TASK20_DEFAULTS[kind] and _source_bound_null(v))}
    inputs = {
        f: "jsonb(ceo_native_json(" + (
            "coalesce(" + expression + "," + source.TASK20_DEFAULTS[kind][f] + ")"
            if replace and f in required and f in source.TASK20_DEFAULTS[kind] else expression
        ) + "))" for f, expression in expressions.items()
    }
    inputs.update({f: "jsonb(ceo_native_json(" + source.TASK20_DEFAULTS[kind].get(f, "null") + "))"
                   for f in source.TASK20_PAYLOAD_FIELDS[kind] if f not in inputs and f not in bound})
    bindings = _expression_bindings(expressions, parameters)
    layout = TASK20_LAYOUTS[kind]
    domain = {f: expr for f, expr in inputs.items() if f not in owners}
    payload = layout.pack({f: bound.get(f) for f in layout.fields}, scalar_codec=_encoded_native_sqlite_value)
    columns = ["payload_json", *owners.values()]
    outputs = [layout.assignments_sql("?", domain)]
    output_bindings = [payload, *(v for f in domain for v in bindings.get(f, ()))]
    for field in owners:
        if field in bound:
            outputs.append("jsonb(?)")
            output_bindings.append(encoded(bound[field]))
        else:
            outputs.append(inputs[field])
            output_bindings.extend(bindings.get(field, ()))
    return columns, outputs, tuple(output_bindings)


def claim_projection():
    """Current claim facts from Tasks; error clocks/IDs are passive history."""
    return (
        "select t.lifecycle_adapter_name as adapter_name,t.legacy_key as source_id,"
        "t.lifecycle_lease_owner as owner,t.lifecycle_owner_pid as owner_pid,"
        "t.lifecycle_generation as generation,t.lifecycle_lease_expires_at as lease_expires_at,"
        "history.terminal_at,history.last_error,history.created_at,history.updated_at,"
        "cast(history.legacy_key as integer) as rowid "
        "from tasks t left join state history on history.record_type='dispatcher_claim_leases' "
        "and history.adapter_name=t.lifecycle_adapter_name and history.source_id=t.legacy_key "
        "where t.lifecycle_adapter_name is not null"
    )


def _task_identity(adapter, source_id):
    kind = DISPATCH_KINDS[adapter]
    return ("record_type=? and record_type not in ('workbench_tasks','workbench_turns') "
            "and cast(legacy_key as integer)=?", (kind, int(source_id)))


def _write_claim_history(db, adapter, source_id, values, *, create=False, repack=True):
    """Derive metadata after the Task CAS; never use history as a lease fence."""
    if create:
        original = {f: v for f, v in values.items() if f not in ("terminal_at", "last_error")}
        upsert_state(db, "dispatcher_claim_leases",
            {"adapter_name": adapter, "source_id": source_id, **original},
            {f: state_json_field(f) for f in original}
            | {"terminal_at": "json_quote('')", "last_error": "json_quote('')"})
    else:
        if repack:
            # Dispatcher point writes used the native full-row State writer;
            # its declared field order is itself part of the source JSON bytes.
            row = db.execute("select cast(legacy_key as integer) from state "
                "where record_type='dispatcher_claim_leases' and adapter_name=? and source_id=?",
                (adapter, source_id)).fetchone()
            count = 0 if row is None else update_state(db, "dispatcher_claim_leases", row[0], values)
        else:
            count = update_state_rows(db, "dispatcher_claim_leases", values,
                where_sql="adapter_name=? and source_id=?", parameters=(adapter, source_id))
        if count != 1:
            raise ValueError("Task claim history identity is missing")


def acquire_claim(db, *, adapter_name, source_id, owner, owner_pid, lease_expires_at, now,
                  domain_values=None, domain_sql=None, domain_parameters=(),
                  structural_sql=None, domain_where=""):
    identity, keys = _task_identity(adapter_name, source_id)
    row = db.execute("select lifecycle_adapter_name,lifecycle_generation from tasks where " + identity, keys).fetchone()
    if row is None:
        raise ValueError("dispatcher Task source is missing")
    if row[0] is not None and row[0] != adapter_name:
        # Distinct adapter sequence identity is historical bookkeeping, never
        # ownership arbitration. Current owner/expiry is checked on Task below.
        prior = db.execute("select generation from state where record_type='dispatcher_claim_leases' "
                           "and adapter_name=? and source_id=?", (adapter_name, source_id)).fetchone()
        generation = 1 if prior is None else int(prior[0]) + 1
    else:
        generation = 1 if row[1] is None else int(row[1]) + 1
    domain = source_assignments(DISPATCH_KINDS[adapter_name], domain_values or {}, domain_sql or {})
    fields = {"lease_owner": owner, "owner_pid": owner_pid, "generation": generation,
              "lease_expires_at": lease_expires_at, "adapter_name": adapter_name}
    sql = ",".join(_quote(f) + "=jsonb(?)" for f in fields)
    if domain.sql:
        sql += "," + domain.sql
    if structural_sql:
        sql += "," + ",".join(_quote(f) + "=" + expression for f, expression in structural_sql.items())
    guard = " and (" + domain_where + ")" if domain_where else ""
    changed = db.execute("update tasks set " + sql + " where " + identity
        + " and (lifecycle_lease_owner is null or lifecycle_lease_owner='' or lifecycle_lease_expires_at<=?)" + guard,
        (*map(encoded, fields.values()), *domain.parameters, *domain_parameters, *keys, now)).rowcount
    if changed != 1:
        raise ValueError("dispatcher source lease is still active")
    _write_claim_history(db, adapter_name, source_id, {
        "owner": owner, "owner_pid": owner_pid, "generation": generation,
        "lease_expires_at": lease_expires_at, "terminal_at": "", "last_error": "",
        "updated_at": now,
    }, create=True)
    return generation


def assert_claim(db, *, envelope, owner, now):
    identity, keys = _task_identity(envelope.adapter_name, envelope.source_id)
    if db.execute("select 1 from tasks where " + identity
        + " and lifecycle_adapter_name=? and lifecycle_lease_owner=? and lifecycle_generation=? "
        "and lifecycle_lease_expires_at>?",
        (*keys, envelope.adapter_name, owner, envelope.generation, now)).fetchone() is None:
        raise ValueError("dispatcher source claim is no longer owned")


def transition_claim(db, *, envelope, owner, values, history, now=None,
                     domain_values=None, domain_sql=None, domain_parameters=(), domain_where=""):
    """Update one Task domain+claim CAS and its passive metadata in one transaction."""
    identity, keys = _task_identity(envelope.adapter_name, envelope.source_id)
    domain = source_assignments(DISPATCH_KINDS[envelope.adapter_name],
                                domain_values or {}, domain_sql or {})
    sql = ",".join(_quote(f) + "=jsonb(?)" for f in values)
    if domain.sql:
        sql = ",".join(s for s in (sql, domain.sql) if s)
    guard = " and lifecycle_adapter_name=? and lifecycle_lease_owner=? and lifecycle_generation=?"
    params = (*map(encoded, values.values()), *domain.parameters, *domain_parameters,
              *keys, envelope.adapter_name, owner, envelope.generation)
    if now is not None:
        guard += " and lifecycle_lease_expires_at>?"
        params += (now,)
    if domain_where:
        guard += " and (" + domain_where + ")"
    changed = db.execute("update tasks set " + sql + " where " + identity + guard, params).rowcount
    if changed != 1:
        raise ValueError("dispatcher source claim is no longer owned")
    _write_claim_history(db, envelope.adapter_name, envelope.source_id, history)


def recover_claims(db, *, adapter, source_ids, values, history, where_sql="", parameters=(),
                   repack_history=False):
    """Existing domain recovery, fenced using current Task values only."""
    if not source_ids:
        return 0
    ids = tuple(str(value) for value in source_ids)
    clause = "record_type=? and lifecycle_adapter_name=? and legacy_key in (" + ",".join("?" for _ in ids) + ")"
    if where_sql:
        clause += " and (" + where_sql + ")"
    rows = db.execute("update tasks set " + ",".join(_quote(f) + "=jsonb(?)" for f in values)
        + " where " + clause + " returning legacy_key",
        (*map(encoded, values.values()), DISPATCH_KINDS[adapter], adapter, *ids, *parameters)).fetchall()
    for row in rows:
        _write_claim_history(db, adapter, row[0], history, repack=repack_history)
    return len(rows)


def forget_pruned_claims(db, deleted):
    """Preserve source absence after the existing terminal-history retention."""
    for adapter, source_id, generation in deleted:
        if adapter not in DISPATCH_KINDS:
            continue
        db.execute(
            "update tasks set lease_owner=null,owner_pid=null,generation=null,"
            "lease_expires_at=null,adapter_name=null "
            "where lifecycle_adapter_name=? and legacy_key=? and record_type=? "
            "and lifecycle_generation is ? "
            "and (lifecycle_lease_owner is null or lifecycle_lease_owner='')",
            (adapter, source_id, DISPATCH_KINDS[adapter], generation),
        )
