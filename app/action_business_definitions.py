"""Finite opt-in Actions shape for the ten source Business action families."""

from app.action_email_definitions import EMAIL_NUMERIC_SCOPE, _encoded_value, _encoded_value_check
from app.business_candidate_definitions import TRIM_WS
from app.provenance_definitions import SIGNAL_FIELDS, physical_signal_checks, physical_promotion_trigger


BUSINESS_ACTION_FIELDS = {
    "business_attention_events": ("id", "attention_item_id", "event_type", "signal_id", "before_json", "after_json", "reason", "created_at"),
    "business_attention_proposal_tasks": ("attention_item_id", "task_id", "created_at"),
    "business_attention_tasks": ("attention_item_id", "task_id", "created_at"),
    "business_project_evidence": ("project_id", "signal_id", "created_at"),
    "business_task_anchor_links": ("id", "task_id", "anchor_id", "status", "active", "evidence_signal_id", "reason", "created_at"),
    "business_task_date_evidence": ("id", "task_id", "date_type", "value_at", "raw_phrase", "source_signal_id", "actor_kind", "actor_user_id", "actor_name", "created_at"),
    "business_task_evidence": ("task_id", "signal_id", "evidence_role", "created_at"),
    "business_task_relations": ("from_task_id", "to_task_id", "relation_type", "status", "supporting_signal_id", "reason", "created_at"),
    "business_task_signals": ("id", "source_type", "source_ref", "source_time", "conversation_id", "conversation_title", "author_user_id", "author_name", "source_document_id", "context_json", "dedupe_key", "created_at", "author_kind"),
    "business_work_cluster_tasks": ("cluster_id", "task_id", "created_at"),
}
BUSINESS_ACTION_KINDS = tuple(BUSINESS_ACTION_FIELDS)
BUSINESS_ACTION_AUTO = (
    "business_attention_events", "business_task_anchor_links",
    "business_task_date_evidence", "business_task_signals",
)
BUSINESS_ACTION_KEYS = {
    "business_attention_proposal_tasks": ("attention_item_id", "task_id"),
    "business_attention_tasks": ("attention_item_id", "task_id"),
    "business_project_evidence": ("project_id", "signal_id"),
    "business_task_evidence": ("task_id", "signal_id", "evidence_role"),
    "business_task_relations": ("from_task_id", "to_task_id", "relation_type"),
    "business_work_cluster_tasks": ("cluster_id", "task_id"),
}
BUSINESS_ACTION_INTEGER = {
    "attention_item_id", "task_id", "project_id", "signal_id", "anchor_id", "active",
    "evidence_signal_id", "source_signal_id", "from_task_id", "to_task_id",
    "supporting_signal_id", "source_document_id", "cluster_id",
    "body_utf8_bytes", "provenance_id",
}
BUSINESS_ACTION_DEFAULTS = {
    "business_attention_events": {"created_at": "current_timestamp"},
    "business_attention_proposal_tasks": {"created_at": "current_timestamp"},
    "business_attention_tasks": {"created_at": "current_timestamp"},
    "business_project_evidence": {"created_at": "current_timestamp"},
    "business_task_anchor_links": {"status": "'proposed'", "active": "1", "reason": "''", "created_at": "current_timestamp"},
    "business_task_date_evidence": {"value_at": "''", "actor_user_id": "''", "actor_name": "''", "created_at": "current_timestamp"},
    "business_task_evidence": {"created_at": "current_timestamp"},
    "business_task_relations": {"status": "'proposed'", "reason": "''", "created_at": "current_timestamp"},
    "business_task_signals": {"source_time": "''", "conversation_id": "''", "conversation_title": "''", "author_user_id": "''", "author_name": "''", "context_json": "'{}'", "created_at": "current_timestamp", "author_kind": "'unknown'"},
    "business_work_cluster_tasks": {"created_at": "current_timestamp"},
}
BUSINESS_ACTION_PARENT_FIELDS = {
    "attention_item_id": ("business", "attention_item_public_id"),
    "task_id": ("business", "task_public_id"),
    "project_id": ("business", "project_public_id"),
    "anchor_id": ("business", "anchor_public_id"),
    "cluster_id": ("business", "work_cluster_public_id"),
    "from_task_id": ("business", "task_public_id"),
    "to_task_id": ("business", "task_public_id"),
    "signal_id": ("actions", "business_signal_public_id"),
    "evidence_signal_id": ("actions", "business_signal_public_id"),
    "source_signal_id": ("actions", "business_signal_public_id"),
    "supporting_signal_id": ("actions", "business_signal_public_id"),
    "source_document_id": ("business_source_documents", "id"),
}
BUSINESS_ACTION_FKS = {
    kind: tuple(field for field in fields if field in BUSINESS_ACTION_PARENT_FIELDS)
    for kind, fields in BUSINESS_ACTION_FIELDS.items()
}


def business_action_column(kind, field):
    if kind not in BUSINESS_ACTION_FIELDS or (field not in BUSINESS_ACTION_FIELDS[kind]
            and not (kind == "business_task_signals" and field in SIGNAL_FIELDS)):
        raise ValueError("unsupported Business action field")
    return "ba_" + kind.removeprefix("business_") + "_" + field


def _source_check(kind, field):
    column = business_action_column(kind, field)
    if field in ("reason", "raw_phrase", "source_type", "source_ref", "dedupe_key") and (
        field != "reason" or kind == "business_attention_events"
    ):
        return f"trim({column},{TRIM_WS})<>''"
    if field in ("before_json", "after_json", "context_json"):
        return f"json_valid({column}) and json_type({column})='object'"
    enums = {
        ("business_attention_events", "event_type"): ("opened", "updated", "category_changed", "resolved", "reopened"),
        ("business_task_anchor_links", "status"): ("proposed", "confirmed", "rejected"),
        ("business_task_date_evidence", "date_type"): ("assigned_at", "requested_deadline_at", "external_deadline_at", "committed_deadline_at", "estimated_deadline_at", "next_check_at"),
        ("business_task_date_evidence", "actor_kind"): ("human", "system", "agent", "unknown"),
        ("business_task_evidence", "evidence_role"): ("discovery", "commitment", "assignment", "acceptance", "completion", "correction", "merge_identity", "relevance", "resolution"),
        ("business_task_relations", "relation_type"): ("depends_on", "blocks", "supports", "supersedes", "related_to"),
        ("business_task_relations", "status"): ("proposed", "confirmed", "rejected"),
        ("business_task_signals", "author_kind"): ("human", "system", "agent", "unknown"),
    }
    if kind == "business_task_anchor_links" and field == "active":
        return f"{column} in (0,1)"
    options = enums.get((kind, field))
    return f"{column} in ({','.join(map(repr, options))})" if options else None


def business_action_schema_parts(*, physical_provenance=False, outer_columns=(), payloads=None):
    """Generated source fields, exact checks/FKs, indexes and native behavior triggers."""
    columns = ["business_signal_public_id integer generated always as "
               "(case when record_type='business_task_signals' then cast(legacy_key as integer) end) virtual"]
    checks, indexes, triggers = [], [
        "create unique index idx_actions_business_signal_public_id on actions(business_signal_public_id)"
    ], []
    for kind, source_fields in BUSINESS_ACTION_FIELDS.items():
        fields = (("id", *SIGNAL_FIELDS) if physical_provenance and kind == "business_task_signals"
                  else source_fields)
        payload_fields = tuple(field for field in fields if field != "id" or kind not in BUSINESS_ACTION_AUTO)
        for field in payload_fields:
            column = business_action_column(kind, field)
            affinity = "integer" if field in BUSINESS_ACTION_INTEGER else "text"
            columns.append(f"{column} {affinity} generated always as "
                           f"(case when record_type='{kind}' then "
                           + (payloads.value(kind, field) if payloads else _encoded_value("payload_json", "$." + field)) + " end) virtual")
        if physical_provenance and kind == "business_task_signals":
            rules = physical_signal_checks(lambda field: business_action_column(kind, field), payloads=payloads)
        else:
            rules = [*(payloads.check(kind, field) if payloads else _encoded_value_check("payload_json", "$." + field) for field in payload_fields),
                     *(f"{business_action_column(kind, field)} is not null" for field in payload_fields)]
            rules.extend(filter(None, (_source_check(kind, field) for field in payload_fields)))
            rules.append(payloads.shape(kind) if payloads else "json_remove(payload_json," + ",".join(repr("$." + field) for field in payload_fields) + ")='{}'")
        if kind == "business_task_relations":
            rules.append(f"{business_action_column(kind, 'from_task_id')}<>"
                         f"{business_action_column(kind, 'to_task_id')}")
        checks.append(f"check(record_type<>'{kind}' or ({' and '.join(rules)}))")
        fk_fields = ("provenance_id",) if physical_provenance and kind == "business_task_signals" else BUSINESS_ACTION_FKS[kind]
        for field in fk_fields:
            parent, endpoint = (("provenance", "id") if field == "provenance_id"
                                else BUSINESS_ACTION_PARENT_FIELDS[field])
            checks.append(f"foreign key({business_action_column(kind, field)}) "
                          f"references {parent}({endpoint}) on update no action on delete no action")
        if kind in BUSINESS_ACTION_KEYS:
            keys = ",".join(business_action_column(kind, field) for field in BUSINESS_ACTION_KEYS[kind])
            indexes.append(f"create unique index idx_actions_{kind}_key on actions({keys}) where record_type='{kind}'")
        if kind in BUSINESS_ACTION_AUTO:
            name = "actions:" + kind
            triggers.append(f"create trigger trg_actions_{kind}_sequence before insert on actions "
                            f"when new.record_type='{kind}' begin "
                            f"insert into sqlite_sequence(name,seq) select '{name}',0 "
                            f"where not exists(select 1 from sqlite_sequence where name='{name}'); "
                            f"update sqlite_sequence set seq=max(seq,cast(new.legacy_key as integer)) "
                            f"where name='{name}'; end")
    for kind, field in (("business_task_anchor_links", "task_id"),
                        ("business_task_signals", "dedupe_key")):
        indexes.append(f"create unique index idx_actions_{kind}_{field} "
                       f"on actions({business_action_column(kind, field)}" +
                       (f",{business_action_column(kind, 'anchor_id')}" if kind == "business_task_anchor_links" else "") +
                       f") where record_type='{kind}'")
    for kind, fields in (
        ("business_attention_events", ("attention_item_id", "created_at", "id")),
        ("business_attention_proposal_tasks", ("task_id", "attention_item_id")),
        ("business_attention_tasks", ("task_id", "attention_item_id")),
        ("business_task_anchor_links", ("task_id", "status", "active", "anchor_id")),
        ("business_task_date_evidence", ("task_id", "id")),
        ("business_task_evidence", ("task_id", "created_at", "signal_id")),
        ("business_task_relations", ("from_task_id", "relation_type", "to_task_id")),
        *((() if physical_provenance else (("business_task_signals", ("source_type", "source_ref", "source_time", "id")),))),
        ("business_work_cluster_tasks", ("task_id", "cluster_id")),
    ):
        # Both relation endpoints are NOT NULL in the source. These generated
        # columns are NULL for every other Action kind, so the field predicate
        # selects exactly the relation partition and remains usable by each
        # arm of a source-shaped OR query.
        predicate = ("ba_task_relations_from_task_id is not null"
                     if kind == "business_task_relations" else f"record_type='{kind}'")
        indexes.append(f"create index idx_actions_{kind}_lookup on actions("
                       + ",".join("cast(legacy_key as integer)" if field == "id"
                                  else business_action_column(kind, field) for field in fields)
                       + f") where {predicate}")
    indexes.extend((
        "create index idx_actions_business_task_relations_reverse on actions("
        "ba_task_relations_to_task_id,ba_task_relations_relation_type,"
        "ba_task_relations_from_task_id) where ba_task_relations_to_task_id is not null",
        *((() if physical_provenance else (
            "create index idx_actions_business_task_signals_document on actions("
            "ba_task_signals_source_document_id) where record_type='business_task_signals'",
        ))),
    ))
    if physical_provenance:
        indexes.extend((
            "create index idx_actions_signal_shared on actions(ba_task_signals_provenance_id,business_signal_public_id) where record_type='business_task_signals' and ba_task_signals_provenance_id is not null",
            "create unique index idx_actions_signal_inline_key on actions(ba_task_signals_identity_key) where record_type='business_task_signals' and ba_task_signals_provenance_id is null",
            "create index idx_actions_signal_inline_source on actions(ba_task_signals_source_type,ba_task_signals_source_ref,ba_task_signals_source_time,business_signal_public_id) where record_type='business_task_signals' and ba_task_signals_provenance_id is null",
        ))
    triggers.extend((
        "create trigger trg_actions_business_date_evidence_immutable_insert before insert on actions "
        "when new.record_type='business_task_date_evidence' and exists(select 1 from actions "
        "where record_type='business_task_date_evidence' and legacy_key=new.legacy_key) "
        "begin select raise(abort,'business task date evidence is immutable'); end",
        "create trigger trg_actions_business_date_evidence_immutable_update before update on actions "
        "when old.record_type='business_task_date_evidence' "
        "begin select raise(abort,'business task date evidence is immutable'); end",
        "create trigger trg_actions_business_date_evidence_immutable_delete before delete on actions "
        "when old.record_type='business_task_date_evidence' "
        "begin select raise(abort,'business task date evidence is immutable'); end",
        "create trigger trg_actions_business_signals_immutable_insert before insert on actions "
        "when new.record_type='business_task_signals' and exists(select 1 from actions "
        "where record_type='business_task_signals' and ("
        + ("id=new.id or " if physical_provenance else "")
        + "legacy_key=new.legacy_key or "
        "ba_task_signals_dedupe_key=new.ba_task_signals_dedupe_key)) "
        "begin select raise(abort,'business task signals are immutable: UNIQUE id or dedupe_key'); end",
        (physical_promotion_trigger(outer_columns, lambda field: business_action_column("business_task_signals", field), payloads=payloads)
         if physical_provenance else
         "create trigger trg_actions_business_signals_immutable_update before update on actions "
         "when old.record_type='business_task_signals' "
         "begin select raise(abort,'business task signals are immutable'); end"),
        "create trigger trg_actions_business_signals_immutable_delete before delete on actions "
        "when old.record_type='business_task_signals' "
        "begin select raise(abort,'business task signals are immutable'); end",
    ))
    if physical_provenance:
        triggers.append(
            "create trigger trg_actions_business_signals_native_leaf_insert before insert on actions "
            "when new.record_type='business_task_signals' "
            "and new.ba_task_signals_native_provenance_json is not null "
            "and json_valid(new.ba_task_signals_native_provenance_json) "
            "and exists(select 1 from json_each(new.ba_task_signals_native_provenance_json,'$.leaf_path') "
            "where type not in ('text','integer') "
            "or (type='integer' and value<0)) "
            "begin select raise(abort,'invalid native source leaf path'); end"
        )
    checks.append("check(record_type='business_task_signals' or business_signal_public_id is null)")
    return columns, checks, indexes, triggers


# The shared numeric scope now carries record_type<>'action' (see EMAIL_NUMERIC_SCOPE).
BUSINESS_NUMERIC_SCOPE = EMAIL_NUMERIC_SCOPE


def business_action_projection(kind, *, source_variant="actual", include_rowid=False,
                               public_id_index=False):
    if kind not in BUSINESS_ACTION_KINDS or source_variant not in ("actual", "fresh"):
        raise ValueError("unsupported Business action projection")
    fields = BUSINESS_ACTION_FIELDS[kind]
    select = (["cast(a.legacy_key as integer) as _source_rowid"] if include_rowid else [])
    select.extend(("cast(a.legacy_key as integer) as id" if field == "id" and kind in BUSINESS_ACTION_AUTO
                   else f"a.{business_action_column(kind, field)} as {field}") for field in fields)
    # The numeric public-id readers must use the partial expression index; without
    # statistics SQLite otherwise prefers idx_actions_email_public_key and sorts.
    source = "actions a indexed by idx_actions_public_id" if public_id_index else "actions a"
    return ("select " + ",".join(select) + f" from {source} where a.record_type='{kind}'"
            + (" and " + BUSINESS_NUMERIC_SCOPE if public_id_index else ""))
