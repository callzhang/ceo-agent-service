"""Exact opt-in Actions projection for the two Email history tables."""

from app.action_email_definitions import _encoded_value, _encoded_value_check


HISTORY_FIELDS = {
    "email_historical_classification_history": (
        "event_id", "stable_message_identity", "model_id", "predicted_category",
        "threshold", "probability", "important", "action_outcome", "created_at",
    ),
    "email_historical_operations": (
        "operation_id", "account_id", "stable_message_identity", "model_id",
        "classification_id", "action_plan_id", "action_ids_json",
        "predicted_category", "threshold", "probability", "important", "status",
        "action_outcome", "created_at", "updated_at",
    ),
}
HISTORY_KINDS = tuple(HISTORY_FIELDS)
HISTORY_KEYS = {kind: fields[0] for kind, fields in HISTORY_FIELDS.items()}
HISTORY_INTEGER = frozenset(("important", "classification_id"))
HISTORY_REAL = frozenset(("threshold", "probability"))


def history_column(kind: str, field: str) -> str:
    if kind not in HISTORY_FIELDS or field not in HISTORY_FIELDS[kind]:
        raise ValueError("unsupported Email history field")
    return ("email_history_" if kind == HISTORY_KINDS[0] else "email_operation_") + field


def history_projection(kind: str, *, include_rowid: bool = False) -> str:
    if kind not in HISTORY_FIELDS:
        raise ValueError("unsupported Email history kind")
    fields = HISTORY_FIELDS[kind]
    selected = ["cast(legacy_key as integer) as _source_rowid"] if include_rowid else []
    selected += [history_column(kind, field) + " as " + field for field in fields]
    return "select " + ",".join(selected) + " from actions where record_type='" + kind + "'"


def history_schema_parts(*, payloads=None):
    columns, checks, indexes = [], [], []
    for kind, fields in HISTORY_FIELDS.items():
        prefix = "email_history_" if kind == HISTORY_KINDS[0] else "email_operation_"
        for field in fields:
            affinity = "integer" if field in HISTORY_INTEGER else "real" if field in HISTORY_REAL else "text"
            columns.append(
                f"{prefix}{field} {affinity} generated always as "
                f"(case when record_type='{kind}' then "
                + (payloads.value(kind, field) if payloads else _encoded_value("payload_json", "$." + field)) + " end) virtual"
            )
        rules = [payloads.check(kind, field) if payloads else _encoded_value_check("payload_json", "$." + field) for field in fields]
        rules.append(payloads.shape(kind) if payloads else "json_remove(payload_json," + ",".join(repr("$." + field) for field in fields) + ")='{}'")
        if kind == HISTORY_KINDS[0]:
            for field in ("stable_message_identity", "model_id", "action_outcome", "created_at"):
                rules.append(f"{prefix}{field} is not null and trim({prefix}{field})<>''")
            for field in ("event_id", "predicted_category"):
                rules.append(f"{prefix}{field} is null or trim({prefix}{field})<>''")
            for field in ("threshold", "probability"):
                rules.append(f"{prefix}{field} is null or ({prefix}{field}>=0.0 and {prefix}{field}<=1.0)")
            rules.append(f"{prefix}important is null or {prefix}important in (0,1)")
        else:
            for field in fields[1:]:
                rules.append(f"{prefix}{field} is not null")
            rules.extend((
                f"json_valid({prefix}action_ids_json)",
                f"{prefix}important in (0,1)",
                f"{prefix}status in ('processing','terminal')",
            ))
            indexes.append("create unique index idx_actions_email_operation_identity "
                           "on actions(email_operation_model_id,email_operation_stable_message_identity) "
                           "where record_type='email_historical_operations'")
            indexes.append("create index idx_actions_email_operation_processing "
                           "on actions(email_operation_status,email_operation_created_at,email_operation_operation_id) "
                           "where record_type='email_historical_operations'")
        indexes.append(f"create unique index idx_actions_{'history' if kind == HISTORY_KINDS[0] else 'operation'}_public_id "
                       f"on actions({prefix}{fields[0]}) where record_type='{kind}'")
        checks.append(f"check(record_type<>'{kind}' or ({' and '.join('(' + rule + ')' for rule in rules)}))")
    indexes.append("create index idx_actions_email_history_created "
                   "on actions(email_history_created_at,email_history_event_id) "
                   "where record_type='email_historical_classification_history'")
    return tuple(columns), tuple(checks), tuple(indexes)
