"""Fixed runtime declaration for the common external Action entity."""

from app.storage_payload import ACTION_RECORD_TYPES, PayloadLayout

ACTION_KIND = "action"
ACTION_KIND_CODE = ACTION_RECORD_TYPES.code(ACTION_KIND)
ACTION_FIELDS = (
    "operation", "parameters_json", "status", "action_plan_id", "classification_id",
    "account_id", "external_action_key", "next_attempt_at", "started_at", "created_at",
    "updated_at", "prepared_body", "prepared_at", "delivery_channel", "delivery_key",
    "feedback_token", "postfix_version", "conversation_id", "trigger_message_id", "recall_key",
    "recall_status", "recall_error", "recalled_at", "agent_run_id", "business_object_key",
    "action_identity",
)
ACTION_LAYOUT = PayloadLayout(ACTION_FIELDS)
ACTION_INTEGER_FIELDS = frozenset({"classification_id", "agent_run_id"})
ACTION_ALLOWED_FIELDS = frozenset(ACTION_FIELDS)

RECEIPT_FIELDS = (
    "attempt_number", "status", "operation", "target", "provider_result_id",
    "provider_result_json", "result_digest", "agent_run_id", "observed_at", "error_text",
    "dispatched_at", "started_at", "finished_at", "execution_id", "action_index",
    "outcome_json", "created_at", "updated_at", "provider_receipt_id",
    "supersedes_provider_receipt_id",
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
    "create unique index idx_actions_action_delivery_key on actions(action_delivery_channel,action_delivery_key) "
    "where record_type='action' and action_delivery_channel is not null and action_delivery_key is not null",
)

RECEIPT_SCHEMA_DDL = (
    "create table action_receipts (id integer primary key, action_id integer not null references actions(id), "
    "operation text, target text, provider_result_id text, provider_result_json text, result_digest text, "
    "status text check(status is null or status in ('done','skipped','failed','dispatched','verified','confirmed_no_effect','uncertain')), "
    "attempt_number integer, error_text text, agent_run_id integer, started_at text, finished_at text, "
    "execution_id integer, action_index integer, outcome_json text, created_at text, updated_at text, "
    "observed_at text, dispatched_at text, provider_receipt_id integer references action_receipts(id), "
    "supersedes_provider_receipt_id integer references action_receipts(id), "
    "check(supersedes_provider_receipt_id is null or supersedes_provider_receipt_id<id))",
    "create index idx_action_receipts_action on action_receipts(action_id,id)",
    "create index idx_action_receipts_execution_order on action_receipts(execution_id,action_index,action_id) "
    "where execution_id is not null",
    "create unique index idx_action_receipts_one_successor on action_receipts(supersedes_provider_receipt_id) "
    "where supersedes_provider_receipt_id is not null",
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
    "create trigger trg_action_receipt_supersedes_owner_insert before insert on action_receipts "
    "when new.supersedes_provider_receipt_id is not null and not exists(select 1 from action_receipts r "
    "where r.id=new.supersedes_provider_receipt_id and r.action_id=new.action_id and r.provider_result_json is not null) "
    "begin select raise(abort,'superseded provider receipt must resolve within Action'); end",
    "create trigger trg_action_receipt_reference_owner_update before update of provider_receipt_id on action_receipts "
    "when new.provider_receipt_id is not null and old.provider_receipt_id is not new.provider_receipt_id and not exists("
    "select 1 from action_receipts r where r.id=new.provider_receipt_id and r.action_id=new.action_id and r.provider_result_json is not null) "
    "begin select raise(abort,'provider receipt reference must resolve within Action'); end",
    "create trigger trg_action_receipt_identity_immutable before update of id,action_id on action_receipts "
    "when old.id is not new.id or old.action_id is not new.action_id begin "
    "select raise(abort,'receipt identity and owner are immutable'); end",
    "create trigger trg_action_receipt_lineage_immutable before update of provider_result_json,result_digest,supersedes_provider_receipt_id,provider_receipt_id on action_receipts "
    "when old.provider_result_json is not new.provider_result_json or old.result_digest is not new.result_digest or "
    "old.supersedes_provider_receipt_id is not new.supersedes_provider_receipt_id or "
    "(old.provider_receipt_id is not null and old.provider_receipt_id is not new.provider_receipt_id) begin "
    "select raise(abort,'provider receipt versions and exact references are immutable'); end",
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
