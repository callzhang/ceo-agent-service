"""Fixed runtime declaration for the common external Action entity."""

from app.storage_payload import ACTION_RECORD_TYPES

ACTION_KIND = "action"
ACTION_KIND_CODE = ACTION_RECORD_TYPES.code(ACTION_KIND)
ACTION_FIELDS = (
    "operation", "status", "action_plan_id", "external_action_key", "next_attempt_at", "started_at", "created_at",
    "updated_at", "prepared_body", "prepared_at", "delivery_channel", "delivery_key",
    "feedback_token", "postfix_version", "delivery_receipt_id", "conversation_id", "trigger_message_id", "recall_key",
    "prepared_receipt_id", "recall_status", "recall_error", "recalled_at", "agent_run_id", "business_object_key",
    "action_identity",
)
ACTION_INTEGER_FIELDS = frozenset({"agent_run_id", "delivery_receipt_id", "prepared_receipt_id"})
ACTION_ALLOWED_FIELDS = frozenset(ACTION_FIELDS)

RECEIPT_FIELDS = (
    "attempt_number", "status", "operation", "target", "provider_result_id",
    "provider_result_json", "result_digest", "agent_run_id", "observed_at", "error_text",
    "dispatched_at", "started_at", "finished_at", "execution_id", "action_index",
    "outcome_json", "created_at", "updated_at", "provider_receipt_id",
)
RECEIPT_ALLOWED_FIELDS = frozenset(RECEIPT_FIELDS)
ACTION_GENERATED_COLUMNS = tuple(
    f"action_{field} {'integer' if field in ACTION_INTEGER_FIELDS else 'text'} generated always as "
    f"(case when _kind={ACTION_KIND_CODE} then json_extract(action_data,'$.{field}') end) virtual"
    for field in ACTION_FIELDS
)

ACTION_INDEX_DDL = (
    "create unique index idx_actions_action_external_key on actions(action_external_action_key) "
    "where record_type='action' and action_external_action_key is not null",
    "create unique index idx_actions_action_direct_email_plan_operation "
    "on actions(action_action_plan_id,action_operation) where record_type='action' "
    "and action_action_plan_id is not null and action_operation in "
    "('archive','flag_important','mark_read','move','trash','label')",
    "create unique index idx_actions_action_delivery_key on actions(action_delivery_channel,action_delivery_key) "
    "where record_type='action' and action_delivery_channel is not null and action_delivery_key is not null",
)

RECEIPT_SCHEMA_DDL = (
    "create table action_receipts (id integer primary key, action_id integer not null references actions(id), "
    "operation text, target text, provider_result_id text, provider_result_json text, result_digest text, "
    "status text check(status is null or status in ('done','skipped','failed','dispatched','verified','confirmed_no_effect','uncertain')), "
    "attempt_number integer, error_text text, agent_run_id integer, started_at text, finished_at text, "
    "execution_id integer, action_index integer, outcome_json text, created_at text, updated_at text, "
    "observed_at text, dispatched_at text, provider_receipt_id integer references action_receipts(id))",
    "create index idx_action_receipts_action on action_receipts(action_id,id)",
    "create unique index idx_action_receipts_execution_order on action_receipts(execution_id,action_index) "
    "where execution_id is not null",
    "create table action_observers (action_id integer not null references actions(id), agent_run_id integer not null, "
    "task_id integer not null, created_at text not null, primary key(action_id,agent_run_id))",
    "create trigger trg_action_receipt_owner_insert before insert on action_receipts when not exists("
    "select 1 from actions where id=new.action_id and record_type='action') begin "
    "select raise(abort,'receipt owner must be Action'); end",
    "create trigger trg_action_observer_owner_insert before insert on action_observers when not exists("
    "select 1 from actions where id=new.action_id and record_type='action') begin "
    "select raise(abort,'observer owner must be Action'); end",
    "create trigger trg_action_receipt_reference_owner_insert before insert on action_receipts "
    "when new.provider_receipt_id is not null and not exists(select 1 from action_receipts r "
    "where r.id=new.provider_receipt_id and r.action_id=new.action_id and r.provider_result_json is not null) "
    "begin select raise(abort,'provider receipt reference must resolve within Action'); end",
    "create trigger trg_action_receipt_reference_owner_update before update of provider_receipt_id on action_receipts "
    "when new.provider_receipt_id is not null and old.provider_receipt_id is not new.provider_receipt_id and not exists("
    "select 1 from action_receipts r where r.id=new.provider_receipt_id and r.action_id=new.action_id and r.provider_result_json is not null) "
    "begin select raise(abort,'provider receipt reference must resolve within Action'); end",
    "create trigger trg_action_delivery_receipt_owner_delete before delete on action_receipts "
    "when exists(select 1 from actions a where a.record_type='action' and a.action_delivery_receipt_id=old.id) "
    "begin select raise(abort,'Sent receipt is still referenced by Action'); end",
    "create trigger trg_action_receipt_identity_immutable before update of id,action_id on action_receipts "
    "when old.id is not new.id or old.action_id is not new.action_id begin "
    "select raise(abort,'receipt identity and owner are immutable'); end",
    "create trigger trg_action_receipt_lineage_immutable before update of provider_result_json,result_digest,provider_receipt_id on action_receipts "
    "when old.provider_result_json is not new.provider_result_json or "
    "(old.result_digest is not new.result_digest and not (old.result_digest is null and "
    "exists(select 1 from actions a where a.record_type='action' and a.id=old.action_id and a.action_delivery_receipt_id=old.id))) or "
    "(old.provider_receipt_id is not new.provider_receipt_id and not ("
    "((exists(select 1 from actions a where a.record_type='action' and a.id=old.action_id and a.action_delivery_receipt_id=old.id) "
    "or (old.provider_result_json is null and old.execution_id is null and old.action_index is null) "
    "or (old.execution_id is not null and old.action_index is not null)) "
    "and exists(select 1 from action_receipts next where next.id=new.provider_receipt_id and next.action_id=old.action_id) "
    "and ((old.execution_id is not null and old.action_index is not null) or new.observed_at is old.observed_at)))) begin "
    "select raise(abort,'provider receipt versions and exact references are immutable'); end",
)

ACTION_ENTITY_PARENT_TRIGGER_DDL = (
    "create trigger trg_action_delivery_receipt_owner_insert before insert on actions "
    "when new.record_type='action' and new.action_delivery_receipt_id is not null and not exists("
    "select 1 from action_receipts r where r.id=new.action_delivery_receipt_id and r.action_id=new.id) "
    "begin select raise(abort,'Sent receipt reference must resolve within Action'); end",
    "create trigger trg_action_delivery_receipt_owner_update before update of action_data on actions "
    "when old.record_type='action' and new.record_type='action' "
    "and old.action_delivery_receipt_id is not new.action_delivery_receipt_id "
    "and new.action_delivery_receipt_id is not null and not exists("
    "select 1 from action_receipts r where r.id=new.action_delivery_receipt_id and r.action_id=new.id) "
    "begin select raise(abort,'Sent receipt reference must resolve within Action'); end",
    "create trigger trg_action_prepared_receipt_owner_insert before insert on actions "
    "when new.record_type='action' and new.action_prepared_receipt_id is not null and not exists("
    "select 1 from action_receipts r where r.id=new.action_prepared_receipt_id and r.action_id=new.id "
    "and r.provider_result_json is not null) begin "
    "select raise(abort,'prepared receipt reference must resolve within Action'); end",
    "create trigger trg_action_prepared_receipt_owner_update before update of action_data on actions "
    "when old._kind=70 and new._kind=70 "
    "and json_extract(old.action_data,'$.prepared_receipt_id') is not json_extract(new.action_data,'$.prepared_receipt_id') "
    "and json_extract(new.action_data,'$.prepared_receipt_id') is not null and not exists("
    "select 1 from action_receipts r where r.id=json_extract(new.action_data,'$.prepared_receipt_id') and r.action_id=new.id "
    "and r.provider_result_json is not null) begin "
    "select raise(abort,'prepared receipt reference must resolve within Action'); end",
)

# Conversion provenance is deliberately identity-only. Business bodies and
# provider payloads live on the current Action and receipt rows themselves.
ACTION_IDENTITY_MAP_DDL = (
    "create table action_entity_identity_map (source_type text not null, source_identity text not null, "
    "action_id integer not null references actions(id), receipt_id integer references action_receipts(id), "
    "primary key(source_type,source_identity))",
    "create index idx_action_identity_map_owner on action_entity_identity_map(action_id)",
)


class ActionEntityRecordTypes:
    """Current common discriminator; historical/source factories keep their own tags."""

    def __init__(self, base):
        self.base = base
        self.names = (*base.names, ACTION_KIND)

    def code(self, name):
        return ACTION_KIND_CODE if name == ACTION_KIND else self.base.code(name)

    def declarations(self, kinds=None):
        names = self.names if kinds is None else tuple(kinds)
        codes = tuple(self.code(name) for name in names)
        if len(set(codes)) != len(codes):
            raise ValueError("duplicate current Action discriminator")
        return (
            "_kind integer not null check(_kind in (" + ",".join(map(str, codes)) + "))",
            "record_type text generated always as (case _kind " + " ".join(
                "when " + str(code) + " then " + repr(name)
                for name, code in zip(names, codes, strict=True)
            ) + " end) virtual",
        )

    def trigger_sql(self, sql):
        return self.base.trigger_sql(sql)
