"""Frozen Email52 declaration and explicit Email53 metadata ownership.

The one physical row has distinct classification and message public IDs. The
four locator facts shared by valid pairs are stored once; raw folders, headers,
timestamps and body forms retain their separate native SQLite columns.
"""

CLASS_FIELDS = (
    "id", "account_id", "folder", "uidvalidity", "uid", "rfc_message_id",
    "thread_id", "stable_message_identity", "sender", "subject", "preview",
    "model_text", "received_at", "category", "predicted_category",
    "confirmed_category", "confidence", "margin", "probabilities_json",
    "model_id", "config_version", "status", "classification_source",
    "agent_result_json", "current_action_plan_id", "included_in_model_id",
    "legacy_processed_without_plan", "confirmed_at", "created_at", "updated_at",
)
MESSAGE_FIELDS = (
    "id", "account_id", "stable_message_identity", "folder", "uidvalidity",
    "uid", "rfc_message_id", "in_reply_to", "references_json", "thread_identity",
    "sender", "recipients_json", "subject", "normalized_text", "preview",
    "attachment_metadata_json", "received_at", "created_at", "updated_at",
)
SHARED_FIELDS = frozenset(("account_id", "stable_message_identity", "uidvalidity", "uid"))
MESSAGE_METADATA_FIELDS = frozenset(("folder", "sender", "subject", "received_at", "preview"))


def canonical_column(kind: str, field: str, *, metadata_owner: bool = False) -> str:
    fields = CLASS_FIELDS if kind == "email_classifications" else MESSAGE_FIELDS if kind == "email_messages" else ()
    if field not in fields:
        raise ValueError("unsupported Email source field")
    if kind == "email_classifications" and field == "id":
        return "classification_id"
    if field in SHARED_FIELDS:
        return field
    if metadata_owner and field in MESSAGE_METADATA_FIELDS:
        return "message_" + field
    return ("class_" if kind == "email_classifications" else "message_") + field


def email_projection(kind: str, *, metadata_owner: bool = False) -> str:
    fields = CLASS_FIELDS if kind == "email_classifications" else MESSAGE_FIELDS if kind == "email_messages" else ()
    if not fields:
        raise ValueError("unsupported Email source kind")
    selected = ",".join(f'"{canonical_column(kind, field, metadata_owner=metadata_owner)}" as "{field}"' for field in fields)
    predicate = " where classification_id is not null" if kind == "email_classifications" else ""
    return "select " + selected + " from main.emails" + predicate


EMAILS_TABLE_SQL = """create table main.emails (
    id integer primary key,
    classification_id integer unique,
    message_id integer not null unique,
    account_id text not null,
    stable_message_identity text not null unique,
    uidvalidity integer not null check(uidvalidity > 0),
    uid integer not null check(uid > 0),
    class_folder text,
    class_rfc_message_id text,
    class_thread_id text,
    class_sender text,
    class_subject text,
    class_preview text,
    class_model_text text,
    class_received_at text,
    class_category text,
    class_predicted_category text,
    class_confirmed_category text,
    class_confidence real,
    class_margin real,
    class_probabilities_json text,
    class_model_id text,
    class_config_version text,
    class_status text,
    class_classification_source text,
    class_agent_result_json text,
    class_current_action_plan_id text,
    class_included_in_model_id text,
    class_legacy_processed_without_plan integer,
    class_confirmed_at text,
    class_created_at text,
    class_updated_at text,
    message_folder text not null,
    message_rfc_message_id text not null,
    message_in_reply_to text not null default '',
    message_references_json text not null default '[]' check(json_valid(message_references_json)),
    message_thread_identity text not null,
    message_sender text not null,
    message_recipients_json text not null check(json_valid(message_recipients_json)),
    message_subject text not null,
    message_normalized_text text not null,
    message_source_reference_json text check(message_source_reference_json is null or json_valid(message_source_reference_json)),
    message_preview text not null,
    message_attachment_metadata_json text not null check(json_valid(message_attachment_metadata_json)),
    message_received_at text not null,
    message_created_at text not null,
    message_updated_at text not null,
    check(classification_id is null or (
        class_folder is not null and class_sender is not null and
        class_subject is not null and class_preview is not null and
        class_model_text is not null and class_received_at is not null and
        class_confidence is not null and class_margin is not null and
        class_probabilities_json is not null and class_model_id is not null and
        class_config_version is not null and class_status is not null and
        class_classification_source is not null and class_agent_result_json is not null and
        class_legacy_processed_without_plan in (0,1) and
        class_confirmed_at is not null and class_created_at is not null and
        class_updated_at is not null
    ))
)"""

EMAILS_INDEX_SQL = (
    "create index idx_emails_class_account_locator on emails(account_id,class_folder,uidvalidity,class_status,class_updated_at desc,uid) where classification_id is not null",
    "create index idx_emails_class_account_status on emails(account_id,class_status,class_updated_at desc) where classification_id is not null",
    "create index idx_emails_class_status on emails(class_status,class_updated_at desc,classification_id desc) where classification_id is not null",
    "create index idx_emails_message_account_locator on emails(account_id,message_folder,uidvalidity,uid)",
)


def email_metadata_declaration(*, metadata_owner: bool = False, action_owner: bool = False):
    """Select Email53 explicitly; the default Email52 declaration stays frozen."""
    if not metadata_owner:
        return EMAILS_TABLE_SQL, EMAILS_INDEX_SQL, EMAILS_TRIGGER_SQL
    table = EMAILS_TABLE_SQL
    for field in MESSAGE_METADATA_FIELDS:
        table = table.replace("    class_" + field + " text,\n", "")
    table = table.replace(
        "        class_folder is not null and class_sender is not null and\n"
        "        class_subject is not null and class_preview is not null and\n"
        "        class_model_text is not null and class_received_at is not null and\n",
        "        class_model_text is not null and\n",
    )
    # The counting expression includes embedded NULs, which length(text)
    # alone stops at. It does not modify the stored preview.
    table = table.replace(
        "    message_preview text not null,", "    message_preview text not null "
        "check(typeof(message_preview)='text' and "
        "length(cast(unhex(replace(hex(message_preview),'00','01')) as text))<=1000),")
    indexes = tuple(sql.replace("account_id,class_folder,", "account_id,message_folder,")
                    for sql in EMAILS_INDEX_SQL)
    triggers = EMAILS_TRIGGER_SQL
    if action_owner:
        triggers = tuple(
            sql.replace(
                "a.record_type='email_actions' and a.email_classification_id=old.classification_id and a.email_status='processing'",
                "a.record_type='action' and a.action_classification_id=old.classification_id and a.action_status='processing' and a.action_operation in ('archive','flag_important','mark_read','move','trash','label')",
            ) if sql.startswith("create trigger trg_emails_direct_action_blocks_plan_switch ") else sql
            for sql in triggers
        )
    return table, indexes, triggers

EMAILS_TRIGGER_SQL = (
    "create trigger trg_emails_class_source_insert before insert on emails when new.classification_id is not null and new.class_classification_source not in ('model','user','agent') begin select raise(abort,'invalid email classification source'); end",
    "create trigger trg_emails_class_source_update before update of class_classification_source on emails when new.classification_id is not null and new.class_classification_source not in ('model','user','agent') begin select raise(abort,'invalid email classification source'); end",
    "create trigger trg_emails_class_status_insert before insert on emails when new.classification_id is not null and new.class_status not in ('pending_feedback','processed') begin select raise(abort,'invalid email classification status'); end",
    "create trigger trg_emails_class_status_update before update of class_status on emails when new.classification_id is not null and new.class_status not in ('pending_feedback','processed') begin select raise(abort,'invalid email classification status'); end",
    "create trigger trg_emails_training_inclusion_invalidate after update of class_confirmed_category,class_model_text,class_confirmed_at,class_classification_source,class_status on emails when old.classification_id is not null and old.class_included_in_model_id is not null and (old.class_confirmed_category is not new.class_confirmed_category or old.class_model_text is not new.class_model_text or old.class_confirmed_at is not new.class_confirmed_at or old.class_classification_source is not new.class_classification_source or old.class_status is not new.class_status) begin update emails set class_included_in_model_id=null where id=new.id; end",
    "create trigger trg_emails_direct_action_blocks_plan_switch before update of class_status,class_current_action_plan_id on emails when old.classification_id is not null and new.classification_id is not null and (old.class_status is not new.class_status or old.class_current_action_plan_id is not new.class_current_action_plan_id) and exists(select 1 from actions a where a.record_type='email_actions' and a.email_classification_id=old.classification_id and a.email_status='processing') begin select raise(abort,'email_direct_action_in_flight'); end",
    "create trigger trg_emails_reply_dispatch_blocks_plan_switch before update of class_current_action_plan_id on emails when old.classification_id is not null and new.classification_id is not null and old.class_current_action_plan_id is not new.class_current_action_plan_id and exists(select 1 from actions a where a.record_type='email_reply_dispatch_claims' and a.email_classification_id=old.classification_id and a.email_status='dispatching') begin select raise(abort,'email_reply_dispatch_in_flight'); end",
    "create trigger trg_emails_unsubscribe_blocks_plan_switch before update of class_status,class_current_action_plan_id on emails when old.classification_id is not null and new.classification_id is not null and (old.class_status is not new.class_status or old.class_current_action_plan_id is not new.class_current_action_plan_id) and exists(select 1 from actions a where a.record_type='email_unsubscribe_claims' and a.email_classification_id=old.classification_id and a.email_status='dispatching') begin select raise(abort,'email_unsubscribe_write_in_flight'); end",
    "create trigger trg_emails_reply_dispatch_blocks_message_delete before delete on emails when exists(select 1 from actions a where a.record_type='email_reply_dispatch_claims' and a.email_account_id=old.account_id and a.email_stable_message_identity=old.stable_message_identity and a.email_status='dispatching') begin select raise(abort,'email_reply_dispatch_in_flight'); end",
    "create trigger trg_emails_reply_dispatch_blocks_thread_update before update of account_id,stable_message_identity,message_thread_identity on emails when (old.account_id is not new.account_id or old.stable_message_identity is not new.stable_message_identity or old.message_thread_identity is not new.message_thread_identity) and exists(select 1 from actions a where a.record_type='email_reply_dispatch_claims' and a.email_account_id=old.account_id and a.email_stable_message_identity=old.stable_message_identity and a.email_status='dispatching') begin select raise(abort,'email_reply_dispatch_in_flight'); end",
    "create trigger trg_emails_unsubscribe_blocks_message_delete before delete on emails when exists(select 1 from actions a where a.record_type='email_unsubscribe_claims' and a.email_account_id=old.account_id and a.email_stable_message_identity=old.stable_message_identity and a.email_status='dispatching') begin select raise(abort,'email_unsubscribe_write_in_flight'); end",
    "create trigger trg_emails_unsubscribe_blocks_message_update before update of account_id,stable_message_identity,message_thread_identity on emails when (old.account_id is not new.account_id or old.stable_message_identity is not new.stable_message_identity or old.message_thread_identity is not new.message_thread_identity) and exists(select 1 from actions a where a.record_type='email_unsubscribe_claims' and a.email_account_id=old.account_id and a.email_stable_message_identity=old.stable_message_identity and a.email_status='dispatching') begin select raise(abort,'email_unsubscribe_write_in_flight'); end",
)
